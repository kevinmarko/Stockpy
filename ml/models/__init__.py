"""
ml.models — Model abstractions package (qlib-style, no qlib dependency).

Provides:
  Model   — Abstract base class every ML model in this platform must implement.
  All concrete model implementations live alongside this package or in submodules;
  import them directly (e.g., ``from ml.lgbm_ranker import LGBMCrossSectionalRanker``).
"""

from ml.models.base import Model

__all__ = ["Model"]
