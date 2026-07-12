"""
scripts/refresh_validations.py — Walk-forward validation cadence runner (Tier 4.2).

Iterates a registry of strategy adapters, runs ``StrategyValidationHarness``
for each, writes JSON summaries to ``reports/``, and prints a pass/fail table.
Designed to be run monthly (or on demand) so validation reports never go stale.

Usage
-----
::

    python -m scripts.refresh_validations                     # validate all
    python -m scripts.refresh_validations --strategies rsi2_mean_reversion
    python -m scripts.refresh_validations --start 2010-01-01 --end 2023-12-31
    ./scripts/refresh_validations.sh                          # venv-activating wrapper

Options
-------
--strategies NAME[,NAME]   Comma-separated strategy names (default: all registered).
--start  YYYY-MM-DD        Backtest start date (default: 2005-01-01).
--end    YYYY-MM-DD        Backtest end date (default: today).
--output-dir  PATH         Directory for JSON report output (default: reports/).
--n-cpcv-splits  N         CPCV split count (default: 10).
--n-test-splits  N         Walk-forward test splits (default: 2).
--json                     Also print ONE machine-readable JSON line (last line
                           of stdout) mapping strategy_id → {deployable, pbo,
                           dsr, sharpe, max_drawdown[, error]} for GUI parsing.

Strategy registry shape
-----------------------
``STRATEGY_REGISTRY: Dict[str, Tuple[adapter_fn, turnover, universe]]`` — the
KEYS are the stable strategy-id strings (the GUI's multiselect reads
``STRATEGY_REGISTRY.keys()``).  ``universe`` is a ``List[str]`` of the tickers
the adapter needs; SPY-only adapters declare ``["SPY"]`` and are invoked with a
single ``pd.Series`` (the SPY close), while multi-ticker adapters are invoked
with ``(closes: pd.DataFrame, shares: Dict[str, float])``.  ``run_validations``
downloads exactly the union of tickers required by the selected strategies.

Honest cross-sectional scope (CONSTRAINT #4)
--------------------------------------------
The ``multifactor_lowvol_size`` cross-sectional adapter is intentionally
restricted to the Low-Vol and Size factors — the only two honestly derivable
from free, point-in-time-correct data (trailing realized vol from prices; log
market-cap from prices × a CURRENT shares-outstanding snapshot, flagged as an
approximation).  Value and Quality (book-to-market, earnings yield, ROE,
operating margin) require POINT-IN-TIME historical fundamentals that yfinance's
current-snapshot ``.info`` does not provide and no free vendor supplies.  They
are deliberately EXCLUDED rather than fabricated — mirroring the same scope note
in ``tests/test_validation_multifactor.py``.

Design constraints
------------------
* CONSTRAINT #6 — every per-strategy execution is wrapped in try/except so one
  failed strategy never aborts the run; the failed strategy is recorded with an
  ``error`` key and the overall exit code is non-zero.
* CONSTRAINT #4 — fabricated/synthetic returns are never passed to the harness;
  if the adapter cannot build valid X/y the strategy is skipped with an error.
  No fabricated point-in-time fundamentals (see the cross-sectional scope note).
* CONSTRAINT #7 — data fetching uses yfinance (same library as the existing
  test harnesses in ``tests/test_validation_*.py``).  No new data providers.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from validation.thresholds import (
    PBO_MAX,
    DSR_MIN,
    NET_SHARPE_MIN,
    MAX_DRAWDOWN_MAX,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Strategy adapters
# =============================================================================

def _build_rsi2_adapter(
    spy_close: pd.Series,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """RSI(2) mean-reversion on SPY with SMA-200 long-only trend filter.

    Mirrors the test harness in ``tests/test_validation_rsi2.py`` so the
    refresh script exercises the same signal path the validated tests cover.
    """
    def _rsi2(s: pd.Series, length: int = 2) -> pd.Series:
        delta = s.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)
        avg_gain = gain.ewm(alpha=1.0 / length, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / length, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0.0, np.nan)
        return (100.0 - (100.0 / (1.0 + rs))).fillna(100.0)

    rsi = _rsi2(spy_close)
    sma_5 = spy_close.rolling(5).mean()
    sma_200 = spy_close.rolling(200).mean()
    daily_ret = spy_close.pct_change()

    uptrend = spy_close > sma_200
    not_reverted = spy_close <= sma_5
    oversold = ((10.0 - rsi) / 10.0).clip(0.0, 1.0).where(rsi < 10.0, 0.0)
    raw_score = oversold.where(uptrend & not_reverted, 0.0)

    # Price-derived RISK-OFF proxy (see test_validation_rsi2.py for rationale)
    ret_5d = spy_close.pct_change(5)
    crash = ret_5d < -0.06
    rolling_peak = spy_close.rolling(252, min_periods=1).max()
    drawdown = (spy_close - rolling_peak) / rolling_peak
    recession = drawdown < -0.20
    risk_off = (crash | recession).fillna(False)
    gated_score = raw_score.where(~risk_off, 0.0)

    valid_idx = sma_200.dropna().index
    y = daily_ret.loc[valid_idx].fillna(0.0)
    X = pd.DataFrame(
        {"RSI_2": rsi.loc[valid_idx], "SMA_200": sma_200.loc[valid_idx]},
        index=valid_idx,
    )

    ungated_ret = (raw_score.shift(1) * daily_ret).fillna(0.0).loc[valid_idx]
    gated_ret = (gated_score.shift(1) * daily_ret).fillna(0.0).loc[valid_idx]

    precomputed = {"RSI2_Gated": gated_ret, "RSI2_Ungated": ungated_ret}
    return X, y, precomputed


def _build_tsmom_adapter(
    spy_close: pd.Series,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """12-1M time-series momentum on SPY with volatility targeting.

    Mirrors the core logic in ``tests/test_validation_ts_momentum.py``.
    Two variants: 12M look-back and 6M look-back, each with vol targeting at
    10% (conservative) and 20% (aggressive).
    """
    daily_ret = spy_close.pct_change()
    roc_12m = spy_close.shift(1) / spy_close.shift(253) - 1.0
    roc_6m = spy_close.shift(1) / spy_close.shift(127) - 1.0
    vol_60d = daily_ret.shift(1).rolling(60).std() * np.sqrt(252)

    valid_idx = (
        roc_12m.dropna().index.intersection(vol_60d.dropna().index)
    )
    y = daily_ret.loc[valid_idx].fillna(0.0)
    X = pd.DataFrame(
        {
            "ROC_12M": roc_12m.loc[valid_idx],
            "ROC_6M": roc_6m.loc[valid_idx],
            "Vol": vol_60d.loc[valid_idx],
        },
        index=valid_idx,
    )

    precomputed: Dict[str, pd.Series] = {}
    for roc_col, target_vol in [
        ("ROC_12M", 0.10), ("ROC_12M", 0.20),
        ("ROC_6M", 0.10), ("ROC_6M", 0.20),
    ]:
        roc = X[roc_col]
        vol = X["Vol"]
        vol_safe = np.where(vol > 0, vol, 0.20)
        vol_scalar = np.minimum(1.0, target_vol / vol_safe)
        sign_val = np.sign(roc.values)
        score = pd.Series(sign_val * vol_scalar, index=valid_idx)
        ret = (score.shift(1) * y).fillna(0.0)
        precomputed[f"TSMOM_{roc_col}_vol{int(target_vol * 100)}pct"] = ret

    return X, y, precomputed


def _build_macd_adapter(
    spy_close: pd.Series,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """MACD (12/26/9) trend-following on SPY — causal, long-only + long/short.

    MACD line = EMA12 − EMA26; signal line = EMA9(MACD); histogram = line − signal.
    All EMAs are strictly causal (``adjust=False``: value at t uses only close[≤t]).
    The trading signal is ``.shift(1)``-ed before multiplying by the realized
    daily return, so the return at t uses only information available at t−1.

    Three honest variants:
      * ``MACD_LongOnly``   — hold when histogram > 0, else flat.
      * ``MACD_LongShort``  — sign of the histogram (±1).
      * ``MACD_TrendFilter``— long only when histogram > 0 AND close > SMA-200.
    """
    ema_12 = spy_close.ewm(span=12, adjust=False).mean()
    ema_26 = spy_close.ewm(span=26, adjust=False).mean()
    macd_line = ema_12 - ema_26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal_line

    sma_200 = spy_close.rolling(200).mean()
    daily_ret = spy_close.pct_change()

    # Align every variant on a common valid index (drop the SMA-200 warm-up so
    # the trend-filter variant is defined everywhere the others are).
    valid_idx = sma_200.dropna().index
    y = daily_ret.loc[valid_idx].fillna(0.0)
    X = pd.DataFrame(
        {
            "MACD_Hist": hist.loc[valid_idx],
            "SMA_200": sma_200.loc[valid_idx],
        },
        index=valid_idx,
    )

    long_only = (hist > 0).astype(float)
    long_short = pd.Series(np.sign(hist.values), index=hist.index)
    trend = long_only.where(spy_close > sma_200, 0.0)

    precomputed = {
        "MACD_LongOnly": (long_only.shift(1) * daily_ret).fillna(0.0).loc[valid_idx],
        "MACD_LongShort": (long_short.shift(1) * daily_ret).fillna(0.0).loc[valid_idx],
        "MACD_TrendFilter": (trend.shift(1) * daily_ret).fillna(0.0).loc[valid_idx],
    }
    return X, y, precomputed


def _wma(series: pd.Series, window: int) -> pd.Series:
    """Linear-weighted moving average (weights 1..window), causal.

    Value at t is a weighted mean of ``series[t-window+1 .. t]`` — depends only
    on data at or before t (no lookahead).  Vectorized as a rolling dot-product.
    """
    weights = np.arange(1.0, window + 1.0)
    return series.rolling(window).apply(
        lambda x: float(np.dot(x, weights) / weights.sum()), raw=True
    )


def _build_coppock_adapter(
    spy_close: pd.Series,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """Coppock-curve long-horizon momentum on SPY — causal, monthly-scaled.

    Classic Coppock (Coppock 1962) = 10-period WMA of (ROC(14) + ROC(11)),
    designed on MONTHLY bars.  Applied here to daily bars with the periods
    scaled by ~21 trading days/month (14m ≈ 294d, 11m ≈ 231d, 10m WMA ≈ 210d)
    so it stays a genuine long-horizon momentum indicator.  Every component is
    causal (``pct_change`` / rolling WMA look only backward); the position is
    ``.shift(1)``-ed before multiplying by the realized daily return.

    Two honest variants:
      * ``Coppock_Long``  — long when the curve is above zero.
      * ``Coppock_Rising``— long when the curve is above zero AND rising.
    """
    month = 21
    roc_long = spy_close.pct_change(14 * month) * 100.0
    roc_short = spy_close.pct_change(11 * month) * 100.0
    coppock = _wma(roc_long + roc_short, 10 * month)

    daily_ret = spy_close.pct_change()
    valid_idx = coppock.dropna().index
    if len(valid_idx) == 0:
        # Insufficient history for the long look-back — return empty so the
        # caller records a clean "insufficient history" error (CONSTRAINT #4).
        empty = pd.Series(dtype=float)
        return pd.DataFrame(), empty, {}

    y = daily_ret.loc[valid_idx].fillna(0.0)
    X = pd.DataFrame({"Coppock": coppock.loc[valid_idx]}, index=valid_idx)

    long_pos = (coppock > 0.0).astype(float)
    rising_pos = long_pos.where(coppock > coppock.shift(1), 0.0)

    precomputed = {
        "Coppock_Long": (long_pos.shift(1) * daily_ret).fillna(0.0).loc[valid_idx],
        "Coppock_Rising": (rising_pos.shift(1) * daily_ret).fillna(0.0).loc[valid_idx],
    }
    return X, y, precomputed


def _xsec_zscore(df: pd.DataFrame) -> pd.DataFrame:
    """Per-row (per-day) cross-sectional z-score, winsorized at ±3.

    Identical methodology to ``signals/multifactor.py``'s ``_zscore_winsorize``
    and ``tests/test_validation_multifactor.py``.  Operates row-wise across the
    ticker columns — no time-series leakage between rows.
    """
    mean = df.mean(axis=1)
    std = df.std(axis=1)
    z = df.sub(mean, axis=0).div(std.replace(0.0, np.nan), axis=0)
    return z.clip(lower=-3.0, upper=3.0)


def _build_lowvol_size_adapter(
    closes: pd.DataFrame,
    shares: Optional[Dict[str, float]] = None,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """Cross-sectional Low-Vol + Size proxy over a multi-ticker universe.

    Follows ``tests/test_validation_multifactor.py`` exactly:
      * Low-Vol : negated 60-day trailing annualized realized vol (causal —
                  ``pct_change().shift(1)`` before the rolling window).
      * Size    : negated log market-cap (``log(price × CURRENT shares)``) so
                  SMALLER caps score positive.  CURRENT shares applied against
                  historical prices is an approximation (buybacks/issuance drift
                  the count) — flagged, never treated as exact.

    HONEST SCOPE (CONSTRAINT #4): Value and Quality are deliberately excluded —
    they need point-in-time fundamentals no free vendor supplies; fabricating
    18 years of P/B, P/E, ROE would violate the no-fabricated-metrics rule.
    A ticker with no shares snapshot gets a NaN Size (never a fabricated 0.0)
    and simply drops out of the Size cross-section for those rows.

    The portfolio is a daily-rebalanced, equal-weighted long-only book tilted
    into the top half of the composite each day; ``weights.shift(1)`` enforces
    no lookahead.
    """
    shares = shares or {}
    common_index = closes.dropna(how="all").index

    low_vol_cols: Dict[str, pd.Series] = {}
    size_cols: Dict[str, pd.Series] = {}
    ret_cols: Dict[str, pd.Series] = {}
    for ticker in closes.columns:
        close = closes[ticker].reindex(common_index)
        daily_returns = close.pct_change().shift(1)
        vol_60d = daily_returns.rolling(window=60).std() * np.sqrt(252)
        low_vol_cols[ticker] = -vol_60d  # low vol -> high score
        ret_cols[ticker] = close.pct_change()

        so = shares.get(ticker)
        if so:
            size_cols[ticker] = -np.log(close * float(so))  # smaller -> positive
        else:
            size_cols[ticker] = pd.Series(np.nan, index=common_index)

    low_vol_df = pd.DataFrame(low_vol_cols)
    size_df = pd.DataFrame(size_cols)
    rets_df = pd.DataFrame(ret_cols)

    low_vol_xz = _xsec_zscore(low_vol_df)
    size_xz = _xsec_zscore(size_df)
    # Where Size is entirely NaN (no shares), fall back to the Low-Vol tilt only
    # rather than blanking the whole composite.
    composite = pd.concat([low_vol_xz, size_xz]).groupby(level=0).mean()

    weights = composite.rank(axis=1, pct=True).ge(0.5).astype(float)
    weights = weights.div(weights.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)
    portfolio_returns = (weights.shift(1) * rets_df).sum(axis=1).fillna(0.0)

    X = pd.DataFrame(index=common_index)
    X["LowVol_Composite"] = low_vol_xz.mean(axis=1).fillna(0.0)
    X["Size_Composite"] = size_xz.mean(axis=1).fillna(0.0)
    y = rets_df.mean(axis=1).fillna(0.0)

    # Trim the leading realized-vol warm-up so X/y aren't dominated by NaN-zeros.
    valid_idx = X.index[60:]
    X = X.loc[valid_idx]
    y = y.loc[valid_idx]
    precomputed = {
        "Multifactor_LowVol_Size": portfolio_returns.loc[valid_idx],
    }
    return X, y, precomputed


def _make_strategy_fn(
    precomputed: Dict[str, pd.Series],
    turnover: float = 0.01,
) -> Callable:
    """Return a StrategyValidationHarness-compatible ``strategy_fn``.

    The harness calls ``strategy_fn(X_train, y_train, X_test, y_test)`` and
    expects a list of dicts with keys
    ``params`` / ``train_returns`` / ``test_returns`` / ``turnover``.
    """

    def strategy_fn(
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_test: pd.DataFrame,
        y_test: pd.Series,
    ) -> List[Dict[str, Any]]:
        configs = []
        for name, full_rets in precomputed.items():
            configs.append({
                "params": name,
                "train_returns": full_rets.loc[full_rets.index.intersection(y_train.index)],
                "test_returns": full_rets.loc[full_rets.index.intersection(y_test.index)],
                "turnover": turnover,
            })
        return configs

    return strategy_fn


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------
# Format: strategy_id → (adapter_fn, turnover, universe)
#
#   * ``adapter_fn`` returns ``(X, y, precomputed)`` — the feature matrix, the
#     daily return series, and a dict of pre-computed strategy return series.
#   * ``turnover`` — average daily turnover fed to the harness cost model.
#   * ``universe``  — list of tickers the adapter needs.  A SINGLE-name universe
#     (``["SPY"]``) means the adapter is invoked with the SPY close ``pd.Series``;
#     a MULTI-name universe means it is invoked with
#     ``(closes: pd.DataFrame, shares: Dict[str, float])``.
#
# ``run_validations`` downloads exactly the union of tickers required by the
# selected strategies (and current shares-outstanding only for multi-name
# universes).  The KEYS are the stable strategy-id strings the GUI multiselect
# reads via ``STRATEGY_REGISTRY.keys()`` — do not rename them casually.
# New strategies: add an entry here and implement the adapter above.
# ---------------------------------------------------------------------------
STRATEGY_REGISTRY: Dict[str, Tuple[Callable, float, List[str]]] = {
    "rsi2_mean_reversion": (_build_rsi2_adapter, 0.02, ["SPY"]),
    "timeseries_momentum": (_build_tsmom_adapter, 0.005, ["SPY"]),
    "macd_trend": (_build_macd_adapter, 0.03, ["SPY"]),
    "coppock_momentum": (_build_coppock_adapter, 0.01, ["SPY"]),
    "multifactor_lowvol_size": (
        _build_lowvol_size_adapter,
        0.05,
        ["AAPL", "JNJ", "XOM", "KO", "JPM", "PG", "INTC", "T"],
    ),
}


# =============================================================================
# Data download
# =============================================================================

def _download_closes(
    tickers: List[str], start_date: str, end_date: str
) -> pd.DataFrame:
    """Download adjusted closes for ``tickers`` via yfinance.

    Returns a DataFrame indexed by date with one column per successfully-fetched
    ticker (columns follow the requested order; failed tickers are simply
    absent, never fabricated).  Raises ``RuntimeError`` if nothing downloads.
    """
    import yfinance as yf

    ordered = list(dict.fromkeys(tickers))  # dedupe, preserve order
    df = yf.download(
        ordered, start=start_date, end=end_date, progress=False, auto_adjust=True
    )
    if df is None or df.empty:
        raise RuntimeError(
            f"Failed to download price data for {ordered} ({start_date}–{end_date}). "
            "Check your internet connection and try again."
        )

    if isinstance(df.columns, pd.MultiIndex):
        closes = df["Close"].copy()
    else:
        # Single-ticker download → flat OHLCV columns.
        closes = df[["Close"]].copy()
        closes.columns = [ordered[0]]

    closes.index = pd.to_datetime(closes.index)
    # Keep only requested tickers that actually returned data, in request order.
    present = [t for t in ordered if t in closes.columns]
    return closes[present]


def _download_spy(start_date: str, end_date: str) -> pd.Series:
    """Back-compat shim: SPY adjusted closes as a Series (uses _download_closes)."""
    return _download_closes(["SPY"], start_date, end_date)["SPY"]


def _download_shares(tickers: List[str]) -> Dict[str, float]:
    """Fetch CURRENT shares-outstanding snapshot per ticker via yfinance.

    A CURRENT snapshot applied against historical prices is an approximation
    (share counts drift via buybacks/issuance) — used only for the Size factor
    and flagged as such.  Per-ticker failures are logged and skipped so one bad
    symbol never aborts the batch; a missing ticker → absent from the dict (its
    Size factor degrades to NaN downstream, never a fabricated value).
    """
    import yfinance as yf

    out: Dict[str, float] = {}
    for ticker in dict.fromkeys(tickers):
        try:
            info = yf.Ticker(ticker).fast_info
            so = info.get("shares") if hasattr(info, "get") else None
            if not so:
                so = getattr(info, "shares", None)
            if so:
                out[ticker] = float(so)
        except Exception as exc:  # noqa: BLE001 — per-ticker dead-letter
            logger.warning("Shares-outstanding fetch failed for %s: %s", ticker, exc)
            continue
    return out


# =============================================================================
# Validation runner
# =============================================================================

def run_validations(
    strategies: Optional[List[str]] = None,
    start_date: str = "2005-01-01",
    end_date: Optional[str] = None,
    output_dir: Path = Path("reports"),
    n_cpcv_splits: int = 10,
    n_test_splits: int = 2,
) -> Dict[str, dict]:
    """Run walk-forward validation for each registered strategy.

    Parameters
    ----------
    strategies:
        Names to validate; ``None`` = all registered strategies.
    start_date, end_date:
        Historical window for backtesting (yfinance date strings).
    output_dir:
        Where to write JSON summaries.  Created automatically.
    n_cpcv_splits, n_test_splits:
        Passed to ``StrategyValidationHarness``.

    Returns
    -------
    dict mapping strategy_id → summary dict (same schema as
    ``ValidationReport.to_summary_dict()``; failed strategies include an
    ``"error"`` key and ``"deployable": false``).
    """
    from execution.cost_model import TieredCostModel
    from validation.harness import StrategyValidationHarness

    if end_date is None:
        end_date = date.today().isoformat()

    if strategies is None:
        strategies = list(STRATEGY_REGISTRY)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Union of tickers required by the selected (known) strategies, plus the
    # subset that additionally needs a current shares-outstanding snapshot
    # (multi-name cross-sectional universes only).
    known = [s for s in strategies if s in STRATEGY_REGISTRY]
    ticker_union = sorted({
        t for s in known for t in STRATEGY_REGISTRY[s][2]
    })
    share_tickers = sorted({
        t
        for s in known
        if len(STRATEGY_REGISTRY[s][2]) > 1
        for t in STRATEGY_REGISTRY[s][2]
    })

    closes_df: pd.DataFrame = pd.DataFrame()
    shares: Dict[str, float] = {}
    if ticker_union:
        logger.info(
            "Downloading price history for %s (%s → %s) …",
            ticker_union, start_date, end_date,
        )
        try:
            closes_df = _download_closes(ticker_union, start_date, end_date)
        except Exception as exc:
            logger.error("Cannot download price data: %s", exc)
            return {
                name: {
                    "strategy_id": name,
                    "deployable": False,
                    "error": f"Price download failed: {exc}",
                    "report_date": date.today().isoformat(),
                }
                for name in strategies
            }

        if share_tickers:
            logger.info("Fetching shares-outstanding snapshot for %s …", share_tickers)
            shares = _download_shares(share_tickers)

    cost_model = TieredCostModel()
    results: Dict[str, dict] = {}

    for name in strategies:
        if name not in STRATEGY_REGISTRY:
            logger.warning(
                "Unknown strategy '%s' — skipping. Known strategies: %s",
                name, sorted(STRATEGY_REGISTRY),
            )
            results[name] = {
                "strategy_id": name,
                "deployable": False,
                "error": f"Not in STRATEGY_REGISTRY. Known: {sorted(STRATEGY_REGISTRY)}",
                "report_date": date.today().isoformat(),
            }
            continue

        logger.info("Validating: %s", name)
        try:
            adapter_fn, turnover, universe = STRATEGY_REGISTRY[name]
            available = [t for t in universe if t in closes_df.columns]
            if not available:
                raise RuntimeError(
                    f"No price data downloaded for universe {universe} — "
                    "cannot validate this strategy."
                )

            if len(universe) == 1:
                # SPY-style single-name adapter: invoked with a pd.Series.
                X, y, precomputed = adapter_fn(closes_df[universe[0]])
            else:
                # Cross-sectional adapter: invoked with (closes_df, shares).
                X, y, precomputed = adapter_fn(closes_df[available], shares)

            if X.empty or y.empty or not precomputed:
                raise RuntimeError(
                    "Adapter returned an empty feature/return frame — "
                    "insufficient history for this start/end range."
                )

            strategy_fn = _make_strategy_fn(precomputed, turnover=turnover)

            harness = StrategyValidationHarness(
                strategy_fn=strategy_fn,
                universe_fn=lambda _, u=available: u,
                cost_model=cost_model,
                n_cpcv_splits=n_cpcv_splits,
                n_test_splits=n_test_splits,
                reports_dir=str(output_dir),
            )

            report = harness.run(
                start_date=str(X.index[0].date()),
                end_date=str(X.index[-1].date()),
                X=X,
                y=y,
                strategy_name=name,
            )

            summary = report.to_summary_dict()
            results[name] = summary
            logger.info(
                "  %-32s deployable=%-5s  Sharpe=%s  PBO=%s  DSR=%s  MaxDD=%s",
                name,
                summary.get("deployable"),
                f"{summary.get('sharpe', float('nan')):.3f}"
                if summary.get("sharpe") is not None else "  —  ",
                f"{summary.get('pbo', float('nan')):.3f}",
                f"{summary.get('dsr', float('nan')):.3f}",
                f"{summary.get('max_drawdown'):.3f}"
                if summary.get("max_drawdown") is not None else "  —  ",
            )

        except Exception as exc:  # CONSTRAINT #6 — per-strategy dead-letter
            logger.error(
                "Strategy '%s' validation failed: %s", name, exc, exc_info=True
            )
            results[name] = {
                "strategy_id": name,
                "deployable": False,
                "error": str(exc),
                "report_date": date.today().isoformat(),
            }

    return results


# =============================================================================
# CLI helpers
# =============================================================================

def _fail_reason(s: dict) -> str:
    """Re-derive which deployability gate(s) a FAIL strategy missed.

    Mirrors ``ValidationReport.deployable`` (validation/harness.py) exactly,
    reading the shared thresholds from ``validation.thresholds`` — so an
    all-green Sharpe/PBO/DSR row that still fails on Max Drawdown or the
    options-selling stress gate shows a concrete reason instead of nothing.
    Returns a compact ``, ``-joined string (empty if nothing tripped).
    """
    reasons: List[str] = []

    md = s.get("max_drawdown")
    if md is None:
        reasons.append("MaxDD n/a")
    elif float(md) >= MAX_DRAWDOWN_MAX:
        reasons.append(f"MaxDD {float(md) * 100:.0f}%>{MAX_DRAWDOWN_MAX * 100:.0f}%")

    pbo = s.get("pbo")
    if pbo is not None and not math.isnan(float(pbo)) and float(pbo) >= PBO_MAX:
        reasons.append(f"PBO {float(pbo):.2f}>{PBO_MAX:.2f}")

    dsr = s.get("dsr")
    if dsr is not None and not math.isnan(float(dsr)) and float(dsr) <= DSR_MIN:
        reasons.append(f"DSR {float(dsr):.2f}<{DSR_MIN:.2f}")

    sharpe = s.get("sharpe")
    if sharpe is None:
        reasons.append("Sharpe n/a")
    elif float(sharpe) <= NET_SHARPE_MIN:
        reasons.append(f"Sharpe {float(sharpe):.2f}<{NET_SHARPE_MIN:.2f}")

    # stress_gate_passed is True for non-options strategies (gate N/A), so an
    # explicit False here always denotes a real options-selling stress failure.
    if s.get("stress_gate_passed") is False:
        reasons.append("stress")

    return ", ".join(reasons)


def _print_summary_table(results: Dict[str, dict]) -> None:
    """Print a compact ASCII pass/fail table to stdout."""
    hdr = (
        f"  {'Strategy':<32} {'Status':<10} {'Sharpe':>7} {'PBO':>7} "
        f"{'DSR':>7} {'MaxDD':>8}  {'Reason'}"
    )
    print()
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    any_fail = False
    for name, s in results.items():
        reason = ""
        if "error" in s:
            status = "ERROR"
            any_fail = True
        elif s.get("deployable"):
            status = "✅ PASS"
        else:
            status = "❌ FAIL"
            any_fail = True
            reason = _fail_reason(s)

        def _fmt(v: Any) -> str:
            if v is None or (isinstance(v, float) and math.isnan(v)):
                return "   —  "
            return f"{float(v):.3f}"

        def _fmt_pct(v: Any) -> str:
            if v is None or (isinstance(v, float) and math.isnan(v)):
                return "   —  "
            return f"{float(v) * 100:.1f}%"

        print(
            f"  {name:<32} {status:<10} "
            f"{_fmt(s.get('sharpe')):>7} "
            f"{_fmt(s.get('pbo')):>7} "
            f"{_fmt(s.get('dsr')):>7} "
            f"{_fmt_pct(s.get('max_drawdown')):>8}  "
            f"{reason}"
        )

    print()
    if any_fail:
        print("⚠️  One or more strategies did not meet deployability thresholds.")
        print("   See reports/<strategy>_validation_summary.json for details.")
    else:
        print("✅  All strategies passed validation gates.")
    print()


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point.  Returns exit code 0 on all-pass, 1 on any failure."""
    parser = argparse.ArgumentParser(
        prog="scripts.refresh_validations",
        description="Run walk-forward validation for registered strategies (monthly cadence).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--strategies", type=str, default=None,
        help=(
            "Comma-separated strategy names to validate. "
            f"Default: all ({', '.join(sorted(STRATEGY_REGISTRY))})."
        ),
    )
    parser.add_argument(
        "--start", dest="start_date", type=str, default="2005-01-01",
        metavar="YYYY-MM-DD", help="Backtest start date (default: 2005-01-01).",
    )
    parser.add_argument(
        "--end", dest="end_date", type=str, default=None,
        metavar="YYYY-MM-DD", help="Backtest end date (default: today).",
    )
    parser.add_argument(
        "--output-dir", type=str, default="reports",
        help="Directory for JSON report output (default: reports/).",
    )
    parser.add_argument(
        "--n-cpcv-splits", type=int, default=10,
        help="Number of CPCV splits (default: 10).",
    )
    parser.add_argument(
        "--n-test-splits", type=int, default=2,
        help="Walk-forward test splits (default: 2).",
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help=(
            "Also print ONE machine-readable JSON line (the LAST line of stdout) "
            "mapping strategy_id → {deployable, pbo, dsr, sharpe, max_drawdown"
            "[, error]}. The human pass/fail table is still printed above it."
        ),
    )
    args = parser.parse_args(argv)

    strats: Optional[List[str]] = (
        [s.strip() for s in args.strategies.split(",") if s.strip()]
        if args.strategies
        else None
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    )

    results = run_validations(
        strategies=strats,
        start_date=args.start_date,
        end_date=args.end_date,
        output_dir=Path(args.output_dir),
        n_cpcv_splits=args.n_cpcv_splits,
        n_test_splits=args.n_test_splits,
    )

    _print_summary_table(results)

    if args.as_json:
        # One machine-readable line (the LAST json-parseable line of stdout) so
        # the GUI can parse results without scraping the human table.
        json_out = {
            sid: {
                "deployable": bool(s.get("deployable", False)),
                "pbo": s.get("pbo"),
                "dsr": s.get("dsr"),
                "sharpe": s.get("sharpe"),
                "max_drawdown": s.get("max_drawdown"),
                **({"error": s["error"]} if "error" in s else {}),
            }
            for sid, s in results.items()
        }
        print(json.dumps(json_out))

    any_fail = any(
        "error" in s or not s.get("deployable", False) for s in results.values()
    )
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
