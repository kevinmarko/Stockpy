"""
InvestYo Quant Platform - Forecast Backfill Registry Bridge
===========================================================
Bridges ``ml/forecast_backfill.py``'s screen-trained RandomForest
meta-labelers into the same PBO/DSR-gated live registry
(``ml/registry.yaml`` -> ``ml/meta_bootstrap.py::bootstrap_meta_registry()``
-> ``signals/aggregator.py``'s live meta-label confidence gate) that
``scripts/train_meta_labelers.py`` already feeds for the 2 AFML-trained
signals -- see ``ml/meta_bootstrap.py``'s module docstring for the full
picture (AFML-priority tie-break, feature-compatibility gate).

Both public functions here are dead-letter safe (CONSTRAINT #6): any
internal failure is caught and degrades to an honest "not registered"
result, never raises, and never fabricates a DSR/PBO value (CONSTRAINT #4).
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from settings import settings

logger = logging.getLogger("ML.ForecastBackfillRegistryBridge")

# Must be an exact match with the 6 signals that declare meta_label_features
# in ml/forecast_backfill.py's active_strategies universe.
BACKFILL_ELIGIBLE_SIGNAL_IDS = frozenset([
    "timeseries_momentum",
    "cross_sectional_momentum",
    "rsi2_mean_reversion",
    "sector_quality_rank",
    "vrp_premium_selling",
    "options_flow_sentiment",
])

DEFAULT_LIVE_HORIZON_DAYS = 10


def resolve_live_horizon(signal_id: str) -> int:
    """Resolve the single "live" horizon (of the 4 the backfill screen
    trains) used for the registry bridge for ``signal_id``. Settings are
    read fresh on every call (not cached at import time) so a runtime
    settings change takes effect without a process restart."""
    default = getattr(
        settings, "META_LABELING_BACKFILL_DEFAULT_HORIZON_DAYS", DEFAULT_LIVE_HORIZON_DAYS
    )
    overrides = getattr(settings, "META_LABELING_BACKFILL_LIVE_HORIZON_DAYS", {}) or {}
    return int(overrides.get(signal_id, default))


# >= 2 candidate configs so run_cpcv_evaluation's DSR/PBO measure genuine
# selection bias (n_trials > 1) rather than trivially collapsing to a
# single-candidate constant -- mirrors scripts/train_meta_labelers.py's own
# _CANDIDATE_PARAMS pattern.
_CANDIDATE_PARAMS: List[Dict[str, Any]] = [
    {"min_samples_leaf": 5},
    {"min_samples_leaf": 10},
    {"min_samples_leaf": 20},
]


def compute_backfill_cpcv_metrics(
    X: pd.DataFrame,
    signal_sign: pd.Series,
    target: pd.Series,
    dates: pd.Series,
    horizon_days: int,
    *,
    theta_c: float = 0.5,
    n_estimators: int = 100,
    max_depth: int = 4,
    random_state: int = 42,
    n_splits: int = 6,
    n_test_splits: int = 2,
    min_events: int = 100,
) -> Dict[str, Any]:
    """Run genuine CPCV over the backfill screen's (X, target) panel and
    derive DSR/PBO via ``validation.metrics.run_cpcv_evaluation`` -- the
    same validated primitive ``scripts/train_meta_labelers.py`` /
    ``scripts/train_lgbm.py`` call. Never reimplements DSR/PBO math.

    ``target`` is the binary "was the primary signal's direction correct at
    this horizon" label (1/0); ``signal_sign`` is the primary signal's own
    direction (+1/-1) for the same rows. Each fold's candidate classifiers
    predict P(correct); a position is taken when that probability clears
    ``theta_c``, and the realized return is ``position * signal_sign *
    (2*target - 1)`` -- a discrete +-1-per-event outcome, the same
    construction ``scripts/train_meta_labelers.py::compute_cpcv_metrics``
    uses for its own meta-gated returns proxy.

    Returns ``{"cpcv_dsr", "pbo", "mean_oos_sharpe"}``, each honestly
    ``None`` (never fabricated -- CONSTRAINT #4) when there are fewer than
    ``min_events`` rows or CPCV produces zero paths. ``mean_oos_max_dd`` is
    deliberately never included: this discrete +-1 outcome series isn't a
    compoundable capital-fraction return, matching the AFML path's own
    documented limitation (see that function's docstring for the full
    reasoning). Never raises.
    """
    empty = {"cpcv_dsr": None, "pbo": None, "mean_oos_sharpe": None}

    if X is None or len(X) < min_events:
        logger.warning(
            "compute_backfill_cpcv_metrics: only %d events (< %d) -- "
            "metrics stay null (honest).",
            0 if X is None else len(X), min_events,
        )
        return empty

    from sklearn.ensemble import RandomForestClassifier

    from validation.metrics import run_cpcv_evaluation

    try:
        # Re-index onto a plain date index (not a MultiIndex) for
        # CombinatorialPurgedCV -- mirrors ml/forecast_backfill.py's own
        # step_5_backtrain_meta_labelers' EXACT working CPCV usage
        # (X_dates/t1 both indexed by date, not by X's original MultiIndex).
        date_index = pd.DatetimeIndex(pd.Series(dates).values)
        feat_cols = list(X.columns)
        X_dates = pd.DataFrame(X.values, index=date_index, columns=feat_cols)
        # Stash signal_sign as a hidden column so strategy_fn can recover it
        # from each fold's row slice -- the CPCV splitter only carries X/y
        # through positionally, matching scripts/train_meta_labelers.py's
        # own "_yp"/"_yb" hidden-column convention.
        X_dates = X_dates.assign(_signal_sign=pd.Series(signal_sign).values)
        y_dates = pd.Series(pd.Series(target).values.astype(int), index=date_index)
        t1 = pd.Series(date_index + pd.Timedelta(days=horizon_days), index=date_index)

        def strategy_fn(X_tr, y_tr, X_te, y_te):
            """Fit candidate RandomForestClassifiers on the fold; return
            genuinely independent IS/OOS gated-return trials."""
            try:
                if len(X_tr) < 30 or len(X_te) < 8:
                    return []
                sign_tr = X_tr["_signal_sign"].to_numpy()
                sign_te = X_te["_signal_sign"].to_numpy()
                Xf_tr = X_tr[feat_cols]
                Xf_te = X_te[feat_cols]
                outcome_tr = sign_tr * (2 * y_tr.to_numpy(dtype=int) - 1)
                outcome_te = sign_te * (2 * y_te.to_numpy(dtype=int) - 1)

                trials: List[Dict[str, Any]] = []
                for params in _CANDIDATE_PARAMS:
                    clf = RandomForestClassifier(
                        n_estimators=n_estimators,
                        max_depth=max_depth,
                        random_state=random_state,
                        n_jobs=-1,
                        **params,
                    )
                    clf.fit(Xf_tr, y_tr)
                    if len(clf.classes_) != 2:
                        continue
                    proba_tr = clf.predict_proba(Xf_tr)[:, 1]
                    proba_te = clf.predict_proba(Xf_te)[:, 1]
                    position_tr = (proba_tr >= theta_c).astype(int)
                    position_te = (proba_te >= theta_c).astype(int)
                    train_ret = pd.Series(position_tr * outcome_tr, index=X_tr.index)
                    test_ret = pd.Series(position_te * outcome_te, index=X_te.index)
                    trials.append({
                        "params": str(params),
                        "train_returns": train_ret,
                        "test_returns": test_ret,
                    })
                return trials
            except Exception as exc:  # dead-letter: a bad fold must not abort CPCV
                logger.debug("compute_backfill_cpcv_metrics fold strategy_fn failed: %s", exc)
                return []

        result = run_cpcv_evaluation(
            strategy_fn=strategy_fn,
            X=X_dates,
            y=y_dates,
            t1=t1,
            n_splits=n_splits,
            n_test_splits=n_test_splits,
        )
        if not result.get("paths"):
            logger.warning("compute_backfill_cpcv_metrics: CPCV produced no paths -- metrics stay null (honest).")
            return empty

        return {
            "cpcv_dsr": float(result["dsr"]),
            "pbo": float(result["pbo"]),
            "mean_oos_sharpe": float(result["mean_oos_sharpe"]),
        }
    except Exception as exc:
        logger.warning("compute_backfill_cpcv_metrics: cpcv evaluation failed: %s", exc)
        return empty


def register_backfill_model(
    signal_id: str,
    horizon_days: int,
    model: Any,
    feature_names: List[str],
    n_train: int,
    cpcv_result: Dict[str, Any],
    hyperparameters: Dict[str, Any],
    train_window: Dict[str, Any],
    registry_path: Optional[Any] = None,
) -> Tuple[bool, Optional[str]]:
    """Wrap ``model`` as a ``MetaLabeler`` and, if it passes the
    feature-compatibility gate, persist it under the ``backfill_meta_``
    prefix and update ``ml/registry.yaml``'s ``meta_labeler_backfill_<signal_id>``
    row via the SAME ``ml.registry_io.update_model_metrics`` the AFML path
    uses -- ``deployable`` is always re-derived from ``cpcv_dsr``/``pbo``
    there, never passed in, so the gate can never be spoofed from here.

    Returns ``(registered, skip_reason)``. Never raises (CONSTRAINT #6): any
    failure -- including an incompatible feature set -- returns
    ``(False, <reason>)`` without ever saving a pickle or touching the
    registry for a feature-incompatible model.
    """
    logger = logging.getLogger(__name__)

    try:
        from ml.meta_bootstrap import check_feature_compatibility

        is_compat, missing = check_feature_compatibility(feature_names)
        if not is_compat:
            return False, f"incompatible_features_missing_{','.join(missing)}"

        from path_confinement import is_confined

        from ml.meta_labeling import MetaLabeler, _MODELS_DIR

        labeler = MetaLabeler.from_fitted(
            signal_id=signal_id,
            model=model,
            feature_names=feature_names,
            n_train_samples=n_train,
            trained_at=datetime.now(timezone.utc),
        )

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"backfill_meta_{signal_id}_{horizon_days}d_{stamp}.pkl"
        filepath = (_MODELS_DIR / filename).resolve()
        if not is_confined(filepath, _MODELS_DIR.resolve()):
            raise ValueError(f"Refusing to write model artifact outside {_MODELS_DIR}: {filepath}")
        labeler.save(filepath)

        from ml.registry_io import update_model_metrics

        update_model_metrics(
            model_key=f"meta_labeler_backfill_{signal_id}",
            trained_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            cpcv_dsr=cpcv_result.get("cpcv_dsr"),
            pbo=cpcv_result.get("pbo"),
            n_train=n_train,
            path=registry_path,
            artifact_file=filename,
            hyperparameters=hyperparameters,
            train_window=train_window,
            features=feature_names,
            cpcv_mean_oos_sharpe=cpcv_result.get("mean_oos_sharpe"),
            cpcv_mean_oos_max_dd=None,
        )
        return True, None
    except Exception as exc:
        logger.warning(
            "register_backfill_model: failed to register backfill model for %r: %s",
            signal_id, exc,
        )
        return False, str(exc)
