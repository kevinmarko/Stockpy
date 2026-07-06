import pytest
import numpy as np
import pandas as pd
import yfinance as yf
from execution.cost_model import TieredCostModel
from validation.harness import StrategyValidationHarness

def test_validation_tsmom_spy(tmp_path):
    # 1. Download SPY data from 2000 to 2023
    df = yf.download("SPY", start="2000-01-01", end="2023-12-31", progress=False)
    assert not df.empty, "Failed to download SPY data"
    df.index = pd.to_datetime(df.index)
    
    # Squeeze columns
    close = df["Close"].squeeze()
    
    # 2. Compute features lookahead-free on full contiguous series
    roc_12m = close.shift(1) / close.shift(253) - 1.0
    roc_6m = close.shift(1) / close.shift(127) - 1.0
    
    # Use rolling 60-day standard deviation (annualized) of daily returns
    daily_ret = close.pct_change()
    vol_60d = daily_ret.shift(1).rolling(window=60).std() * np.sqrt(252)
    
    # Align and drop NaNs
    valid_idx = roc_12m.dropna().index.intersection(vol_60d.dropna().index)
    
    y = daily_ret.loc[valid_idx]
    roc_12m = roc_12m.loc[valid_idx]
    roc_6m = roc_6m.loc[valid_idx]
    vol_60d = vol_60d.loc[valid_idx]
    
    X = pd.DataFrame(index=valid_idx)
    X["ROC_12M"] = roc_12m
    X["ROC_6M"] = roc_6m
    X["Vol"] = vol_60d
    
    # 3. Precompute full strategy returns for different configurations to prevent index-shift misalignment
    rf_options = [0.0, 0.01, 0.02]
    target_vol_options = [0.10, 0.15, 0.20, 0.30]
    use_tanh_options = [True, False]
    
    precomputed_returns = {}
    
    for rf in rf_options:
        for target_vol in target_vol_options:
            for use_tanh in use_tanh_options:
                for is_12m in [True, False]:
                    roc_col = "ROC_12M" if is_12m else "ROC_6M"
                    roc = X[roc_col]
                    vol = X["Vol"]
                    
                    diff = roc - rf
                    sign_val = np.sign(diff)
                    
                    vol_safe = np.where(vol > 0, vol, 0.20)
                    vol_scalar = np.minimum(1.0, target_vol / vol_safe)
                    
                    if use_tanh:
                        strength_factor = np.tanh(np.abs(roc) * 3)
                    else:
                        strength_factor = 1.0
                        
                    score = sign_val * vol_scalar * strength_factor
                    
                    # Compute contiguous daily strategy returns
                    full_strat_ret = score.shift(1) * y
                    full_strat_ret = full_strat_ret.fillna(0.0)
                    
                    config_name = f"TSMOM_{'12M' if is_12m else '6M'}_rf{rf}_vol{target_vol}_tanh{use_tanh}"
                    precomputed_returns[config_name] = full_strat_ret

    def tsmom_strategy(X_train, y_train, X_test, y_test):
        configs = []
        for name, full_returns in precomputed_returns.items():
            configs.append({
                "params": name,
                "train_returns": full_returns.loc[y_train.index],
                "test_returns": full_returns.loc[y_test.index],
                "turnover": 0.005  # Low turnover (~0.5% daily trading volume)
            })
        return configs

    cost_model = TieredCostModel()
    
    def mock_universe_fn(as_of_date):
        return ["SPY"]

    harness = StrategyValidationHarness(
        strategy_fn=tsmom_strategy,
        universe_fn=mock_universe_fn,
        cost_model=cost_model,
        n_cpcv_splits=10,
        n_test_splits=2,
        reports_dir=str(tmp_path)
    )

    report = harness.run(
        start_date=str(valid_idx[0].date()),
        end_date=str(valid_idx[-1].date()),
        X=X,
        y=y,
        strategy_name="TSMOM_Harness_Test"
    )
    
    print(f"\n--- TSMOM VALIDATION HARNESS REPORT ---")
    print(f"Sharpe Ratio (net): {report.sharpe:.3f}")
    print(f"Sortino Ratio: {report.sortino:.3f}")
    print(f"Max Drawdown: {report.max_dd*100:.2f}%")
    print(f"DSR: {report.dsr:.4f}")
    print(f"PBO: {report.pbo:.4f}")
    print(f"Deployable: {report.deployable}")
    
    assert report.sharpe >= 0.3
    assert report.deployable is True
