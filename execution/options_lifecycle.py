import logging
from typing import Optional, Any, Dict
from settings import settings
from dto_models import MacroEconomicDTO

logger = logging.getLogger(__name__)

def run_automated_delta_hedge_cycle(executor: Any) -> Optional[Dict[str, Any]]:
    """Resolves ONE real SPY quote and, only if available, sizes and
    executes the automated dynamic SPY delta hedge off that single value.
    """
    try:
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
        if hedge_res.get("hedged"):
            logger.info(
                "Automated SPY delta hedging executed: %s %d SPY @ ~$%.2f",
                hedge_res.get("order", {}).get("side"),
                hedge_res.get("order", {}).get("qty"),
                hedge_res.get("fill", {}).get("fill_price", 0.0),
            )
        return hedge_res
    except Exception as exc:
        logger.warning("Automated SPY delta hedge cycle failed (non-critical): %s", exc)
        return None

def run_automated_options_lifecycle(macro_dto: Optional[MacroEconomicDTO] = None) -> None:
    """Runs the automated options paper-trading lifecycle: exit management,
    0DTE fast exits, new-position auto-execution, and dynamic SPY delta
    hedging.
    """
    if not (
        getattr(settings, "PAPER_OPTIONS_AUTO_EXECUTE_ENABLED", False)
        or getattr(settings, "OPTIONS_AUTO_EXIT_ENABLED", False)
        or getattr(settings, "OPTIONS_DELTA_HEDGE_ENABLED", False)
        or getattr(settings, "OPTIONS_0DTE_ENABLED", False)
    ):
        return
    try:
        from execution.options_paper_executor import OptionsPaperExecutor
        _executor = OptionsPaperExecutor()

        # 1. Manage Exits (50% profit target, 2.0x stop loss, 21-DTE gamma)
        if getattr(settings, "OPTIONS_AUTO_EXIT_ENABLED", False):
            _exit_res = _executor.execute_auto_exits()
            logger.info(
                "Automated options exit lifecycle management: %d evaluated, %d closed, %d failed",
                _exit_res.get("evaluated_count", 0),
                _exit_res.get("closed_count", 0),
                _exit_res.get("failed_count", 0),
            )

        # 1b. Manage 0DTE Fast Exits (Profit Target +75%, Stop Loss -30%, 15:45 ET Hard Stop)
        if getattr(settings, "OPTIONS_0DTE_ENABLED", False) or getattr(settings, "OPTIONS_AUTO_EXIT_ENABLED", False):
            try:
                from pilots.zero_dte_engine import manage_0dte_exits
                _0dte_res = manage_0dte_exits(store=_executor.store)
                if _0dte_res.get("executed_count", 0) > 0:
                    logger.info(
                        "Automated 0DTE options exit lifecycle: %d evaluated, %d executed, %d failed",
                        _0dte_res.get("evaluated_count", 0),
                        _0dte_res.get("executed_count", 0),
                        _0dte_res.get("failed_count", 0),
                    )
            except Exception as _0dte_exc:
                logger.debug("0DTE exit lifecycle evaluation: %s", _0dte_exc)

        # 2. Open New Strategy Option Positions
        if getattr(settings, "PAPER_OPTIONS_AUTO_EXECUTE_ENABLED", False):
            _exec_res = _executor.execute_strategy_directives(macro_dto=macro_dto)
            logger.info(
                "Automated strategy options paper execution completed: "
                "%d executed, %d skipped, %d failed",
                _exec_res.get("executed_count", 0),
                _exec_res.get("skipped_count", 0),
                _exec_res.get("failed_count", 0),
            )

        # 3. Dynamic SPY Delta Hedging
        if getattr(settings, "OPTIONS_DELTA_HEDGE_ENABLED", False):
            run_automated_delta_hedge_cycle(_executor)
    except Exception as _auto_opt_exc:
        logger.warning(
            "Automated strategy options paper execution/lifecycle failed (non-critical): %s",
            _auto_opt_exc,
        )
