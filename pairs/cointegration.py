import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.tsa.stattools import coint, adfuller
from dataclasses import dataclass

@dataclass
class Pair:
    ticker1: str
    ticker2: str
    p_value: float
    half_life: float

def compute_half_life(spread_series: pd.Series) -> float:
    """
    Calculate the half-life of mean reversion using an AR(1) model on the spread:
    u_t - u_{t-1} = lambda * u_{t-1} + epsilon_t
    where half-life HL = -ln(2) / ln(1 + lambda)
    """
    if len(spread_series) < 10:
        return float('inf')
        
    spread_lag = spread_series.shift(1).dropna()
    spread_diff = spread_series.diff().dropna()
    
    # Align indices
    common_idx = spread_lag.index.intersection(spread_diff.index)
    if len(common_idx) < 10:
        return float('inf')
        
    x = spread_lag.loc[common_idx].values
    y = spread_diff.loc[common_idx].values
    
    try:
        # Run OLS with a constant
        x_const = sm.add_constant(x)
        # Note: If x is collinear, add_constant might return single column or params length might be 1.
        # So check the number of parameters returned.
        model = sm.OLS(y, x_const).fit()
        if len(model.params) < 2:
            return float('inf')
        lambda_val = model.params[1]
    except Exception:
        return float('inf')
        
    # Check for mean reversion
    if lambda_val >= 0 or (1 + lambda_val) <= 0:
        return float('inf')
        
    hl = -np.log(2) / np.log(1 + lambda_val)
    return hl

def find_cointegrated_pairs(price_df: pd.DataFrame, p_threshold: float = 0.05, max_pairs: int = 20) -> list[Pair]:
    """
    Runs the Engle-Granger cointegration test for all ticker combinations.
    Filters by p-value threshold and ensures half-life is between 5 and 60 days.
    """
    pairs = []
    tickers = price_df.columns
    n = len(tickers)
    
    for i in range(n):
        for j in range(i + 1, n):
            t1, t2 = tickers[i], tickers[j]
            # Drop NaNs to align both price series
            df_pair = price_df[[t1, t2]].dropna()
            if len(df_pair) < 60:
                continue
                
            try:
                # Run coint from statsmodels (tests null of no-cointegration)
                _, p_val, _ = coint(df_pair[t1], df_pair[t2], trend='c')
            except Exception:
                continue
                
            if p_val < p_threshold:
                # OLS to get spread residuals
                try:
                    y = df_pair[t1]
                    x = sm.add_constant(df_pair[t2])
                    model = sm.OLS(y, x).fit()
                    spread = model.resid
                    hl = compute_half_life(spread)
                except Exception:
                    continue
                    
                if 5 <= hl <= 60:
                    pairs.append(Pair(ticker1=t1, ticker2=t2, p_value=p_val, half_life=hl))
                    
    # Sort by p-value ascending
    pairs.sort(key=lambda p: p.p_value)
    return pairs[:max_pairs]

def rolling_adf_pvalue(spread_series: pd.Series, window: int = 60) -> pd.Series:
    """
    Computes ADF unit-root p-value over a rolling window.
    """
    pvalues = pd.Series(index=spread_series.index, dtype=float)
    
    for i in range(len(spread_series)):
        if i < window - 1:
            pvalues.iloc[i] = np.nan
            continue
        window_spread = spread_series.iloc[i - window + 1 : i + 1]
        try:
            # Run adfuller
            res = adfuller(window_spread, autolag='AIC')
            pvalues.iloc[i] = res[1]
        except Exception:
            pvalues.iloc[i] = np.nan
            
    return pvalues
