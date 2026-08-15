import numpy as np

def compute_trading_trajectory(
    total_shares: float,
    total_time: float,
    n_intervals: int,
    volatility: float,
    temp_impact: float,
    perm_impact: float,
    risk_aversion: float
) -> dict:
    """
    Computes the optimal trading trajectory using the continuous approximation
    of the Almgren-Chriss model.
    
    Args:
        total_shares: Total number of shares to execute.
        total_time: Total time for execution.
        n_intervals: Number of trading intervals.
        volatility: Volatility of the asset (sigma).
        temp_impact: Temporary market impact coefficient (eta).
        perm_impact: Permanent market impact coefficient (gamma).
        risk_aversion: Risk aversion parameter (lambda).
        
    Returns:
        dict: A dictionary containing:
            - 'trajectory': list of remaining shares at each step
            - 'trade_list': list of shares to trade at each interval
            - 'expected_shortfall': Expected cost of the strategy
            - 'variance': Variance of the strategy cost
    """
    if n_intervals <= 0 or total_time <= 0:
        raise ValueError("n_intervals and total_time must be positive")
    if temp_impact <= 0:
        raise ValueError("temp_impact must be positive")

    tau = total_time / n_intervals

    trajectory = [total_shares]
    trade_list = []

    if risk_aversion <= 0:
        # TWAP
        for k in range(1, n_intervals + 1):
            x_k = total_shares * (1.0 - k / n_intervals)
            n_k = trajectory[-1] - x_k
            trade_list.append(n_k)
            trajectory.append(x_k)
    else:
        kappa = np.sqrt(risk_aversion * (volatility ** 2) / temp_impact)
        for k in range(1, n_intervals + 1):
            t_k = k * tau
            if k == n_intervals:
                x_k = 0.0
            else:
                x_k = total_shares * np.sinh(kappa * (total_time - t_k)) / np.sinh(kappa * total_time)
            
            n_k = trajectory[-1] - x_k
            trade_list.append(n_k)
            trajectory.append(x_k)

    expected_shortfall = 0.5 * perm_impact * (total_shares ** 2) + \
                         (temp_impact - 0.5 * perm_impact * tau) * \
                         np.sum((np.array(trade_list) ** 2) / tau)
                         
    x_k_array = np.array(trajectory[1:])
    variance = (volatility ** 2) * np.sum(tau * (x_k_array ** 2))

    return {
        "trajectory": trajectory,
        "trade_list": trade_list,
        "expected_shortfall": float(expected_shortfall),
        "variance": float(variance)
    }
