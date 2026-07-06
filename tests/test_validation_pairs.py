import numpy as np
import pandas as pd
import pytest
from execution.cost_model import TieredCostModel
from validation.harness import StrategyValidationHarness
from signals.pairs_trading import generate_pairs_signals

def test_validation_harness_runs_on_pairs_strategy(tmp_path):
    """
    Smoke-tests the StrategyValidationHarness on our Kalman-based pairs strategy
    using synthetic cointegrated series.
    """
    np.random.seed(42)
    n = 300
    dates = pd.date_range(start='2020-01-01', periods=n, freq='B')
    
    # 1. Create a synthetic cointegrated pair
    x = np.cumsum(np.random.normal(0, 0.5, n)) + 100.0
    spread = [0.0]
    for _ in range(n - 1):
        spread.append(0.9 * spread[-1] + np.random.normal(0, 0.1))
    spread = np.array(spread)
    y = 0.8 * x + 5.0 + spread
    
    # 2. Build X features DataFrame (containing prices for both assets)
    X = pd.DataFrame(index=dates)
    X['Y'] = y
    X['X'] = x
    
    # Target y is a dummy series (since returns are computed inside strategy_fn)
    y_series = pd.Series(0.0, index=dates)
    
    # 3. Define the strategy function for the harness
    def pairs_strategy_fn(X_train, y_train, X_test, y_test):
        trials = []
        for threshold in [1.5, 2.0]:
            train_signals = generate_pairs_signals(X_train['Y'], X_train['X'], entry_threshold=threshold)
            test_signals = generate_pairs_signals(X_test['Y'], X_test['X'], entry_threshold=threshold)
            trials.append({
                "params": f"Kalman_Pairs_Entry_{threshold}",
                "train_returns": train_signals['daily_returns'],
                "test_returns": test_signals['daily_returns'],
                "turnover": float(test_signals['turnover'].mean())
            })
        return trials
        
    cost_model = TieredCostModel()
    
    def mock_universe_fn(as_of_date):
        return ["Y", "X"]
        
    harness = StrategyValidationHarness(
        strategy_fn=pairs_strategy_fn,
        universe_fn=mock_universe_fn,
        cost_model=cost_model,
        n_cpcv_splits=5,  # Fewer splits for fast unit testing
        n_test_splits=2,
        reports_dir=str(tmp_path)
    )
    
    # Run the harness
    report = harness.run(
        start_date=str(dates[0].date()),
        end_date=str(dates[-1].date()),
        X=X,
        y=y_series,
        strategy_name="Kalman_Pairs_Validation_Test"
    )
    
    # 4. Verify outputs
    assert not np.isnan(report.sharpe)
    assert not np.isnan(report.max_dd)
    assert isinstance(report.deployable, bool)
    assert report.pbo >= 0.0
    assert report.dsr >= 0.0
