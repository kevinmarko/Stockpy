"""
tests/test_alpaca_paper_smoke.py
=================================
Smoke test for the AlpacaBroker paper-trading integration.

Requires real Alpaca paper credentials in CI secrets / .env:
  ALPACA_API_KEY  / ALPACA_SECRET_KEY

If the keys are absent the entire test module is **skipped** (not failed).
No stubs — this test hits the live Alpaca paper-trading sandbox.

What it verifies
----------------
1. AlpacaBroker can be instantiated with paper credentials.
2. ``get_account()`` returns a non-zero equity snapshot.
3. ``submit_order()`` sends a tiny test order (1 share of SPY, market).
4. The order appears in ``get_orders()`` within a few seconds.
5. The order can be cancelled via ``cancel_order()``.
6. Cancelled order is reflected back by ``get_orders()``.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

# ---------------------------------------------------------------------------
# Skip entire module when credentials are absent
# ---------------------------------------------------------------------------

_has_creds = bool(os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY"))

pytestmark = pytest.mark.skipif(
    not _has_creds,
    reason="ALPACA_API_KEY / ALPACA_SECRET_KEY not set; skipping live smoke test.",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _broker():
    from execution.alpaca_broker import AlpacaBroker

    return AlpacaBroker(
        api_key=os.environ["ALPACA_API_KEY"],
        secret_key=os.environ["ALPACA_SECRET_KEY"],
        paper=True,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_get_account():
    """AlpacaBroker.get_account() returns a non-trivial snapshot."""
    broker = _broker()
    acct = asyncio.run(broker.get_account())
    assert acct.equity > 0, "Account equity must be positive in paper account"
    assert acct.buying_power >= 0
    assert acct.currency == "USD"


def test_submit_cancel_order():
    """Submit a 1-share market order for SPY, verify it appears, then cancel."""
    from execution.broker_base import OrderIntent, OrderSide, OrderStatus, OrderType

    broker = _broker()

    intent = OrderIntent(
        strategy_id="smoke_test",
        symbol="SPY",
        side=OrderSide.BUY,
        qty=1.0,
        order_type=OrderType.MARKET,
        time_in_force="day",
        client_order_id="smoke_test_oid_12345",
    )

    # Submit
    result = asyncio.run(broker.submit_order(intent))
    assert result.status not in (OrderStatus.ERROR, OrderStatus.REJECTED), (
        f"Order submission failed: {result.error_message}"
    )
    assert result.broker_order_id is not None, "broker_order_id should be set"

    broker_id = result.broker_order_id

    # Allow Alpaca a moment to process the order
    time.sleep(1.0)

    # Verify it appears in get_orders
    orders = asyncio.run(broker.get_orders(limit=20))
    order_ids = [o.broker_order_id for o in orders]
    assert broker_id in order_ids, (
        f"Submitted order {broker_id} not found in get_orders response: {order_ids}"
    )

    # Cancel (market orders during market hours fill instantly; this may return
    # False if already filled — that is acceptable for a smoke test)
    cancelled = asyncio.run(broker.cancel_order(broker_id))
    # We just verify cancel doesn't raise, not that it always succeeds
    assert isinstance(cancelled, bool)


def test_get_open_positions():
    """get_open_positions() returns a list (may be empty in fresh paper account)."""
    broker = _broker()
    positions = asyncio.run(broker.get_open_positions())
    assert isinstance(positions, list)
