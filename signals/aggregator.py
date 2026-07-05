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

Task B4 addition (signal-weight & regime-config validation): previously,
``resolve_regime_weights()`` silently fell back to the flat default weights
whenever a ``REGIME_SIGNAL_WEIGHTS`` key didn't match a recognized regime
string (e.g. a typo like ``"RISK-ON"`` instead of ``"RISK ON"``) — the caller
had no way to know the override was ignored. Separately, ``settings.SIGNAL_WEIGHTS``
had no validation at all: a stray negative weight or an absurdly large one
(e.g. 1000.0, letting one module dominate the weighted sum) passed silently.
``validate_signal_weight_config()`` closes both gaps with a memoized (run
once per process, unless ``force=True``), log-only validator — see its
docstring below. ``resolve_regime_weights()`` now also logs a WARNING (not a
silent fallback) whenever ``market_regime`` doesn't resolve to a real
override key and ``regime_weights`` is non-empty with no matching entry AND
no ``"_default"`` catch-all.
"""

import logging
import math
from typing import Dict, List, Tuple
import pandas as pd

from signals.base import SignalContext, SignalOutput
from signals.registry import SignalRegistry
from settings import settings

logger = logging.getLogger(__name__)

# Import the global MetaLabelerRegistry singleton — lazy to avoid circular
# imports at module load time. (ml.meta_labeling → no signals dependency.)
def _get_meta_registry():
    """Lazy import of global_meta_registry to avoid load-time circular imports."""
    from ml.meta_labeling import global_meta_registry  # noqa: PLC0415
    return global_meta_registry


# ---------------------------------------------------------------------------
# Task B4 — Signal-weight & regime-config validation
# ---------------------------------------------------------------------------

#: Upper bound for any single entry in ``settings.SIGNAL_WEIGHTS``. A weight
#: above this would let one module's score alone swing the aggregate by more
#: than the entire useful range of the weighted sum (score in [-1, +1] times
#: this weight), effectively silencing every other module. 100.0 is roughly
#: 3x the largest weight in the project's own default ``SIGNAL_WEIGHTS``
#: (macro_regime=45.0, edge_garch=35.0) — generous enough for legitimate
#: operator tuning, but low enough to catch an obvious fat-fingered value
#: like 1000.0.
MAX_SANE_SIGNAL_WEIGHT: float = 100.0

#: Canonical macro regime strings, authoritative from
#: ``dto_models.MacroEconomicDTO.market_regime`` / ``_rules_based_regime``
#: and mirrored in ``macro_engine.py``'s Pandera schema
#: (``market_regime: Series[str] = pa.Field(isin=[...])``). Any
#: ``REGIME_SIGNAL_WEIGHTS`` key that is not one of these four (or the
#: reserved catch-all ``"_default"``) is a typo/mis-configuration, not a
#: real regime.
CANONICAL_REGIMES: frozenset = frozenset({"RISK ON", "NEUTRAL", "RECESSION", "CREDIT EVENT"})

#: Reserved catch-all key recognized by ``resolve_regime_weights()``.
_REGIME_DEFAULT_KEY: str = "_default"

# Memoization guard so ``validate_signal_weight_config()`` only logs once per
# process by default (startup-time validation, not a per-cycle re-check).
# Exposed at module level (not function-local) so tests can reset it via
# ``signals.aggregator._signal_weight_config_validated = False``.
_signal_weight_config_validated: bool = False


def validate_signal_weight_config(
    signal_weights: Dict[str, float] = None,
    regime_weights: Dict[str, Dict[str, float]] = None,
    *,
    max_weight: float = MAX_SANE_SIGNAL_WEIGHT,
    force: bool = False,
) -> List[str]:
    """Validate ``settings.SIGNAL_WEIGHTS`` / ``settings.REGIME_SIGNAL_WEIGHTS``
    and log a clear WARNING for every violation, instead of the previous
    fully-silent behavior.

    Two independent checks:

    1. **Weight bounds** — every value in ``signal_weights`` must be
       non-negative and at most ``max_weight``. A negative weight would
       invert a module's intended direction in the weighted sum; a weight
       above ``max_weight`` lets one module dominate/silence every other
       module (see ``MAX_SANE_SIGNAL_WEIGHT`` docstring for the rationale
       behind the default bound).
    2. **Regime key validity** — every top-level key in ``regime_weights``
       (when non-empty) must be either a ``CANONICAL_REGIMES`` string or the
       reserved catch-all ``"_default"``. A mistyped key (e.g. ``"RISK-ON"``)
       previously fell back to the flat default weights with zero signal to
       the operator that their override was silently ignored — this now logs
       a WARNING naming the exact offending key.

    This function is memoized at module level (``_signal_weight_config_validated``)
    so it only runs its checks once per process by default — intended as a
    startup-time / first-use validation, not a per-cycle re-check on the hot
    path. Pass ``force=True`` to re-run regardless (used by tests).

    Parameters
    ----------
    signal_weights:
        Defaults to ``settings.SIGNAL_WEIGHTS`` when ``None``.
    regime_weights:
        Defaults to ``settings.REGIME_SIGNAL_WEIGHTS`` when ``None``.
    max_weight:
        Upper bound for a sane weight. Defaults to ``MAX_SANE_SIGNAL_WEIGHT``.
    force:
        Bypass the memoization guard and re-validate unconditionally.

    Returns
    -------
    list[str]
        Every violation message that was logged (empty list = clean config).
        Returned (not just logged) so callers — e.g. a Gravity audit step or
        a test — can assert on the exact violations without parsing logs.
    """
    global _signal_weight_config_validated

    if _signal_weight_config_validated and not force:
        return []

    if signal_weights is None:
        signal_weights = settings.SIGNAL_WEIGHTS
    if regime_weights is None:
        regime_weights = settings.REGIME_SIGNAL_WEIGHTS

    violations: List[str] = []

    # --- Check 1: weight bounds (non-negative, below max_weight) -----------
    for name, weight in (signal_weights or {}).items():
        try:
            w = float(weight)
        except (TypeError, ValueError):
            msg = (
                f"SIGNAL_WEIGHTS[{name!r}] = {weight!r} is not numeric — "
                "cannot validate bounds."
            )
            violations.append(msg)
            logger.warning("validate_signal_weight_config: %s", msg)
            continue
        if w < 0.0:
            msg = (
                f"SIGNAL_WEIGHTS[{name!r}] = {w} is negative. A negative "
                "weight inverts this module's intended contribution sign in "
                "the aggregator's weighted sum — this is almost certainly a "
                "mis-configuration, not an intentional inverse-signal."
            )
            violations.append(msg)
            logger.warning("validate_signal_weight_config: %s", msg)
        elif w > max_weight:
            msg = (
                f"SIGNAL_WEIGHTS[{name!r}] = {w} exceeds MAX_SANE_SIGNAL_WEIGHT "
                f"({max_weight}). A weight this large lets a single module "
                "dominate/silence the entire weighted-sum aggregate."
            )
            violations.append(msg)
            logger.warning("validate_signal_weight_config: %s", msg)

    # --- Check 2: regime key validity ---------------------------------------
    for regime_key in (regime_weights or {}).keys():
        if regime_key == _REGIME_DEFAULT_KEY:
            continue
        if regime_key not in CANONICAL_REGIMES:
            msg = (
                f"REGIME_SIGNAL_WEIGHTS key {regime_key!r} does not match any "
                f"recognized macro regime ({sorted(CANONICAL_REGIMES)}) or the "
                f"catch-all {_REGIME_DEFAULT_KEY!r}. This override will be "
                "SILENTLY IGNORED by resolve_regime_weights() for every cycle "
                "unless it happens to also collide with a real regime string — "
                "check for a typo (e.g. 'RISK-ON' instead of 'RISK ON')."
            )
            violations.append(msg)
            logger.warning("validate_signal_weight_config: %s", msg)

    _signal_weight_config_validated = True
    return violations


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

    Task B4 — non-silent fallback
    ------------------------------
    When ``regime_weights`` is non-empty but ``market_regime`` is a
    *legitimate, recognized* regime string (one of
    ``CANONICAL_REGIMES``) with no matching key AND no ``"_default"``
    catch-all configured, this now logs a WARNING before falling back to
    ``default_weights`` — previously this was a fully silent fallback with no
    signal that the override dict simply doesn't cover the current regime.
    A ``market_regime`` value that is NOT a recognized regime string at all
    (e.g. empty string during startup, or a genuinely malformed macro DTO) is
    not itself flagged here — that is exactly what
    ``validate_signal_weight_config()`` catches on the *override dict's own
    keys*, independent of what regime is active on any given cycle.
    """
    if not regime_weights:
        return default_weights

    # Exact regime match first, then catch-all "_default"
    regime_override = regime_weights.get(market_regime) or regime_weights.get(_REGIME_DEFAULT_KEY)
    if not regime_override:
        if market_regime in CANONICAL_REGIMES:
            logger.warning(
                "resolve_regime_weights: REGIME_SIGNAL_WEIGHTS is configured but has "
                "no entry for the current regime %r and no %r catch-all — falling "
                "back to flat SIGNAL_WEIGHTS for this cycle.",
                market_regime, _REGIME_DEFAULT_KEY,
            )
        return default_weights

    # Merge: regime-specific values override defaults; unspecified keys keep defaults
    return {**default_weights, **regime_override}


class SignalAggregator:
    """Aggregates multiple signal module outputs using configured weights.

    Tier 2.1: weights are now resolved per-cycle from ``settings.REGIME_SIGNAL_WEIGHTS``
    so that mean-reversion modules can be boosted in RISK ON and suppressed in
    RECESSION/CREDIT EVENT without touching the flat default dict.  Pass
    ``weights`` explicitly only in unit tests that need to override the settings.

    Task B4: construction triggers ``validate_signal_weight_config()`` (once
    per process, memoized) so a mis-configured ``SIGNAL_WEIGHTS``/
    ``REGIME_SIGNAL_WEIGHTS`` in ``.env`` is flagged with a WARNING log at the
    earliest point the aggregator is actually used, rather than staying
    silent for the life of the process.
    """

    def __init__(self, registry: SignalRegistry, weights: Dict[str, float] = None):
        self.registry = registry
        self.weights = weights if weights is not None else settings.SIGNAL_WEIGHTS
        # First-use validation (memoized inside validate_signal_weight_config
        # itself) — never raises; violations are logged as WARNINGs.
        try:
            validate_signal_weight_config(self.weights, settings.REGIME_SIGNAL_WEIGHTS)
        except Exception as exc:  # pragma: no cover — defensive; validator itself never raises
            logger.warning("SignalAggregator: signal weight config validation failed: %s", exc)

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
