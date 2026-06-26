"""
InvestYo Quant Platform - Signal Aggregator
===========================================
Aggregates pluggable quantitative signal scores into a single final score.

Stage 1.7 addition: ``aggregate()`` also computes ``meta_label_composite``
— the geometric mean of all *active* modules' ``SignalOutput.meta_label_proba``
values. Since every current module defaults ``meta_label_proba=1.0``, the
composite is always 1.0 (multiplicative no-op). ``StrategyEngine`` multiplies
the final Kelly Target by it.

Stage 4 addition (meta-labeling): the aggregator now queries
``ml.meta_labeling.global_meta_registry`` for each active signal module.  If a
``MetaLabeler`` is registered for a signal and its predicted
``P(primary_signal_correct)`` falls below ``settings.META_LABEL_MIN_CONFIDENCE``
(default 0.4), the aggregator sets ``meta_label_composite = 0.0`` (a hard gate
that zeroes the Kelly Target for this cycle). Otherwise it uses the meta-labeler's
probability instead of ``output.meta_label_proba`` in the geometric mean.

When ``global_meta_registry`` is empty (the default until real MetaLabelers are
registered), the behavior is identical to the pre-Stage-4 code: every module's
``meta_label_proba`` defaults to 1.0 and the composite is 1.0.

Tier 2.1 addition (regime-conditional weights): ``resolve_regime_weights()``
merges per-regime weight overrides from ``settings.REGIME_SIGNAL_WEIGHTS`` onto
the flat ``settings.SIGNAL_WEIGHTS`` base.  An empty ``REGIME_SIGNAL_WEIGHTS``
dict (the project default) preserves the previous flat-weight behavior exactly —
fully backward-compatible.  ``aggregate()`` resolves effective weights once at
the top of the loop, before iterating over modules, so the hot path adds only a
single dict-lookup per call.
"""

import math
from typing import Dict, List, Tuple
import pandas as pd

from signals.base import SignalContext, SignalOutput
from signals.registry import SignalRegistry
from settings import settings

# Import the global MetaLabelerRegistry singleton — lazy to avoid circular
# imports at module load time. (ml.meta_labeling → no signals dependency.)
def _get_meta_registry():
    """Lazy import of global_meta_registry to avoid load-time circular imports."""
    from ml.meta_labeling import global_meta_registry  # noqa: PLC0415
    return global_meta_registry


def resolve_regime_weights(
    market_regime: str,
    regime_weights: Dict[str, Dict[str, float]],
    default_weights: Dict[str, float],
) -> Dict[str, float]:
    """Return effective signal weights for the current macro regime.

    When ``regime_weights`` is empty (the project default), returns
    ``default_weights`` unchanged — identical to the pre-Tier-2.1 behavior.

    When regime-specific overrides are configured, the function:

    1. Looks up ``market_regime`` in ``regime_weights`` (exact match).
    2. Falls back to ``regime_weights["_default"]`` when no exact match exists.
    3. If still no match, returns ``default_weights`` unchanged.
    4. Otherwise merges: ``{**default_weights, **regime_override}`` so only the
       listed keys are changed; every other module inherits its flat-weight value.

    Parameters
    ----------
    market_regime : str
        The current macro regime string from ``MacroEconomicDTO.market_regime``
        (e.g. ``"RISK ON"``, ``"RECESSION"``).
    regime_weights : dict[str, dict[str, float]]
        Per-regime override dicts.  Keys are regime names or ``"_default"``.
        An empty dict means "no overrides" (the project default).
    default_weights : dict[str, float]
        The flat ``settings.SIGNAL_WEIGHTS`` dict (base weights).

    Returns
    -------
    dict[str, float]
        Effective weights to use for this cycle.  Always a new dict (never
        mutates ``default_weights`` or the regime override in place).

    Examples
    --------
    >>> flat = {"macro_regime": 45.0, "rsi2_mean_reversion": 10.0}
    >>> overrides = {"RECESSION": {"rsi2_mean_reversion": 0.0, "macro_regime": 60.0}}
    >>> resolve_regime_weights("RECESSION", overrides, flat)
    {'macro_regime': 60.0, 'rsi2_mean_reversion': 0.0}
    >>> resolve_regime_weights("RISK ON", overrides, flat)  # no match → defaults
    {'macro_regime': 45.0, 'rsi2_mean_reversion': 10.0}
    """
    if not regime_weights:
        return default_weights

    # Exact regime match first, then catch-all "_default"
    regime_override = regime_weights.get(market_regime) or regime_weights.get("_default")
    if not regime_override:
        return default_weights

    # Merge: regime-specific values override defaults; unspecified keys keep defaults
    return {**default_weights, **regime_override}


class SignalAggregator:
    """Aggregates multiple signal module outputs using configured weights.

    Tier 2.1: weights are now resolved per-cycle from ``settings.REGIME_SIGNAL_WEIGHTS``
    so that mean-reversion modules can be boosted in RISK ON and suppressed in
    RECESSION/CREDIT EVENT without touching the flat default dict.  Pass
    ``weights`` explicitly only in unit tests that need to override the settings.
    """

    def __init__(self, registry: SignalRegistry, weights: Dict[str, float] = None):
        self.registry = registry
        self.weights = weights if weights is not None else settings.SIGNAL_WEIGHTS

    def aggregate(
        self, row: pd.Series, context: SignalContext
    ) -> Tuple[float, List[str], List[str], List[str], Dict[str, SignalOutput], float]:
        """
        Computes all signals and aggregates their scores using the configured weights.

        Tier 2.1 change: effective weights are resolved once at call time via
        ``resolve_regime_weights()`` using ``context.macro.market_regime``.  When
        ``settings.REGIME_SIGNAL_WEIGHTS`` is empty (the default), this is a
        no-op dict lookup and the behavior is identical to pre-Tier-2.1.

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
        # Tier 2.1: resolve effective weights for the current macro regime.
        # Falls back to self.weights when REGIME_SIGNAL_WEIGHTS is empty (default).
        market_regime = getattr(context.macro, "market_regime", "")
        effective_weights = resolve_regime_weights(
            market_regime,
            settings.REGIME_SIGNAL_WEIGHTS,
            self.weights,
        )

        outputs = self.registry.compute_all(row, context)

        score = 50.0  # Base neutral score
        score_log: List[str] = []
        warnings: List[str] = []
        details: List[str] = []
        # Accumulate log(meta_label_proba) for the geometric-mean composite.
        # Only active modules (not suppressed by is_active_in_regime) contribute.
        meta_log_sum: float = 0.0
        meta_active_count: int = 0
        # Hard gate: True if any active module's MetaLabeler returned P < threshold.
        # When True, meta_label_composite is forced to 0.0 (position zeroed).
        meta_hard_gate: bool = False

        meta_registry = _get_meta_registry()

        for name, output in outputs.items():
            # Regime gate: a module that declares itself inactive this cycle
            # (e.g. RSI(2) mean reversion during RISK-OFF) contributes nothing —
            # neither its score nor its explanation lines are surfaced, and it
            # does NOT contribute to meta_label_composite (an inactive strategy
            # should not reduce the composite probability for a cycle it's
            # suppressed in).
            # Operator override: a module explicitly disabled via the GUI
            # Strategy Matrix (settings.DISABLED_SIGNAL_MODULES) is dropped here
            # just like a regime-gated module — no score contribution, no
            # meta_label_composite effect, and its explanation lines are not
            # surfaced. An empty disabled list reproduces the legacy behavior.
            if name in settings.DISABLED_SIGNAL_MODULES:
                continue

            module = self.registry.get(name)
            if not module.is_active_in_regime(context.macro):
                continue

            # Tier 2.1: use regime-resolved weight (may differ from self.weights[name])
            weight = effective_weights.get(name, 0.0)

            # Weighted contribution (clamped between -weight and +weight)
            contrib = output.score * weight
            score += contrib

            # Meta-label probability: prefer MetaLabelerRegistry over the
            # SignalOutput default (1.0 placeholder). If a MetaLabeler is
            # registered for this signal, query it with the current row features
            # plus the primary signal's own score (the meta-model conditions on
            # how confident the primary signal is).
            if meta_registry.has(name):
                try:
                    feat_row = pd.DataFrame([row.to_dict()])
                    feat_row["primary_score"] = output.score
                    mlp = meta_registry.get_proba(name, feat_row)
                except Exception as exc:
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "MetaLabelerRegistry.get_proba[%s] failed: %s — defaulting to 1.0.", name, exc
                    )
                    mlp = 1.0

                # Hard gate: P < threshold → force composite to 0 (position zero)
                if mlp < settings.META_LABEL_MIN_CONFIDENCE:
                    meta_hard_gate = True
                    mlp = 0.0
            else:
                # No MetaLabeler registered: use the SignalOutput placeholder (1.0)
                mlp = max(1e-9, min(1.0, float(output.meta_label_proba)))

            # Accumulate in log-space only when the hard gate has not triggered.
            # After a hard gate, we still finish the loop to collect score/log
            # lines, but further log accumulation is irrelevant.
            if not meta_hard_gate:
                meta_log_sum += math.log(max(1e-9, mlp))
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
        # Hard gate overrides to exactly 0.0 when any signal fell below threshold.
        # When no modules were active (shouldn't happen in normal operation),
        # default to 1.0 (neutral) to avoid division-by-zero.
        if meta_hard_gate:
            meta_label_composite = 0.0
        elif meta_active_count > 0:
            meta_label_composite = math.exp(meta_log_sum / meta_active_count)
        else:
            meta_label_composite = 1.0

        return final_score, score_log, warnings, details, outputs, meta_label_composite
