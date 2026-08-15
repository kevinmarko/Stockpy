import numpy as np
from validation.synthetic_diffusion_engine import (
    train_diffusion_model,
    generate_synthetic_crash_paths,
    compute_diffusion_var,
)

def test_diffusion_engine_e2e():
    # 1. Create dummy historical data
    # 100 paths of length 10
    np.random.seed(42)
    historical_data = np.random.randn(100, 10) * 0.02 - 0.005 # mild negative drift
    
    # 2. Train model
    model = train_diffusion_model(historical_data, epochs=50, lr=0.01)
    
    assert "W1" in model
    assert "b1" in model
    assert "W2" in model
    assert "b2" in model
    assert model["L"] == 10
    
    # 3. Generate synthetic paths
    num_paths = 200
    steps = 50
    synthetic_paths = generate_synthetic_crash_paths(model, num_paths=num_paths, steps=steps, dt=0.02)
    
    assert synthetic_paths.shape == (num_paths, 10)
    assert not np.isnan(synthetic_paths).any()
    
    # 4. Compute VaR and CVaR
    var, cvar = compute_diffusion_var(synthetic_paths, confidence_level=0.95)
    
    assert isinstance(var, float)
    assert isinstance(cvar, float)
    assert cvar >= var # CVaR is expected shortfall, should be >= VaR when both are positive loss magnitudes

def test_compute_diffusion_var():
    # Deterministic paths
    paths = np.array([
        [-0.10, -0.05], # total -0.15
        [-0.02, -0.01], # total -0.03
        [ 0.05,  0.05], # total  0.10
        [-0.20, -0.10], # total -0.30
        [-0.01,  0.02], # total  0.01
    ])
    # total returns: -0.15, -0.03, 0.10, -0.30, 0.01
    # sorted: -0.30, -0.15, -0.03, 0.01, 0.10
    # At confidence 0.8 (alpha=0.2), we want the 20th percentile
    # 20th percentile of 5 items is interpolated
    var, cvar = compute_diffusion_var(paths, confidence_level=0.8)
    
    assert var > 0
    assert cvar > 0
    assert cvar >= var
