import pytest
import asyncio
from unittest.mock import patch, MagicMock
from execution.fmp_paper_broker import FMPPaperBroker
from execution.broker_base import OrderIntent, OrderSide, OrderType, OrderStatus

@pytest.fixture
def mock_store():
    with patch("execution.fmp_paper_broker.PaperAccountStore") as mock:
        store_instance = mock.return_value
        store_instance.apply_fill.return_value = True
        yield store_instance

@pytest.fixture
def broker(mock_store):
    return FMPPaperBroker(db_url="sqlite:///:memory:")

@pytest.mark.anyio
async def test_submit_order_success(broker, mock_store):
    intent = OrderIntent(
        strategy_id="test_strat",
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=10.0,
        order_type=OrderType.MARKET,
        client_order_id="test_order_1"
    )
    
    with patch("data.fmp_client.quote", return_value=[{"symbol": "AAPL", "price": 150.0, "marketCap": 2e12}]):
        result = await broker.submit_order(intent)
        
    assert result.status == OrderStatus.FILLED
    assert result.filled_qty == 10.0
    assert result.filled_avg_price == 150.0
    
    # Verify stream event
    event = await broker.stream_queue.get()
    assert event.event_type == "fill"
    assert event.client_order_id == "test_order_1"
    assert event.filled_qty == 10.0

@pytest.mark.anyio
async def test_submit_order_fmp_failure(broker):
    intent = OrderIntent(
        strategy_id="test_strat",
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=10.0,
        order_type=OrderType.MARKET,
        client_order_id="test_order_2"
    )
    
    with patch("data.fmp_client.quote", return_value=[]):
        result = await broker.submit_order(intent)
        
    assert result.status == OrderStatus.ERROR
    assert "not found" in result.error_message

@pytest.mark.anyio
async def test_submit_order_insufficient_funds(broker, mock_store):
    mock_store.apply_fill.return_value = False
    
    intent = OrderIntent(
        strategy_id="test_strat",
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=10.0,
        order_type=OrderType.MARKET,
        client_order_id="test_order_3"
    )
    
    with patch("data.fmp_client.quote", return_value=[{"symbol": "AAPL", "price": 150.0}]):
        result = await broker.submit_order(intent)
        
    assert result.status == OrderStatus.REJECTED
    assert "Insufficient" in result.error_message

@pytest.mark.anyio
async def test_cancel_order(broker):
    result = await broker.cancel_order("any_id")
    assert result is False
