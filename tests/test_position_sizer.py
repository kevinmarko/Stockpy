"""
tests/test_position_sizer.py
=============================
Owning suite for ``sizing/position_sizer.py`` -- the new ordered
sizing-composition pipeline (``size_position()``), the portfolio-level
gross-exposure cap (``apply_portfolio_gross_cap()``), and the
``was_capped`` / ``binding_constraint`` guardrail telemetry.

Does NOT re-test the underlying Kelly / vol-target math (owned by
tests/test_kelly*.py and tests/test_vol_target.py) or the
StrategyEngine.evaluate_security() wiring (owned by
tests/test_strategy_engine.py::TestSizingWiring) -- this file is purely
about the new orchestration layer in isolation.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from sizing.position_sizer import (
    CapEventSummary,
    ESCALATION,
    KELLY_CAP,
    MAX_POSITION_WEIGHT_CONSTRAINT,
    PORTFOLIO_GROSS,
    VOL_TARGET_LEVERAGE,
    apply_portfolio_gross_cap,
    clamp_with_binding,
    detect_raw_cap_binding,
    size_position,
)
from sizing.vol_target import portfolio_vol_target
from risk.etf_transmission import transmission_multiplier


# ===========================================================================
# 1. size_position -- ordered pipeline, no capping
# ===========================================================================
class TestSizePositionNoCapping:
    def test_plain_composition_uncapped(self):
        """pre=0.10, regime=0.8, meta=1.0, ceiling=1.0 -> no ceiling ever
        approached; final = 0.08, was_capped is False."""
        out = size_position(
            0.10, regime_multiplier=0.8, meta_label_composite=1.0,
            max_position_weight=1.0,
        )
        assert out.final_weight == pytest.approx(0.08, rel=1e-9)
        assert out.was_capped is False
        assert out.binding_constraint is None
        assert out.constraints_applied == ()

    def test_regime_multiplier_alone_never_flags_was_capped(self):
        """A routine risk-off cycle (regime_multiplier well below 1.0) is NOT
        a guardrail cap -- Regime_Multiplier is already its own surfaced
        field; was_capped must stay False so the escalation/alert path isn't
        drowned out by ordinary regime derating."""
        out = size_position(
            0.50, regime_multiplier=0.1, meta_label_composite=1.0,
            max_position_weight=1.0,
        )
        assert out.final_weight == pytest.approx(0.05, rel=1e-9)
        assert out.was_capped is False
        assert out.binding_constraint is None

    def test_neutral_multipliers_are_identity(self):
        out = size_position(
            0.33, regime_multiplier=1.0, meta_label_composite=1.0,
            max_position_weight=1.0,
        )
        assert out.final_weight == pytest.approx(0.33, rel=1e-9)
        assert out.pre_regime_weight == pytest.approx(0.33)
        assert out.was_capped is False


# ===========================================================================
# 2. size_position -- MAX_POSITION_WEIGHT binding (both detection points)
# ===========================================================================
class TestSizePositionMaxPositionWeight:
    def test_pre_regime_clamp_already_at_ceiling(self):
        """raw_weight (2.0, e.g. vol-target-fallback saturating MAX_LEVERAGE)
        was already clamped down to pre_regime_weight=1.0 by
        StrategyEngine._calculate_kelly_sizing before this call -- step 2
        must detect that the ceiling bound upstream."""
        out = size_position(
            1.0, regime_multiplier=1.0, meta_label_composite=1.0,
            max_position_weight=1.0, path_tag="vol_target_fallback(scalein=1.00,n=30)",
            raw_weight=2.0, kelly_cap=0.20, max_leverage=2.0,
        )
        assert out.final_weight == pytest.approx(1.0)
        assert out.was_capped is True
        assert out.binding_constraint == MAX_POSITION_WEIGHT_CONSTRAINT

    def test_second_clamp_fires_if_composition_exceeds_ceiling(self):
        """Guards the re-clamp step even in the (atypical) case where
        multipliers compose to exceed 1.0 -- e.g. a future meta-label
        composite > 1.0."""
        out = size_position(
            0.80, regime_multiplier=1.0, meta_label_composite=1.5,
            max_position_weight=1.0,
        )
        assert out.final_weight == pytest.approx(1.0)
        assert out.was_capped is True
        assert out.binding_constraint == MAX_POSITION_WEIGHT_CONSTRAINT

    def test_no_false_positive_when_raw_weight_absent(self):
        """Without raw_weight supplied, step 1/2 detection degrades to
        'unknown' rather than guessing -- no spurious binding_constraint."""
        out = size_position(
            0.50, regime_multiplier=1.0, meta_label_composite=1.0,
            max_position_weight=1.0,
        )
        assert out.was_capped is False
        assert out.binding_constraint is None


# ===========================================================================
# 3. size_position -- raw formula cap detection (informational, step 1)
# ===========================================================================
class TestSizePositionRawCapDetection:
    def test_kelly_cap_detected_on_aggregate_path(self):
        out = size_position(
            0.20, regime_multiplier=1.0, meta_label_composite=1.0,
            max_position_weight=1.0, path_tag="aggregate_kelly",
            raw_weight=0.20, kelly_cap=0.20, max_leverage=2.0,
        )
        assert out.binding_constraint == KELLY_CAP
        assert out.was_capped is True
        assert KELLY_CAP in out.constraints_applied

    def test_kelly_cap_detected_on_bootstrap_path(self):
        out = size_position(
            0.20, regime_multiplier=1.0, meta_label_composite=1.0,
            max_position_weight=1.0,
            path_tag="bootstrap_kelly_5th_pct(n=100,k5=0.2000,k50=0.2000,k95=0.2000)",
            raw_weight=0.20, kelly_cap=0.20, max_leverage=2.0,
        )
        assert out.binding_constraint == KELLY_CAP

    def test_vol_target_leverage_detected_at_full_scale_in(self):
        """scalein=1.00 (>= MIN_TRADES_REQUIRED) and raw_weight saturates
        MAX_LEVERAGE -> flagged. This is the TRUE-saturation case."""
        out = size_position(
            1.0, regime_multiplier=1.0, meta_label_composite=1.0,
            max_position_weight=1.0,
            path_tag="vol_target_fallback(scalein=1.00,n=30)",
            raw_weight=2.0, kelly_cap=0.20, max_leverage=2.0,
        )
        # max_position_weight ALSO binds here (raw=2.0 > ceiling=1.0) -- the
        # more-restrictive/most-recent constraint (max_position_weight) wins
        # as binding_constraint, but vol_target_leverage is still recorded.
        assert VOL_TARGET_LEVERAGE in out.constraints_applied
        assert out.binding_constraint == MAX_POSITION_WEIGHT_CONSTRAINT

    def test_no_false_positive_when_scaled_in_partially(self):
        """A ramped-in (scalein < 1.0) fallback weight sits below
        MAX_LEVERAGE even when the underlying formula would otherwise
        saturate -- must NOT be flagged as a leverage-cap event."""
        out = size_position(
            0.5, regime_multiplier=1.0, meta_label_composite=1.0,
            max_position_weight=1.0,
            path_tag="vol_target_fallback(scalein=0.50,n=15)",
            raw_weight=1.0,  # 0.5 * 2.0 -- half-scaled, doesn't saturate 2.0
            kelly_cap=0.20, max_leverage=2.0,
        )
        assert VOL_TARGET_LEVERAGE not in out.constraints_applied
        assert out.was_capped is False

    def test_cold_start_no_vol_never_flags_raw_cap(self):
        out = size_position(
            0.0, regime_multiplier=1.0, meta_label_composite=1.0,
            max_position_weight=1.0, path_tag="cold_start_no_vol",
            raw_weight=0.0, kelly_cap=0.20, max_leverage=2.0,
        )
        assert out.binding_constraint is None
        assert out.was_capped is False


# ===========================================================================
# 3b. detect_raw_cap_binding / clamp_with_binding -- the two public helpers
# size_position() composes internally, and engine.advisory reuses directly
# (CONSTRAINT #7: one comparison implementation, not two independent copies).
# ===========================================================================
class TestDetectRawCapBindingDirect:
    def test_kelly_path_saturated(self):
        assert detect_raw_cap_binding("aggregate_kelly", 0.20, kelly_cap=0.20, max_leverage=2.0) == KELLY_CAP

    def test_kelly_path_not_saturated(self):
        assert detect_raw_cap_binding("aggregate_kelly", 0.10, kelly_cap=0.20, max_leverage=2.0) is None

    def test_vol_target_path_saturated(self):
        assert (
            detect_raw_cap_binding("vol_target_fallback(scalein=1.00,n=30)", 2.0, kelly_cap=0.20, max_leverage=2.0)
            == VOL_TARGET_LEVERAGE
        )

    def test_unknown_path_tag_never_flags(self):
        assert detect_raw_cap_binding("", 0.20, kelly_cap=0.20, max_leverage=2.0) is None

    def test_none_raw_weight_never_flags(self):
        assert detect_raw_cap_binding("aggregate_kelly", None, kelly_cap=0.20, max_leverage=2.0) is None


class TestClampWithBindingDirect:
    def test_no_binding_when_under_ceiling(self):
        clamped, bound = clamp_with_binding(0.5, 1.0, "some_constraint")
        assert clamped == pytest.approx(0.5)
        assert bound is None

    def test_binds_and_clamps_when_over_ceiling(self):
        clamped, bound = clamp_with_binding(1.5, 1.0, "some_constraint")
        assert clamped == pytest.approx(1.0)
        assert bound == "some_constraint"

    def test_negative_value_floors_at_zero(self):
        clamped, bound = clamp_with_binding(-0.5, 1.0, "some_constraint")
        assert clamped == 0.0
        assert bound is None

    def test_exactly_at_ceiling_does_not_bind(self):
        clamped, bound = clamp_with_binding(1.0, 1.0, "some_constraint")
        assert clamped == pytest.approx(1.0)
        assert bound is None

    def test_nan_value_is_never_fabricated_to_a_capped_zero(self):
        """Audit regression (honesty-auditor pass): two Python comparison
        quirks -- ``min(nan, ceiling)`` returns ``nan`` (since ``ceiling <
        nan`` is False), then ``max(0.0, nan)`` returns ``0.0`` (since ``nan
        > 0.0`` is also False) -- would otherwise silently collapse an
        honestly-unavailable (NaN) upstream weight into a fabricated
        "sized to zero, nothing bound" result (CONSTRAINT #4). A NaN input
        must stay NaN, with no binding_constraint fabricated either."""
        clamped, bound = clamp_with_binding(float("nan"), 1.0, "some_constraint")
        assert math.isnan(clamped)
        assert bound is None

    def test_none_value_is_never_fabricated_to_a_capped_zero(self):
        clamped, bound = clamp_with_binding(None, 1.0, "some_constraint")
        assert math.isnan(clamped)
        assert bound is None


# ===========================================================================
# 4. size_position -- cap-aware escalation
# ===========================================================================
class TestSizePositionEscalation:
    def test_escalation_applies_at_or_above_threshold(self):
        out = size_position(
            0.50, regime_multiplier=1.0, meta_label_composite=1.0,
            max_position_weight=1.0,
            recent_cap_events=CapEventSummary(consecutive_capped_cycles=5),
            escalation_threshold=5, escalation_factor=0.5,
        )
        assert out.final_weight == pytest.approx(0.25, rel=1e-9)
        assert out.escalation_applied is True
        assert out.was_capped is True
        assert out.binding_constraint == ESCALATION

    def test_no_escalation_below_threshold(self):
        out = size_position(
            0.50, regime_multiplier=1.0, meta_label_composite=1.0,
            max_position_weight=1.0,
            recent_cap_events=CapEventSummary(consecutive_capped_cycles=4),
            escalation_threshold=5, escalation_factor=0.5,
        )
        assert out.final_weight == pytest.approx(0.50, rel=1e-9)
        assert out.escalation_applied is False
        assert out.was_capped is False

    def test_escalation_disabled_when_params_omitted(self):
        """recent_cap_events supplied but threshold/factor omitted (e.g. the
        SIZING_CAP_ESCALATION_ENABLED default-off case) -- no-op."""
        out = size_position(
            0.50, regime_multiplier=1.0, meta_label_composite=1.0,
            max_position_weight=1.0,
            recent_cap_events=CapEventSummary(consecutive_capped_cycles=99),
        )
        assert out.final_weight == pytest.approx(0.50, rel=1e-9)
        assert out.escalation_applied is False

    def test_escalation_never_produces_negative_weight(self):
        out = size_position(
            0.01, regime_multiplier=1.0, meta_label_composite=1.0,
            max_position_weight=1.0,
            recent_cap_events=CapEventSummary(consecutive_capped_cycles=10),
            escalation_threshold=5, escalation_factor=0.0,
        )
        assert out.final_weight == 0.0
        assert out.final_weight >= 0.0


# ===========================================================================
# 4b. size_position -- NaN inputs must never fabricate a "capped-to-zero"
# result (audit regression; no known live caller currently passes NaN here
# -- every upstream weight computation degrades to an explicit 0.0 before
# reaching this function -- but this is a shared, reusable helper and the
# failure mode is silent, so it is hardened defensively).
# ===========================================================================
class TestSizePositionNaNSafety:
    def test_nan_regime_multiplier_yields_nan_not_a_fabricated_zero(self):
        out = size_position(
            0.50, regime_multiplier=float("nan"), meta_label_composite=1.0,
            max_position_weight=1.0,
        )
        assert math.isnan(out.final_weight)

    def test_nan_meta_label_composite_yields_nan_not_a_fabricated_zero(self):
        out = size_position(
            0.50, regime_multiplier=1.0, meta_label_composite=float("nan"),
            max_position_weight=1.0,
        )
        assert math.isnan(out.final_weight)

    def test_nan_pre_regime_weight_yields_nan_not_a_fabricated_zero(self):
        out = size_position(
            float("nan"), regime_multiplier=1.0, meta_label_composite=1.0,
            max_position_weight=1.0,
        )
        assert math.isnan(out.final_weight)


# ===========================================================================
# 5. apply_portfolio_gross_cap
# ===========================================================================
class TestPortfolioGrossCap:
    def test_empty_universe(self):
        out = apply_portfolio_gross_cap({}, max_gross=3.0)
        assert out.scaled_weights == {}
        assert out.was_capped is False
        assert out.method == "empty"

    def test_under_gross_ceiling_is_noop(self):
        weights = {"AAPL": 0.3, "MSFT": 0.3, "GOOG": 0.2}
        out = apply_portfolio_gross_cap(weights, max_gross=3.0)
        assert out.was_capped is False
        assert out.binding_constraint is None
        assert out.scale_factor == pytest.approx(1.0)
        for symbol, w in weights.items():
            assert out.scaled_weights[symbol] == pytest.approx(w)

    def test_over_gross_ceiling_scales_uniformly(self):
        """gross = 1.0+1.0+1.0 = 3.0, cap at 1.5 -> scalar = 0.5, relative
        weights preserved."""
        weights = {"AAPL": 1.0, "MSFT": 1.0, "GOOG": 1.0}
        out = apply_portfolio_gross_cap(weights, max_gross=1.5)
        assert out.was_capped is True
        assert out.binding_constraint == PORTFOLIO_GROSS
        assert out.scale_factor == pytest.approx(0.5, rel=1e-9)
        for symbol, w in weights.items():
            assert out.scaled_weights[symbol] == pytest.approx(w * 0.5, rel=1e-9)
        assert out.method == "sum_gross_fallback"

    def test_zero_gross_is_noop(self):
        weights = {"AAPL": 0.0, "MSFT": 0.0}
        out = apply_portfolio_gross_cap(weights, max_gross=3.0)
        assert out.was_capped is False
        assert out.scaled_weights == {"AAPL": 0.0, "MSFT": 0.0}

    def test_cov_matrix_path_delegates_to_portfolio_vol_target(self):
        """When a covariance matrix + target_vol are supplied, the cov-matrix
        path must produce EXACTLY what portfolio_vol_target() itself would
        return -- proving this is a thin dispatcher, not a reimplementation."""
        positions = {"AAPL": 0.6, "MSFT": 0.6}
        cov = pd.DataFrame(
            [[0.04, 0.01], [0.01, 0.04]], index=["AAPL", "MSFT"], columns=["AAPL", "MSFT"]
        )
        expected = portfolio_vol_target(positions, cov, target_vol=0.10, max_leverage=1.0)

        out = apply_portfolio_gross_cap(
            positions, max_gross=1.0, cov_matrix=cov, target_vol=0.10
        )
        assert out.method == "cov_matrix_vol_target"
        for symbol in positions:
            assert out.scaled_weights[symbol] == pytest.approx(expected[symbol], rel=1e-9)

    def test_scale_factor_derivation_skips_zero_weight_names(self):
        """A zero-weight name must not be used to derive the representative
        scale_factor (0/0 is undefined) -- the first non-zero name is used
        instead, and the zero-weight name's own scaled value stays 0.0."""
        weights = {"ZERO": 0.0, "AAPL": 2.0, "MSFT": 2.0}
        out = apply_portfolio_gross_cap(weights, max_gross=2.0)
        assert out.scaled_weights["ZERO"] == pytest.approx(0.0)
        assert out.scale_factor == pytest.approx(0.5, rel=1e-9)

    def test_a_single_nan_weight_does_not_silently_disable_the_cap_for_others(self):
        """Audit regression: sum(abs(w) for w in weights.values()) is
        NaN-poisoned by a single non-finite entry, and
        min(1.0, max_gross / nan) evaluates to exactly 1.0 (a Python
        comparison quirk -- 'x < 1.0' is False whenever x is NaN, so min()
        keeps its first argument) -- silently turning this always-on,
        cycle-wide risk ceiling into a total no-op, with no exception raised
        and was_capped=False indistinguishable from "genuinely under cap".
        A symbol whose sizing legitimately could not be computed this cycle
        (honest NaN, CONSTRAINT #4) must not defeat the cap for every OTHER
        name in the same cycle."""
        weights = {"DEADLETTERED": float("nan"), "AAPL": 1.0, "MSFT": 1.0, "GOOG": 1.0}
        out = apply_portfolio_gross_cap(weights, max_gross=1.5)
        assert out.was_capped is True
        assert out.binding_constraint == PORTFOLIO_GROSS
        assert out.scale_factor == pytest.approx(0.5, rel=1e-9)
        assert out.scaled_weights["AAPL"] == pytest.approx(0.5, rel=1e-9)
        assert out.scaled_weights["MSFT"] == pytest.approx(0.5, rel=1e-9)
        assert out.scaled_weights["GOOG"] == pytest.approx(0.5, rel=1e-9)
        # The non-finite symbol's own weight is preserved honestly, never
        # coerced to 0.0 or a fabricated scaled number.
        assert math.isnan(out.scaled_weights["DEADLETTERED"])

    def test_all_nan_weights_degrade_to_a_no_op_not_a_crash(self):
        weights = {"A": float("nan"), "B": float("nan")}
        out = apply_portfolio_gross_cap(weights, max_gross=3.0)
        assert out.was_capped is False
        assert out.scale_factor == pytest.approx(1.0)
        assert math.isnan(out.scaled_weights["A"])
        assert math.isnan(out.scaled_weights["B"])

    def test_inf_weight_is_excluded_like_nan(self):
        weights = {"INF": float("inf"), "AAPL": 1.0, "MSFT": 1.0}
        out = apply_portfolio_gross_cap(weights, max_gross=1.0)
        assert out.was_capped is True
        assert out.scale_factor == pytest.approx(0.5, rel=1e-9)
        assert out.scaled_weights["AAPL"] == pytest.approx(0.5, rel=1e-9)
        assert math.isinf(out.scaled_weights["INF"])


# ===========================================================================
# 6. CapEventSummary -- plain data container
# ===========================================================================
class TestCapEventSummary:
    def test_defaults(self):
        summary = CapEventSummary(consecutive_capped_cycles=0)
        assert summary.consecutive_capped_cycles == 0
        assert summary.last_binding_constraint is None

    def test_immutable(self):
        summary = CapEventSummary(consecutive_capped_cycles=3, last_binding_constraint=KELLY_CAP)
        with pytest.raises(Exception):
            summary.consecutive_capped_cycles = 4  # frozen dataclass


# ===========================================================================
# 7. ETF volatility-transmission derate (risk/etf_transmission.py) composed
#    into size_position()'s step 3 alongside regime_multiplier.
# ===========================================================================
_LEGACY_FIELDS = (
    "raw_weight", "pre_regime_weight", "regime_multiplier",
    "meta_label_composite", "final_weight", "path_tag",
    "binding_constraint", "was_capped", "constraints_applied",
    "escalation_applied",
)


def _legacy_view(decision):
    """Every ``SizingDecision`` field that existed BEFORE this feature.

    Used to prove the flag-off path is byte-identical: the only permitted
    difference between a pre-change decision and a post-change one is the
    brand-new ``etf_transmission_multiplier`` field itself.
    """
    return {name: getattr(decision, name) for name in _LEGACY_FIELDS}


def _reference_pre_change_composition(
    pre_regime_weight, regime_multiplier, meta_label_composite, max_position_weight,
):
    """Independently reconstructs the PRE-CHANGE step-3 arithmetic + ceiling
    binding, using only ``clamp_with_binding`` -- a public helper this PR did
    NOT touch. Deliberately not a copy of ``size_position``'s body, so the
    comparison below is a real cross-check rather than a tautology."""
    composed = pre_regime_weight * regime_multiplier * meta_label_composite
    return clamp_with_binding(composed, max_position_weight, MAX_POSITION_WEIGHT_CONSTRAINT)


class TestETFTransmissionNoOpIsByteIdentical:
    """The single most important property of this feature: with the flag off
    (i.e. the multiplier absent, None, NaN, or an explicit 1.0), every
    pre-existing ``SizingDecision`` field must be EXACTLY what it was before
    the feature existed -- not approximately, not usually."""

    @staticmethod
    def _grid():
        """Deterministic randomized parameter grid (seeded -- a flaky
        no-op proof would be worse than no proof)."""
        import random

        rng = random.Random(20260727)
        for _ in range(200):
            path_tag = rng.choice([
                "", "aggregate_kelly", "bootstrap_kelly_5th_pct(n=1000)",
                "vol_target_fallback(scale_in=1.00)", "unknown_path",
            ])
            yield {
                "pre_regime_weight": rng.uniform(0.0, 0.6),
                "regime_multiplier": rng.uniform(0.0, 1.2),
                "meta_label_composite": rng.uniform(0.0, 1.2),
                "max_position_weight": rng.choice([0.05, 0.2, 0.5, 1.0]),
                "path_tag": path_tag,
                "raw_weight": rng.choice([None, rng.uniform(0.0, 2.5)]),
                "kelly_cap": 0.20,
                "max_leverage": 2.0,
            }

    @pytest.mark.parametrize("multiplier", [None, 1.0, float("nan")])
    def test_absent_or_neutral_multiplier_reproduces_every_legacy_field(self, multiplier):
        for params in self._grid():
            pre = params["pre_regime_weight"]
            kwargs = {k: v for k, v in params.items() if k != "pre_regime_weight"}

            baseline = size_position(pre, **kwargs)                       # kwarg omitted
            with_mult = size_position(pre, etf_transmission_multiplier=multiplier, **kwargs)

            assert _legacy_view(with_mult) == _legacy_view(baseline), (
                f"flag-off path diverged for multiplier={multiplier!r}, params={params}"
            )
            # ... and the sanitized value recorded is exactly the 1.0 no-op.
            assert with_mult.etf_transmission_multiplier == 1.0
            assert baseline.etf_transmission_multiplier == 1.0

    def test_no_op_final_weight_matches_an_independent_reconstruction(self):
        """Cross-check against ``clamp_with_binding`` (untouched by this PR)
        rather than against ``size_position`` itself."""
        for params in self._grid():
            out = size_position(
                params["pre_regime_weight"],
                **{k: v for k, v in params.items() if k != "pre_regime_weight"},
            )
            expected, expected_bound = _reference_pre_change_composition(
                params["pre_regime_weight"], params["regime_multiplier"],
                params["meta_label_composite"], params["max_position_weight"],
            )
            if math.isnan(expected):
                assert math.isnan(out.final_weight)
            else:
                assert out.final_weight == pytest.approx(expected, rel=1e-12, abs=1e-15)
            if expected_bound is not None:
                assert out.binding_constraint == expected_bound


class TestETFTransmissionDerateComposition:
    def test_derate_scales_final_weight(self):
        out = size_position(
            0.10, etf_transmission_multiplier=0.7, max_position_weight=1.0,
        )
        assert out.final_weight == pytest.approx(0.07, rel=1e-12)
        assert out.etf_transmission_multiplier == pytest.approx(0.7)

    def test_composes_multiplicatively_with_regime_and_meta_label(self):
        out = size_position(
            0.20, regime_multiplier=0.5, meta_label_composite=0.8,
            etf_transmission_multiplier=0.75, max_position_weight=1.0,
        )
        # 0.20 * 0.5 * 0.8 * 0.75
        assert out.final_weight == pytest.approx(0.06, rel=1e-12)

    def test_composition_is_order_independent(self):
        """Multiplication commutes -- pinned so a future refactor that moves
        the derate to a different point in step 3 cannot silently change the
        number (only an ORDER-DEPENDENT step, e.g. an intermediate clamp
        inserted between the multipliers, would break this)."""
        a = size_position(
            0.30, regime_multiplier=0.6, meta_label_composite=1.0,
            etf_transmission_multiplier=0.5, max_position_weight=1.0,
        )
        b = size_position(
            0.30, regime_multiplier=0.5, meta_label_composite=1.0,
            etf_transmission_multiplier=0.6, max_position_weight=1.0,
        )
        assert a.final_weight == pytest.approx(b.final_weight, rel=1e-12)

    def test_max_position_weight_still_binds_after_the_derate(self):
        """The derate is applied BEFORE the ceiling re-clamp, so a name whose
        derated weight still exceeds the ceiling is genuinely capped."""
        out = size_position(
            0.50, regime_multiplier=1.0, meta_label_composite=1.0,
            etf_transmission_multiplier=0.9, max_position_weight=0.20,
        )
        assert out.final_weight == pytest.approx(0.20)
        assert out.was_capped is True
        assert out.binding_constraint == MAX_POSITION_WEIGHT_CONSTRAINT


class TestETFTransmissionIsNotAGuardrailCap:
    """THE contract of this PR. The ETF derate follows the ``regime_multiplier``
    precedent exactly: continuous, signal-driven derating is surfaced as its
    OWN field and must NEVER set ``was_capped`` / ``binding_constraint``,
    which are reserved for hard ceilings. Folding it in would make the
    guardrail fire on every ETF-heavy name and drown out genuine ceiling
    events in sizing/cap_audit_store.py and the SIZING_CAP_ALERT_THRESHOLD_PCT
    alert."""

    @pytest.mark.parametrize("multiplier", [0.99, 0.75, 0.5, 0.01, 0.0])
    def test_derate_alone_never_flags_was_capped(self, multiplier):
        out = size_position(
            0.05, regime_multiplier=1.0, meta_label_composite=1.0,
            etf_transmission_multiplier=multiplier, max_position_weight=1.0,
        )
        assert out.was_capped is False
        assert out.binding_constraint is None
        assert out.constraints_applied == ()
        assert out.etf_transmission_multiplier == pytest.approx(multiplier)

    def test_maximal_derate_with_regime_derate_still_never_flags(self):
        out = size_position(
            0.05, regime_multiplier=0.1, meta_label_composite=0.9,
            etf_transmission_multiplier=0.5, max_position_weight=1.0,
        )
        assert out.was_capped is False
        assert out.binding_constraint is None


class TestETFTransmissionNaNSafety:
    """A missing measurement must degrade to the exact 1.0 no-op, NEVER to a
    NaN. A NaN ``final_weight`` is EXCLUDED from
    ``apply_portfolio_gross_cap``'s gross sum, so a coverage gap would shrink
    the gross denominator and silently LOOSEN the portfolio-wide cap for
    every name that DID have coverage -- a data outage relaxing a risk
    limit."""

    @pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), float("-inf"), "n/a", object()])
    def test_unusable_multiplier_is_exactly_one_and_weight_stays_finite(self, bad):
        out = size_position(
            0.25, regime_multiplier=1.0, meta_label_composite=1.0,
            etf_transmission_multiplier=bad, max_position_weight=1.0,
        )
        assert out.etf_transmission_multiplier == 1.0
        assert not math.isnan(out.final_weight)
        assert math.isfinite(out.final_weight)
        assert out.final_weight == pytest.approx(0.25)

    def test_missing_coverage_cannot_loosen_the_portfolio_gross_cap(self):
        """End-to-end version of the trap: 3 of 4 names have no ETF coverage.
        All four weights must remain finite, so all four stay inside the
        gross-exposure sum and the cap still binds."""
        weights = {}
        for symbol, mult in [("A", float("nan")), ("B", None), ("C", float("nan")), ("D", 0.6)]:
            weights[symbol] = size_position(
                1.0, etf_transmission_multiplier=mult, max_position_weight=1.0,
            ).final_weight
        assert all(math.isfinite(w) for w in weights.values())
        capped = apply_portfolio_gross_cap(weights, max_gross=1.0)
        assert capped.was_capped is True
        assert capped.binding_constraint == PORTFOLIO_GROSS
        # gross = 1 + 1 + 1 + 0.6 = 3.6 -> scalar = 1/3.6. Had the three
        # uncovered names gone NaN, gross would have been 0.6 and the cap
        # would NOT have bound at all.
        assert capped.scale_factor == pytest.approx(1.0 / 3.6, rel=1e-9)

    def test_nan_regime_multiplier_still_yields_nan(self):
        """The ETF sanitizer must NOT accidentally rescue an honest NaN in a
        DIFFERENT input -- regime/meta NaN semantics are unchanged."""
        out = size_position(
            0.50, regime_multiplier=float("nan"),
            etf_transmission_multiplier=0.5, max_position_weight=1.0,
        )
        assert math.isnan(out.final_weight)


# ===========================================================================
# 8. risk/etf_transmission.py::transmission_multiplier -- the derate itself
# ===========================================================================
_KNOBS = {"max_derate": 0.30, "ownership_reference": 0.20, "floor": 0.50}


class TestTransmissionMultiplierShape:
    def test_zero_ownership_is_a_no_op(self):
        assert transmission_multiplier(0.0, 1.0, **_KNOBS) == 1.0

    def test_zero_comovement_is_a_no_op(self):
        """Heavy ETF ownership that produces no co-movement transmits
        nothing, and must derate nothing."""
        assert transmission_multiplier(0.90, 0.0, **_KNOBS) == 1.0

    def test_reference_ownership_with_full_comovement_applies_max_derate(self):
        assert transmission_multiplier(0.20, 1.0, **_KNOBS) == pytest.approx(0.70)

    def test_half_reference_ownership_applies_half_the_derate(self):
        assert transmission_multiplier(0.10, 1.0, **_KNOBS) == pytest.approx(0.85)

    def test_linear_in_comovement(self):
        assert transmission_multiplier(0.20, 0.5, **_KNOBS) == pytest.approx(0.85)

    def test_ownership_clips_at_the_reference(self):
        """Ownership past the reference cannot keep escalating the haircut."""
        at_ref = transmission_multiplier(0.20, 1.0, **_KNOBS)
        way_past = transmission_multiplier(0.95, 1.0, **_KNOBS)
        assert way_past == pytest.approx(at_ref)

    def test_comovement_clips_at_one(self):
        """A slightly-out-of-range R^2 (float noise from a regression) must
        not push the derate past max_derate."""
        assert transmission_multiplier(0.20, 1.4, **_KNOBS) == pytest.approx(0.70)

    def test_negative_inputs_clip_to_zero_and_never_boost_a_position(self):
        assert transmission_multiplier(-0.5, 1.0, **_KNOBS) == 1.0
        assert transmission_multiplier(0.20, -0.5, **_KNOBS) == 1.0

    def test_monotone_non_increasing_in_ownership(self):
        prev = 1.0
        for own in [0.0, 0.02, 0.05, 0.08, 0.10, 0.15, 0.20, 0.40, 1.0]:
            m = transmission_multiplier(own, 0.8, **_KNOBS)
            assert m <= prev + 1e-12, f"multiplier rose at ownership={own}"
            prev = m

    def test_monotone_non_increasing_in_comovement(self):
        prev = 1.0
        for r2 in [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0]:
            m = transmission_multiplier(0.15, r2, **_KNOBS)
            assert m <= prev + 1e-12, f"multiplier rose at r2={r2}"
            prev = m

    def test_always_within_floor_and_one(self):
        for own in [0.0, 0.05, 0.2, 0.5, 3.0]:
            for r2 in [0.0, 0.3, 1.0]:
                m = transmission_multiplier(own, r2, **_KNOBS)
                assert _KNOBS["floor"] <= m <= 1.0


class TestTransmissionMultiplierFloor:
    def test_floor_binds_at_maximal_ownership_and_comovement(self):
        knobs = {"max_derate": 0.90, "ownership_reference": 0.20, "floor": 0.50}
        # Unfloored the formula would give 1 - 0.90 = 0.10.
        assert transmission_multiplier(0.20, 1.0, **knobs) == pytest.approx(0.50)
        assert transmission_multiplier(5.00, 1.0, **knobs) == pytest.approx(0.50)

    def test_floor_of_one_makes_the_overlay_a_total_no_op(self):
        knobs = {"max_derate": 0.90, "ownership_reference": 0.20, "floor": 1.0}
        assert transmission_multiplier(0.50, 1.0, **knobs) == 1.0

    def test_derate_can_never_zero_a_position_out(self):
        knobs = {"max_derate": 1.0, "ownership_reference": 0.20, "floor": 0.50}
        assert transmission_multiplier(1.0, 1.0, **knobs) == pytest.approx(0.50)


class TestTransmissionMultiplierMissingInputs:
    """Exactly 1.0 -- never NaN -- on any unusable input. See the module
    docstring for why (the portfolio-gross-cap loosening trap)."""

    @pytest.mark.parametrize("own", [None, float("nan"), float("inf"), "", "n/a", object()])
    def test_unusable_ownership(self, own):
        assert transmission_multiplier(own, 1.0, **_KNOBS) == 1.0

    @pytest.mark.parametrize("r2", [None, float("nan"), float("-inf"), "", object()])
    def test_unusable_comovement(self, r2):
        assert transmission_multiplier(0.20, r2, **_KNOBS) == 1.0

    def test_both_missing(self):
        assert transmission_multiplier(None, None, **_KNOBS) == 1.0

    @pytest.mark.parametrize("reference", [0.0, -0.1, float("nan"), None])
    def test_unusable_ownership_reference_degrades_to_the_no_op(self, reference):
        """A misconfigured reference must not divide-by-zero, raise, or invent
        a derate the operator never configured."""
        assert transmission_multiplier(
            0.20, 1.0, max_derate=0.30, ownership_reference=reference, floor=0.50,
        ) == 1.0

    @pytest.mark.parametrize("knob", ["max_derate", "floor"])
    def test_unusable_knob_degrades_to_the_no_op(self, knob):
        knobs = dict(_KNOBS)
        knobs[knob] = float("nan")
        assert transmission_multiplier(0.20, 1.0, **knobs) == 1.0

    def test_returns_a_real_float_not_a_numpy_nan_lookalike(self):
        out = transmission_multiplier(float("nan"), float("nan"), **_KNOBS)
        assert isinstance(out, float)
        assert not math.isnan(out)


class TestTransmissionMultiplierAgainstLiveSettings:
    """The shipped defaults must themselves be sane -- a bounded overlay is
    only bounded if the configured knobs are."""

    def test_shipped_defaults_produce_a_bounded_monotone_derate(self):
        from settings import settings

        knobs = {
            "max_derate": settings.ETF_TRANSMISSION_MAX_DERATE,
            "ownership_reference": settings.ETF_TRANSMISSION_OWNERSHIP_REFERENCE,
            "floor": settings.ETF_TRANSMISSION_MIN_MULTIPLIER,
        }
        assert transmission_multiplier(0.0, 0.0, **knobs) == 1.0
        worst = transmission_multiplier(1.0, 1.0, **knobs)
        assert settings.ETF_TRANSMISSION_MIN_MULTIPLIER <= worst < 1.0

    def test_feature_ships_disabled(self):
        from settings import settings

        assert settings.ETF_TRANSMISSION_SIZING_ENABLED is False
