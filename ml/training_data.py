"""
ml.training_data — Point-in-Time Training-Panel Builder
=======================================================
Builds the supervised training panel that the Stage-4 ML layer
(``LGBMCrossSectionalRanker`` / ``MetaLabeler``) needs but which currently
cannot be assembled — ``ml/data/cache/`` ships with ZERO PIT feature snapshots
and no code walked historical trading dates to build one.

This module is the foundation.  For each ``as_of_date`` in ``[start, end]`` it:

1. Assembles a dashboard-shaped, one-row-per-ticker feature frame from bars
   available **strictly before** ``as_of_date`` (no lookahead — the row for a
   ticker at date D depends only on ``close[: D)``).
2. Runs :func:`ml.feature_engineering.build_pit_feature_matrix` on that frame to
   produce the canonical cross-sectional feature columns.
3. Persists the snapshot via :class:`ml.data.store.PITFeatureStore` so future
   incremental retrains can pull an expanding window without recomputation.

After walking every date it:

* Stacks the per-date frames into ``X`` with a ``(date, ticker)`` MultiIndex.
* Builds ``y`` — cross-sectional forward-``horizon_days`` return rank percentiles
  — via :func:`ml.feature_engineering.build_forward_return_ranks`, aligned to
  ``X``'s index.
* Emits ``t1`` — each event's forward-window *end* timestamp — for
  CombinatorialPurgedCV purging/embargo.
* Emits ``price_history`` — a wide adjusted-close DataFrame (columns=tickers,
  index=dates).

Design constraints
------------------
* **No fabricated metrics** (CONSTRAINT #4): a feature that cannot be computed
  stays ``NaN``; a ticker with no usable price history is silently dropped.
* **Per-ticker dead-lettering** (CONSTRAINT #6): one bad symbol never aborts the
  build — it is logged and skipped.
* **Reuse, don't rewrite**: feature math lives in ``ml/feature_engineering.py``;
  the cache lives in ``ml/data/store.py``; bar persistence lives in
  ``data/historical_store.py``.  This module only orchestrates.

Price source
------------
Prefers :meth:`data.historical_store.HistoricalStore.get_bars` (DB-cached,
incremental).  An injected ``data_engine`` (``DataEngine`` / ``MockDataEngine``
implementing ``IDataProvider.fetch_technical_raw``) takes precedence when
supplied — the intended path for offline tests.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from ml.feature_engineering import (
    FEATURE_COLUMNS,
    build_forward_return_ranks,
    build_pit_feature_matrix,
)
from ml.data.store import PITFeatureStore

logger = logging.getLogger("ML.TrainingData")

# Trading-day lookbacks mirroring processing_engine's causal momentum/vol math.
# (processing_engine.calculate_momentum_metrics uses shift(1)/shift(253) etc.)
_ROC_12M_LB = 252   # ≈ 12 months of trading days
_ROC_6M_LB = 126    # ≈ 6 months
_REALIZED_VOL_WINDOW = 60
_GARCH_PROXY_WINDOW = 20   # matches scripts/train_lgbm.py's pre-convergence proxy


# ──────────────────────────────────────────────────────────────────────────────
# Price sourcing
# ──────────────────────────────────────────────────────────────────────────────
def _bars_for_symbol(
    symbol: str,
    *,
    data_engine=None,
    lookback_days: int = 504,
) -> pd.DataFrame:
    """Return a tz-naive OHLCV DataFrame for *symbol* (or empty on any failure).

    Precedence: an injected ``data_engine`` (offline/testing) wins; otherwise
    fall back to :meth:`HistoricalStore.get_bars`.  Never raises — returns an
    empty frame so the caller can dead-letter the symbol.

    NOTE: this fetches ONE symbol at a time.  When ``data_engine`` is supplied,
    prefer :func:`_bars_for_universe` instead — some data providers'
    ``fetch_technical_raw`` distributes per-ticker dispersion by the ticker's
    POSITION within the list passed to a single call (e.g. ``enumerate(tickers)``
    seeding); calling it once per symbol collapses every symbol to the same
    "position 0" result.  This function remains for the ``HistoricalStore``
    fallback path (inherently per-symbol) and as an isolated per-symbol retry.
    """
    try:
        if data_engine is not None:
            raw = data_engine.fetch_technical_raw([symbol])
            df = raw.get(symbol)
            if df is None or df.empty:
                return pd.DataFrame()
            return _normalize_bars(df)

        # Lazy import to avoid a heavy/circular import at module load.
        from data.historical_store import HistoricalStore

        store = HistoricalStore()
        df = store.get_bars(symbol, lookback_days=lookback_days)
        if df is None or df.empty:
            return pd.DataFrame()
        return _normalize_bars(df)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("training_data: bars fetch failed for %s: %s", symbol, exc)
        return pd.DataFrame()


def _bars_for_universe(
    universe: list,
    *,
    data_engine=None,
    lookback_days: int = 504,
) -> dict:
    """Return ``{symbol: OHLCV DataFrame}`` for the whole universe.

    When ``data_engine`` is supplied, this fetches ALL symbols in ONE batched
    call — matching ``IDataProvider.fetch_technical_raw(tickers: list)``'s
    batch contract, and required for correctness with providers whose
    per-ticker dispersion depends on ticker POSITION within that call's list
    (calling it once per symbol would make every symbol look like "position 0").
    If the batch call itself raises, falls back to isolated per-symbol calls
    (via :func:`_bars_for_symbol`) so one bad symbol can't take down the rest
    (CONSTRAINT #6).  When ``data_engine`` is ``None``, sources per-symbol from
    ``HistoricalStore.get_bars`` (that API has no batch form).
    """
    if data_engine is None:
        return {
            symbol: bars
            for symbol in universe
            if not (bars := _bars_for_symbol(symbol, data_engine=None, lookback_days=lookback_days)).empty
        }

    try:
        raw = data_engine.fetch_technical_raw(list(universe)) or {}
    except Exception as exc:
        logger.warning(
            "training_data: batch bars fetch failed (%s) — retrying per-symbol.", exc
        )
        return {
            symbol: bars
            for symbol in universe
            if not (bars := _bars_for_symbol(symbol, data_engine=data_engine)).empty
        }

    out: dict = {}
    for symbol in universe:
        df = raw.get(symbol)
        if df is None or df.empty:
            continue
        try:
            out[symbol] = _normalize_bars(df)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("training_data: normalize failed for %s: %s", symbol, exc)
    return out


def _normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a bar frame to a tz-naive, ascending-sorted DatetimeIndex."""
    out = df.copy()
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    # Strip intraday timestamps so cross-source dates align (yfinance vs FRED).
    idx = idx.normalize()
    out.index = idx
    out = out[~out.index.duplicated(keep="last")]
    return out.sort_index()


# ──────────────────────────────────────────────────────────────────────────────
# Per-ticker point-in-time feature computation (from bars only)
# ──────────────────────────────────────────────────────────────────────────────
def _causal_rsi(close: pd.Series, length: int) -> float:
    """Wilder RSI at the last bar of *close* (already sliced to be PIT-safe).

    Returns ``NaN`` when there is insufficient history.  Uses the same
    exponential (Wilder) smoothing as ``pandas_ta.rsi``.
    """
    if len(close) < length + 1:
        return float("nan")
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    last_gain = avg_gain.iloc[-1]
    last_loss = avg_loss.iloc[-1]
    if pd.isna(last_gain) or pd.isna(last_loss):
        return float("nan")
    if last_loss == 0:
        return 100.0 if last_gain > 0 else float("nan")
    rs = last_gain / last_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def _pit_ticker_row(close: pd.Series) -> dict:
    """Compute the dashboard-shaped raw feature inputs from a PIT close series.

    ``close`` must already be sliced to bars STRICTLY BEFORE ``as_of_date``.
    Every value is ``NaN`` when it cannot be honestly computed (CONSTRAINT #4).

    Only the price-derivable dashboard columns are populated here:
    ``ROC_12M``, ``ROC_6M``, ``RSI``, ``RSI_2``, ``low_vol_score``,
    ``GARCH_Vol`` (a realized-vol proxy — see module docstring).  The
    remaining feature-matrix inputs (fundamentals, factor Z-scores) are
    absent → ``build_pit_feature_matrix`` fills them with ``NaN``.
    """
    row: dict[str, float] = {}

    # Momentum (causal ratios; the input series already excludes as_of_date).
    if len(close) > _ROC_12M_LB:
        p_now = float(close.iloc[-1])
        p_12m = float(close.iloc[-1 - _ROC_12M_LB])
        row["ROC_12M"] = (p_now / p_12m - 1.0) if p_12m > 0 else float("nan")
    else:
        row["ROC_12M"] = float("nan")

    if len(close) > _ROC_6M_LB:
        p_now = float(close.iloc[-1])
        p_6m = float(close.iloc[-1 - _ROC_6M_LB])
        row["ROC_6M"] = (p_now / p_6m - 1.0) if p_6m > 0 else float("nan")
    else:
        row["ROC_6M"] = float("nan")

    # Mean-reversion (Wilder RSI at the last available bar).
    row["RSI"] = _causal_rsi(close, 14)
    row["RSI_2"] = _causal_rsi(close, 2)

    # Low-vol factor: negative annualized 60-day realized vol.
    daily_ret = close.pct_change().dropna()
    if len(daily_ret) >= _REALIZED_VOL_WINDOW:
        realized_vol = float(
            daily_ret.iloc[-_REALIZED_VOL_WINDOW:].std(ddof=1) * np.sqrt(252.0)
        )
        row["low_vol_score"] = -realized_vol if np.isfinite(realized_vol) else float("nan")
    else:
        row["low_vol_score"] = float("nan")

    # GARCH_Vol proxy: annualized 20-day realized vol (a legitimate causal
    # approximation, not a fabricated value — full GJR-GARCH lives in
    # technical_options_engine.py and isn't reproduced here).
    if len(daily_ret) >= _GARCH_PROXY_WINDOW:
        garch_proxy = float(
            daily_ret.iloc[-_GARCH_PROXY_WINDOW:].std(ddof=1) * np.sqrt(252.0)
        )
        row["GARCH_Vol"] = garch_proxy if np.isfinite(garch_proxy) else float("nan")
    else:
        row["GARCH_Vol"] = float("nan")

    return row


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────
def _empty_panel(universe) -> Tuple[pd.DataFrame, pd.Series, pd.Series, pd.DataFrame]:
    """Return correctly-shaped empty (X, y, t1, price_history)."""
    empty_idx = pd.MultiIndex.from_arrays(
        [pd.DatetimeIndex([]), pd.Index([], dtype=object)],
        names=["date", "ticker"],
    )
    X = pd.DataFrame(columns=FEATURE_COLUMNS, index=empty_idx)
    y = pd.Series(dtype=float, index=empty_idx, name="fwd_return_rank")
    t1 = pd.Series(dtype="datetime64[ns]", index=empty_idx, name="t1")
    price_history = pd.DataFrame(columns=list(universe) if universe else [])
    return X, y, t1, price_history


def build_training_panel(
    start,
    end,
    universe,
    *,
    data_engine=None,
    horizon_days: int = 21,
    step_days: int = 1,
) -> Tuple[pd.DataFrame, pd.Series, pd.Series, pd.DataFrame]:
    """Build the supervised PIT training panel over ``[start, end]``.

    Parameters
    ----------
    start, end :
        Inclusive date bounds (anything ``pd.Timestamp`` accepts).  Trading
        dates are taken from the union of every ticker's available bar index
        that falls in the window.
    universe :
        Iterable of ticker symbols.
    data_engine :
        Optional injected ``IDataProvider`` (``DataEngine`` / ``MockDataEngine``).
        When supplied it is the price source — the offline/testing path.  When
        ``None``, bars come from ``HistoricalStore.get_bars``.
    horizon_days :
        Forward-return horizon (in trading rows) for the label ``y`` and the
        ``t1`` forward-window end timestamps.  Default 21 (≈ one month).
    step_days :
        Thin the walked ``as_of_dates`` to every ``step_days``-th trading date
        (default 1 = every date, the original behavior).  Callers that need
        many dates of history but want to bound CPCV fold cost (e.g.
        ``scripts/train_lgbm.py``) can pass e.g. ``step_days=5``.  Thinning
        happens BEFORE the per-date walk, so it also saves computation (never
        walks dates it then discards).

    Returns
    -------
    (X, y, t1, price_history)
        * ``X`` — features, ``(date, ticker)`` MultiIndex, columns = ``FEATURE_COLUMNS``.
        * ``y`` — cross-sectional forward-return rank ∈ [0, 1], aligned to ``X``.
        * ``t1`` — forward-window end timestamp per event, aligned to ``X`` (for CPCV).
        * ``price_history`` — wide adjusted closes, columns = tickers, index = dates.

    Notes
    -----
    * No lookahead: the feature row for a ticker at date ``D`` uses only bars
      with timestamp ``< D``.
    * Per-ticker try/except (CONSTRAINT #6); NaN never fabricated (CONSTRAINT #4).
    * Each ``as_of_date`` snapshot is persisted via ``PITFeatureStore.write``.
    """
    universe = [str(s).upper() for s in (universe or [])]
    t_start = pd.Timestamp(start).normalize()
    t_end = pd.Timestamp(end).normalize()

    if not universe:
        logger.info("build_training_panel: empty universe → empty panel.")
        return _empty_panel(universe)

    # ── 1. Load universe bars (batched; dead-lettered) and assemble price_history ──
    bars_by_symbol = _bars_for_universe(universe, data_engine=data_engine)
    close_by_ticker: dict[str, pd.Series] = {}
    for symbol in universe:
        try:
            bars = bars_by_symbol.get(symbol)
            if bars is None or bars.empty or "Close" not in bars.columns:
                logger.info("build_training_panel: no usable bars for %s — skipped.", symbol)
                continue
            close_by_ticker[symbol] = bars["Close"].astype(float)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("build_training_panel: %s dead-lettered: %s", symbol, exc)
            continue

    if not close_by_ticker:
        logger.info("build_training_panel: no tickers produced bars → empty panel.")
        return _empty_panel(universe)

    price_history = pd.DataFrame(close_by_ticker).sort_index()

    # ── 2. Determine as-of trading dates inside the window ──────────────────────
    all_dates = price_history.index
    as_of_dates = all_dates[(all_dates >= t_start) & (all_dates <= t_end)]
    if len(as_of_dates) == 0:
        logger.info("build_training_panel: no trading dates in window → empty panel.")
        return _empty_panel(universe)
    if step_days > 1:
        as_of_dates = as_of_dates[::step_days]

    store = PITFeatureStore()

    # ── 3. Walk each as-of date; build + persist a PIT feature frame ────────────
    per_date_frames: list[pd.DataFrame] = []
    kept_dates: list[pd.Timestamp] = []
    for as_of_date in as_of_dates:
        rows: dict[str, dict] = {}
        for symbol, close in close_by_ticker.items():
            try:
                # STRICTLY BEFORE as_of_date — this is the no-lookahead cut.
                prior = close.loc[close.index < as_of_date]
                if prior.empty:
                    continue
                rows[symbol] = _pit_ticker_row(prior)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "build_training_panel: %s @ %s dead-lettered: %s",
                    symbol, as_of_date.date(), exc,
                )
                continue

        if not rows:
            continue

        universe_df = pd.DataFrame.from_dict(rows, orient="index")
        universe_df.index.name = "ticker"

        feat = build_pit_feature_matrix(universe_df, as_of_date=as_of_date)

        # Persist the snapshot for future incremental retrains (dead-lettered).
        # Drop the non-serializable Timestamp attr build_pit_feature_matrix
        # stamps on .attrs before Parquet write (pyarrow can't JSON-encode it).
        try:
            feat_to_write = feat.copy()
            feat_to_write.attrs = {}
            store.write(as_of_date, feat_to_write)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "build_training_panel: PITFeatureStore.write(%s) failed: %s",
                as_of_date.date(), exc,
            )

        feat_indexed = feat.copy()
        feat_indexed.index = pd.MultiIndex.from_product(
            [[as_of_date], feat_indexed.index], names=["date", "ticker"]
        )
        per_date_frames.append(feat_indexed)
        kept_dates.append(as_of_date)

    if not per_date_frames:
        logger.info("build_training_panel: no PIT frames produced → empty panel.")
        return _empty_panel(universe)

    X = pd.concat(per_date_frames)
    X = X[FEATURE_COLUMNS]

    # ── 4. Labels: cross-sectional forward-return rank percentiles ──────────────
    kept_idx = pd.DatetimeIndex(sorted(set(kept_dates)))
    fwd_ranks = build_forward_return_ranks(
        price_history, kept_idx, horizon_days=horizon_days
    )

    y = pd.Series(np.nan, index=X.index, name="fwd_return_rank")
    for (dt, ticker) in X.index:
        if dt in fwd_ranks.index and ticker in fwd_ranks.columns:
            y.loc[(dt, ticker)] = fwd_ranks.loc[dt, ticker]

    # ── 5. t1: forward-window END timestamp per event (for CPCV purging) ────────
    date_positions = {d: price_history.index.get_loc(d) for d in kept_idx}
    n_dates = len(price_history.index)
    t1 = pd.Series(pd.NaT, index=X.index, name="t1")
    for (dt, ticker) in X.index:
        loc = date_positions.get(dt)
        if loc is None:
            continue
        future_loc = loc + horizon_days
        if future_loc < n_dates:
            t1.loc[(dt, ticker)] = price_history.index[future_loc]

    logger.info(
        "build_training_panel: %d events over %d dates, %d tickers.",
        len(X), len(kept_idx), price_history.shape[1],
    )
    return X, y, t1, price_history
