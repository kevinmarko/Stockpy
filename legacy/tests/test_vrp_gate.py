import pytest
from dto_models import MacroEconomicDTO
from technical_options_engine import OptionsPricingRecommender

def test_vrp_gate_scenarios():
    """
    Asserts that the VRP regime gate makes the correct options pricing strategy matrix recommendations.
    Only sell premium if (true_ivr > 50 AND vrp > 0.02 AND macro_dto.vix < 30 AND macro_dto.market_regime != "CREDIT EVENT").
    """
    recommender = OptionsPricingRecommender(stock_price=100.0)
    
    # Standard bullish trend parameters
    trend_bullish = "Bullish"
    trend_bearish = "Bearish"
    trend_neutral = "Neutral"

    # Scenario 1: Happy path - sell premium (Put Credit Spread)
    macro_ok = MacroEconomicDTO(
        yield_curve_10y_2y=0.5,
        high_yield_oas=2.0,
        inflation_rate=2.0,
        vix_value=15.0  # < 30
    )
    # market_regime should be "RISK ON" (not CREDIT EVENT)
    assert macro_ok.market_regime != "CREDIT EVENT"
    
    res1 = recommender.generate_strategy_pricing_matrix(
        true_ivr=75.0,  # > 50
        current_iv=0.25,
        trend_bias=trend_bullish,
        vrp=0.05,       # > 0.02
        macro_dto=macro_ok
    )
    assert res1["Strategy"] == "Put Credit Spread"
    assert res1["Action"] == "Sell to Open"

    # Scenario 2: Negative VRP -> DO NOT sell premium
    res2 = recommender.generate_strategy_pricing_matrix(
        true_ivr=75.0,
        current_iv=0.25,
        trend_bias=trend_bullish,
        vrp=-0.01,      # < 0.02
        macro_dto=macro_ok
    )
    assert res2["Strategy"] == "Cash"
    assert res2["Action"] == "Wait"

    # Scenario 3: VIX >= 30 -> DO NOT sell premium
    macro_high_vix = MacroEconomicDTO(
        yield_curve_10y_2y=0.5,
        high_yield_oas=2.0,
        inflation_rate=2.0,
        vix_value=32.0  # >= 30
    )
    res3 = recommender.generate_strategy_pricing_matrix(
        true_ivr=80.0,
        current_iv=0.45,
        trend_bias=trend_neutral,
        vrp=0.08,
        macro_dto=macro_high_vix
    )
    assert res3["Strategy"] == "Cash"
    assert res3["Action"] == "Wait"

    # Scenario 4: Credit Event -> DO NOT sell premium
    macro_credit_event = MacroEconomicDTO(
        yield_curve_10y_2y=0.5,
        high_yield_oas=6.5,  # triggers CREDIT EVENT
        inflation_rate=2.0,
        vix_value=18.0
    )
    assert macro_credit_event.market_regime == "CREDIT EVENT"
    
    res4 = recommender.generate_strategy_pricing_matrix(
        true_ivr=80.0,
        current_iv=0.35,
        trend_bias=trend_bearish,
        vrp=0.06,
        macro_dto=macro_credit_event
    )
    assert res4["Strategy"] == "Cash"
    assert res4["Action"] == "Wait"

    # Scenario 5: VRP is NaN (get_vrp() couldn't resolve an options-chain-
    # derived IV -- common, not rare) -> fail closed, DO NOT sell premium.
    # Regression test for the bug where `vrp is not None and vrp <= threshold`
    # let a NaN VRP through: `nan <= threshold` is always False in Python, so
    # the old gate treated "unmeasurable" as "comfortably above threshold".
    res5 = recommender.generate_strategy_pricing_matrix(
        true_ivr=80.0,
        current_iv=0.35,
        trend_bias=trend_bullish,
        vrp=float("nan"),
        macro_dto=macro_ok,
    )
    assert res5["Strategy"] == "Cash"
    assert res5["Action"] == "Wait"

    # Scenario 6: VRP is None (no options chain available to derive VRP from
    # at all -- distinct from Scenario 5's "we tried and it came back NaN")
    # deliberately still SKIPS the VRP leg of the gate, unchanged from before
    # the Scenario 5 fix. This is the documented contract several callers
    # (gui/panels/options_matrix.py, reporting/options_snapshot.py,
    # investyo_mcp_server.py, options_ondemand.py, api/metrics_api.py) rely on
    # when they have no options chain to compute a real VRP from.
    res6 = recommender.generate_strategy_pricing_matrix(
        true_ivr=80.0,
        current_iv=0.35,
        trend_bias=trend_bullish,
        vrp=None,
        macro_dto=macro_ok,
    )
    assert res6["Strategy"] == "Put Credit Spread"
    assert res6["Action"] == "Sell to Open"
