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
            "unrealized_pl_pct": unrealized_pl_pct
        })
    return results

def get_orders(status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    store = PaperAccountStore(readonly=True)
    return store.get_full_orders(status=status, limit=limit)
