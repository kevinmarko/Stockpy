import pytest
import json
import uuid
import time
from unittest.mock import patch, MagicMock, AsyncMock

from execution.broker_base import (
    AccountSnapshot,
    OrderIntent,
    OrderSide,
    OrderType,
    OrderStatus,
    OrderResult,
    PositionSnapshot,
)
from data.robinhood_portfolio import AccountSnapshot as RHAccountSnapshot, PortfolioPosition
from settings import settings


@pytest.fixture
def anyio_backend():
    return "asyncio"

# Must mock setting before import if necessary, but we can mock _get_broker
from broker_live_execution_mcp import (
    execute_live_trade,
    confirm_live_trade,
    cancel_order,
    get_live_positions,
    _pending_orders,
    _rate_limiter,
)


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

    with patch("broker_live_execution_mcp.OrderManager", return_value=mock_om), \
         patch("broker_live_execution_mcp._get_broker", return_value=AsyncMock()):
        result = await confirm_live_trade(token)
        data = json.loads(result)

        assert data["status"] == "success"
        assert data["broker_order_id"] == "broker-123"
        assert data["order_status"] == "accepted"

        # Verify removed from pending
        assert token not in _pending_orders

        # The risk gate is no longer a silent no-op -- a real RiskContext
        # must have been threaded through to the manager call.
        assert mock_om.submit_order_with_idempotency.await_count == 1
        _, kwargs = mock_om.submit_order_with_idempotency.await_args
        assert kwargs.get("risk_context") is not None


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

    with patch("broker_live_execution_mcp._get_broker", return_value=mock_broker):
        result = await cancel_order("broker-123")
        data = json.loads(result)
        assert data["status"] == "success"


# ---------------------------------------------------------------------------
# get_live_positions() -- regression coverage for the dict-iteration bug.
#
# AccountSnapshot.positions is dict[symbol -> PortfolioPosition] (see
# data/robinhood_portfolio.py). The old code did `for p in positions:`,
# which iterates the dict's string KEYS, then called `.symbol`/`.qty` on
# those strings -- an immediate AttributeError on any account with real
# holdings. This fixture uses the REAL AccountSnapshot/PortfolioPosition
# dataclasses (not a MagicMock standing in for arbitrary attributes) so the
# test would have failed under the old code.
# ---------------------------------------------------------------------------

def _real_multi_position_snapshot() -> RHAccountSnapshot:
    from datetime import datetime, timezone

    positions = {
        "AAPL": PortfolioPosition(
            symbol="AAPL",
            quantity=10.0,
            average_cost=100.0,
            current_price=150.0,
            market_value=1500.0,
            unrealized_pl=500.0,
            unrealized_pl_pct=50.0,
            dividends_received=12.5,
            name="Apple Inc.",
        ),
        "MSFT": PortfolioPosition(
            symbol="MSFT",
            quantity=4.0,
            average_cost=300.0,
            current_price=320.0,
            market_value=1280.0,
            unrealized_pl=80.0,
            unrealized_pl_pct=6.67,
            dividends_received=3.0,
            name="Microsoft Corp.",
        ),
    }
    return RHAccountSnapshot(
        positions=positions,
        buying_power=5000.0,
        total_equity=12000.0,
        total_dividends=15.5,
        fetched_at=datetime.now(timezone.utc),
    )


def test_get_live_positions_multi_position_real_snapshot():
    """Would have raised AttributeError under the pre-fix `for p in positions:`
    dict-key iteration bug -- now returns correctly-mapped position rows."""
    snapshot = _real_multi_position_snapshot()

    with patch("data.historical_store.HistoricalStore") as mock_store:
        mock_store.return_value.latest_account_snapshot.return_value = snapshot

        result = get_live_positions()
        data = json.loads(result)

    assert data["status"] == "success"
    assert data["total_equity"] == 12000.0
    assert data["buying_power"] == 5000.0
    assert len(data["positions"]) == 2

    by_symbol = {p["symbol"]: p for p in data["positions"]}
    assert by_symbol["AAPL"]["quantity"] == 10.0
    assert by_symbol["AAPL"]["market_value"] == 1500.0
    assert by_symbol["AAPL"]["unrealized_pl"] == 500.0
    assert by_symbol["MSFT"]["quantity"] == 4.0


def test_get_live_positions_empty_snapshot():
    snapshot = RHAccountSnapshot(
        positions={},
        buying_power=1000.0,
        total_equity=1000.0,
        total_dividends=0.0,
        fetched_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    with patch("data.historical_store.HistoricalStore") as mock_store:
        mock_store.return_value.latest_account_snapshot.return_value = snapshot
        result = get_live_positions()
        data = json.loads(result)

    assert data["status"] == "success"
    assert data["positions"] == []
    assert data["total_equity"] == 1000.0


def test_get_live_positions_no_snapshot():
    with patch("data.historical_store.HistoricalStore") as mock_store:
        mock_store.return_value.latest_account_snapshot.return_value = None
        result = get_live_positions()
        data = json.loads(result)

    assert data["status"] == "error"


# ---------------------------------------------------------------------------
# confirm_live_trade() -- regression coverage for the silently-skipped
# pre-trade risk gate. Uses the REAL OrderManager + REAL PreTradeRiskGate
# (neither is mocked here) against a mocked broker, so the only thing under
# test is whether a genuine RiskContext reaches the gate. A position whose
# notional exceeds account equity trips `max_position_size_check` under the
# gate's default settings (MAX_POSITION_WEIGHT=1.0, i.e. 100% of equity) --
# before this fix, no RiskContext was ever built or passed, so this same
# order would have silently gone through to broker.submit_order.
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_confirm_live_trade_blocked_by_real_risk_gate():
    token = str(uuid.uuid4())
    intent = OrderIntent(
        strategy_id="mcp-agent",
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=100.0,  # 100 shares
        order_type=OrderType.MARKET,
    )
    _pending_orders[token] = {
        "intent": intent,
        "expires": time.time() + 300,
    }

    mock_broker = AsyncMock()
    mock_broker.get_open_positions.return_value = []
    mock_broker.get_account.return_value = AccountSnapshot(
        equity=1000.0, cash=1000.0, buying_power=1000.0
    )
    # submit_order must NEVER be reached -- the risk gate should block first.
    mock_broker.submit_order = AsyncMock(
        side_effect=AssertionError("broker.submit_order should not have been called")
    )

    mock_quote = MagicMock()
    mock_quote.price = 500.0  # 100 shares * $500 = $50,000 notional >> $1,000 equity

    with patch("broker_live_execution_mcp._get_broker", return_value=mock_broker), \
         patch("data.market_data.get_provider") as mock_get_provider:
        mock_get_provider.return_value.get_latest_quote.return_value = mock_quote

        result = await confirm_live_trade(token)
        data = json.loads(result)

    assert data["status"] == "error"
    assert "PRE-TRADE GATE" in data["message"]
    assert "max_position_size" in data["message"]
    mock_broker.submit_order.assert_not_awaited()
    assert token not in _pending_orders


@pytest.mark.anyio
async def test_confirm_live_trade_passes_real_risk_gate_when_within_limits():
    """Sanity counterpart to the blocked test above -- a small, well within
    limits order still executes once a genuine RiskContext is threaded
    through (proves the fix doesn't just fail closed for everything)."""
    token = str(uuid.uuid4())
    intent = OrderIntent(
        strategy_id="mcp-agent",
        symbol="AAPL",
        side=OrderSide.BUY,
        qty=1.0,
        order_type=OrderType.MARKET,
    )
    _pending_orders[token] = {
        "intent": intent,
        "expires": time.time() + 300,
    }

    mock_broker = AsyncMock()
    mock_broker.get_open_positions.return_value = []
    mock_broker.get_account.return_value = AccountSnapshot(
        equity=1_000_000.0, cash=1_000_000.0, buying_power=1_000_000.0
    )
    mock_broker.submit_order.return_value = OrderResult(
        client_order_id="",
        broker_order_id="mock-order-1",
        status=OrderStatus.ACCEPTED,
    )

    mock_quote = MagicMock()
    mock_quote.price = 150.0

    # market_hours_check runs regardless of wall-clock time in CI; disable
    # enforcement for this test so it isn't flaky depending on when the
    # suite happens to run (irrelevant to what this test is verifying).
    with patch("broker_live_execution_mcp._get_broker", return_value=mock_broker), \
         patch("data.market_data.get_provider") as mock_get_provider, \
         patch.object(settings, "RISK_GATE_ENFORCE_MARKET_HOURS", False):
        mock_get_provider.return_value.get_latest_quote.return_value = mock_quote

        result = await confirm_live_trade(token)
        data = json.loads(result)

    assert data["status"] == "success"
    assert data["broker_order_id"] == "mock-order-1"
    mock_broker.submit_order.assert_awaited_once()


# ---------------------------------------------------------------------------
# _get_broker() -- ported from the pre-rename test suite. Confirms
# broker_live_execution_mcp.py's _get_broker() genuinely delegates to
# execution.broker_selection.resolve_broker_backend() (the single source of
# truth shared with main_orchestrator.py::_execute_broker_orders) rather than
# re-deriving the fmp_paper/live-trading safety check independently. Without
# this, this file's _get_broker() would silently construct FMPPaperBroker (a
# local paper ledger) instead of the real AlpacaBroker for
# confirm_live_trade/cancel_order during a genuinely-going-live run.
# ---------------------------------------------------------------------------

def test_get_broker_forces_alpaca_when_going_live_with_fmp_paper():
    from broker_live_execution_mcp import _get_broker
    from settings import settings as _settings

    mock_alpaca_instance = MagicMock()

    with patch.object(_settings, "BROKER_BACKEND", "fmp_paper"), \
         patch.object(_settings, "ADVISORY_ONLY", False), \
         patch.object(_settings, "ALPACA_PAPER", False), \
         patch("observability.alerts.send_alert") as mock_alert, \
         patch("diagnostics_and_visuals.telemetry.error") as mock_err, \
         patch("execution.alpaca_broker.AlpacaBroker", return_value=mock_alpaca_instance) as mock_alpaca_cls, \
         patch("execution.fmp_paper_broker.FMPPaperBroker") as mock_fmp_cls:
        broker = _get_broker()

    mock_alert.assert_called_once()
    mock_err.assert_called_once()
    mock_alpaca_cls.assert_called_once()
    mock_fmp_cls.assert_not_called()
    assert broker is mock_alpaca_instance


def test_get_broker_uses_fmp_paper_when_not_going_live():
    """Sanity check: the guard only engages when genuinely going live --
    BROKER_BACKEND='fmp_paper' in paper/advisory mode still constructs
    FMPPaperBroker exactly as before."""
    from broker_live_execution_mcp import _get_broker
    from settings import settings as _settings
    from execution.fmp_paper_broker import FMPPaperBroker

    with patch.object(_settings, "BROKER_BACKEND", "fmp_paper"), \
         patch.object(_settings, "ADVISORY_ONLY", True), \
         patch.object(_settings, "ALPACA_PAPER", True):
        broker = _get_broker()

    assert isinstance(broker, FMPPaperBroker)
