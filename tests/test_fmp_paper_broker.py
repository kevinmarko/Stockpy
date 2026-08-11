"""tests/test_fmp_paper_broker.py

Sync test functions driving the broker's async methods via asyncio.run() --
matches this repo's established pattern (tests/test_alpaca_broker.py); this
codebase has no pytest-asyncio/anyio plugin registered, and pytest.ini's
--strict-markers makes an unregistered @pytest.mark.anyio fail collection
outright rather than silently no-op.
"""
import asyncio
from unittest.mock import patch

import pytest

from execution.fmp_paper_broker import FMPPaperBroker
from execution.broker_base import OrderIntent, OrderSide, OrderType, OrderStatus
from execution.order_manager import OrderManager
from execution.kill_switch import GlobalKillSwitch


def _intent(**overrides):
    defaults = {
        "strategy_id": "test_strat",
        "symbol": "AAPL",
        "side": OrderSide.BUY,
        "qty": 10.0,
        "order_type": OrderType.MARKET,
        "client_order_id": "test_order_1",
    }
    defaults.update(overrides)
    return OrderIntent(**defaults)


def test_submit_order_success():
    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    with patch("data.fmp_client.quote", return_value=[{"symbol": "AAPL", "price": 150.0, "marketCap": 2e12}]):
        result = asyncio.run(broker.submit_order(_intent()))

    assert result.status == OrderStatus.FILLED
    assert result.filled_qty == 10.0
    assert result.filled_avg_price == 150.0

    event = asyncio.run(broker.stream_queue.get())
    assert event.event_type == "fill"
    assert event.client_order_id == "test_order_1"
    assert event.filled_qty == 10.0


def test_submit_order_fmp_empty_quote_is_an_error_not_a_crash():
    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    with patch("data.fmp_client.quote", return_value=[]):
        result = asyncio.run(broker.submit_order(_intent(client_order_id="test_order_2")))

    assert result.status == OrderStatus.ERROR
    assert "not found" in result.error_message.lower()


def test_submit_order_fmp_exception_is_an_error_not_a_crash():
    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    with patch("data.fmp_client.quote", side_effect=RuntimeError("network down")):
        result = asyncio.run(broker.submit_order(_intent(client_order_id="test_order_2b")))

    assert result.status == OrderStatus.ERROR
    assert "network down" in result.error_message


def test_submit_order_invalid_price_is_rejected():
    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    with patch("data.fmp_client.quote", return_value=[{"symbol": "AAPL", "price": 0.0}]):
        result = asyncio.run(broker.submit_order(_intent(client_order_id="test_order_2c")))

    assert result.status == OrderStatus.ERROR
    assert "invalid price" in result.error_message.lower()


def test_submit_order_dry_run_never_touches_quote_or_store():
    """dry_run=True must short-circuit before any quote fetch or store write
    -- mirrors AlpacaBroker.submit_order's exact pattern (Finding 10)."""
    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    intent = _intent(client_order_id="dry_run_order")
    intent.dry_run = True
    with patch("data.fmp_client.quote") as mock_quote:
        result = asyncio.run(broker.submit_order(intent))
        mock_quote.assert_not_called()

    assert result.status == OrderStatus.ACCEPTED
    assert result.broker_order_id is None
    # Store must be untouched -- no order recorded, no fill applied.
    assert broker.store.get_orders() == []
    assert broker.store.get_open_positions() == []


def test_submit_order_missing_market_cap_uses_none_not_zero():
    """A quote payload missing marketCap must feed TieredCostModel a real
    None, not a fabricated 0.0 -- a fabricated 0.0 routes to the smallest
    ('illiquid', 20.0 bps) liquidity tier instead of the unknown-market-cap
    ('large_cap', 1.0 bps) fallback, a ~20x cost misclassification
    (Finding 21)."""
    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    captured = {}
    real_calc = broker.cost_model.calculate_cost

    def spy_calc(*args, **kwargs):
        captured["market_cap"] = kwargs.get("market_cap")
        return real_calc(*args, **kwargs)

    with patch("data.fmp_client.quote", return_value=[{"symbol": "AAPL", "price": 150.0}]), \
         patch.object(broker.cost_model, "calculate_cost", side_effect=spy_calc):
        result = asyncio.run(broker.submit_order(_intent(client_order_id="no_mcap")))

    assert result.status == OrderStatus.FILLED
    assert captured["market_cap"] is None
    assert broker.cost_model.get_liquidity_tier(captured["market_cap"]) == "large_cap"


def test_submit_order_rejects_multi_leg_options_orders():
    # A single-symbol FMP quote cannot honestly price a spread/condor --
    # V1 rejects rather than silently mis-filling it.
    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    intent = _intent(legs=[{"symbol": "AAPL240119C00150000", "ratio_qty": 1.0, "side": OrderSide.BUY}])
    with patch("data.fmp_client.quote") as mock_quote:
        result = asyncio.run(broker.submit_order(intent))
        mock_quote.assert_not_called()

    assert result.status == OrderStatus.REJECTED
    assert "multi-leg" in result.error_message.lower()


def test_submit_order_rejects_non_positive_quantity():
    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    with patch("data.fmp_client.quote") as mock_quote:
        result = asyncio.run(broker.submit_order(_intent(qty=0.0)))
        mock_quote.assert_not_called()

    assert result.status == OrderStatus.REJECTED
    assert "invalid order quantity" in result.error_message.lower()


def test_submit_order_marketable_limit_buy_fills_at_quote():
    # BUY limit at $160, quote is $150 -- already marketable (better than
    # asked), fills for real like a real broker's price improvement.
    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    intent = _intent(order_type=OrderType.LIMIT, limit_price=160.0)
    with patch("data.fmp_client.quote", return_value=[{"symbol": "AAPL", "price": 150.0}]):
        result = asyncio.run(broker.submit_order(intent))

    assert result.status == OrderStatus.FILLED
    assert result.filled_avg_price == 150.0


def test_submit_order_unmarketable_limit_buy_is_rejected_not_mis_filled():
    # BUY limit at $100, quote is $150 -- not marketable. V1 has no
    # resting-order book, so this must be an honest rejection, never a
    # fill at a price the order never asked for.
    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    intent = _intent(order_type=OrderType.LIMIT, limit_price=100.0)
    with patch("data.fmp_client.quote", return_value=[{"symbol": "AAPL", "price": 150.0}]):
        result = asyncio.run(broker.submit_order(intent))

    assert result.status == OrderStatus.REJECTED
    assert "not marketable" in result.error_message.lower()


def test_submit_order_unmarketable_limit_sell_is_rejected():
    # SELL limit at $160, quote is $150 -- asking for more than the market
    # will pay right now, not marketable.
    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    intent = _intent(side=OrderSide.SELL, order_type=OrderType.LIMIT, limit_price=160.0)
    with patch("data.fmp_client.quote", return_value=[{"symbol": "AAPL", "price": 150.0}]):
        result = asyncio.run(broker.submit_order(intent))

    assert result.status == OrderStatus.REJECTED


def test_submit_order_limit_missing_limit_price_is_rejected():
    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    intent = _intent(order_type=OrderType.LIMIT, limit_price=None)
    with patch("data.fmp_client.quote", return_value=[{"symbol": "AAPL", "price": 150.0}]):
        result = asyncio.run(broker.submit_order(intent))

    assert result.status == OrderStatus.REJECTED
    assert "limit_price" in result.error_message.lower()


def test_submit_order_insufficient_funds():
    # A tiny starting cash balance guarantees apply_fill's real insufficient-
    # funds path fires -- not a mocked store, the actual PaperAccountStore.
    from db_config import session_scope
    from data.paper_account_store import PaperAccount

    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    with session_scope(broker.store.Session) as session:
        acc = session.query(PaperAccount).filter_by(id=1).first()
        acc.cash_balance = 10.0

    with patch("data.fmp_client.quote", return_value=[{"symbol": "AAPL", "price": 150.0}]):
        result = asyncio.run(broker.submit_order(_intent(client_order_id="test_order_3")))

    assert result.status == OrderStatus.REJECTED
    assert "insufficient" in result.error_message.lower()


def test_submit_order_readonly_mode_never_writes(tmp_path):
    # create_readonly_db_engine deliberately raises for :memory: (an
    # unenforceable read-only guarantee on an empty, private db) -- use a
    # real (missing) file, matching test_paper_account_store.py's
    # readonly_store fixture.
    broker = FMPPaperBroker(db_url=f"sqlite:///{tmp_path / 'missing.db'}", readonly=True)
    with patch("data.fmp_client.quote", return_value=[{"symbol": "AAPL", "price": 150.0}]):
        result = asyncio.run(broker.submit_order(_intent()))

    assert result.status == OrderStatus.ERROR
    assert "readonly" in result.error_message.lower()


def test_cancel_order_returns_false_orders_fill_immediately():
    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    assert asyncio.run(broker.cancel_order("any_id")) is False


def test_get_account_and_positions_reflect_a_real_fill(tmp_path):
    # :memory: is one private db PER CONNECTION -- get_open_positions/
    # get_account run through asyncio.to_thread (a different real OS
    # thread), which would silently see an empty, table-less database. A
    # real file matches actual production usage and avoids that footgun.
    broker = FMPPaperBroker(db_url=f"sqlite:///{tmp_path / 'paper.db'}")
    with patch("data.fmp_client.quote", return_value=[{"symbol": "AAPL", "price": 150.0}]):
        asyncio.run(broker.submit_order(_intent()))

    with patch("data.fmp_client.batch_quote", return_value=[{"symbol": "AAPL", "price": 150.0}]):
        positions = asyncio.run(broker.get_open_positions())
        account = asyncio.run(broker.get_account())

    assert len(positions) == 1
    assert positions[0].symbol == "AAPL"
    assert positions[0].qty == 10.0
    assert account.cash < 100000.0  # starting cash minus the fill cost


def test_get_orders_returns_recorded_fill(tmp_path):
    broker = FMPPaperBroker(db_url=f"sqlite:///{tmp_path / 'paper.db'}")
    with patch("data.fmp_client.quote", return_value=[{"symbol": "AAPL", "price": 150.0}]):
        asyncio.run(broker.submit_order(_intent()))

    orders = asyncio.run(broker.get_orders())
    assert len(orders) == 1
    assert orders[0].status == OrderStatus.FILLED
    assert orders[0].client_order_id == "test_order_1"


# ---------------------------------------------------------------------------
# Integration: dropping into OrderManager gets kill-switch/dry-run gating
# for free, with no separate wiring -- confirms the "drop-in broker" design
# goal actually holds for FMPPaperBroker, not just AlpacaBroker.
# ---------------------------------------------------------------------------


def test_order_manager_dry_run_never_touches_the_broker(tmp_path):
    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    kill_switch = GlobalKillSwitch(sentinel_file=tmp_path / "KILL_SWITCH")
    manager = OrderManager(broker, dry_run=True, kill_switch=kill_switch)

    with patch("data.fmp_client.quote") as mock_quote:
        result = asyncio.run(
            manager.submit_order_with_idempotency(_intent(client_order_id=None))
        )
        mock_quote.assert_not_called()

    assert result.status == OrderStatus.ACCEPTED
    assert result.broker_order_id is None


def test_order_manager_kill_switch_blocks_submission(tmp_path):
    from execution.kill_switch import KillSwitchActiveError

    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    kill_switch = GlobalKillSwitch(sentinel_file=tmp_path / "KILL_SWITCH")
    kill_switch.activate("test halt")
    manager = OrderManager(broker, kill_switch=kill_switch)

    with patch("data.fmp_client.quote") as mock_quote:
        with pytest.raises(KillSwitchActiveError):
            asyncio.run(manager.submit_order_with_idempotency(_intent(client_order_id=None)))
        mock_quote.assert_not_called()


def test_order_manager_live_submission_reaches_the_paper_broker(tmp_path):
    broker = FMPPaperBroker(db_url="sqlite:///:memory:")
    kill_switch = GlobalKillSwitch(sentinel_file=tmp_path / "KILL_SWITCH")
    manager = OrderManager(broker, dry_run=False, kill_switch=kill_switch)

    with patch("data.fmp_client.quote", return_value=[{"symbol": "AAPL", "price": 150.0}]):
        result = asyncio.run(
            manager.submit_order_with_idempotency(_intent(client_order_id=None))
        )

    assert result.status == OrderStatus.FILLED
    assert result.filled_avg_price == 150.0
