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
# 5b. apply_portfolio_gross_cap -- the covariance path is REDUCTION-ONLY.
#
# Audit regression: apply_portfolio_gross_cap() promises a *cap*, but its
# cov-matrix branch delegated to sizing.vol_target.portfolio_vol_target()
# passing `max_gross` straight through as that function's `max_leverage`.
# portfolio_vol_target's scalar is target_vol / sqrt(w' Sigma w) -- which is
# > 1.0 for any book quieter than target_vol -- and it saturates at
# `max_leverage` OUTRIGHT when portfolio_vol <= 0 or NaN (a degenerate or
# non-PSD covariance estimate). With settings.MAX_PORTFOLIO_GROSS = 3.0 the
# "cap" could therefore lever the entire book up 3x while reporting
# was_capped=False / binding_constraint=None, since that telemetry only fires
# on a scalar strictly BELOW 1.0.
#
# The guard is at THIS layer, not in portfolio_vol_target: scaling up toward
# a vol target is legitimate, documented behavior there (that is what a vol
# *targeting* primitive does, and what its max_leverage bound is for) and it
# has other callers -- tests/test_vol_target.py::
# test_portfolio_vol_target_caps_at_max_leverage still asserts that
# scale-up. Only apply_portfolio_gross_cap promises a cap.
#
# Unreachable from production today (pipeline/production_steps.py, the only
# caller, passes neither cov_matrix nor target_vol) -- this locks the
# invariant down before that path is wired.
# ===========================================================================
class TestPortfolioGrossCapCovPathIsReductionOnly:
    # A near-silent book (2% vol per name, uncorrelated) against a 10%
    # target_vol -- target_vol / portfolio_vol is ~35x, so the pre-fix code
    # levered every name up to the max_gross ceiling.
    QUIET_COV = pd.DataFrame(
        [[0.0004, 0.0], [0.0, 0.0004]], index=["AAPL", "MSFT"], columns=["AAPL", "MSFT"]
    )

    def test_scalar_above_one_is_clamped_and_weights_are_unchanged(self):
        positions = {"AAPL": 0.10, "MSFT": 0.10}
        out = apply_portfolio_gross_cap(
            positions, max_gross=3.0, cov_matrix=self.QUIET_COV, target_vol=0.10
        )
        assert out.method == "cov_matrix_vol_target"
        assert out.scale_factor == pytest.approx(1.0, rel=1e-12)
        assert out.was_capped is False
        assert out.binding_constraint is None
        for symbol, w in positions.items():
            assert out.scaled_weights[symbol] == pytest.approx(w, rel=1e-12)

    def test_the_pre_fix_uplift_would_have_been_levered_up(self):
        """Pins the magnitude of what the guard prevents: the raw primitive
        still levers this same book to the max_gross ceiling (3x) -- proving
        the clamp above is doing real work, not asserting a no-op."""
        positions = {"AAPL": 0.10, "MSFT": 0.10}
        unguarded = portfolio_vol_target(
            positions, self.QUIET_COV, target_vol=0.10, max_leverage=3.0
        )
        assert unguarded["AAPL"] == pytest.approx(0.30, rel=1e-9)

        out = apply_portfolio_gross_cap(
            positions, max_gross=3.0, cov_matrix=self.QUIET_COV, target_vol=0.10
        )
        assert out.scaled_weights["AAPL"] == pytest.approx(0.10, rel=1e-12)

    def test_degenerate_zero_covariance_does_not_lever_the_book_up(self):
        """A zero covariance matrix drives portfolio_vol == 0, which makes
        portfolio_vol_target saturate its scalar at max_leverage outright.
        The book must NOT be levered up on an absent risk estimate."""
        positions = {"AAPL": 0.40, "MSFT": 0.40}
        zero_cov = pd.DataFrame(
            [[0.0, 0.0], [0.0, 0.0]], index=["AAPL", "MSFT"], columns=["AAPL", "MSFT"]
        )
        out = apply_portfolio_gross_cap(
            positions, max_gross=3.0, cov_matrix=zero_cov, target_vol=0.10
        )
        assert out.method == "cov_matrix_vol_target"
        assert out.was_capped is False
        assert out.scale_factor == pytest.approx(1.0, rel=1e-12)
        for symbol, w in positions.items():
            assert out.scaled_weights[symbol] == pytest.approx(w, rel=1e-12)
            assert out.scaled_weights[symbol] <= w + 1e-12

    def test_non_psd_covariance_does_not_lever_the_book_up(self):
        """A non-PSD estimate yields a negative w' Sigma w, which
        portfolio_vol_target floors at 0.0 -- the same max_leverage
        saturation path as the zero-covariance case above."""
        positions = {"AAPL": 1.0, "MSFT": -1.0}
        non_psd = pd.DataFrame(
            [[0.04, 0.10], [0.10, 0.04]], index=["AAPL", "MSFT"], columns=["AAPL", "MSFT"]
        )
        out = apply_portfolio_gross_cap(
            positions, max_gross=3.0, cov_matrix=non_psd, target_vol=0.10
        )
        assert out.scaled_weights["AAPL"] == pytest.approx(1.0, rel=1e-12)
        assert out.scaled_weights["MSFT"] == pytest.approx(-1.0, rel=1e-12)
        assert out.was_capped is False

    def test_genuine_capping_still_works_on_the_cov_path(self):
        """The guard must only bound the UPSIDE -- a book noisier than
        target_vol still gets scaled down, with full telemetry."""
        positions = {"AAPL": 0.6, "MSFT": 0.6}
        cov = pd.DataFrame(
            [[0.04, 0.01], [0.01, 0.04]], index=["AAPL", "MSFT"], columns=["AAPL", "MSFT"]
        )
        # portfolio_vol = sqrt(0.36 * 0.10) = 0.1897367; 0.10 / that = 0.5270463
        expected_scalar = 0.10 / math.sqrt(0.036)
        out = apply_portfolio_gross_cap(
            positions, max_gross=3.0, cov_matrix=cov, target_vol=0.10
        )
        assert out.method == "cov_matrix_vol_target"
        assert out.was_capped is True
        assert out.binding_constraint == PORTFOLIO_GROSS
        assert out.scale_factor == pytest.approx(expected_scalar, rel=1e-9)
        for symbol, w in positions.items():
            assert out.scaled_weights[symbol] == pytest.approx(w * expected_scalar, rel=1e-9)

    def test_max_gross_below_one_remains_the_binding_ceiling(self):
        """The guard is min(max_gross, 1.0), not an unconditional 1.0 -- a
        caller asking for a ceiling TIGHTER than 1.0 still gets it."""
        positions = {"AAPL": 0.10, "MSFT": 0.10}
        out = apply_portfolio_gross_cap(
            positions, max_gross=0.5, cov_matrix=self.QUIET_COV, target_vol=0.10
        )
        assert out.was_capped is True
        assert out.binding_constraint == PORTFOLIO_GROSS
        assert out.scale_factor == pytest.approx(0.5, rel=1e-12)
        assert out.scaled_weights["AAPL"] == pytest.approx(0.05, rel=1e-12)

    def test_non_finite_weight_alongside_a_cov_matrix_is_carried_through(self):
        """The non-finite exclusion contract is unchanged by the guard: a
        dead-lettered name is kept out of the vol computation entirely (it is
        never handed to portfolio_vol_target, so it is never coerced to that
        function's missing-symbol 0.0) and carried through untouched."""
        positions = {"DEADLETTERED": float("nan"), "AAPL": 0.6, "MSFT": 0.6}
        cov = pd.DataFrame(
            [[0.04, 0.01], [0.01, 0.04]], index=["AAPL", "MSFT"], columns=["AAPL", "MSFT"]
        )
        expected_scalar = 0.10 / math.sqrt(0.036)
        out = apply_portfolio_gross_cap(
            positions, max_gross=3.0, cov_matrix=cov, target_vol=0.10
        )
        assert out.method == "cov_matrix_vol_target"
        assert out.was_capped is True
        assert math.isnan(out.scaled_weights["DEADLETTERED"])
        assert out.scaled_weights["AAPL"] == pytest.approx(0.6 * expected_scalar, rel=1e-9)
        assert out.scaled_weights["MSFT"] == pytest.approx(0.6 * expected_scalar, rel=1e-9)

    def test_non_finite_weight_with_a_quiet_cov_matrix_is_still_untouched(self):
        """Same contract on the clamped (scalar == 1.0) branch."""
        positions = {"DEADLETTERED": float("nan"), "AAPL": 0.10, "MSFT": 0.10}
        out = apply_portfolio_gross_cap(
            positions, max_gross=3.0, cov_matrix=self.QUIET_COV, target_vol=0.10
        )
        assert math.isnan(out.scaled_weights["DEADLETTERED"])
        assert out.scaled_weights["AAPL"] == pytest.approx(0.10, rel=1e-12)
        assert out.was_capped is False


# ===========================================================================
# 5c. apply_portfolio_gross_cap -- the sum-of-|weight| fallback branch is
# UNCHANGED by the cov-path guard above. This is the branch every production
# caller actually takes today (pipeline/production_steps.py passes neither
# cov_matrix nor target_vol), so it must not shift by so much as a ULP.
# ===========================================================================
class TestPortfolioGrossCapFallbackUnchanged:
    @pytest.mark.parametrize(
        "weights, max_gross",
        [
            ({"AAPL": 0.3, "MSFT": 0.3, "GOOG": 0.2}, 3.0),   # under ceiling
            ({"AAPL": 1.0, "MSFT": 1.0, "GOOG": 1.0}, 1.5),   # over ceiling
            ({"AAPL": 1.0, "MSFT": 1.0, "GOOG": 1.0}, 3.0),   # exactly at ceiling
            ({"AAPL": 2.0, "MSFT": -2.0}, 1.0),               # short leg, |w| gross
            ({"AAPL": 0.0, "MSFT": 0.0}, 3.0),                # zero gross
        ],
    )
    def test_fallback_matches_the_original_formula_exactly(self, weights, max_gross):
        gross = sum(abs(w) for w in weights.values())
        expected_scalar = 1.0 if gross <= 0 else min(1.0, max_gross / gross)

        out = apply_portfolio_gross_cap(weights, max_gross=max_gross)

        assert out.method == "sum_gross_fallback"
        for symbol, w in weights.items():
            # Exact equality, not approx: the fallback arithmetic must be
            # byte-identical to the pre-guard implementation.
            assert out.scaled_weights[symbol] == w * expected_scalar
        assert out.was_capped is (expected_scalar < 1.0 - 1e-9)
        assert out.binding_constraint == (PORTFOLIO_GROSS if out.was_capped else None)

    def test_target_vol_without_cov_matrix_still_takes_the_fallback(self):
        """Both arguments are required to select the risk-aware path -- one
        alone must not silently change branches."""
        weights = {"AAPL": 1.0, "MSFT": 1.0, "GOOG": 1.0}
        out = apply_portfolio_gross_cap(weights, max_gross=1.5, target_vol=0.10)
        assert out.method == "sum_gross_fallback"
        assert out.scaled_weights["AAPL"] == 0.5

    def test_cov_matrix_without_target_vol_still_takes_the_fallback(self):
        weights = {"AAPL": 1.0, "MSFT": 1.0, "GOOG": 1.0}
        cov = pd.DataFrame(
            [[0.04, 0.01], [0.01, 0.04]], index=["AAPL", "MSFT"], columns=["AAPL", "MSFT"]
        )
        out = apply_portfolio_gross_cap(weights, max_gross=1.5, cov_matrix=cov)
        assert out.method == "sum_gross_fallback"
        assert out.scaled_weights["AAPL"] == 0.5


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
