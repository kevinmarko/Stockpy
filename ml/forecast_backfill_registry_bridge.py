"""
InvestYo Quant Platform - Forecast Backfill Registry Bridge
===========================================================
"""

import logging
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
import pandas as pd

from settings import settings

# Must be exact match with the 6 eligible signals
BACKFILL_ELIGIBLE_SIGNAL_IDS = frozenset([
    "timeseries_momentum",
    "cross_sectional_momentum",
    "rsi2_mean_reversion",
    "sector_quality_rank",
    "vrp_premium_selling",
    "options_flow_sentiment"
])

DEFAULT_LIVE_HORIZON_DAYS = getattr(settings, "META_LABELING_BACKFILL_DEFAULT_HORIZON_DAYS", 10)

def resolve_live_horizon(signal_id: str) -> int:
    return getattr(settings, "META_LABELING_BACKFILL_LIVE_HORIZON_DAYS", {}).get(signal_id, DEFAULT_LIVE_HORIZON_DAYS)

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
    min_events: int = 100
) -> Dict[str, Any]:
    
    if len(X) < min_events:
        return {
            "cpcv_dsr": None,
            "pbo": None,
            "mean_oos_sharpe": None
        }

    from validation.metrics import run_cpcv_evaluation
    from sklearn.ensemble import RandomForestClassifier

    def strategy_fn(X_train, y_train, X_test):
        if len(X_train) == 0:
            return pd.Series(0, index=X_test.index)
            
        clf1 = RandomForestClassifier(n_estimators=n_estimators, max_depth=max_depth, min_samples_leaf=5, random_state=random_state)
        clf2 = RandomForestClassifier(n_estimators=n_estimators, max_depth=max_depth, min_samples_leaf=10, random_state=random_state)
        clf3 = RandomForestClassifier(n_estimators=n_estimators, max_depth=max_depth, min_samples_leaf=20, random_state=random_state)
        
        preds = []
        for clf in [clf1, clf2, clf3]:
            try:
                clf.fit(X_train, y_train)
                # Ensure binary classification output (not single class)
                if len(clf.classes_) == 2:
                    p = clf.predict_proba(X_test)[:, 1]
                    preds.append(p)
            except Exception:
                pass
                
        if not preds:
            return pd.Series(0, index=X_test.index)
            
        avg_proba = sum(preds) / len(preds)
        return pd.Series(avg_proba, index=X_test.index)

    try:
        metrics = run_cpcv_evaluation(
            X=X,
            signal_sign=signal_sign,
            target=target,
            dates=dates,
            strategy_fn=strategy_fn,
            n_splits=n_splits,
            n_test_splits=n_test_splits,
            horizon_days=horizon_days,
            theta_c=theta_c
        )
        return {
            "cpcv_dsr": metrics.get("dsr"),
            "pbo": metrics.get("pbo"),
            "mean_oos_sharpe": metrics.get("mean_oos_sharpe")
        }
    except Exception as exc:
        logging.getLogger(__name__).warning("compute_backfill_cpcv_metrics: cpcv evaluation failed: %s", exc)
        return {
            "cpcv_dsr": None,
            "pbo": None,
            "mean_oos_sharpe": None
        }

def register_backfill_model(
    signal_id: str, 
    horizon_days: int, 
    model: Any, 
    feature_names: List[str], 
    n_train: int, 
    cpcv_result: Dict[str, Any], 
    hyperparameters: Dict[str, Any], 
    train_window: str, 
    registry_path: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    
    logger = logging.getLogger(__name__)

    try:
        from ml.meta_bootstrap import check_feature_compatibility
        is_compat, missing = check_feature_compatibility(feature_names)
        if not is_compat:
            return False, f"incompatible_features_missing_{','.join(missing)}"
            
        from ml.meta_labeling import MetaLabeler
        # Wrap the fitted model
        labeler = MetaLabeler.from_fitted(
            signal_id=signal_id,
            model=model,
            feature_names=feature_names,
            n_train_samples=n_train,
            trained_at=datetime.utcnow()
        )
        
        # Save model
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"backfill_meta_{signal_id}_{horizon_days}d_{stamp}.pkl"
        
        from config import MODELS_DIR
        filepath = MODELS_DIR / filename
        labeler.save(filepath)
        
        # Update registry
        from ml.registry_io import update_model_metrics
        update_model_metrics(
            model_key=f"meta_labeler_backfill_{signal_id}",
            cpcv_dsr=cpcv_result.get("cpcv_dsr"),
            pbo=cpcv_result.get("pbo"),
            n_train=n_train,
            hyperparameters=hyperparameters,
            train_window=train_window,
            registry_path=registry_path
        )
        return True, None
    except Exception as exc:
        logger.warning(
            "register_backfill_model: failed to register backfill model for %r: %s", 
            signal_id, exc
        )
        return False, str(exc)
