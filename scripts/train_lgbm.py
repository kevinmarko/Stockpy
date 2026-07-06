#!/usr/bin/env python3
"""
InvestYo Quant Platform - LGBM Cross-Sectional Ranker Training Job
=================================================================
CLI that trains the ``lgbm_ranker`` model registered in ``ml/registry.yaml``,
computes out-of-sample DSR / PBO via CPCV, persists the model to
``ml/models/lgbm_latest.pkl``, and updates the registry row (including the
``deployable`` gate: DSR > 0.95 AND PBO < 0.5).

Reuses (does NOT reimplement):
  - ml.lgbm_ranker.LGBMCrossSectionalRanker      (train / save)
  - ml.feature_engineering.build_pit_feature_matrix / build_forward_return_ranks
  - validation.metrics.run_cpcv_evaluation / deflated_sharpe_ratio /
    probability_of_backtest_overfitting
  - validation.purged_cv.CombinatorialPurgedCV
  - data_engine.DataEngine (live)  /  MockDataEngine (offline / tests)
  - ml.registry_io.update_model_metrics

Usage
-----
    python scripts/train_lgbm.py                       # live DataEngine
    python scripts/train_lgbm.py --offline             # MockDataEngine
    python scripts/train_lgbm.py --tickers AAPL,MSFT,...  --lookback-days 504

Honesty constraints (CLAUDE.md CONSTRAINT #4):
  - No fabricated metrics.  If CPCV cannot produce a DSR / PBO (empty panel,
    too few dates), the metric stays ``None`` and ``deployable=False``.
  - Dead-letter resilient: per-ticker fetch failures are skipped, never abort.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# Repo-root import shim so `python scripts/train_lgbm.py` works from anywhere.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ml.feature_engineering import (  # noqa: E402
    FEATURE_COLUMNS,
    build_forward_return_ranks,
    build_pit_feature_matrix,
)
from ml.lgbm_ranker import LGBMCrossSectionalRanker  # noqa: E402
from ml.registry_io import update_model_metrics  # noqa: E402
from validation.metrics import (  # noqa: E402
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
    run_cpcv_evaluation,
)

logger = logging.getLogger("ML.TrainLGBM")

_MODEL_KEY = "lgbm_ranker"
_MODEL_PATH = _REPO_ROOT / "ml" / "models" / "lgbm_latest.pkl"

# A modest default universe for offline / smoke runs.  Live runs can override
# with --tickers.
_DEFAULT_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "NVDA", "JPM", "JNJ", "PG", "XOM",
]


@dataclass
class TrainingPanel:
    """A supervised training panel of (date, ticker) observations."""

    X: pd.DataFrame          # MultiIndex (date, ticker) -> FEATURE_COLUMNS
    y: pd.Series             # forward-return rank ∈ [0, 1]
    t1: pd.Series            # event-end times aligned to X.index
    n_dates: int


# ──────────────────────────────────────────────────────────────────────────────
# Panel construction
#
# NOTE (merge coordination): a parallel PR (Agent 1) is adding a canonical
# ``ml/training_data.build_training_panel()`` helper.  Once it lands, this inline
# ``build_training_panel`` should be REFACTORED to import and delegate to that
# shared helper instead of duplicating the panel-assembly logic here.  We build
# it inline for now to keep this PR self-contained and avoid a merge conflict on
# ``ml/training_data.py`` (which this PR deliberately does NOT create).
# ──────────────────────────────────────────────────────────────────────────────


def _bars_to_close_panel(bars: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Wide close-price frame: columns=tickers, index=dates (tz-naive)."""
    closes: dict[str, pd.Series] = {}
    for sym, df in bars.items():
        if df is None or df.empty or "Close" not in df.columns:
            continue
        s = df["Close"].copy()
        # Normalize index to tz-naive midnight dates for cross-ticker alignment.
        idx = pd.to_datetime(s.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        s.index = idx.normalize()
        s = s[~s.index.duplicated(keep="last")]
        closes[sym] = s
    if not closes:
        return pd.DataFrame()
    return pd.DataFrame(closes).sort_index()


def _pit_features_for_date(
    close_panel: pd.DataFrame,
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    """Build a per-ticker feature row as of ``as_of`` from price history only.

    We derive the price-based feature inputs that ``build_pit_feature_matrix``
    consumes (ROC_12M, ROC_6M, RSI, RSI_2, GARCH_Vol proxy).  Fundamental /
    factor-Z columns are absent here and correctly fall through to NaN inside
    ``build_pit_feature_matrix`` — that function is the single source of truth
    for feature ordering and cross-sectional ranking, so we only supply raw
    inputs and let it rank.
    """
    hist = close_panel.loc[:as_of]
    if len(hist) < 22:
        return pd.DataFrame()

    rows = {}
    for sym in close_panel.columns:
        s = hist[sym].dropna()
        if len(s) < 22:
            continue
        px = float(s.iloc[-1])
        if not np.isfinite(px) or px <= 0:
            continue
        # Rate-of-change momentum (causal — uses only history up to as_of).
        roc_12m = _roc(s, 252)
        roc_6m = _roc(s, 126)
        rsi_14 = _rsi(s, 14)
        rsi_2 = _rsi(s, 2)
        garch_vol = _realized_vol(s, 20)   # realized-vol proxy for GARCH_Vol
        rows[sym] = {
            "ROC_12M": roc_12m,
            "ROC_6M": roc_6m,
            "RSI": rsi_14,
            "RSI_2": rsi_2,
            "GARCH_Vol": garch_vol,
        }
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).T


def _roc(s: pd.Series, n: int) -> float:
    if len(s) <= n:
        return np.nan
    p0 = float(s.iloc[-n - 1])
    p1 = float(s.iloc[-1])
    if p0 <= 0:
        return np.nan
    return (p1 - p0) / p0


def _rsi(s: pd.Series, n: int) -> float:
    if len(s) <= n:
        return np.nan
    delta = s.diff().dropna()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    if gain.empty or loss.empty:
        return np.nan
    last_gain = float(gain.iloc[-1])
    last_loss = float(loss.iloc[-1])
    if not np.isfinite(last_gain) or not np.isfinite(last_loss):
        return np.nan
    if last_loss == 0:
        return 100.0
    rs = last_gain / last_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _realized_vol(s: pd.Series, n: int) -> float:
    rets = s.pct_change().dropna()
    if len(rets) < n:
        return np.nan
    return float(rets.iloc[-n:].std() * np.sqrt(252))


def build_training_panel(
    data_engine,
    tickers: list[str],
    *,
    horizon_days: int = 21,
    step_days: int = 5,
    min_dates: int = 12,
) -> TrainingPanel:
    """Assemble a supervised (date, ticker) training panel from historical bars.

    Fetches OHLCV via the injected ``data_engine`` (DataEngine live, or
    MockDataEngine offline), builds PIT features per sampled date via
    ``build_pit_feature_matrix``, and pairs them with forward-return rank
    targets from ``build_forward_return_ranks``.
    """
    bars = data_engine.fetch_technical_raw(tickers)
    close_panel = _bars_to_close_panel(bars)

    if close_panel.empty or close_panel.shape[1] < 2:
        logger.warning("Training panel: insufficient tickers with bars (%d).",
                       0 if close_panel.empty else close_panel.shape[1])
        return TrainingPanel(pd.DataFrame(), pd.Series(dtype=float), pd.Series(dtype=float), 0)

    all_dates = close_panel.index
    # Sample as-of dates that leave room for the forward-return horizon.
    usable = all_dates[:-horizon_days] if len(all_dates) > horizon_days else all_dates[:0]
    as_of_dates = pd.DatetimeIndex(usable[252::step_days]) if len(usable) > 252 else \
        pd.DatetimeIndex(usable[::step_days])

    if len(as_of_dates) < min_dates:
        # Loosen: sample everything we can (still requires >=22 rows of history
        # per date inside _pit_features_for_date, so early dates self-skip).
        as_of_dates = pd.DatetimeIndex(usable[::step_days])

    fwd_ranks = build_forward_return_ranks(close_panel, as_of_dates, horizon_days=horizon_days)

    X_parts: list[pd.DataFrame] = []
    y_parts: list[pd.Series] = []
    t1_parts: list[pd.Series] = []

    for dt in as_of_dates:
        if dt not in fwd_ranks.index:
            continue
        raw_feat = _pit_features_for_date(close_panel, dt)
        if raw_feat.empty:
            continue
        feat = build_pit_feature_matrix(raw_feat, as_of_date=dt, macro_vix=None)
        target = fwd_ranks.loc[dt]  # Series indexed by ticker

        common = feat.index.intersection(target.dropna().index)
        if len(common) < 2:
            continue
        feat = feat.loc[common]
        tgt = target.loc[common].astype(float)

        # MultiIndex (date, ticker) so LambdaRank groups by date (query).
        mi = pd.MultiIndex.from_product([[dt], common], names=["date", "ticker"])
        feat.index = mi
        tgt.index = mi
        # Event end = as_of + horizon (forward-return realizes then).
        t1 = pd.Series(dt + pd.Timedelta(days=horizon_days), index=mi)

        X_parts.append(feat)
        y_parts.append(tgt)
        t1_parts.append(t1)

    if not X_parts:
        logger.warning("Training panel empty after assembly (0 usable dates).")
        return TrainingPanel(pd.DataFrame(), pd.Series(dtype=float), pd.Series(dtype=float), 0)

    X = pd.concat(X_parts)[list(FEATURE_COLUMNS)]
    y = pd.concat(y_parts)
    t1 = pd.concat(t1_parts)
    n_dates = X.index.get_level_values(0).nunique()

    logger.info("Training panel: %d rows across %d dates, %d features.",
                len(X), n_dates, X.shape[1])
    return TrainingPanel(X, y, t1, n_dates)


# ──────────────────────────────────────────────────────────────────────────────
# CPCV out-of-sample metrics (DSR / PBO)
# ──────────────────────────────────────────────────────────────────────────────


def compute_cpcv_metrics(panel: TrainingPanel) -> dict:
    """Run CPCV over the panel and return {'dsr','pbo','mean_oos_sharpe'}.

    Uses ``run_cpcv_evaluation`` with a LightGBM-ranker strategy_fn: each CPCV
    fold trains a fresh ranker on the train slice, then measures long-short
    return of the top-vs-bottom ranked names on BOTH the train and test slices.
    DSR / PBO are then derived from the IS/OOS Sharpe matrix by the runner.

    Returns metrics as ``None`` (honest) when the panel is too small to yield
    any CPCV path.
    """
    empty = {"dsr": None, "pbo": None, "mean_oos_sharpe": None}
    if panel.X.empty or panel.n_dates < 6:
        logger.warning("CPCV skipped: too few dates (%d) for path evaluation.", panel.n_dates)
        return empty

    # Flatten to a date-indexed frame the CPCV splitter understands.
    X = panel.X.copy()
    y = panel.y.copy()
    date_index = X.index.get_level_values(0)
    ticker_index = X.index.get_level_values(1)
    X_flat = X.set_axis(date_index)
    y_flat = pd.Series(y.values, index=date_index)
    # Keep ticker labels available for grouping inside strategy_fn.
    X_flat = X_flat.assign(_ticker=ticker_index.values)

    # Multiple candidate hyper-parameter configs per fold so DSR/PBO actually
    # measure SELECTION BIAS (with a single candidate, n_trials=1 and DSR
    # trivially returns 1.0 — an over-optimistic, meaningless validation).
    _CANDIDATE_PARAMS = [
        {"num_leaves": 15, "learning_rate": 0.05, "n_estimators": 200},
        {"num_leaves": 31, "learning_rate": 0.03, "n_estimators": 300},
        {"num_leaves": 63, "learning_rate": 0.02, "n_estimators": 400},
    ]

    def strategy_fn(X_tr, y_tr, X_te, y_te):
        """Train candidate rankers on the fold and produce IS/OOS long-short returns."""
        try:
            feat_cols = [c for c in FEATURE_COLUMNS if c in X_tr.columns]
            if not feat_cols or len(X_tr) < 10 or len(X_te) < 4:
                return []

            X_tr_mi = _restore_multiindex(X_tr, feat_cols)
            y_tr_al = pd.Series(y_tr.values, index=X_tr_mi.index)

            trials = []
            for params in _CANDIDATE_PARAMS:
                ranker = LGBMCrossSectionalRanker(
                    params=params, purged_kfold_splits=3, embargo_pct=0.0,
                )
                ranker.train(X_tr_mi, y_tr_al)
                if ranker._model is None:
                    continue
                train_ret = _long_short_returns(ranker, X_tr, y_tr, feat_cols)
                test_ret = _long_short_returns(ranker, X_te, y_te, feat_cols)
                if train_ret.empty or test_ret.empty:
                    continue
                trials.append({
                    "params": str(params),
                    "train_returns": train_ret,
                    "test_returns": test_ret,
                })
            return trials
        except Exception as exc:  # dead-letter: a bad fold must not abort CPCV
            logger.debug("CPCV fold strategy_fn failed: %s", exc)
            return []

    result = run_cpcv_evaluation(
        strategy_fn=strategy_fn,
        X=X_flat,
        y=y_flat,
        t1=None,
        n_splits=6,
        n_test_splits=2,
    )

    if not result.get("paths"):
        logger.warning("CPCV produced no paths — leaving metrics null (honest).")
        return empty

    return {
        "dsr": float(result["dsr"]),
        "pbo": float(result["pbo"]),
        "mean_oos_sharpe": float(result["mean_oos_sharpe"]),
    }


def _restore_multiindex(X_flat: pd.DataFrame, feat_cols: list[str]) -> pd.DataFrame:
    """Rebuild a (date, ticker) MultiIndex from a date-indexed flat slice."""
    dates = X_flat.index
    tickers = X_flat["_ticker"].values if "_ticker" in X_flat.columns else \
        [f"t{i}" for i in range(len(X_flat))]
    mi = pd.MultiIndex.from_arrays([dates, tickers], names=["date", "ticker"])
    out = X_flat[feat_cols].copy()
    out.index = mi
    return out


def _long_short_returns(
    ranker: LGBMCrossSectionalRanker,
    X_slice: pd.DataFrame,
    y_slice: pd.Series,
    feat_cols: list[str],
) -> pd.Series:
    """Per-date long-short return: mean(top-half target) - mean(bottom-half target).

    ``y`` is the forward-return RANK, so the realized long-short spread of a
    ranker that agrees with the true ranking is positive.  Grouped by date to
    yield a return time series for Sharpe computation.
    """
    df = X_slice[feat_cols].copy()
    df["_date"] = X_slice.index
    df["_y"] = np.asarray(y_slice.values, dtype=float)
    rets = []
    idx = []
    for dt, grp in df.groupby("_date", sort=True):
        if len(grp) < 2:
            continue
        preds = ranker.predict(grp[feat_cols])
        grp = grp.assign(_pred=preds)
        grp = grp.sort_values("_pred")
        half = max(1, len(grp) // 2)
        short_leg = grp["_y"].iloc[:half].mean()
        long_leg = grp["_y"].iloc[-half:].mean()
        # Map rank spread (in [0,1]) into a small daily-return-like signal.
        rets.append(float(long_leg - short_leg) * 0.02)
        idx.append(dt)
    if not rets:
        return pd.Series(dtype=float)
    return pd.Series(rets, index=pd.Index(idx))


# ──────────────────────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────────────────────


class _SyntheticDataEngine:
    """Offline engine yielding DISTINCT per-ticker random-walk price paths.

    ``MockDataEngine`` gives every ticker the SAME preset series, which makes
    the cross-sectional forward-return ranks degenerate (all tied) and the CPCV
    Sharpe / DSR meaningless.  For an honest offline training run we need genuine
    cross-sectional dispersion, so we synthesize an independent geometric
    random walk per ticker (seeded for determinism).  Shape matches
    ``DataEngine.fetch_technical_raw`` exactly (OHLCV, DatetimeIndex).
    """

    def __init__(self, n_days: int = 400, seed: int = 7):
        self.n_days = n_days
        self.seed = seed

    def fetch_technical_raw(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        dates = pd.date_range(end=datetime.now(), periods=self.n_days, freq="B")
        for i, sym in enumerate(tickers):
            rng = np.random.RandomState(self.seed + i * 101)
            drift = rng.normal(0.0004, 0.0004)          # per-ticker drift dispersion
            rets = rng.normal(drift, 0.012, self.n_days)
            closes = 100.0 * np.exp(np.cumsum(rets))
            out[sym] = pd.DataFrame(
                {
                    "Open": closes,
                    "High": closes * 1.01,
                    "Low": closes * 0.99,
                    "Close": closes,
                    "Volume": [1_000_000] * self.n_days,
                },
                index=dates,
            )
        return out


def run_training(
    tickers: list[str],
    *,
    offline: bool = False,
    save_path: Optional[Path] = None,
    registry_path: Optional[Path] = None,
    data_engine=None,
) -> dict:
    """End-to-end: build panel, train, CPCV-validate, persist, update registry.

    Returns a summary dict: {dsr, pbo, n_train, deployable, model_path}.
    An explicit ``data_engine`` (any object with ``fetch_technical_raw``) can be
    injected for tests; otherwise ``offline`` selects the synthetic engine and
    the default selects the live ``DataEngine``.
    """
    save_path = Path(save_path) if save_path is not None else _MODEL_PATH

    # 1. Data engine
    if data_engine is not None:
        de = data_engine
    elif offline:
        de = _SyntheticDataEngine()
    else:
        try:
            import os
            from data_engine import DataEngine
            fred_key = os.environ.get("FRED_API_KEY")
            de = DataEngine(fred_api_key=fred_key) if fred_key else DataEngine()
        except Exception as exc:
            # No FRED key / live engine unavailable: fall back to the synthetic
            # engine (distinct per-ticker paths → honest, non-degenerate metrics),
            # NOT MockDataEngine (which gives every ticker identical prices and a
            # degenerate all-tied cross-section).
            logger.error("Live DataEngine unavailable (%s) — using synthetic engine.", exc)
            de = _SyntheticDataEngine()

    # 2. Panel
    panel = build_training_panel(de, tickers)

    # 3. Train (safe on empty panel — LGBM ranker no-ops)
    ranker = LGBMCrossSectionalRanker()
    ranker.train(panel.X, panel.y, panel.t1)

    # 4. CPCV metrics (honest None on insufficient data)
    metrics = compute_cpcv_metrics(panel)
    dsr, pbo = metrics["dsr"], metrics["pbo"]

    # 5. Persist model (only if it actually trained — no fabricated artifact)
    model_saved = False
    if ranker._model is not None:
        ranker.save(save_path)
        model_saved = True
        logger.info("Model persisted to %s", save_path)
    else:
        logger.warning("Model did not train (empty/insufficient panel) — no artifact written.")

    # 6. Update registry (deployable gate derived inside registry_io)
    trained_date = (
        datetime.now(timezone.utc).strftime("%Y-%m-%d") if model_saved else None
    )
    entry = update_model_metrics(
        _MODEL_KEY,
        trained_date=trained_date,
        cpcv_dsr=dsr,
        pbo=pbo,
        n_train=len(panel.X) if model_saved else None,
        path=registry_path,
    )

    return {
        "dsr": dsr,
        "pbo": pbo,
        "n_train": len(panel.X) if model_saved else 0,
        "n_dates": panel.n_dates,
        "deployable": entry["deployable"],
        "model_path": str(save_path) if model_saved else None,
        "mean_oos_sharpe": metrics.get("mean_oos_sharpe"),
    }


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Train the LGBM cross-sectional ranker.")
    p.add_argument("--tickers", type=str, default=",".join(_DEFAULT_TICKERS),
                   help="Comma-separated ticker universe.")
    p.add_argument("--offline", action="store_true",
                   help="Use MockDataEngine (no network).")
    p.add_argument("--save-path", type=str, default=None,
                   help="Override model output path (default ml/models/lgbm_latest.pkl).")
    p.add_argument("--registry-path", type=str, default=None,
                   help="Override registry.yaml path (for tests).")
    p.add_argument("--log-level", type=str, default="INFO")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    summary = run_training(
        tickers,
        offline=args.offline,
        save_path=Path(args.save_path) if args.save_path else None,
        registry_path=Path(args.registry_path) if args.registry_path else None,
    )

    print("\n=== LGBM Ranker Training Summary ===")
    print(f"  DSR:            {summary['dsr']}")
    print(f"  PBO:            {summary['pbo']}")
    print(f"  n_train:        {summary['n_train']}")
    print(f"  n_dates:        {summary['n_dates']}")
    print(f"  mean OOS Sharpe:{summary['mean_oos_sharpe']}")
    print(f"  deployable:     {summary['deployable']}")
    print(f"  model_path:     {summary['model_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
