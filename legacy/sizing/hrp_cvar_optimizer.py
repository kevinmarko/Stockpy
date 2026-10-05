"""InvestYo Quant Platform — Hierarchical Risk Parity (HRP) & CVaR Optimizer.

Implements Lopez de Prado's Hierarchical Risk Parity portfolio allocation combined
with Conditional Value at Risk (CVaR / Expected Shortfall) optimization.
"""

import logging

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, leaves_list
from scipy.spatial.distance import squareform
from scipy.optimize import minimize
from typing import List, Dict, Optional, Tuple, Any, Union, Callable

logger = logging.getLogger(__name__)

def compute_correlation_distance(cov: pd.DataFrame) -> pd.DataFrame:
    """
    Computes correlation and correlation distance matrix.
    Formula: D_{i,j} = sqrt(0.5 * (1 - rho_{i,j}))
    """
    vols = np.sqrt(np.diag(cov))
    outer_vols = np.outer(vols, vols)
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
    return float(np.dot(ivp, np.dot(cov_slice, ivp)))

def recursive_bisection(cov: pd.DataFrame, sort_ix: List[int]) -> pd.Series:
    """
    Recursively bisects the sorted covariance matrix to assign HRP weights.
    """
    w = pd.Series(1.0, index=sort_ix)
    c_items = [sort_ix]
    
    cov_np = cov.values
    
    while len(c_items) > 0:
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
    tail_losses = portfolio_returns[portfolio_returns <= var]
    if len(tail_losses) == 0:
        return 0.0
    return float(-tail_losses.mean())

def constrain_cvar(returns: pd.DataFrame, initial_weights: pd.Series, max_cvar: float, alpha: float = 0.05) -> pd.Series:
    """
    Constrains portfolio weights to satisfy a maximum CVaR constraint using SLSQP.
    Minimizes distance from initial_weights subject to CVaR <= max_cvar.
    """
    num_assets = returns.shape[1]
    returns_np = returns.values
    initial_w_np = initial_weights.values
    
    def objective(w: np.ndarray) -> float:
        return np.sum((w - initial_w_np) ** 2)
        
    def objective_jac(w: np.ndarray) -> np.ndarray:
        return 2 * (w - initial_w_np)
        
    def cvar_constraint(w: np.ndarray) -> float:
        return max_cvar - calculate_cvar(w, returns_np, alpha)
        
    def cvar_constraint_jac(w: np.ndarray) -> np.ndarray:
        port_ret = np.dot(returns_np, w)
        var = np.percentile(port_ret, alpha * 100)
        tail_returns = returns_np[port_ret <= var]
        if len(tail_returns) == 0:
            return np.zeros_like(w)
        return np.mean(tail_returns, axis=0)
        
    def weight_constraint(w: np.ndarray) -> float:
        return np.sum(w) - 1.0
        
    def weight_constraint_jac(w: np.ndarray) -> np.ndarray:
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

def optimize_hrp_cvar(
    returns: pd.DataFrame,
    max_cvar: Optional[float] = None,
    alpha: float = 0.05,
) -> pd.Series:
    """
    Computes standard HRP weights from historical returns and optionally applies CVaR constraint.
    """
    if returns.empty or returns.shape[1] == 0:
        return pd.Series(dtype=float)
    if returns.shape[1] == 1:
        return pd.Series([1.0], index=returns.columns)
        
    cov = returns.cov()
    dist = compute_correlation_distance(cov)
    sort_ix = quasi_diagonalization(dist)
    weights = recursive_bisection(cov, sort_ix)
    if max_cvar is not None:
        weights = constrain_cvar(returns, weights, max_cvar=max_cvar, alpha=alpha)
    return weights

def optimize_turnover_regularized_hrp_cvar(
    returns: pd.DataFrame,
    current_weights: Optional[Dict[str, float]] = None,
    lambda_turnover: float = 0.05,
    max_cvar: Optional[float] = None,
    max_weight: float = 1.0,
    min_weight: float = 0.0,
    sector_map: Optional[Dict[str, str]] = None,
    sector_caps: Optional[Dict[str, float]] = None,
    asset_betas: Optional[Dict[str, float]] = None,
    target_beta_range: Optional[Union[List[float], Tuple[float, float]]] = None,
    alpha: float = 0.05,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Phase 35: Turnover-Regularized & Factor-Neutral HRP Multi-Asset Optimizer.

    Objective:
        min_w [ CVaR_alpha(w) + lambda_turnover * sum_i |w_i - w_{0, i}| ]

    Linear Constraints (SLSQP):
        - sum_i w_i = 1.0 (fully invested)
        - w_i in [min_weight, max_weight]
        - Sector constraints: sum_{i in S_k} w_i <= sector_caps[k]
        - Beta constraints: beta_min <= sum_i w_i * beta_i <= beta_max
        - Optional CVaR constraint: CVaR_alpha(w) <= max_cvar

    Initial Guess:
        Initialized from HRP baseline weights.

    Output Telemetry:
        - weights: Dict[str, float] (optimal weights)
        - turnover: float (1/2 * sum |w_i - w_{0, i}|)
        - cvar: float (expected portfolio tail loss)
        - portfolio_beta: float (sum w_i * beta_i)
        - sector_exposures: Dict[str, float] (sum_{i in S_k} w_i)
        - diversification_ratio: float ((w @ vols) / sqrt(w @ Cov @ w))
        - status: str ("optimal" or "fallback")
    """
    if returns.empty or returns.shape[1] == 0:
        return {
            "weights": {},
            "allocations": pd.Series(dtype=float),
            "hrp_allocations": pd.Series(dtype=float),
            "turnover": 0.0,
            "cvar": 0.0,
            "cvar_95": 0.0,
            "expected_return": 0.0,
            "sharpe_ratio": 0.0,
            "portfolio_beta": 0.0,
            "sector_exposures": {},
            "diversification_ratio": 1.0,
            "dendrogram": {},
            "status": "fallback",
        }

    assets = list(returns.columns)
    N = len(assets)
    returns_np = returns.values

    if "max_asset_weight" in kwargs:
        max_weight = float(kwargs["max_asset_weight"])

    # Incumbent portfolio weights w0
    if current_weights is not None:
        w0 = np.array([float(current_weights.get(a, 0.0)) for a in assets], dtype=float)
    else:
        w0 = None

    # Handle 1-asset edge case
    if N == 1:
        sym = str(assets[0])
        w_single = np.array([1.0])
        w_dict = {sym: 1.0}
        turnover_val = 0.5 * abs(1.0 - w0[0]) if w0 is not None else 0.0
        cvar_val = float(calculate_cvar(w_single, returns_np, alpha=alpha))
        beta_val = float(asset_betas.get(sym, 1.0)) if asset_betas else 1.0
        sec_val = sector_map.get(sym, "Unknown") if sector_map else None
        sec_exp = {sec_val: 1.0} if sec_val else {}
        mean_ret = float(returns_np.mean() * 252) if len(returns_np) > 0 else 0.0
        ann_vol = float(returns_np.std() * np.sqrt(252)) if len(returns_np) > 0 else 0.0
        sharpe_val = float(mean_ret / ann_vol) if ann_vol > 1e-6 else 0.0
        return {
            "weights": w_dict,
            "allocations": pd.Series(w_dict),
            "hrp_allocations": pd.Series(w_dict),
            "turnover": turnover_val,
            "cvar": cvar_val,
            "cvar_95": cvar_val,
            "expected_return": mean_ret,
            "sharpe_ratio": sharpe_val,
            "portfolio_beta": beta_val,
            "sector_exposures": sec_exp,
            "diversification_ratio": 1.0,
            "dendrogram": {"name": sym, "distance": 0.0},
            "status": "optimal",
        }

    # Compute baseline HRP weights as starting point
    cov = returns.cov()
    dendrogram_tree: Dict[str, Any] = {}
    hrp_fallback = False
    try:
        dist = compute_correlation_distance(cov)
        dist_np = dist.values
        dist_np = (dist_np + dist_np.T) / 2
        np.fill_diagonal(dist_np, 0.0)
        condensed_dist = squareform(dist_np, checks=False)
        Z = linkage(condensed_dist, method='single')
        nodes = {i: {"name": assets[i], "distance": 0.0} for i in range(N)}
        for i, row in enumerate(Z):
            idx1, idx2, d, _ = row
            nodes[N + i] = {
                "name": f"Cluster {i+1}",
                "distance": float(d),
                "children": [nodes[int(idx1)], nodes[int(idx2)]],
            }
        dendrogram_tree = nodes[N + len(Z) - 1]
        sort_ix = quasi_diagonalization(dist)
        hrp_series = recursive_bisection(cov, sort_ix)
        w_hrp = hrp_series.reindex(assets).fillna(1.0 / N).values
    except Exception as exc:
        logger.warning("HRP quasi-diagonalization failed, falling back to equal-weight: %s", exc)
        w_hrp = np.ones(N) / N
        hrp_fallback = True

    # Initial guess: clipped and normalized HRP weights within bounds
    w_init = np.clip(w_hrp, min_weight, max_weight)
    if np.sum(w_init) > 0:
        w_init = w_init / np.sum(w_init)
    else:
        w_init = np.ones(N) / N

    # Objective: CVaR_alpha(w) + lambda_turnover * sum_i |w_i - w_{0, i}|
    def objective(w: np.ndarray) -> float:
        cvar_term = calculate_cvar(w, returns_np, alpha=alpha)
        if w0 is not None and lambda_turnover > 0.0:
            turnover_penalty = float(lambda_turnover) * float(np.sum(np.abs(w - w0)))
        else:
            turnover_penalty = 0.0
        return cvar_term + turnover_penalty

    # Single-asset concentration bounds
    bounds = [(min_weight, max_weight) for _ in range(N)]

    # Linear and non-linear constraints
    constraints: List[Dict[str, Any]] = []

    # 1. Fully invested: sum(w) = 1.0
    constraints.append({
        'type': 'eq',
        'fun': lambda w: np.sum(w) - 1.0,
    })

    # 2. Sector caps: sum_{i in S_k} w_i <= sector_caps[k]
    if sector_caps and sector_map:
        for sec_name, cap_val in sector_caps.items():
            matching_indices = [i for i, a in enumerate(assets) if sector_map.get(a) == sec_name]
            if matching_indices:
                def make_sec_con(indices: List[int] = matching_indices, cap: float = float(cap_val)) -> Callable[[np.ndarray], float]:
                    return lambda w: cap - np.sum(w[indices])
                constraints.append({
                    'type': 'ineq',
                    'fun': make_sec_con(matching_indices, float(cap_val)),
                })

    # 3. Factor/Beta range constraints: beta_min <= sum(w_i * beta_i) <= beta_max
    if asset_betas and target_beta_range:
        betas = np.array([float(asset_betas.get(a, 1.0)) for a in assets], dtype=float)
        beta_min, beta_max = target_beta_range
        if beta_min is not None:
            def make_beta_min_con(b_vec: np.ndarray = betas, b_min: float = float(beta_min)) -> Callable[[np.ndarray], float]:
                return lambda w: np.dot(w, b_vec) - b_min
            constraints.append({
                'type': 'ineq',
                'fun': make_beta_min_con(betas, float(beta_min)),
            })
        if beta_max is not None:
            def make_beta_max_con(b_vec: np.ndarray = betas, b_max: float = float(beta_max)) -> Callable[[np.ndarray], float]:
                return lambda w: b_max - np.dot(w, b_vec)
            constraints.append({
                'type': 'ineq',
                'fun': make_beta_max_con(betas, float(beta_max)),
            })

    # 4. Optional Max CVaR constraint: max_cvar - CVaR(w) >= 0
    if max_cvar is not None:
        def make_cvar_con(cvar_limit: float = float(max_cvar)) -> Callable[[np.ndarray], float]:
            return lambda w: cvar_limit - calculate_cvar(w, returns_np, alpha=alpha)
        constraints.append({
            'type': 'ineq',
            'fun': make_cvar_con(float(max_cvar)),
        })

    # Solve via SLSQP
    try:
        res = minimize(
            objective,
            w_init,
            method='SLSQP',
            bounds=bounds,
            constraints=constraints,
            options={'ftol': 1e-7, 'maxiter': 500, 'disp': False},
        )
        if res.success:
            w_opt = np.clip(res.x, min_weight, max_weight)
            if np.sum(w_opt) > 0:
                w_opt = w_opt / np.sum(w_opt)
            status = "optimal"
        else:
            w_opt = w_init
            status = "fallback"
    except Exception as exc:
        logger.warning("SLSQP optimization failed, falling back to initial weights: %s", exc)
        w_opt = w_init
        status = "fallback"

    # Telemetry metrics
    weights_dict = {a: float(w_opt[i]) for i, a in enumerate(assets)}
    allocations_series = pd.Series(weights_dict)
    hrp_allocations_series = pd.Series(w_hrp, index=assets)

    # Turnover: 0.5 * sum |w_i - w_{0, i}|
    if w0 is not None:
        turnover = 0.5 * float(np.sum(np.abs(w_opt - w0)))
    else:
        turnover = 0.0

    # CVaR
    cvar_val = float(calculate_cvar(w_opt, returns_np, alpha=alpha))

    # Portfolio beta
    if asset_betas:
        betas = np.array([float(asset_betas.get(a, 1.0)) for a in assets], dtype=float)
        portfolio_beta = float(np.dot(w_opt, betas))
    else:
        portfolio_beta = 1.0

    # Sector exposures: sum_{i in S_k} w_i
    sector_exposures: Dict[str, float] = {}
    if sector_map:
        for i, a in enumerate(assets):
            sec = sector_map.get(a, "Unknown")
            sector_exposures[sec] = sector_exposures.get(sec, 0.0) + float(w_opt[i])
        if sector_caps:
            for sec in sector_caps:
                if sec not in sector_exposures:
                    sector_exposures[sec] = 0.0

    # Diversification ratio: (w @ individual_vols) / portfolio_vol
    cov_mat = cov.values
    vols = np.sqrt(np.maximum(np.diag(cov_mat), 0.0))
    port_var = float(np.dot(w_opt, np.dot(cov_mat, w_opt)))
    port_vol = np.sqrt(max(port_var, 0.0))
    weighted_vol = float(np.dot(w_opt, vols))
    if port_vol > 1e-8:
        diversification_ratio = float(weighted_vol / port_vol)
    else:
        diversification_ratio = 1.0

    cov_annual = cov_mat * 252
    port_vol_annual = np.sqrt(max(float(np.dot(w_opt, np.dot(cov_annual, w_opt))), 0.0))
    port_ret = float(np.dot(returns_np.mean(axis=0) * 252, w_opt))
    sharpe = float(port_ret / port_vol_annual) if port_vol_annual > 1e-6 else 0.0

    return {
        "weights": weights_dict,
        "allocations": allocations_series,
        "hrp_allocations": hrp_allocations_series,
        "turnover": turnover,
        "cvar": cvar_val,
        "cvar_95": cvar_val,
        "expected_return": port_ret,
        "sharpe_ratio": sharpe,
        "portfolio_beta": portfolio_beta,
        "sector_exposures": sector_exposures,
        "diversification_ratio": diversification_ratio,
        "dendrogram": dendrogram_tree,
        "status": status,
        "hrp_fallback": hrp_fallback,
    }



