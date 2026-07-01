"""
tests/test_signal_module_contracts.py
======================================
Unit coverage for the 5 registered SignalModule subclasses with zero direct
compute()-level coverage (aroon_trend, graham_value, macd_momentum,
macro_regime, relative_strength), gap-closing tests for two modules whose
existing tests (tests/test_scoring_signals.py) never exercise their
unguarded code paths (dividend_quality, forecast_alignment), and a
cross-cutting contract sweep across all 17 modules registered in
signals.registry.global_registry.

Excluded (already covered, not duplicated): signals/edge_garch.py,
signals/rsi_extremes.py, signals/sortino_drawdown.py (fully covered by
tests/test_scoring_signals.py); signals/regime_multiplier.py,
signals/lgbm_ranker.py, signals/timeseries_momentum.py,
signals/rsi2_mean_reversion.py, signals/cross_sectional_momentum.py,
signals/multifactor.py, signals/news_catalyst.py (each has a dedicated test
file); signals/pairs_trading.py (a plain function, not a registered
SignalModule -- covered in tests/test_no_fabricated_metrics.py).

A SIGNIFICANT FINDING, pinned (not silently fixed) throughout this file:
signals/registry.py's compute_all() validates only that each
required_features KEY exists on the row -- never that its VALUE is non-NaN
-- before calling compute(). In Python, `NaN > x` and `NaN < x` are always
False. Several modules below are written as
`if condition: <bullish> else: <bearish>` -- so a NaN required value falls
through to the else/bearish branch and produces a confidently NEGATIVE score
from missing data, not a neutral one. This is a real, multi-module,
consistent pattern (not a one-off bug), but per the precedent set by the
prior two items in this initiative, this PR is test-only: it pins and
documents the actual verified behavior rather than silently patching the
signal-scoring production code. A follow-up to add pd.isna() guards
(matching the pattern signals/edge_garch.py, signals/rsi_extremes.py,
signals/sortino_drawdown.py, signals/relative_strength.py already use) is a
natural next step, intentionally left for the user to decide on separately.
"""

from __future__ import annotations

import math
from datetime import datetime

import pandas as pd
import pytest

from dto_models import FundamentalDataDTO, MacroEconomicDTO, MarketBarDTO
from signals.aroon_trend import AroonTrendSignal
from signals.base import SignalContext, SignalModule, SignalOutput
from signals.dividend_quality import DividendQualitySignal
from signals.forecast_alignment import ForecastAlignmentSignal
from signals.graham_value import GrahamValueSignal
from signals.macd_momentum import MACDMomentumSignal
from signals.macro_regime import MacroRegimeSignal
from signals.registry import global_registry
from signals.relative_strength import RelativeStrengthSignal


# ============================================================================
# Shared helpers
# ============================================================================

def _signal_context(
    *,
    market_regime_inputs: tuple[float, float, float] = (0.5, 2.0, 0.03),  # yield_curve, hy_oas, inflation
    vix_value: float = 15.0,
    sahm_rule_indicator: float = 0.0,
    graham_eps: float = 5.0,
    graham_book_value: float = 50.0,
    dividend_yield: float = 0.0,
    payout_ratio: float = 0.0,
    sector: str = "Technology",
) -> SignalContext:
    bar = MarketBarDTO(
        date=datetime.now(), ticker="TEST", open_price=100.0,
        high_price=101.0, low_price=99.0, close_price=100.0, volume=1_000,
    )
    yield_curve, hy_oas, inflation = market_regime_inputs
    fund = FundamentalDataDTO(
        ticker="TEST", pe_ratio=15.0, pb_ratio=2.0,
        dividend_yield=dividend_yield, book_value=graham_book_value,
        eps_trailing=graham_eps, dividend_growth_rate=0.02,
        payout_ratio=payout_ratio, sector=sector, company_name="Test Co",
    )
    macro = MacroEconomicDTO(
        yield_curve_10y_2y=yield_curve, high_yield_oas=hy_oas,
        inflation_rate=inflation, vix_value=vix_value,
        sahm_rule_indicator=sahm_rule_indicator,
    )
    return SignalContext(bar=bar, fundamentals=fund, macro=macro)


def _realistic_row() -> pd.Series:
    """A fully-populated row mirroring strategy_engine.py's
    StrategyEngine.evaluate_security() row construction (lines 240-265) --
    every key all 17 registered modules read, with valid non-NaN values
    representative of a healthy, liquid stock. Used by the cross-cutting
    sweep so the 6 unguarded modules in the module docstring above don't
    spuriously fail a sweep that isn't testing their missing-input path."""
    return pd.Series({
        "Symbol": "TEST",
        "ticker": "TEST",
        "sector": "Technology",
        "forecast_price": 105.0,
        "trend_strength": 60.0,
        "atr": 1.5,
        "macd_line": 0.5,
        "macd_signal": 0.3,
        "aroon_osc": 65.0,
        "rsi": 55.0,
        "sortino_ratio": 1.2,
        "max_drawdown": -0.10,
        "relative_strength": 0.02,
        "garch_vol": 0.20,
        "GARCH_Vol": 0.20,
        "edge_ratio": 1.0,
        "chandelier_long": 95.0,
        "chandelier_short": 0.0,
        "current_price": 100.0,
        "Close": 100.0,
        "roc_12m": 0.08,
        "ROC_12M": 0.08,
        "SMA_200": 95.0,
        "RSI_2": 50.0,
        "SMA_5": 99.0,
    })


# ============================================================================
# Section 1 — Per-module dedicated classes (genuinely zero coverage)
# ============================================================================

class TestAroonTrend:
    def _score(self, **row) -> SignalOutput:
        return AroonTrendSignal().compute(pd.Series(row), _signal_context())

    def test_strong_aroon_uptrend_adds_15pts(self):
        out = self._score(aroon_osc=65.0, trend_strength=60.0)
        assert out.score == pytest.approx(15.0 / 15.0)
        assert "Strong Aroon Oscillator Uptrend" in out.explanation

    def test_aroon_at_50_boundary_is_uptrend_inclusive(self):
        out = self._score(aroon_osc=50.0, trend_strength=60.0)
        assert out.score == pytest.approx(1.0)

    def test_choppy_market_subtracts_15pts(self):
        out = self._score(aroon_osc=20.0, trend_strength=60.0)
        assert out.score == pytest.approx(-1.0)
        assert "Choppy Market" in out.explanation

    def test_strong_aroon_downtrend_subtracts_15pts(self):
        out = self._score(aroon_osc=-65.0, trend_strength=60.0)
        assert out.score == pytest.approx(-1.0)
        assert "Strong Aroon Oscillator Downtrend" in out.explanation

    def test_aroon_missing_falls_back_to_trend_strength_bullish(self):
        out = self._score(trend_strength=55.0)
        assert out.score == pytest.approx(10.0 / 15.0)
        assert "Bullish technical trend" in out.explanation

    def test_aroon_missing_trend_strength_weakening_band(self):
        out = self._score(trend_strength=35.0)
        assert out.score == pytest.approx(-5.0 / 15.0)

    def test_aroon_missing_trend_strength_bearish(self):
        out = self._score(trend_strength=10.0)
        assert out.score == pytest.approx(-1.0)

    def test_nan_aroon_uses_fallback_path_not_crash(self):
        """A NaN aroon_osc correctly falls through `pd.isna()` into the
        documented trend_strength fallback -- this path IS guarded."""
        out = self._score(aroon_osc=float("nan"), trend_strength=55.0)
        assert out.score == pytest.approx(10.0 / 15.0)

    def test_nan_trend_strength_with_no_aroon_fabricates_bearish_score(self):
        """KNOWN GAP (see module docstring): trend_strength has NO pd.isna()
        guard. NaN >= 50.0 and 30.0 <= NaN are both False in Python, so
        execution falls through to the final else branch and fabricates a
        confident -15pts 'Bearish pricing structure' reading from a missing
        value -- never a neutral 0.0. This test pins the actual current
        behavior; it is not asserting this is correct."""
        out = self._score(trend_strength=float("nan"))
        assert out.score == pytest.approx(-1.0)
        assert "Bearish pricing structure" in out.explanation


class TestGrahamValue:
    def _score(self, current_price, **ctx_kwargs) -> SignalOutput:
        row = pd.Series({"current_price": current_price})
        return GrahamValueSignal().compute(row, _signal_context(**ctx_kwargs))

    def test_undervalued_vs_graham_adds_15pts(self):
        # graham_number = sqrt(22.5 * 5.0 * 50.0) ~= 74.66, well above price.
        out = self._score(current_price=50.0, graham_eps=5.0, graham_book_value=50.0)
        assert out.score == pytest.approx(15.0 / 15.0)
        assert "Undervalued vs Graham" in out.explanation

    def test_overvalued_vs_graham_subtracts_10pts(self):
        out = self._score(current_price=200.0, graham_eps=5.0, graham_book_value=50.0)
        assert out.score == pytest.approx(-10.0 / 15.0)
        assert "Overvalued vs Graham" in out.explanation

    def test_no_graham_value_possible_subtracts_5pts(self):
        # eps<=0 -> graham_number property returns 0.0 (dto_models.py contract)
        out = self._score(current_price=100.0, graham_eps=-5.0, graham_book_value=50.0)
        assert out.score == pytest.approx(-5.0 / 15.0)
        assert "No Intrinsic Graham Value possible" in out.explanation

    def test_nan_current_price_fabricates_overvalued_score(self):
        """KNOWN GAP (see module docstring): current_price has NO pd.isna()
        guard. `graham_val > NaN` is False, so a real, positive graham_val
        falls through to the 'Overvalued' branch on a NaN price -- never a
        neutral 0.0. Pinning actual behavior, not asserting correctness."""
        out = self._score(current_price=float("nan"), graham_eps=5.0, graham_book_value=50.0)
        assert out.score == pytest.approx(-10.0 / 15.0)
        assert "Overvalued vs Graham" in out.explanation


class TestMacdMomentum:
    def _score(self, **row) -> SignalOutput:
        return MACDMomentumSignal().compute(pd.Series(row), _signal_context())

    def test_aroon_absent_is_neutral_gate_closed(self):
        """MACD is only scored when aroon_osc is present -- this IS guarded."""
        out = self._score(macd_line=10.0, macd_signal=1.0)
        assert out.score == 0.0
        assert out.explanation == ""

    def test_bullish_crossover_adds_10pts(self):
        out = self._score(aroon_osc=60.0, macd_line=1.0, macd_signal=0.5)
        assert out.score == pytest.approx(10.0 / 15.0)
        assert "MACD Bullish" in out.explanation

    def test_bearish_crossover_subtracts_15pts(self):
        out = self._score(aroon_osc=60.0, macd_line=0.3, macd_signal=0.5)
        assert out.score == pytest.approx(-1.0)
        assert "MACD Bearish Crossover" in out.explanation

    def test_equal_macd_line_and_signal_is_bearish_branch(self):
        # `>` is strict; a tie falls to the else (bearish) branch.
        out = self._score(aroon_osc=60.0, macd_line=0.5, macd_signal=0.5)
        assert out.score == pytest.approx(-1.0)

    def test_nan_aroon_osc_keeps_gate_closed_neutral(self):
        """NaN aroon_osc IS guarded via pd.isna() -- gate stays closed,
        neutral score, regardless of macd_line/macd_signal."""
        out = self._score(aroon_osc=float("nan"), macd_line=10.0, macd_signal=1.0)
        assert out.score == 0.0

    def test_nan_macd_values_with_aroon_present_fabricates_bearish_score(self):
        """KNOWN GAP (see module docstring): once the aroon gate is open,
        macd_line/macd_signal have NO pd.isna() guard. `NaN > x` is False,
        so a NaN macd_line falls through to the bearish branch -- a
        confidently negative score from missing data."""
        out = self._score(aroon_osc=60.0, macd_line=float("nan"), macd_signal=0.5)
        assert out.score == pytest.approx(-1.0)
        assert "MACD Bearish Crossover" in out.explanation


class TestMacroRegime:
    def _score(self, sector=None, **ctx_kwargs) -> SignalOutput:
        row = pd.Series({"sector": sector}) if sector is not None else pd.Series({})
        return MacroRegimeSignal().compute(row, _signal_context(**ctx_kwargs))

    def test_recession_subtracts_15pts(self):
        out = self._score(market_regime_inputs=(-0.5, 7.0, 0.02))  # -> RECESSION
        assert out.score == pytest.approx(-15.0 / 45.0)
        assert "Recession Regime Active" in out.explanation

    def test_credit_event_subtracts_25pts(self):
        out = self._score(market_regime_inputs=(0.5, 6.5, 0.02))  # -> CREDIT EVENT
        assert out.score == pytest.approx(-25.0 / 45.0)
        assert "Hostile Credit Event" in out.explanation

    def test_risk_on_adds_10pts(self):
        out = self._score(market_regime_inputs=(0.5, 2.0, 0.02))  # -> RISK ON
        assert out.score == pytest.approx(10.0 / 45.0)
        assert "Favorable Macro Regime" in out.explanation

    def test_neutral_regime_scores_zero(self):
        out = self._score(market_regime_inputs=(0.5, 5.0, 0.02))  # -> NEUTRAL
        assert out.score == 0.0

    def test_killswitch_active_adds_additional_penalty(self):
        # VIX > 30 alone is sufficient to trip killSwitch regardless of regime.
        out = self._score(market_regime_inputs=(0.5, 2.0, 0.02), vix_value=35.0)
        # RISK ON (+10) + killSwitch (-5) = +5 of 45
        assert out.score == pytest.approx(5.0 / 45.0)
        assert "SYSTEMIC KILLSWITCH ACTIVE" in out.explanation

    def test_financial_sector_penalized_during_recession(self):
        out = self._score(
            sector="Financial Services",
            market_regime_inputs=(-0.5, 7.0, 0.02),
        )
        # RECESSION (-15) + sector penalty (-15) = -30 of 45
        assert out.score == pytest.approx(-30.0 / 45.0)

    def test_defensive_sector_rewarded_during_credit_event(self):
        out = self._score(
            sector="Healthcare",
            market_regime_inputs=(0.5, 6.5, 0.02),
        )
        # CREDIT EVENT (-25) + defensive premium (+10) = -15 of 45
        assert out.score == pytest.approx(-15.0 / 45.0)

    def test_sector_rotation_does_not_apply_outside_risk_off_regimes(self):
        # RISK ON regime + a "Financial" sector must NOT apply the sector
        # penalty -- rotation is gated on regime in {RECESSION, CREDIT EVENT}.
        out = self._score(sector="Financial Services", market_regime_inputs=(0.5, 2.0, 0.02))
        assert out.score == pytest.approx(10.0 / 45.0)

    def test_missing_sector_does_not_raise_in_risk_off_regime(self):
        """sector is read via row.get('sector') -- already guarded."""
        out = self._score(market_regime_inputs=(-0.5, 7.0, 0.02))
        assert out.score == pytest.approx(-15.0 / 45.0)

    def test_none_macro_context_raises_attribute_error(self):
        """KNOWN GAP (see module docstring): context.macro is accessed
        unconditionally as context.macro.market_regime -- a None macro
        context crashes with AttributeError rather than degrading."""
        bar = MarketBarDTO(datetime.now(), "TEST", 100.0, 101.0, 99.0, 100.0, 1_000)
        fund = FundamentalDataDTO(
            ticker="TEST", pe_ratio=15.0, pb_ratio=2.0, dividend_yield=0.0,
            book_value=50.0, eps_trailing=5.0, dividend_growth_rate=0.02,
            payout_ratio=0.0, sector="Technology", company_name="Test Co",
        )
        broken_ctx = SignalContext(bar=bar, fundamentals=fund, macro=None)
        with pytest.raises(AttributeError):
            MacroRegimeSignal().compute(pd.Series({"sector": "Technology"}), broken_ctx)


class TestRelativeStrength:
    """Already well-guarded (mirrors signals/edge_garch.py's pattern) --
    short tests pinning the happy path + the neutral-on-missing contract."""

    def _score(self, **row) -> SignalOutput:
        return RelativeStrengthSignal().compute(pd.Series(row), _signal_context())

    def test_outperforming_spy_adds_10pts(self):
        out = self._score(relative_strength=0.05)
        assert out.score == pytest.approx(1.0)
        assert "Outperforming S&P 500" in out.explanation

    def test_underperforming_spy_subtracts_10pts(self):
        out = self._score(relative_strength=-0.05)
        assert out.score == pytest.approx(-1.0)
        assert "Underperforming S&P 500" in out.explanation

    def test_zero_relative_strength_is_underperform_branch(self):
        # `> 0` is strict; exactly 0.0 falls to the underperform branch.
        out = self._score(relative_strength=0.0)
        assert out.score == pytest.approx(-1.0)

    def test_missing_relative_strength_is_neutral(self):
        assert self._score().score == 0.0

    def test_nan_relative_strength_is_neutral(self):
        assert self._score(relative_strength=float("nan")).score == 0.0


# ============================================================================
# Section 2 — Gap-closing classes for modules with existing-but-incomplete
# coverage in tests/test_scoring_signals.py
# ============================================================================

class TestDividendQualityMissingContext:
    """tests/test_scoring_signals.py::TestDividendQuality always supplies a
    fully-populated FundamentalDataDTO via _make_context(); it never
    exercises context.fundamentals being None or carrying a NaN
    dividend_yield. This class closes that gap."""

    def test_none_fundamentals_raises_attribute_error(self):
        """KNOWN GAP (see module docstring): context.fundamentals is
        accessed unconditionally -- a None fundamentals object crashes with
        AttributeError rather than degrading to a neutral score."""
        bar = MarketBarDTO(datetime.now(), "TEST", 100.0, 101.0, 99.0, 100.0, 1_000)
        macro = MacroEconomicDTO(yield_curve_10y_2y=0.5, high_yield_oas=2.0, inflation_rate=0.02)
        broken_ctx = SignalContext(bar=bar, fundamentals=None, macro=macro)
        with pytest.raises(AttributeError):
            DividendQualitySignal().compute(pd.Series({}), broken_ctx)

    def test_nan_dividend_yield_safely_no_ops_to_zero(self):
        """Unlike the modules in Section 1, this one is accidentally safe:
        `NaN > 0` is False, so a NaN dividend_yield falls through to the
        implicit 'no dividend' branch (points stays 0.0) -- the lucky
        outcome here, NOT an explicit pd.isna() guard."""
        bar = MarketBarDTO(datetime.now(), "TEST", 100.0, 101.0, 99.0, 100.0, 1_000)
        fund = FundamentalDataDTO(
            ticker="TEST", pe_ratio=15.0, pb_ratio=2.0, dividend_yield=float("nan"),
            book_value=50.0, eps_trailing=5.0, dividend_growth_rate=0.02,
            payout_ratio=0.0, sector="Technology", company_name="Test Co",
        )
        macro = MacroEconomicDTO(yield_curve_10y_2y=0.5, high_yield_oas=2.0, inflation_rate=0.02)
        ctx = SignalContext(bar=bar, fundamentals=fund, macro=macro)
        out = DividendQualitySignal().compute(pd.Series({}), ctx)
        assert out.score == 0.0


class TestForecastAlignmentMissingPriceData:
    """tests/test_scoring_signals.py::TestForecastAlignment always supplies
    real positive floats; it never exercises NaN prices or current_price=0.
    This class closes that gap."""

    def _ctx(self) -> SignalContext:
        bar = MarketBarDTO(datetime.now(), "TEST", 100.0, 101.0, 99.0, 100.0, 1_000)
        fund = FundamentalDataDTO(
            ticker="TEST", pe_ratio=15.0, pb_ratio=2.0, dividend_yield=0.0,
            book_value=50.0, eps_trailing=5.0, dividend_growth_rate=0.02,
            payout_ratio=0.0, sector="Technology", company_name="Test Co",
        )
        macro = MacroEconomicDTO(yield_curve_10y_2y=0.5, high_yield_oas=2.0, inflation_rate=0.02)
        return SignalContext(bar=bar, fundamentals=fund, macro=macro)

    def test_nan_forecast_price_fabricates_erosion_score(self):
        """KNOWN GAP (see module docstring): `forecast_price > current_price`
        is False when forecast_price is NaN, so execution falls to the
        'structural price erosion' branch -- a confidently bearish -10pts
        reading from a missing forecast, never neutral."""
        row = pd.Series({"current_price": 100.0, "forecast_price": float("nan")})
        out = ForecastAlignmentSignal().compute(row, self._ctx())
        assert out.score == pytest.approx(-1.0)
        assert "structural price erosion" in out.explanation

    def test_nan_current_price_fabricates_erosion_score(self):
        row = pd.Series({"current_price": float("nan"), "forecast_price": 105.0})
        out = ForecastAlignmentSignal().compute(row, self._ctx())
        assert out.score == pytest.approx(-1.0)

    def test_zero_current_price_fabricates_maximal_bullish_score(self):
        """KNOWN GAP (see module docstring), verified by direct execution
        (not just source-reading): a real current_price of exactly 0.0 with
        forecast_price > 0 does NOT raise ZeroDivisionError -- Python float
        division by zero produces `inf`, not an exception (only int
        division raises). `expected_gain` becomes `inf`, which is `>= 1.5`,
        so this fabricates the single MOST CONFIDENT bullish reading the
        module can produce (+1.0, "Strong forecast projection (+inf%)") from
        a degenerate zero price -- arguably worse than the bearish-fabrication
        pattern elsewhere in this file, since it actively encourages a BUY."""
        row = pd.Series({"current_price": 0.0, "forecast_price": 10.0})
        out = ForecastAlignmentSignal().compute(row, self._ctx())
        assert out.score == pytest.approx(1.0)
        assert "+inf%" in out.explanation


# ============================================================================
# Section 3 — Cross-cutting universal-contract sweep over all 17 registered
# modules (signals/__init__.py eagerly imports every signals/*.py file, so
# global_registry.get_all() is fully populated by the time this module is
# collected by pytest).
# ============================================================================

_ALL_REGISTERED = sorted(global_registry.get_all().items())
_RSI2_NAME = "rsi2_mean_reversion"  # documented long-only [0, 1] exception


@pytest.mark.parametrize("name,module", _ALL_REGISTERED, ids=[n for n, _ in _ALL_REGISTERED])
class TestUniversalSignalModuleContract:
    def test_at_least_seventeen_modules_registered(self, name, module):
        # Sanity check the parametrization itself isn't silently empty.
        assert len(_ALL_REGISTERED) == 17

    def test_abc_conformance(self, name, module):
        assert isinstance(module, SignalModule)
        assert module.name == name
        assert isinstance(module.name, str) and len(module.name) > 0

    def test_required_features_is_list_of_strings(self, name, module):
        assert isinstance(module.required_features, list)
        assert all(isinstance(f, str) for f in module.required_features)

    @pytest.mark.parametrize(
        "regime_inputs,vix,sahm",
        [
            ((0.5, 2.0, 0.02), 15.0, 0.0),    # RISK ON
            ((-0.5, 7.0, 0.02), 20.0, 0.6),   # RECESSION
            ((0.5, 6.5, 0.02), 20.0, 0.0),    # CREDIT EVENT
            ((0.5, 5.0, 0.02), 35.0, 0.0),    # NEUTRAL + VIX killswitch
        ],
        ids=["risk_on", "recession", "credit_event", "neutral_high_vix"],
    )
    def test_is_active_in_regime_returns_bool_without_raising(
        self, name, module, regime_inputs, vix, sahm
    ):
        ctx = _signal_context(market_regime_inputs=regime_inputs, vix_value=vix, sahm_rule_indicator=sahm)
        result = module.is_active_in_regime(ctx.macro)
        assert isinstance(result, bool)

    def test_pre_compute_does_not_raise_on_empty_universe(self, name, module):
        ctx = _signal_context()
        # Default no-op for most modules; the 4 cross-sectional overrides
        # (cross_sectional_momentum, multifactor, lgbm_ranker, news_catalyst)
        # all explicitly guard an empty/columnless DataFrame (verified by
        # reading their source) rather than crash.
        module.pre_compute(pd.DataFrame(), ctx)

    def test_score_band_with_realistic_populated_row(self, name, module):
        """Using a fully-populated, realistic row (all required keys for
        all 17 modules, valid non-NaN values) -- this deliberately does NOT
        exercise the missing-input paths documented as fabricated/crashing
        in Sections 1-2 above; it proves the NORMAL, well-formed-input case
        stays within the documented score band for every registered module."""
        ctx = _signal_context()
        row = _realistic_row()
        out = module.compute(row, ctx)
        assert isinstance(out, SignalOutput)
        assert isinstance(out.score, (int, float))
        lo, hi = (0.0, 1.0) if name == _RSI2_NAME else (-1.0, 1.0)
        assert lo <= out.score <= hi, f"{name} score {out.score} outside [{lo}, {hi}]"
        assert isinstance(out.confidence, (int, float))
        assert 0.0 <= out.confidence <= 1.0
        assert isinstance(out.explanation, str)
        assert isinstance(out.meta_label_proba, (int, float))
        assert 0.0 <= out.meta_label_proba <= 1.0
