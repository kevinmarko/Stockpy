import numpy as np
import pandas as pd
from execution.cost_model import TieredCostModel
from validation.harness import StrategyValidationHarness

def test_validation_harness_random_strategy(tmp_path):
    """
    Verify that a random coin-flip strategy has deployable=False
    and PBO is around 0.5.
    """
    np.random.seed(42)
    dates = pd.date_range("2020-01-01", periods=200)
    X = pd.DataFrame(np.random.randn(200, 2), index=dates)
    y = pd.Series(np.random.randn(200) * 0.01, index=dates)

    # 5 random strategy configurations
    def random_strategy_fn(X_train, y_train, X_test, y_test):
        return [
            {
                "params": f"config_{i}",
                # Generate random returns around 0.0
                "train_returns": pd.Series(np.random.normal(0, 0.01, len(y_train)), index=y_train.index),
                "test_returns": pd.Series(np.random.normal(0, 0.01, len(y_test)), index=y_test.index)
            }
            for i in range(5)
        ]

    cost_model = TieredCostModel()
    
    def mock_universe_fn(as_of_date):
        return ["MOCK"]

    # We use n_cpcv_splits=5 to run quickly and reliably
    harness = StrategyValidationHarness(
        strategy_fn=random_strategy_fn,
        universe_fn=mock_universe_fn,
        cost_model=cost_model,
        n_cpcv_splits=5,
        n_test_splits=1,
        reports_dir=str(tmp_path)
    )

    report = harness.run(
        start_date="2020-01-01",
        end_date="2020-10-01",
        X=X,
        y=y,
        strategy_name="Random_Strategy_Test"
    )
    
    # 1. Random coin flip should not be deployable
    assert report.deployable is False
    # 2. PBO should be around 0.5 (for 5 splits, PBO can be 0.2, 0.4, 0.6, 0.8)
    assert 0.2 <= report.pbo <= 0.8
