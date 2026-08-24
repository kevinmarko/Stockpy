import numpy as np
import pytest
from validation.synthetic_diffusion_engine import (
    REGIME_MAP,
    REGIME_ID_TO_NAME,
    build_return_windows,
    train_diffusion_model,
    train_conditional_diffusion_model,
    generate_synthetic_crash_paths,
    generate_guided_crisis_paths,
    compute_diffusion_var,
    compute_multi_quantile_var,
    _reverse_sde_drift,
)


def test_diffusion_engine_e2e():
    # 1. Create dummy historical data
    # 100 paths of length 10
    np.random.seed(42)
    historical_data = np.random.randn(100, 10) * 0.02 - 0.005  # mild negative drift
    
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
    assert cvar >= var  # CVaR is expected shortfall, should be >= VaR when both are positive loss magnitudes


def test_compute_diffusion_var():
    # Deterministic paths
    paths = np.array([
        [-0.10, -0.05],  # total -0.15
        [-0.02, -0.01],  # total -0.03
        [ 0.05,  0.05],  # total  0.10
        [-0.20, -0.10],  # total -0.30
        [-0.01,  0.02],  # total  0.01
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


# ---------------------------------------------------------------------------
# Phase 34: Conditional Generative Diffusion Engine Tests
# ---------------------------------------------------------------------------


def test_train_conditional_diffusion_model_shapes_and_convergence():
    """Verify conditional diffusion model training shapes, parameter structure, and numerical stability."""
    np.random.seed(42)
    N, L = 120, 12
    num_classes = 5
    historical_data = np.random.randn(N, L) * 0.02 - 0.005
    regime_labels = np.random.randint(0, num_classes, size=N)

    model = train_conditional_diffusion_model(
        historical_data,
        regime_labels=regime_labels,
        num_classes=num_classes,
        epochs=60,
        lr=0.01,
        seed=42,
    )

    # Expected input dimension = L + 1 (tau) + num_classes (one-hot conditioning)
    in_dim = L + 1 + num_classes
    assert model["W1"].shape == (in_dim, 64)
    assert model["b1"].shape == (64,)
    assert model["W2"].shape == (64, L)
    assert model["b2"].shape == (L,)
    assert model["L"] == L
    assert model["num_classes"] == num_classes

    # Verify no NaN or Inf weights
    for param in ["W1", "b1", "W2", "b2"]:
        assert not np.isnan(model[param]).any(), f"NaN found in {param}"
        assert not np.isinf(model[param]).any(), f"Inf found in {param}"


def test_train_conditional_diffusion_model_string_labels():
    """Verify train_conditional_diffusion_model accepts string regime labels."""
    np.random.seed(42)
    historical_data = np.random.randn(50, 8) * 0.01
    regime_labels = ["vol_shock", "credit_freeze", "stagflation", "liquidity_squeeze", "unconditional"] * 10

    model = train_conditional_diffusion_model(
        historical_data,
        regime_labels=regime_labels,
        num_classes=5,
        epochs=20,
    )
    assert model["L"] == 8
    assert model["num_classes"] == 5


def test_generate_guided_crisis_paths_across_regimes():
    """Verify generate_guided_crisis_paths works for all supported regimes with no NaNs."""
    np.random.seed(42)
    N, L = 100, 10
    historical_data = np.random.randn(N, L) * 0.02
    regimes = ["unconditional", "vol_shock", "credit_freeze", "stagflation", "liquidity_squeeze"]
    regime_labels = np.array([REGIME_MAP[r] for r in np.random.choice(regimes, size=N)])

    model = train_conditional_diffusion_model(
        historical_data,
        regime_labels=regime_labels,
        num_classes=5,
        epochs=50,
        seed=42,
    )

    num_paths = 150
    for regime in regimes:
        paths = generate_guided_crisis_paths(
            model=model,
            regime=regime,
            guidance_scale=2.0,
            num_paths=num_paths,
            steps=30,
            dt=0.01,
        )
        assert paths.shape == (num_paths, L), f"Shape mismatch for regime {regime}"
        assert not np.isnan(paths).any(), f"NaN in paths for regime {regime}"
        assert not np.isinf(paths).any(), f"Inf in paths for regime {regime}"


def test_classifier_free_guidance_monotonicity():
    """Verify that higher guidance scale (w=3.0) in vol_shock produces higher tail dispersion / larger CVaR than unguided (w=0.0)."""
    np.random.seed(42)
    L = 10
    num_classes = 5
    N_per_class = 200

    # Construct distinct synthetic data distributions for classes:
    # Class 0: Baseline low vol
    # Class 1 (vol_shock): elevated volatility with a genuine negative
    # (crash) mean -- previously this was fabricated with a POSITIVE mean
    # (+0.10), which happened to still pass under the pre-fix (wrong-sign)
    # reverse SDE via pathological divergence rather than real CFG signal;
    # see docs/known_issues/synthetic_diffusion_reverse_sde_sign_error.md.
    paths_c0 = np.random.randn(N_per_class, L) * 0.01
    paths_c1 = np.random.randn(N_per_class, L) * 0.05 - 0.10
    paths_c2 = np.random.randn(N_per_class, L) * 0.03 + 0.05
    paths_c3 = np.random.randn(N_per_class, L) * 0.02 + 0.04
    paths_c4 = np.random.randn(N_per_class, L) * 0.04 + 0.06

    historical_data = np.concatenate([paths_c0, paths_c1, paths_c2, paths_c3, paths_c4], axis=0)
    regime_labels = np.array(
        [0] * N_per_class + [1] * N_per_class + [2] * N_per_class + [3] * N_per_class + [4] * N_per_class
    )

    model = train_conditional_diffusion_model(
        historical_data,
        regime_labels=regime_labels,
        num_classes=num_classes,
        epochs=300,
        lr=0.01,
        seed=42,
    )

    # Generate paths with w=0.0 (unguided conditional) and w=3.0 (guided)
    np.random.seed(100)
    paths_w0 = generate_guided_crisis_paths(
        model,
        regime="vol_shock",
        guidance_scale=0.0,
        num_paths=2000,
        steps=50,
        dt=0.01,
    )

    np.random.seed(100)
    paths_w3 = generate_guided_crisis_paths(
        model,
        regime="vol_shock",
        guidance_scale=3.0,
        num_paths=2000,
        steps=50,
        dt=0.01,
    )

    # Compute tail dispersion and CVaR
    dispersion_w0 = float(np.std(np.sum(paths_w0, axis=1)))
    dispersion_w3 = float(np.std(np.sum(paths_w3, axis=1)))
    var_w0, cvar_w0 = compute_diffusion_var(paths_w0, confidence_level=0.95)
    var_w3, cvar_w3 = compute_diffusion_var(paths_w3, confidence_level=0.95)

    # Guided paths amplify the vol_shock regime crash features, increasing VaR / CVaR
    assert (
        cvar_w3 > cvar_w0 or var_w3 > var_w0
    ), f"Expected higher tail dispersion/CVaR with w=3.0: disp_w3={dispersion_w3:.4f}, disp_w0={dispersion_w0:.4f}, cvar_w3={cvar_w3:.4f}, cvar_w0={cvar_w0:.4f}, var_w3={var_w3:.4f}, var_w0={var_w0:.4f}"


def test_compute_multi_quantile_var():
    """Verify compute_multi_quantile_var returns expected keys and satisfies CVaR_99 >= VaR_99 >= VaR_95."""
    np.random.seed(42)
    # 500 paths with negative drift to ensure positive VaR/CVaR
    paths = np.random.randn(500, 10) * 0.03 - 0.01

    results = compute_multi_quantile_var(paths, confidence_levels=[0.95, 0.99])

    assert "95" in results
    assert "99" in results

    var_95, cvar_95 = results["95"]
    var_99, cvar_99 = results["99"]

    assert isinstance(var_95, float)
    assert isinstance(cvar_95, float)
    assert isinstance(var_99, float)
    assert isinstance(cvar_99, float)

    # Monotonicity of tail risk
    assert var_95 > 0, "VaR_95 should be positive loss"
    assert var_99 >= var_95, f"VaR_99 ({var_99}) should be >= VaR_95 ({var_95})"
    assert cvar_99 >= var_99, f"CVaR_99 ({cvar_99}) should be >= VaR_99 ({var_99})"
    assert cvar_95 >= var_95, f"CVaR_95 ({cvar_95}) should be >= VaR_95 ({var_95})"
    assert cvar_99 >= cvar_95, f"CVaR_99 ({cvar_99}) should be >= CVaR_95 ({cvar_95})"


def test_backwards_compatibility_generate_synthetic_crash_paths():
    """Verify generate_synthetic_crash_paths runs seamlessly on both unconditional and conditional models."""
    np.random.seed(42)
    historical_data = np.random.randn(60, 8) * 0.015

    # 1. Unconditional model
    uncond_model = train_diffusion_model(historical_data, epochs=30, lr=0.01)
    paths_uncond = generate_synthetic_crash_paths(uncond_model, num_paths=50, steps=20)
    assert paths_uncond.shape == (50, 8)
    assert not np.isnan(paths_uncond).any()

    # 2. Conditional model evaluated unconditionally
    cond_model = train_conditional_diffusion_model(historical_data, epochs=30, lr=0.01)
    paths_cond = generate_synthetic_crash_paths(cond_model, num_paths=50, steps=20)
    assert paths_cond.shape == (50, 8)
    assert not np.isnan(paths_cond).any()


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


# ---------------------------------------------------------------------------
# 2026-08: Reverse-SDE sign-error regression guards (distributional recovery)
#
# Every generation test above this point only checks shape/NaN/Inf or a
# directional monotonicity comparison -- none checks that the generated
# distribution is actually the right one. That gap is exactly how a sign
# error in the reverse-SDE drift (dx = [-x - 2*score]dt instead of the
# correct dx = [x + 2*score]dt) shipped undetected: the buggy formula still
# produced finite, differently-shaped, monotonic-in-the-right-direction
# output, just wildly wrong in magnitude and sometimes sign. See
# docs/known_issues/synthetic_diffusion_reverse_sde_sign_error.md for the
# full root-cause writeup. These tests assert the generated distribution
# actually recovers a KNOWN target, not just that it doesn't crash.
# ---------------------------------------------------------------------------


def test_reverse_sde_drift_recovers_known_gaussian_analytic_score():
    """Bypasses the trained score network entirely: uses the EXACT analytic
    score of a known conjugate target distribution x_0 ~ N(5, 1). For this
    module's forward process (dx = -x dtau + sqrt(2) dW), the marginal
    p_tau(x) = N(5*exp(-tau), 1) for ALL tau (x_0's own unit variance
    exactly cancels in the marginal-variance formula
    var0*exp(-2tau) + (1-exp(-2tau)) with var0=1), so the analytic score is
    score(x, tau) = -(x - 5*exp(-tau)).

    Integrates the reverse SDE via the real `_reverse_sde_drift` helper in a
    loop mirroring the production discretization exactly (tau decreasing,
    positive dt steps) from tau_max=3.0 (~ the stationary N(0,1) prior) down
    to tau~0, and asserts the recovered empirical distribution lands close
    to the true target N(5, 1). The pre-fix (wrong-sign) drift diverges
    instead -- reproduced independently at these same parameters as
    mean~-25 to -29, var~770-800 (order-of-magnitude wrong, not a rounding
    difference)."""
    rng = np.random.default_rng(7)
    mu0 = 5.0
    tau_max = 3.0
    dt = 0.01
    steps = 300
    num_paths = 5000

    x = rng.standard_normal(num_paths)
    for i in range(steps):
        tau = max(tau_max - i * dt, 1e-3)
        score = -(x - mu0 * np.exp(-tau))
        drift = _reverse_sde_drift(x, score)
        z = rng.standard_normal(num_paths)
        x = np.clip(x + drift * dt + np.sqrt(2.0) * np.sqrt(dt) * z, -50.0, 50.0)

    recovered_mean = float(np.mean(x))
    recovered_var = float(np.var(x))
    assert abs(recovered_mean - mu0) < 0.5, (
        f"Expected recovered mean near {mu0}, got {recovered_mean:.3f} -- "
        "reverse SDE drift sign is likely wrong."
    )
    assert abs(recovered_var - 1.0) < 0.5, (
        f"Expected recovered variance near 1.0, got {recovered_var:.3f} -- "
        "reverse SDE drift sign is likely wrong."
    )


def test_generate_synthetic_crash_paths_recovers_known_training_distribution():
    """Full-pipeline regression guard: trains the REAL train_diffusion_model
    on data drawn from a known Gaussian and asserts the REAL
    generate_synthetic_crash_paths actually recovers that distribution's
    location and rough scale -- not just shape/NaN-freedom, which every
    other generation test in this file already checked before the 2026-08
    sign fix and was insufficient to catch it.

    Uses hyperparameters in this codebase's own already-proven-reliable
    range (matching test_classifier_free_guidance_monotonicity's data
    scale/epoch/tau_max choices) rather than the live endpoint's much
    shorter epochs=15/dt=1/252 combination, which a separate, disclosed,
    NOT-yet-fixed calibration/undertraining gap (see the known-issues doc)
    prevents from converging within a reasonable tolerance for reasons
    unrelated to this sign bug -- using those hyperparameters here would
    make the test measure that unrelated gap instead of the sign fix.

    Tolerances are deliberately generous but bug-distinguishing: the
    pre-fix drift, reproduced independently at these same parameters,
    produces mean~-0.90 (vs. a true -0.04) and std~7.96 (vs. a true 0.015)
    -- both clearly outside the bounds asserted below."""
    np.random.seed(42)
    N, L = 300, 5
    true_mean, true_std = -0.04, 0.015
    historical_data = np.random.randn(N, L) * true_std + true_mean

    model = train_diffusion_model(historical_data, epochs=300, lr=0.01, seed=42)

    np.random.seed(7)
    paths = generate_synthetic_crash_paths(model, num_paths=3000, steps=50, dt=0.01)

    recovered_mean = float(np.mean(paths))
    recovered_std = float(np.std(paths))
    assert abs(recovered_mean - true_mean) < 0.15, (
        f"Expected recovered mean near {true_mean}, got {recovered_mean:.4f} -- "
        "reverse SDE drift sign is likely wrong."
    )
    assert recovered_std < 2.0, (
        f"Expected recovered std well below the pre-fix magnitude, got "
        f"{recovered_std:.4f} (true training std was {true_std}) -- "
        "reverse SDE drift sign is likely wrong."
    )


def test_generate_guided_crisis_paths_recovers_known_crash_regime_direction():
    """Full-pipeline regression guard for the conditional/guided code path
    -- the one the live `POST /pilots/options/ai/diffusion-stress-test`
    endpoint actually calls (`generate_synthetic_crash_paths` is unused by
    production, per api/pilots_api.py::post_diffusion_stress_test).

    Trains the REAL train_conditional_diffusion_model with a genuine
    negative-mean "vol_shock" crash class, generates via the REAL
    generate_guided_crisis_paths with classifier-free guidance, and asserts
    the recovered distribution has the CORRECT SIGN (a "crash" regime must
    generate predominantly negative returns) and a bounded scale. The sign
    check is the most direct test of this specific bug class: the pre-fix
    drift, reproduced independently at these same parameters, recovers a
    POSITIVE mean (~+1.02) for a regime whose real training data is
    negative -- the wrong direction entirely, not just the wrong magnitude
    (std~9.26 in the same buggy repro, vs. the bound asserted below)."""
    np.random.seed(42)
    N_per_class, L = 200, 5
    paths_c0 = np.random.randn(N_per_class, L) * 0.01
    true_mean, true_std = -0.08, 0.03
    paths_c1 = np.random.randn(N_per_class, L) * true_std + true_mean
    historical_data = np.concatenate([paths_c0, paths_c1], axis=0)
    regime_labels = np.array([0] * N_per_class + [1] * N_per_class)

    model = train_conditional_diffusion_model(
        historical_data,
        regime_labels=regime_labels,
        num_classes=5,
        epochs=300,
        lr=0.01,
        seed=42,
    )

    np.random.seed(7)
    paths = generate_guided_crisis_paths(
        model,
        regime="vol_shock",
        guidance_scale=2.0,
        num_paths=3000,
        steps=50,
        dt=0.01,
    )

    recovered_mean = float(np.mean(paths))
    recovered_std = float(np.std(paths))
    assert recovered_mean < 0, (
        f"Expected a negative (crash-direction) mean for the vol_shock "
        f"regime (true training mean {true_mean}), got {recovered_mean:.4f} "
        "-- reverse SDE drift sign is likely wrong."
    )
    assert recovered_std < 2.0, (
        f"Expected recovered std well below the pre-fix magnitude, got "
        f"{recovered_std:.4f} (true training std was {true_std}) -- "
        "reverse SDE drift sign is likely wrong."
    )

