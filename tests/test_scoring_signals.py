"""
InvestYo Quant Platform - Core Scoring Signal Tests
===================================================
Dedicated unit coverage for the five point-scoring signal modules that drive
the BUY/SELL/HOLD score yet previously had no tests of their own:

    signals/edge_garch.py          (weight 35.0 — highest in the system)
    signals/dividend_quality.py    (weight 25.0)
    signals/rsi_extremes.py        (weight 20.0)
    signals/sortino_drawdown.py    (weight 10.0)
    signals/forecast_alignment.py  (weight 10.0)

Each module maps a small set of indicator features to a points adjustment, then
normalizes ``points / weight`` into the canonical ``[-1.0, 1.0]`` score band
that ``SignalAggregator`` multiplies by the configured weight.  These tests pin:

* every score branch (threshold boundaries on both sides),
* the "no data" / NaN / missing-feature paths return a neutral 0.0 score
  (CONSTRAINT #4 — never fabricate a directional opinion from absent data),
* the points-to-score normalization (so a future weight change can't silently
  push a score outside [-1, 1]),
* ABC conformance + auto-registration into ``global_registry``,
* the registered weight in ``settings.SIGNAL_WEIGHTS`` matches the module's
  internal normalization constant (a divergence would mis-scale the signal),
* end-to-end contribution through ``SignalAggregator.aggregate()``.
"""

from datetime import datetime

import pandas as pd
import pytest

from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry
from signals.edge_garch import EdgeGarchSignal
from signals.dividend_quality import DividendQualitySignal
from signals.rsi_extremes import RSIExtremesSignal
from signals.sortino_drawdown import SortinoDrawdownSignal
from signals.forecast_alignment import ForecastAlignmentSignal
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO
from settings import settings


# =============================================================================
# Shared fixtures
# =============================================================================
def _make_context(
    *,
    dividend_yield: float = 0.0,
    payout_ratio: float = 0.0,
    sector: str = "Technology",
) -> SignalContext:
    """A neutral SignalContext; dividend fields tunable for DividendQuality."""
    bar = MarketBarDTO(datetime.now(), "TEST", 100.0, 101.0, 99.0, 100.0, 1_000)
    fund = FundamentalDataDTO(
        ticker="TEST",
        pe_ratio=None,
        pb_ratio=None,
        dividend_yield=dividend_yield,
        book_value=0.0,
        eps_trailing=0.0,
        dividend_growth_rate=0.0,
        payout_ratio=payout_ratio,
        sector=sector,
        company_name="Test Co",
    )
    macro = MacroEconomicDTO(
        yield_curve_10y_2y=0.5,
        high_yield_oas=2.0,
        inflation_rate=0.03,
        vix_value=15.0,
    )
    return SignalContext(bar=bar, fundamentals=fund, macro=macro)


# =============================================================================
# EdgeGarchSignal  (weight 35.0)
# =============================================================================
class TestEdgeGarch:
    def _score(self, **row) -> SignalOutput:
        return EdgeGarchSignal().compute(pd.Series(row), _make_context())

    def test_strong_edge_adds_15pts(self):
        out = self._score(edge_ratio=1.5, garch_vol=0.10)
        assert out.score == pytest.approx(15.0 / 35.0)
        assert "Strong Mathematical Edge" in out.explanation

    def test_edge_at_120_boundary_is_strong(self):
        # >= 1.2 is the strong threshold (inclusive)
        out = self._score(edge_ratio=1.2)
        assert out.score == pytest.approx(15.0 / 35.0)

    def test_negative_edge_subtracts_15pts(self):
        out = self._score(edge_ratio=0.5)
        assert out.score == pytest.approx(-15.0 / 35.0)
        assert "Negative Mathematical Edge" in out.explanation

    def test_edge_just_below_080_is_negative(self):
        out = self._score(edge_ratio=0.79)
        assert out.score == pytest.approx(-15.0 / 35.0)

    def test_edge_in_neutral_band_scores_zero(self):
        # 0.8 <= edge < 1.2 is neutral (no points either way)
        out = self._score(edge_ratio=1.0)
        assert out.score == 0.0
        assert out.explanation == ""

    def test_extreme_garch_vol_subtracts_20pts(self):
        out = self._score(garch_vol=0.55)
        assert out.score == pytest.approx(-20.0 / 35.0)
        assert "Extreme GARCH Volatility" in out.explanation

    def test_garch_vol_at_040_boundary_is_neutral(self):
        # Strictly > 0.40 triggers the penalty; exactly 0.40 must not.
        out = self._score(garch_vol=0.40)
        assert out.score == 0.0

    def test_strong_edge_plus_extreme_vol_combine(self):
        # +15 (edge) and -20 (vol) = -5 points
        out = self._score(edge_ratio=1.5, garch_vol=0.55)
        assert out.score == pytest.approx(-5.0 / 35.0)

    def test_non_positive_edge_ratio_ignored(self):
        # edge_ratio must be > 0 to be considered at all
        out = self._score(edge_ratio=0.0)
        assert out.score == 0.0

    def test_nan_and_missing_inputs_score_zero(self):
        assert self._score().score == 0.0
        assert self._score(edge_ratio=float("nan"), garch_vol=float("nan")).score == 0.0


# =============================================================================
# DividendQualitySignal  (weight 25.0)
# =============================================================================
class TestDividendQuality:
    def test_sustainable_dividend_adds_10pts(self):
        ctx = _make_context(dividend_yield=0.03, payout_ratio=0.40, sector="Technology")
        out = DividendQualitySignal().compute(pd.Series({}), ctx)
        assert out.score == pytest.approx(10.0 / 25.0)
        assert "Sustainable Dividend" in out.explanation

    def test_yield_trap_subtracts_25pts(self):
        # payout_ratio >= 0.75 (non-REIT) → unsustainable → full penalty
        ctx = _make_context(dividend_yield=0.08, payout_ratio=1.10, sector="Technology")
        out = DividendQualitySignal().compute(pd.Series({}), ctx)
        assert out.score == pytest.approx(-25.0 / 25.0)
        assert "Yield Trap Warning" in out.explanation

    def test_yield_trap_emits_warning_line(self):
        ctx = _make_context(dividend_yield=0.08, payout_ratio=1.10)
        out = DividendQualitySignal().compute(pd.Series({}), ctx)
        warnings = [ln for ln in out.explanation.splitlines() if ln.startswith("WARNING:")]
        assert warnings, "unsustainable dividend must emit a WARNING: explanation line"

    def test_no_dividend_scores_zero(self):
        ctx = _make_context(dividend_yield=0.0, payout_ratio=0.0)
        out = DividendQualitySignal().compute(pd.Series({}), ctx)
        assert out.score == 0.0
        assert out.explanation == ""

    def test_reit_high_payout_still_sustainable(self):
        # REITs are sustainable below 0.95; 0.90 payout in Real Estate passes.
        ctx = _make_context(dividend_yield=0.06, payout_ratio=0.90, sector="Real Estate")
        out = DividendQualitySignal().compute(pd.Series({}), ctx)
        assert out.score == pytest.approx(10.0 / 25.0)

    def test_non_reit_high_payout_is_trap(self):
        # Same 0.90 payout but a non-REIT sector → trap (threshold 0.75).
        ctx = _make_context(dividend_yield=0.06, payout_ratio=0.90, sector="Industrials")
        out = DividendQualitySignal().compute(pd.Series({}), ctx)
        assert out.score == pytest.approx(-25.0 / 25.0)


# =============================================================================
# RSIExtremesSignal  (weight 20.0)
# =============================================================================
class TestRSIExtremes:
    def _score(self, **row) -> SignalOutput:
        return RSIExtremesSignal().compute(pd.Series(row), _make_context())

    def test_oversold_adds_20pts(self):
        out = self._score(rsi=15.0)
        assert out.score == pytest.approx(20.0 / 20.0)
        assert out.score == 1.0
        assert "Mean Reversion" in out.explanation

    def test_overbought_subtracts_20pts(self):
        out = self._score(rsi=85.0)
        assert out.score == pytest.approx(-1.0)
        assert "Overbought" in out.explanation

    def test_rsi_at_30_boundary_is_neutral(self):
        # Strictly < 30 to fire; exactly 30 is neutral.
        assert self._score(rsi=30.0).score == 0.0

    def test_rsi_at_70_boundary_is_neutral(self):
        # Strictly > 70 to fire; exactly 70 is neutral.
        assert self._score(rsi=70.0).score == 0.0

    def test_midrange_rsi_scores_zero(self):
        assert self._score(rsi=50.0).score == 0.0

    def test_nan_and_missing_rsi_score_zero(self):
        assert self._score().score == 0.0
        assert self._score(rsi=float("nan")).score == 0.0


# =============================================================================
# SortinoDrawdownSignal  (weight 10.0)
# =============================================================================
class TestSortinoDrawdown:
    def _score(self, **row) -> SignalOutput:
        return SortinoDrawdownSignal().compute(pd.Series(row), _make_context())

    def test_high_sortino_adds_10pts(self):
        out = self._score(sortino_ratio=3.0)
        assert out.score == pytest.approx(1.0)
        assert "High Sortino" in out.explanation

    def test_sortino_at_20_boundary_is_neutral(self):
        # Strictly > 2.0 to reward.
        assert self._score(sortino_ratio=2.0).score == 0.0

    def test_steep_drawdown_subtracts_10pts(self):
        out = self._score(max_drawdown=-0.40)
        assert out.score == pytest.approx(-1.0)
        assert "Steep Drawdown" in out.explanation

    def test_drawdown_at_minus_025_boundary_is_neutral(self):
        # Strictly < -0.25 to penalize.
        assert self._score(max_drawdown=-0.25).score == 0.0

    def test_reward_and_penalty_cancel(self):
        # +10 (sortino) and -10 (drawdown) net to 0.
        out = self._score(sortino_ratio=3.0, max_drawdown=-0.40)
        assert out.score == 0.0

    def test_nan_and_missing_inputs_score_zero(self):
        assert self._score().score == 0.0
        assert self._score(
            sortino_ratio=float("nan"), max_drawdown=float("nan")
        ).score == 0.0


# =============================================================================
# ForecastAlignmentSignal  (weight 10.0)
# =============================================================================
class TestForecastAlignment:
    def _score(self, current_price, forecast_price) -> SignalOutput:
        row = pd.Series({"current_price": current_price, "forecast_price": forecast_price})
        return ForecastAlignmentSignal().compute(row, _make_context())

    def test_strong_upside_adds_10pts(self):
        # +2% projected gain (>= 1.5% threshold)
        out = self._score(current_price=100.0, forecast_price=102.0)
        assert out.score == pytest.approx(1.0)
        assert "Strong forecast projection" in out.explanation

    def test_upside_at_15pct_boundary_is_strong(self):
        out = self._score(current_price=100.0, forecast_price=101.5)
        assert out.score == pytest.approx(1.0)

    def test_moderate_upside_adds_5pts(self):
        # +0.5% gain → moderate (> 0 but < 1.5%)
        out = self._score(current_price=100.0, forecast_price=100.5)
        assert out.score == pytest.approx(5.0 / 10.0)
        assert "Moderate positive forecast" in out.explanation

    def test_forecast_below_price_subtracts_10pts(self):
        out = self._score(current_price=100.0, forecast_price=95.0)
        assert out.score == pytest.approx(-1.0)
        assert "structural price erosion" in out.explanation

    def test_forecast_equal_to_price_is_erosion_branch(self):
        # The else branch fires when forecast is NOT strictly greater than price.
        out = self._score(current_price=100.0, forecast_price=100.0)
        assert out.score == pytest.approx(-1.0)


# =============================================================================
# Cross-cutting: score band, ABC conformance, registration, weight alignment
# =============================================================================
_ALL_MODULES = [
    (EdgeGarchSignal, "edge_garch", 35.0),
    (DividendQualitySignal, "dividend_quality", 25.0),
    (RSIExtremesSignal, "rsi_extremes", 20.0),
    (SortinoDrawdownSignal, "sortino_drawdown", 10.0),
    (ForecastAlignmentSignal, "forecast_alignment", 10.0),
]


@pytest.mark.parametrize("cls,name,weight", _ALL_MODULES)
def test_abc_conformance(cls, name, weight):
    mod = cls()
    assert isinstance(mod, SignalModule)
    assert mod.name == name


@pytest.mark.parametrize("cls,name,weight", _ALL_MODULES)
def test_auto_registered_in_global_registry(cls, name, weight):
    all_mods = global_registry.get_all()
    names = (
        set(all_mods.keys())
        if isinstance(all_mods, dict)
        else {getattr(m, "name", None) for m in all_mods}
    )
    assert name in names, f"{name} must auto-register on import"


@pytest.mark.parametrize("cls,name,weight", _ALL_MODULES)
def test_settings_weight_matches_normalization_constant(cls, name, weight):
    """The internal ``weight`` normalizer must equal the configured aggregator
    weight; a divergence would mis-scale the signal's contribution."""
    assert settings.SIGNAL_WEIGHTS.get(name) == pytest.approx(weight)


@pytest.mark.parametrize("cls,name,weight", _ALL_MODULES)
def test_score_always_within_unit_band(cls, name, weight):
    """No reachable input may push the normalized score outside [-1, 1]."""
    mod = cls()
    ctx = _make_context(dividend_yield=0.08, payout_ratio=1.5, sector="Technology")
    # Throw an extreme row at every module; only the relevant keys are read.
    row = pd.Series({
        "edge_ratio": 99.0,
        "garch_vol": 5.0,
        "rsi": 0.0,
        "sortino_ratio": 99.0,
        "max_drawdown": -0.99,
        "current_price": 100.0,
        "forecast_price": 1.0,
    })
    out = mod.compute(row, ctx)
    assert -1.0 <= out.score <= 1.0
    assert out.confidence == 1.0


# =============================================================================
# End-to-end: contribution flows through SignalAggregator
# =============================================================================
def test_edge_garch_contributes_through_aggregator():
    """A strong-edge row must lift the aggregate score above the 50 neutral
    base by exactly the edge_garch weighted contribution (in isolation)."""
    from signals.aggregator import SignalAggregator
    from signals.registry import SignalRegistry

    reg = SignalRegistry()
    reg.register(EdgeGarchSignal())
    agg = SignalAggregator(registry=reg, weights={"edge_garch": 35.0})

    ctx = _make_context()
    row = pd.Series({"Symbol": "TEST", "edge_ratio": 1.5, "garch_vol": 0.10})
    result = agg.aggregate(row, ctx)
    final_score = result[0]

    # score = 15/35; contribution = (15/35) * 35 = 15 points above the 50 base.
    assert final_score == pytest.approx(65.0, abs=1e-6)
