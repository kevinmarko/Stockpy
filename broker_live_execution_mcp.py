import os
import json
import uuid
import time
import asyncio
from typing import Optional
from mcp.server.fastmcp import FastMCP
from execution.broker_base import OrderIntent, OrderSide, OrderType
from execution.order_manager import OrderManager
from execution.risk_gate import PreTradeRiskGate, RiskContext
from execution.live_trade_proposals_store import LiveTradeProposalStore
from settings import settings

mcp = FastMCP("Broker Live Execution")

# Simple token bucket rate limiter
class RateLimiter:
    def __init__(self, capacity: int, fill_rate: float):
        self.capacity = capacity
        self.tokens = capacity
        self.fill_rate = fill_rate
        self.last_fill = time.time()

    def consume(self, tokens: int = 1) -> bool:
        now = time.time()
        elapsed = now - self.last_fill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.fill_rate)
        self.last_fill = now

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

# 5 requests per minute = 1 request every 12 seconds
_rate_limiter = RateLimiter(capacity=5, fill_rate=1.0/12.0)

def _get_broker():
    # resolve_broker_backend() is the single source of truth for "which
    # broker should actually be used" -- shared with
    # main_orchestrator.py::_execute_broker_orders so the two call sites
    # can never drift on the fmp_paper/live-trading safety guard. It
    # forces 'alpaca' (and fires a CRITICAL alert) when
    # BROKER_BACKEND='fmp_paper' while this run is genuinely going live
    # (ADVISORY_ONLY=False and ALPACA_PAPER=False).
    from execution.broker_selection import resolve_broker_backend

    if resolve_broker_backend() == "fmp_paper":
        from execution.fmp_paper_broker import FMPPaperBroker
        return FMPPaperBroker()
    from execution.alpaca_broker import AlpacaBroker
    return AlpacaBroker()

@mcp.tool()
def execute_live_trade(symbol: str, side: str, qty: float, order_type: str = "market", limit_price: float = None) -> str:
    """
    PROPOSES a live trade for operator approval. This does NOT place the order
    and does NOT skip to execution once "confirmed" -- the returned
    confirmation_token identifies a durable, pending_approval proposal that
    must be approved by the operator (via the Pilots PWA) before
    confirm_live_trade can ever submit it to the broker.
    """
    if not settings.LIVE_TRADE_EXECUTION_ENABLED:
        return json.dumps({
            "status": "error",
            "message": "Live trade execution is disabled (LIVE_TRADE_EXECUTION_ENABLED=false).",
        })

    if not _rate_limiter.consume():
        return json.dumps({"status": "error", "message": "Rate limit exceeded. Try again later."})

    try:
        side_enum = OrderSide(side.lower())
        otype_enum = OrderType(order_type.lower())
    except ValueError as e:
        return json.dumps({"status": "error", "message": f"Invalid order parameters: {e}"})

    intent = OrderIntent(
        strategy_id="mcp-agent",
        symbol=symbol.upper(),
        side=side_enum,
        qty=float(qty),
        order_type=otype_enum,
        limit_price=limit_price
    )

    try:
        token = LiveTradeProposalStore().create_proposal(
            symbol=intent.symbol,
            side=intent.side.value,
            qty=intent.qty,
            order_type=intent.order_type.value,
            limit_price=intent.limit_price,
        )
    except ValueError as e:
        return json.dumps({"status": "error", "message": f"Invalid order parameters: {e}"})

    try:
        from observability.alerts import send_alert
        send_alert(
            "WARNING",
            f"Live trade proposal {token} awaiting operator approval: "
            f"{intent.side.value} {intent.qty} {intent.symbol}",
        )
    except Exception:
        pass  # notification failure must never block proposal creation

    return json.dumps({
        "status": "pending_confirmation",
        "confirmation_token": token,
        "details": {
            "symbol": intent.symbol,
            "side": intent.side.value,
            "qty": intent.qty,
            "type": intent.order_type.value,
            "limit_price": intent.limit_price
        },
        "message": (
            "This order will NOT execute until the operator approves it via "
            "the Pilots PWA. Do not call confirm_live_trade expecting "
            "immediate success -- it will report the proposal's current "
            "status (pending/rejected/expired) until approved."
        )
    }, indent=2)

@mcp.tool()
async def confirm_live_trade(confirmation_token: str) -> str:
    """
    Confirms and executes a previously prepared live trade.

    Builds a real RiskContext (open positions, account snapshot, and a
    current price for the traded symbol) before submission so
    ``PreTradeRiskGate`` actually runs — passing ``risk_context=None`` to
    ``OrderManager.submit_order_with_idempotency`` makes the gate a silent
    no-op (see execution/order_manager.py's documented behavior), which was
    a real safety gap on this live-order-placement path.
    """
    if not _rate_limiter.consume():
        return json.dumps({"status": "error", "message": "Rate limit exceeded. Try again later."})

    store = LiveTradeProposalStore()
    proposal = store.get_by_token(confirmation_token)
    if proposal is None:
        return json.dumps({"status": "error", "message": "Invalid or expired confirmation_token."})

    if proposal.status != "approved":
        return json.dumps({
            "status": "error",
            "message": (
                f"Order not yet executable: current status is '{proposal.status}'. "
                "It must be approved by the operator via the Pilots PWA before "
                "it can execute."
            ),
        })

    intent = OrderIntent(
        strategy_id=proposal.strategy_id,
        symbol=proposal.symbol,
        side=OrderSide(proposal.side),
        qty=float(proposal.qty),
        order_type=OrderType(proposal.order_type),
        limit_price=proposal.limit_price,
    )

    broker = _get_broker()
    om = OrderManager(broker, dry_run=False, risk_gate=PreTradeRiskGate())

    try:
        # Best-effort broker context for the pre-trade risk gate, mirroring
        # main_orchestrator.py::_execute_broker_orders' construction. Each
        # optional RiskContext field passes conservatively (never blocks) on
        # a None/empty value, so a partial failure here degrades the gate's
        # coverage rather than aborting the trade -- the fail-closed
        # boundary is still the risk gate itself, which now genuinely runs.
        try:
            open_positions = await broker.get_open_positions()
        except Exception:
            open_positions = []
        try:
            account = await broker.get_account()
        except Exception:
            account = None

        current_prices: dict = {}
        try:
            from data.market_data import get_provider, MarketDataError
            quote = get_provider().get_latest_quote(intent.symbol)
            current_prices[intent.symbol.upper()] = float(quote.price)
        except Exception:
            pass  # no live price available -- position-size check skips conservatively

        risk_context = RiskContext(
            open_positions=open_positions,
            account=account,
            current_prices=current_prices,
            is_premium_sell_strategy=False,
        )

        result = await om.submit_order_with_idempotency(intent, risk_context=risk_context)
        if result.status.value == "error":
            try:
                store.mark_failed(confirmation_token, result.error_message or "unknown error")
            except Exception:
                pass  # marking-failed is best-effort; the real error is reported below
            return json.dumps({
                "status": "error",
                "message": result.error_message,
            }, indent=2)

        try:
            store.mark_executed(confirmation_token, result.broker_order_id)
        except Exception:
            pass  # the broker order already went through; a bookkeeping failure here must not mask that

        return json.dumps({
            "status": "success",
            "broker_order_id": result.broker_order_id,
            "order_status": result.status.value
        }, indent=2)
    except Exception as e:
        try:
            store.mark_failed(confirmation_token, str(e))
        except Exception:
            pass
        return json.dumps({"status": "error", "message": str(e)})

@mcp.tool()
async def cancel_order(order_id: str) -> str:
    """Cancels an open order."""
    if not _rate_limiter.consume():
        return json.dumps({"status": "error", "message": "Rate limit exceeded."})

    broker = _get_broker()
    try:
        success = await broker.cancel_order(order_id)
        if success:
            return json.dumps({"status": "success", "message": f"Order {order_id} cancelled."})
        return json.dumps({"status": "error", "message": f"Failed to cancel {order_id}."})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

@mcp.tool()
def get_live_positions() -> str:
    """Fetches the latest real account snapshot from robinhood_portfolio."""
    if not _rate_limiter.consume():
        return json.dumps({"status": "error", "message": "Rate limit exceeded."})

    try:
        from data.historical_store import HistoricalStore
        snapshot = HistoricalStore().latest_account_snapshot()
        if snapshot is None:
            return json.dumps({"status": "error", "message": "No account snapshot available."})

        positions = getattr(snapshot, "positions", {}) or {}
        if not positions:
            return json.dumps({
                "status": "success",
                "total_equity": float(snapshot.total_equity),
                "buying_power": float(snapshot.buying_power),
                "positions": [],
            })

        pos_list = []
        # AccountSnapshot.positions is dict[symbol -> PortfolioPosition] --
        # iterate .values(), not the dict itself (which yields string keys).
        for p in positions.values():
            pos_list.append({
                "symbol": p.symbol,
                "quantity": float(p.quantity),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl)
            })

        return json.dumps({
            "status": "success",
            "total_equity": float(snapshot.total_equity),
            "buying_power": float(snapshot.buying_power),
            "positions": pos_list
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

if __name__ == "__main__":
    mcp.run()
