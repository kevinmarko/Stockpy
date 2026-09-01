"""
execution/options_lifecycle.py — Shared Automated Options Paper-Trading Lifecycle.
================================================================================
Houses the automated options lifecycle routines:
  1. Exit management (50% profit target, 2.0x stop loss, 21-DTE gamma)
  1b. 0DTE Fast Exits (+75% profit target, -30% stop loss, 15:45 ET hard exit)
  2. New-position strategy auto-execution (passes_premium_gate & Stage 4 ML sizing)
  3. Dynamic SPY Delta Hedging (beta-weighted delta rebalancing with deadband)

Shared between the standalone advisory orchestrator (main.py) and the persistent
orchestrator daemon (desktop/daemon_runtime.py).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from settings import settings

logger = logging.getLogger("InvestYo.main")


def run_automated_delta_hedge_cycle(
    executor: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Runs one automated dynamic SPY delta hedging cycle against PaperAccountStore.

    Sized off real live SPY quotes; refuses to fabricate a price (CONSTRAINT #4)
    and skips cleanly if no quote is available.

    Returns the execute_delta_hedge() result dict, or None when skipped/failed.
    Never raises (CONSTRAINT #6).
    """
    try:
        if executor is None:
            from execution.options_paper_executor import OptionsPaperExecutor
            executor = OptionsPaperExecutor()

        from pilots.options_hedging import execute_delta_hedge
        from pilots.options_risk import calculate_portfolio_greeks
        from pilots.price_provider import get_current_price

        spy_spot = get_current_price("SPY")
        if not spy_spot or spy_spot <= 0:
            logger.warning(
                "Automated SPY delta hedge skipped this cycle: no live SPY "
                "quote available (refusing to size a hedge off a fabricated price)."
            )
            return None

        greeks = calculate_portfolio_greeks(store=executor.store, spy_spot=spy_spot)
        hedge_res = execute_delta_hedge(store=executor.store, portfolio_greeks=greeks, spy_spot=spy_spot)
        if hedge_res and hedge_res.get("hedged"):
            order_info = hedge_res.get("order") or {}
            side = order_info.get("side") or hedge_res.get("action")
            qty = order_info.get("qty") or hedge_res.get("shares")
            fill_info = hedge_res.get("fill") or {}
            fill_price = fill_info.get("fill_price", 0.0)
            logger.info(
                "Automated SPY delta hedging executed: %s %s SPY @ ~$%.2f",
                side,
                qty,
                fill_price,
            )
        return hedge_res
    except Exception as exc:
        logger.warning("Automated SPY delta hedge cycle failed (non-critical): %s", exc)
        return None


def run_automated_options_lifecycle(
    macro_dto: Optional[Any] = None,
    executor: Optional[Any] = None,
    delta_hedge_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Runs the automated options paper-trading lifecycle: exit management,
    0DTE fast exits, new-position auto-execution, and dynamic SPY delta
    hedging.

    Gated on settings:
      - PAPER_OPTIONS_AUTO_EXECUTE_ENABLED
      - OPTIONS_AUTO_EXIT_ENABLED
      - OPTIONS_DELTA_HEDGE_ENABLED
      - OPTIONS_0DTE_ENABLED

    Never raises -- every step is independently non-fatal to the pipeline
    (CONSTRAINT #6).
    """
    if not (
        getattr(settings, "PAPER_OPTIONS_AUTO_EXECUTE_ENABLED", False)
        or getattr(settings, "OPTIONS_AUTO_EXIT_ENABLED", False)
        or getattr(settings, "OPTIONS_DELTA_HEDGE_ENABLED", False)
        or getattr(settings, "OPTIONS_0DTE_ENABLED", False)
    ):
        return {"status": "skipped", "reason": "all_flags_disabled"}

    results: Dict[str, Any] = {"status": "executed"}
    try:
        if executor is None:
            from execution.options_paper_executor import OptionsPaperExecutor
            executor = OptionsPaperExecutor()

        # 1. Manage Exits (50% profit target, 2.0x stop loss, 21-DTE gamma)
        if getattr(settings, "OPTIONS_AUTO_EXIT_ENABLED", False):
            try:
                _exit_res = executor.execute_auto_exits()
                results["auto_exits"] = _exit_res
                logger.info(
                    "Automated options exit lifecycle management: %d evaluated, %d closed, %d failed",
                    _exit_res.get("evaluated_count", 0),
                    _exit_res.get("closed_count", 0) if "closed_count" in _exit_res else _exit_res.get("executed_count", 0),
                    _exit_res.get("failed_count", 0),
                )
            except Exception as _exit_exc:
                logger.warning("Automated options exit lifecycle step failed: %s", _exit_exc)
                results["auto_exits"] = {"error": str(_exit_exc)}

        # 1b. Manage 0DTE Fast Exits (Profit Target +75%, Stop Loss -30%, 15:45 ET Hard Stop)
        if getattr(settings, "OPTIONS_0DTE_ENABLED", False) or getattr(settings, "OPTIONS_AUTO_EXIT_ENABLED", False):
            try:
                from pilots.zero_dte_engine import manage_0dte_exits
                _0dte_res = manage_0dte_exits(store=executor.store)
                results["0dte_exits"] = _0dte_res
                if _0dte_res.get("executed_count", 0) > 0:
                    logger.info(
                        "Automated 0DTE options exit lifecycle: %d evaluated, %d executed, %d failed",
                        _0dte_res.get("evaluated_count", 0),
                        _0dte_res.get("executed_count", 0),
                        _0dte_res.get("failed_count", 0),
                    )
            except Exception as _0dte_exc:
                logger.debug("0DTE exit lifecycle evaluation: %s", _0dte_exc)
                results["0dte_exits"] = {"error": str(_0dte_exc)}

        # 2. Open New Strategy Option Positions
        if getattr(settings, "PAPER_OPTIONS_AUTO_EXECUTE_ENABLED", False):
            try:
                _exec_res = executor.execute_strategy_directives(macro_dto=macro_dto)
                results["strategy_execution"] = _exec_res
                logger.info(
                    "Automated strategy options paper execution completed: "
                    "%d executed, %d skipped, %d failed",
                    _exec_res.get("executed_count", 0),
                    _exec_res.get("skipped_count", 0),
                    _exec_res.get("failed_count", 0),
                )
            except Exception as _strat_exc:
                logger.warning("Automated strategy options execution step failed: %s", _strat_exc)
                results["strategy_execution"] = {"error": str(_strat_exc)}

        # 3. Dynamic SPY Delta Hedging
        if getattr(settings, "OPTIONS_DELTA_HEDGE_ENABLED", False):
            try:
                hedge_fn = delta_hedge_fn or run_automated_delta_hedge_cycle
                _hedge_res = hedge_fn(executor)
                results["delta_hedge"] = _hedge_res
            except Exception as _hedge_exc:
                logger.warning("Automated SPY delta hedge step failed: %s", _hedge_exc)
                results["delta_hedge"] = {"error": str(_hedge_exc)}

    except Exception as _auto_opt_exc:
        logger.warning(
            "Automated strategy options paper execution/lifecycle failed (non-critical): %s",
            _auto_opt_exc,
        )
        results["error"] = str(_auto_opt_exc)

    return results
