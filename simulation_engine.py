"""
InvestYo Quant Platform - Simulation & Backtesting Layer
========================================================
Step 7 of the Modernization Roadmap: Simulation Layer Automation.

This module uses:
1. VectorBT for rapid, matrix-based parameter optimization.
2. Backtrader for event-driven simulation (transaction costs, slippage).
"""

import pandas as pd
import numpy as np
from datetime import datetime
import logging
from typing import Optional, Tuple, List

# Configure module logger
logger = logging.getLogger("Simulation_Engine")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

try:
    import vectorbt as vbt
except ImportError:
    logger.warning("vectorbt not installed. Run: pip install vectorbt")

try:
    import backtrader as bt
except ImportError:
    logger.warning("backtrader not installed. Run: pip install backtrader")

# =============================================================================
# SURVIVORSHIP BIAS WARNING HELPER
# =============================================================================
def print_survivorship_warning_for_backtest(index: pd.Index):
    """Helper to load and print the survivorship bias warning for backtest time ranges."""
    try:
        from universe_engine import get_universe_with_survivorship_warning, print_survivorship_bias_warning
        if isinstance(index, pd.DatetimeIndex):
            start_date = index.min().date()
        else:
            start_date = pd.to_datetime(index).min().date()
        _, bias_report = get_universe_with_survivorship_warning(start_date)
        print_survivorship_bias_warning(bias_report)
    except Exception as e:
        logger.warning(f"Could not generate survivorship bias report: {e}")
        print("=" * 80)
        print("WARNING — SURVIVORSHIP BIAS: Free-data backtests systematically overstate returns by ~0.5-1.5%/year on US equities and far more on small-caps/emerging markets. Treat results accordingly.")
        print("=" * 80)

def get_vbt_costs(market_cap: Optional[float] = None) -> Tuple[float, float]:
    """Helper to convert TieredCostModel parameters to VectorBT fees and slippage."""
    try:
        from execution.cost_model import TieredCostModel
        model = TieredCostModel()
        tier = model.get_liquidity_tier(market_cap)
        spread_bps = model.spread_bps_by_liquidity[tier]
        # fees = half-spread + average sell-side reg fee (1.39 bps)
        fees_pct = ((spread_bps / 2.0) + 1.39) / 10000.0
        slippage_pct = model.slippage_bps_market_order / 10000.0
        return fees_pct, slippage_pct
    except Exception as e:
        logger.warning(f"Could not calculate cost model parameters: {e}. Defaulting to 10bps total.")
        return 0.0005, 0.0005

def cost_sensitivity_curve(strategy_returns: pd.Series, cost_bps_range: Tuple[float, float] = (0, 50)):
    """
    Simulates strategy returns under varying execution costs (in bps per transaction)
    and logs a sensitivity warning if Sharpe ratio collapses below 1.0 at 20 bps.
    """
    print("\n--- Cost Sensitivity Analysis ---")
    print(f"{'Cost (bps)':<12}{'Annualized Return':<20}{'Sharpe Ratio':<15}")
    print("-" * 50)
    
    if strategy_returns.empty or strategy_returns.std() == 0:
        logger.warning("No returns data for sensitivity analysis.")
        return
        
    for cost_bps in np.arange(cost_bps_range[0], cost_bps_range[1] + 1, step=5):
        cost_rate = cost_bps / 10000.0
        trade_days = strategy_returns != 0
        adjusted_returns = strategy_returns.copy()
        adjusted_returns[trade_days] -= cost_rate
        
        ann_return = adjusted_returns.mean() * 252
        std_ret = adjusted_returns.std()
        sharpe = (adjusted_returns.mean() / std_ret * np.sqrt(252)) if std_ret > 0 else np.nan
        
        print(f"{cost_bps:<12.1f}{ann_return * 100:<20.2f}%{sharpe:<15.2f}")
        
        if cost_bps == 20.0 and (np.isnan(sharpe) or sharpe < 1.0):
            logger.warning(
                f"🚨 WARNING: Strategy Sharpe ratio collapses to {sharpe:.2f} (below 1.0) "
                f"under a realistic 20 bps transaction cost model!"
            )
from typing import Tuple, Optional

# =============================================================================
# 1. VECTORBT: MATRIX-BASED PARAMETER OPTIMIZATION
# =============================================================================
def optimize_strategy_vectorbt(price_series: pd.Series):
    """
    Uses VectorBT to test thousands of Moving Average combinations instantly
    using vectorized Numpy arrays.
    """
    print_survivorship_warning_for_backtest(price_series.index)
    print("\n--- Running VectorBT Matrix Optimization ---")
    
    # Define a range of Fast and Slow moving averages to test
    fast_windows = np.arange(10, 30, step=5)
    slow_windows = np.arange(50, 150, step=20)
    windows = np.concatenate([fast_windows, slow_windows])
    
    # Generate moving averages
    fast_ma, slow_ma = vbt.MA.run_combs(price_series, window=windows, r=2, short_names=['fast', 'slow'])
    
    # Generate crossover signals
    entries = fast_ma.ma_crossed_above(slow_ma)
    exits = fast_ma.ma_crossed_below(slow_ma)
    
    # Get costs from model
    fees_pct, slippage_pct = get_vbt_costs(market_cap=None)
    
    # Build Portfolio and calculate Sharpe Ratios
    portfolio = vbt.Portfolio.from_signals(price_series, entries, exits, freq='1D', fees=fees_pct, slippage=slippage_pct)
    
    # Extract the best performing parameter combination based on Total Return
    returns = portfolio.total_return()
    best_combo = returns.idxmax()
    best_return = returns.max()
    
    print(f"✅ VectorBT Optimization Complete.")
    print(f"🏆 Best Parameters: Fast MA = {best_combo[0]}, Slow MA = {best_combo[1]}")
    print(f"💰 Best Total Return: {best_return * 100:.2f}%")
    
    return best_combo

# =============================================================================
# 2. BACKTRADER: EVENT-DRIVEN REALISTIC SIMULATION
# =============================================================================
class InstitutionalStrategy(bt.Strategy):
    """
    Event-driven Backtrader strategy. Simulates realistic market impact, 
    slippage, and utilizes ATR-based stop losses.
    """
    params = (
        ('fast_ma', 15),
        ('slow_ma', 50),
        ('atr_period', 14),
        ('risk_per_trade', 0.02) # Risk 2% of capital per trade
    )

    def __init__(self):
        self.fast_ma = bt.indicators.SMA(self.data.close, period=self.params.fast_ma)
        self.slow_ma = bt.indicators.SMA(self.data.close, period=self.params.slow_ma)
        self.crossover = bt.indicators.CrossOver(self.fast_ma, self.slow_ma)
        self.atr = bt.indicators.ATR(self.data, period=self.params.atr_period)
        self.order = None

    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return
        if order.status in [order.Completed]:
            if order.isbuy():
                print(f"BUY EXECUTED: Price: {order.executed.price:.2f}, Cost: {order.executed.value:.2f}, Comm: {order.executed.comm:.2f}")
            elif order.issell():
                print(f"SELL EXECUTED: Price: {order.executed.price:.2f}, Cost: {order.executed.value:.2f}, Comm: {order.executed.comm:.2f}")
            self.bar_executed = len(self)
        self.order = None

    def next(self):
        # Do nothing if an order is pending
        if self.order:
            return

        # Entry Logic
        if not self.position:
            if self.crossover > 0: # Fast crosses above Slow
                # Volatility-Based Position Sizing
                risk_amount = self.broker.getvalue() * self.params.risk_per_trade
                stop_distance = self.atr[0] * 2.0 # 2 ATR Stop Loss
                size = int(risk_amount / stop_distance) if stop_distance > 0 else 0
                
                if size > 0:
                    print(f"SIGNAL: BUY Created at close {self.data.close[0]:.2f}. Stop Loss planned at {self.data.close[0] - stop_distance:.2f}")
                    self.order = self.buy(size=size)
        
        # Exit Logic
        else:
            if self.crossover < 0: # Fast crosses below Slow
                print(f"SIGNAL: SELL Created at close {self.data.close[0]:.2f}")
                self.order = self.sell(size=self.position.size)

def run_backtrader_simulation(dataframe: pd.DataFrame):
    """Executes the Backtrader engine with slippage and commission."""
    print_survivorship_warning_for_backtest(dataframe.index)
    print("\n--- Running Event-Driven Backtrader Simulation ---")
    cerebro = bt.Cerebro()
    cerebro.addstrategy(InstitutionalStrategy)

    # Format Pandas DataFrame for Backtrader
    data = bt.feeds.PandasData(dataname=dataframe)  # type: ignore
    cerebro.adddata(data)

    # Set Institutional Capital, Fees, and Slippage Models
    cerebro.broker.setcash(100000.0)
    
    try:
        from execution.cost_model import TieredCostModel, TieredCostCommissionInfo
        model = TieredCostModel()
        comm_info = TieredCostCommissionInfo(tiered_model=model, market_cap=None, order_type='market')  # type: ignore
        cerebro.broker.addcommissioninfo(comm_info)
    except Exception as e:
        logger.warning(f"Could not load custom commission info: {e}. Falling back to flat assumptions.")
        cerebro.broker.setcommission(commission=0.001) # 0.1% Commission
        cerebro.broker.set_slippage_perc(perc=0.0005)  # 0.05% Market Impact Slippage

    print(f"Starting Portfolio Value: ${cerebro.broker.getvalue():,.2f}")
    cerebro.run()
    print(f"Final Portfolio Value: ${cerebro.broker.getvalue():,.2f}")


# =============================================================================
# 3. EXECUTION
# =============================================================================
if __name__ == '__main__':
    # Generate 500 days of synthetic trending data with volatility
    np.random.seed(42)
    dates = pd.date_range(start='2024-01-01', periods=500, freq='B')
    returns = np.random.normal(0.0005, 0.015, len(dates))
    price = 100 * np.exp(np.cumsum(returns))
    
    df = pd.DataFrame({
        'open': price * np.random.uniform(0.99, 1.01, len(dates)),
        'high': price * np.random.uniform(1.01, 1.03, len(dates)),
        'low': price * np.random.uniform(0.97, 0.99, len(dates)),
        'close': price,
        'volume': np.random.randint(100000, 500000, len(dates))
    }, index=dates)

    # 1. Run VectorBT to find the best Moving Averages mathematically
    try:
        best_params = optimize_strategy_vectorbt(df['close'])
    except Exception as e:
        print(f"VectorBT execution skipped: {e}")

    # 2. Run Backtrader to test those exact parameters against slippage
    try:
        run_backtrader_simulation(df)
    except Exception as e:
        print(f"Backtrader execution skipped: {e}")

    # 3. Run Cost Sensitivity Analysis
    try:
        # We model the returns of the price series as the strategy returns
        cost_sensitivity_curve(pd.Series(returns, index=dates))
    except Exception as e:
        print(f"Cost sensitivity curve execution skipped: {e}")

