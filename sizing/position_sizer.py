"""
InvestYo Quant Platform - Position Sizing Decision Pipeline
===============================================================
Unifies the previously-scattered sizing clamps (the HMM regime multiplier
composition, the meta-label composite, and the ``settings.MAX_POSITION_WEIGHT``
single-name ceiling) into ONE ordered, auditable pipeline, plus a new
portfolio-level gross-exposure cap. Adds guardrail telemetry
(``was_capped`` / ``binding_constraint``) that did not exist anywhere in the
codebase before this module.

Design notes
------------
* **Does not reimplement per-name sizing math.** ``sizing/kelly.py`` and
  ``sizing/vol_target.py`` remain the single source of truth for the Kelly /
  volatility-target formulas themselves. This module only composes their
  *already-computed* outputs with the regime/meta-label multipliers and the
  configured ceilings, and reports which ceiling (if any) bound.
* **``StrategyEngine._calculate_kelly_sizing`` is unchanged.** Its own
  ``settings.MAX_POSITION_WEIGHT`` clamp on the raw per-symbol weight is a
  directly-tested contract (``tests/test_kelly_no_history.py``,
  ``tests/test_kelly_order_sizing.py``, ``Gravity AI Review Suite.py`` step 16
  call ``engine._calculate_kelly_sizing(...)`` standalone and assert the clamp
  fires). ``size_position()`` picks up from that already-clamped
  ``pre_regime_weight`` -- exactly the value ``evaluate_security`` used to
  compose inline at strategy_engine.py:405-408 -- and re-clamps once more
  after composing the regime/meta multipliers, matching today's numeric
  contract exactly while consolidating the orchestration into one function
  the rest of the codebase (advisory path, audits, GUI) can call instead of
  re-deriving the clamp arithmetic locally.
* **``was_capped`` / ``binding_constraint`` are reserved for hard ceilings**
  (the raw formula's own cap, ``MAX_POSITION_WEIGHT``, the portfolio gross
  cap, and cap-aware escalation) -- NOT for the continuous HMM regime
  derating. ``regime_multiplier`` is already surfaced as its own float
  (unchanged from today's ``Regime_Multiplier`` column) so no information is
  lost; folding a routine regime<1.0 cycle into "was_capped" would make the
  guardrail fire on almost every risk-off day and drown out genuine ceiling
  events in the audit log / alerting (see ``sizing/cap_audit_store.py`` and
  the ``settings.SIZING_CAP_ALERT_THRESHOLD_PCT`` alert wired in
  ``pipeline/production_steps.py``, via ``observability.alerts.send_alert``).
* **Pure / no IO.** Escalation is driven by an optional ``CapEventSummary``
  the caller supplies (typically read from ``sizing/cap_audit_store.py``);
  this module never touches the database itself, consistent with this
  repo's DI conventions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import pandas as pd

from sizing.vol_target import portfolio_vol_target

logger = logging.getLogger(__name__)

# Binding-constraint string constants. Kept as plain strings (not an Enum) so
# they serialize directly into dashboard_df / JSON / SQLite without a codec,
# matching the existing ``sizing_path_tag`` convention in sizing/kelly.py.
KELLY_CAP = "kelly_cap"
VOL_TARGET_LEVERAGE = "vol_target_leverage"
MAX_POSITION_WEIGHT_CONSTRAINT = "max_position_weight"
PORTFOLIO_GROSS = "portfolio_gross"
ESCALATION = "escalation"


@dataclass(frozen=True)
class CapEventSummary:
    """Recent cap-history for one (symbol, strategy_id) pair, read by the
    caller from ``sizing/cap_audit_store.py`` and passed into
    ``size_position()`` to drive the cap-aware escalation rule.

    Parameters
    ----------
    consecutive_capped_cycles : int
        Number of consecutive prior cycles in which this name's sizing was
        capped by ANY hard constraint (see module docstring). 0 if the most
        recent cycle was not capped, or no history exists.
    last_binding_constraint : str or None
        The ``binding_constraint`` value from the most recent capped cycle,
        for diagnostic logging only.
    """

    consecutive_capped_cycles: int
    last_binding_constraint: Optional[str] = None


@dataclass(frozen=True)
class SizingDecision:
    """The full, auditable result of composing one symbol's sizing weight.

    ``final_weight`` is what callers should use as the position weight (the
    same value historically surfaced as ``"Kelly Target"`` /
    ``Kelly_Target_Post_Regime``). Every other field exists so the decision
    is explainable after the fact -- to a GUI panel, the Sheet, the audit
    log, or an operator asking "why is this name only sized at 2%?".
    """

    raw_weight: float
    pre_regime_weight: float
    regime_multiplier: float
    meta_label_composite: float
    final_weight: float
    path_tag: str
    binding_constraint: Optional[str]
    was_capped: bool
    constraints_applied: Tuple[str, ...] = field(default_factory=tuple)
    escalation_applied: bool = False


def detect_raw_cap_binding(
    path_tag: str,
    raw_weight: float,
    kelly_cap: float,
    max_leverage: float,
    epsilon: float = 1e-9,
) -> Optional[str]:
    """Reports whether a per-symbol sizing formula's OWN cap saturated.

    Public (not just ``size_position()``'s internal helper) so any sizing
    path -- e.g. ``engine.advisory``'s own, deliberately-decoupled
    ``_compute_kelly_sizing_detailed()`` -- can detect the same class of
    event without re-deriving this comparison itself (CONSTRAINT #7).

    This never re-derives the Kelly / vol-target math -- it only compares the
    already-computed ``raw_weight`` against the known cap constant for the
    path that produced it (identified via ``path_tag``, the same string
    ``sizing/kelly.py`` already returns). A one-sided detector: the
    vol-target fallback's cold-start scale-in (see
    ``kelly_sizing_for_strategy``) means a scaled-in weight will usually sit
    below ``max_leverage`` even when the underlying formula would otherwise
    saturate -- this function only flags TRUE saturation (scale-in == 1.0),
    never a false positive from a ramped-in weight.
    """
    if raw_weight is None:
        return None
    if path_tag == "aggregate_kelly" or path_tag.startswith("bootstrap_kelly_5th_pct"):
        if raw_weight >= kelly_cap - epsilon:
            return KELLY_CAP
    elif path_tag.startswith("vol_target_fallback"):
        if raw_weight >= max_leverage - epsilon:
            return VOL_TARGET_LEVERAGE
    return None


def clamp_with_binding(
    value: float,
    ceiling: float,
    constraint_name: str,
    epsilon: float = 1e-9,
) -> Tuple[float, Optional[str]]:
    """Clamps ``value`` to ``[0.0, ceiling]``, reporting ``constraint_name``
    iff the ceiling actually bound (``value`` exceeded it).

    Public so every sizing path applying a single-name ceiling -- both
    ``size_position()``'s own MAX_POSITION_WEIGHT re-clamp and
    ``engine.advisory``'s independent ``CONFIG["max_single_position_pct"]``
    clamp -- shares this one comparison instead of each hand-rolling
    ``max(0.0, min(value, ceiling))`` plus its own binding check
    (CONSTRAINT #7).
    """
    clamped = max(0.0, min(value, ceiling))
    bound = constraint_name if value > ceiling + epsilon else None
    return clamped, bound


def size_position(
    pre_regime_weight: float,
    *,
    regime_multiplier: float = 1.0,
    meta_label_composite: float = 1.0,
    max_position_weight: float,
    path_tag: str = "",
    raw_weight: Optional[float] = None,
    kelly_cap: Optional[float] = None,
    max_leverage: Optional[float] = None,
    recent_cap_events: Optional[CapEventSummary] = None,
    escalation_threshold: Optional[int] = None,
    escalation_factor: Optional[float] = None,
    epsilon: float = 1e-9,
) -> SizingDecision:
    """The single ordered sizing-composition pipeline (per symbol, per cycle).

    Pipeline order
    --------------
    1. (Informational) Did the per-symbol raw formula's own cap (``KELLY_CAP``
       or the vol-target fallback's ``MAX_LEVERAGE``) saturate upstream, in
       whatever produced ``pre_regime_weight``? Detected via ``path_tag`` +
       ``raw_weight`` if supplied.
    2. Did ``StrategyEngine._calculate_kelly_sizing``'s own
       ``MAX_POSITION_WEIGHT`` clamp already bind (``pre_regime_weight`` sits
       at the ceiling despite a larger ``raw_weight``)?
    3. Compose: ``pre_regime_weight * regime_multiplier * meta_label_composite``,
       then clamp to ``[0.0, max_position_weight]`` again (a no-op in the
       common case where both multipliers are <= 1.0, since step 2 already
       clamped ``pre_regime_weight``; guarded regardless for safety).
    4. (Optional) Cap-aware escalation: if ``recent_cap_events`` shows this
       name has been capped for >= ``escalation_threshold`` consecutive
       cycles, down-weight by ``escalation_factor``.

    A caller sizing a whole cycle's universe should follow this with
    ``apply_portfolio_gross_cap()`` across all symbols' ``final_weight``
    values -- that step is deliberately NOT inside this function since it
    needs every name in the cycle at once, not one symbol in isolation.

    Parameters
    ----------
    pre_regime_weight : float
        The already-``MAX_POSITION_WEIGHT``-clamped output of
        ``StrategyEngine._calculate_kelly_sizing`` (today's
        ``Kelly_Target_Pre_Regime``).
    regime_multiplier : float
        HMM risk-on-probability second opinion (``signals/regime_multiplier.py``);
        1.0 = neutral/no-op.
    meta_label_composite : float
        Stage 4 meta-label geometric mean; 1.0 = neutral placeholder today.
    max_position_weight : float
        ``settings.MAX_POSITION_WEIGHT``.
    path_tag : str
        The ``sizing_path_tag`` from ``_calculate_kelly_sizing`` (e.g.
        ``"aggregate_kelly"``, ``"vol_target_fallback(...)"``). Used only for
        the informational raw-cap detection in step 1; safe to omit.
    raw_weight : float or None
        The PRE-``MAX_POSITION_WEIGHT``-clamp weight (i.e. what
        ``_raw_kelly_or_vol_target_sizing`` returned before
        ``_calculate_kelly_sizing``'s clamp). Needed for step 1 and step 2
        detection; omit if unavailable -- detection degrades to "unknown"
        (``binding_constraint`` stays unset for those steps) rather than
        guessing.
    kelly_cap, max_leverage : float or None
        ``settings.KELLY_CAP`` / ``settings.MAX_LEVERAGE``, needed for step 1.
    recent_cap_events : CapEventSummary or None
        Recent cap history for this (symbol, strategy) pair; enables step 4.
    escalation_threshold : int or None
        ``settings.SIZING_CAP_ESCALATION_THRESHOLD_CYCLES``.
    escalation_factor : float or None
        ``settings.SIZING_CAP_ESCALATION_FACTOR`` (e.g. 0.5 = half-weight).

    Returns
    -------
    SizingDecision
    """
    constraints: list = []
    binding: Optional[str] = None

    # Step 1: informational -- did the raw per-symbol formula's own cap bind?
    if raw_weight is not None and kelly_cap is not None and max_leverage is not None:
        raw_cap_hit = detect_raw_cap_binding(path_tag, raw_weight, kelly_cap, max_leverage, epsilon)
        if raw_cap_hit:
            constraints.append(raw_cap_hit)
            binding = raw_cap_hit

    # Step 2: did _calculate_kelly_sizing's own MAX_POSITION_WEIGHT clamp bind?
    if (
        raw_weight is not None
        and raw_weight > max_position_weight + epsilon
        and pre_regime_weight >= max_position_weight - epsilon
    ):
        if MAX_POSITION_WEIGHT_CONSTRAINT not in constraints:
            constraints.append(MAX_POSITION_WEIGHT_CONSTRAINT)
        binding = MAX_POSITION_WEIGHT_CONSTRAINT

    # Step 3: compose regime x meta-label, re-clamp.
    composed = pre_regime_weight * regime_multiplier * meta_label_composite
    final_weight, composed_bound = clamp_with_binding(
        composed, max_position_weight, MAX_POSITION_WEIGHT_CONSTRAINT, epsilon
    )
    if composed_bound:
        if composed_bound not in constraints:
            constraints.append(composed_bound)
        binding = composed_bound

    was_capped = binding is not None

    # Step 4: cap-aware escalation (opt-in; caller supplies recent cap history).
    escalation_applied = False
    if (
        recent_cap_events is not None
        and escalation_threshold is not None
        and escalation_factor is not None
        and recent_cap_events.consecutive_capped_cycles >= escalation_threshold
    ):
        escalated = max(0.0, final_weight * escalation_factor)
        if escalated < final_weight - epsilon:
            final_weight = escalated
            constraints.append(ESCALATION)
            binding = ESCALATION
            was_capped = True
            escalation_applied = True
            logger.info(
                "size_position: escalation applied (consecutive_capped_cycles=%d >= "
                "threshold=%d) -> weight scaled by %.3f to %.6f.",
                recent_cap_events.consecutive_capped_cycles, escalation_threshold,
                escalation_factor, final_weight,
            )

    return SizingDecision(
        raw_weight=raw_weight if raw_weight is not None else pre_regime_weight,
        pre_regime_weight=pre_regime_weight,
        regime_multiplier=regime_multiplier,
        meta_label_composite=meta_label_composite,
        final_weight=final_weight,
        path_tag=path_tag,
        binding_constraint=binding,
        was_capped=was_capped,
        constraints_applied=tuple(constraints),
        escalation_applied=escalation_applied,
    )


@dataclass(frozen=True)
class PortfolioCapResult:
    """Result of applying the portfolio-level gross-exposure cap across one
    cycle's universe of already per-name-sized weights."""

    scaled_weights: Dict[str, float]
    scale_factor: float
    was_capped: bool
    binding_constraint: Optional[str]
    method: str


def apply_portfolio_gross_cap(
    per_name_weights: Dict[str, float],
    *,
    max_gross: float,
    cov_matrix: Optional[pd.DataFrame] = None,
    target_vol: Optional[float] = None,
    epsilon: float = 1e-9,
) -> PortfolioCapResult:
    """Scales a cycle's per-name sizing weights so aggregate exposure respects
    ``max_gross`` -- the new portfolio-level constraint layered on top of the
    existing per-name-only caps.

    Reuses (does not reimplement) ``sizing.vol_target.portfolio_vol_target``
    when a covariance matrix is available -- the risk-aware path, scaling by
    ``target_vol / sqrt(w^T Sigma w)`` capped at ``max_gross``. Falls back to
    a simple sum-of-|weight| gross-exposure scalar when no covariance matrix
    is supplied (the common case -- most callers won't have a full
    cross-sectional covariance estimate on hand every cycle). Both paths
    apply exactly ONE uniform scalar across every name, preserving relative
    weights -- the same design ``portfolio_vol_target`` already uses, so this
    function is a thin dispatcher, not a second implementation.

    Parameters
    ----------
    per_name_weights : dict[str, float]
        This cycle's final (post ``size_position``) weight per symbol.
    max_gross : float
        ``settings.MAX_PORTFOLIO_GROSS`` -- either the volatility-target
        ``max_leverage`` ceiling (cov-matrix path) or the raw gross-exposure
        ceiling (sum-of-|weight| fallback path).
    cov_matrix : pd.DataFrame or None
        Covariance matrix of asset returns, computed strictly from data prior
        to the current bar (caller's responsibility -- this function performs
        no lookahead checks itself, matching ``portfolio_vol_target``'s own
        contract). None selects the sum-of-|weight| fallback.
    target_vol : float or None
        Required alongside ``cov_matrix`` to use the risk-aware path.

    Returns
    -------
    PortfolioCapResult
        ``was_capped=True`` / ``binding_constraint="portfolio_gross"`` iff the
        single scalar applied is strictly less than 1.0.
    """
    if not per_name_weights:
        return PortfolioCapResult(scaled_weights={}, scale_factor=1.0, was_capped=False, binding_constraint=None, method="empty")

    if cov_matrix is not None and target_vol is not None:
        scaled = portfolio_vol_target(per_name_weights, cov_matrix, target_vol=target_vol, max_leverage=max_gross)
        method = "cov_matrix_vol_target"
    else:
        gross = sum(abs(w) for w in per_name_weights.values())
        scalar = 1.0 if gross <= 0 else min(1.0, max_gross / gross)
        scaled = {symbol: weight * scalar for symbol, weight in per_name_weights.items()}
        method = "sum_gross_fallback"

    # Both paths apply one uniform scalar -- recover it from the first
    # non-zero name for telemetry (rather than re-deriving it, to stay
    # agnostic to which branch ran).
    scale_factor = 1.0
    for symbol, raw in per_name_weights.items():
        if abs(raw) > epsilon:
            scale_factor = scaled.get(symbol, 0.0) / raw
            break

    was_capped = scale_factor < 1.0 - epsilon
    return PortfolioCapResult(
        scaled_weights=scaled,
        scale_factor=scale_factor,
        was_capped=was_capped,
        binding_constraint=PORTFOLIO_GROSS if was_capped else None,
        method=method,
    )
