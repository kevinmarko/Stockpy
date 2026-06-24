import numpy as np
import pandas as pd
import pytest
from signals.pairs_trading import generate_pairs_signals
from pairs.simulation import run_pairs_backtrader_simulation

def test_pairs_backtrader_simulation():
    """
    Test that the Backtrader pairs trading backtester runs successfully
    and outputs the final portfolio value and a series of returns.
    """
    np.random.seed(42)
    n = 200
    dates = pd.date_range(start='2020-01-01', periods=n, freq='B')
    
    # 1. Create a synthetic cointegrated pair
    x = np.cumsum(np.random.normal(0, 0.5, n)) + 100.0
    spread = [0.0]
    for _ in range(n - 1):
        spread.append(0.9 * spread[-1] + np.random.normal(0, 0.1))
    spread = np.array(spread)
    y = 0.8 * x + 5.0 + spread
    
    y_series = pd.Series(y, index=dates)
    x_series = pd.Series(x, index=dates)
    
    # 2. Generate signals
    signals_df = generate_pairs_signals(y_series, x_series)
    
    # 3. Run simulation
    final_val, daily_returns = run_pairs_backtrader_simulation(
        y_series,
        x_series,
        signals_df,
        initial_cash=100000.0,
        y_name="AssetY",
        x_name="AssetX"
    )
    
    # 4. Assertions
    assert isinstance(final_val, float)
    assert final_val > 0.0
    assert isinstance(daily_returns, pd.Series)
    assert len(daily_returns) == n
    assert not daily_returns.isna().all()
