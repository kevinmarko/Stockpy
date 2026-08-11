import pytest
import json
import uuid
import time
from unittest.mock import patch, MagicMock, AsyncMock

@pytest.fixture
def anyio_backend():
    return "asyncio"

# Must mock setting before import if necessary, but we can mock _get_broker
from execution.broker_base import OrderIntent, OrderSide, OrderType, OrderStatus, OrderResult
from robinhood_execution_mcp import execute_live_trade, confirm_live_trade, cancel_order, get_live_positions, _pending_orders, _rate_limiter

@pytest.fixture(autouse=True)
def reset_state():
    _pending_orders.clear()
    _rate_limiter.tokens = _rate_limiter.capacity

def test_execute_live_trade_returns_token():
    result = execute_live_trade("AAPL", "buy", 10.0, "market")
    data = json.loads(result)
    assert data["status"] == "pending_confirmation"
    assert "confirmation_token" in data
    assert data["details"]["symbol"] == "AAPL"
    assert data["details"]["qty"] == 10.0
    
    # Check it's in pending orders
    token = data["confirmation_token"]
    assert token in _pending_orders
    assert _pending_orders[token]["intent"].symbol == "AAPL"

@pytest.mark.anyio
async def test_confirm_live_trade_success():
    # Setup pending order
    token = str(uuid.uuid4())
    intent = OrderIntent(
        strategy_id="mcp-agent",
        symbol="MSFT",
        side=OrderSide.BUY,
        qty=5.0,
        order_type=OrderType.MARKET
    )
    _pending_orders[token] = {
        "intent": intent,
        "expires": time.time() + 300
    }
    
    mock_om = AsyncMock()
    mock_om.submit_order_with_idempotency.return_value = OrderResult(
        client_order_id="client-123",
        status=OrderStatus.ACCEPTED,
        broker_order_id="broker-123"
    )
    
    with patch("robinhood_execution_mcp.OrderManager", return_value=mock_om), \
         patch("robinhood_execution_mcp._get_broker", return_value=MagicMock()):
        result = await confirm_live_trade(token)
        data = json.loads(result)
        
        assert data["status"] == "success"
        assert data["broker_order_id"] == "broker-123"
        assert data["order_status"] == "accepted"
        
        # Verify removed from pending
        assert token not in _pending_orders
        
@pytest.mark.anyio
async def test_confirm_live_trade_expired():
    token = str(uuid.uuid4())
    intent = OrderIntent(
        strategy_id="mcp-agent",
        symbol="MSFT",
        side=OrderSide.BUY,
        qty=5.0,
        order_type=OrderType.MARKET
    )
    _pending_orders[token] = {
        "intent": intent,
        "expires": time.time() - 10 # expired
    }
    
    result = await confirm_live_trade(token)
    data = json.loads(result)
    assert data["status"] == "error"
    assert "expired" in data["message"]
    
def test_rate_limiter_blocks():
    # Consume all tokens
    for _ in range(5):
        assert _rate_limiter.consume() == True
        
    # 6th should fail
    assert _rate_limiter.consume() == False
    
    result = execute_live_trade("AAPL", "buy", 10.0)
    data = json.loads(result)
    assert data["status"] == "error"
    assert "Rate limit exceeded" in data["message"]

@pytest.mark.anyio
async def test_cancel_order():
    mock_broker = AsyncMock()
    mock_broker.cancel_order.return_value = True
    
    with patch("robinhood_execution_mcp._get_broker", return_value=mock_broker):
        result = await cancel_order("broker-123")
        data = json.loads(result)
        assert data["status"] == "success"

def test_get_live_positions():
    mock_snapshot = MagicMock()
    mock_snapshot.net_liquidity = 10000.0
    mock_snapshot.buying_power = 5000.0
    
    pos = MagicMock()
    pos.symbol = "AAPL"
    pos.qty = 10.0
    pos.market_value = 1500.0
    pos.unrealized_pl = 50.0
    mock_snapshot.positions = [pos]
    
    with patch("data.historical_store.HistoricalStore") as mock_store:
        mock_instance = mock_store.return_value
        mock_instance.latest_account_snapshot.return_value = mock_snapshot
        
        result = get_live_positions()
        data = json.loads(result)
        
        assert data["status"] == "success"
        assert data["net_liquidity"] == 10000.0
        assert len(data["positions"]) == 1
        assert data["positions"][0]["symbol"] == "AAPL"
