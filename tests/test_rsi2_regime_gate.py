"""
InvestYo Quant Platform - RSI(2) Regime Gate Tests
=====================================================
Verifies the hard RISK-OFF gate on RSI2MeanReversionSignal.is_active_in_regime
and its wiring through SignalAggregator: with a recession/credit-event/high-VIX
macro context, the module's contribution is forced to 0 regardless of how
oversold RSI(2) is.
"""

import pandas as pd
from datetime import datetime

from signals.rsi2_mean_reversion import RSI2MeanReversionSignal
from signals.base import SignalContext
from signals.registry import SignalRegistry
from signals.aggregator import SignalAggregator
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO


def _bar_and_fundamentals():
    bar = MarketBarDTO(datetime.now(), "TEST", 100.0, 100.0, 100.0, 100.0, 1000)
    fundamentals = FundamentalDataDTO(
        ticker="TEST", company_name="Test Corp", sector="Technology",
        pe_ratio=15.0, pb_ratio=1.5, book_value=50.0, eps_trailing=5.0,
        dividend_yield=0.02, dividend_growth_rate=0.05, payout_ratio=0.30,
    )
    return bar, fundamentals


def _oversold_uptrend_row() -> pd.Series:
    return pd.Series({
        "Close": 100.0,
        "RSI_2": 1.0,   # maximally oversold
        "SMA_5": 102.0,
        "SMA_200": 90.0,  # uptrend
        "sector": "Technology",
    })


def test_recession_regime_forces_score_zero():
    sig = RSI2MeanReversionSignal()
    bar, fundamentals = _bar_and_fundamentals()
    # yield_curve < -0.25 and credit_spread > 6.0 -> RECESSION
    macro = MacroEconomicDTO(
        yield_curve_10y_2y=-0.5, high_yield_oas=8.0, inflation_rate=2.0,
        nominal_10y=4.0, vix_value=15.0,
    )
    assert macro.market_regime == "RECESSION"
    assert sig.is_active_in_regime(macro) is False


def test_credit_event_regime_forces_score_zero():
    sig = RSI2MeanReversionSignal()
    macro = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=7.0, inflation_rate=2.0,
        nominal_10y=4.0, vix_value=15.0,
    )
    assert macro.market_regime == "CREDIT EVENT"
    assert sig.is_active_in_regime(macro) is False


def test_high_vix_forces_score_zero_even_in_neutral_regime():
    sig = RSI2MeanReversionSignal()
    macro = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=2.0, inflation_rate=2.0,
        nominal_10y=4.0, vix_value=35.0,
    )
    assert macro.market_regime in ("NEUTRAL", "RISK ON")
    assert sig.is_active_in_regime(macro) is False


def test_risk_on_regime_remains_active():
    sig = RSI2MeanReversionSignal()
    macro = MacroEconomicDTO(
        yield_curve_10y_2y=2.0, high_yield_oas=1.5, inflation_rate=2.0,
        nominal_10y=4.0, vix_value=12.0,
    )
    assert sig.is_active_in_regime(macro) is True


def test_aggregator_suppresses_contribution_during_recession():
    """End-to-end: SignalAggregator must zero out this module's contribution
    when macro is RECESSION, even though the raw compute() score would be high."""
    registry = SignalRegistry()
    sig = RSI2MeanReversionSignal()
    registry.register(sig)

    bar, fundamentals = _bar_and_fundamentals()
    row = _oversold_uptrend_row()

    # Sanity check: in isolation (no regime gate), this row scores high.
    benign_macro = MacroEconomicDTO(0.5, 2.0, 2.0, 4.0, vix_value=15.0)
    benign_context = SignalContext(bar=bar, fundamentals=fundamentals, macro=benign_macro)
    raw_output = sig.compute(row, benign_context)
    assert raw_output.score > 0.5

    # Now run through the aggregator under a RECESSION macro.
    recession_macro = MacroEconomicDTO(-0.5, 8.0, 2.0, 4.0, vix_value=15.0)
    recession_context = SignalContext(bar=bar, fundamentals=fundamentals, macro=recession_macro)
    aggregator = SignalAggregator(registry, weights={"rsi2_mean_reversion": 10.0})

    final_score, score_log, warnings, details, outputs, _meta = aggregator.aggregate(row, recession_context)

    # Base neutral score is 50.0; the gated module must contribute nothing to
    # the aggregate score or explainer log, even though compute() itself still
    # ran (outputs retains the raw, ungated score for introspection/debugging).
    assert final_score == 50.0
    assert not any("Oversold-in-uptrend" in line for line in score_log)
    assert "rsi2_mean_reversion" in outputs  # raw compute() output is preserved
    assert outputs["rsi2_mean_reversion"].score > 0.5  # but never reaches the score/log
