import os
import json
import uuid
import time
import asyncio
from typing import Optional
from mcp.server.fastmcp import FastMCP
from execution.broker_base import OrderIntent, OrderSide, OrderType
from execution.order_manager import OrderManager
from execution.risk_gate import PreTradeRiskGate
from settings import settings

mcp = FastMCP("Robinhood Execution")

# In-memory storage for dual-key confirmation
_pending_orders = {}

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
    PREPARES a live trade for execution. This does NOT place the order immediately.
    Returns a confirmation_token that must be passed to confirm_live_trade to execute.
    """
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
    
    token = str(uuid.uuid4())
    _pending_orders[token] = {
        "intent": intent,
        "expires": time.time() + 300 # 5 minutes TTL
    }
    
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
        "message": "Call confirm_live_trade with the confirmation_token to execute this order."
    }, indent=2)

@mcp.tool()
async def confirm_live_trade(confirmation_token: str) -> str:
    """
    Confirms and executes a previously prepared live trade.
    """
    if not _rate_limiter.consume():
        return json.dumps({"status": "error", "message": "Rate limit exceeded. Try again later."})
        
    if confirmation_token not in _pending_orders:
        return json.dumps({"status": "error", "message": "Invalid or expired confirmation_token."})
        
    order_data = _pending_orders.pop(confirmation_token)
    if time.time() > order_data["expires"]:
        return json.dumps({"status": "error", "message": "Confirmation token expired."})
        
    intent = order_data["intent"]
    
    broker = _get_broker()
    om = OrderManager(broker, dry_run=False, risk_gate=PreTradeRiskGate())
    
    try:
        result = await om.submit_order_with_idempotency(intent)
        return json.dumps({
            "status": "success",
            "broker_order_id": result.broker_order_id,
            "order_status": result.status.value
        }, indent=2)
    except Exception as e:
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
            
        positions = getattr(snapshot, "positions", [])
        if not positions:
            return json.dumps({"status": "success", "positions": []})
            
        pos_list = []
        for p in positions:
            pos_list.append({
                "symbol": p.symbol,
                "qty": float(p.qty),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl)
            })
            
        return json.dumps({
            "status": "success",
            "net_liquidity": float(snapshot.net_liquidity),
            "buying_power": float(snapshot.buying_power),
            "positions": pos_list
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

if __name__ == "__main__":
    mcp.run()
