"""
InvestYo Quant Platform - Validation Metrics (DSR, PBO, and CPCV Runner)
========================================================================
Implements institutional-grade metrics to correct for backtest overfitting:
1. Standard Sharpe Ratio
2. Deflated Sharpe Ratio (DSR)
3. Probability of Backtest Overfitting (PBO)
4. CPCV Evaluation Runner
"""

import logging
import numpy as np
import pandas as pd
from scipy.stats import norm
from typing import List, Dict, Any, Tuple, Callable

# Set up module logger
logger = logging.getLogger("Validation_Metrics")

def sharpe_ratio(returns: pd.Series, freq: int = 252) -> float:
    """
    Calculates the standard annualized Sharpe Ratio.
    Assumes zero risk-free rate for simplicity.
    """
    if isinstance(returns, pd.DataFrame):
        returns = returns.squeeze()
    if not isinstance(returns, pd.Series):
        returns = pd.Series(returns)
        
    if len(returns) < 2:
        return np.nan
    mean_ret = returns.mean()
    std_ret = returns.std()
    if std_ret == 0 or np.isnan(std_ret):
        return np.nan
    return (mean_ret / std_ret) * np.sqrt(freq)

def deflated_sharpe_ratio(
    sr_observed: float,
    n_trials: int,
    sr_variance: float,
    skew: float,
    kurtosis: float,
    n_observations: int,
    freq: int = 252
) -> float:
    """
    Calculates the Deflated Sharpe Ratio (DSR) as defined by Bailey & Lopez de Prado (2014).
    
    Args:
        sr_observed: Observed Sharpe ratio (annualized).
        n_trials: Number of strategy configurations/trials tested.
        sr_variance: Variance of the annualized Sharpe ratios across the trials.
        skew: Skewness of the strategy's returns.
        kurtosis: Kurtosis of the strategy's returns.
        n_observations: Number of observations (T) in the backtest.
        freq: Frequency of the observations (e.g. 252 for daily, 12 for monthly).
    
    Returns:
        DSR value (float between 0 and 1), indicating the probability that the true SR is > 0.
    """
    if n_trials <= 1:
        return 1.0  # No selection bias if only one trial
    
    # 1. Convert annualized SR and variance to non-annualized daily/monthly equivalent
    # SR_daily = SR_annual / sqrt(freq)
    sr_hat = sr_observed / np.sqrt(freq)
    var_sr = sr_variance / freq

    # Euler-Mascheroni constant
    euler = 0.57721566490153286
    
    # 2. Estimate expected maximum Sharpe ratio under null hypothesis (SR_0)
    # Using Bailey-Lopez de Prado approximation
    z_n = norm.ppf(1.0 - 1.0 / n_trials)
    z_ne = norm.ppf(1.0 - 1.0 / (n_trials * np.e))
    # Deal with infinite values for small n_trials or edge cases
    if np.isinf(z_n) or np.isnan(z_n):
        z_n = 0.0
    if np.isinf(z_ne) or np.isnan(z_ne):
        z_ne = 0.0
        
    sr_0 = np.sqrt(var_sr) * ((1.0 - euler) * z_n + euler * z_ne)

    # 3. Calculate DSR test statistic Z
    # Z = (sr_hat - sr_0) * sqrt(T - 1) / sqrt(1 - skew * sr_hat + ((kurt - 1)/4) * sr_hat^2)
    # Note: kurtosis must be the non-excess kurtosis (so if excess kurtosis is used, add 3.0)
    # The standard scipy.stats.kurtosis returns excess, so we assume the input is non-excess.
    denominator = np.sqrt(1.0 - skew * sr_hat + ((kurtosis - 1.0) / 4.0) * (sr_hat ** 2))
    
    if denominator == 0 or np.isnan(denominator):
        return np.nan
        
    z_stat = ((sr_hat - sr_0) * np.sqrt(n_observations - 1)) / denominator
    
    return float(norm.cdf(z_stat))

def probability_of_backtest_overfitting(
    in_sample_sharpes: np.ndarray,
    out_of_sample_sharpes: np.ndarray
) -> float:
    """
    Calculates the Probability of Backtest Overfitting (PBO) using Bailey et al. (2014) method.
    
    Args:
        in_sample_sharpes: Array of shape (n_paths, n_strategies) with IS performance.
        out_of_sample_sharpes: Array of shape (n_paths, n_strategies) with OOS performance.
        
    Returns:
        PBO (float between 0 and 1), the probability that the best IS strategy performs below the median OOS.
    """
    n_paths, n_strategies = in_sample_sharpes.shape
    if n_paths == 0 or n_strategies == 0:
        return 0.0
        
    overfit_count = 0
    
    for s in range(n_paths):
        # Best strategy index in-sample for path s
        best_is_idx = np.nanargmax(in_sample_sharpes[s])
        
        # OOS performance of the best IS strategy
        oos_perf_of_best_is = out_of_sample_sharpes[s, best_is_idx]
        
        # Median OOS performance of all strategies on path s
        median_oos_perf = np.nanmedian(out_of_sample_sharpes[s])
        
        if oos_perf_of_best_is < median_oos_perf:
            overfit_count += 1
            
    return float(overfit_count) / n_paths

def run_cpcv_evaluation(
    strategy_fn: Callable[[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series], List[Dict[str, Any]]],
    X: pd.DataFrame,
    y: pd.Series,
    t1: pd.Series = None,
    n_splits: int = 10,
    n_test_splits: int = 2,
    freq: int = 252
) -> Dict[str, Any]:
    """
    Runs CPCV evaluation across all combination paths and calculates validation metrics.
    
    Args:
        strategy_fn: Callable taking (X_train, y_train, X_test, y_test) and returning a list of dicts:
                     [{"params": dict/str, "train_returns": pd.Series, "test_returns": pd.Series}]
                     for multiple strategy candidates.
        X: Features DataFrame.
        y: Targets Series.
        t1: Event end times.
    """
    from validation.purged_cv import CombinatorialPurgedCV
    
    cv = CombinatorialPurgedCV(n_splits=n_splits, n_test_splits=n_test_splits)
    
    paths_data = []
    is_sharpe_matrix = []
    oos_sharpe_matrix = []
    
    # Store all path returns for the best strategy
    best_strategy_oos_returns = []
    
    logger.info("Executing CPCV path evaluation...")
    
    for train_idx, test_idx, path_id in cv.split(X, y, t1):
        if len(train_idx) == 0:
            continue
            
        X_train, y_train = X.iloc[train_idx], y.iloc[train_idx]
        X_test, y_test = X.iloc[test_idx], y.iloc[test_idx]
        
        # Run strategy evaluation
        trials = strategy_fn(X_train, y_train, X_test, y_test)
        if not trials:
            continue
            
        is_sharpes = []
        oos_sharpes = []
        
        for trial in trials:
            is_sr = sharpe_ratio(trial["train_returns"], freq=freq)
            oos_sr = sharpe_ratio(trial["test_returns"], freq=freq)
            is_sharpes.append(is_sr if not np.isnan(is_sr) else -999.0)
            oos_sharpes.append(oos_sr if not np.isnan(oos_sr) else -999.0)
            
        is_sharpe_matrix.append(is_sharpes)
        oos_sharpe_matrix.append(oos_sharpes)
        
        # Track the best performing configuration on this path (in-sample)
        best_is_idx = np.argmax(is_sharpes)
        best_trial = trials[best_is_idx]
        
        paths_data.append({
            "path_id": path_id,
            "sharpe": oos_sharpes[best_is_idx],
            "returns": best_trial["test_returns"].tolist(),
            "params": best_trial["params"]
        })
        best_strategy_oos_returns.extend(best_trial["test_returns"].tolist())

    if not is_sharpe_matrix:
        return {"paths": [], "dsr": 0.0, "pbo": 1.0, "mean_oos_sharpe": 0.0, "distribution": np.array([])}
        
    is_sharpe_matrix = np.array(is_sharpe_matrix)
    oos_sharpe_matrix = np.array(oos_sharpe_matrix)
    
    # 1. Calculate PBO
    pbo = probability_of_backtest_overfitting(is_sharpe_matrix, oos_sharpe_matrix)
    
    # 2. Calculate DSR for the best overall selected strategy
    # Let's find the configuration that performed best overall in-sample (on average)
    mean_is_sharpes = is_sharpe_matrix.mean(axis=0)
    best_overall_idx = np.argmax(mean_is_sharpes)
    best_overall_oos_sharpes = oos_sharpe_matrix[:, best_overall_idx]
    
    # Calculate returns skew/kurtosis of the selected strategy (all merged OOS returns)
    all_oos_returns = pd.Series(best_strategy_oos_returns)
    skew = all_oos_returns.skew() if len(all_oos_returns) > 2 else 0.0
    kurt = all_oos_returns.kurtosis() + 3.0 if len(all_oos_returns) > 2 else 3.0 # convert to non-excess
    
    if np.isnan(skew): skew = 0.0
    if np.isnan(kurt): kurt = 3.0
    
    # Observed Sharpe ratio is the mean OOS Sharpe of the selected strategy
    sr_observed = np.mean(best_overall_oos_sharpes)
    n_trials = is_sharpe_matrix.shape[1]
    
    # Variance of Sharpe ratios across all trials
    sr_variance = np.var(mean_is_sharpes)
    if sr_variance == 0:
        sr_variance = 1e-6
        
    dsr = deflated_sharpe_ratio(
        sr_observed=sr_observed,
        n_trials=n_trials,
        sr_variance=sr_variance,
        skew=skew,
        kurtosis=kurt,
        n_observations=len(X),
        freq=freq
    )
    
    distribution = oos_sharpe_matrix[:, best_overall_idx]
    mean_oos_sharpe = float(np.mean(distribution))
    
    return {
        "paths": paths_data,
        "dsr": dsr,
        "pbo": pbo,
        "mean_oos_sharpe": mean_oos_sharpe,
        "distribution": distribution
    }
