"""
ml/drl_market_maker_ppo.py
===========================
A real Proximal Policy Optimization (PPO) agent for Avellaneda-Stoikov market
making, trained against the existing ``ml.drl_market_maker.MarketMakingEnv``.

WHY THIS MODULE EXISTS: ``ml/drl_market_maker.py``'s own "HONEST STATUS" docstring
(Section 5) states plainly that ``train_market_maker_policy`` -- despite the
module's "DRL" framing and the master plan's Phase 22 "Deep RL" label -- is a
2-parameter (gamma, kappa) stochastic hill-climb, NOT a neural network or
policy-gradient method. This module is the real thing: a genuine actor-critic
policy network, trained via PPO's clipped surrogate objective + Generalized
Advantage Estimation (GAE), that learns a STATE-DEPENDENT quoting policy
(direct ``[delta_bid, delta_ask]`` half-spread offsets conditioned on the
env's own 6-dim observation -- inventory, time remaining, price drift, vol,
reservation-spread, running PnL) rather than 2 global constants applied for
an entire episode. That state-dependence is the actual point of using RL here
at all: a closed-form Avellaneda-Stoikov quote reacts to inventory/time only
through the fixed analytical formula; a trained policy can in principle learn
asymmetric, non-analytic skewing behavior the closed form cannot express.

PURE NUMPY, NOT TORCH -- matching this ml/ package's own established
convention (see ``ml/transformer_vol_forecaster.py``'s full Temporal Fusion
Transformer implementation in pure NumPy/SciPy) rather than introducing a new
heavy dependency: ``torch`` is listed in ``requirements-optional.txt`` but is
NOT actually installed in this repo's own committed .venv (Python 3.12) nor
importable under the system Python this module was authored against (Python
3.14 -- PyTorch wheels routinely lag several months behind a new CPython
release). A hand-rolled 2-layer MLP actor-critic with hand-derived backprop
is fully sufficient for a state space this small (6 observations) and keeps
this module installable with zero new dependencies. The backprop math is
verified against finite-difference numerical gradients in
``tests/test_drl_market_maker_ppo.py::TestGradientCorrectness`` -- a
hand-derived-but-wrong backward pass would silently fail to train anything
while still "looking like" real PPO, which is exactly the kind of
plausible-but-fake implementation this repo's own conventions (CONSTRAINT #4)
exist to catch; the gradient check is what makes the "real" claim verifiable
rather than asserted.

ACTION SPACE: this module uses ``MarketMakingEnv.step()``'s ``[delta_bid,
delta_ask]`` direct-quote-offset action shape (a first-class, already-supported
action form -- see that method's own branch for a ``list/tuple/np.ndarray``
of length >= 2), not the ``{"gamma": ..., "kappa": ...}`` shape
``train_market_maker_policy`` uses. The raw network output is passed through
a softplus transform (``log(1 + exp(x))``) to guarantee non-negative half-spreads
without a hard clip boundary; importance-sampling ratios for the PPO
objective are computed in the pre-transform (raw, Gaussian) space, which is
exact -- no Jacobian correction is needed because both the old and new policy
evaluate the density of the SAME stored raw sample, and the deterministic
softplus transform cancels out of the ratio consistently in the same way it
would for a torch/tensorflow squashed-Gaussian policy.

NOT REGISTERED IN STRATEGY_REGISTRY, NOT WIRED TO ANY API ENDPOINT OR WEBAPP
SCREEN as of this module's introduction -- see ``docs/VALIDATION_STRATEGY_FIX_LOG.md``
for why PBO/DSR don't apply to this kind of policy at all (same reasoning as
``train_market_maker_policy``'s own exemption entry), and
``.claude/pr788_options_desk_hardening_walkthrough.md``'s follow-on scoping
notes for the wiring status.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ml.drl_market_maker import (
    MarketMakingConfig,
    MarketMakingEnv,
    MarketMakerMetrics,
    generate_gbm_price_path,
)

EPSILON = 1e-8

__all__ = [
    "PPOConfig",
    "MLPActorCritic",
    "RolloutBuffer",
    "compute_gae",
    "train_ppo_market_maker",
    "evaluate_ppo_policy",
    "PPOTrainingResult",
]


# ===========================================================================
# 1. Numerically-stable primitives
# ===========================================================================

def _softplus(x: np.ndarray) -> np.ndarray:
    """log(1 + exp(x)), numerically stable for large |x|."""
    return np.where(x > 20.0, x, np.log1p(np.exp(-np.abs(x))) + np.maximum(x, 0.0))


def _gaussian_log_prob(raw: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    """Diagonal-covariance Gaussian log-density, summed over the action dimension.

    raw, mean: (..., act_dim). std: (act_dim,). Returns (...,).
    """
    z = (raw - mean) / std
    return -0.5 * np.sum(z * z + 2.0 * np.log(std) + math.log(2.0 * math.pi), axis=-1)


def _gaussian_entropy(std: np.ndarray) -> float:
    """Differential entropy of a diagonal Gaussian, summed over dimensions."""
    return float(np.sum(0.5 * math.log(2.0 * math.pi * math.e) + np.log(std)))


# ===========================================================================
# 2. Actor-Critic network: hand-rolled 2-layer MLP, forward + backward
# ===========================================================================

class MLPActorCritic:
    """Shared-trunk actor-critic: Linear(obs->H) -> tanh -> {policy head, value head}.

    Policy head outputs a per-action-dim Gaussian mean; ``log_std`` is a free
    parameter vector (state-independent), the standard convention for
    continuous-action PPO. Value head outputs a scalar state-value estimate.

    All forward/backward math is hand-derived (see this module's own
    docstring for why) and verified against finite differences in the test
    suite -- see that test before trusting a change to this class's math.
    """

    def __init__(
        self,
        obs_dim: int,
        act_dim: int,
        hidden_dim: int = 32,
        seed: int = 0,
        init_log_std: float = -0.5,
    ) -> None:
        rng = np.random.default_rng(seed)
        # Small random init (Xavier-ish) keeps the initial policy close to a
        # near-zero-mean Gaussian in the raw (pre-softplus) space -- softplus(0)
        # = log(2) ~= 0.69, a modest, sane starting half-spread.
        scale1 = math.sqrt(1.0 / max(1, obs_dim))
        scale2 = math.sqrt(1.0 / max(1, hidden_dim))
        self.W1 = rng.normal(0.0, scale1, size=(hidden_dim, obs_dim))
        self.b1 = np.zeros(hidden_dim)
        self.W_pi = rng.normal(0.0, scale2, size=(act_dim, hidden_dim))
        self.b_pi = np.zeros(act_dim)
        self.W_v = rng.normal(0.0, scale2, size=(1, hidden_dim))
        self.b_v = np.zeros(1)
        self.log_std = np.full(act_dim, init_log_std, dtype=np.float64)

        self.obs_dim = obs_dim
        self.act_dim = act_dim
        self.hidden_dim = hidden_dim

        self._adam_m: Dict[str, np.ndarray] = {}
        self._adam_v: Dict[str, np.ndarray] = {}
        self._adam_t: int = 0

    # -- parameter access -----------------------------------------------

    def parameters(self) -> Dict[str, np.ndarray]:
        return {
            "W1": self.W1, "b1": self.b1,
            "W_pi": self.W_pi, "b_pi": self.b_pi,
            "W_v": self.W_v, "b_v": self.b_v,
            "log_std": self.log_std,
        }

    def clone(self) -> "MLPActorCritic":
        """Deep copy, used to snapshot the pre-update ("old") policy for the
        PPO importance-sampling ratio."""
        clone = MLPActorCritic(self.obs_dim, self.act_dim, self.hidden_dim)
        for name, p in self.parameters().items():
            setattr(clone, name, np.array(p, copy=True))
        return clone

    # -- forward -----------------------------------------------------------

    def forward(
        self, obs: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """obs: (obs_dim,) or (N, obs_dim). Returns (mean, value, h, pre), each
        batched to (N, ...) internally then squeezed back if input was 1-D."""
        single = obs.ndim == 1
        x = obs[None, :] if single else obs
        pre = x @ self.W1.T + self.b1          # (N, H)
        h = np.tanh(pre)                        # (N, H)
        mean = h @ self.W_pi.T + self.b_pi      # (N, A)
        value = (h @ self.W_v.T + self.b_v)[:, 0]  # (N,)
        if single:
            return mean[0], value[0], h[0], pre[0]
        return mean, value, h, pre

    def act(
        self, obs: np.ndarray, rng: np.random.Generator, deterministic: bool = False
    ) -> Tuple[np.ndarray, np.ndarray, float, float]:
        """Samples an action for a SINGLE observation.

        Returns (env_action, raw_sample, log_prob, value) -- env_action is the
        softplus-transformed [delta_bid, delta_ask] to feed MarketMakingEnv.step();
        raw_sample and log_prob must be stored in the rollout buffer for the
        PPO update (never recompute log_prob from env_action -- the transform
        is not invertible in closed form the way this module needs).
        """
        mean, value, _h, _pre = self.forward(obs)
        std = np.exp(self.log_std)
        if deterministic:
            raw = mean.copy()
        else:
            raw = mean + std * rng.normal(size=mean.shape)
        log_prob = float(_gaussian_log_prob(raw, mean, std))
        env_action = _softplus(raw)
        return env_action, raw, log_prob, float(value)

    # -- backward (batched) -------------------------------------------------

    def compute_ppo_gradients(
        self,
        obs: np.ndarray,           # (N, obs_dim)
        raw_actions: np.ndarray,   # (N, act_dim)
        old_log_probs: np.ndarray,  # (N,)
        advantages: np.ndarray,    # (N,)
        returns: np.ndarray,       # (N,)
        clip_eps: float,
        value_coef: float,
        entropy_coef: float,
    ) -> Tuple[Dict[str, np.ndarray], Dict[str, float]]:
        """One PPO minibatch: forward pass, clipped-surrogate + value + entropy
        loss, and hand-derived gradients w.r.t. every parameter.

        Returns (grads, diagnostics). diagnostics carries the scalar loss
        components and the mean KL-ish log-ratio, useful for logging/early
        stopping but not required for correctness.
        """
        n = obs.shape[0]
        mean, value, h, _pre = self.forward(obs)     # (N,A), (N,), (N,H)
        std = np.exp(self.log_std)                    # (A,)

        new_log_probs = _gaussian_log_prob(raw_actions, mean, std)  # (N,)
        log_ratio = new_log_probs - old_log_probs
        ratio = np.exp(log_ratio)                      # (N,)

        surr1 = ratio * advantages
        surr2 = np.clip(ratio, 1.0 - clip_eps, 1.0 + clip_eps) * advantages
        # PPO-clip's well-known gradient rule: the surrogate objective is
        # min(surr1, surr2); its subgradient w.r.t. ratio is `advantage`
        # whenever surr1 <= surr2 (the unclipped term achieves the min, or
        # they're tied at the clip boundary) and exactly 0 otherwise (the
        # clipped term is strictly smaller, i.e. clip() is "active" and its
        # own gradient region is flat).
        unclipped_is_min = surr1 <= surr2
        policy_loss = -np.mean(np.minimum(surr1, surr2))

        value_loss = np.mean((value - returns) ** 2)
        entropy = _gaussian_entropy(std)

        total_loss = policy_loss + value_coef * value_loss - entropy_coef * entropy

        # ---- gradients --------------------------------------------------
        # d(policy_loss)/d(ratio_i) = -[unclipped_is_min_i] * advantage_i / N
        dL_dratio = np.where(unclipped_is_min, -advantages / n, 0.0)   # (N,)
        # d(ratio)/d(new_log_prob) = ratio
        dL_dlogprob = dL_dratio * ratio                                 # (N,)

        # d(log_prob)/d(mean) = (raw - mean) / std^2  -- shape (N, A)
        z = (raw_actions - mean) / std                                  # (N, A)
        dL_dmean = dL_dlogprob[:, None] * (z / std)                     # (N, A)

        # d(log_prob)/d(log_std_a) = z_a^2 - 1  (per action dim, summed over batch)
        dL_dlogstd_policy = np.sum(dL_dlogprob[:, None] * (z * z - 1.0), axis=0)  # (A,)
        # Entropy term: d(entropy)/d(log_std_a) = 1 (state-independent);
        # loss contribution is -entropy_coef * entropy.
        dL_dlogstd_entropy = -entropy_coef * np.ones_like(self.log_std)
        dL_dlogstd = dL_dlogstd_policy + dL_dlogstd_entropy

        # Value head: loss = value_coef * mean((value - return)^2)
        dL_dvalue = value_coef * 2.0 * (value - returns) / n            # (N,)

        # ---- backprop through the two heads into the shared trunk -------
        dL_dWpi = dL_dmean.T @ h                                        # (A, H)
        dL_dbpi = np.sum(dL_dmean, axis=0)                              # (A,)
        dL_dh_from_pi = dL_dmean @ self.W_pi                            # (N, H)

        dL_dWv = (dL_dvalue[None, :] @ h)                               # (1, H)
        dL_dbv = np.array([np.sum(dL_dvalue)])                          # (1,)
        dL_dh_from_v = dL_dvalue[:, None] @ self.W_v                    # (N, H)

        dL_dh = dL_dh_from_pi + dL_dh_from_v                            # (N, H)
        dL_dpre = dL_dh * (1.0 - h * h)                                 # tanh' , (N, H)

        dL_dW1 = dL_dpre.T @ obs                                        # (H, obs_dim)
        dL_db1 = np.sum(dL_dpre, axis=0)                                # (H,)

        grads = {
            "W1": dL_dW1, "b1": dL_db1,
            "W_pi": dL_dWpi, "b_pi": dL_dbpi,
            "W_v": dL_dWv, "b_v": dL_dbv,
            "log_std": dL_dlogstd,
        }
        diagnostics = {
            "policy_loss": float(policy_loss),
            "value_loss": float(value_loss),
            "entropy": float(entropy),
            "total_loss": float(total_loss),
            "approx_kl": float(np.mean(np.abs(log_ratio))),
            "clip_fraction": float(np.mean(~unclipped_is_min)),
        }
        return grads, diagnostics

    # -- optimizer: hand-rolled Adam ----------------------------------------

    def adam_step(
        self,
        grads: Dict[str, np.ndarray],
        lr: float,
        beta1: float = 0.9,
        beta2: float = 0.999,
        eps: float = 1e-8,
        max_grad_norm: Optional[float] = 0.5,
    ) -> None:
        """One Adam update over every parameter, with optional global
        gradient-norm clipping (computed jointly across all parameters, the
        standard convention -- not per-parameter)."""
        params = self.parameters()

        if max_grad_norm is not None:
            total_sq = sum(float(np.sum(g * g)) for g in grads.values())
            total_norm = math.sqrt(total_sq)
            if total_norm > max_grad_norm and total_norm > EPSILON:
                scale = max_grad_norm / total_norm
                grads = {k: g * scale for k, g in grads.items()}

        self._adam_t += 1
        t = self._adam_t
        for name, g in grads.items():
            if name not in self._adam_m:
                self._adam_m[name] = np.zeros_like(params[name])
                self._adam_v[name] = np.zeros_like(params[name])
            m = self._adam_m[name] = beta1 * self._adam_m[name] + (1.0 - beta1) * g
            v = self._adam_v[name] = beta2 * self._adam_v[name] + (1.0 - beta2) * (g * g)
            m_hat = m / (1.0 - beta1 ** t)
            v_hat = v / (1.0 - beta2 ** t)
            update = lr * m_hat / (np.sqrt(v_hat) + eps)
            params[name] -= update


# ===========================================================================
# 3. Rollout buffer + GAE
# ===========================================================================

@dataclass
class RolloutBuffer:
    obs: List[np.ndarray] = field(default_factory=list)
    raw_actions: List[np.ndarray] = field(default_factory=list)
    log_probs: List[float] = field(default_factory=list)
    values: List[float] = field(default_factory=list)
    rewards: List[float] = field(default_factory=list)
    dones: List[bool] = field(default_factory=list)

    def add(self, obs, raw_action, log_prob, value, reward, done) -> None:
        self.obs.append(obs)
        self.raw_actions.append(raw_action)
        self.log_probs.append(log_prob)
        self.values.append(value)
        self.rewards.append(reward)
        self.dones.append(done)

    def __len__(self) -> int:
        return len(self.obs)

    def clear(self) -> None:
        self.obs.clear()
        self.raw_actions.clear()
        self.log_probs.clear()
        self.values.clear()
        self.rewards.clear()
        self.dones.clear()


def compute_gae(
    rewards: np.ndarray,
    values: np.ndarray,
    dones: np.ndarray,
    last_value: float,
    gamma: float,
    gae_lambda: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """Generalized Advantage Estimation (Schulman et al. 2016).

    rewards, values, dones: (T,) arrays for one or more concatenated
    episodes (dones marks true episode boundaries, so advantage/return
    bootstrapping never leaks across an episode end). last_value is the
    value estimate to bootstrap from after the FINAL stored step (0.0 is
    correct when that step was itself terminal).

    Returns (advantages, returns), each (T,).
    """
    t_len = len(rewards)
    advantages = np.zeros(t_len, dtype=np.float64)
    gae = 0.0
    next_value = last_value
    for t in reversed(range(t_len)):
        next_non_terminal = 0.0 if dones[t] else 1.0
        delta = rewards[t] + gamma * next_value * next_non_terminal - values[t]
        gae = delta + gamma * gae_lambda * next_non_terminal * gae
        advantages[t] = gae
        next_value = values[t]
    returns = advantages + values
    return advantages, returns


# ===========================================================================
# 4. Training loop
# ===========================================================================

@dataclass
class PPOConfig:
    hidden_dim: int = 32
    learning_rate: float = 3e-4
    gamma: float = 0.999           # per-STEP discount (390 steps/session -> ~0.68 over a full session)
    gae_lambda: float = 0.95
    clip_eps: float = 0.2
    value_coef: float = 0.5
    entropy_coef: float = 0.01
    epochs_per_update: int = 4
    minibatch_size: int = 64
    max_grad_norm: float = 0.5
    init_log_std: float = -0.5
    seed: int = 42


@dataclass
class PPOTrainingResult:
    """Result of a PPO training run -- deliberately mirrors
    ``ml.drl_market_maker.PolicyOptimizationResult.to_dict()``'s field
    naming style (best_*, episodes_trained, training_history, converged)
    so it can be presented through a compatible response shape, without
    being byte-identical (this is a genuinely different algorithm with
    genuinely different diagnostics -- e.g. approx_kl/clip_fraction have
    no hill-climb equivalent)."""

    final_mean_reward: float
    best_mean_reward: float
    n_iterations: int
    episodes_per_iteration: int
    training_history: List[Dict[str, Any]] = field(default_factory=list)
    converged: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "final_mean_reward": float(self.final_mean_reward),
            "best_mean_reward": float(self.best_mean_reward),
            "n_iterations": int(self.n_iterations),
            "episodes_per_iteration": int(self.episodes_per_iteration),
            "training_history": self.training_history,
            "converged": bool(self.converged),
        }


def _collect_rollouts(
    env: MarketMakingEnv,
    net: MLPActorCritic,
    rng: np.random.Generator,
    episodes_per_iteration: int,
) -> RolloutBuffer:
    buf = RolloutBuffer()
    for _ in range(episodes_per_iteration):
        obs, _info = env.reset(seed=int(rng.integers(0, 1_000_000)))
        terminated = False
        while not terminated:
            env_action, raw, log_prob, value = net.act(obs, rng, deterministic=False)
            next_obs, reward, terminated, _truncated, _info = env.step(env_action)
            buf.add(obs, raw, log_prob, value, reward, terminated)
            obs = next_obs
    return buf


def train_ppo_market_maker(
    env: Optional[MarketMakingEnv] = None,
    price_paths: Optional[List[np.ndarray]] = None,
    config: Optional[PPOConfig] = None,
    n_iterations: int = 50,
    episodes_per_iteration: int = 4,
    convergence_window_frac: float = 0.2,
    convergence_min_window: int = 10,
) -> Tuple[MLPActorCritic, PPOTrainingResult]:
    """Trains a real PPO actor-critic policy against ``MarketMakingEnv``.

    Each iteration: collect ``episodes_per_iteration`` full episodes under
    the CURRENT (stochastic) policy, compute GAE advantages/returns, then run
    ``config.epochs_per_update`` epochs of minibatch Adam updates over that
    batch (standard on-policy PPO -- data is discarded after each iteration,
    never replayed, matching PPO's on-policy requirement).

    ``converged`` uses the SAME honest plateau-based signal convention as
    ``ml.drl_market_maker.train_market_maker_policy`` (best mean-episode-reward
    hasn't improved for the trailing 20% of iterations, minimum 10) rather
    than a hardcoded constant -- see that function's own fix
    (docs/VALIDATION_STRATEGY_FIX_LOG.md, 2026-08) for why a hardcoded
    `converged=True` is a real, previously-shipped honesty bug this module
    does not want to repeat.
    """
    cfg = config or PPOConfig()
    rng = np.random.default_rng(cfg.seed)

    if env is None:
        paths = price_paths or [generate_gbm_price_path(seed=cfg.seed + i) for i in range(8)]
        env = MarketMakingEnv(price_paths=paths, seed=cfg.seed)

    net = MLPActorCritic(
        obs_dim=env.observation_dim,
        act_dim=env.action_dim,
        hidden_dim=cfg.hidden_dim,
        seed=cfg.seed,
        init_log_std=cfg.init_log_std,
    )

    history: List[Dict[str, Any]] = []
    best_mean_reward = -float("inf")
    last_improved_iter = 0
    final_mean_reward = -float("inf")

    for it in range(n_iterations):
        buf = _collect_rollouts(env, net, rng, episodes_per_iteration)

        rewards = np.asarray(buf.rewards, dtype=np.float64)
        values = np.asarray(buf.values, dtype=np.float64)
        dones = np.asarray(buf.dones, dtype=bool)
        obs_arr = np.asarray(buf.obs, dtype=np.float64)
        raw_arr = np.asarray(buf.raw_actions, dtype=np.float64)
        old_log_probs = np.asarray(buf.log_probs, dtype=np.float64)

        # Every stored trajectory ends on a terminal step (episodes run to
        # completion in _collect_rollouts), so the bootstrap value after the
        # final stored step is always 0.0 -- never a truncated mid-episode cut.
        advantages, returns = compute_gae(
            rewards, values, dones, last_value=0.0, gamma=cfg.gamma, gae_lambda=cfg.gae_lambda,
        )
        # Advantage normalization -- standard PPO stabilization, guarded
        # against a degenerate (near-constant) batch per this repo's own
        # degenerate-std convention (CLAUDE.md: any ratio dividing by a
        # computed std must guard with < 1e-12, never an exact == 0 check).
        adv_std = float(np.std(advantages))
        if adv_std >= 1e-12:
            advantages = (advantages - np.mean(advantages)) / adv_std

        n_samples = len(buf)
        indices = np.arange(n_samples)
        last_diag: Dict[str, float] = {}
        for _epoch in range(cfg.epochs_per_update):
            rng.shuffle(indices)
            for start in range(0, n_samples, cfg.minibatch_size):
                mb_idx = indices[start:start + cfg.minibatch_size]
                grads, diag = net.compute_ppo_gradients(
                    obs_arr[mb_idx], raw_arr[mb_idx], old_log_probs[mb_idx],
                    advantages[mb_idx], returns[mb_idx],
                    clip_eps=cfg.clip_eps, value_coef=cfg.value_coef,
                    entropy_coef=cfg.entropy_coef,
                )
                net.adam_step(grads, lr=cfg.learning_rate, max_grad_norm=cfg.max_grad_norm)
                last_diag = diag

        # Mean per-episode reward this iteration (sum of step rewards / episode count).
        mean_ep_reward = float(np.sum(rewards) / max(1, episodes_per_iteration))
        final_mean_reward = mean_ep_reward
        if mean_ep_reward > best_mean_reward:
            best_mean_reward = mean_ep_reward
            last_improved_iter = it

        history.append({
            "iteration": it + 1,
            "mean_episode_reward": mean_ep_reward,
            "best_mean_episode_reward": best_mean_reward,
            **last_diag,
        })

    n_iters = max(1, n_iterations)
    convergence_window = max(convergence_min_window, int(convergence_window_frac * n_iters))
    iters_since_improvement = (n_iters - 1) - last_improved_iter
    converged = n_iters >= convergence_window and iters_since_improvement >= convergence_window

    result = PPOTrainingResult(
        final_mean_reward=final_mean_reward,
        best_mean_reward=best_mean_reward,
        n_iterations=n_iterations,
        episodes_per_iteration=episodes_per_iteration,
        training_history=history,
        converged=bool(converged),
    )
    return net, result


# ===========================================================================
# 5. Evaluation -- reuses MarketMakingEnv.compute_metrics() for an
#    apples-to-apples comparison against the closed-form AS quoter / hill-climb.
# ===========================================================================

def evaluate_ppo_policy(
    net: MLPActorCritic,
    env: MarketMakingEnv,
    n_episodes: int = 20,
    seed: int = 123,
    deterministic: bool = True,
) -> Dict[str, Any]:
    """Runs ``n_episodes`` full episodes under the trained policy (by default
    deterministic -- using the Gaussian mean, not a stochastic sample, which
    is the standard convention for EVALUATING a trained RL policy even though
    TRAINING samples stochastically) and returns the same
    ``MarketMakerMetrics`` fields ``ml.drl_market_maker.compare_market_making_strategies``
    already reports for the closed-form/hill-climb policies, so results are
    directly comparable -- not a bespoke metric set that would need its own
    interpretation.
    """
    rng = np.random.default_rng(seed)
    per_episode: List[MarketMakerMetrics] = []

    def _policy(e: MarketMakingEnv):
        obs = e.get_observation()
        env_action, _raw, _log_prob, _value = net.act(obs, rng, deterministic=deterministic)
        return env_action

    for _ in range(n_episodes):
        env.reset(seed=int(rng.integers(0, 1_000_000)))
        _history, metrics = env.simulate_episode(policy=_policy)
        per_episode.append(metrics)

    def _mean(attr: str) -> float:
        vals = [getattr(m, attr) for m in per_episode]
        return float(np.mean(vals)) if vals else 0.0

    return {
        "n_episodes": n_episodes,
        "mean_total_pnl": _mean("total_pnl"),
        "mean_sharpe_ratio": _mean("sharpe_ratio"),
        "mean_max_drawdown": _mean("max_drawdown"),
        "mean_inventory_variance": _mean("inventory_variance"),
        "mean_abs_inventory": _mean("mean_abs_inventory"),
        "mean_terminal_inventory": _mean("terminal_inventory"),
        "mean_bid_fill_rate": _mean("bid_fill_rate"),
        "mean_ask_fill_rate": _mean("ask_fill_rate"),
        "mean_total_trades": _mean("total_trades"),
        "mean_profit_factor": _mean(
            "profit_factor"
        ) if all(math.isfinite(m.profit_factor) for m in per_episode) else float("inf"),
    }
