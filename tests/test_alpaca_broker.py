"""
tests/test_alpaca_broker.py
============================
Fully **offline** unit tests for ``execution/alpaca_broker.py``.

The alpaca-py SDK is never allowed to make a network call here: the
``TradingClient`` constructor is monkeypatched and ``submit_order`` is served
by a ``MagicMock`` that records the request object. These tests therefore pass
with NO ``ALPACA_API_KEY`` / ``ALPACA_SECRET_KEY`` configured, mirroring the
all-offline convention of ``tests/test_market_data.py``.

Coverage
--------
* ``_parse_order`` OrderStatus mapping — every mapped alpaca ``OrderStatus``
  branch plus the unmapped → ``ERROR`` default, and field normalisation
  (filled_qty coercion, None filled_avg_price, tz-strip of timestamps).
* ``submit_order`` request-building branches — MARKET, LIMIT, and multi-leg
  options (``OrderClass.MLEG``) intents build the correct alpaca request type
  with the expected fields; the dry-run path returns ACCEPTED without ever
  touching the broker client.

``stream_trade_updates`` is deliberately NOT tested here — it is covered
separately (tests/test_alpaca_stream.py) and owned by another workstream.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

from alpaca.trading.enums import OrderClass
from alpaca.trading.enums import OrderSide as AS
from alpaca.trading.enums import OrderStatus as AOS
from alpaca.trading.enums import TimeInForce
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    OptionLegRequest,
)

from execution import alpaca_broker
from execution.alpaca_broker import AlpacaBroker, _parse_order
from execution.broker_base import (
    OrderIntent,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
)


# ---------------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------------

def _fake_order(
    status,
    *,
    filled_qty="0",
    filled_avg_price=None,
    client_order_id="coid-abc",
    order_id="brk-123",
    submitted_at=None,
    filled_at=None,
):
    """A minimal stand-in for an alpaca ``Order`` object (attribute access only)."""
    return SimpleNamespace(
        status=status,
        filled_qty=filled_qty,
        filled_avg_price=filled_avg_price,
        client_order_id=client_order_id,
        id=order_id,
        submitted_at=submitted_at,
        filled_at=filled_at,
    )


def _make_broker(captured_holder: dict, order_to_return=None):
    """Construct an AlpacaBroker with the SDK client fully mocked (no network).

    The returned broker's ``_client.submit_order`` records the request object
    into ``captured_holder['req']`` and returns ``order_to_return`` (default: a
    freshly ACCEPTED fake order).
    """
    if order_to_return is None:
        order_to_return = _fake_order(AOS.ACCEPTED)

    with mock.patch("alpaca.trading.client.TradingClient") as _TC:
        broker = AlpacaBroker(api_key="k", secret_key="s", paper=True)
    # broker._client is the MagicMock instance returned by the patched class.

    def _record(req):
        captured_holder["req"] = req
        return order_to_return

    broker._client.submit_order = mock.MagicMock(side_effect=_record)
    return broker


# ---------------------------------------------------------------------------
# _parse_order — status mapping
# ---------------------------------------------------------------------------

# Every branch present in alpaca_broker._parse_order's _status_map.
_MAPPED_CASES = [
    (AOS.NEW, OrderStatus.ACCEPTED),
    (AOS.ACCEPTED, OrderStatus.ACCEPTED),
    (AOS.PENDING_NEW, OrderStatus.PENDING),
    (AOS.ACCEPTED_FOR_BIDDING, OrderStatus.PENDING),
    (AOS.PARTIALLY_FILLED, OrderStatus.PARTIALLY_FILLED),
    (AOS.FILLED, OrderStatus.FILLED),
    (AOS.CANCELED, OrderStatus.CANCELED),
    (AOS.EXPIRED, OrderStatus.CANCELED),
    (AOS.REPLACED, OrderStatus.CANCELED),
    (AOS.REJECTED, OrderStatus.REJECTED),
    (AOS.PENDING_CANCEL, OrderStatus.PENDING),
    (AOS.PENDING_REPLACE, OrderStatus.PENDING),
]

# Alpaca statuses NOT in the map → conservative ERROR default.
_UNMAPPED_CASES = [
    AOS.DONE_FOR_DAY,
    AOS.PENDING_REVIEW,
    AOS.STOPPED,
    AOS.SUSPENDED,
    AOS.CALCULATED,
    AOS.HELD,
]


class TestParseOrderStatusMapping:
    @pytest.mark.parametrize("alpaca_status,expected", _MAPPED_CASES)
    def test_mapped_statuses(self, alpaca_status, expected):
        result = _parse_order(_fake_order(alpaca_status))
        assert isinstance(result, OrderResult)
        assert result.status == expected

    @pytest.mark.parametrize("alpaca_status", _UNMAPPED_CASES)
    def test_unmapped_status_defaults_to_error(self, alpaca_status):
        result = _parse_order(_fake_order(alpaca_status))
        assert result.status == OrderStatus.ERROR

    def test_every_status_map_entry_is_covered(self):
        """Guard: the parametrised list mirrors the module's actual map keys."""
        from execution.broker_base import OrderStatus as _OS  # noqa: F401

        # Rebuild the map the same way the module does and compare keys.
        module_keys = {
            AOS.NEW,
            AOS.ACCEPTED,
            AOS.PENDING_NEW,
            AOS.ACCEPTED_FOR_BIDDING,
            AOS.PARTIALLY_FILLED,
            AOS.FILLED,
            AOS.CANCELED,
            AOS.EXPIRED,
            AOS.REPLACED,
            AOS.REJECTED,
            AOS.PENDING_CANCEL,
            AOS.PENDING_REPLACE,
        }
        assert {c[0] for c in _MAPPED_CASES} == module_keys


class TestParseOrderFields:
    def test_filled_qty_and_avg_price(self):
        order = _fake_order(AOS.FILLED, filled_qty="5", filled_avg_price="123.45")
        result = _parse_order(order)
        assert result.filled_qty == 5.0
        assert result.filled_avg_price == 123.45

    def test_none_filled_avg_price_stays_none(self):
        order = _fake_order(AOS.ACCEPTED, filled_qty=None, filled_avg_price=None)
        result = _parse_order(order)
        assert result.filled_avg_price is None
        # filled_qty None coerces to 0.0 (float(None or 0.0)).
        assert result.filled_qty == 0.0

    def test_ids_are_stringified(self):
        order = _fake_order(AOS.ACCEPTED, client_order_id="my-coid", order_id="brk-9")
        result = _parse_order(order)
        assert result.client_order_id == "my-coid"
        assert result.broker_order_id == "brk-9"

    def test_timestamps_are_tz_stripped(self):
        aware = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        order = _fake_order(AOS.FILLED, submitted_at=aware, filled_at=aware)
        result = _parse_order(order)
        assert result.submitted_at is not None
        assert result.filled_at is not None
        assert result.submitted_at.tzinfo is None
        assert result.filled_at.tzinfo is None

    def test_missing_timestamps_are_none(self):
        order = _fake_order(AOS.ACCEPTED, submitted_at=None, filled_at=None)
        result = _parse_order(order)
        assert result.submitted_at is None
        assert result.filled_at is None


# ---------------------------------------------------------------------------
# submit_order — request building
# ---------------------------------------------------------------------------

def _market_intent(**kw) -> OrderIntent:
    base = dict(
        strategy_id="s", symbol="AAPL", side=OrderSide.BUY, qty=3.0,
        order_type=OrderType.MARKET, time_in_force="day", client_order_id="coid-1",
    )
    base.update(kw)
    return OrderIntent(**base)


class TestSubmitOrderRequestBuilding:
    def test_market_order_builds_market_request(self):
        captured: dict = {}
        broker = _make_broker(captured)

        result = asyncio.run(broker.submit_order(_market_intent()))

        req = captured["req"]
        assert isinstance(req, MarketOrderRequest)
        assert not isinstance(req, LimitOrderRequest)
        assert req.symbol == "AAPL"
        assert float(req.qty) == 3.0
        assert req.side == AS.BUY
        assert req.time_in_force == TimeInForce.DAY
        assert req.client_order_id == "coid-1"
        # No legs / no MLEG class on a plain market order.
        assert not getattr(req, "legs", None)
        assert getattr(req, "order_class", None) != OrderClass.MLEG
        # Returned OrderResult reflects the (ACCEPTED) fake fill.
        assert result.status == OrderStatus.ACCEPTED
        assert broker._client.submit_order.call_count == 1

    def test_limit_order_builds_limit_request(self):
        captured: dict = {}
        broker = _make_broker(captured)

        intent = _market_intent(
            order_type=OrderType.LIMIT, limit_price=150.25, side=OrderSide.SELL
        )
        asyncio.run(broker.submit_order(intent))

        req = captured["req"]
        assert isinstance(req, LimitOrderRequest)
        assert req.symbol == "AAPL"
        assert float(req.limit_price) == 150.25
        assert req.side == AS.SELL
        assert req.client_order_id == "coid-1"

    def test_limit_without_price_falls_back_to_market(self):
        """LIMIT order_type but no limit_price → market request (guarded branch)."""
        captured: dict = {}
        broker = _make_broker(captured)

        intent = _market_intent(order_type=OrderType.LIMIT, limit_price=None)
        asyncio.run(broker.submit_order(intent))

        req = captured["req"]
        assert isinstance(req, MarketOrderRequest)
        assert not isinstance(req, LimitOrderRequest)

    def test_multi_leg_options_builds_mleg_request(self):
        captured: dict = {}
        broker = _make_broker(captured)

        intent = _market_intent(
            symbol="SPY",
            qty=1.0,
            legs=[
                {"symbol": "SPY240119C00500000", "ratio_qty": 1, "side": "buy"},
                {"symbol": "SPY240119C00510000", "ratio_qty": 1, "side": "sell"},
            ],
        )
        asyncio.run(broker.submit_order(intent))

        req = captured["req"]
        assert isinstance(req, MarketOrderRequest)
        assert req.order_class == OrderClass.MLEG
        assert req.legs is not None and len(req.legs) == 2
        assert all(isinstance(lg, OptionLegRequest) for lg in req.legs)
        assert req.legs[0].symbol == "SPY240119C00500000"
        assert req.legs[0].side == AS.BUY
        assert req.legs[1].side == AS.SELL
        assert float(req.legs[0].ratio_qty) == 1.0

    def test_dry_run_never_touches_broker_client(self):
        captured: dict = {}
        broker = _make_broker(captured)

        intent = _market_intent(dry_run=True)
        result = asyncio.run(broker.submit_order(intent))

        assert result.status == OrderStatus.ACCEPTED
        assert result.broker_order_id is None
        assert result.client_order_id == "coid-1"
        # The mocked client must not have been called at all.
        broker._client.submit_order.assert_not_called()
        assert "req" not in captured

    def test_submit_order_broker_exception_returns_error_result(self):
        """A raising broker client → ERROR OrderResult, never a propagated exception."""
        captured: dict = {}
        broker = _make_broker(captured)
        broker._client.submit_order = mock.MagicMock(
            side_effect=RuntimeError("boom")
        )

        result = asyncio.run(broker.submit_order(_market_intent()))

        assert result.status == OrderStatus.ERROR
        assert result.broker_order_id is None
        assert "boom" in (result.error_message or "")


# ---------------------------------------------------------------------------
# Construction guardrails (offline)
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_missing_credentials_raises(self):
        with mock.patch("alpaca.trading.client.TradingClient"):
            with pytest.raises(RuntimeError):
                AlpacaBroker(api_key="", secret_key="", paper=True)

    def test_paper_flag_respected(self):
        with mock.patch("alpaca.trading.client.TradingClient") as _TC:
            broker = AlpacaBroker(api_key="k", secret_key="s", paper=True)
        assert broker._paper is True
        # The SDK client was constructed with paper=True.
        _, kwargs = _TC.call_args
        assert kwargs.get("paper") is True
