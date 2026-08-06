"""
InvestYo Quant Platform - Signals Package
==========================================
Decoupled signal abstractions, registry, aggregator, and standard modules.
"""

_BASE_EXPORTS = {"SignalContext", "SignalOutput", "SignalModule"}
_REGISTRY_EXPORTS = {"SignalRegistry", "global_registry"}
_AGGREGATOR_EXPORTS = {"SignalAggregator"}

def __getattr__(name):
    if name in _BASE_EXPORTS:
        from signals import base
        return getattr(base, name)
    if name in _REGISTRY_EXPORTS:
        from signals import registry
        return getattr(registry, name)
    if name in _AGGREGATOR_EXPORTS:
        from signals import aggregator
        return getattr(aggregator, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def _register_all():
    # Trigger imports to execute the registration decorators for standard modules
    import signals.macro_regime
    import signals.graham_value
    import signals.dividend_quality
    import signals.macd_momentum
    import signals.aroon_trend
    import signals.forecast_alignment
    import signals.relative_strength
    import signals.rsi_extremes
    import signals.sortino_drawdown
    import signals.edge_garch
    import signals.timeseries_momentum
    import signals.cross_sectional_momentum
    import signals.rsi2_mean_reversion
    import signals.multifactor
    import signals.regime_multiplier
    import signals.lgbm_ranker
    import signals.news_catalyst

_register_all()
