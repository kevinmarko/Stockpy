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
