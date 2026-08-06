"""
InvestYo Quant Platform - FMP Paper Broker
==========================================
Paper trading backend using Financial Modeling Prep (FMP) quotes and a local
SQLite store for cash/position tracking. Uses `TieredCostModel` to enforce
realistic commissions, bid-ask spread, slippage, and regulatory fees.
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
        if self._readonly:
            return OrderResult(
                client_order_id=intent.client_order_id or "unknown",
                broker_order_id=None,
                status=OrderStatus.ERROR,
                error_message="Cannot submit orders in readonly mode."
            )

        client_order_id = intent.client_order_id or "unknown"
        
        # 1. Fetch live quote
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
                
            market_cap = float(quote_data.get("marketCap", 0.0))
            
        except Exception as e:
            logger.error(f"FMPPaperBroker: Failed to fetch quote for {intent.symbol}: {e}")
            return self._error_result(client_order_id, f"FMP quote failed: {e}")

        # 2. Calculate execution costs
        costs = self.cost_model.calculate_cost(
            side=intent.side.value,
            shares=intent.qty,
            price=raw_price,
            order_type=intent.order_type.value,
            market_cap=market_cap
        )
        
        total_cost_dollars = costs["total_dollars"]
        commission_and_fees = total_cost_dollars
        
        # Simulated fill price is adjusted by the per-share cost if we want to bundle it into the basis,
        # but apply_fill expects the raw fill price and a separate commission fee to deduct from cash.
        # Wait, slippage and spread are inherently price adjustments.
        # TieredCostModel returns total_dollars which includes commission, sec, taf, slippage, and spread.
        # We will pass the raw_price as fill_price, and `total_cost_dollars` as commission_and_fees.
        fill_price = raw_price
        
        # 3. Apply Fill
        success = self.store.apply_fill(
            client_order_id=client_order_id,
            symbol=intent.symbol,
            side=intent.side.value,
            qty=intent.qty,
            fill_price=fill_price,
            commission_and_fees=commission_and_fees,
            status=OrderStatus.FILLED.value
        )
        
        if not success:
            return self._error_result(client_order_id, "Insufficient funds or inventory", OrderStatus.REJECTED)
            
        # 4. Result and Stream Event
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
        # All our paper orders fill or reject immediately.
        return False

    async def get_open_positions(self) -> List[PositionSnapshot]:
        # Using a threadpool if it were purely async, but SQLite reads are fast.
        # Real production would run this via asyncio.to_thread
        return await asyncio.to_thread(self.store.get_open_positions)

    async def get_account(self) -> AccountSnapshot:
        return await asyncio.to_thread(self.store.get_account)

    async def get_orders(self, status: Optional[str] = None, limit: int = 100) -> List[OrderResult]:
        return await asyncio.to_thread(self.store.get_orders, status, limit)

    async def stream_trade_updates(self) -> AsyncIterator[TradeUpdateEvent]:
        while True:
            event = await self.stream_queue.get()
            yield event
            self.stream_queue.task_done()
