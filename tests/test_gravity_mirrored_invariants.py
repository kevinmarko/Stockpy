"""
tests/test_gravity_mirrored_invariants.py
===========================================
pytest mirrors of the load-bearing pandera schema invariants asserted only
inside ``Gravity AI Review Suite.py`` (a standalone script, not importable
as a normal module -- its filename contains a space). A refactor that
breaks one of these invariants passes the pytest suite today and is only
caught on the next manual Gravity run; these tests close that gap for the
two invariants verified (by direct execution, not just source-reading) to
be genuinely unenforced anywhere else in the codebase.

Two invariants from Gravity's ``MarketDataSchema``/``FundamentalDataSchema``/
``MacroDataSchema`` were investigated and found to be ALREADY covered
elsewhere, so they are NOT duplicated here:
  * ``MarketDataSchema.check_high_low_logic`` (high >= low) -- config.py's
    own ``MarketDataSchema`` has an equivalent check, but more importantly
    the invariant is actually ENFORCED (not just asserted) at
    ``dto_models.MarketBarDTO.__init__`` via a high<low coercion, already
    covered by ``tests/test_dto_boundary_contracts.py::
    TestMarketBarDTOBoundaryNormalization::test_high_below_low_is_coerced_to_low``.
  * The Sahm Rule "threshold" check inside Gravity's step_37 six-bug
    regression audit duplicates ``tests/test_bug_fixes.py::TestSahmRuleWiring``
    (BUG-1/BUG-2), already covered.

Two invariants ARE genuinely gravity-only and worth mirroring:
  * ``FundamentalDataSchema.dividend_yield: ge=0.0, le=1.0`` -- verified by
    direct execution that NOTHING in the production pipeline (neither
    ``dto_models.normalize_yfinance_dividend_yield`` nor
    ``FundamentalDataDTO.from_raw_dict``) clamps this value; only the
    percent-to-fraction division happens.
  * ``MacroDataSchema``'s ``nullable=False`` fields -- verified by direct
    execution that this is asymmetrically enforced: ``yield_curve_10y_2y``/
    ``high_yield_oas``/``inflation_rate``/``vix_value`` are all routed
    through ``BaseDTO._to_float`` (None -> 0.0, never propagates), but
    ``sahm_rule_indicator`` is a plain direct assignment with NO coercion
    -- passing ``None`` for it does not raise at construction time but
    DOES raise ``TypeError`` the first time ``market_regime``/``killSwitch``
    compares it against a threshold. A genuine, previously-undocumented
    crash risk, found here via direct execution.
"""

from __future__ import annotations

import math

import pytest

from dto_models import FundamentalDataDTO, MacroEconomicDTO, normalize_yfinance_dividend_yield


# ---------------------------------------------------------------------------
# Mirrors Gravity's FundamentalDataSchema.dividend_yield: ge=0.0, le=1.0
# ---------------------------------------------------------------------------

class TestDividendYieldBoundsMirror:
    @pytest.mark.parametrize("raw_percent,expected_fraction", [
        (0.0, 0.0),
        (2.57, 0.0257),   # a realistic ~2.6% yielder (the documented Apple-scale example)
        (15.0, 0.15),     # a realistic high-yield REIT/BDC
        (100.0, 1.0),     # boundary: exactly 100% -- still within Gravity's le=1.0
    ])
    def test_realistic_yfinance_percent_normalizes_within_gravity_bounds(
        self, raw_percent, expected_fraction
    ):
        """The real production path: normalize_yfinance_dividend_yield()
        (called upstream in data_engine.py/data/market_data.py) runs BEFORE
        FundamentalDataDTO.from_raw_dict() ever sees the value -- this test
        exercises that exact two-step pipeline, not from_raw_dict() alone."""
        info = {"dividendYield": raw_percent, "sector": "Technology", "shortName": "Test Co"}
        normalized_info = normalize_yfinance_dividend_yield(dict(info))
        dto = FundamentalDataDTO.from_raw_dict("TEST", normalized_info)
        assert math.isclose(dto.dividend_yield, expected_fraction, rel_tol=1e-9)
        assert 0.0 <= dto.dividend_yield <= 1.0

    def test_garbage_upstream_value_is_not_clamped_to_gravity_bounds(self):
        """DOCUMENTED GAP (verified by direct execution, not fixed here):
        neither normalize_yfinance_dividend_yield() nor
        FundamentalDataDTO.from_raw_dict() clamp the result -- a garbage
        upstream yfinance value that is itself already >100% (a data-quality
        bug on Yahoo's side, not implausible for a low-liquidity ticker) sails
        straight through the /100 normalization and out the other side still
        violating Gravity's asserted [0.0, 1.0] bound. Gravity would flag
        this at audit time; nothing in the pytest-exercised production path
        would. Pinned here so a future change that silently "fixes" this
        gap doesn't go unnoticed, and so the gap itself stays documented
        and visible rather than silently assumed away."""
        info = {"dividendYield": 250.0, "sector": "Technology", "shortName": "Test Co"}
        normalized_info = normalize_yfinance_dividend_yield(dict(info))
        dto = FundamentalDataDTO.from_raw_dict("TEST", normalized_info)
        assert dto.dividend_yield == pytest.approx(2.5)
        assert dto.dividend_yield > 1.0, (
            "If this now fails, dividend_yield clamping was added somewhere "
            "in the pipeline -- update this test to assert the new clamped "
            "behavior instead of the documented gap."
        )

    def test_negative_upstream_value_is_not_clamped_to_gravity_bounds(self):
        """Same documented-gap pattern for the lower bound."""
        info = {"dividendYield": -5.0, "sector": "Technology", "shortName": "Test Co"}
        normalized_info = normalize_yfinance_dividend_yield(dict(info))
        dto = FundamentalDataDTO.from_raw_dict("TEST", normalized_info)
        assert dto.dividend_yield < 0.0


# ---------------------------------------------------------------------------
# Mirrors Gravity's MacroDataSchema nullable=False fields
# ---------------------------------------------------------------------------

class TestMacroDataNullabilityMirror:
    @pytest.mark.parametrize("field_kwarg,dto_attr", [
        ("yield_curve_10y_2y", "yield_curve"),
        ("high_yield_oas", "credit_spread"),
        ("inflation_rate", "inflation"),
        ("vix_value", "vix"),
    ])
    def test_none_input_is_safely_coerced_never_propagates(self, field_kwarg, dto_attr):
        """These four fields all route through BaseDTO._to_float(value,
        default=0.0), so a None upstream value (e.g. a FRED series
        temporarily unavailable) never reaches market_regime/killSwitch as
        a raw None -- it becomes a defined, if conservative, 0.0."""
        kwargs = {
            "yield_curve_10y_2y": 0.5, "high_yield_oas": 2.0, "inflation_rate": 2.0,
        }
        kwargs[field_kwarg] = None
        dto = MacroEconomicDTO(**kwargs)
        value = getattr(dto, dto_attr)
        assert value is not None
        assert not math.isnan(value)
        assert value == 0.0

    def test_sahm_rule_indicator_none_is_not_coerced_and_crashes_downstream(self):
        """DOCUMENTED GAP (verified by direct execution): unlike its four
        sibling macro fields above, sahm_rule_indicator is assigned directly
        in MacroEconomicDTO.__init__ (`self.sahm_rule_indicator =
        sahm_rule_indicator`) with NO _to_float() coercion. Construction
        with sahm_rule_indicator=None does not raise, but the very next
        access of market_regime/killSwitch/_rules_based_regime DOES --
        `TypeError: '>=' not supported between instances of 'NoneType' and
        'float'`. This is exactly the crash risk Gravity's MacroDataSchema
        nullable=False assertion exists to catch, and it is NOT currently
        guarded anywhere in the DTO layer. Pinned as current behavior, not
        fixed here (a production fix would add sahm_rule_indicator to the
        _to_float() coercions alongside its siblings)."""
        dto = MacroEconomicDTO(
            yield_curve_10y_2y=0.5, high_yield_oas=2.0, inflation_rate=2.0,
            sahm_rule_indicator=None,
        )
        assert dto.sahm_rule_indicator is None  # construction itself doesn't raise

        with pytest.raises(TypeError):
            _ = dto._rules_based_regime

        with pytest.raises(TypeError):
            _ = dto.market_regime

        with pytest.raises(TypeError):
            _ = dto.killSwitch

    def test_realistic_sahm_rule_indicator_values_never_none(self):
        """Contrast case: every REAL call site in this codebase passes a
        float (calculate_sahm_rule()'s fallback_val default is 0.0, never
        None) -- the gap above is a structural landmine, not something any
        current production code path is known to actually trigger."""
        dto = MacroEconomicDTO(
            yield_curve_10y_2y=0.5, high_yield_oas=2.0, inflation_rate=2.0,
            sahm_rule_indicator=0.0,
        )
        assert dto.sahm_rule_indicator == 0.0
        assert dto.market_regime in ("RISK ON", "NEUTRAL", "RECESSION", "CREDIT EVENT")
        assert isinstance(dto.killSwitch, bool)
