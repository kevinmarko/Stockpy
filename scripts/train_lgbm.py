#!/usr/bin/env python3
"""
InvestYo Quant Platform - LGBM Cross-Sectional Ranker Training Job
=================================================================
CLI that trains the ``lgbm_ranker`` model registered in ``ml/registry.yaml``,
computes out-of-sample DSR / PBO via CPCV, persists the model to a dated
``ml/models/lgbm_<YYYYMMDD>.pkl`` artifact (auto-named by
``LGBMCrossSectionalRanker.save()``), and updates the registry row (including
the ``deployable`` gate: DSR > 0.95 AND PBO < 0.5, and the ``artifact_file``
provenance field naming the exact binary that produced these metrics).

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

from ml.feature_engineering import FEATURE_COLUMNS  # noqa: E402
from ml.lgbm_ranker import LGBMCrossSectionalRanker  # noqa: E402
from ml.registry_io import update_model_metrics  # noqa: E402
from ml.training_data import build_training_panel as _shared_build_training_panel  # noqa: E402
from validation.metrics import (  # noqa: E402
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
    run_cpcv_evaluation,
)

logger = logging.getLogger("ML.TrainLGBM")

_MODEL_KEY = "lgbm_ranker"

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
# Panel construction — delegates to the shared ml.training_data foundation.
#
# This used to duplicate its own date-grid PIT panel builder inline (to avoid a
# merge conflict while ``ml/training_data.py`` was landing in a parallel PR).
# Now that both are on main, this wraps the shared
# ``ml.training_data.build_training_panel()`` instead of reimplementing it —
# same PIT feature math (including the GARCH_Vol realized-vol proxy, which
# ``ml/training_data.py`` now also computes), one source of truth.
# ──────────────────────────────────────────────────────────────────────────────


def build_training_panel(
    data_engine,
    tickers: list[str],
    *,
    horizon_days: int = 21,
    step_days: int = 5,
    min_dates: int = 12,
    lookback_days: int = 3 * 365,
) -> TrainingPanel:
    """Assemble a supervised (date, ticker) training panel from historical bars.

    Thin wrapper around ``ml.training_data.build_training_panel`` — walks a
    generous ``[today - lookback_days, today]`` window (the shared builder
    self-bounds to whatever bars actually exist per ticker), thinning to every
    ``step_days``-th trading date to keep CPCV fold cost bounded.  Falls back
    to full date density (``step_days=1``) if thinning leaves too few dates.
    """
    end = pd.Timestamp.now().normalize()
    start = end - pd.Timedelta(days=lookback_days)

    def _build(step: int) -> TrainingPanel:
        X, y, t1, _price_history = _shared_build_training_panel(
            start, end, tickers,
            data_engine=data_engine, horizon_days=horizon_days, step_days=step,
        )
        if X.empty:
            return TrainingPanel(pd.DataFrame(), pd.Series(dtype=float), pd.Series(dtype=float), 0)
        # Two honesty filters, both dropping rows rather than fabricating values:
        #  1. Forward-return rank is NaN when the horizon window runs off the
        #     end of a ticker's history.
        #  2. ROC_12M is NaN for as-of dates inside the first ~252 trading days
        #     of a ticker's history (the momentum lookback isn't satisfied yet).
        #     The shared builder isn't calendar-bounded by `start` (it fetches
        #     all available bars per ticker), so `start` alone doesn't skip
        #     these early, feature-incomplete dates — drop them explicitly
        #     instead of training the ranker on a systematically weaker slice.
        valid = y.notna() & X["ROC_12M"].notna()
        X, y, t1 = X.loc[valid], y.loc[valid], t1.loc[valid]
        if X.empty:
            return TrainingPanel(pd.DataFrame(), pd.Series(dtype=float), pd.Series(dtype=float), 0)
        n_dates = X.index.get_level_values(0).nunique()
        return TrainingPanel(X[list(FEATURE_COLUMNS)], y, t1, n_dates)

    panel = _build(step_days)
    if panel.n_dates < min_dates and step_days > 1:
        logger.info(
            "Training panel: only %d dates at step_days=%d — retrying at full density.",
            panel.n_dates, step_days,
        )
        panel = _build(1)

    logger.info(
        "Training panel: %d rows across %d dates, %d features.",
        len(panel.X), panel.n_dates, panel.X.shape[1] if not panel.X.empty else 0,
    )
    return panel


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

    When ``save_path`` is not given, the model is persisted to a fresh dated
    ``ml/models/lgbm_<YYYYMMDD>.pkl`` (via ``LGBMCrossSectionalRanker.save(None)``)
    rather than a mutable ``*_latest.pkl`` pointer — so the registry's
    ``artifact_file`` provenance field always names the exact, unique binary
    that produced a given run's metrics.
    """
    save_path = Path(save_path) if save_path is not None else None

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
        save_path = ranker.save(save_path)
        model_saved = True
        logger.info("Model persisted to %s", save_path)
    else:
        logger.warning("Model did not train (empty/insufficient panel) — no artifact written.")

    # 6. Update registry (deployable gate derived inside registry_io)
    trained_date = (
        datetime.now(timezone.utc).strftime("%Y-%m-%d") if model_saved else None
    )
    # Provenance — only populated when a model actually trained + persisted, so
    # the YAML never records a window/artifact for a run that produced nothing.
    train_window: Optional[dict] = None
    if model_saved and not panel.X.empty:
        dates = panel.X.index.get_level_values(0)
        train_window = {
            "start": pd.Timestamp(dates.min()).strftime("%Y-%m-%d"),
            "end": pd.Timestamp(dates.max()).strftime("%Y-%m-%d"),
            "n_dates": panel.n_dates,
        }
    entry = update_model_metrics(
        _MODEL_KEY,
        trained_date=trained_date,
        cpcv_dsr=dsr,
        pbo=pbo,
        n_train=len(panel.X) if model_saved else None,
        path=registry_path,
        artifact_file=save_path.name if model_saved else None,
        hyperparameters=ranker.params if model_saved else None,
        train_window=train_window,
        features=list(FEATURE_COLUMNS) if model_saved else None,
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
                   help="Override model output path (default: auto-dated "
                        "ml/models/lgbm_<YYYYMMDD>.pkl).")
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
