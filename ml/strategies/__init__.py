"""
ml.strategies — Strategy Specifications Package (qlib-style, no qlib dependency)
=================================================================================
The "Strategy Layer" in the three-tier architecture. Strategies CONSUME model
outputs (from ml/models/) and emit trade decisions. The existing rule-based and
signal-module strategies live in signals/ and strategy_engine.py; this package
provides typed specs that link an ML model to a signal module and document the
connection for Gravity audits.

StrategySpec is a lightweight data container — it does NOT execute trades. The
execution path remains: main_orchestrator.py → StrategyEngine → signals/.

Usage
-----
>>> spec = StrategySpec(
...     model=LGBMCrossSectionalRanker.load_latest(),
...     signal_id="lgbm_ranker",
...     description="LambdaRank cross-sectional ranker feeding LGBMRankerSignal",
... )
>>> current_scores = spec.score(universe_df)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from ml.models.base import Model


@dataclass
class StrategySpec:
    """Links an ML model to its corresponding SignalModule.

    Fields
    ------
    model :
        A trained ``ml.models.base.Model`` instance.
    signal_id :
        The ``SignalModule.name`` that consumes this model's output
        (e.g., ``"lgbm_ranker"``, ``"timeseries_momentum"``).
    description :
        Human-readable description for Gravity audits and reporting.
    meta_labeler_signal_ids :
        Signal IDs for which this model acts as a MetaLabeler.
        Empty list = this is a primary model, not a meta-model.
    """
    model: Model
    signal_id: str
    description: str = ""
    meta_labeler_signal_ids: list[str] = field(default_factory=list)

    def score(self, X: pd.DataFrame) -> pd.Series:
        """Generate raw model scores for the current cross-section.

        Returns pd.Series indexed by X's index (ticker), values = raw predictions.
        """
        preds = self.model.predict(X)
        return pd.Series(preds, index=X.index, name=self.signal_id)

    @property
    def is_meta_labeler(self) -> bool:
        return bool(self.meta_labeler_signal_ids)


__all__ = ["StrategySpec"]
