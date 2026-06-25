"""
execution/alpaca_broker.py
===========================
Concrete BrokerBase implementation backed by the official alpaca-py SDK.

Features
--------
* Paper-trading (default) and live endpoints — controlled by
  ``settings.ALPACA_PAPER`` (True = paper).
* Full equity order support: market / limit, GTC / day / IOC.
* Multi-leg options spread / condor support via ``OrderIntent.legs`` and
  ``OptionLegRequest``; equity orders set ``legs=[]``.
* Real-time trade updates via ``TradingStream.subscribe_trade_updates``.
* Normalises every alpaca response into the BrokerBase dataclasses so the
  rest of the platform never imports alpaca types directly.

Credentials
-----------
Read from ``settings.ALPACA_API_KEY`` / ``settings.ALPACA_SECRET_KEY``
(sourced from environment / ``.env``).  Raises ``RuntimeError`` at
construction if either is missing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from execution.broker_base import (
    AccountSnapshot,
    BrokerBase,
    OrderIntent,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSnapshot,
    TradeUpdateEvent,
)
from settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports — alpaca-py is optional (tests that don't need a broker can
# import this module without the SDK installed, as long as they don't
# instantiate AlpacaBroker).
# ---------------------------------------------------------------------------

def _require_alpaca() -> None:
    try:
        import alpaca  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "alpaca-py is required for AlpacaBroker. "
            "Install it with: pip install alpaca-py"
        ) from exc


# ---------------------------------------------------------------------------
# Type helpers (avoid direct alpaca imports at module level)
# ---------------------------------------------------------------------------

def _to_order_side(side: OrderSide):  # -> alpaca OrderSide
    from alpaca.trading.enums import OrderSide as AS
    return AS.BUY if side == OrderSide.BUY else AS.SELL


def _to_tif(tif: str):  # -> alpaca TimeInForce
    from alpaca.trading.enums import TimeInForce
    mapping = {
        "day": TimeInForce.DAY,
        "gtc": TimeInForce.GTC,
        "ioc": TimeInForce.IOC,
        "opg": TimeInForce.OPG,
        "cls": TimeInForce.CLS,
        "fok": TimeInForce.FOK,
    }
    val = mapping.get(tif.lower())
    if val is None:
        logger.warning("Unknown TimeInForce '%s'; defaulting to DAY", tif)
        from alpaca.trading.enums import TimeInForce
        val = TimeInForce.DAY
    return val


def _parse_order(order) -> OrderResult:
    """Normalise an alpaca Order object into an OrderResult."""
    from alpaca.trading.enums import OrderStatus as AOS

    _status_map = {
        AOS.NEW: OrderStatus.ACCEPTED,
        AOS.ACCEPTED: OrderStatus.ACCEPTED,
        AOS.PENDING_NEW: OrderStatus.PENDING,
        AOS.ACCEPTED_FOR_BIDDING: OrderStatus.PENDING,
        AOS.PARTIALLY_FILLED: OrderStatus.PARTIALLY_FILLED,
        AOS.FILLED: OrderStatus.FILLED,
        AOS.CANCELED: OrderStatus.CANCELED,
        AOS.EXPIRED: OrderStatus.CANCELED,
        AOS.REPLACED: OrderStatus.CANCELED,
        AOS.REJECTED: OrderStatus.REJECTED,
        AOS.PENDING_CANCEL: OrderStatus.PENDING,
        AOS.PENDING_REPLACE: OrderStatus.PENDING,
    }

    status = _status_map.get(order.status, OrderStatus.ERROR)
    filled_qty = float(order.filled_qty or 0.0)
    filled_avg = float(order.filled_avg_price) if order.filled_avg_price else None
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None

    if order.submitted_at:
        submitted_at = order.submitted_at.replace(tzinfo=None)
    if order.filled_at:
        filled_at = order.filled_at.replace(tzinfo=None)

    return OrderResult(
        client_order_id=str(order.client_order_id or ""),
        broker_order_id=str(order.id),
        status=status,
        filled_qty=filled_qty,
        filled_avg_price=filled_avg,
        submitted_at=submitted_at,
        filled_at=filled_at,
    )


class AlpacaBroker(BrokerBase):
    """
    Alpaca paper/live broker adapter.

    Parameters
    ----------
    paper : bool | None
        Override ``settings.ALPACA_PAPER`` for testing.
    api_key : str | None
        Override ``settings.ALPACA_API_KEY`` for testing.
    secret_key : str | None
        Override ``settings.ALPACA_SECRET_KEY`` for testing.
    """

    def __init__(
        self,
        *,
        paper: Optional[bool] = None,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
    ) -> None:
        _require_alpaca()
        from alpaca.trading.client import TradingClient

        self._paper = paper if paper is not None else settings.ALPACA_PAPER
        self._api_key = api_key or settings.ALPACA_API_KEY
        self._secret_key = secret_key or settings.ALPACA_SECRET_KEY

        if not self._api_key or not self._secret_key:
            raise RuntimeError(
                "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in settings / .env. "
                "Obtain paper-trading credentials at https://alpaca.markets/"
            )

        # TradingClient is synchronous; alpaca-py's async wrapper uses the same
        # credentials and is constructed in stream_trade_updates to avoid
        # holding a connection open unnecessarily.
        self._client = TradingClient(
            api_key=self._api_key,
            secret_key=self._secret_key,
            paper=self._paper,
        )
        mode = "PAPER" if self._paper else "LIVE"
        logger.info("AlpacaBroker initialised in %s mode", mode)

    # ------------------------------------------------------------------
    # submit_order
    # ------------------------------------------------------------------

    async def submit_order(self, intent: OrderIntent) -> OrderResult:
        """
        Build and submit an order to Alpaca.

        Dry-run path: logs the intent and returns an OrderResult with
        status=ACCEPTED and broker_order_id=None so the rest of the
        pipeline can treat it like a real submission.

        Multi-leg options: ``intent.legs`` is converted to a list of
        ``OptionLegRequest`` objects and attached to a ``MarketOrderRequest``
        with ``order_class="mleg"``.
        """
        if intent.dry_run:
            logger.info(
                "[DRY-RUN] Would submit %s %s x %.4f @ %s (strategy=%s, coid=%s)",
                intent.side.value.upper(),
                intent.symbol,
                intent.qty,
                intent.limit_price or "MARKET",
                intent.strategy_id,
                intent.client_order_id,
            )
            return OrderResult(
                client_order_id=intent.client_order_id or "",
                broker_order_id=None,
                status=OrderStatus.ACCEPTED,
                submitted_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )

        from alpaca.trading.enums import OrderClass
        from alpaca.trading.requests import (
            LimitOrderRequest,
            MarketOrderRequest,
            OptionLegRequest,
        )

        client_oid = intent.client_order_id or ""

        try:
            if intent.legs:
                # Multi-leg options order (spread / condor)
                legs = [
                    OptionLegRequest(
                        symbol=lg["symbol"],
                        ratio_qty=float(lg["ratio_qty"]),
                        side=_to_order_side(OrderSide(lg["side"])),
                    )
                    for lg in intent.legs
                ]
                req = MarketOrderRequest(
                    symbol=intent.symbol,
                    qty=intent.qty,
                    side=_to_order_side(intent.side),
                    time_in_force=_to_tif(intent.time_in_force),
                    order_class=OrderClass.MLEG,
                    client_order_id=client_oid,
                    legs=legs,
                )
            elif intent.order_type == OrderType.LIMIT and intent.limit_price is not None:
                req = LimitOrderRequest(
                    symbol=intent.symbol,
                    qty=intent.qty,
                    side=_to_order_side(intent.side),
                    time_in_force=_to_tif(intent.time_in_force),
                    limit_price=intent.limit_price,
                    client_order_id=client_oid,
                )
            else:
                req = MarketOrderRequest(
                    symbol=intent.symbol,
                    qty=intent.qty,
                    side=_to_order_side(intent.side),
                    time_in_force=_to_tif(intent.time_in_force),
                    client_order_id=client_oid,
                )

            order = self._client.submit_order(req)
            result = _parse_order(order)
            logger.info(
                "Submitted order: %s %s x %.4f -> broker_id=%s status=%s",
                intent.side.value,
                intent.symbol,
                intent.qty,
                result.broker_order_id,
                result.status.value,
            )
            return result

        except Exception as exc:
            logger.error(
                "submit_order failed for %s %s x %.4f (coid=%s): %s",
                intent.side.value,
                intent.symbol,
                intent.qty,
                client_oid,
                exc,
                exc_info=True,
            )
            return OrderResult(
                client_order_id=client_oid,
                broker_order_id=None,
                status=OrderStatus.ERROR,
                error_message=str(exc),
                submitted_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )

    # ------------------------------------------------------------------
    # cancel_order
    # ------------------------------------------------------------------

    async def cancel_order(self, broker_order_id: str) -> bool:
        try:
            self._client.cancel_order_by_id(broker_order_id)
            logger.info("Cancelled order %s", broker_order_id)
            return True
        except Exception as exc:
            logger.warning("cancel_order(%s) failed: %s", broker_order_id, exc)
            return False

    # ------------------------------------------------------------------
    # get_open_positions
    # ------------------------------------------------------------------

    async def get_open_positions(self) -> list[PositionSnapshot]:
        try:
            positions = self._client.get_all_positions()
            return [
                PositionSnapshot(
                    symbol=p.symbol,
                    qty=float(p.qty),
                    avg_entry_price=float(p.avg_entry_price),
                    market_value=float(p.market_value),
                    unrealized_pl=float(p.unrealized_pl),
                )
                for p in positions
            ]
        except Exception as exc:
            logger.error("get_open_positions failed: %s", exc, exc_info=True)
            raise

    # ------------------------------------------------------------------
    # get_account
    # ------------------------------------------------------------------

    async def get_account(self) -> AccountSnapshot:
        try:
            acct = self._client.get_account()
            return AccountSnapshot(
                equity=float(acct.equity),
                cash=float(acct.cash),
                buying_power=float(acct.buying_power),
                currency=str(acct.currency or "USD"),
            )
        except Exception as exc:
            logger.error("get_account failed: %s", exc, exc_info=True)
            raise

    # ------------------------------------------------------------------
    # get_orders
    # ------------------------------------------------------------------

    async def get_orders(
        self,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[OrderResult]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        qstatus = None
        if status:
            try:
                qstatus = QueryOrderStatus(status)
            except ValueError:
                logger.warning("Unknown order status filter '%s'; fetching all", status)

        req = GetOrdersRequest(status=qstatus, limit=limit)
        try:
            orders = self._client.get_orders(req)
            return [_parse_order(o) for o in orders]
        except Exception as exc:
            logger.error("get_orders failed: %s", exc, exc_info=True)
            raise

    # ------------------------------------------------------------------
    # stream_trade_updates  (async generator)
    # ------------------------------------------------------------------

    async def stream_trade_updates(self) -> AsyncIterator[TradeUpdateEvent]:
        """
        Subscribe to Alpaca's real-time trade update WebSocket stream.

        Each fill / cancel / rejection emitted by Alpaca is converted to a
        ``TradeUpdateEvent`` and yielded.  The generator reconnects
        automatically on transient errors (alpaca-py handles this internally).
        """
        from alpaca.trading.enums import OrderSide as AOS
        from alpaca.trading.stream import TradingStream

        stream = TradingStream(
            api_key=self._api_key,
            secret_key=self._secret_key,
            paper=self._paper,
        )

        events: list[TradeUpdateEvent] = []

        async def _handler(data) -> None:
            try:
                order = data.order
                side = (
                    OrderSide.BUY
                    if order.side == AOS.BUY
                    else OrderSide.SELL
                )
                ts = getattr(data, "timestamp", None) or datetime.now(timezone.utc)
                if hasattr(ts, "replace"):
                    ts = ts.replace(tzinfo=None)
                evt = TradeUpdateEvent(
                    event_type=str(data.event),
                    broker_order_id=str(order.id),
                    client_order_id=str(order.client_order_id or ""),
                    symbol=str(order.symbol),
                    side=side,
                    filled_qty=float(order.filled_qty or 0.0),
                    filled_avg_price=(
                        float(order.filled_avg_price)
                        if order.filled_avg_price
                        else None
                    ),
                    timestamp=ts,
                )
                events.append(evt)
            except Exception as exc:
                logger.error("stream handler error: %s", exc, exc_info=True)

        stream.subscribe_trade_updates(_handler)
        logger.info("AlpacaBroker: starting trade-update stream")

        import asyncio
        loop = asyncio.get_event_loop()
        stream_task = loop.run_in_executor(None, stream.run)

        try:
            while True:
                await asyncio.sleep(0.1)
                while events:
                    yield events.pop(0)
        except asyncio.CancelledError:
            logger.info("AlpacaBroker: trade-update stream cancelled")
            stream.stop()
            await stream_task
            return
