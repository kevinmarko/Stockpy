import pytest
import json
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
from execution.live_trade_proposals_store import LiveTradeProposalStore
from data.robinhood_portfolio import AccountSnapshot as RHAccountSnapshot, PortfolioPosition
from settings import settings


@pytest.fixture
def anyio_backend():
    return "asyncio"


from broker_live_execution_mcp import (
    execute_live_trade,
    confirm_live_trade,
    cancel_order,
    get_live_positions,
    _rate_limiter,
)


@pytest.fixture(autouse=True)
def reset_state(tmp_path, monkeypatch):
    """Every test gets: (1) a fresh, isolated live_trade_proposals DB (a
    tmp_path-backed SQLite file -- NOT :memory:, since execute_live_trade /
    confirm_live_trade each construct their own LiveTradeProposalStore()
    instance and :memory: would give each construction its own empty DB);
    (2) a full rate-limiter bucket; (3) LIVE_TRADE_EXECUTION_ENABLED defaulted
    True so existing/new tests don't have to opt in individually -- the
    dedicated disabled-flag test below flips it back off."""
    db_url = f"sqlite:///{tmp_path / 'live_trade_proposals.db'}"
    monkeypatch.setattr(settings, "DATABASE_URL", db_url)
    monkeypatch.setattr(settings, "LIVE_TRADE_EXECUTION_ENABLED", True)
    _rate_limiter.tokens = _rate_limiter.capacity
    yield


def _store() -> LiveTradeProposalStore:
    return LiveTradeProposalStore(db_url=settings.DATABASE_URL)


# ---------------------------------------------------------------------------
# execute_live_trade
# ---------------------------------------------------------------------------


def test_execute_live_trade_returns_token_and_creates_pending_proposal():
    result = execute_live_trade("AAPL", "buy", 10.0, "market")
    data = json.loads(result)
    assert data["status"] == "pending_confirmation"
    assert "confirmation_token" in data
    assert data["details"]["symbol"] == "AAPL"
    assert data["details"]["qty"] == 10.0
    assert "operator" in data["message"].lower() or "approve" in data["message"].lower()

    token = data["confirmation_token"]
    proposal = _store().get_by_token(token)
    assert proposal is not None
    assert proposal.status == "pending_approval"
    assert proposal.symbol == "AAPL"
    assert proposal.side == "buy"


def test_execute_live_trade_disabled_returns_honest_message_and_creates_no_proposal(monkeypatch):
    monkeypatch.setattr(settings, "LIVE_TRADE_EXECUTION_ENABLED", False)

    result = execute_live_trade("AAPL", "buy", 10.0, "market")
    data = json.loads(result)

    assert data["status"] == "error"
    assert "disabled" in data["message"].lower()
    assert "confirmation_token" not in data

    # No proposal of any kind was created.
    assert _store().get_pending(limit=10) == []


def test_execute_live_trade_never_calls_the_broker():
    """A happy-path execute_live_trade call creates a proposal and must never
    touch the broker -- it only proposes, it never executes."""
    mock_broker = AsyncMock()
    with patch("broker_live_execution_mcp._get_broker", return_value=mock_broker):
        result = execute_live_trade("AAPL", "buy", 10.0, "market")
        data = json.loads(result)

    assert data["status"] == "pending_confirmation"
    mock_broker.submit_order.assert_not_called()
    assert not mock_broker.method_calls


def test_execute_live_trade_sends_notification_best_effort():
    with patch("observability.alerts.send_alert") as mock_alert:
        result = execute_live_trade("AAPL", "buy", 10.0, "market")
    data = json.loads(result)
    assert data["status"] == "pending_confirmation"
    mock_alert.assert_called_once()
    args, _ = mock_alert.call_args
    assert args[0] == "WARNING"
    assert data["confirmation_token"] in args[1]


def test_execute_live_trade_notification_failure_does_not_block_proposal_creation():
    with patch("observability.alerts.send_alert", side_effect=RuntimeError("webhook down")):
        result = execute_live_trade("AAPL", "buy", 10.0, "market")
    data = json.loads(result)
    assert data["status"] == "pending_confirmation"
    assert _store().get_by_token(data["confirmation_token"]) is not None


# ---------------------------------------------------------------------------
# confirm_live_trade -- status-gated enforcement
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_confirm_live_trade_pending_approval_is_not_executable():
    token = _store().create_proposal(symbol="MSFT", side="buy", qty=5.0, order_type="market")

    with patch("broker_live_execution_mcp.OrderManager") as mock_om_cls:
        result = await confirm_live_trade(token)

    data = json.loads(result)
    assert data["status"] == "error"
    assert "not yet executable" in data["message"].lower()
    assert "pending_approval" in data["message"]
    mock_om_cls.assert_not_called()

    # Status is untouched by the failed confirm attempt.
    assert _store().get_by_token(token).status == "pending_approval"


@pytest.mark.anyio
async def test_confirm_live_trade_rejected_is_not_executable():
    store = _store()
    token = store.create_proposal(symbol="MSFT", side="buy", qty=5.0, order_type="market")
    store.reject_proposal(token)

    with patch("broker_live_execution_mcp.OrderManager") as mock_om_cls:
        result = await confirm_live_trade(token)

    data = json.loads(result)
    assert data["status"] == "error"
    assert "not yet executable" in data["message"].lower()
    assert "rejected" in data["message"]
    mock_om_cls.assert_not_called()


@pytest.mark.anyio
async def test_confirm_live_trade_expired_is_not_executable():
    from datetime import datetime, timedelta, timezone
    from execution.live_trade_proposals_store import LiveTradeProposal

    store = _store()
    token = store.create_proposal(symbol="MSFT", side="buy", qty=5.0, order_type="market")
    with store.Session() as session:
        row = session.query(LiveTradeProposal).filter_by(token=token).first()
        row.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        session.commit()

    with patch("broker_live_execution_mcp.OrderManager") as mock_om_cls:
        result = await confirm_live_trade(token)

    data = json.loads(result)
    assert data["status"] == "error"
    assert "not yet executable" in data["message"].lower()
    assert "expired" in data["message"]
    mock_om_cls.assert_not_called()


@pytest.mark.anyio
async def test_confirm_live_trade_unrecognized_token_error_shape():
    result = await confirm_live_trade("does-not-exist")
    data = json.loads(result)
    assert data["status"] == "error"
    assert "invalid or expired confirmation_token" in data["message"].lower()


# ---------------------------------------------------------------------------
# confirm_live_trade -- approved proposal actually executes
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_confirm_live_trade_approved_executes_and_marks_executed():
    store = _store()
    token = store.create_proposal(symbol="MSFT", side="buy", qty=5.0, order_type="market")
    store.approve_proposal(token)

    mock_om = AsyncMock()
    mock_om.submit_order_with_idempotency.return_value = OrderResult(
        client_order_id="client-123",
        status=OrderStatus.ACCEPTED,
        broker_order_id="broker-123",
    )

    with patch("broker_live_execution_mcp.OrderManager", return_value=mock_om), \
         patch("broker_live_execution_mcp._get_broker", return_value=AsyncMock()):
        result = await confirm_live_trade(token)
        data = json.loads(result)

    assert data["status"] == "success"
    assert data["broker_order_id"] == "broker-123"
    assert data["order_status"] == "accepted"

    assert mock_om.submit_order_with_idempotency.await_count == 1
    _, kwargs = mock_om.submit_order_with_idempotency.await_args
    assert kwargs.get("risk_context") is not None

    row = store.get_by_token(token)
    assert row.status == "executed"
    assert row.broker_order_id == "broker-123"


@pytest.mark.anyio
async def test_confirm_live_trade_approved_broker_error_marks_failed():
    store = _store()
    token = store.create_proposal(symbol="MSFT", side="buy", qty=5.0, order_type="market")
    store.approve_proposal(token)

    mock_om = AsyncMock()
    mock_om.submit_order_with_idempotency.return_value = OrderResult(
        client_order_id="client-123",
        broker_order_id=None,
        status=OrderStatus.ERROR,
        error_message="broker rejected the order",
    )

    with patch("broker_live_execution_mcp.OrderManager", return_value=mock_om), \
         patch("broker_live_execution_mcp._get_broker", return_value=AsyncMock()):
        result = await confirm_live_trade(token)
        data = json.loads(result)

    assert data["status"] == "error"
    assert data["message"] == "broker rejected the order"

    row = store.get_by_token(token)
    assert row.status == "failed"
    assert row.error_message == "broker rejected the order"


@pytest.mark.anyio
async def test_confirm_live_trade_duplicate_call_on_executed_proposal_is_idempotent():
    """A second confirm_live_trade call against an already-executed proposal
    must not re-submit to the broker -- its status is now 'executed', so the
    status-gate naturally routes it into the 'not yet executable' branch.
    This IS the idempotency guarantee for this new code path (distinct from
    OrderManager's own client_order_id dedup at the broker layer)."""
    store = _store()
    token = store.create_proposal(symbol="MSFT", side="buy", qty=5.0, order_type="market")
    store.approve_proposal(token)

    mock_om = AsyncMock()
    mock_om.submit_order_with_idempotency.return_value = OrderResult(
        client_order_id="client-123",
        status=OrderStatus.ACCEPTED,
        broker_order_id="broker-123",
    )

    with patch("broker_live_execution_mcp.OrderManager", return_value=mock_om), \
         patch("broker_live_execution_mcp._get_broker", return_value=AsyncMock()):
        first = json.loads(await confirm_live_trade(token))
        assert first["status"] == "success"
        assert mock_om.submit_order_with_idempotency.await_count == 1

        second = json.loads(await confirm_live_trade(token))

    assert second["status"] == "error"
    assert "not yet executable" in second["message"].lower()
    assert "executed" in second["message"]
    # The broker was never called a second time.
    assert mock_om.submit_order_with_idempotency.await_count == 1


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
    store = _store()
    token = store.create_proposal(symbol="AAPL", side="buy", qty=100.0, order_type="market")
    store.approve_proposal(token)

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

    row = store.get_by_token(token)
    assert row.status == "failed"


@pytest.mark.anyio
async def test_confirm_live_trade_passes_real_risk_gate_when_within_limits():
    """Sanity counterpart to the blocked test above -- a small, well within
    limits order still executes once a genuine RiskContext is threaded
    through (proves the fix doesn't just fail closed for everything)."""
    store = _store()
    token = store.create_proposal(symbol="AAPL", side="buy", qty=1.0, order_type="market")
    store.approve_proposal(token)

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
