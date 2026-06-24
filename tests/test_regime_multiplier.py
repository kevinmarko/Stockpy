"""
InvestYo Quant Platform - Regime Multiplier Signal Tests
============================================================
Unit tests for signals/regime_multiplier.py: confirms it never contributes
directional alpha (score always 0.0) and correctly carries
hmm_risk_on_probability as a position-sizing multiplier via its confidence
field, with a neutral (1.0) default when the HMM didn't run.
"""

from datetime import datetime

import pandas as pd

from signals.regime_multiplier import RegimeMultiplierSignal
from signals.base import SignalModule, SignalContext
from signals.registry import global_registry
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO


def _make_context(hmm_risk_on_probability=None) -> SignalContext:
    bar = MarketBarDTO(datetime.now(), "TEST", 100.0, 100.0, 100.0, 100.0, 1000)
    fund = FundamentalDataDTO(
        ticker="TEST", pe_ratio=None, pb_ratio=None, dividend_yield=0.0,
        book_value=0.0, eps_trailing=0.0, dividend_growth_rate=0.0,
        payout_ratio=0.0, sector="Unknown", company_name="Unknown",
    )
    macro = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=2.0, inflation_rate=0.03,
        vix_value=15.0, hmm_risk_on_probability=hmm_risk_on_probability,
    )
    return SignalContext(bar=bar, fundamentals=fund, macro=macro)


# =============================================================================
# Happy path
# =============================================================================
def test_confidence_carries_hmm_risk_on_probability():
    ctx = _make_context(hmm_risk_on_probability=0.65)
    module = RegimeMultiplierSignal()
    output = module.compute(pd.Series({"Symbol": "TEST"}), ctx)
    assert output.confidence == 0.65


def test_score_is_always_zero_regardless_of_hmm_value():
    """Never adds directional alpha -- score must be 0.0 for any input."""
    for p in (0.0, 0.1, 0.5, 0.9, 1.0):
        ctx = _make_context(hmm_risk_on_probability=p)
        module = RegimeMultiplierSignal()
        output = module.compute(pd.Series({"Symbol": "TEST"}), ctx)
        assert output.score == 0.0


# =============================================================================
# Edge case: HMM unavailable -> neutral multiplier, never penalized
# =============================================================================
def test_neutral_multiplier_when_hmm_unavailable():
    ctx = _make_context(hmm_risk_on_probability=None)
    module = RegimeMultiplierSignal()
    output = module.compute(pd.Series({"Symbol": "TEST"}), ctx)
    assert output.score == 0.0
    assert output.confidence == 1.0


# =============================================================================
# Aggregator-level: weighted-sum contribution must be exactly 0.0
# =============================================================================
def test_aggregator_contribution_is_always_zero():
    from signals.aggregator import SignalAggregator
    from signals.registry import SignalRegistry

    mock_registry = SignalRegistry()
    mock_registry.register(RegimeMultiplierSignal())
    # Even with a large weight, contribution must stay 0.0 since score=0.0.
    aggregator = SignalAggregator(mock_registry, weights={"regime_multiplier": 999.0})

    ctx = _make_context(hmm_risk_on_probability=0.05)
    final_score, score_log, _warnings, _details, outputs, _meta = aggregator.aggregate(
        pd.Series({"Symbol": "TEST"}), ctx
    )
    assert final_score == 50.0  # neutral base, unchanged
    assert outputs["regime_multiplier"].confidence == 0.05  # introspection still works


# =============================================================================
# ABC conformance + registration + settings weight invariant
# =============================================================================
def test_module_conforms_to_signal_module_abc():
    module = RegimeMultiplierSignal()
    assert isinstance(module, SignalModule)
    assert module.name == "regime_multiplier"


def test_module_is_registered():
    assert "regime_multiplier" in global_registry._modules


def test_settings_weight_is_zero():
    """Structural enforcement of the 'no directional alpha' invariant --
    even if compute() were ever changed to return a nonzero score by mistake,
    this weight being 0.0 keeps it inert in the aggregator's weighted sum."""
    from settings import settings
    assert settings.SIGNAL_WEIGHTS.get("regime_multiplier") == 0.0
