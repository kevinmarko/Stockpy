"""
InvestYo Quant Platform - Position Sizing Package
===================================================
Volatility-targeted and fractional-Kelly position sizing, replacing the
arbitrary score-derived win-probability formulas previously duplicated in
strategy_engine.py and main_orchestrator.py.

Stage 1.7 additions: per-strategy bootstrap-conservative sizing via
``kelly_sizing_for_strategy()``, plus the helper
``_get_per_strategy_returns()``.
"""

from sizing.vol_target import volatility_target_weight, portfolio_vol_target
from sizing.kelly import (
    estimate_win_rate_and_payoff,
    estimate_win_rate_and_payoff_per_strategy,
    bootstrap_kelly_confidence,
    kelly_sizing_for_strategy,
    _get_per_strategy_returns,
    fractional_kelly,
)
