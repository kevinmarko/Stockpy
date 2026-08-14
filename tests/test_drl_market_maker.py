"""
tests/test_drl_market_maker.py — Unit Tests for Avellaneda-Stoikov Quoting & DRL Market Maker.
================================================================================================

Validates:
1. Avellaneda-Stoikov (2008) Reservation Price calculations and invariant properties.
2. Optimal Asymmetric Quoting Spreads (spread symmetry, skewing, limits, tuple unpacking).
3. Poisson order arrival intensities and fill probability functions.
4. Intraday Market Maker Simulation (390-min session, performance decomposition, Sharpe ratio).
5. DRL Policy Optimizer (train_market_maker_policy hyperparameter tuning).
6. MarketMakingEnv Gym-compatible environment (reset, step, transitions, PnL, inventory bounds).
7. Performance metrics and Sharpe ratio computation.
8. Monte Carlo strategy comparison (Avellaneda-Stoikov inventory dampening).
9. Edge cases & degenerate bounds (never raises).
10. AST Import Safety (strictly zero heavy engine imports).
"""

import ast
import json
import math
from pathlib import Path
import pytest
import numpy as np

from ml.drl_market_maker import (
    DEFAULT_A,
    DEFAULT_DT,
    DEFAULT_GAMMA,
    DEFAULT_KAPPA,
    DEFAULT_MAX_INVENTORY,
    DEFAULT_SIGMA,
    OptimalSpreads,
    MarketMakingConfig,
    MarketMakerConfig,
    StepResult,
    MarketMakerMetrics,
    MarketMakerSessionResult,
    PolicyOptimizationResult,
    compute_reservation_price,
    compute_optimal_spreads,
    compute_optimal_quotes,
    compute_arrival_intensity,
    compute_fill_probability,
    compute_fill_probabilities,
    generate_gbm_price_path,
    MarketMakingEnv,
    MarketMakerEnv,
    simulate_market_maker_execution,
    train_market_maker_policy,
    simulate_avellaneda_stoikov,
    simulate_symmetric_market_maker,
    compare_market_making_strategies,
)


# ===========================================================================
# 1. Avellaneda-Stoikov Reservation Price Tests
# ===========================================================================

def test_reservation_price_zero_inventory():
    """At zero inventory (q=0), reservation price MUST equal the mid price."""
    mid = 100.0
    r = compute_reservation_price(mid_price=mid, inventory=0, gamma=0.1, sigma=0.3, time_to_close=1.0)
    assert pytest.approx(r, abs=1e-7) == mid


def test_reservation_price_long_inventory():
    """When long (q > 0), reservation price MUST be lower than mid price to induce selling."""
    mid = 100.0
    q = 5.0
    gamma = 0.1
    sigma = 0.2
    tau = 0.5
    expected = mid - (q * gamma * (sigma ** 2) * tau)  # 100 - (5 * 0.1 * 0.04 * 0.5) = 100 - 0.01 = 99.99
    r = compute_reservation_price(mid_price=mid, inventory=q, gamma=gamma, sigma=sigma, time_to_close=tau)
    assert pytest.approx(r, abs=1e-7) == expected
    assert r < mid


def test_reservation_price_short_inventory():
    """When short (q < 0), reservation price MUST be higher than mid price to induce buying."""
    mid = 100.0
    q = -4.0
    gamma = 0.1
    sigma = 0.2
    tau = 0.5
    expected = mid - (q * gamma * (sigma ** 2) * tau)  # 100 - (-4 * 0.1 * 0.04 * 0.5) = 100 + 0.008 = 100.008
    r = compute_reservation_price(mid_price=mid, inventory=q, gamma=gamma, sigma=sigma, time_to_close=tau)
    assert pytest.approx(r, abs=1e-7) == expected
    assert r > mid


def test_reservation_price_monotonicity_with_risk_aversion():
    """Reservation price discount increases strictly with risk aversion gamma."""
    mid = 100.0
    q = 3.0
    tau = 1.0
    sigma = 0.3

    r_low_gamma = compute_reservation_price(mid, q, gamma=0.01, sigma=sigma, time_to_close=tau)
    r_high_gamma = compute_reservation_price(mid, q, gamma=0.50, sigma=sigma, time_to_close=tau)

    # Higher gamma means market maker is more eager to offload long inventory -> lower reservation price
    assert r_high_gamma < r_low_gamma


def test_reservation_price_zero_time_remaining():
    """At terminal time (tau = 0), reservation price equals mid price regardless of inventory."""
    mid = 150.0
    r = compute_reservation_price(mid_price=mid, inventory=10, gamma=0.5, sigma=0.4, time_to_close=0.0)
    assert pytest.approx(r, abs=1e-7) == mid


# ===========================================================================
# 2. Optimal Quoting Spreads Tests
# ===========================================================================

def test_optimal_spreads_zero_inventory_symmetry():
    """At q=0, optimal bid and ask spreads around mid MUST be identical."""
    mid = 100.0
    spreads = compute_optimal_spreads(
        mid_price=mid,
        inventory=0,
        gamma=0.1,
        sigma=0.3,
        time_to_close=1.0,
        kappa=1.5,
    )

    assert pytest.approx(spreads.delta_bid, rel=1e-6) == spreads.delta_ask
    assert pytest.approx(spreads.total_spread, rel=1e-6) == (spreads.delta_bid + spreads.delta_ask)
    assert pytest.approx(spreads.reservation_price, abs=1e-7) == mid
    assert pytest.approx(spreads.ask_price - mid, rel=1e-6) == spreads.delta_ask
    assert pytest.approx(mid - spreads.bid_price, rel=1e-6) == spreads.delta_bid


def test_optimal_spreads_long_inventory_skewing():
    """When long (q > 0), ask spread delta_a MUST tighten and bid spread delta_b MUST widen."""
    mid = 100.0
    spreads = compute_optimal_spreads(
        mid_price=mid,
        inventory=5,
        gamma=0.1,
        sigma=0.3,
        time_to_close=1.0,
        kappa=1.5,
    )

    assert spreads.delta_ask < spreads.delta_bid
    # Ask price is closer to mid, bid price is further below mid
    assert (spreads.ask_price - mid) < (mid - spreads.bid_price)


def test_optimal_spreads_short_inventory_skewing():
    """When short (q < 0), ask spread delta_a MUST widen and bid spread delta_b MUST tighten."""
    mid = 100.0
    spreads = compute_optimal_spreads(
        mid_price=mid,
        inventory=-5,
        gamma=0.1,
        sigma=0.3,
        time_to_close=1.0,
        kappa=1.5,
    )

    assert spreads.delta_ask > spreads.delta_bid
    # Bid price is closer to mid, ask price is further above mid
    assert (mid - spreads.bid_price) < (spreads.ask_price - mid)


def test_optimal_spreads_quotes_centered_on_reservation_price():
    """Quotes are always centered symmetrically around the reservation price."""
    mid = 105.0
    inventory = 4
    gamma = 0.15
    sigma = 0.25
    tau = 0.8
    kappa = 1.2

    spreads = compute_optimal_spreads(mid, inventory, gamma, sigma, tau, kappa)
    res_price = spreads.reservation_price

    midpoint_of_quotes = (spreads.ask_price + spreads.bid_price) / 2.0
    assert pytest.approx(midpoint_of_quotes, abs=1e-6) == res_price
    assert pytest.approx(spreads.ask_price - res_price, abs=1e-6) == spreads.half_spread
    assert pytest.approx(res_price - spreads.bid_price, abs=1e-6) == spreads.half_spread


def test_optimal_spreads_tuple_unpacking():
    """OptimalSpreads supports direct 2-tuple unpacking (delta_bid, delta_ask)."""
    spreads = compute_optimal_spreads(100.0, 0, 0.1, 0.2, 1.0, 1.5)
    delta_b, delta_a = spreads
    assert delta_b == spreads.delta_bid
    assert delta_a == spreads.delta_ask


def test_optimal_spreads_small_gamma_numerical_stability():
    """Near-zero gamma approaches 1/kappa limit gracefully without division by zero."""
    spreads = compute_optimal_spreads(100.0, 0, gamma=1e-9, sigma=0.2, time_to_close=1.0, kappa=2.0)
    # Expected half-spread is approx 1 / kappa = 0.5
    assert pytest.approx(spreads.half_spread, rel=1e-3) == 0.5


def test_compute_optimal_quotes_boundary_gates():
    """Verifies that max inventory limits properly disable quoting."""
    quotes_normal = compute_optimal_quotes(
        mid_price=100.0,
        inventory=0,
        max_inventory=10,
    )
    assert quotes_normal["bid_active"] is True
    assert quotes_normal["ask_active"] is True

    # At max long inventory (+10), buying is disabled
    quotes_max_long = compute_optimal_quotes(
        mid_price=100.0,
        inventory=10,
        max_inventory=10,
    )
    assert quotes_max_long["bid_active"] is False
    assert quotes_max_long["ask_active"] is True

    # At max short inventory (-10), selling is disabled
    quotes_max_short = compute_optimal_quotes(
        mid_price=100.0,
        inventory=-10,
        max_inventory=10,
    )
    assert quotes_max_short["bid_active"] is True
    assert quotes_max_short["ask_active"] is False


# ===========================================================================
# 3. Poisson Arrival & Fill Probability Tests
# ===========================================================================

def test_arrival_intensity_decay():
    """Arrival intensity lambda(delta) decreases exponentially as spread delta increases."""
    A = 100.0
    kappa = 1.5
    lam_tight = compute_arrival_intensity(delta=0.1, A=A, kappa=kappa)
    lam_wide = compute_arrival_intensity(delta=1.0, A=A, kappa=kappa)

    assert lam_tight > lam_wide
    assert pytest.approx(lam_wide / lam_tight, rel=1e-4) == math.exp(-kappa * (1.0 - 0.1))


def test_fill_probability_bounds():
    """Fill probability is strictly bounded in [0, 1]."""
    p_zero_dt = compute_fill_probability(delta=0.5, A=100.0, kappa=1.5, dt=0.0)
    assert p_zero_dt == 0.0

    p_normal = compute_fill_probability(delta=0.5, A=100.0, kappa=1.5, dt=0.005)
    assert 0.0 < p_normal < 1.0

    p_huge_intensity = compute_fill_probability(delta=0.0, A=1e6, kappa=1.5, dt=1.0)
    assert pytest.approx(p_huge_intensity, abs=1e-5) == 1.0


def test_compute_fill_probabilities_pair():
    """compute_fill_probabilities returns valid pair bounded in [0, 1]."""
    p_b, p_a = compute_fill_probabilities(delta_bid=0.05, delta_ask=0.05, A=140.0, kappa=1.5, dt=1.0/390.0)
    assert 0.0 <= p_b <= 1.0
    assert 0.0 <= p_a <= 1.0
    assert p_b == pytest.approx(p_a, rel=1e-5)


# ===========================================================================
# 4. GBM Price Path Generator Tests
# ===========================================================================

def test_generate_gbm_price_path():
    """GBM path generator produces positive prices of specified length."""
    path = generate_gbm_price_path(s0=100.0, sigma=0.25, dt=1.0/390.0, steps=390, seed=123)
    assert len(path) == 390
    assert path[0] == pytest.approx(100.0, rel=1e-5)
    assert np.all(path > 0.0)

    # Seed determinism
    path2 = generate_gbm_price_path(s0=100.0, sigma=0.25, dt=1.0/390.0, steps=390, seed=123)
    np.testing.assert_allclose(path, path2)


# ===========================================================================
# 5. Full 390-Minute Intraday MM Simulation & Attribution Tests (Workstream 4)
# ===========================================================================

def test_simulate_market_maker_execution_390_minutes():
    """Simulates a full 390-minute trading day and verifies all performance attribution fields."""
    price_path = generate_gbm_price_path(s0=150.0, sigma=0.20, dt=1.0/390.0, steps=390, seed=42)

    result = simulate_market_maker_execution(
        price_path=price_path,
        gamma=0.1,
        sigma=0.20,
        kappa=1.5,
        A=140.0,
        dt=1.0/390.0,
        max_inventory=10,
        seed=42,
    )

    assert isinstance(result, MarketMakerSessionResult)

    # Validate required metrics
    assert isinstance(result.total_pnl, float)
    assert isinstance(result.spread_capture, float)
    assert isinstance(result.inventory_holding_penalty, float)
    assert isinstance(result.adverse_selection_loss, float)
    assert isinstance(result.max_abs_inventory, int)
    assert isinstance(result.terminal_inventory, int)
    assert isinstance(result.sharpe_ratio, float)

    # Verification of values
    assert result.spread_capture >= 0.0
    assert result.inventory_holding_penalty >= 0.0
    assert result.adverse_selection_loss >= 0.0
    assert 0 <= result.max_abs_inventory <= 10
    assert abs(result.terminal_inventory) <= 10
    assert result.total_trades == result.buy_trades + result.sell_trades
    assert result.round_trip_trades == min(result.buy_trades, result.sell_trades)

    # Series lengths
    assert len(result.pnl_series) == 390
    assert len(result.inventory_series) == 390
    assert len(result.price_series) == 390
    assert len(result.history) == 389

    # Dictionary indexing support
    assert result["total_pnl"] == result.total_pnl
    assert result["spread_capture"] == result.spread_capture
    assert result.get("sharpe_ratio") == result.sharpe_ratio

    # JSON serialization check
    res_dict = result.to_dict()
    assert isinstance(res_dict, dict)
    json_str = json.dumps(res_dict)
    assert "total_pnl" in json_str


def test_simulate_market_maker_execution_flat_market():
    """In a completely flat price path, market maker captures spread with minimal inventory drift."""
    flat_path = np.full(100, 100.0)
    result = simulate_market_maker_execution(
        price_path=flat_path,
        gamma=0.1,
        sigma=0.01,
        kappa=2.0,
        A=200.0,
        dt=1.0/390.0,
        max_inventory=5,
        seed=999,
    )

    assert result.spread_capture >= 0.0
    assert result.max_abs_inventory <= 5
    assert result.adverse_selection_loss == pytest.approx(0.0, abs=1e-5)


# ===========================================================================
# 6. Policy Optimizer (train_market_maker_policy) Tests (Workstream 4)
# ===========================================================================

def test_train_market_maker_policy_execution():
    """Trains policy optimizer for 10 episodes and verifies parameter tuning and convergence."""
    path = generate_gbm_price_path(s0=100.0, steps=60, seed=42)
    opt_result = train_market_maker_policy(
        env=[path],
        episodes=10,
        learning_rate=0.1,
        gamma_bounds=(0.01, 0.50),
        kappa_bounds=(0.5, 3.0),
        seed=42,
    )

    assert isinstance(opt_result, PolicyOptimizationResult)
    assert 0.01 <= opt_result.best_gamma <= 0.50
    assert 0.5 <= opt_result.best_kappa <= 3.0
    assert opt_result.episodes_trained == 10
    assert len(opt_result.training_history) == 10
    assert opt_result.converged is True

    # Dict access
    assert opt_result["best_gamma"] == opt_result.best_gamma
    assert opt_result.get("best_kappa") == opt_result.best_kappa

    # JSON export
    res_dict = opt_result.to_dict()
    assert "best_gamma" in res_dict
    assert json.dumps(res_dict) is not None


# ===========================================================================
# 7. MarketMakingEnv Lifecycle & Dynamics Tests
# ===========================================================================

def test_env_reset():
    """Environment reset initializes all variables and returns valid observation."""
    cfg = MarketMakingConfig(initial_price=100.0, max_inventory=10, T=1.0, num_steps=200, seed=123)
    env = MarketMakingEnv(config=cfg)

    obs, info = env.reset(seed=123)
    assert isinstance(obs, np.ndarray)
    assert obs.shape == (6,)
    assert info["mid_price"] == 100.0
    assert info["inventory"] == 0.0
    assert info["cash"] == 0.0
    assert info["pnl"] == 0.0


def test_env_step_as_policy():
    """Environment executes steps under Avellaneda-Stoikov policy."""
    cfg = MarketMakingConfig(initial_price=100.0, num_steps=50, seed=42)
    env = MarketMakingEnv(config=cfg)
    env.reset(seed=42)

    obs, reward, terminated, truncated, info = env.step(action="as")
    assert isinstance(obs, np.ndarray)
    assert obs.shape == (6,)
    assert isinstance(reward, float)
    assert isinstance(terminated, bool)
    assert not truncated
    assert "bid_filled" in info
    assert "ask_filled" in info


def test_env_accounting_identities():
    """Validates mark-to-market and cash accounting identities at every step."""
    cfg = MarketMakingConfig(initial_price=100.0, num_steps=20, seed=77)
    env = MarketMakingEnv(config=cfg)
    env.reset(seed=77)

    for _ in range(15):
        obs, reward, term, _, info = env.step(action="as")
        # Accounting Identity: MtM = Cash + Inventory * MidPrice
        expected_mtm = env.cash + (env.inventory * env.mid_price)
        assert pytest.approx(env.mtm_value, abs=1e-6) == expected_mtm
        if term:
            break


def test_env_inventory_limits_enforced():
    """When inventory reaches max_inventory, buy quotes cannot be filled."""
    cfg = MarketMakingConfig(initial_price=100.0, max_inventory=2, order_size=1.0, num_steps=50, seed=99)
    env = MarketMakingEnv(config=cfg)
    env.reset()

    # Artificially set inventory to limit
    env.inventory = 2.0

    # Step with zero bid spread (would normally guarantee fill if allowed)
    obs, reward, term, _, info = env.step(action=[0.001, 10.0])
    assert info["bid_fill_prob"] == 0.0
    assert not info["bid_filled"]
    assert env.inventory <= cfg.max_inventory


def test_env_custom_continuous_actions():
    """Environment accepts custom 2D continuous action vectors [delta_bid, delta_ask]."""
    cfg = MarketMakingConfig(num_steps=10, seed=10)
    env = MarketMakingEnv(config=cfg)
    env.reset()

    custom_action = np.array([0.25, 0.35], dtype=np.float64)
    obs, reward, terminated, _, info = env.step(action=custom_action)

    last_step = env.history[-1]
    assert last_step.bid_spread == pytest.approx(0.25, abs=1e-6)
    assert last_step.ask_spread == pytest.approx(0.35, abs=1e-6)


def test_env_full_episode_simulation():
    """Simulate complete episode from t=0 to t=T and verify metric completeness."""
    cfg = MarketMakingConfig(initial_price=100.0, num_steps=100, seed=42)
    env = MarketMakingEnv(config=cfg)
    history, metrics = env.simulate_episode(policy="as")

    assert len(history) == 100
    assert isinstance(metrics, MarketMakerMetrics)
    assert isinstance(metrics.total_pnl, float)
    assert isinstance(metrics.sharpe_ratio, float)
    assert metrics.total_trades >= 0
    assert 0.0 <= metrics.bid_fill_rate <= 1.0
    assert 0.0 <= metrics.ask_fill_rate <= 1.0
    assert metrics.inventory_variance >= 0.0


# ===========================================================================
# 8. Monte Carlo Strategy Comparison Tests
# ===========================================================================

def test_compare_market_making_strategies_inventory_dampening():
    """Avellaneda-Stoikov significantly dampens inventory variance compared to naive symmetric quoting."""
    cfg = MarketMakingConfig(
        initial_price=100.0,
        sigma=0.3,
        gamma=0.20,
        kappa=1.5,
        A=140.0,
        T=1.0,
        num_steps=150,
        seed=100,
    )

    results = compare_market_making_strategies(config=cfg, num_simulations=40, seed=100)

    as_res = results["avellaneda_stoikov"]
    sym_res = results["symmetric_naive"]

    # Avellaneda-Stoikov should have lower mean inventory variance due to asymmetric skewing
    assert as_res["mean_inventory_variance"] < sym_res["mean_inventory_variance"]
    assert results["inventory_variance_reduction_pct"] > 0.0


# ===========================================================================
# 9. Edge Cases & Boundary Robustness (Never Raises)
# ===========================================================================

def test_simulate_empty_and_short_paths():
    """Handles empty or single-element price paths gracefully without crashing."""
    res_empty = simulate_market_maker_execution(price_path=[])
    assert res_empty.total_pnl == 0.0

    res_single = simulate_market_maker_execution(price_path=[120.0])
    assert res_single.total_pnl == 0.0


def test_extreme_market_making_parameters():
    """Handles extreme parameters (very high gamma, zero sigma, etc.) safely."""
    path = [100.0, 101.0, 99.0, 100.0]
    res = simulate_market_maker_execution(
        price_path=path,
        gamma=100.0,
        sigma=0.0001,
        kappa=100.0,
        max_inventory=1,
    )
    assert isinstance(res, MarketMakerSessionResult)


# ===========================================================================
# 10. AST Import Safety Test (CONSTRAINT #1 & #3)
# ===========================================================================

def test_drl_market_maker_ast_import_safety():
    """Verifies that ml/drl_market_maker.py NEVER imports heavy forbidden engines."""
    file_path = Path(__file__).resolve().parent.parent / "ml" / "drl_market_maker.py"
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename="drl_market_maker.py")

    forbidden_modules = {
        "processing_engine",
        "technical_options_engine",
        "forecasting_engine",
        "strategy_engine",
        "macro_engine",
        "main",
        "main_orchestrator",
        "desktop",
        "gui",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in forbidden_modules:
                    assert forbidden not in alias.name, f"Forbidden import found: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for forbidden in forbidden_modules:
                    assert forbidden not in node.module, f"Forbidden import from found: {node.module}"
