"""Tests for ml/drl_market_maker_ppo.py -- the real PPO actor-critic agent.

TestGradientCorrectness is the single most important test in this file: it
verifies the hand-derived backward pass in MLPActorCritic.compute_ppo_gradients
against finite-difference numerical gradients. A hand-rolled backprop that is
subtly wrong would still run, still produce a "trained" policy, and would be
indistinguishable from a real implementation by inspection alone -- exactly
the kind of plausible-but-fake result this repo's own conventions (CONSTRAINT
#4) exist to catch. If this test ever fails after touching
compute_ppo_gradients, do not weaken the tolerance -- the math is wrong.
"""
from __future__ import annotations

import numpy as np
import pytest

from ml.drl_market_maker import MarketMakingEnv, generate_gbm_price_path
from ml.drl_market_maker_ppo import (
    MLPActorCritic,
    PPOConfig,
    RolloutBuffer,
    compute_gae,
    evaluate_ppo_policy,
    train_ppo_market_maker,
)


# ===========================================================================
# Gradient correctness (finite-difference check)
# ===========================================================================

class TestGradientCorrectness:
    def _make_batch(self, seed=0, n=16, obs_dim=6, act_dim=2):
        rng = np.random.default_rng(seed)
        net = MLPActorCritic(obs_dim=obs_dim, act_dim=act_dim, hidden_dim=8, seed=seed)
        obs = rng.normal(0.0, 1.0, size=(n, obs_dim))
        # Sample real actions/log_probs from the net itself so the batch is
        # internally consistent (raw actions plausible under the policy).
        raw_actions = np.zeros((n, act_dim))
        old_log_probs = np.zeros(n)
        for i in range(n):
            _env_action, raw, log_prob, _value = net.act(obs[i], rng, deterministic=False)
            raw_actions[i] = raw
            old_log_probs[i] = log_prob
        advantages = rng.normal(0.0, 1.0, size=n)
        returns = rng.normal(0.0, 1.0, size=n)
        return net, obs, raw_actions, old_log_probs, advantages, returns

    def _loss_only(self, net, obs, raw_actions, old_log_probs, advantages, returns, clip_eps, value_coef, entropy_coef):
        _grads, diag = net.compute_ppo_gradients(
            obs, raw_actions, old_log_probs, advantages, returns,
            clip_eps=clip_eps, value_coef=value_coef, entropy_coef=entropy_coef,
        )
        return diag["total_loss"]

    def test_gradients_match_finite_differences(self):
        net, obs, raw_actions, old_log_probs, advantages, returns = self._make_batch()
        clip_eps, value_coef, entropy_coef = 0.2, 0.5, 0.01

        grads, _diag = net.compute_ppo_gradients(
            obs, raw_actions, old_log_probs, advantages, returns,
            clip_eps=clip_eps, value_coef=value_coef, entropy_coef=entropy_coef,
        )

        eps = 1e-6
        rng_check = np.random.default_rng(99)
        for name, param in net.parameters().items():
            flat = param.ravel()
            grad_flat = grads[name].ravel()
            # Checking every element of every param is expensive; sample up
            # to 6 random indices per parameter array (or all if smaller).
            n_check = min(6, flat.size)
            idx_to_check = rng_check.choice(flat.size, size=n_check, replace=False)
            for idx in idx_to_check:
                orig = flat[idx]

                flat[idx] = orig + eps
                loss_plus = self._loss_only(net, obs, raw_actions, old_log_probs, advantages, returns, clip_eps, value_coef, entropy_coef)

                flat[idx] = orig - eps
                loss_minus = self._loss_only(net, obs, raw_actions, old_log_probs, advantages, returns, clip_eps, value_coef, entropy_coef)

                flat[idx] = orig  # restore

                numerical_grad = (loss_plus - loss_minus) / (2 * eps)
                analytic_grad = grad_flat[idx]
                assert numerical_grad == pytest.approx(analytic_grad, abs=2e-3, rel=2e-2), (
                    f"Gradient mismatch for {name}[{idx}]: "
                    f"analytic={analytic_grad}, numerical={numerical_grad}"
                )


# ===========================================================================
# GAE
# ===========================================================================

class TestComputeGae:
    def test_single_step_episode_advantage_is_delta(self):
        rewards = np.array([1.0])
        values = np.array([0.5])
        dones = np.array([True])
        adv, ret = compute_gae(rewards, values, dones, last_value=0.0, gamma=0.99, gae_lambda=0.95)
        # dones[0]=True -> next_non_terminal=0 -> delta = reward - value = 0.5
        assert adv[0] == pytest.approx(0.5)
        assert ret[0] == pytest.approx(0.5 + 0.5)

    def test_terminal_flag_prevents_bootstrap_leak_across_episodes(self):
        # Two concatenated 1-step episodes; second episode's advantage must
        # NOT depend on values from the first.
        rewards = np.array([10.0, -10.0])
        values = np.array([0.0, 0.0])
        dones = np.array([True, True])
        adv, ret = compute_gae(rewards, values, dones, last_value=0.0, gamma=0.99, gae_lambda=0.95)
        assert adv[0] == pytest.approx(10.0)
        assert adv[1] == pytest.approx(-10.0)

    def test_returns_equal_advantages_plus_values(self):
        rng = np.random.default_rng(1)
        rewards = rng.normal(size=20)
        values = rng.normal(size=20)
        dones = np.zeros(20, dtype=bool)
        dones[-1] = True
        adv, ret = compute_gae(rewards, values, dones, last_value=0.0, gamma=0.95, gae_lambda=0.9)
        np.testing.assert_allclose(ret, adv + values)


# ===========================================================================
# RolloutBuffer
# ===========================================================================

class TestRolloutBuffer:
    def test_add_and_len(self):
        buf = RolloutBuffer()
        buf.add(np.zeros(6), np.zeros(2), 0.0, 0.0, 1.0, False)
        buf.add(np.zeros(6), np.zeros(2), 0.0, 0.0, 1.0, True)
        assert len(buf) == 2
        buf.clear()
        assert len(buf) == 0


# ===========================================================================
# Full training loop -- functional, not gradient-level
# ===========================================================================

class TestTrainPpoMarketMaker:
    def test_training_runs_and_produces_finite_results(self):
        paths = [generate_gbm_price_path(seed=i, steps=60) for i in range(4)]
        cfg = PPOConfig(hidden_dim=8, epochs_per_update=2, minibatch_size=16, seed=7)
        net, result = train_ppo_market_maker(
            price_paths=paths, config=cfg, n_iterations=6, episodes_per_iteration=2,
        )
        assert np.isfinite(result.final_mean_reward)
        assert np.isfinite(result.best_mean_reward)
        assert len(result.training_history) == 6
        assert isinstance(result.converged, bool)
        # Parameters must have moved from their initial values -- proof the
        # optimizer actually stepped, not just that the loop ran.
        fresh_net = MLPActorCritic(obs_dim=6, act_dim=2, hidden_dim=8, seed=7)
        assert not np.allclose(net.W1, fresh_net.W1)

    def test_to_dict_contract(self):
        paths = [generate_gbm_price_path(seed=i, steps=40) for i in range(2)]
        cfg = PPOConfig(hidden_dim=8, epochs_per_update=1, minibatch_size=16, seed=3)
        _net, result = train_ppo_market_maker(
            price_paths=paths, config=cfg, n_iterations=3, episodes_per_iteration=1,
        )
        d = result.to_dict()
        for key in ("final_mean_reward", "best_mean_reward", "n_iterations",
                    "episodes_per_iteration", "training_history", "converged"):
            assert key in d

    def test_convergence_signal_is_not_hardcoded(self):
        """A too-short run cannot demonstrate a plateau and must report
        converged=False -- the same honesty bar
        ml.drl_market_maker.train_market_maker_policy's own convergence fix
        established (docs/VALIDATION_STRATEGY_FIX_LOG.md, 2026-08)."""
        paths = [generate_gbm_price_path(seed=i, steps=40) for i in range(2)]
        cfg = PPOConfig(hidden_dim=8, epochs_per_update=1, minibatch_size=16, seed=5)
        _net, result = train_ppo_market_maker(
            price_paths=paths, config=cfg, n_iterations=3, episodes_per_iteration=1,
        )
        assert result.converged is False


# ===========================================================================
# Evaluation
# ===========================================================================

class TestEvaluatePpoPolicy:
    def test_returns_same_metric_shape_as_closed_form_comparison(self):
        paths = [generate_gbm_price_path(seed=i, steps=60) for i in range(3)]
        cfg = PPOConfig(hidden_dim=8, epochs_per_update=1, minibatch_size=16, seed=11)
        net, _result = train_ppo_market_maker(
            price_paths=paths, config=cfg, n_iterations=3, episodes_per_iteration=1,
        )
        env = MarketMakingEnv(price_paths=paths, seed=11)
        metrics = evaluate_ppo_policy(net, env, n_episodes=3, seed=1)
        for key in (
            "n_episodes", "mean_total_pnl", "mean_sharpe_ratio", "mean_max_drawdown",
            "mean_inventory_variance", "mean_abs_inventory", "mean_terminal_inventory",
            "mean_bid_fill_rate", "mean_ask_fill_rate", "mean_total_trades", "mean_profit_factor",
        ):
            assert key in metrics
        assert metrics["n_episodes"] == 3

    def test_deterministic_evaluation_is_reproducible(self):
        """Deterministic evaluation uses the Gaussian mean, not a stochastic
        sample -- two evaluation runs with the same trained net and seed
        must produce IDENTICAL metrics (the env's own fill-probability
        randomness is seeded, and the policy itself is deterministic)."""
        paths = [generate_gbm_price_path(seed=1, steps=60)]
        cfg = PPOConfig(hidden_dim=8, epochs_per_update=1, minibatch_size=16, seed=2)
        net, _result = train_ppo_market_maker(
            price_paths=paths, config=cfg, n_iterations=2, episodes_per_iteration=1,
        )
        env1 = MarketMakingEnv(price_paths=paths, seed=1)
        env2 = MarketMakingEnv(price_paths=paths, seed=1)
        m1 = evaluate_ppo_policy(net, env1, n_episodes=2, seed=42, deterministic=True)
        m2 = evaluate_ppo_policy(net, env2, n_episodes=2, seed=42, deterministic=True)
        assert m1 == m2


# ===========================================================================
# Action space contract
# ===========================================================================

class TestActionSpaceContract:
    def test_env_actions_are_always_non_negative(self):
        """MLPActorCritic.act() must always emit softplus-transformed,
        non-negative [delta_bid, delta_ask] -- MarketMakingEnv.step() itself
        clips negative offsets to 0.0, but a policy that routinely tries to
        emit negative spreads would be silently losing information at that
        clip boundary rather than genuinely learning valid quotes."""
        net = MLPActorCritic(obs_dim=6, act_dim=2, hidden_dim=8, seed=0)
        rng = np.random.default_rng(0)
        for _ in range(200):
            obs = rng.normal(0.0, 3.0, size=6)  # wide range, including extremes
            env_action, _raw, _log_prob, _value = net.act(obs, rng, deterministic=False)
            assert np.all(env_action >= 0.0)

    def test_ast_import_safety(self):
        """This module must never import processing_engine, matching every
        other pilots/ml module's AST-safety convention in this repo."""
        import ast
        with open("ml/drl_market_maker_ppo.py") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "processing_engine" not in alias.name
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert "processing_engine" not in node.module
