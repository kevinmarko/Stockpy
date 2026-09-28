"""
legacy/tests/test_etf_transmission_multiplier_shape.py
======================================================
Archived with ETF volatility transmission (2026-09, step 4d). Split out of
tests/test_position_sizer.py (its former section 8); it exercises
legacy/risk/etf_transmission.py::transmission_multiplier directly. Not
collected by pytest (pytest.ini testpaths = tests).
"""
from __future__ import annotations

import math

import pytest

from risk.etf_transmission import transmission_multiplier


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
