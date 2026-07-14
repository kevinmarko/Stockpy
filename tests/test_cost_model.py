import pytest
from execution.cost_model import TieredCostModel

def test_tiered_cost_model_aapl():
    """
    Test 100 shares of AAPL at $150 round-trip.
    AAPL is large_cap, so:
    - Base commission = $0 (default Alpaca style)
    - Bid-ask spread = 1.0 bps round-trip (0.5 bps each side)
    - Market order slippage = 5.0 bps each side = 10.0 bps round-trip
    - SEC fee = 0.0000278 (only on sells)
    - TAF = 0.000166 per share (only on sells, capped at $8.30)
    """
    model = TieredCostModel()
    
    shares = 100
    price = 150.0
    value = shares * price # $15,000
    
    # 1. Test Buy costs
    buy_costs = model.calculate_cost(side="buy", shares=shares, price=price, order_type="market")
    assert buy_costs["commission"] == 0.0
    # spread = 15000 * 0.5 / 10000 = $0.75
    assert buy_costs["spread"] == 0.75
    # slippage = 15000 * 5.0 / 10000 = $7.50
    assert buy_costs["slippage"] == 7.50
    assert buy_costs["sec_fee"] == 0.0
    assert buy_costs["taf"] == 0.0
    assert buy_costs["total_dollars"] == 8.25 # 0.75 + 7.50

    # 2. Test Sell costs
    sell_costs = model.calculate_cost(side="sell", shares=shares, price=price, order_type="market")
    assert sell_costs["commission"] == 0.0
    assert sell_costs["spread"] == 0.75
    assert sell_costs["slippage"] == 7.50
    # sec_fee = 15000 * 0.0000278 = $0.417
    assert abs(sell_costs["sec_fee"] - 0.417) < 0.001
    # TAF = 100 * 0.000166 = $0.0166
    assert abs(sell_costs["taf"] - 0.0166) < 0.0001
    
    expected_sell_total = 0.75 + 7.50 + 0.417 + 0.0166 # ~8.6836
    assert abs(sell_costs["total_dollars"] - expected_sell_total) < 0.01

    # 3. Test TAF Cap
    # 100,000 shares of AAPL at $150
    huge_sell_costs = model.calculate_cost(side="sell", shares=100000, price=150.0, order_type="market")
    # TAF cap should be $8.30
    assert huge_sell_costs["taf"] == 8.30


def test_estimate_round_trip_cost_aapl():
    """
    ``estimate_round_trip_cost`` is a distinct method from ``calculate_cost``
    (which the rest of this file exercises) and, before this test, was never
    called by any test in the suite -- confirmed by a repo-wide grep during
    the 2026-07-14 test-coverage re-audit's Phase 5 Gravity-step
    investigation (mirrors ``Gravity AI Review Suite.py``'s STEP 11 check,
    which asserts this exact scenario's total is ~$16.93).

    Same 100 shares of AAPL @ $150 scenario as
    ``test_tiered_cost_model_aapl`` above, but verifies the ROUND-TRIP
    aggregation logic itself: commission/spread/slippage are summed from
    BOTH legs (buy + sell), while sec_fee/taf come from the SELL leg ONLY
    (regulatory fees apply only to sells -- they must not be double-counted
    by summing both legs, since the buy leg's sec_fee/taf are always 0.0).
    """
    model = TieredCostModel()

    buy_costs = model.calculate_cost(side="buy", shares=100, price=150.0, order_type="market")
    sell_costs = model.calculate_cost(side="sell", shares=100, price=150.0, order_type="market")

    round_trip = model.estimate_round_trip_cost("AAPL", shares=100, price=150.0, order_type="market")

    assert round_trip["commission"] == pytest.approx(buy_costs["commission"] + sell_costs["commission"])
    assert round_trip["spread"] == pytest.approx(buy_costs["spread"] + sell_costs["spread"])
    assert round_trip["slippage"] == pytest.approx(buy_costs["slippage"] + sell_costs["slippage"])
    # Sell-leg-only, NOT buy+sell (the buy leg's sec_fee/taf are 0.0 anyway,
    # but this pins the aggregation rule itself, not just today's values).
    assert round_trip["sec_fee"] == pytest.approx(sell_costs["sec_fee"])
    assert round_trip["taf"] == pytest.approx(sell_costs["taf"])

    assert round_trip["total_dollars"] == pytest.approx(16.93, abs=0.01)
    assert round_trip["total_dollars"] == pytest.approx(
        buy_costs["total_dollars"] + sell_costs["total_dollars"]
    )

    trade_value = 100 * 150.0
    expected_bps = round_trip["total_dollars"] / trade_value * 10000.0
    assert round_trip["total_bps"] == pytest.approx(expected_bps)


def test_estimate_round_trip_cost_zero_trade_value_no_division_by_zero():
    """total_bps must degrade to 0.0, never raise, when shares*price == 0."""
    model = TieredCostModel()

    round_trip = model.estimate_round_trip_cost("AAPL", shares=0, price=150.0, order_type="market")

    assert round_trip["total_bps"] == 0.0
