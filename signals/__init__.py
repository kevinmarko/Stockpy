"""
InvestYo Quant Platform - Signals Package
==========================================
Decoupled signal abstractions, registry, aggregator, and standard modules.
"""

from signals.base import SignalContext, SignalOutput, SignalModule
from signals.registry import SignalRegistry, global_registry
from signals.aggregator import SignalAggregator

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
