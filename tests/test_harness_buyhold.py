import pytest  # type: ignore
from datetime import date
from execution.cost_model import TieredCostModel
from validation.harness import StrategyValidationHarness

@pytest.mark.network
def test_validation_harness_buy_and_hold_spy(tmp_path):
    """
    Verify that Buy-and-Hold SPY during a strongly trending period (2013-2014)
    passes the validation harness checks and returns deployable=True.
    """
    def spy_bh_strategy(X_train, y_train, X_test, y_test):
        return [
            {
                "params": "SPY_Buy_and_Hold",
                "train_returns": y_train,
                "test_returns": y_test
            }
        ]

    cost_model = TieredCostModel()
    
    def mock_universe_fn(as_of_date):
        return ["SPY"]

    harness = StrategyValidationHarness(
        strategy_fn=spy_bh_strategy,
        universe_fn=mock_universe_fn,
        cost_model=cost_model,
        reports_dir=str(tmp_path)
    )
    
    # Run on 2013-2014 where SPY was strongly trending up with low drawdowns
    report = harness.run(
        start_date="2013-01-01",
        end_date="2014-12-31",
        strategy_name="SPY_BH_Test"
    )
    
    assert report.deployable is True, (
        f"SPY Buy-and-Hold should be deployable in 2013-2014. "
        f"Sharpe: {report.sharpe:.2f}, Max DD: {report.max_dd*100:.2f}%, "
        f"DSR: {report.dsr*100:.2f}%, PBO: {report.pbo*100:.2f}%"
    )
