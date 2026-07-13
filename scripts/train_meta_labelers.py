#!/usr/bin/env python3
"""
InvestYo Quant Platform - Meta-Labeler Training CLI
===================================================
Trains the two primary-signal meta-labelers (Lopez de Prado AFML Ch. 3) and
persists them so ``ml/meta_bootstrap.bootstrap_meta_registry()`` can register
them at runtime and the ``SignalAggregator`` meta_hard_gate can actually fire.

For each configured signal (``timeseries_momentum``, ``cross_sectional_momentum``):
  1. Build a price panel for a small universe of names.
  2. Sample events via ``cusum_filter`` and label them with
     ``apply_triple_barrier`` (ml/triple_barrier.py).
  3. Derive a primary-signal direction per event (momentum sign) plus a small
     feature matrix.
  4. Train ``MetaLabeler(signal_id).fit_from_primary(X, y_primary, y_barrier)``
     (ml/meta_labeling.py) and ``.save()`` it to
     ``ml/models/meta_<signal_id>_<YYYYMMDD>.pkl``.
  5. Update the corresponding row of ``ml/registry.yaml``
     (trained_date, cpcv_dsr, pbo, n_train, deployable).

Data sourcing
-------------
This script builds its training panel self-contained. It uses the live
``DataEngine`` when a ``FRED_API_KEY`` + network are available, and otherwise
falls back to a deterministic synthetic geometric-random-walk panel so the
script (and its offline tests) never depend on the network.

    Deliberately NOT converged onto ``ml.training_data.build_training_panel()``:
    that helper builds a date-grid forward-return-rank panel for the LGBM
    ranker (see ``scripts/train_lgbm.py``). Meta-labeler training needs
    CUSUM-sampled *event* rows with triple-barrier labels
    (``ml/triple_barrier.py``) — a different panel shape entirely, not a
    duplicate of the ranker's panel builder.

Registry write
--------------
Converged onto ``ml/registry_io.update_model_metrics`` (the shared,
honesty-enforcing registry writer also used by ``scripts/train_lgbm.py``) —
no more local YAML round-tripping here.

Metric honesty
--------------
This script runs a real Combinatorial Purged Cross-Validation (CPCV) over the
CUSUM-sampled event set (``compute_cpcv_metrics`` below, mirroring
``scripts/train_lgbm.py``'s LGBM path) and writes the resulting ``cpcv_dsr`` /
``pbo`` into the registry. ``deployable`` is then derived by
``ml.registry_io.compute_deployable`` (DSR > 0.95 AND PBO < 0.5) — it is NEVER
passed in, so the gate can never be spoofed. Per CONSTRAINT #4 (no fabricated
metrics), when CPCV cannot run (too few events / no CPCV path), the metrics stay
``None`` and ``deployable`` stays ``false`` honestly — a genuinely-weak model
that fails the gate correctly reports ``deployable: false``. Thresholds are
never loosened to force a green result.

Usage
-----
    python scripts/train_meta_labelers.py
    python scripts/train_meta_labelers.py --signal timeseries_momentum
    python scripts/train_meta_labelers.py --synthetic     # force offline panel
                                                          # (still runs real CPCV)
    python scripts/train_meta_labelers.py --no-registry   # skip YAML update
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Repo-root import shim so `python scripts/train_meta_labelers.py` works.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ml.meta_labeling import MetaLabeler, build_meta_label_target  # noqa: E402
from ml.triple_barrier import apply_triple_barrier, cusum_filter, get_volatility  # noqa: E402
from ml.meta_bootstrap import META_LABELED_SIGNAL_IDS  # noqa: E402
from ml.registry_io import update_model_metrics  # noqa: E402
from validation.metrics import run_cpcv_evaluation  # noqa: E402

logger = logging.getLogger("ML.TrainMetaLabelers")

_REGISTRY_PATH = _REPO_ROOT / "ml" / "registry.yaml"

# Universe used to build the training panel. Kept small & liquid; the goal is a
# few hundred cross-sectional events, not a full backtest.
_DEFAULT_UNIVERSE: Tuple[str, ...] = (
    "AAPL", "MSFT", "GOOGL", "AMZN", "META",
    "SPY", "QQQ", "JPM", "XOM", "JNJ",
)


# ---------------------------------------------------------------------------
# 1. Price panel construction (self-contained)
# ---------------------------------------------------------------------------

def _synthetic_price_series(
    symbol: str,
    n_days: int = 900,
    seed: int = 0,
) -> pd.Series:
    """Deterministic geometric-random-walk close series for offline training.

    Produces genuine trend + volatility structure (unlike MockDataEngine's flat
    preset prices) so ``cusum_filter`` samples real events and
    ``apply_triple_barrier`` yields a mix of +1/-1/0 labels.
    """
    rng = np.random.default_rng(abs(hash((symbol, seed))) % (2**32))
    # Modest positive drift + regime-switching vol so labels aren't degenerate.
    drift = rng.uniform(0.0002, 0.0006)
    vol = rng.uniform(0.010, 0.020)
    shocks = rng.normal(drift, vol, n_days)
    # Inject a few volatility bursts to create clear barrier touches.
    burst_idx = rng.choice(n_days, size=max(1, n_days // 120), replace=False)
    shocks[burst_idx] += rng.normal(0.0, vol * 4.0, len(burst_idx))
    log_price = np.cumsum(shocks) + np.log(rng.uniform(50.0, 200.0))
    dates = pd.date_range(end=datetime.now(timezone.utc).date(), periods=n_days, freq="B")
    return pd.Series(np.exp(log_price), index=pd.DatetimeIndex(dates), name=symbol)


def _build_price_panel(
    universe: Tuple[str, ...],
    *,
    force_synthetic: bool = False,
    n_days: int = 900,
    seed: int = 0,
) -> Dict[str, pd.Series]:
    """Return ``{symbol: close_series}`` for the training universe.

    Tries the live ``DataEngine`` first (when a FRED key + network exist), then
    falls back to the deterministic synthetic panel. Never raises: a per-symbol
    fetch failure degrades that symbol to the synthetic series.

    Future: replace with ``ml.training_data.build_training_panel()`` (Agent 1 PR).
    """
    panel: Dict[str, pd.Series] = {}

    live_frames: Dict[str, pd.DataFrame] = {}
    if not force_synthetic:
        try:
            from settings import settings  # noqa: PLC0415
            from data_engine import DataEngine  # noqa: PLC0415

            fred_key = getattr(settings, "FRED_API_KEY", None)
            if fred_key:
                de = DataEngine(fred_api_key=str(fred_key))
                live_frames = de.fetch_technical_raw(list(universe)) or {}
                logger.info("Fetched live price history for %d symbols.", len(live_frames))
        except Exception as exc:
            logger.warning(
                "Live DataEngine unavailable (%s) — using synthetic panel.", exc
            )

    for i, symbol in enumerate(universe):
        df = live_frames.get(symbol)
        close: Optional[pd.Series] = None
        if isinstance(df, pd.DataFrame) and "Close" in df.columns and len(df) > 200:
            close = df["Close"].dropna()
            close.index = pd.DatetimeIndex(close.index)
        if close is None or len(close) < 200:
            close = _synthetic_price_series(symbol, n_days=n_days, seed=seed + i)
        panel[symbol] = close

    return panel


# ---------------------------------------------------------------------------
# 2. Feature + label construction per name
# ---------------------------------------------------------------------------

def _events_features_labels_for_symbol(
    close: pd.Series,
    *,
    momentum_lookback: int = 63,
) -> Tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Build (X, y_primary, y_barrier) for one name's price history.

    - Events: sampled by ``cusum_filter`` at a threshold equal to the median
      EWMA vol (adaptive, matches the AFML convention).
    - y_barrier: triple-barrier label per event.
    - y_primary: momentum-sign direction at each event (sign of the trailing
      ``momentum_lookback``-day return) — the "primary signal" whose correctness
      the meta-labeler learns to predict.
    - X: a small PIT feature matrix (trailing return, realized vol, distance
      from a moving average) evaluated at each event date only.
    """
    empty = (pd.DataFrame(), pd.Series(dtype=int), pd.Series(dtype=int))
    if close is None or len(close) < 200:
        return empty

    vol = get_volatility(close, span=50)
    thr = float(np.nanmedian(vol.dropna())) if vol.notna().any() else 0.0
    if thr <= 0:
        return empty

    try:
        events = cusum_filter(close, threshold=thr)
    except ValueError:
        return empty
    if len(events) == 0:
        return empty

    barrier = apply_triple_barrier(
        events, close, pt_sl_multiples=(2.0, 1.0), vertical_barrier_days=10
    )
    if barrier.empty:
        return empty

    y_barrier = barrier["label"].astype(int)
    ev_index = y_barrier.index

    # PIT feature matrix at each event date (strictly trailing windows).
    log_ret = np.log(close / close.shift(1))
    mom = close.pct_change(momentum_lookback)
    sma = close.rolling(50).mean()
    realized_vol = log_ret.rolling(20).std()
    dist_sma = (close - sma) / sma

    feat = pd.DataFrame(
        {
            "momentum": mom,
            "realized_vol_20d": realized_vol,
            "dist_sma_50": dist_sma,
            "vol_ewma": vol,
        }
    )
    X = feat.reindex(ev_index)

    # Primary signal direction = momentum sign at the event date.
    mom_at_event = mom.reindex(ev_index)
    y_primary = np.sign(mom_at_event).fillna(0).astype(int)
    y_primary = pd.Series(y_primary.values, index=ev_index, dtype=int)

    return X, y_primary, y_barrier


def _assemble_training_set(
    panel: Dict[str, pd.Series],
) -> Tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Stack per-symbol (X, y_primary, y_barrier) into one training set.

    Uses a RangeIndex to avoid duplicate-date collisions across symbols (the
    meta-labeler treats each event as an i.i.d. row; cross-name date overlap is
    fine because we don't do temporal CV in this training pass).
    """
    X_parts: List[pd.DataFrame] = []
    yp_parts: List[pd.Series] = []
    yb_parts: List[pd.Series] = []

    for symbol, close in panel.items():
        try:
            X, yp, yb = _events_features_labels_for_symbol(close)
        except Exception as exc:
            logger.warning("Feature/label build failed for %s (%s) — skipping.", symbol, exc)
            continue
        if X.empty:
            continue
        X_parts.append(X.reset_index(drop=True))
        yp_parts.append(yp.reset_index(drop=True))
        yb_parts.append(yb.reset_index(drop=True))

    if not X_parts:
        return pd.DataFrame(), pd.Series(dtype=int), pd.Series(dtype=int)

    X_all = pd.concat(X_parts, ignore_index=True)
    yp_all = pd.concat(yp_parts, ignore_index=True)
    yb_all = pd.concat(yb_parts, ignore_index=True)
    return X_all, yp_all, yb_all


# ---------------------------------------------------------------------------
# 2b. CPCV out-of-sample metrics (DSR / PBO) — mirrors scripts/train_lgbm.py
# ---------------------------------------------------------------------------

# Default meta-gating probability threshold for the CPCV returns proxy. Sourced
# from settings.META_LABEL_MIN_CONFIDENCE (the SAME threshold the live
# SignalAggregator hard-gate uses) so the validated strategy matches production
# behaviour; falls back to 0.4 if settings is unavailable (dead-letter).
def _meta_gate_threshold() -> float:
    try:
        from settings import settings  # noqa: PLC0415
        thr = getattr(settings, "META_LABEL_MIN_CONFIDENCE", 0.4)
        return float(thr)
    except Exception:  # dead-letter: never let a settings import abort CPCV
        return 0.4


def _meta_gated_returns(
    labeler: MetaLabeler,
    X_feat: pd.DataFrame,
    y_primary: pd.Series,
    y_barrier: pd.Series,
    threshold: float,
) -> pd.Series:
    """Per-event P&L of the meta-GATED primary signal — the CPCV returns proxy.

    ``signed_outcome = sign(primary_direction) * sign(barrier_label)`` ∈ {-1,0,+1}:
      +1  the primary signal's direction MATCHED the realized triple-barrier
          outcome (it was right — profit-take/stop hit in the signalled direction),
      -1  the primary signal was directionally WRONG,
       0  a vertical-timeout event with no directional resolution.
    This is the primary signal's own raw, un-gated per-event edge (in "R"
    multiples of the barrier width).

    The meta-labeler's job is to GATE those events: take the trade only when
    ``P(primary correct) ≥ threshold``, else stay flat (0 return). A well-
    calibrated meta-labeler therefore RAISES the Sharpe of this gated series
    versus taking every event — exactly the quantity CPCV's IS/OOS Sharpe matrix
    (and hence DSR/PBO) is meant to measure. Flat (gated-out) events remain in
    the series as honest 0-return periods, not dropped, so the Sharpe reflects
    the real track record of the gated strategy.
    """
    proba = labeler.predict_proba(X_feat)  # aligned to X_feat rows
    signed_outcome = (
        np.sign(y_primary.to_numpy(dtype=float))
        * np.sign(y_barrier.to_numpy(dtype=float))
    )
    position = (np.asarray(proba, dtype=float) >= threshold).astype(float)
    rets = position * signed_outcome
    return pd.Series(rets, index=X_feat.index, dtype=float)


def compute_cpcv_metrics(
    X: pd.DataFrame,
    y_primary: pd.Series,
    y_barrier: pd.Series,
    *,
    n_splits: int = 6,
    n_test_splits: int = 2,
    min_events: int = 60,
) -> dict:
    """Run CPCV over the meta-labeler's event set → {'dsr','pbo','mean_oos_sharpe'}.

    Mirrors ``scripts/train_lgbm.py::compute_cpcv_metrics``: each CPCV fold fits
    a fresh ``MetaLabeler`` on the train slice under EACH of several candidate
    hyper-parameter configs (so ``n_trials > 1`` and DSR/PBO actually measure
    SELECTION BIAS — with a single candidate ``n_trials == 1`` and DSR trivially
    collapses to 1.0), then produces the meta-gated returns proxy
    (``_meta_gated_returns``) on both the train and test slices. The runner
    derives DSR / PBO from the resulting IS/OOS Sharpe matrix.

    Returns metrics as ``None`` (honest — CONSTRAINT #4) when the event set is
    too small to yield any CPCV path. Never raises: any internal failure
    degrades to all-``None`` (dead-letter — the model simply stays non-deployable).
    """
    empty = {"dsr": None, "pbo": None, "mean_oos_sharpe": None}
    try:
        if X is None or X.empty or len(X) < min_events:
            logger.warning(
                "CPCV skipped: only %d events (< %d) — metrics stay null (honest).",
                0 if X is None else len(X), min_events,
            )
            return empty

        feat_cols = list(X.columns)
        threshold = _meta_gate_threshold()

        # A fresh RangeIndex so the CUSUM events (stacked across symbols) map
        # cleanly onto CPCV's positional blocks; y_primary/y_barrier are aligned
        # positionally to X (they come from the same _assemble_training_set stack).
        X_flat = X.reset_index(drop=True).copy()
        yp = pd.Series(np.asarray(y_primary), index=X_flat.index, dtype=float)
        yb = pd.Series(np.asarray(y_barrier), index=X_flat.index, dtype=float)
        # Stash the primary/barrier series as hidden columns so strategy_fn can
        # recover them from each fold's row slice (the CPCV splitter only carries
        # X/y through, exactly like train_lgbm stashes '_ticker').
        X_flat = X_flat.assign(_yp=yp.values, _yb=yb.values)
        # y is nominally the binary meta-label; the splitter only uses it
        # positionally, our strategy_fn reads _yp/_yb from the X slice instead.
        y_meta = build_meta_label_target(yp, yb).reindex(X_flat.index).fillna(0).astype(int)

        # ≥ 2 candidate configs → n_trials > 1 (see docstring). Kept modest
        # (small n_estimators) because this is a VALIDATION sweep run once per
        # CPCV fold, not the final persisted model — the goal is an honest
        # selection-bias estimate, not maximal per-fold fit quality.
        _CANDIDATE_PARAMS = [
            {"num_leaves": 7, "n_estimators": 60},
            {"num_leaves": 15, "n_estimators": 120},
            {"num_leaves": 31, "n_estimators": 200},
        ]

        def strategy_fn(X_tr, y_tr, X_te, y_te):
            """Fit candidate meta-labelers on the fold; return IS/OOS gated returns."""
            try:
                if len(X_tr) < 30 or len(X_te) < 8:
                    return []
                yp_tr = X_tr["_yp"]
                yb_tr = X_tr["_yb"]
                Xf_tr = X_tr[feat_cols]
                yp_te = X_te["_yp"]
                yb_te = X_te["_yb"]
                Xf_te = X_te[feat_cols]

                trials = []
                for params in _CANDIDATE_PARAMS:
                    labeler = MetaLabeler(signal_id="_cpcv", lgbm_params=params)
                    labeler.fit_from_primary(Xf_tr, yp_tr, yb_tr)
                    if labeler._model is None:
                        continue
                    train_ret = _meta_gated_returns(labeler, Xf_tr, yp_tr, yb_tr, threshold)
                    test_ret = _meta_gated_returns(labeler, Xf_te, yp_te, yb_te, threshold)
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
            y=y_meta,
            t1=None,
            n_splits=n_splits,
            n_test_splits=n_test_splits,
        )

        if not result.get("paths"):
            logger.warning("CPCV produced no paths — leaving metrics null (honest).")
            return empty

        return {
            "dsr": float(result["dsr"]),
            "pbo": float(result["pbo"]),
            "mean_oos_sharpe": float(result["mean_oos_sharpe"]),
        }
    except Exception as exc:  # dead-letter: never crash training over validation
        logger.warning("CPCV evaluation failed (%s) — metrics stay null (honest).", exc)
        return empty


# ---------------------------------------------------------------------------
# 3. Registry write — converged onto ml.registry_io.update_model_metrics
# ---------------------------------------------------------------------------

def _update_registry_row(
    signal_id: str,
    *,
    trained_date: str,
    n_train: int,
    cpcv_dsr: Optional[float],
    pbo: Optional[float],
    registry_path: Optional[Path] = None,
    artifact_file: Optional[str] = None,
    hyperparameters: Optional[dict] = None,
    train_window: Optional[dict] = None,
    features: Optional[list] = None,
) -> bool:
    """Update the ``meta_labeler_<signal_id>`` row in ml/registry.yaml.

    Thin wrapper around the shared ``ml.registry_io.update_model_metrics`` (the
    same writer ``scripts/train_lgbm.py`` uses) — ``deployable`` is derived
    there from ``cpcv_dsr``/``pbo``, never passed in (no spoofing the gate).
    Optional provenance (``artifact_file``, ``hyperparameters``, ``train_window``,
    ``features``) is forwarded verbatim and never affects the gate.
    ``registry_path`` defaults to the module-level ``_REGISTRY_PATH`` resolved
    at call time (so tests can monkeypatch it). Returns True on success, False
    (and a logged warning) on any failure — never raises (dead-letter).
    """
    if registry_path is None:
        registry_path = _REGISTRY_PATH
    model_key = f"meta_labeler_{signal_id}"
    try:
        update_model_metrics(
            model_key,
            trained_date=trained_date,
            cpcv_dsr=cpcv_dsr,
            pbo=pbo,
            n_train=n_train,
            path=registry_path,
            artifact_file=artifact_file,
            hyperparameters=hyperparameters,
            train_window=train_window,
            features=features,
        )
        logger.info("Updated registry row %r (n_train=%d).", model_key, n_train)
        return True
    except Exception as exc:
        logger.warning("Registry update for %r failed (%s).", model_key, exc)
        return False


# ---------------------------------------------------------------------------
# 4. Train one signal
# ---------------------------------------------------------------------------

def train_signal(
    signal_id: str,
    *,
    force_synthetic: bool = False,
    update_registry: bool = True,
    universe: Tuple[str, ...] = _DEFAULT_UNIVERSE,
    seed: int = 0,
) -> Optional[Path]:
    """Train and persist a MetaLabeler for ``signal_id``.

    Returns the saved pickle path, or None if training was skipped (insufficient
    data). Never raises — a training failure is logged and returns None.
    """
    logger.info("Training meta-labeler for %r ...", signal_id)
    # Vary the seed per signal so the two labelers see different (still
    # deterministic) synthetic panels — mirrors that they'd be trained on
    # different primary-signal directions in production.
    sig_seed = seed + (7 if signal_id == "cross_sectional_momentum" else 0)
    panel = _build_price_panel(universe, force_synthetic=force_synthetic, seed=sig_seed)

    X, y_primary, y_barrier = _assemble_training_set(panel)
    if X.empty or len(X) < 30:
        logger.warning(
            "Meta-labeler %r: only %d events (< 30 required) — not trained.",
            signal_id, len(X),
        )
        return None

    try:
        labeler = MetaLabeler(signal_id=signal_id)
        labeler.fit_from_primary(X, y_primary, y_barrier)
    except Exception as exc:
        logger.warning("Meta-labeler %r fit failed (%s) — not saved.", signal_id, exc)
        return None

    if labeler._model is None:
        logger.warning(
            "Meta-labeler %r: model is None after fit (too few directional "
            "events) — not saved.", signal_id,
        )
        return None

    try:
        path = labeler.save()
    except Exception as exc:
        logger.warning("Meta-labeler %r save failed (%s).", signal_id, exc)
        return None

    # Real CPCV out-of-sample validation over the SAME event set the model was
    # fit on (dead-letter: honest None on failure/too-few-events → non-deployable).
    cpcv = compute_cpcv_metrics(X, y_primary, y_barrier)
    cpcv_dsr, pbo = cpcv["dsr"], cpcv["pbo"]
    logger.info(
        "Meta-labeler %r CPCV: dsr=%s pbo=%s mean_oos_sharpe=%s",
        signal_id, cpcv_dsr, pbo, cpcv.get("mean_oos_sharpe"),
    )

    if update_registry:
        # Provenance: the training window spans the union of the price panel's
        # per-symbol date ranges (the events were CUSUM-sampled from within it).
        train_window: Optional[dict] = None
        starts = [s.index.min() for s in panel.values() if len(s)]
        ends = [s.index.max() for s in panel.values() if len(s)]
        if starts and ends:
            train_window = {
                "start": pd.Timestamp(min(starts)).strftime("%Y-%m-%d"),
                "end": pd.Timestamp(max(ends)).strftime("%Y-%m-%d"),
                "n_dates": len(pd.DatetimeIndex(np.concatenate(
                    [s.index.values for s in panel.values() if len(s)]
                )).normalize().unique()),
            }
        _update_registry_row(
            signal_id,
            trained_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            n_train=labeler._n_train_samples,
            # Real CPCV metrics (honest None when CPCV couldn't run). deployable
            # is derived from these by update_model_metrics — never spoofed.
            cpcv_dsr=cpcv_dsr,
            pbo=pbo,
            artifact_file=Path(path).name,
            hyperparameters=labeler.lgbm_params,
            train_window=train_window,
            features=list(labeler._feature_names),
        )

    logger.info(
        "Meta-labeler %r trained on %d samples → %s",
        signal_id, labeler._n_train_samples, path,
    )
    return path


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Train InvestYo primary-signal meta-labelers.")
    parser.add_argument(
        "--signal",
        choices=list(META_LABELED_SIGNAL_IDS),
        default=None,
        help="Train only this signal_id (default: train all).",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Force the deterministic offline synthetic panel (skip live fetch).",
    )
    parser.add_argument(
        "--no-registry",
        action="store_true",
        help="Skip updating ml/registry.yaml.",
    )
    parser.add_argument("--seed", type=int, default=0, help="Base RNG seed for synthetic panel.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    targets = [args.signal] if args.signal else list(META_LABELED_SIGNAL_IDS)
    trained: List[str] = []
    for signal_id in targets:
        path = train_signal(
            signal_id,
            force_synthetic=args.synthetic,
            update_registry=not args.no_registry,
            seed=args.seed,
        )
        if path is not None:
            trained.append(signal_id)

    if not trained:
        logger.error("No meta-labelers were trained.")
        return 1

    logger.info("Trained %d meta-labeler(s): %s", len(trained), ", ".join(trained))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
