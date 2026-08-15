import pandas as pd
import numpy as np
from sizing.hrp_cvar_optimizer import constrain_cvar

np.random.seed(42)
returns = pd.DataFrame(np.random.normal(0.001, 0.02, size=(1000, 3)), columns=['A', 'B', 'C'])
returns.iloc[0:100, 0] = -0.2
initial_weights = pd.Series([0.5, 0.3, 0.2], index=['A', 'B', 'C'])

# Let's print the result inside constrain_cvar temporarily
from scipy.optimize import minimize
from sizing.hrp_cvar_optimizer import calculate_cvar

def constrain_cvar_debug(returns, initial_weights, max_cvar, alpha=0.05):
    num_assets = returns.shape[1]
    returns_np = returns.values
    initial_w_np = initial_weights.values
    
    def objective(w):
        return np.sum((w - initial_w_np) ** 2)
        
    def cvar_constraint(w):
        return max_cvar - calculate_cvar(w, returns_np, alpha)
        
    def weight_constraint(w):
        return np.sum(w) - 1.0
        
    constraints = [
        {'type': 'ineq', 'fun': cvar_constraint},
        {'type': 'eq', 'fun': weight_constraint}
    ]
    
    bounds = tuple((0.0, 1.0) for _ in range(num_assets))
    
    result = minimize(
        objective,
        initial_w_np,
        method='SLSQP',
        bounds=bounds,
        constraints=constraints,
        options={'ftol': 1e-6, 'disp': True}
    )
    print("Optimization Result:", result)
    
    if result.success:
        optimized_weights = result.x / np.sum(result.x)
        return pd.Series(optimized_weights, index=initial_weights.index)
    else:
        return initial_weights

w_new = constrain_cvar_debug(returns, initial_weights, 0.02, alpha=0.05)
print("Returned weights:", w_new.values)
