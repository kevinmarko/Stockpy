import pandas as pd
import numpy as np
import backtrader as bt
from execution.cost_model import TieredCostModel, TieredCostCommissionInfo

class BuyAndHoldStrategy(bt.Strategy):
    def start(self):
        self.order = None

    def notify_order(self, order):
        if order.status in [order.Completed, order.Canceled, order.Margin]:
            self.order = None

    def next(self):
        if not self.position and not self.order:
            self.order = self.buy(size=500)

class DailyRebalanceStrategy(bt.Strategy):
    def start(self):
        self.order = None

    def notify_order(self, order):
        if order.status in [order.Completed, order.Canceled, order.Margin]:
            self.order = None

    def next(self):
        if self.order:
            return
        if self.position:
            # Sell and immediately buy again
            self.sell(size=self.position.size)
            self.buy(size=500)
        else:
            self.buy(size=500)


def test_cost_integration_backtest_comparison():
    """
    Verify that DailyRebalanceStrategy incurs significantly higher costs 
    and results in a lower final portfolio value than BuyAndHoldStrategy.
    """
    # Create synthetic dataset of 50 bars
    np.random.seed(42)
    dates = pd.date_range(start='2024-01-01', periods=50, freq='B')
    price = 100.0 + np.cumsum(np.random.normal(0.1, 1.0, len(dates)))
    
    df = pd.DataFrame({
        'open': price,
        'high': price + 1.0,
        'low': price - 1.0,
        'close': price,
        'volume': 100000
    }, index=dates)

    # 1. Run Buy and Hold
    cerebro_bh = bt.Cerebro()
    cerebro_bh.addstrategy(BuyAndHoldStrategy)
    data_bh = bt.feeds.PandasData(dataname=df)
    cerebro_bh.adddata(data_bh)
    cerebro_bh.broker.setcash(100000.0)
    model = TieredCostModel()
    comm_bh = TieredCostCommissionInfo(tiered_model=model, market_cap=None, order_type='market')
    cerebro_bh.broker.addcommissioninfo(comm_bh)
    cerebro_bh.run()
    bh_final = cerebro_bh.broker.getvalue()

    # 2. Run Daily Rebalance
    cerebro_dr = bt.Cerebro()
    cerebro_dr.addstrategy(DailyRebalanceStrategy)
    data_dr = bt.feeds.PandasData(dataname=df)
    cerebro_dr.adddata(data_dr)
    cerebro_dr.broker.setcash(100000.0)
    comm_dr = TieredCostCommissionInfo(tiered_model=model, market_cap=None, order_type='market')
    cerebro_dr.broker.addcommissioninfo(comm_dr)
    cerebro_dr.run()
    dr_final = cerebro_dr.broker.getvalue()

    # Daily rebalancing must result in lower returns due to higher friction costs
    assert dr_final < bh_final, f"Daily rebalancing final value ({dr_final}) should be less than buy-and-hold ({bh_final})"
