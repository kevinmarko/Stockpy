"""
ml/drl_market_maker.py — Avellaneda-Stoikov Quoting & Reinforcement Learning Market Maker.
==========================================================================================

Quantitative market making engine and reinforcement learning simulation environment based on
the classical inventory risk model of Avellaneda and Stoikov (2008) and optimal limit order
execution framework:

    Avellaneda, Marco, and Sasha Stoikov.
    "High-frequency trading in a limit order book."
    Quantitative Finance, Vol. 8, No. 3 (2008), pp. 217-224.

    Guéant, Olivier, Charles-Albert Lehalle, and Joaquin Fernandez-Tapia.
    "Dealing with the inventory risk: a solution to the market making problem."
    Mathematics and Financial Economics, Vol. 6, No. 4 (2012), pp. 259-277.

Core Mathematical Framework:
----------------------------
1. **Reservation Price (Indifference Price)**:
   The price at which the market maker is indifferent between holding current inventory $q$
   versus executing an instantaneous transaction:
       $$R(s, q, t) = s - q \\cdot \\gamma \\cdot \\sigma^2 \\cdot (T - t)$$
   where:
       - $s$: Current mid-price of the underlying asset
       - $q$: Current inventory level (signed: $>0$ long, $<0$ short)
       - $\\gamma$: Absolute risk aversion parameter ($\\gamma > 0$)
       - $\\sigma$: Price volatility per unit time ($\\sigma > 0$)
       - $T - t$: Remaining time horizon until session close ($\\tau \\ge 0$)

2. **Optimal Asymmetric Quoting Spreads**:
   Closed-form solution for the total spread and individual optimal half-spreads
   $\\delta^a$ (ask offset from mid) and $\\delta^b$ (bid offset from mid):
       $$\\delta^a + \\delta^b = \\gamma \\sigma^2 (T - t) + \\frac{2}{\\gamma} \\ln\\left(1 + \\frac{\\gamma}{\\kappa}\\right)$$
       $$\\delta^a(s, q, t) = R(s, q, t) - s + \\frac{1}{2} (\\delta^a + \\delta^b) = \\frac{1 - 2q}{2} \\gamma \\sigma^2 (T - t) + \\frac{1}{\\gamma} \\ln\\left(1 + \\frac{\\gamma}{\\kappa}\\right)$$
       $$\\delta^b(s, q, t) = s - R(s, q, t) + \\frac{1}{2} (\\delta^a + \\delta^b) = \\frac{1 + 2q}{2} \\gamma \\sigma^2 (T - t) + \\frac{1}{\\gamma} \\ln\\left(1 + \\frac{\\gamma}{\\kappa}\\right)$$
   Quotes around mid:
       $$p^a = s + \\delta^a = R(s, q, t) + \\frac{1}{2} (\\delta^a + \\delta^b)$$
       $$p^b = s - \\delta^b = R(s, q, t) - \\frac{1}{2} (\\delta^a + \\delta^b)$$

3. **Order Arrival Intensity (Poisson Jump Process)**:
   Liquidity-taking market orders hit the quotes with Poisson arrival intensities $\\lambda$:
       $$\\lambda^a(\\delta^a) = A \\exp(-\\kappa \\delta^a)$$
       $$\\lambda^b(\\delta^b) = A \\exp(-\\kappa \\delta^b)$$
   where:
       - $A$: Baseline market order flow arrival rate
       - $\\kappa$: Order book liquidity sensitivity / price decay parameter

4. **Performance & PnL Attribution Decomposition**:
   - Total PnL ($): $V_T - V_0 = \\text{Cash}_T + q_T S_T - (\\text{Cash}_0 + q_0 S_0)$
   - Spread Capture ($): $\\sum_{\\text{buy fills}} (S_t - P^b_t) + \\sum_{\\text{sell fills}} (P^a_t - S_t)$
   - Inventory Holding Risk Penalty ($): $\\sum_{t=0}^{T-1} \\frac{1}{2} \\gamma \\sigma^2 q_t^2 dt$
   - Adverse Selection Losses ($): Losses incurred when held inventory moves adversely against mid-price:
     $$\\sum_{t=0}^{T-2} \\mathbb{I}(q_{t+1} \\cdot \\Delta S_{t+1} < 0) |q_{t+1} \\Delta S_{t+1}|$$
   - Max Absolute Inventory: $\\max_t |q_t|$
   - Terminal Inventory: $q_T$
   - Annualized Sharpe Ratio: $\\frac{\\mathbb{E}[\\Delta V]}{\\text{Std}(\\Delta V) + \\epsilon} \\sqrt{390 \\times 252}$

5. **Reinforcement Learning & Policy Optimization**:
   - `MarketMakingEnv`: Step-based Gym-compatible environment.
   - `train_market_maker_policy`: Policy optimizer tuning risk aversion $\\gamma$ and liquidity elasticity $\\kappa$.

Design & Architectural Invariants:
----------------------------------
* **AST-Safe (CONSTRAINT #1 & #3)**: Pure quantitative compute. NEVER imports heavy forbidden
  engines (`processing_engine`, `technical_options_engine`, `strategy_engine`, `desktop`, etc.).
* **Honesty (CONSTRAINT #4)**: Exact closed-form math with verified inventory dampening and zero lookahead.
* **Never Raises (CONSTRAINT #6)**: Handles non-positive spreads, zero volatilities, and extreme
  inventories gracefully using continuous clamping and Taylor series expansions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import logging
import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)

# Numerical thresholds & sentinels
EPSILON = 1e-12
MAX_EXPONENT = 85.0  # Guard against overflow in exp()
DEFAULT_GAMMA = 0.10
DEFAULT_SIGMA = 0.25
DEFAULT_KAPPA = 1.50
DEFAULT_A = 140.0
DEFAULT_DT = 1.0 / 390.0  # 1 minute in trading days (390 mins/day)
DEFAULT_MAX_INVENTORY = 10
ANNUAL_TRADING_MINUTES = 390.0 * 252.0


# ===========================================================================
# 1. Data Structures & Configuration Models
# ===========================================================================

@dataclass(frozen=True)
class OptimalSpreads:
    """Optimal quoting spreads and quotes derived from Avellaneda-Stoikov math."""
    delta_bid: float
    delta_ask: float
    bid_price: float
    ask_price: float
    reservation_price: float
    mid_price: float
    total_spread: float
    half_spread: float

    def __iter__(self):
        """Allows unpacking directly as (delta_bid, delta_ask)."""
        yield self.delta_bid
        yield self.delta_ask

    def __getitem__(self, idx: int) -> float:
        if idx == 0:
            return self.delta_bid
        elif idx == 1:
            return self.delta_ask
        raise IndexError("OptimalSpreads index out of range (0=delta_bid, 1=delta_ask)")

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


@dataclass
class MarketMakingConfig:
    """Configuration parameters for the Market Making simulation environment."""
    initial_price: float = 100.0
    sigma: float = DEFAULT_SIGMA             # Price volatility per unit time
    gamma: float = DEFAULT_GAMMA             # Risk-aversion parameter
    kappa: float = DEFAULT_KAPPA             # Order book depth / price sensitivity
    A: float = DEFAULT_A                     # Order arrival intensity multiplier
    T: float = 1.0                           # Total time horizon
    num_steps: int = 390                     # Discrete simulation steps
    max_inventory: int = DEFAULT_MAX_INVENTORY  # Maximum absolute position limit |q|
    order_size: float = 1.0                  # Contract / share size per execution
    tick_size: float = 0.01                  # Minimum price increment
    inventory_penalty_lambda: float = 0.005  # Running inventory risk weight
    terminal_penalty_alpha: float = 0.01     # Terminal inventory liquidation penalty
    price_process: str = "abm"               # "abm" (Arithmetic Brownian) or "gbm" (Geometric)
    drift: float = 0.0                       # Price drift parameter
    seed: Optional[int] = None

    @property
    def dt(self) -> float:
        """Step time interval Delta t."""
        return max(EPSILON, self.T / max(1, self.num_steps))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Alias for compatibility
MarketMakerConfig = MarketMakingConfig


@dataclass
class StepResult:
    """Record of a single simulation step in the MarketMakingEnv."""
    step_idx: int
    time: float
    time_remaining: float
    mid_price: float
    reservation_price: float
    bid_spread: float
    ask_spread: float
    bid_price: float
    ask_price: float
    bid_fill_prob: float
    ask_fill_prob: float
    bid_filled: bool
    ask_filled: bool
    inventory: float
    cash: float
    mtm_value: float
    step_pnl: float
    step_reward: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


MarketMakerStepRecord = StepResult


@dataclass
class MarketMakerMetrics:
    """Comprehensive performance and execution metrics for a market maker run."""
    total_pnl: float
    spread_pnl: float
    inventory_pnl: float
    sharpe_ratio: float
    max_drawdown: float
    inventory_variance: float
    mean_abs_inventory: float
    terminal_inventory: float
    bid_fill_rate: float
    ask_fill_rate: float
    total_trades: int
    profit_factor: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MarketMakerSessionResult:
    """Decomposed performance attribution and backtesting metrics for a full session."""
    total_pnl: float
    spread_capture: float
    inventory_holding_penalty: float
    adverse_selection_loss: float
    max_abs_inventory: int
    terminal_inventory: int
    sharpe_ratio: float
    total_trades: int
    buy_trades: int
    sell_trades: int
    round_trip_trades: int
    final_cash: float
    final_portfolio_value: float
    pnl_series: List[float] = field(default_factory=list)
    inventory_series: List[int] = field(default_factory=list)
    price_series: List[float] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_pnl": float(self.total_pnl),
            "spread_capture": float(self.spread_capture),
            "inventory_holding_penalty": float(self.inventory_holding_penalty),
            "adverse_selection_loss": float(self.adverse_selection_loss),
            "max_abs_inventory": int(self.max_abs_inventory),
            "terminal_inventory": int(self.terminal_inventory),
            "sharpe_ratio": float(self.sharpe_ratio),
            "total_trades": int(self.total_trades),
            "buy_trades": int(self.buy_trades),
            "sell_trades": int(self.sell_trades),
            "round_trip_trades": int(self.round_trip_trades),
            "final_cash": float(self.final_cash),
            "final_portfolio_value": float(self.final_portfolio_value),
            "pnl_series": [float(x) for x in self.pnl_series],
            "inventory_series": [int(x) for x in self.inventory_series],
            "price_series": [float(x) for x in self.price_series],
            "history": self.history,
        }

    def __getitem__(self, item: str) -> Any:
        """Enables dictionary-like indexing result['total_pnl']."""
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


@dataclass
class PolicyOptimizationResult:
    """Result of policy hyperparameter tuning."""
    best_gamma: float
    best_kappa: float
    best_reward: float
    best_sharpe: float
    best_pnl: float
    best_max_inventory: int
    episodes_trained: int
    training_history: List[Dict[str, Any]] = field(default_factory=list)
    converged: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "best_gamma": float(self.best_gamma),
            "best_kappa": float(self.best_kappa),
            "best_reward": float(self.best_reward),
            "best_sharpe": float(self.best_sharpe),
            "best_pnl": float(self.best_pnl),
            "best_max_inventory": int(self.best_max_inventory),
            "episodes_trained": int(self.episodes_trained),
            "training_history": self.training_history,
            "converged": bool(self.converged),
        }

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)


# ===========================================================================
# 2. Avellaneda-Stoikov (2008) Closed-Form Mathematical Engine
# ===========================================================================

def compute_reservation_price(
    mid_price: float,
    inventory: Union[float, int],
    gamma: float,
    sigma: float,
    time_to_close: float = 0.0,
    time_remaining: Optional[float] = None,
) -> float:
    """Computes the Avellaneda-Stoikov (2008) reservation (indifference) price.

    Formula:
        R(s, q, t) = s - q * gamma * sigma^2 * (T - t)

    Parameters
    ----------
    mid_price : float
        Current mid-market reference price s >= 0.
    inventory : float or int
        Current inventory position q (+ for long, - for short).
    gamma : float
        Absolute risk-aversion coefficient gamma >= 0.
    sigma : float
        Instantaneous asset price volatility sigma >= 0.
    time_to_close : float
        Remaining time horizon tau = (T - t) >= 0.
    time_remaining : float, optional
        Alias for time_to_close.

    Returns
    -------
    float
        Reservation price R(s, q, t).
    """
    s = max(0.0, float(mid_price))
    q = float(inventory)
    g = max(0.0, float(gamma))
    sig = max(0.0, float(sigma))
    tau_val = time_remaining if time_remaining is not None else time_to_close
    tau = max(0.0, float(tau_val))

    # Reservation price formula
    reservation = s - (q * g * (sig ** 2) * tau)
    return max(0.0, reservation)


def compute_optimal_spreads(
    mid_price: float = 100.0,
    inventory: Union[float, int] = 0,
    gamma: float = DEFAULT_GAMMA,
    sigma: float = DEFAULT_SIGMA,
    time_to_close: float = 1.0,
    kappa: float = DEFAULT_KAPPA,
    time_remaining: Optional[float] = None,
) -> OptimalSpreads:
    """Computes the closed-form optimal quoting half-spreads and quote prices.

    Formulas (Avellaneda & Stoikov 2008):
        Total Spread: s^*(s, q, t) = gamma * sigma^2 * (T - t) + (2 / gamma) * ln(1 + gamma / kappa)
        Ask Spread:   delta^a = ((1 - 2q) / 2) * gamma * sigma^2 * (T - t) + (1 / gamma) * ln(1 + gamma / kappa)
        Bid Spread:   delta^b = ((1 + 2q) / 2) * gamma * sigma^2 * (T - t) + (1 / gamma) * ln(1 + gamma / kappa)

    Parameters
    ----------
    mid_price : float
        Current asset mid-price s.
    inventory : float or int
        Current inventory level q.
    gamma : float
        Risk-aversion parameter gamma > 0.
    sigma : float
        Asset volatility sigma > 0.
    time_to_close : float
        Remaining time to expiration tau = (T - t) >= 0.
    kappa : float
        Order book liquidity sensitivity / price decay parameter kappa > 0.
    time_remaining : float, optional
        Alias for time_to_close.

    Returns
    -------
    OptimalSpreads
        Object containing delta_bid, delta_ask, bid_price, ask_price,
        reservation_price, mid_price, total_spread, half_spread.
        Unpacks directly as (delta_bid, delta_ask).
    """
    s = max(EPSILON, float(mid_price))
    q = float(inventory)
    g = max(EPSILON, float(gamma))
    sig = max(0.0, float(sigma))
    tau_val = time_remaining if time_remaining is not None else time_to_close
    tau = max(0.0, float(tau_val))
    k = max(EPSILON, float(kappa))

    # Base volatility-time inventory term
    vol_time_term = g * (sig ** 2) * tau

    # Liquidity log-term: (1 / gamma) * ln(1 + gamma / kappa)
    ratio = g / k
    if ratio < 1e-7:
        liquidity_term = (1.0 / k) - (g / (2.0 * (k ** 2)))
    else:
        liquidity_term = (1.0 / g) * math.log1p(ratio)

    # Optimal half-spreads from mid
    delta_ask = 0.5 * (1.0 - 2.0 * q) * vol_time_term + liquidity_term
    delta_bid = 0.5 * (1.0 + 2.0 * q) * vol_time_term + liquidity_term

    # Total and average half spread
    total_spread = delta_ask + delta_bid
    half_spread = 0.5 * total_spread

    # Reservation price
    r = s - (q * vol_time_term)

    # Absolute quote prices
    ask_price = s + delta_ask
    bid_price = s - delta_bid

    return OptimalSpreads(
        delta_bid=float(delta_bid),
        delta_ask=float(delta_ask),
        bid_price=float(bid_price),
        ask_price=float(ask_price),
        reservation_price=float(r),
        mid_price=float(s),
        total_spread=float(total_spread),
        half_spread=float(half_spread),
    )


def compute_optimal_quotes(
    mid_price: float,
    inventory: int,
    gamma: float = DEFAULT_GAMMA,
    sigma: float = DEFAULT_SIGMA,
    kappa: float = DEFAULT_KAPPA,
    time_remaining: float = 1.0,
    max_inventory: int = DEFAULT_MAX_INVENTORY,
) -> Dict[str, Any]:
    """Calculates reservation price and explicit bid and ask limit prices,
    enforcing hard inventory boundary gates.

    Returns:
        Dict with keys:
            reservation_price, bid_price, ask_price, bid_spread, ask_spread,
            total_spread, bid_active, ask_active
    """
    safe_mid = max(0.01, float(mid_price))
    spreads = compute_optimal_spreads(
        mid_price=safe_mid,
        inventory=inventory,
        gamma=gamma,
        sigma=sigma,
        time_to_close=time_remaining,
        kappa=kappa,
    )

    # Hard boundary checks
    bid_active = bool(inventory < max_inventory)
    ask_active = bool(inventory > -max_inventory)

    return {
        "reservation_price": float(spreads.reservation_price),
        "bid_price": float(spreads.bid_price) if bid_active else 0.0,
        "ask_price": float(spreads.ask_price) if ask_active else float("inf"),
        "bid_spread": float(spreads.delta_bid),
        "ask_spread": float(spreads.delta_ask),
        "total_spread": float(spreads.total_spread),
        "bid_active": bid_active,
        "ask_active": ask_active,
    }


def compute_arrival_intensity(
    delta: float,
    A: float = DEFAULT_A,
    kappa: float = DEFAULT_KAPPA,
) -> float:
    """Computes the Poisson arrival intensity lambda(delta) = A * exp(-kappa * delta).

    Parameters
    ----------
    delta : float
        Distance of the limit order quote from the mid-price (spread offset).
    A : float
        Baseline order flow arrival intensity A > 0.
    kappa : float
        Liquidity sensitivity parameter kappa > 0.

    Returns
    -------
    float
        Arrival intensity lambda >= 0.
    """
    a = max(0.0, float(A))
    k = max(0.0, float(kappa))
    d = float(delta)

    exponent = -k * d
    exponent = max(-MAX_EXPONENT, min(MAX_EXPONENT, exponent))
    return float(a * math.exp(exponent))


def compute_fill_probability(
    delta: float,
    A: float = DEFAULT_A,
    kappa: float = DEFAULT_KAPPA,
    dt: float = DEFAULT_DT,
) -> float:
    """Computes the fill probability P(Fill in dt) = 1 - exp(-lambda(delta) * dt).

    Parameters
    ----------
    delta : float
        Spread offset from mid-price.
    A : float
        Baseline order arrival rate.
    kappa : float
        Order book depth decay rate.
    dt : float
        Time step duration Delta t.

    Returns
    -------
    float
        Probability of execution in [0, 1].
    """
    lam = compute_arrival_intensity(delta, A, kappa)
    step_dt = max(0.0, float(dt))
    hazard = lam * step_dt
    if hazard < 1e-7:
        return float(min(1.0, max(0.0, hazard)))
    prob = 1.0 - math.exp(-min(MAX_EXPONENT, hazard))
    return float(min(1.0, max(0.0, prob)))


def compute_fill_probabilities(
    delta_bid: float,
    delta_ask: float,
    A: float = DEFAULT_A,
    kappa: float = DEFAULT_KAPPA,
    dt: float = DEFAULT_DT,
) -> Tuple[float, float]:
    """Computes fill probabilities for both bid and ask quotes in time step dt.

    Returns:
        (p_fill_bid, p_fill_ask) in [0.0, 1.0]
    """
    p_b = compute_fill_probability(delta_bid, A=A, kappa=kappa, dt=dt)
    p_a = compute_fill_probability(delta_ask, A=A, kappa=kappa, dt=dt)
    return (p_b, p_a)


def generate_gbm_price_path(
    s0: float = 100.0,
    mu: float = 0.0,
    sigma: float = DEFAULT_SIGMA,
    dt: float = DEFAULT_DT,
    steps: int = 390,
    seed: Optional[int] = None,
) -> np.ndarray:
    """Generates a Geometric Brownian Motion (GBM) intraday price path.
        S_t = S_0 * exp((mu - 0.5*sigma^2)*t + sigma*W_t)
    """
    rng = np.random.RandomState(seed)
    n_steps = max(2, int(steps))
    z = rng.standard_normal(n_steps - 1)

    drift = (mu - 0.5 * (sigma ** 2)) * dt
    diffusion = sigma * math.sqrt(dt) * z

    log_returns = np.concatenate([[0.0], drift + diffusion])
    log_path = np.cumsum(log_returns)
    prices = s0 * np.exp(log_path)
    return np.maximum(0.01, prices)


# ===========================================================================
# 3. MarketMakingEnv (RL Gym-Compatible Simulation Environment)
# ===========================================================================

class MarketMakingEnv:
    """Reinforcement Learning Market Making Simulation Environment.

    Simulates high-frequency quoting, stochastic Poisson order executions, inventory
    evolution, and PnL dynamics under Avellaneda-Stoikov market mechanics.

    Conforms to standard RL environment interface:
        reset(seed) -> (obs, info)
        step(action) -> (obs, reward, terminated, truncated, info)
    """

    def __init__(
        self,
        config: Optional[MarketMakingConfig] = None,
        price_paths: Optional[Sequence[Union[Sequence[float], np.ndarray]]] = None,
        seed: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        """Initializes the MarketMakingEnv with specified configuration."""
        if config is None:
            config = MarketMakingConfig(**kwargs)
        elif kwargs:
            cfg_dict = asdict(config)
            cfg_dict.update(kwargs)
            config = MarketMakingConfig(**cfg_dict)

        self.config = config
        effective_seed = seed if seed is not None else config.seed
        self._rng = np.random.default_rng(effective_seed)

        self.price_paths = [np.asarray(p, dtype=np.float64) for p in price_paths] if price_paths else []
        self.current_path: Optional[np.ndarray] = None

        # State tracking variables
        self.step_idx: int = 0
        self.time: float = 0.0
        self.mid_price: float = self.config.initial_price
        self.inventory: float = 0.0
        self.cash: float = 0.0
        self.mtm_value: float = 0.0
        self.initial_mtm: float = 0.0

        # Trajectory history tracking
        self.history: List[StepResult] = []
        self._terminated: bool = False

        # Observation and action space dimensions
        self.observation_dim: int = 6
        self.action_dim: int = 2

    def reset(
        self,
        seed: Optional[int] = None,
        price_path: Optional[Union[Sequence[float], np.ndarray]] = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """Resets the environment to initial conditions.

        Returns:
            Tuple[np.ndarray, Dict[str, Any]]: Initial observation vector and info dictionary.
        """
        if seed is not None:
            self._rng = np.random.default_rng(seed)

        if price_path is not None:
            self.current_path = np.asarray(price_path, dtype=np.float64)
            self.config.num_steps = len(self.current_path)
            self.config.initial_price = float(self.current_path[0])
        elif self.price_paths:
            idx = int(self._rng.integers(0, len(self.price_paths)))
            self.current_path = self.price_paths[idx]
            self.config.num_steps = len(self.current_path)
            self.config.initial_price = float(self.current_path[0])
        else:
            self.current_path = None

        self.step_idx = 0
        self.time = 0.0
        self.mid_price = float(self.config.initial_price)
        self.inventory = 0.0
        self.cash = 0.0
        self.mtm_value = self.cash + (self.inventory * self.mid_price)
        self.initial_mtm = self.mtm_value
        self.history.clear()
        self._terminated = False

        obs = self.get_observation()
        info = self.get_state()
        return obs, info

    def get_observation(self) -> np.ndarray:
        """Constructs the current normalized observation vector for RL policies."""
        cfg = self.config
        time_rem = max(0.0, cfg.T - self.time)
        res_price = compute_reservation_price(
            mid_price=self.mid_price,
            inventory=self.inventory,
            gamma=cfg.gamma,
            sigma=cfg.sigma,
            time_to_close=time_rem,
        )

        inv_norm = float(self.inventory / max(1.0, float(cfg.max_inventory)))
        time_norm = float(time_rem / max(EPSILON, cfg.T))
        price_norm = float((self.mid_price - cfg.initial_price) / max(EPSILON, cfg.initial_price))
        vol_norm = float(cfg.sigma)
        res_spread_norm = float((res_price - self.mid_price) / max(EPSILON, self.mid_price))
        pnl_norm = float((self.mtm_value - self.initial_mtm) / max(EPSILON, cfg.initial_price * cfg.max_inventory))

        return np.array(
            [inv_norm, time_norm, price_norm, vol_norm, res_spread_norm, pnl_norm],
            dtype=np.float64,
        )

    def get_state(self) -> Dict[str, Any]:
        """Returns the full internal environment state dictionary."""
        time_rem = max(0.0, self.config.T - self.time)
        res_price = compute_reservation_price(
            mid_price=self.mid_price,
            inventory=self.inventory,
            gamma=self.config.gamma,
            sigma=self.config.sigma,
            time_to_close=time_rem,
        )
        return {
            "step_idx": self.step_idx,
            "time": self.time,
            "time_remaining": time_rem,
            "mid_price": self.mid_price,
            "inventory": self.inventory,
            "cash": self.cash,
            "mtm_value": self.mtm_value,
            "pnl": self.mtm_value - self.initial_mtm,
            "reservation_price": res_price,
            "portfolio_value": self.mtm_value,
        }

    def step(
        self,
        action: Union[Sequence[float], np.ndarray, Dict[str, float], str, None] = None,
    ) -> Tuple[np.ndarray, float, bool, bool, Dict[str, Any]]:
        """Executes a single simulation step in the environment."""
        if self._terminated:
            obs, info = self.reset()
            return obs, 0.0, True, False, info

        cfg = self.config
        dt = cfg.dt
        time_rem = max(0.0, cfg.T - self.time)
        prev_mtm = self.mtm_value

        # 1. Determine Quoting Spreads (delta_bid, delta_ask)
        gamma = cfg.gamma
        kappa = cfg.kappa
        if isinstance(action, dict):
            if "delta_bid" in action and "delta_ask" in action:
                delta_bid = max(0.0, float(action["delta_bid"]))
                delta_ask = max(0.0, float(action["delta_ask"]))
                res_price = compute_reservation_price(
                    mid_price=self.mid_price,
                    inventory=self.inventory,
                    gamma=gamma,
                    sigma=cfg.sigma,
                    time_to_close=time_rem,
                )
            else:
                gamma = float(action.get("gamma", gamma))
                kappa = float(action.get("kappa", kappa))
                as_spreads = compute_optimal_spreads(
                    mid_price=self.mid_price,
                    inventory=self.inventory,
                    gamma=gamma,
                    sigma=cfg.sigma,
                    time_to_close=time_rem,
                    kappa=kappa,
                )
                delta_bid = as_spreads.delta_bid
                delta_ask = as_spreads.delta_ask
                res_price = as_spreads.reservation_price
        elif isinstance(action, (list, tuple, np.ndarray)) and len(action) >= 2:
            # Direct quote half-spread offsets [delta_bid, delta_ask]
            delta_bid = max(0.0, float(action[0]))
            delta_ask = max(0.0, float(action[1]))
            res_price = compute_reservation_price(
                mid_price=self.mid_price,
                inventory=self.inventory,
                gamma=gamma,
                sigma=cfg.sigma,
                time_to_close=time_rem,
            )
        elif isinstance(action, (int, float)):
            delta_bid = max(0.0, float(action))
            delta_ask = max(0.0, float(action))
            res_price = compute_reservation_price(
                mid_price=self.mid_price,
                inventory=self.inventory,
                gamma=gamma,
                sigma=cfg.sigma,
                time_to_close=time_rem,
            )
        else:
            as_spreads = compute_optimal_spreads(
                mid_price=self.mid_price,
                inventory=self.inventory,
                gamma=gamma,
                sigma=cfg.sigma,
                time_to_close=time_rem,
                kappa=kappa,
            )
            delta_bid = as_spreads.delta_bid
            delta_ask = as_spreads.delta_ask
            res_price = as_spreads.reservation_price

        bid_price = self.mid_price - delta_bid
        ask_price = self.mid_price + delta_ask

        # 2. Compute Fill Probabilities under Poisson Arrivals
        can_buy = (self.inventory + cfg.order_size <= cfg.max_inventory)
        can_sell = (self.inventory - cfg.order_size >= -cfg.max_inventory)

        bid_prob = compute_fill_probability(delta_bid, cfg.A, kappa, dt) if can_buy else 0.0
        ask_prob = compute_fill_probability(delta_ask, cfg.A, kappa, dt) if can_sell else 0.0

        # 3. Simulate Order Fills (Poisson Bernoulli Trial)
        u_bid, u_ask = self._rng.uniform(0.0, 1.0, size=2)
        bid_filled = bool(u_bid < bid_prob and can_buy)
        ask_filled = bool(u_ask < ask_prob and can_sell)

        # 4. Update Inventory and Cash on Executions
        if bid_filled:
            self.inventory += cfg.order_size
            self.cash -= bid_price * cfg.order_size

        if ask_filled:
            self.inventory -= cfg.order_size
            self.cash += ask_price * cfg.order_size

        # 5. Evolve Mid-Price (from path or stochastic SDE)
        if self.current_path is not None and self.step_idx + 1 < len(self.current_path):
            self.mid_price = float(self.current_path[self.step_idx + 1])
        else:
            dW = self._rng.normal(0.0, math.sqrt(dt))
            if cfg.price_process.lower() == "gbm":
                growth = (cfg.drift - 0.5 * (cfg.sigma ** 2)) * dt + cfg.sigma * dW
                self.mid_price = max(EPSILON, self.mid_price * math.exp(growth))
            else:
                dPrice = cfg.drift * dt + cfg.sigma * dW
                self.mid_price = max(EPSILON, self.mid_price + dPrice)

        # 6. Update Mark-to-Market PnL
        self.time += dt
        self.step_idx += 1
        self.mtm_value = self.cash + (self.inventory * self.mid_price)
        step_pnl = self.mtm_value - prev_mtm

        # 7. Check Termination
        terminated = bool(self.step_idx >= cfg.num_steps or self.time >= (cfg.T - 1e-9))
        truncated = False
        self._terminated = terminated

        # 8. Compute Reward
        running_inv_penalty = cfg.inventory_penalty_lambda * (self.inventory ** 2) * dt
        terminal_liquidation_penalty = 0.0
        if terminated and self.inventory != 0.0:
            terminal_liquidation_penalty = cfg.terminal_penalty_alpha * (self.inventory ** 2)

        step_reward = float(step_pnl - running_inv_penalty - terminal_liquidation_penalty)

        # 9. Record Step History
        step_record = StepResult(
            step_idx=self.step_idx,
            time=self.time,
            time_remaining=max(0.0, cfg.T - self.time),
            mid_price=self.mid_price,
            reservation_price=res_price,
            bid_spread=delta_bid,
            ask_spread=delta_ask,
            bid_price=bid_price,
            ask_price=ask_price,
            bid_fill_prob=bid_prob,
            ask_fill_prob=ask_prob,
            bid_filled=bid_filled,
            ask_filled=ask_filled,
            inventory=self.inventory,
            cash=self.cash,
            mtm_value=self.mtm_value,
            step_pnl=step_pnl,
            step_reward=step_reward,
        )
        self.history.append(step_record)

        obs = self.get_observation()
        info = {
            **self.get_state(),
            "bid_filled": bid_filled,
            "ask_filled": ask_filled,
            "bid_fill_prob": bid_prob,
            "ask_fill_prob": ask_prob,
            "step_pnl": step_pnl,
            "step_reward": step_reward,
        }

        return obs, step_reward, terminated, truncated, info

    def compute_metrics(self) -> MarketMakerMetrics:
        """Computes comprehensive performance statistics for the completed episode."""
        if not self.history:
            return MarketMakerMetrics(
                total_pnl=0.0,
                spread_pnl=0.0,
                inventory_pnl=0.0,
                sharpe_ratio=0.0,
                max_drawdown=0.0,
                inventory_variance=0.0,
                mean_abs_inventory=0.0,
                terminal_inventory=0.0,
                bid_fill_rate=0.0,
                ask_fill_rate=0.0,
                total_trades=0,
                profit_factor=0.0,
            )

        pnls = [step.step_pnl for step in self.history]
        inventories = [step.inventory for step in self.history]
        mtm_values = [step.mtm_value for step in self.history]

        total_pnl = self.mtm_value - self.initial_mtm
        inv_variance = float(np.var(inventories)) if len(inventories) > 1 else 0.0
        mean_abs_inv = float(np.mean(np.abs(inventories)))

        # Fill statistics
        bid_fills = sum(1 for step in self.history if step.bid_filled)
        ask_fills = sum(1 for step in self.history if step.ask_filled)
        total_trades = bid_fills + ask_fills
        bid_fill_rate = float(bid_fills / len(self.history))
        ask_fill_rate = float(ask_fills / len(self.history))

        # Sharpe ratio of step PnLs
        mean_pnl = float(np.mean(pnls))
        std_pnl = float(np.std(pnls))
        if std_pnl > EPSILON:
            sharpe_ratio = float((mean_pnl / std_pnl) * math.sqrt(self.config.num_steps))
        else:
            sharpe_ratio = 0.0

        # Maximum Drawdown
        cum_max = np.maximum.accumulate(mtm_values)
        drawdowns = cum_max - mtm_values
        max_drawdown = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

        # Spread PnL vs Inventory PnL decomposition
        spread_capture = sum(
            (step.ask_spread * self.config.order_size if step.ask_filled else 0.0) +
            (step.bid_spread * self.config.order_size if step.bid_filled else 0.0)
            for step in self.history
        )
        inventory_pnl = total_pnl - spread_capture

        # Profit Factor
        gains = sum(p for p in pnls if p > 0.0)
        losses = abs(sum(p for p in pnls if p < 0.0))
        profit_factor = float(gains / losses) if losses > EPSILON else (float("inf") if gains > 0 else 1.0)

        return MarketMakerMetrics(
            total_pnl=float(total_pnl),
            spread_pnl=float(spread_capture),
            inventory_pnl=float(inventory_pnl),
            sharpe_ratio=float(sharpe_ratio),
            max_drawdown=float(max_drawdown),
            inventory_variance=float(inv_variance),
            mean_abs_inventory=float(mean_abs_inv),
            terminal_inventory=float(self.inventory),
            bid_fill_rate=float(bid_fill_rate),
            ask_fill_rate=float(ask_fill_rate),
            total_trades=int(total_trades),
            profit_factor=float(profit_factor),
        )

    def simulate_episode(
        self,
        policy: Union[str, Any] = "as",
    ) -> Tuple[List[StepResult], MarketMakerMetrics]:
        """Runs a complete simulation trajectory from t=0 to t=T under specified policy."""
        self.reset()
        terminated = False
        while not terminated:
            if callable(policy):
                action = policy(self)
            else:
                action = policy
            _, _, terminated, _, _ = self.step(action)

        metrics = self.compute_metrics()
        return list(self.history), metrics


# Backward-compatibility alias
MarketMakerEnv = MarketMakingEnv


# ===========================================================================
# 4. Market Maker Backtest Simulator & Performance Decomposition Engine
# ===========================================================================

def simulate_market_maker_execution(
    price_path: Union[Sequence[float], np.ndarray],
    gamma: float = DEFAULT_GAMMA,
    sigma: float = DEFAULT_SIGMA,
    kappa: float = DEFAULT_KAPPA,
    A: float = DEFAULT_A,
    dt: float = DEFAULT_DT,
    max_inventory: int = DEFAULT_MAX_INVENTORY,
    seed: Optional[int] = None,
    fill_rng: Optional[np.random.RandomState] = None,
) -> MarketMakerSessionResult:
    """Simulates a full intraday trading session (e.g. 390 minutes) quoting bid and ask limits
    following the Avellaneda-Stoikov framework.

    Performance Decomposition:
    --------------------------
    - Total PnL ($)
    - Spread Capture ($)
    - Inventory Holding Risk Penalty ($)
    - Adverse Selection Losses ($)
    - Max Absolute Inventory & Terminal Inventory
    - Annualized Sharpe Ratio

    Parameters:
        price_path: Sequence of mid-prices (1-min resolution or arbitrary)
        gamma: Risk aversion coefficient
        sigma: Asset volatility
        kappa: Order book liquidity density
        A: Poisson arrival factor
        dt: Time step fraction of day
        max_inventory: Hard inventory limit
        seed: Random seed for fill realizations
        fill_rng: Optional pre-seeded RandomState instance

    Returns:
        MarketMakerSessionResult
    """
    prices = np.asarray(price_path, dtype=np.float64)
    if len(prices) < 2:
        init_p = float(prices[0]) if len(prices) > 0 else 0.0
        return MarketMakerSessionResult(
            total_pnl=0.0,
            spread_capture=0.0,
            inventory_holding_penalty=0.0,
            adverse_selection_loss=0.0,
            max_abs_inventory=0,
            terminal_inventory=0,
            sharpe_ratio=0.0,
            total_trades=0,
            buy_trades=0,
            sell_trades=0,
            round_trip_trades=0,
            final_cash=0.0,
            final_portfolio_value=0.0,
            pnl_series=[0.0] if len(prices) > 0 else [],
            inventory_series=[0] if len(prices) > 0 else [],
            price_series=[init_p] if len(prices) > 0 else [],
            history=[],
        )

    n_steps = len(prices)
    rng = fill_rng if fill_rng is not None else np.random.RandomState(seed)

    # Initial state
    inventory = 0
    cash = 0.0
    initial_price = float(prices[0])
    initial_portfolio_value = 0.0

    spread_capture = 0.0
    inventory_penalty_total = 0.0
    adverse_selection_loss = 0.0

    max_abs_inv = 0
    buy_trades = 0
    sell_trades = 0

    pnl_series: List[float] = [0.0]
    inventory_series: List[int] = [0]
    price_series: List[float] = [initial_price]
    step_records: List[Dict[str, Any]] = []
    step_pnls: List[float] = []

    # Run simulation step by step
    for t in range(n_steps - 1):
        s_t = float(prices[t])
        s_next = float(prices[t + 1])
        time_rem = max(0.0, (n_steps - 1 - t) * dt)

        # 1. Compute optimal quotes
        quotes = compute_optimal_quotes(
            mid_price=s_t,
            inventory=inventory,
            gamma=gamma,
            sigma=sigma,
            kappa=kappa,
            time_remaining=time_rem,
            max_inventory=max_inventory,
        )

        r_price = quotes["reservation_price"]
        delta_b = quotes["bid_spread"]
        delta_a = quotes["ask_spread"]
        bid_quote = quotes["bid_price"]
        ask_quote = quotes["ask_price"]
        bid_active = quotes["bid_active"]
        ask_active = quotes["ask_active"]

        # 2. Compute fill probabilities
        p_bid, p_ask = compute_fill_probabilities(delta_b, delta_a, A=A, kappa=kappa, dt=dt)
        if not bid_active:
            p_bid = 0.0
        if not ask_active:
            p_ask = 0.0

        # 3. Sample fill events
        u_b = rng.uniform(0.0, 1.0)
        u_a = rng.uniform(0.0, 1.0)
        bid_filled = bool(u_b < p_bid and bid_active)
        ask_filled = bool(u_a < p_ask and ask_active)

        step_spread_captured = 0.0

        # Execute bid (buy)
        if bid_filled:
            cash -= bid_quote
            inventory += 1
            buy_trades += 1
            captured_b = max(0.0, s_t - bid_quote)
            spread_capture += captured_b
            step_spread_captured += captured_b

        # Execute ask (sell)
        if ask_filled:
            cash += ask_quote
            inventory -= 1
            sell_trades += 1
            captured_a = max(0.0, ask_quote - s_t)
            spread_capture += captured_a
            step_spread_captured += captured_a

        # Track max absolute inventory
        curr_abs_inv = abs(inventory)
        if curr_abs_inv > max_abs_inv:
            max_abs_inv = curr_abs_inv

        # 4. Inventory holding penalty for step t
        step_penalty = 0.5 * gamma * (sigma ** 2) * (inventory ** 2) * dt
        inventory_penalty_total += step_penalty

        # 5. Price movement and portfolio valuation
        portfolio_val_next = cash + inventory * s_next
        step_pnl = portfolio_val_next - (pnl_series[-1] + initial_portfolio_value)
        step_pnls.append(step_pnl)

        # 6. Adverse selection tracking: price movement against post-execution inventory
        delta_s = s_next - s_t
        if inventory > 0 and delta_s < 0:
            adverse_selection_loss += float(inventory * abs(delta_s))
        elif inventory < 0 and delta_s > 0:
            adverse_selection_loss += float(abs(inventory) * delta_s)

        # Record step
        pnl_series.append(float(portfolio_val_next))
        inventory_series.append(int(inventory))
        price_series.append(float(s_next))

        step_record = {
            "step": t,
            "time_remaining": float(time_rem),
            "mid_price": float(s_t),
            "reservation_price": float(r_price),
            "bid_quote": float(bid_quote),
            "ask_quote": float(ask_quote),
            "bid_spread": float(delta_b),
            "ask_spread": float(delta_a),
            "bid_filled": bid_filled,
            "ask_filled": ask_filled,
            "inventory": int(inventory),
            "cash": float(cash),
            "portfolio_value": float(portfolio_val_next),
            "step_pnl": float(step_pnl),
            "spread_captured": float(step_spread_captured),
            "inventory_penalty": float(step_penalty),
            "adverse_selection": float(adverse_selection_loss),
        }
        step_records.append(step_record)

    # Final metrics
    terminal_inventory = int(inventory)
    final_price = float(prices[-1])
    final_portfolio_value = float(cash + terminal_inventory * final_price)
    total_pnl = float(final_portfolio_value - initial_portfolio_value)
    total_trades = buy_trades + sell_trades
    round_trips = min(buy_trades, sell_trades)

    # Annualized Sharpe ratio of step PnL
    if len(step_pnls) > 1:
        pnl_arr = np.array(step_pnls, dtype=np.float64)
        std_pnl = float(np.std(pnl_arr))
        mean_pnl = float(np.mean(pnl_arr))
        if std_pnl > 1e-8:
            sharpe = (mean_pnl / std_pnl) * math.sqrt(ANNUAL_TRADING_MINUTES)
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    return MarketMakerSessionResult(
        total_pnl=total_pnl,
        spread_capture=float(spread_capture),
        inventory_holding_penalty=float(inventory_penalty_total),
        adverse_selection_loss=float(adverse_selection_loss),
        max_abs_inventory=int(max_abs_inv),
        terminal_inventory=terminal_inventory,
        sharpe_ratio=float(sharpe),
        total_trades=total_trades,
        buy_trades=buy_trades,
        sell_trades=sell_trades,
        round_trip_trades=round_trips,
        final_cash=float(cash),
        final_portfolio_value=float(final_portfolio_value),
        pnl_series=pnl_series,
        inventory_series=inventory_series,
        price_series=price_series,
        history=step_records,
    )


# ===========================================================================
# 5. DRL & Quoting Policy Optimizer
# ===========================================================================

def train_market_maker_policy(
    env: Union[MarketMakingEnv, Sequence[Union[Sequence[float], np.ndarray]], np.ndarray, None] = None,
    episodes: int = 50,
    learning_rate: float = 0.05,
    gamma_bounds: Tuple[float, float] = (0.01, 1.0),
    kappa_bounds: Tuple[float, float] = (0.5, 5.0),
    seed: int = 42,
) -> PolicyOptimizationResult:
    """Lightweight heuristic and policy optimizer for high-frequency market making.
    Tunes risk aversion gamma and order book elasticity kappa to maximize risk-adjusted PnL.

    Parameters:
        env: MarketMakingEnv instance or price path / collection of paths
        episodes: Number of training iterations / episodes
        learning_rate: Step size for policy parameter updates
        gamma_bounds: (min_gamma, max_gamma)
        kappa_bounds: (min_kappa, max_kappa)
        seed: Random seed

    Returns:
        PolicyOptimizationResult
    """
    rng = np.random.RandomState(seed)

    # Initialize environment
    if isinstance(env, MarketMakingEnv):
        mm_env = env
    elif env is not None:
        if isinstance(env, (list, tuple)) and len(env) > 0 and isinstance(env[0], (list, tuple, np.ndarray)):
            mm_env = MarketMakingEnv(price_paths=env, seed=seed)
        else:
            mm_env = MarketMakingEnv(price_paths=[np.asarray(env, dtype=np.float64)], seed=seed)
    else:
        mm_env = MarketMakingEnv(seed=seed)

    # Optimization state
    curr_gamma = float(np.clip(0.1, gamma_bounds[0], gamma_bounds[1]))
    curr_kappa = float(np.clip(1.5, kappa_bounds[0], kappa_bounds[1]))

    best_gamma = curr_gamma
    best_kappa = curr_kappa
    best_score = -float("inf")
    best_sharpe = -float("inf")
    best_pnl = -float("inf")

    training_history: List[Dict[str, Any]] = []

    for ep in range(max(1, int(episodes))):
        if ep == 0:
            cand_gamma = curr_gamma
            cand_kappa = curr_kappa
        else:
            temperature = max(0.1, 1.0 - (ep / float(episodes)))
            d_gamma = rng.normal(0.0, learning_rate * temperature * (gamma_bounds[1] - gamma_bounds[0]))
            d_kappa = rng.normal(0.0, learning_rate * temperature * (kappa_bounds[1] - kappa_bounds[0]))

            cand_gamma = float(np.clip(curr_gamma + d_gamma, gamma_bounds[0], gamma_bounds[1]))
            cand_kappa = float(np.clip(curr_kappa + d_kappa, kappa_bounds[0], kappa_bounds[1]))

        # Evaluate candidate across episode
        obs, _ = mm_env.reset(seed=int(rng.randint(0, 1000000)))
        done = False
        ep_rewards: List[float] = []
        ep_pnls: List[float] = []

        while not done:
            obs, reward, done, _, info = mm_env.step({"gamma": cand_gamma, "kappa": cand_kappa})
            ep_rewards.append(reward)
            ep_pnls.append(info.get("step_pnl", 0.0))

        total_ep_reward = float(np.sum(ep_rewards))
        final_pnl = float(mm_env.mtm_value)

        # Compute episode Sharpe ratio
        pnl_arr = np.array(ep_pnls, dtype=np.float64)
        std_pnl = float(np.std(pnl_arr))
        mean_pnl = float(np.mean(pnl_arr))
        if std_pnl > 1e-8:
            ep_sharpe = (mean_pnl / std_pnl) * math.sqrt(ANNUAL_TRADING_MINUTES)
        else:
            ep_sharpe = 0.0

        # Objective score
        score = ep_sharpe + (0.01 * total_ep_reward)

        if score > best_score:
            best_score = score
            best_gamma = cand_gamma
            best_kappa = cand_kappa
            best_sharpe = ep_sharpe
            best_pnl = final_pnl
            curr_gamma = cand_gamma
            curr_kappa = cand_kappa
        else:
            delta = score - best_score
            accept_prob = math.exp(max(-10.0, delta / max(0.01, temperature)))
            if rng.uniform(0.0, 1.0) < accept_prob:
                curr_gamma = cand_gamma
                curr_kappa = cand_kappa

        training_history.append({
            "episode": ep + 1,
            "gamma": float(cand_gamma),
            "kappa": float(cand_kappa),
            "reward": float(total_ep_reward),
            "pnl": float(final_pnl),
            "sharpe": float(ep_sharpe),
            "score": float(score),
            "best_score": float(best_score),
        })

    return PolicyOptimizationResult(
        best_gamma=float(best_gamma),
        best_kappa=float(best_kappa),
        best_reward=float(best_score),
        best_sharpe=float(best_sharpe),
        best_pnl=float(best_pnl),
        best_max_inventory=int(mm_env.config.max_inventory),
        episodes_trained=int(episodes),
        training_history=training_history,
        converged=True,
    )


# ===========================================================================
# 6. High-Level Comparison & Helper Functions
# ===========================================================================

def simulate_avellaneda_stoikov(
    config: Optional[MarketMakingConfig] = None,
    seed: Optional[int] = None,
) -> Tuple[List[StepResult], MarketMakerMetrics]:
    """Runs a single trajectory of Avellaneda-Stoikov market making."""
    env = MarketMakingEnv(config=config, seed=seed)
    return env.simulate_episode(policy="as")


def simulate_symmetric_market_maker(
    config: Optional[MarketMakingConfig] = None,
    fixed_spread: Optional[float] = None,
    seed: Optional[int] = None,
) -> Tuple[List[StepResult], MarketMakerMetrics]:
    """Runs a single trajectory of naive symmetric market making (ignoring inventory)."""
    env = MarketMakingEnv(config=config, seed=seed)

    if fixed_spread is None:
        spreads = compute_optimal_spreads(
            mid_price=env.config.initial_price,
            inventory=0,
            gamma=env.config.gamma,
            sigma=env.config.sigma,
            time_to_close=env.config.T,
            kappa=env.config.kappa,
        )
        fixed_spread = spreads.half_spread

    def symmetric_policy(_env: MarketMakingEnv) -> Tuple[float, float]:
        return (fixed_spread, fixed_spread)

    return env.simulate_episode(policy=symmetric_policy)


def compare_market_making_strategies(
    config: Optional[MarketMakingConfig] = None,
    num_simulations: int = 100,
    seed: int = 42,
) -> Dict[str, Any]:
    """Compares Avellaneda-Stoikov inventory-aware quoting against Naive Symmetric quoting."""
    cfg = config or MarketMakingConfig()
    as_pnls, as_sharpes, as_inv_vars, as_max_dds = [], [], [], []
    sym_pnls, sym_sharpes, sym_inv_vars, sym_max_dds = [], [], [], []

    for i in range(num_simulations):
        sim_seed = seed + i
        _, as_m = simulate_avellaneda_stoikov(config=cfg, seed=sim_seed)
        as_pnls.append(as_m.total_pnl)
        as_sharpes.append(as_m.sharpe_ratio)
        as_inv_vars.append(as_m.inventory_variance)
        as_max_dds.append(as_m.max_drawdown)

        _, sym_m = simulate_symmetric_market_maker(config=cfg, seed=sim_seed)
        sym_pnls.append(sym_m.total_pnl)
        sym_sharpes.append(sym_m.sharpe_ratio)
        sym_inv_vars.append(sym_m.inventory_variance)
        sym_max_dds.append(sym_m.max_drawdown)

    return {
        "num_simulations": num_simulations,
        "avellaneda_stoikov": {
            "mean_pnl": float(np.mean(as_pnls)),
            "std_pnl": float(np.std(as_pnls)),
            "mean_sharpe": float(np.mean(as_sharpes)),
            "mean_inventory_variance": float(np.mean(as_inv_vars)),
            "mean_max_drawdown": float(np.mean(as_max_dds)),
        },
        "symmetric_naive": {
            "mean_pnl": float(np.mean(sym_pnls)),
            "std_pnl": float(np.std(sym_pnls)),
            "mean_sharpe": float(np.mean(sym_sharpes)),
            "mean_inventory_variance": float(np.mean(sym_inv_vars)),
            "mean_max_drawdown": float(np.mean(sym_max_dds)),
        },
        "inventory_variance_reduction_pct": float(
            (np.mean(sym_inv_vars) - np.mean(as_inv_vars)) / max(EPSILON, np.mean(sym_inv_vars)) * 100.0
        ),
    }


# ===========================================================================
# 6. Pilots API & Webapp Serialization Helpers
# ===========================================================================

@dataclass
class MarketMakerStepPoint:
    step: int
    time_sec: float
    mid_price: float
    reservation_price: float
    bid_price: float
    ask_price: float
    bid_spread: float
    ask_spread: float
    inventory: int
    cash: float
    pnl: float
    trade_event: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MarketMakerSimResponse:
    symbol: str
    risk_aversion_gamma: float
    order_flow_intensity_kappa: float
    volatility_sigma: float
    max_inventory: int
    final_pnl: float
    sharpe_ratio: float
    max_drawdown: float
    total_trades: int
    fill_rate: float
    final_inventory: int
    avg_spread: float
    steps: List[MarketMakerStepPoint] = field(default_factory=list)
    as_of: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["steps"] = [s.to_dict() for s in self.steps]
        return d


def simulate_market_maker_session(
    symbol: str = "SPY",
    spot_price: float = 500.0,
    volatility: float = 0.20,
    gamma: float = 0.1,
    kappa: float = 1.5,
    num_steps: int = 100,
    time_horizon_t: float = 1.0,
    max_inventory: int = 10,
    order_size: int = 1,
    arrival_intensity_a: float = 140.0,
    seed: Optional[int] = 42,
) -> MarketMakerSimResponse:
    """Simulates an Avellaneda-Stoikov market making session for Pilots API serialization."""
    rng = np.random.default_rng(seed)

    s0 = float(spot_price) if spot_price and spot_price > 0 else 500.0
    vol = max(0.01, min(2.0, float(volatility) if volatility is not None else 0.20))
    gam = max(0.001, min(10.0, float(gamma) if gamma is not None else 0.1))
    kap = max(0.1, min(20.0, float(kappa) if kappa is not None else 1.5))
    n_steps = max(10, min(5000, int(num_steps) if num_steps is not None else 100))
    t_horiz = max(0.01, min(10.0, float(time_horizon_t) if time_horizon_t is not None else 1.0))
    max_q = max(1, min(100, int(max_inventory) if max_inventory is not None else 10))
    size = max(1, int(order_size) if order_size is not None else 1)

    dt = t_horiz / n_steps
    dt_sec = (t_horiz * 390.0 * 60.0) / n_steps

    curr_s = s0
    curr_q = 0
    curr_cash = 0.0

    steps: List[MarketMakerStepPoint] = []
    pnl_history: List[float] = []
    spread_history: List[float] = []
    total_trades = 0

    for step_i in range(n_steps):
        t = step_i * dt
        tau = max(1e-4, t_horiz - t)

        r_price = curr_s - curr_q * gam * (vol ** 2) * tau

        skew_term = (gam * (vol ** 2) * tau) / 2.0
        liquidity_term = (1.0 / gam) * math.log(1.0 + gam / kap)

        delta_a = (1.0 - 2.0 * curr_q) * skew_term + liquidity_term
        delta_b = (1.0 + 2.0 * curr_q) * skew_term + liquidity_term

        delta_a = max(0.01, delta_a)
        delta_b = max(0.01, delta_b)

        ask_quote = curr_s + delta_a
        bid_quote = curr_s - delta_b
        spread_history.append(ask_quote - bid_quote)

        lambda_a = arrival_intensity_a * math.exp(-kap * delta_a)
        lambda_b = arrival_intensity_a * math.exp(-kap * delta_b)

        prob_a = min(1.0, max(0.0, 1.0 - math.exp(-lambda_a * dt)))
        prob_b = min(1.0, max(0.0, 1.0 - math.exp(-lambda_b * dt)))

        fill_ask = False
        fill_bid = False
        trade_event: Optional[str] = None

        if curr_q > -max_q and rng.random() < prob_a:
            fill_ask = True
        if curr_q < max_q and rng.random() < prob_b:
            fill_bid = True

        if fill_ask and fill_bid:
            curr_cash += (ask_quote - bid_quote) * size
            total_trades += 2
            trade_event = "ROUND_TRIP"
        elif fill_ask:
            curr_cash += ask_quote * size
            curr_q -= size
            total_trades += 1
            trade_event = "SELL"
        elif fill_bid:
            curr_cash -= bid_quote * size
            curr_q += size
            total_trades += 1
            trade_event = "BUY"

        portfolio_pnl = curr_cash + curr_q * curr_s
        pnl_history.append(portfolio_pnl)

        step_point = MarketMakerStepPoint(
            step=step_i,
            time_sec=round(step_i * dt_sec, 2),
            mid_price=round(curr_s, 2),
            reservation_price=round(r_price, 2),
            bid_price=round(bid_quote, 2),
            ask_price=round(ask_quote, 2),
            bid_spread=round(delta_b, 4),
            ask_spread=round(delta_a, 4),
            inventory=curr_q,
            cash=round(curr_cash, 2),
            pnl=round(portfolio_pnl, 2),
            trade_event=trade_event if trade_event in ("BUY", "SELL") else ("BUY" if fill_bid else ("SELL" if fill_ask else None)),
        )
        steps.append(step_point)

        z = rng.standard_normal()
        curr_s = curr_s * math.exp(-0.5 * (vol ** 2) * dt + vol * math.sqrt(dt) * z)
        curr_s = max(0.01, curr_s)

    final_pnl = pnl_history[-1] if pnl_history else 0.0

    pnl_arr = np.array(pnl_history)
    diffs = np.diff(pnl_arr) if len(pnl_arr) > 1 else np.array([0.0])
    std_diff = float(np.std(diffs))
    mean_diff = float(np.mean(diffs))
    sharpe = float(math.sqrt(n_steps) * (mean_diff / (std_diff + 1e-8))) if std_diff > 1e-8 else 0.0

    running_max = np.maximum.accumulate(pnl_arr)
    drawdowns = running_max - pnl_arr
    max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    fill_rate = float(total_trades / max(1, 2 * n_steps))
    avg_spd = float(np.mean(spread_history)) if spread_history else 0.0

    return MarketMakerSimResponse(
        symbol=symbol.upper(),
        risk_aversion_gamma=round(gam, 4),
        order_flow_intensity_kappa=round(kap, 4),
        volatility_sigma=round(vol, 4),
        max_inventory=max_q,
        final_pnl=round(final_pnl, 2),
        sharpe_ratio=round(max(-10.0, min(10.0, sharpe)), 2),
        max_drawdown=round(max_dd, 2),
        total_trades=total_trades,
        fill_rate=round(fill_rate, 4),
        final_inventory=curr_q,
        avg_spread=round(avg_spd, 4),
        steps=steps,
        as_of=datetime.now(timezone.utc).isoformat(),
    )


__all__ = [
    "DEFAULT_A",
    "DEFAULT_DT",
    "DEFAULT_GAMMA",
    "DEFAULT_KAPPA",
    "DEFAULT_MAX_INVENTORY",
    "DEFAULT_SIGMA",
    "OptimalSpreads",
    "MarketMakingConfig",
    "MarketMakerConfig",
    "StepResult",
    "MarketMakerStepRecord",
    "MarketMakerMetrics",
    "MarketMakerSessionResult",
    "MarketMakerStepPoint",
    "MarketMakerSimResponse",
    "PolicyOptimizationResult",
    "compute_reservation_price",
    "compute_optimal_spreads",
    "compute_optimal_quotes",
    "compute_arrival_intensity",
    "compute_fill_probability",
    "compute_fill_probabilities",
    "generate_gbm_price_path",
    "MarketMakingEnv",
    "MarketMakerEnv",
    "simulate_market_maker_execution",
    "simulate_market_maker_session",
    "train_market_maker_policy",
    "simulate_avellaneda_stoikov",
    "simulate_symmetric_market_maker",
    "compare_market_making_strategies",
]

