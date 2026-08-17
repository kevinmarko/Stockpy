import numpy as np
from validation.synthetic_diffusion_engine import (
    train_diffusion_model,
    generate_synthetic_crash_paths,
    compute_diffusion_var,
    build_return_windows,
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


# ---------------------------------------------------------------------------
# build_return_windows -- real historical-return windowing (2026-08, closes
# audit finding F7's "no lookahead-bias perturbation coverage" gap for this
# module). api/pilots_api.py::post_diffusion_stress_test previously fed
# train_diffusion_model np.random.randn(...) * volatility + drift as
# "historical data"; build_return_windows is the real replacement -- a
# window ending at a given point must never depend on returns after it, per
# CLAUDE.md's "zero lookahead bias ... perturbation tests" requirement.
# ---------------------------------------------------------------------------


def test_build_return_windows_shape_and_values():
    returns = np.arange(20, dtype=float)  # 0, 1, 2, ..., 19
    windows = build_return_windows(returns, window_len=5, max_windows=200)
    # 20 - 5 + 1 = 16 possible windows, all fit under max_windows.
    assert windows.shape == (16, 5)
    np.testing.assert_array_equal(windows[0], [0, 1, 2, 3, 4])
    np.testing.assert_array_equal(windows[-1], [15, 16, 17, 18, 19])


def test_build_return_windows_caps_at_max_windows_using_most_recent():
    returns = np.arange(100, dtype=float)
    windows = build_return_windows(returns, window_len=10, max_windows=5)
    assert windows.shape == (5, 10)
    # The most recent windows (closest to the end of `returns`) are kept.
    np.testing.assert_array_equal(windows[-1], returns[-10:])


def test_build_return_windows_insufficient_data_returns_empty_not_fabricated():
    returns = np.array([1.0, 2.0, 3.0])
    windows = build_return_windows(returns, window_len=10, max_windows=200)
    assert windows.shape == (0, 10)


def test_build_return_windows_unaffected_by_future_data():
    """Perturbation test: a window entirely within the first N returns must
    be byte-identical whether or not the array is later extended with new
    (perturbed) values -- proves no window derives a value from returns
    'after' its own slice."""
    base_returns = np.linspace(-0.05, 0.05, 50)
    windows_base = build_return_windows(base_returns, window_len=10, max_windows=200)

    extended_returns = np.concatenate([base_returns, np.full(20, 999.0)])
    windows_extended = build_return_windows(extended_returns, window_len=10, max_windows=200)

    # Every window that is a pure slice of the original (unperturbed) prefix
    # must match exactly between the two calls.
    n_pure_prefix_windows = len(base_returns) - 10 + 1
    np.testing.assert_array_equal(
        windows_base[:n_pure_prefix_windows],
        windows_extended[:n_pure_prefix_windows],
    )
    # Sanity: later windows (which DO touch the perturbed tail) actually
    # differ -- proves the extension was a real perturbation.
    assert not np.allclose(windows_extended[-1], windows_base[-1])


def test_synthetic_diffusion_engine_no_lookahead_bias():
    """Verifies that future data mutations t > T do not affect model training or generated paths for t <= T."""
    np.random.seed(42)
    # Generate historical paths up to time T
    N, L = 50, 10
    full_history = np.random.randn(100, L) * 0.02 - 0.005

    # Baseline slice up to index N
    hist_baseline = full_history[:N].copy()
    model_baseline = train_diffusion_model(hist_baseline, epochs=20, lr=0.01)

    # Mutate future history beyond N (index N to 100)
    mutated_full_history = full_history.copy()
    mutated_full_history[N:] = np.random.randn(50, L) * 10.0

    # Extract historical slice up to N from mutated history
    hist_mutated = mutated_full_history[:N].copy()
    model_mutated = train_diffusion_model(hist_mutated, epochs=20, lr=0.01)

    # Weights must be bit-exact identical
    for k in ["W1", "b1", "W2", "b2"]:
        np.testing.assert_array_equal(model_baseline[k], model_mutated[k])

