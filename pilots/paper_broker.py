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

def execute_paper_order(
    symbol: str,
    *,
    asset_type: str = "option",
    side: str = "buy",
    quantity: Optional[float] = None,
    dollar_amount: Optional[float] = None,
    order_type: str = "market",
    limit_price: Optional[float] = None,
    expiration: Optional[str] = None,
    legs: Optional[List[Dict[str, Any]]] = None,
    is_live: bool = False
) -> Dict[str, Any]:
    """
    Executes a paper order for stock or options, updating PaperAccountStore.
    Delegates directly to pilots.paper_broker_options_order.execute_paper_order.
    """
    from pilots.paper_broker_options_order import execute_paper_order as _exec_order
    return _exec_order(
        symbol=symbol,
        asset_type=asset_type,
        side=side,
        quantity=quantity,
        dollar_amount=dollar_amount,
        order_type=order_type,
        limit_price=limit_price,
        expiration=expiration,
        legs=legs,
        is_live=is_live,
    )

def get_strategy_options_candidates(symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Fetches current gate-passing strategy option directives ready for automated paper execution."""
    from execution.options_paper_executor import OptionsPaperExecutor
    executor = OptionsPaperExecutor()
    return executor.get_actionable_directives(symbols=symbols)

def execute_strategy_options(
    symbols: Optional[List[str]] = None,
    dry_run: bool = False,
    max_notional: Optional[float] = None
) -> Dict[str, Any]:
    """Executes automated strategy option trades into the paper broker."""
    from execution.options_paper_executor import OptionsPaperExecutor
    executor = OptionsPaperExecutor()
    directives = executor.get_actionable_directives(symbols=symbols)
    return executor.execute_strategy_directives(
        directives=directives,
        dry_run=dry_run,
        max_notional_per_order=max_notional
    )

def get_portfolio_greeks() -> Dict[str, Any]:
    """Computes aggregate net portfolio Greeks across all open paper positions.

    Resolves a real SPY quote up front via ``pilots.price_provider`` (the
    same helper ``pilots.options_hedging.get_delta_hedge_preview`` already
    uses) and threads it into ``calculate_portfolio_greeks`` explicitly --
    that function used to silently fabricate a $500.0 SPY price whenever no
    SPY position happened to be held (CONSTRAINT #4 violation; see
    docs/known_issues/options_risk_fabricated_spy_spot.md). Passing ``None``
    on a failed resolution here is intentional: calculate_portfolio_greeks
    still attempts its own real quote resolution via the market data
    provider as a second, independent chance, and never fabricates a price
    either way.
    """
    from pilots.options_risk import calculate_portfolio_greeks
    from pilots.price_provider import get_current_price
    store = PaperAccountStore(readonly=True)
    spy_spot = get_current_price("SPY")
    return calculate_portfolio_greeks(
        store=store,
        spy_spot=spy_spot if spy_spot and spy_spot > 0 else None,
    )


def manage_position_exits(
    dry_run: bool = False,
    profit_target_pct: Optional[float] = None,
    stop_loss_multiple: Optional[float] = None,
    manage_dte_threshold: Optional[int] = None,
) -> Dict[str, Any]:
    """Evaluates open positions against profit/stop/DTE rules and executes auto-exits."""
    from execution.options_paper_executor import OptionsPaperExecutor
    executor = OptionsPaperExecutor()
    candidates = executor.evaluate_position_exits(
        profit_target_pct=profit_target_pct,
        stop_loss_multiple=stop_loss_multiple,
        manage_dte_threshold=manage_dte_threshold,
    )
    return executor.execute_auto_exits(exit_candidates=candidates, dry_run=dry_run)


def execute_roll(
    symbol: str,
    close_legs: List[Dict[str, Any]],
    open_legs: List[Dict[str, Any]],
    limit_price: Optional[float] = None,
    contracts: int = 1,
    is_live: bool = False,
) -> Dict[str, Any]:
    """Executes an atomic multi-leg roll in the paper broker."""
    if is_live:
        return {
            "ok": False,
            "message": "Advisory-Only Mode: Live roll order execution is disabled. "
                       "Multi-leg roll proposals must be reviewed and executed via Robinhood directly."
        }

    from datetime import datetime, timezone
    store = PaperAccountStore()
    client_order_id = f"ROLL-{symbol}-{int(datetime.now(timezone.utc).timestamp())}"

    success = store.apply_roll_fill(
        client_order_id=client_order_id,
        symbol=symbol,
        close_legs=close_legs,
        open_legs=open_legs,
        contracts=contracts,
        limit_price=limit_price,
        strategy_id="Manual Trade",
    )

    if success:
        return {
            "ok": True,
            "order_id": client_order_id,
            "symbol": symbol,
            "contracts": contracts,
            "message": f"Successfully rolled {contracts} contract(s) for {symbol}",
        }
    else:
        return {
            "ok": False,
            "order_id": client_order_id,
            "symbol": symbol,
            "contracts": contracts,
            "message": f"Roll fill failed for {symbol}: insufficient cash or database lock",
        }



