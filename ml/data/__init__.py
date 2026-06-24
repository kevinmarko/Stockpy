"""
ml.data — Point-in-Time Feature Data Layer (qlib-style, no qlib dependency)
============================================================================
This package is the "Data Server" tier in the three-layer qlib-style architecture:

  ml/data/     ← THIS PACKAGE  (PIT features, label construction, caching)
  ml/models/   ← Model ABC + concrete implementations
  ml/strategies/ ← Strategy specs that consume model outputs

Exports
-------
build_pit_feature_matrix   — (from ml.feature_engineering) today's cross-section
build_forward_return_ranks — (from ml.feature_engineering) labeling targets
build_meta_features        — extend the base feature matrix for MetaLabeler
"""

from ml.feature_engineering import (
    build_pit_feature_matrix,
    build_forward_return_ranks,
    FEATURE_COLUMNS,
)

from ml.data.store import PITFeatureStore

__all__ = [
    "build_pit_feature_matrix",
    "build_forward_return_ranks",
    "build_meta_features",
    "FEATURE_COLUMNS",
    "PITFeatureStore",
]


def build_meta_features(
    base_features: "pd.DataFrame",
    primary_score: "pd.Series | None" = None,
) -> "pd.DataFrame":
    """Extend base PIT features with primary-signal score for MetaLabeler.

    The meta-model needs to condition on "how confident is the primary signal?"
    as a feature, in addition to the raw market features.

    Parameters
    ----------
    base_features :
        DataFrame from ``build_pit_feature_matrix``, indexed by ticker or date.
    primary_score :
        The primary signal's own score (e.g., SignalOutput.score ∈ [-1, 1]).
        If None, the column is omitted (model uses only market features).

    Returns
    -------
    pd.DataFrame with all base_features columns plus optionally ``primary_score``.
    """
    import pandas as pd
    out = base_features.copy()
    if primary_score is not None:
        out["primary_score"] = primary_score.reindex(out.index)
    return out
