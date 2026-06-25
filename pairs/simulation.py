import pandas as pd
import numpy as np
import logging
from datetime import datetime
import backtrader as bt
from execution.cost_model import TieredCostModel, TieredCostCommissionInfo
from simulation_engine import print_survivorship_warning_for_backtest

logger = logging.getLogger("Pairs_Simulation")

class PairsTradingBTStrategy(bt.Strategy):
    """
    Event-driven Backtrader strategy for pairs trading.
    Takes a pre-computed signals DataFrame and executes trades accordingly.
    """
    params = (
        ('signals_df', None),  # DataFrame containing 'position' and 'beta' indexed by date
    )

    def __init__(self):
        self.y_data = self.datas[0]
        self.x_data = self.datas[1]
        self.order_y = None
        self.order_x = None

    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return
        if order.status in [order.Completed]:
            action = "BUY" if order.isbuy() else "SELL"
            logger.debug(f"{action} EXECUTED - Asset: {order.data._name}, Price: {order.executed.price:.2f}, Size: {order.executed.size}, Comm: {order.executed.comm:.2f}")
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            logger.warning(f"Order rejected/canceled/margin for {order.data._name} - status: {order.status}")
        
        if order.data == self.y_data:
            self.order_y = None
        elif order.data == self.x_data:
            self.order_x = None

    def next(self):
        # Get the current datetime from the first data feed
        dt = self.y_data.datetime.date(0)
        ts = pd.Timestamp(dt)
        
        if self.p.signals_df is None or ts not in self.p.signals_df.index:
            return
            
        row = self.p.signals_df.loc[ts]
        target_pos = row['position']
        beta = row['beta']
        
        if pd.isna(target_pos) or pd.isna(beta):
            # Close everything
            self.order_target_percent(self.y_data, 0.0)
            self.order_target_percent(self.x_data, 0.0)
            return
            
        # Rebalancing weights based on the position signal
        if target_pos == 1.0:
            # Long Y, Short X
            # Weights sum to 1.0 in absolute value (gross leverage = 1.0)
            w_y = 1.0 / (1.0 + abs(beta))
            w_x = -beta / (1.0 + abs(beta))
        elif target_pos == -1.0:
            # Short Y, Long X
            w_y = -1.0 / (1.0 + abs(beta))
            w_x = beta / (1.0 + abs(beta))
        else:
            w_y = 0.0
            w_x = 0.0
            
        # Place target percent orders
        self.order_y = self.order_target_percent(self.y_data, w_y)
        self.order_x = self.order_target_percent(self.x_data, w_x)

def run_pairs_backtrader_simulation(
    y_series: pd.Series,
    x_series: pd.Series,
    signals_df: pd.DataFrame,
    initial_cash: float = 100000.0,
    y_name: str = "Asset_Y",
    x_name: str = "Asset_X"
) -> tuple[float, pd.Series]:
    """
    Runs the event-driven Backtrader simulation for a pair of assets.
    
    Returns:
    - final_value: float
    - daily_returns: pd.Series
    """
    # 1. Print survivorship warning
    print_survivorship_warning_for_backtest(y_series.index)
    
    # 2. Setup Cerebro
    cerebro = bt.Cerebro()
    cerebro.addstrategy(PairsTradingBTStrategy, signals_df=signals_df)
    
    # 3. Add data feeds
    # Construct required Open-High-Low-Close-Volume DataFrame for Backtrader
    # Since we only have closing prices, we map it to OHLC with dummy/realized bounds
    df_y = pd.DataFrame(index=y_series.index)
    df_y['open'] = y_series
    df_y['high'] = y_series
    df_y['low'] = y_series
    df_y['close'] = y_series
    df_y['volume'] = 100000.0
    
    df_x = pd.DataFrame(index=x_series.index)
    df_x['open'] = x_series
    df_x['high'] = x_series
    df_x['low'] = x_series
    df_x['close'] = x_series
    df_x['volume'] = 100000.0
    
    data_y = bt.feeds.PandasData(dataname=df_y)
    data_x = bt.feeds.PandasData(dataname=df_x)
    
    cerebro.adddata(data_y, name=y_name)
    cerebro.adddata(data_x, name=x_name)
    
    # 4. Set Cash and Commission Model
    cerebro.broker.setcash(initial_cash)
    
    # Use institutional tiered cost model
    model = TieredCostModel()
    comm_info = TieredCostCommissionInfo(
        tiered_model=model,
        market_cap=None,
        order_type='market'
    )
    cerebro.broker.addcommissioninfo(comm_info)
    
    # Add TimeReturn analyzer
    cerebro.addanalyzer(bt.analyzers.TimeReturn, _name='timereturn')
    
    # Run simulation
    logger.info(f"Starting Backtrader Pairs Simulation ({y_name}-{x_name}). Cash: ${initial_cash:,.2f}")
    results = cerebro.run()
    
    final_value = cerebro.broker.getvalue()
    logger.info(f"Finished Backtrader Pairs Simulation. Final Value: ${final_value:,.2f}")
    
    # Extract daily returns
    timereturn = results[0].analyzers.timereturn.get_analysis()
    daily_returns_series = pd.Series(timereturn)
    daily_returns_series.index = pd.to_datetime(daily_returns_series.index)
    
    return final_value, daily_returns_series
