"""InvestYo Quant Platform — Hierarchical Risk Parity (HRP) & CVaR Optimizer.

Implements Lopez de Prado's Hierarchical Risk Parity portfolio allocation combined
with Conditional Value at Risk (CVaR / Expected Shortfall) optimization.
"""

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, leaves_list
from scipy.spatial.distance import squareform
from scipy.optimize import minimize
from typing import List

def compute_correlation_distance(cov: pd.DataFrame) -> pd.DataFrame:
    """
    Computes correlation and correlation distance matrix.
    Formula: D_{i,j} = sqrt(0.5 * (1 - rho_{i,j}))
    """
    vols = np.sqrt(np.diag(cov))
    outer_vols = np.outer(vols, vols)
    # Handle division by zero if volatility is zero
    with np.errstate(divide='ignore', invalid='ignore'):
        corr = cov / outer_vols
    corr = np.nan_to_num(corr, nan=0.0)
    corr = np.clip(corr, -1.0, 1.0)
    
    dist = np.sqrt(np.clip(0.5 * (1 - corr), 0.0, 1.0))
    np.fill_diagonal(dist, 0.0)
    return pd.DataFrame(dist, index=cov.index, columns=cov.columns)

def quasi_diagonalization(dist: pd.DataFrame) -> List[int]:
    """
    Performs hierarchical clustering and returns the sorted indices for quasi-diagonalization.
    Uses 'single' linkage clustering standard in HRP.
    """
    dist_np = dist.values
    # To avoid rounding errors making the matrix non-symmetric
    dist_np = (dist_np + dist_np.T) / 2
    np.fill_diagonal(dist_np, 0.0)
    
    condensed_dist = squareform(dist_np, checks=False)
    Z = linkage(condensed_dist, method='single')
    sort_ix = leaves_list(Z)
    return sort_ix.tolist()

def _get_cluster_var(cov: np.ndarray, c_items: List[int]) -> float:
    """
    Calculate cluster variance using inverse variance portfolio allocation.
    """
    cov_slice = cov[np.ix_(c_items, c_items)]
    ivp = 1.0 / np.clip(np.diag(cov_slice), a_min=1e-10, a_max=None)
    ivp /= ivp.sum()
    return np.dot(ivp, np.dot(cov_slice, ivp))

def recursive_bisection(cov: pd.DataFrame, sort_ix: List[int]) -> pd.Series:
    """
    Recursively bisects the sorted covariance matrix to assign HRP weights.
    """
    w = pd.Series(1.0, index=sort_ix)
    c_items = [sort_ix]
    
    cov_np = cov.values
    
    while len(c_items) > 0:
        # Split each cluster into two
        c_items = [i[j:k] for i in c_items for j, k in ((0, len(i) // 2), (len(i) // 2, len(i))) if len(i) > 1]
        
        for i in range(0, len(c_items), 2):
            c_items0 = c_items[i]
            c_items1 = c_items[i + 1]
            
            c_var0 = _get_cluster_var(cov_np, c_items0)
            c_var1 = _get_cluster_var(cov_np, c_items1)
            
            total_var = c_var0 + c_var1
            alpha = 0.5 if total_var < 1e-12 else 1.0 - c_var0 / total_var
            
            w[c_items0] *= alpha
            w[c_items1] *= 1 - alpha
            
    # Map sort indices back to dataframe column positions
    w.index = cov.columns[w.index]
    return w.sort_index()

def calculate_cvar(weights: np.ndarray, returns: np.ndarray, alpha: float = 0.05) -> float:
    """
    Calculates Conditional Value at Risk (CVaR) for a given portfolio.
    Returns positive expected loss in the tail.
    """
    if len(returns) == 0:
        return 0.0
    portfolio_returns = np.dot(returns, weights)
    var = np.percentile(portfolio_returns, alpha * 100)
    # Filter returns worse than or equal to VaR
    tail_losses = portfolio_returns[portfolio_returns <= var]
    if len(tail_losses) == 0:
        return 0.0
    return -tail_losses.mean()

def constrain_cvar(returns: pd.DataFrame, initial_weights: pd.Series, max_cvar: float, alpha: float = 0.05) -> pd.Series:
    """
    Constrains portfolio weights to satisfy a maximum CVaR constraint using SLSQP.
    Minimizes distance from initial_weights subject to CVaR <= max_cvar.
    """
    num_assets = returns.shape[1]
    returns_np = returns.values
    initial_w_np = initial_weights.values
    
    def objective(w):
        return np.sum((w - initial_w_np) ** 2)
        
    def objective_jac(w):
        return 2 * (w - initial_w_np)
        
    def cvar_constraint(w):
        return max_cvar - calculate_cvar(w, returns_np, alpha)
        
    def cvar_constraint_jac(w):
        port_ret = np.dot(returns_np, w)
        var = np.percentile(port_ret, alpha * 100)
        tail_returns = returns_np[port_ret <= var]
        if len(tail_returns) == 0:
            return np.zeros_like(w)
        # CVaR = -mean(R_tail @ w)
        # d(CVaR)/dw = -mean(R_tail, axis=0)
        # Constraint = max_cvar - CVaR
        # d(Constraint)/dw = mean(R_tail, axis=0)
        return np.mean(tail_returns, axis=0)
        
    def weight_constraint(w):
        return np.sum(w) - 1.0
        
    def weight_constraint_jac(w):
        return np.ones_like(w)
        
    constraints = [
        {'type': 'ineq', 'fun': cvar_constraint, 'jac': cvar_constraint_jac},
        {'type': 'eq', 'fun': weight_constraint, 'jac': weight_constraint_jac}
    ]
    
    bounds = tuple((0.0, 1.0) for _ in range(num_assets))
    
    result = minimize(
        objective,
        initial_w_np,
        method='SLSQP',
        jac=objective_jac,
        bounds=bounds,
        constraints=constraints,
        options={'ftol': 1e-6, 'disp': False}
    )
    
    if result.success:
        optimized_weights = result.x / np.sum(result.x)
        return pd.Series(optimized_weights, index=initial_weights.index)
    else:
        return initial_weights
