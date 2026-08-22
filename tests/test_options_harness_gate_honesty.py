import pytest
import pandas as pd
import numpy as np
from validation.options_harness import OptionsValidationHarness

def test_harness_thin_sample_dsr():
    harness = OptionsValidationHarness()
    df = pd.DataFrame({
        "Close": [100.0, 101.0, 102.0],
        "High": [101.0, 102.0, 103.0],
        "Low": [99.0, 100.0, 101.0],
    }, index=pd.date_range("2020-01-01", periods=3))
    res = harness.run_backtest(strategy="Iron Condor", ticker="SPY", start_date="2020-01-01", end_date="2020-01-03", price_df=df)
    if len(res.trades) < 30:
        assert pd.isna(res.dsr) or np.isnan(res.dsr)

def test_harness_fabricated_metrics_removed():
    harness = OptionsValidationHarness()
    df = pd.DataFrame({
        "Close": [100.0, 100.0],
        "High": [100.0, 100.0],
        "Low": [100.0, 100.0],
    }, index=pd.date_range("2020-01-01", periods=2))
    res = harness.run_backtest(strategy="Iron Condor", ticker="SPY", start_date="2020-01-01", end_date="2020-01-02", price_df=df)
    if not res.trades:
        assert pd.isna(res.sortino_ratio) or np.isnan(res.sortino_ratio)
        assert pd.isna(res.max_drawdown_pct) or np.isnan(res.max_drawdown_pct) or res.max_drawdown_pct == 0.0
        assert pd.isna(res.profit_factor) or np.isnan(res.profit_factor) or np.isinf(res.profit_factor)
