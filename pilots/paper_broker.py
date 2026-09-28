"""Dependency-light read helper for paper broker data, keeping heavy engines out of the Pilots API."""

from typing import Any, Dict, List, Optional
from data.paper_account_store import PaperAccountStore

def get_account() -> Dict[str, Any]:
    store = PaperAccountStore(readonly=True)
    snapshot = store.get_account()
    return {
        "equity": snapshot.equity,
        "cash": snapshot.cash,
        "buying_power": snapshot.buying_power
    }

def get_positions() -> List[Dict[str, Any]]:
    store = PaperAccountStore(readonly=True)
    snapshots = store.get_open_positions()
    results = []
    for p in snapshots:
        current_price = None
        unrealized_pl_pct = None
        if p.market_value is not None and p.qty != 0:
            current_price = p.market_value / p.qty
        if p.avg_entry_price and p.avg_entry_price > 0 and current_price:
            unrealized_pl_pct = (current_price / p.avg_entry_price) - 1.0
            
        results.append({
            "symbol": p.symbol,
            "qty": p.qty,
            "avg_cost": p.avg_entry_price,
            "current_price": current_price,
            "market_value": p.market_value,
            "unrealized_pl": p.unrealized_pl,
            "unrealized_pl_pct": unrealized_pl_pct,
            "strategy_id": p.strategy_id,
            "pilot_id": p.pilot_id,
            "experiment_arm": p.experiment_arm,
        })
    return results

def get_orders(status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    store = PaperAccountStore(readonly=True)
    return store.get_full_orders(status=status, limit=limit)

def get_closed_trades(symbol: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Realized-PnL history for flattened/expired/rolled paper positions."""
    store = PaperAccountStore(readonly=True)
    return store.get_full_closed_trades(symbol=symbol, limit=limit)

def execute_equity_order(
    symbol: str,
    *,
    side: str = "buy",
    quantity: Optional[float] = None,
    dollar_amount: Optional[float] = None,
    order_type: str = "market",
    limit_price: Optional[float] = None,
    is_live: bool = False,
) -> Dict[str, Any]:
    """Manual equity paper order (Quick Trade). See pilots.paper_equity_order."""
    from pilots.paper_equity_order import execute_equity_order as _exec_equity
    return _exec_equity(
        symbol,
        side=side,
        quantity=quantity,
        dollar_amount=dollar_amount,
        order_type=order_type,
        limit_price=limit_price,
        is_live=is_live,
    )
