"""
InvestYo Quant Platform - Position Sizing Package
===================================================
Volatility-targeted and fractional-Kelly position sizing, replacing the
arbitrary score-derived win-probability formulas previously duplicated in
strategy_engine.py and main_orchestrator.py.

Stage 1.7 additions: per-strategy bootstrap-conservative sizing via
``kelly_sizing_for_strategy()``, plus the helper
``_get_per_strategy_returns()``.

Sizing decision pipeline additions (``sizing/position_sizer.py``): the
ordered regime/meta-label/ceiling composition (``size_position()``), the
portfolio-level gross-exposure cap (``apply_portfolio_gross_cap()``), and the
``was_capped`` / ``binding_constraint`` guardrail telemetry that did not
exist before -- see that module's docstring for the full design rationale.
"""

_VOL_TARGET_EXPORTS = {"volatility_target_weight", "portfolio_vol_target"}
_KELLY_EXPORTS = {
    "estimate_win_rate_and_payoff",
    "estimate_win_rate_and_payoff_per_strategy",
    "bootstrap_kelly_confidence",
    "kelly_sizing_for_strategy",
    "_get_per_strategy_returns",
    "fractional_kelly",
}
_POSITION_SIZER_EXPORTS = {
    "SizingDecision",
    "CapEventSummary",
    "PortfolioCapResult",
    "size_position",
    "apply_portfolio_gross_cap",
    "detect_raw_cap_binding",
    "clamp_with_binding",
}

def __getattr__(name):
    if name in _VOL_TARGET_EXPORTS:
        from sizing import vol_target
        return getattr(vol_target, name)
    if name in _KELLY_EXPORTS:
        from sizing import kelly
        return getattr(kelly, name)
    if name in _POSITION_SIZER_EXPORTS:
        from sizing import position_sizer
        return getattr(position_sizer, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
