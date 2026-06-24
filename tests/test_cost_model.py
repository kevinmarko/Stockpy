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
