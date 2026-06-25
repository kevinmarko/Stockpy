import numpy as np
import pandas as pd
from pairs.kalman_hedge import KalmanHedgeRatio
from pairs.cointegration import compute_half_life, rolling_adf_pvalue

def generate_pairs_signals(
    y_prices: pd.Series,
    x_prices: pd.Series,
    entry_threshold: float = 2.0,
    exit_threshold: float = 0.0,
    stop_loss_threshold: float = 4.0,
    adf_window: int = 60,
    adf_exit_threshold: float = 0.10
) -> pd.DataFrame:
    """
    Generates trading signals for a pair of cointegrated assets Y and X.
    Uses a Kalman Filter to dynamically estimate the hedge ratio (beta) and intercept (alpha).
    
    Signals:
    - Entry: |Z_t| > entry_threshold
    - Exit: Z_t crosses exit_threshold (0) OR cointegration breaks (rolling ADF p > adf_exit_threshold)
      OR stop loss hit (|Z_t| > stop_loss_threshold)
    
    Returns:
    - pd.DataFrame containing columns:
      y, x, alpha, beta, spread, z_score, rolling_p, position, daily_returns, turnover
    """
    kh = KalmanHedgeRatio()
    hedge_df = kh.estimate_hedge_ratio(y_prices, x_prices)
    
    idx = hedge_df.index
    y = y_prices.loc[idx]
    x = x_prices.loc[idx]
    alpha = hedge_df['alpha']
    beta = hedge_df['beta']
    
    # Spread: u_t = y_t - (alpha_t + beta_t * x_t)
    spread = y - (alpha + beta * x)
    
    # Calculate half-life of mean reversion
    hl = compute_half_life(spread)
    if np.isinf(hl) or np.isnan(hl) or hl <= 0:
        hl = 20.0  # Default fallback
        
    hl_int = int(max(5, min(60, np.round(hl))))
    zscore_window = int(2 * hl_int)
    
    # Rolling z-score of spread
    spread_mean = spread.rolling(window=zscore_window, min_periods=zscore_window // 2).mean()
    spread_std = spread.rolling(window=zscore_window, min_periods=zscore_window // 2).std()
    z_score = (spread - spread_mean) / spread_std
    
    # Rolling adf p-value
    rolling_p = rolling_adf_pvalue(spread, window=adf_window)
    
    # State machine for generating positions
    positions = pd.Series(0.0, index=idx)
    current_pos = 0.0
    
    for t in range(len(idx)):
        if pd.isna(z_score.iloc[t]) or pd.isna(rolling_p.iloc[t]):
            positions.iloc[t] = 0.0
            continue
            
        z = z_score.iloc[t]
        p = rolling_p.iloc[t]
        
        if current_pos == 0.0:
            if z > entry_threshold:
                current_pos = -1.0  # Short Y, Long X
            elif z < -entry_threshold:
                current_pos = 1.0   # Long Y, Short X
        else:
            should_exit = False
            if current_pos == 1.0:
                if z >= exit_threshold or p > adf_exit_threshold or z < -stop_loss_threshold:
                    should_exit = True
            elif current_pos == -1.0:
                if z <= -exit_threshold or p > adf_exit_threshold or z > stop_loss_threshold:
                    should_exit = True
                    
            if should_exit:
                current_pos = 0.0
                
        positions.iloc[t] = current_pos
        
    # Calculate daily returns
    # Daily return on capital:
    # Capital = y_{t-1} + |beta_{t-1}| * x_{t-1}
    # Return = position_{t-1} * ((y_t - y_{t-1}) - beta_{t-1} * (x_t - x_{t-1})) / Capital
    y_diff = y.diff()
    x_diff = x.diff()
    
    y_lag = y.shift(1)
    x_lag = x.shift(1)
    beta_lag = beta.shift(1)
    pos_lag = positions.shift(1)
    
    capital = y_lag + beta_lag.abs() * x_lag
    raw_return = pos_lag * (y_diff - beta_lag * x_diff) / capital
    daily_returns = raw_return.fillna(0.0)
    
    # Calculate turnover
    # Change in position represents trading activity
    # If position changes, turnover is the total size traded divided by capital
    # Trade size of Y is 1 unit of Y, trade size of X is beta units of X.
    pos_diff = positions.diff().abs()
    trade_size = pos_diff * (y + beta.abs() * x)
    turnover = (trade_size / capital).fillna(0.0)
    
    signals_df = pd.DataFrame(index=idx)
    signals_df['y'] = y
    signals_df['x'] = x
    signals_df['alpha'] = alpha
    signals_df['beta'] = beta
    signals_df['spread'] = spread
    signals_df['z_score'] = z_score
    signals_df['rolling_p'] = rolling_p
    signals_df['position'] = positions
    signals_df['daily_returns'] = daily_returns
    signals_df['turnover'] = turnover
    
    return signals_df
