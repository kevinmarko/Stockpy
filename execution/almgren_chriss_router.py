"""InvestYo Quant Platform — Almgren-Chriss Optimal Execution Router.

Computes optimal trading trajectories and schedule slicing to minimize market impact
and volatility risk under the Almgren-Chriss execution framework.
"""

import numpy as np
from typing import List, Dict

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
    if total_shares <= 0:
        raise ValueError("total_shares must be positive")
    if n_intervals <= 0 or total_time <= 0:
        raise ValueError("n_intervals and total_time must be positive")
    if temp_impact <= 0:
        raise ValueError("temp_impact must be positive")
    if volatility < 0:
        raise ValueError("volatility cannot be negative")
    if perm_impact < 0:
        raise ValueError("perm_impact cannot be negative")

    tau = total_time / n_intervals

    # AC(2001) well-posedness condition: the "effective" temporary-impact coefficient
    # eta_tilde = eta - 0.5*gamma*tau must be strictly positive for the quadratic cost
    # function to be convex. A degenerate parameterization (temp_impact too small
    # relative to perm_impact*tau) would otherwise silently produce a nonsensical
    # (possibly negative) expected_shortfall with no validation catching it.
    effective_temp_impact = temp_impact - 0.5 * perm_impact * tau
    if effective_temp_impact <= 0:
        raise ValueError(
            "temp_impact must exceed 0.5 * perm_impact * (total_time / n_intervals) "
            "for a well-posed Almgren-Chriss cost function "
            "(eta_tilde = eta - 0.5*gamma*tau must be > 0)"
        )

    trajectory = [total_shares]
    trade_list = []

    kappa = 0.0
    if risk_aversion > 0 and volatility > 0:
        kappa = np.sqrt(risk_aversion * (volatility ** 2) / temp_impact)

    if risk_aversion <= 0 or volatility == 0 or kappa == 0:
        # TWAP
        for k in range(1, n_intervals + 1):
            x_k = total_shares * (1.0 - k / n_intervals)
            n_k = trajectory[-1] - x_k
            trade_list.append(n_k)
            trajectory.append(x_k)
    else:
        for k in range(1, n_intervals + 1):
            t_k = k * tau
            if k == n_intervals:
                x_k = 0.0
            elif kappa * total_time > 100:
                x_k = total_shares * np.exp(-kappa * t_k)
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


def calculate_efficient_frontier(
    total_shares: float,
    total_time: float,
    n_intervals: int,
    volatility: float,
    temp_impact: float,
    perm_impact: float,
    lambda_min: float = 1e-8,
    lambda_max: float = 1e-2,
    n_points: int = 20
) -> List[Dict[str, float]]:
    lambdas = np.logspace(np.log10(lambda_min), np.log10(lambda_max), n_points)
    frontier = []
    for risk_aversion in lambdas:
        result = compute_trading_trajectory(
            total_shares=total_shares,
            total_time=total_time,
            n_intervals=n_intervals,
            volatility=volatility,
            temp_impact=temp_impact,
            perm_impact=perm_impact,
            risk_aversion=risk_aversion
        )
        frontier.append({
            "risk_aversion": float(risk_aversion),
            "expected_shortfall": result["expected_shortfall"],
            "variance": result["variance"]
        })
    return frontier
