"""
InvestYo Quant Platform - Signal Aggregator
===========================================
Aggregates pluggable quantitative signal scores into a single final score.

Stage 1.7 addition: ``aggregate()`` now also computes ``meta_label_composite``
— the geometric mean of all *active* modules' ``SignalOutput.meta_label_proba``
values. Since every current module defaults ``meta_label_proba=1.0``, the
composite is always 1.0 (multiplicative no-op). ``StrategyEngine`` multiplies
the final Kelly Target by it; when Stage 4 wires real meta-labels, only the
relevant module's ``compute()`` needs to return a sub-1.0 value.
"""

import math
from typing import Dict, List, Tuple
import pandas as pd

from signals.base import SignalContext, SignalOutput
from signals.registry import SignalRegistry
from settings import settings


class SignalAggregator:
    """Aggregates multiple signal module outputs using configured weights."""
    
    def __init__(self, registry: SignalRegistry, weights: Dict[str, float] = None):
        self.registry = registry
        self.weights = weights if weights is not None else settings.SIGNAL_WEIGHTS

    def aggregate(
        self, row: pd.Series, context: SignalContext
    ) -> Tuple[float, List[str], List[str], List[str], Dict[str, SignalOutput], float]:
        """
        Computes all signals and aggregates their scores using the configured weights.

        Returns a 6-tuple: ``(final_score, score_log, warnings, details, outputs,
        meta_label_composite)``.

        ``meta_label_composite`` is the geometric mean of ``meta_label_proba``
        across all *active* modules (i.e. those not suppressed by
        ``is_active_in_regime()``). Since all current modules default
        ``meta_label_proba=1.0``, this value is always 1.0 until Stage 4
        wires real meta-label probabilities. It is returned here so
        ``StrategyEngine`` can multiply the final Kelly Target by it without
        needing access to the raw ``outputs`` dict.

        Args:
            row: pandas Series representing indicators.
            context: SignalContext holding DTO objects.
            
        Returns:
            Tuple:
                - final_score (float): 0–100 aggregate score
                - score_log (List[str]): explanation lines
                - warnings (List[str]): WARNING-prefixed lines
                - details (List[str]): DETAIL-prefixed lines
                - outputs (Dict[str, SignalOutput]): raw per-module outputs
                - meta_label_composite (float): geometric mean of active
                  modules' meta_label_proba values (1.0 = no-op)
        """
        outputs = self.registry.compute_all(row, context)

        score = 50.0  # Base neutral score
        score_log: List[str] = []
        warnings: List[str] = []
        details: List[str] = []
        # Accumulate log(meta_label_proba) for the geometric-mean composite.
        # Only active modules (not suppressed by is_active_in_regime) contribute.
        meta_log_sum: float = 0.0
        meta_active_count: int = 0

        for name, output in outputs.items():
            # Regime gate: a module that declares itself inactive this cycle
            # (e.g. RSI(2) mean reversion during RISK-OFF) contributes nothing —
            # neither its score nor its explanation lines are surfaced, and it
            # does NOT contribute to meta_label_composite (an inactive strategy
            # should not reduce the composite probability for a cycle it's
            # suppressed in).
            module = self.registry.get(name)
            if not module.is_active_in_regime(context.macro):
                continue

            weight = self.weights.get(name, 0.0)
            
            # Weighted contribution (clamped between -weight and +weight)
            contrib = output.score * weight
            score += contrib

            # Meta-label composite: accumulate in log-space for the geometric mean.
            # Clamp proba to (1e-9, 1.0] to avoid log(0) and negative composites.
            mlp = max(1e-9, min(1.0, float(output.meta_label_proba)))
            meta_log_sum += math.log(mlp)
            meta_active_count += 1

            # Parse explanation to extract score_log, warnings, and details
            if output.explanation:
                lines = output.explanation.strip().split("\n")
                for line in lines:
                    line = line.strip()
                    if line.startswith("WARNING:"):
                        warnings.append(line[len("WARNING:"):].strip())
                    elif line.startswith("DETAIL:"):
                        details.append(line[len("DETAIL:"):].strip())
                    else:
                        score_log.append(line)

        final_score = max(0.0, min(100.0, score))

        # Geometric mean of active module meta_label_proba values.
        # When no modules were active (shouldn't happen in normal operation),
        # default to 1.0 (neutral) to avoid division-by-zero.
        if meta_active_count > 0:
            meta_label_composite = math.exp(meta_log_sum / meta_active_count)
        else:
            meta_label_composite = 1.0

        return final_score, score_log, warnings, details, outputs, meta_label_composite
