"""
InvestYo Quant Platform - FMP Paper Broker
==========================================
Paper trading backend using Financial Modeling Prep (FMP) quotes and a local
SQLite store for cash/position tracking. Uses `TieredCostModel` to enforce
realistic commissions, bid-ask spread, slippage, and regulatory fees.

V1 scope (matches main_orchestrator.py's only production caller, which
submits equity market orders exclusively):
  - Market orders: instant fill-or-fail at the current FMP quote.
  - Limit orders: instant fill-or-REJECT -- filled at the quote price only
    if it's already marketable against the limit (immediately executable);
    otherwise rejected. There is no resting-order book, so an unmarketable
    limit order can never later fill on its own -- rejecting it is the
    honest outcome, not silently filling at a price the order didn't ask
    for and not silently accepting an order that will sit forever.
  - Multi-leg options orders (OrderIntent.legs non-empty): rejected. A
    single-symbol FMP quote cannot honestly price a spread/condor; faking
    one from an equity quote would be fabricated data (CONSTRAINT #4).
"""

import asyncio
import logging
from typing import AsyncIterator, Optional, List
from datetime import datetime, timezone

from execution.broker_base import (
    BrokerBase,
    OrderIntent,
    OrderResult,
    OrderStatus,
    OrderSide,
    OrderType,
    AccountSnapshot,
    PositionSnapshot,
    TradeUpdateEvent
)
from execution.cost_model import TieredCostModel
from data.paper_account_store import PaperAccountStore
from data import fmp_client

logger = logging.getLogger("FMPPaperBroker")

class FMPPaperBroker(BrokerBase):
    def __init__(self, db_url: Optional[str] = None, *, readonly: bool = False):
        self.store = PaperAccountStore(db_url, readonly=readonly)
        self.cost_model = TieredCostModel()
        self.stream_queue: asyncio.Queue[TradeUpdateEvent] = asyncio.Queue()
        self._readonly = readonly

    async def submit_order(self, intent: OrderIntent) -> OrderResult:
        """Submit an order for execution."""
        if self._readonly:
            return OrderResult(
                client_order_id=intent.client_order_id or "unknown",
                broker_order_id=None,
                status=OrderStatus.ERROR,
                error_message="Cannot submit orders in readonly mode."
            )

        client_order_id = intent.client_order_id or "unknown"

        # 1. Dry-run interception -- matches AlpacaBroker.submit_order's exact
        # pattern (execution/alpaca_broker.py). Placed before any quote fetch
        # or store write so a dry_run=True intent never touches fmp_client or
        # PaperAccountStore, and OrderManager tests exercising both broker
        # backends see identical dry-run behavior.
        if intent.dry_run:
            logger.info(
                "[DRY-RUN] Would submit %s %s x %.4f @ %s (strategy=%s, coid=%s)",
                intent.side.value.upper(),
                intent.symbol,
                intent.qty,
                intent.limit_price or "MARKET",
                intent.strategy_id,
                client_order_id,
            )
            return OrderResult(
                client_order_id=client_order_id,
                broker_order_id=None,
                status=OrderStatus.ACCEPTED,
                submitted_at=datetime.now(timezone.utc),
            )

        # 2. Reject what this broker cannot honestly simulate (see module
        # docstring) instead of silently mis-filling it.
        if intent.legs:
            return self._error_result(
                client_order_id,
                "FMPPaperBroker does not support multi-leg options orders in V1 "
                "(a single-symbol quote cannot honestly price a spread/condor)",
                OrderStatus.REJECTED,
            )
        if intent.qty <= 0:
            return self._error_result(
                client_order_id, f"Invalid order quantity {intent.qty}", OrderStatus.REJECTED
            )

        # 3. Fetch live quote
        try:
            # quote() returns a list of dicts, e.g. [{"symbol": "AAPL", "price": 150.0, "marketCap": 2e9}]
            resp = fmp_client.quote(intent.symbol)
            if not resp or not isinstance(resp, list):
                logger.error(f"FMPPaperBroker: Empty or invalid quote for {intent.symbol}")
                return self._error_result(client_order_id, "Quote not found or invalid format")

            quote_data = resp[0]
            raw_price = float(quote_data.get("price", 0.0))
            if raw_price <= 0:
                logger.error(f"FMPPaperBroker: Invalid price {raw_price} for {intent.symbol}")
                return self._error_result(client_order_id, f"Invalid price {raw_price}")

            # marketCap is genuinely unmeasured when FMP omits it, not zero --
            # a fabricated 0.0 previously routed straight into
            # TieredCostModel.get_liquidity_tier's smallest-market-cap bucket
            # ("illiquid", 20.0 bps) instead of its unknown-market-cap
            # fallback ("large_cap", 1.0 bps), a ~20x cost misclassification
            # (CONSTRAINT #4: missing data must surface as None, never a
            # fabricated default that looks like a real measurement).
            raw_market_cap = quote_data.get("marketCap")
            market_cap = float(raw_market_cap) if raw_market_cap else None

        except Exception as e:
            logger.error(f"FMPPaperBroker: Failed to fetch quote for {intent.symbol}: {e}")
            return self._error_result(client_order_id, f"FMP quote failed: {e}")

        # 4. Limit orders: no resting-order book in V1, so an order that
        # isn't marketable RIGHT NOW against the current quote is honestly
        # rejected rather than silently filled at a price the order never
        # asked for, or silently accepted and left to sit forever.
        if intent.order_type == OrderType.LIMIT:
            if intent.limit_price is None:
                return self._error_result(
                    client_order_id, "LIMIT order missing limit_price", OrderStatus.REJECTED
                )
            marketable = (
                raw_price <= intent.limit_price
                if intent.side == OrderSide.BUY
                else raw_price >= intent.limit_price
            )
            if not marketable:
                return self._error_result(
                    client_order_id,
                    f"Limit price {intent.limit_price} not marketable at current quote "
                    f"{raw_price} (FMPPaperBroker has no resting-order book in V1)",
                    OrderStatus.REJECTED,
                )

        # 5. Calculate execution costs
        costs = self.cost_model.calculate_cost(
            side=intent.side.value,
            shares=intent.qty,
            price=raw_price,
            order_type=intent.order_type.value,
            market_cap=market_cap
        )
        
        # TieredCostModel.calculate_cost's total_dollars already bundles
        # commission, SEC/TAF fees, spread, and slippage into one figure. V1
        # applies it as a flat cash deduction against the raw quote price
        # rather than adjusting the fill price itself -- simpler, and the
        # net cash impact to the paper account is identical either way.
        total_cost_dollars = costs["total_dollars"]
        commission_and_fees = total_cost_dollars
        fill_price = raw_price
        
        # 6. Apply Fill
        success = self.store.apply_fill(
            client_order_id=client_order_id,
            symbol=intent.symbol,
            side=intent.side.value,
            qty=intent.qty,
            fill_price=fill_price,
            commission_and_fees=commission_and_fees,
            target_qty=getattr(intent, "target_qty", None),
            status=OrderStatus.FILLED.value
        )
        
        if not success:
            return self._error_result(client_order_id, "Insufficient funds or inventory", OrderStatus.REJECTED)
            
        # 7. Result and Stream Event
        broker_order_id = f"FMP-{client_order_id}"
        now = datetime.now(timezone.utc)
        
        result = OrderResult(
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
            status=OrderStatus.FILLED,
            filled_qty=intent.qty,
            filled_avg_price=fill_price,
            submitted_at=now,
            filled_at=now
        )
        
        event = TradeUpdateEvent(
            event_type="fill",
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
            symbol=intent.symbol,
            side=intent.side,
            filled_qty=intent.qty,
            filled_avg_price=fill_price,
            timestamp=now
        )
        
        # Push to stream
        try:
            self.stream_queue.put_nowait(event)
        except Exception as e:
            logger.error(f"FMPPaperBroker: Failed to enqueue stream event: {e}")
            
        return result

    def _error_result(self, client_order_id: str, message: str, status: OrderStatus = OrderStatus.ERROR) -> OrderResult:
        now = datetime.now(timezone.utc)
        return OrderResult(
            client_order_id=client_order_id,
            broker_order_id=None,
            status=status,
            error_message=message,
            submitted_at=now
        )

    async def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an open order."""
        # All our paper orders fill or reject immediately.
        return False

    async def get_open_positions(self) -> List[PositionSnapshot]:
        """Get all open positions."""
        # Using a threadpool if it were purely async, but SQLite reads are fast.
        # Real production would run this via asyncio.to_thread
        return await asyncio.to_thread(self.store.get_open_positions)

    async def get_account(self) -> AccountSnapshot:
        """Get the current account snapshot."""
        return await asyncio.to_thread(self.store.get_account)

    async def get_orders(self, status: Optional[str] = None, limit: int = 100) -> List[OrderResult]:
        """Get order history."""
        return await asyncio.to_thread(self.store.get_orders, status, limit)

    async def stream_trade_updates(self) -> AsyncIterator[TradeUpdateEvent]:
        """Stream real-time trade updates."""
        while True:
            event = await self.stream_queue.get()
            yield event
            self.stream_queue.task_done()
