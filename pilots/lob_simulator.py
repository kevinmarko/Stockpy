"""
pilots/lob_simulator.py — Limit Order Book (LOB) Microstructure & Markovian Queue Simulator.
=============================================================================================

Quantitative market microstructure engine based on the continuous-time Markovian limit order book
framework of Cont, Stoikov, and Talreja (2010):

    Cont, Rama, Sasha Stoikov, and Rishi Talreja.
    "A Stochastic Model for Order Book Dynamics."
    Operations Research, Vol. 58, No. 3 (2010), pp. 549-563.

Microstructure Modeling Overview:
---------------------------------
1. **Continuous-Time Markov Chain LOB Dynamics**:
   - Limit orders arrive as independent Poisson processes with intensity $\\lambda(i)$ at price level $i$.
   - Order cancellations arrive at rate proportional to depth: $\\mu(i) \\cdot Q(t)$, where $\\mu(i)$ is the
     individual cancellation intensity per resting order.
   - Market orders arrive as independent Poisson processes with intensity $\\theta$ at the best quotes (touch),
     consuming resting depth under price-time (FIFO) priority.

2. **Markovian Queue Priority & Fill Simulation**:
   - For a limit order of size $S$ placed at price level $p$ behind a queue of size $Q_0$:
     - Queue ahead $Q(t)$ diminishes via:
       a) Market order arrivals (intensity $\\theta$) executing resting volume.
       b) Cancellations in queue ahead (intensity $\\mu \\cdot Q(t)$).
     - Once $Q(t) = 0$, subsequent market orders execute our order $S$.
     - Simultaneously, orders joining behind us (rate $\\lambda$) do not alter our FIFO priority.
     - Adverse price movement occurs if the opposite side queue depletes and the market moves away
       before our order completes execution.

3. **Key Analytical & Empirical Metrics**:
   - **Fill Probability**: $P(\\text{Fill} \\mid \\text{Horizon } T) = P(\\tau_{\\text{fill}} \\le T)$
   - **Expected Time to Fill**: $\\mathbb{E}[\\tau_{\\text{fill}} \\mid \\tau_{\\text{fill}} \\le T]$
   - **Probability of Adverse Move**: $P(\\tau_{\\text{adverse}} < \\tau_{\\text{fill}})$
   - **Queue Depletion Velocity**: $\\mathbb{E}[\\Delta Q / \\Delta t] = \\theta + \\mu \\cdot Q$

Design & Architecture Invariants:
---------------------------------
* **AST-Safe (CONSTRAINT #1 & #3)** — Pure quantitative module. NEVER imports heavy forbidden engines
  (`processing_engine`, `technical_options_engine`, `strategy_engine`, `forecasting_engine`, `macro_engine`,
   `main`, `desktop`, etc.). Imports only standard library, `numpy`, and `scipy`.
* **Honesty (CONSTRAINT #4)** — True Poisson arrival rates derived from input order flow records; zero fabricated
  fills. Degenerate books return clean sentinels without fake data.
* **Never Raises (CONSTRAINT #6)** — Gracefully handles empty trade streams, negative rates, zero horizons,
  and uninitialized queues without raising uncaught exceptions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import enum
import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

try:
    from scipy.special import gammainc, gammaincc, factorial
    _SCIPY_AVAILABLE = True
except Exception:
    _SCIPY_AVAILABLE = False

logger = logging.getLogger(__name__)

# Numerical sentinels and defaults
EPSILON = 1e-12
DEFAULT_TIME_HORIZON_SEC = 60.0
DEFAULT_NUM_SIMULATIONS = 1000
DEFAULT_MARKET_ORDER_AVG_SIZE = 1.0
DEFAULT_TICK_SIZE = 0.01
DEFAULT_MARKET_ORDER_RATE = 5.0
DEFAULT_CANCEL_RATE = 0.02
DEFAULT_VOLATILITY = 0.25

# Event type normalization maps
_LIMIT_SYNONYMS = {"LIMIT", "LIMIT_ORDER", "ADD", "NEW", "INSERT", "POST", "L", "1"}
_CANCEL_SYNONYMS = {"CANCEL", "CANCELLATION", "DELETE", "CANCEL_ORDER", "REMOVE", "DROP", "C", "2"}
_MARKET_SYNONYMS = {"MARKET", "MARKET_ORDER", "TRADE", "EXECUTION", "FILL", "HIT", "TAKE", "M", "T", "3"}

__all__ = [
    "OrderFlowEvent",
    "LOBArrivalRates",
    "QueueSimulationResult",
    "LOBLevel",
    "LOBSnapshot",
    "LOBDynamicsResult",
    "UrgencyLevel",
    "QueuePlacementCandidate",
    "OptimalPlacementResult",
    "LiquiditySlice",
    "LiquiditySliceResult",
    "compute_lob_arrival_rates",
    "simulate_queue_position",
    "compute_cst_fill_probability",
    "simulate_lob_dynamics",
    "estimate_execution_slippage_and_timing",
    "calculate_cont_stoikov_fill_probability",
    "calculate_expected_fill_latency",
    "evaluate_optimal_queue_level",
    "slice_liquidity_order",
    "simulate_queue_fill",
]


# ===========================================================================
# 1. Data Structures & Result Containers
# ===========================================================================

@dataclass
class OrderFlowEvent:
    """Represents a single order flow / microstructure book event."""
    timestamp: float
    event_type: str  # 'LIMIT', 'CANCEL', 'MARKET'
    side: str = "BID"  # 'BID', 'ASK', 'BUY', 'SELL', 'UNKNOWN'
    price: float = 0.0
    size: float = 1.0
    level: int = 1  # 1 = touch / best quote, 2 = 1 tick away, etc.
    queue_depth_before: Optional[float] = None
    queue_depth_after: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)


@dataclass
class LOBArrivalRates:
    """Empirical Poisson arrival rates computed from order flow records."""
    valid: bool
    lambda_limit: float  # Limit order arrival rate (orders/sec)
    mu_cancel: float     # Per-share or total cancellation rate (sec^-1)
    theta_market: float  # Market order arrival rate (orders/sec)
    observation_duration_sec: float
    total_events: int
    event_counts: Dict[str, int] = field(default_factory=dict)
    rates_by_side: Dict[str, Dict[str, float]] = field(default_factory=dict)
    rates_by_level: Dict[int, Dict[str, float]] = field(default_factory=dict)
    average_order_size: Dict[str, float] = field(default_factory=dict)
    average_queue_depth: float = 0.0
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)


@dataclass
class QueueSimulationResult:
    """Results from simulating limit order queue priority and execution under CST (2010)."""
    valid: bool
    price_level: float
    order_size: float
    queue_ahead: float
    time_horizon_sec: float
    num_simulations: int
    fill_probability: float  # P(Fill | Horizon T)
    expected_fill_time_sec: Optional[float]  # E[Time | Fill]
    unconditional_fill_time_sec: float       # E[min(Time, T)]
    median_fill_time_sec: Optional[float]
    prob_adverse_move_before_fill: float     # P(Adverse Move before Fill)
    expected_fill_ratio: float               # Mean fraction of order filled
    queue_depletion_velocity: float          # Average units/sec consumed ahead
    percentiles_fill_time: Dict[str, Optional[float]] = field(default_factory=dict)
    cst_closed_form_fill_prob: Optional[float] = None
    simulated_trajectories_sample: Optional[List[Dict[str, Any]]] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)


@dataclass
class LOBLevel:
    """Price level in a limit order book."""
    price: float
    size: float
    order_count: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LOBSnapshot:
    """Point-in-time snapshot of the two-sided limit order book."""
    timestamp: float
    bids: List[Tuple[float, float]]  # List of (price, size) sorted descending by price
    asks: List[Tuple[float, float]]  # List of (price, size) sorted ascending by price
    best_bid: float = 0.0
    best_ask: float = 0.0
    mid_price: float = 0.0
    spread: float = 0.0
    micro_price: float = 0.0
    bid_depth: float = 0.0
    ask_depth: float = 0.0

    def __post_init__(self):
        if self.bids:
            self.best_bid = float(self.bids[0][0])
            self.bid_depth = float(sum(size for _, size in self.bids))
        if self.asks:
            self.best_ask = float(self.asks[0][0])
            self.ask_depth = float(sum(size for _, size in self.asks))
        if self.bids and self.asks:
            self.mid_price = (self.best_bid + self.best_ask) / 2.0
            self.spread = self.best_ask - self.best_bid
            total_touch_depth = self.bids[0][1] + self.asks[0][1]
            if total_touch_depth > EPSILON:
                # Volume-weighted micro-price: (Ask_Size * Bid + Bid_Size * Ask) / (Bid_Size + Ask_Size)
                self.micro_price = (
                    self.asks[0][1] * self.best_bid + self.bids[0][1] * self.best_ask
                ) / total_touch_depth
            else:
                self.micro_price = self.mid_price
        elif self.bids:
            self.mid_price = self.best_bid
            self.micro_price = self.best_bid
        elif self.asks:
            self.mid_price = self.best_ask
            self.micro_price = self.best_ask

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LOBDynamicsResult:
    """Result of simulating full LOB evolution over time."""
    valid: bool
    timestamps: List[float]
    mid_prices: List[float]
    spreads: List[float]
    micro_prices: List[float]
    trade_events: List[Dict[str, Any]]
    final_snapshot: Optional[LOBSnapshot] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class UrgencyLevel(str, enum.Enum):
    """Urgency profiles modulating the trade-off between fill probability and spread capture."""
    PASSIVE = "passive"          # Patient, seeks maximum spread capture deeper in book
    NORMAL = "normal"            # Balanced trade-off between edge and latency
    AGGRESSIVE = "aggressive"    # High fill priority, favors top of book
    IMMEDIATE = "immediate"      # Immediate fill priority, crosses spread if necessary


@dataclass
class QueuePlacementCandidate:
    """Evaluation metrics for placing a limit order at a specific LOB queue level."""
    level_index: int
    price: float
    side: str
    depth_ahead: float
    fill_probability: float
    expected_fill_latency_sec: float
    spread_capture: float
    adverse_selection_cost: float
    time_decay_hazard: float
    net_expected_edge: float
    queue_position_score: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OptimalPlacementResult:
    """Structured result returned by evaluate_optimal_queue_level."""
    valid: bool
    side: str
    target_size: float
    urgency: str
    recommended_level: int
    recommended_price: float
    queue_position_score: float
    expected_fill_latency_sec: float
    expected_fill_probability: float
    expected_spread_capture: float
    expected_adverse_selection: float
    time_decay_hazard: float
    net_edge: float
    mid_price: float
    spread: float
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    rationale: str = ""
    reason: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)


@dataclass
class LiquiditySlice:
    """Individual child slice generated by the liquidity slicing optimizer."""
    slice_index: int
    size: float
    target_price: float
    level_index: int
    delay_sec: float
    expected_latency_sec: float
    participation_rate: float
    side: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LiquiditySliceResult:
    """Result of slicing a parent order across LOB levels and time."""
    valid: bool
    total_target_size: float
    total_sliced_size: float
    num_slices: int
    side: str
    estimated_duration_sec: float
    average_target_price: float
    estimated_market_impact: float
    slices: List[Dict[str, Any]] = field(default_factory=list)
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)


# ===========================================================================
# 2. Arrival Rate Estimation (Cont, Stoikov, Talreja 2010)
# ===========================================================================

def _normalize_event_type(raw_type: Any) -> str:
    """Maps raw event type strings/codes to canonical 'LIMIT', 'CANCEL', or 'MARKET'."""
    if raw_type is None:
        return "UNKNOWN"
    s = str(raw_type).strip().upper()
    if s in _LIMIT_SYNONYMS:
        return "LIMIT"
    if s in _CANCEL_SYNONYMS:
        return "CANCEL"
    if s in _MARKET_SYNONYMS:
        return "MARKET"
    # Substring heuristics
    if "LIMIT" in s or "ADD" in s or "INSERT" in s or "NEW" in s:
        return "LIMIT"
    if "CANCEL" in s or "DELETE" in s or "REMOVE" in s:
        return "CANCEL"
    if "MARKET" in s or "TRADE" in s or "EXEC" in s or "FILL" in s:
        return "MARKET"
    return "UNKNOWN"


def _normalize_side(raw_side: Any) -> str:
    """Normalizes side strings into 'BID' or 'ASK'."""
    if raw_side is None:
        return "UNKNOWN"
    s = str(raw_side).strip().upper()
    if s in {"BID", "BUY", "B", "1"}:
        return "BID"
    if s in {"ASK", "OFFER", "SELL", "S", "A", "-1", "2"}:
        return "ASK"
    return "UNKNOWN"


def _extract_event_fields(record: Any) -> Tuple[float, str, str, float, float, int, Optional[float]]:
    """Extracts (timestamp, event_type, side, price, size, level, queue_depth) from record."""
    if isinstance(record, OrderFlowEvent):
        return (
            float(record.timestamp),
            record.event_type,
            record.side,
            float(record.price),
            float(record.size),
            int(record.level),
            record.queue_depth_before,
        )
    elif isinstance(record, dict):
        # Timestamp
        ts = record.get("timestamp") or record.get("time") or record.get("t") or 0.0
        if isinstance(ts, (datetime, np.datetime64)):
            if isinstance(ts, datetime):
                ts = ts.timestamp()
            else:
                ts = float(ts.astype("int64") / 1e9)
        else:
            ts = float(ts)

        # Event type
        etype = _normalize_event_type(
            record.get("event_type") or record.get("type") or record.get("action") or record.get("event")
        )

        # Side
        side = _normalize_side(record.get("side") or record.get("order_side"))

        # Price & Size
        price = float(record.get("price") or record.get("p") or 0.0)
        size = float(record.get("size") or record.get("volume") or record.get("v") or record.get("qty") or 1.0)
        level = int(record.get("level") or record.get("price_level") or 1)
        depth = record.get("queue_depth") or record.get("depth") or record.get("queue_depth_before")
        depth_val = float(depth) if depth is not None else None

        return ts, etype, side, price, max(EPSILON, size), level, depth_val
    else:
        # Generic object with attribute access
        ts = getattr(record, "timestamp", getattr(record, "time", 0.0))
        etype = _normalize_event_type(getattr(record, "event_type", getattr(record, "type", "UNKNOWN")))
        side = _normalize_side(getattr(record, "side", "UNKNOWN"))
        price = float(getattr(record, "price", 0.0))
        size = float(getattr(record, "size", getattr(record, "volume", 1.0)))
        level = int(getattr(record, "level", 1))
        depth = getattr(record, "queue_depth_before", getattr(record, "depth", None))
        depth_val = float(depth) if depth is not None else None

        return float(ts), etype, side, price, max(EPSILON, size), level, depth_val


def compute_lob_arrival_rates(
    order_flow_records: Sequence[Any],
    observation_duration_sec: Optional[float] = None,
    side_filter: Optional[str] = None,
    level_filter: Optional[int] = None,
) -> LOBArrivalRates:
    """
    Computes empirical Poisson arrival rates from limit order book event records:
    - lambda_limit: Poisson arrival rate of limit orders (orders / second).
    - mu_cancel: Cancellation intensity per unit depth (cancellations / (second * queue_depth))
      or per-second rate if queue depth is unobserved.
    - theta_market: Poisson arrival rate of market orders (orders / second).

    Parameters:
    -----------
    order_flow_records: Sequence of dicts, OrderFlowEvent, or objects with event metadata.
    observation_duration_sec: Explicit duration in seconds. If None, derived from min/max timestamps.
    side_filter: Optional 'BID' or 'ASK' filter. If None, pools both sides.
    level_filter: Optional price level filter (e.g. 1 for inside touch).

    Returns:
    --------
    LOBArrivalRates dataclass containing estimated parameters, breakdown by side, and breakdown by level.
    """
    if not order_flow_records:
        return LOBArrivalRates(
            valid=False,
            lambda_limit=0.0,
            mu_cancel=0.0,
            theta_market=0.0,
            observation_duration_sec=0.0,
            total_events=0,
            event_counts={"LIMIT": 0, "CANCEL": 0, "MARKET": 0},
            reason="No order flow records provided",
        )

    parsed_events = []
    min_ts = float("inf")
    max_ts = float("-inf")

    side_filter_norm = _normalize_side(side_filter) if side_filter else None

    for r in order_flow_records:
        try:
            ts, etype, side, price, size, level, depth = _extract_event_fields(r)
            if etype == "UNKNOWN":
                continue
            if side_filter_norm and side_filter_norm != "UNKNOWN" and side != side_filter_norm:
                continue
            if level_filter is not None and level != level_filter:
                continue

            parsed_events.append((ts, etype, side, price, size, level, depth))
            if ts < min_ts:
                min_ts = ts
            if ts > max_ts:
                max_ts = ts
        except Exception:
            continue

    if not parsed_events:
        return LOBArrivalRates(
            valid=False,
            lambda_limit=0.0,
            mu_cancel=0.0,
            theta_market=0.0,
            observation_duration_sec=0.0,
            total_events=0,
            event_counts={"LIMIT": 0, "CANCEL": 0, "MARKET": 0},
            reason="No valid events parsed from input records",
        )

    # Determine observation duration
    if observation_duration_sec is not None and observation_duration_sec > 0:
        duration = float(observation_duration_sec)
    else:
        duration = max_ts - min_ts
        if duration <= EPSILON:
            # Fallback duration if all events share identical timestamp
            duration = 1.0

    # Aggregations
    counts = {"LIMIT": 0, "CANCEL": 0, "MARKET": 0}
    total_sizes = {"LIMIT": 0.0, "CANCEL": 0.0, "MARKET": 0.0}
    depth_sum = 0.0
    depth_count = 0

    rates_by_side: Dict[str, Dict[str, float]] = {
        "BID": {"LIMIT": 0, "CANCEL": 0, "MARKET": 0, "total_size": 0.0},
        "ASK": {"LIMIT": 0, "CANCEL": 0, "MARKET": 0, "total_size": 0.0},
    }

    rates_by_level: Dict[int, Dict[str, float]] = {}

    for ts, etype, side, price, size, level, depth in parsed_events:
        counts[etype] = counts.get(etype, 0) + 1
        total_sizes[etype] = total_sizes.get(etype, 0.0) + size

        if side in rates_by_side:
            rates_by_side[side][etype] = rates_by_side[side].get(etype, 0) + 1
            rates_by_side[side]["total_size"] += size

        if level not in rates_by_level:
            rates_by_level[level] = {"LIMIT": 0.0, "CANCEL": 0.0, "MARKET": 0.0}
        rates_by_level[level][etype] = rates_by_level[level].get(etype, 0.0) + 1.0

        if depth is not None and depth > 0:
            depth_sum += depth
            depth_count += 1

    # Base rates per second
    lambda_limit = counts["LIMIT"] / duration
    theta_market = counts["MARKET"] / duration

    avg_depth = (depth_sum / depth_count) if depth_count > 0 else 0.0

    # Per-share cancellation rate mu:
    # In CST (2010), each share in queue has cancellation hazard mu.
    # If mean queue depth Q_bar is known, total cancellations in time T is mu * Q_bar * T
    # => mu = N_cancel / (T * Q_bar). If Q_bar is unobserved, mu = N_cancel / T.
    if avg_depth > EPSILON:
        mu_cancel = counts["CANCEL"] / (duration * avg_depth)
    else:
        mu_cancel = counts["CANCEL"] / duration

    # Format side and level breakdowns into rates
    side_breakdowns: Dict[str, Dict[str, float]] = {}
    for s, s_counts in rates_by_side.items():
        side_breakdowns[s] = {
            "lambda_limit": s_counts.get("LIMIT", 0) / duration,
            "mu_cancel": s_counts.get("CANCEL", 0) / duration,
            "theta_market": s_counts.get("MARKET", 0) / duration,
        }

    level_breakdowns: Dict[int, Dict[str, float]] = {}
    for lvl, lvl_counts in rates_by_level.items():
        level_breakdowns[lvl] = {
            "lambda_limit": lvl_counts.get("LIMIT", 0.0) / duration,
            "mu_cancel": lvl_counts.get("CANCEL", 0.0) / duration,
            "theta_market": lvl_counts.get("MARKET", 0.0) / duration,
        }

    avg_sizes = {
        k: (total_sizes[k] / counts[k]) if counts[k] > 0 else 0.0
        for k in counts
    }

    return LOBArrivalRates(
        valid=True,
        lambda_limit=round(float(lambda_limit), 6),
        mu_cancel=round(float(mu_cancel), 6),
        theta_market=round(float(theta_market), 6),
        observation_duration_sec=round(float(duration), 4),
        total_events=len(parsed_events),
        event_counts=counts,
        rates_by_side=side_breakdowns,
        rates_by_level=level_breakdowns,
        average_order_size=avg_sizes,
        average_queue_depth=round(float(avg_depth), 2),
    )


# ===========================================================================
# 3. Closed-Form Analytical Approximations (Cont-Stoikov-Talreja 2010)
# ===========================================================================

def compute_cst_fill_probability(
    queue_ahead: float,
    order_size: float,
    theta_market: float,
    mu_cancel: float = 0.0,
    time_horizon_sec: float = 60.0,
) -> float:
    """
    Computes analytical probability of limit order execution within horizon T under the
    Cont-Stoikov-Talreja (2010) death-process formulation.

    When cancellation rate mu = 0:
      Total required market order hits k = ceil(queue_ahead + order_size).
      Number of market orders in [0, T] follows Poisson(theta * T).
      P(Fill | T) = P(Poisson(theta * T) >= k) = gammaincc(k, theta * T).

    When cancellation rate mu > 0:
      Uses the non-homogeneous death-process transition probability / Markov absorption.

    Returns:
    --------
    float in [0.0, 1.0]
    """
    try:
        q_ahead = max(0.0, float(queue_ahead))
        s_size = max(0.0, float(order_size))
        theta = max(0.0, float(theta_market))
        mu = max(0.0, float(mu_cancel))
        T = max(0.0, float(time_horizon_sec))

        if s_size <= EPSILON:
            return 1.0
        if T <= EPSILON:
            return 0.0 if q_ahead > 0 else 1.0
        if theta <= EPSILON and mu <= EPSILON:
            return 0.0

        required_depletion = q_ahead + s_size

        if mu <= 1e-9:
            # Pure Poisson arrival case:
            # P(N(T) >= k) where N(T) ~ Poisson(theta * T)
            k = int(math.ceil(required_depletion))
            lam = theta * T
            if _SCIPY_AVAILABLE:
                # Regularized lower incomplete gamma function = P(Poisson(lam) >= k)
                return float(np.clip(gammainc(k, lam), 0.0, 1.0))
            else:
                # Pure python Poisson tail sum
                # P(N < k) = sum_{j=0}^{k-1} e^-lam * lam^j / j!
                cdf = 0.0
                term = math.exp(-lam)
                for j in range(k):
                    cdf += term
                    term *= lam / (j + 1)
                return float(np.clip(1.0 - cdf, 0.0, 1.0))
        else:
            # With individual cancellations:
            # Effective rate of depletion at queue size q is theta + mu * q.
            # Average depletion velocity v(t) is approximately theta + mu * Q(t).
            # Expected queue position at time t satisfies: dQ/dt = -(theta + mu * Q).
            # Q(t) = (Q_0 + theta/mu) * exp(-mu * t) - theta/mu.
            # Time to hit 0: t_fill = (1/mu) * ln((Q_0 + theta/mu) / (theta/mu)).
            # We compute Gaussian / Poisson diffusion approximation of absorption by T:
            expected_depletion = (q_ahead + theta / (mu + EPSILON)) * (1.0 - math.exp(-mu * T)) + (theta * T if q_ahead <= 0 else 0.0)
            depletion_std = math.sqrt(max(EPSILON, expected_depletion * (1.0 + mu * T)))
            z = (expected_depletion - required_depletion) / max(EPSILON, depletion_std)
            # Standard normal CDF
            prob = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
            return float(np.clip(prob, 0.0, 1.0))
    except Exception:
        return 0.0


# ===========================================================================
# 4. Markovian Queue Position Simulator (Gillespie SSA / Jump Process)
# ===========================================================================

def simulate_queue_position(
    price_level: float,
    order_size: float,
    queue_ahead: float,
    lambda_limit: float,
    mu_cancel: float,
    theta_market: float,
    time_horizon_sec: float = DEFAULT_TIME_HORIZON_SEC,
    num_simulations: int = DEFAULT_NUM_SIMULATIONS,
    opposite_queue: float = 100.0,
    theta_opposite: Optional[float] = None,
    mu_opposite: Optional[float] = None,
    lambda_opposite: Optional[float] = None,
    market_order_avg_size: float = DEFAULT_MARKET_ORDER_AVG_SIZE,
    random_seed: Optional[int] = None,
    store_sample_trajectories: bool = False,
) -> QueueSimulationResult:
    """
    Simulates limit order queue progression, fill timing, and adverse selection risk
    under the Cont-Stoikov-Talreja (2010) Markovian order book model.

    Mechanics:
    ----------
    - Limit order of size `order_size` is placed at `price_level` behind `queue_ahead`.
    - Event intensities:
      1. Market orders hitting our level: $\\theta$ (consumes queue ahead first, then fills our order).
      2. Cancellations in queue ahead: $\\mu \\cdot Q_{\\text{ahead}}(t)$.
      3. New limit orders joining behind: $\\lambda$ (does not affect our priority under FIFO).
      4. Opposite side market orders: $\\theta_{\\text{opp}}$ (depletes opposite book).
      5. Opposite side cancellations: $\\mu_{\\text{opp}} \\cdot Q_{\\text{opp}}(t)$.
    - An adverse price move occurs if the opposite queue is fully depleted and the mid-price moves away
      before our order is completely executed.

    Parameters:
    -----------
    price_level: Float price of the limit order (e.g. 100.0).
    order_size: Size of our limit order (e.g. 10.0 contracts/shares).
    queue_ahead: Initial volume sitting ahead of our order at the same price level.
    lambda_limit: Limit order arrival intensity (orders/sec).
    mu_cancel: Per-unit cancellation intensity (sec^-1).
    theta_market: Market order arrival intensity (orders/sec).
    time_horizon_sec: Maximum simulation time window in seconds (default 60.0).
    num_simulations: Number of Monte Carlo trajectories (default 1000).
    opposite_queue: Initial depth on the opposite side of the book.
    theta_opposite: Market order arrival intensity on opposite side (defaults to theta_market).
    mu_opposite: Cancellation rate on opposite side (defaults to mu_cancel).
    lambda_opposite: Limit arrival rate on opposite side (defaults to lambda_limit).
    market_order_avg_size: Average execution size of incoming market orders (default 1.0).
    random_seed: Optional random seed for reproducible testing.
    store_sample_trajectories: If True, records up to 5 sample simulation paths.

    Returns:
    --------
    QueueSimulationResult dataclass containing fill probability, expected fill time,
    adverse move probability, and percentiles.
    """
    # Defensive input validation & normalization
    try:
        p_lvl = float(price_level)
        s_size = max(0.0, float(order_size))
        q_ahead_init = max(0.0, float(queue_ahead))
        lam = max(0.0, float(lambda_limit))
        mu = max(0.0, float(mu_cancel))
        theta = max(0.0, float(theta_market))
        T = max(EPSILON, float(time_horizon_sec))
        n_sims = max(1, int(num_simulations))
        opp_q_init = max(0.0, float(opposite_queue))

        theta_opp = float(theta_opposite) if theta_opposite is not None else theta
        mu_opp = float(mu_opposite) if mu_opposite is not None else mu
        lam_opp = float(lambda_opposite) if lambda_opposite is not None else lam
        mkt_size = max(EPSILON, float(market_order_avg_size))
    except Exception as e:
        return QueueSimulationResult(
            valid=False,
            price_level=0.0,
            order_size=0.0,
            queue_ahead=0.0,
            time_horizon_sec=0.0,
            num_simulations=0,
            fill_probability=0.0,
            expected_fill_time_sec=None,
            unconditional_fill_time_sec=0.0,
            median_fill_time_sec=None,
            prob_adverse_move_before_fill=0.0,
            expected_fill_ratio=0.0,
            queue_depletion_velocity=0.0,
            reason=f"Invalid parameter format: {e}",
        )

    # Trivial edge cases
    if s_size <= EPSILON:
        return QueueSimulationResult(
            valid=True,
            price_level=p_lvl,
            order_size=0.0,
            queue_ahead=q_ahead_init,
            time_horizon_sec=T,
            num_simulations=n_sims,
            fill_probability=1.0,
            expected_fill_time_sec=0.0,
            unconditional_fill_time_sec=0.0,
            median_fill_time_sec=0.0,
            prob_adverse_move_before_fill=0.0,
            expected_fill_ratio=1.0,
            queue_depletion_velocity=0.0,
            percentiles_fill_time={"p10": 0.0, "p25": 0.0, "p50": 0.0, "p75": 0.0, "p90": 0.0, "p95": 0.0},
            cst_closed_form_fill_prob=1.0,
        )

    if theta <= EPSILON and mu <= EPSILON and q_ahead_init > 0:
        # Zero market flow and zero cancellations: Queue never depletes
        return QueueSimulationResult(
            valid=True,
            price_level=p_lvl,
            order_size=s_size,
            queue_ahead=q_ahead_init,
            time_horizon_sec=T,
            num_simulations=n_sims,
            fill_probability=0.0,
            expected_fill_time_sec=None,
            unconditional_fill_time_sec=T,
            median_fill_time_sec=None,
            prob_adverse_move_before_fill=0.0,
            expected_fill_ratio=0.0,
            queue_depletion_velocity=0.0,
            percentiles_fill_time={"p10": None, "p25": None, "p50": None, "p75": None, "p90": None, "p95": None},
            cst_closed_form_fill_prob=0.0,
        )

    rng = np.random.default_rng(random_seed)

    fill_times: List[float] = []
    unconditional_times: List[float] = []
    filled_flags: List[bool] = []
    adverse_move_flags: List[bool] = []
    fill_ratios: List[float] = []
    depletion_velocities: List[float] = []
    sample_trajectories: List[Dict[str, Any]] = []

    # Closed-form theoretical prediction for reference
    cst_analytic_p = compute_cst_fill_probability(
        queue_ahead=q_ahead_init,
        order_size=s_size,
        theta_market=theta,
        mu_cancel=mu,
        time_horizon_sec=T,
    )

    # Monte Carlo Gillespie Jump Process Loop
    for sim_idx in range(n_sims):
        t = 0.0
        q_ahead = q_ahead_init
        s_rem = s_size
        opp_q = opp_q_init
        adverse_move = False
        trajectory_points = []

        if store_sample_trajectories and sim_idx < 5:
            trajectory_points.append({"t": 0.0, "queue_ahead": q_ahead, "remaining_order": s_rem})

        while t < T and s_rem > EPSILON:
            # Active transition intensities:
            # 1. Market order hitting our queue level: theta
            r_mkt = theta if (q_ahead > 0 or s_rem > 0) else 0.0
            # 2. Cancellation in queue ahead: mu * q_ahead
            r_cancel_ahead = mu * q_ahead
            # 3. New limit order behind: lambda
            r_limit_behind = lam
            # 4. Market order hitting opposite side: theta_opp
            r_mkt_opp = theta_opp if opp_q > 0 else 0.0
            # 5. Cancellation on opposite side: mu_opp * opp_q
            r_cancel_opp = mu_opp * opp_q

            r_total = r_mkt + r_cancel_ahead + r_limit_behind + r_mkt_opp + r_cancel_opp

            if r_total <= EPSILON:
                break

            # Time to next jump event (exponential distribution)
            dt = rng.exponential(1.0 / r_total)
            if t + dt > T:
                t = T
                break

            t += dt

            # Determine which event fired
            u = rng.uniform(0.0, r_total)

            if u < r_mkt:
                # Market order hit
                # Consumes queue ahead first
                if q_ahead > 0:
                    consumed = min(q_ahead, mkt_size)
                    q_ahead -= consumed
                    leftover_mkt = mkt_size - consumed
                    if leftover_mkt > EPSILON and q_ahead <= EPSILON:
                        # Leftover market order executes part of our order
                        fill_qty = min(s_rem, leftover_mkt)
                        s_rem -= fill_qty
                else:
                    # Directly hits our order
                    fill_qty = min(s_rem, mkt_size)
                    s_rem -= fill_qty

            elif u < r_mkt + r_cancel_ahead:
                # Cancellation in queue ahead
                cancel_qty = min(q_ahead, 1.0)
                q_ahead = max(0.0, q_ahead - cancel_qty)

            elif u < r_mkt + r_cancel_ahead + r_limit_behind:
                # Limit order joined behind us: no change to our priority or queue ahead
                pass

            elif u < r_mkt + r_cancel_ahead + r_limit_behind + r_mkt_opp:
                # Opposite market order hit
                opp_q = max(0.0, opp_q - mkt_size)
                if opp_q <= EPSILON and s_rem > EPSILON:
                    # Opposite book cleared -> adverse market move triggered
                    adverse_move = True
                    # Re-supply opposite book with Poisson replacement
                    opp_q = opp_q_init * 0.5

            else:
                # Opposite cancellation
                opp_q = max(0.0, opp_q - 1.0)
                if opp_q <= EPSILON and s_rem > EPSILON:
                    adverse_move = True
                    opp_q = opp_q_init * 0.5

            if store_sample_trajectories and sim_idx < 5:
                trajectory_points.append({"t": round(t, 4), "queue_ahead": round(q_ahead, 2), "remaining_order": round(s_rem, 2)})

        is_filled = s_rem <= EPSILON
        filled_flags.append(is_filled)
        fill_ratio = (s_size - s_rem) / s_size
        fill_ratios.append(fill_ratio)

        if is_filled:
            fill_times.append(t)
            unconditional_times.append(t)
            # Depletion velocity for filled trajectory
            total_depleted = q_ahead_init + s_size
            vel = total_depleted / max(EPSILON, t)
            depletion_velocities.append(vel)
        else:
            unconditional_times.append(T)
            # Depletion velocity for partial/unfilled trajectory
            depleted_so_far = (q_ahead_init - q_ahead) + (s_size - s_rem)
            vel = depleted_so_far / max(EPSILON, T)
            depletion_velocities.append(vel)

        adverse_move_flags.append(adverse_move and (not is_filled or adverse_move))

        if store_sample_trajectories and sim_idx < 5:
            sample_trajectories.append({
                "sim_idx": sim_idx,
                "filled": is_filled,
                "fill_time": round(t, 4) if is_filled else None,
                "adverse_move": adverse_move,
                "trajectory": trajectory_points,
            })

    # Summary Statistics
    p_fill = float(np.mean(filled_flags))
    expected_fill_time = float(np.mean(fill_times)) if fill_times else None
    unconditional_fill_time = float(np.mean(unconditional_times))
    median_fill_time = float(np.median(fill_times)) if fill_times else None
    p_adverse = float(np.mean(adverse_move_flags))
    expected_fill_ratio = float(np.mean(fill_ratios))
    avg_velocity = float(np.mean(depletion_velocities)) if depletion_velocities else 0.0

    percentiles: Dict[str, Optional[float]] = {}
    if fill_times:
        p_arr = np.percentile(fill_times, [10, 25, 50, 75, 90, 95])
        percentiles = {
            "p10": round(float(p_arr[0]), 4),
            "p25": round(float(p_arr[1]), 4),
            "p50": round(float(p_arr[2]), 4),
            "p75": round(float(p_arr[3]), 4),
            "p90": round(float(p_arr[4]), 4),
            "p95": round(float(p_arr[5]), 4),
        }
    else:
        percentiles = {"p10": None, "p25": None, "p50": None, "p75": None, "p90": None, "p95": None}

    return QueueSimulationResult(
        valid=True,
        price_level=p_lvl,
        order_size=round(s_size, 4),
        queue_ahead=round(q_ahead_init, 4),
        time_horizon_sec=round(T, 2),
        num_simulations=n_sims,
        fill_probability=round(p_fill, 4),
        expected_fill_time_sec=round(expected_fill_time, 4) if expected_fill_time is not None else None,
        unconditional_fill_time_sec=round(unconditional_fill_time, 4),
        median_fill_time_sec=round(median_fill_time, 4) if median_fill_time is not None else None,
        prob_adverse_move_before_fill=round(p_adverse, 4),
        expected_fill_ratio=round(expected_fill_ratio, 4),
        queue_depletion_velocity=round(avg_velocity, 4),
        percentiles_fill_time=percentiles,
        cst_closed_form_fill_prob=round(cst_analytic_p, 4) if cst_analytic_p is not None else None,
        simulated_trajectories_sample=sample_trajectories if store_sample_trajectories else None,
    )


# ===========================================================================
# 5. Full Limit Order Book Multi-Level Simulator
# ===========================================================================

def simulate_lob_dynamics(
    initial_snapshot: LOBSnapshot,
    arrival_rates: LOBArrivalRates,
    time_horizon_sec: float = 30.0,
    tick_size: float = 0.01,
    random_seed: Optional[int] = None,
) -> LOBDynamicsResult:
    """
    Simulates full multi-level bid and ask limit order book evolution over time
    using the Cont-Stoikov-Talreja (2010) Markovian jump process.

    Parameters:
    -----------
    initial_snapshot: LOBSnapshot with starting bids and asks.
    arrival_rates: LOBArrivalRates containing lambda, mu, theta.
    time_horizon_sec: Duration in seconds to simulate.
    tick_size: Minimum price increment.
    random_seed: Random seed for deterministic simulation.

    Returns:
    --------
    LOBDynamicsResult containing time series of mid-prices, spreads, micro-prices, and trade events.
    """
    if not initial_snapshot or not initial_snapshot.bids or not initial_snapshot.asks:
        return LOBDynamicsResult(
            valid=False,
            timestamps=[],
            mid_prices=[],
            spreads=[],
            micro_prices=[],
            trade_events=[],
            reason="Invalid initial LOB snapshot",
        )

    rng = np.random.default_rng(random_seed)

    # Initialize book structures: dict price -> size
    bid_book: Dict[float, float] = {round(p, 4): max(0.0, s) for p, s in initial_snapshot.bids}
    ask_book: Dict[float, float] = {round(p, 4): max(0.0, s) for p, s in initial_snapshot.asks}

    lam = max(0.01, arrival_rates.lambda_limit)
    mu = max(0.001, arrival_rates.mu_cancel)
    theta = max(0.01, arrival_rates.theta_market)

    t = 0.0
    T = max(1.0, float(time_horizon_sec))

    timestamps: List[float] = [0.0]
    mid_prices: List[float] = [initial_snapshot.mid_price]
    spreads: List[float] = [initial_snapshot.spread]
    micro_prices: List[float] = [initial_snapshot.micro_price]
    trade_events: List[Dict[str, Any]] = []

    while t < T:
        # Find best bid & ask
        valid_bids = [p for p, s in bid_book.items() if s > EPSILON]
        valid_asks = [p for p, s in ask_book.items() if s > EPSILON]

        if not valid_bids or not valid_asks:
            # Replenish if book becomes empty
            if not valid_bids:
                curr_ask = min(valid_asks) if valid_asks else initial_snapshot.best_ask
                bid_book[round(curr_ask - tick_size, 4)] = 10.0
            if not valid_asks:
                curr_bid = max(valid_bids) if valid_bids else initial_snapshot.best_bid
                ask_book[round(curr_bid + tick_size, 4)] = 10.0
            valid_bids = [p for p, s in bid_book.items() if s > EPSILON]
            valid_asks = [p for p, s in ask_book.items() if s > EPSILON]

        best_bid = max(valid_bids)
        best_ask = min(valid_asks)

        bid_depth = sum(bid_book[p] for p in valid_bids)
        ask_depth = sum(ask_book[p] for p in valid_asks)

        # Intensities:
        # Market buy arrives (hits ask): theta
        # Market sell arrives (hits bid): theta
        # Limit buy arrives: lambda
        # Limit ask arrives: lambda
        # Bid cancellations: mu * bid_depth
        # Ask cancellations: mu * ask_depth
        r_mkt_buy = theta
        r_mkt_sell = theta
        r_lim_bid = lam
        r_lim_ask = lam
        r_can_bid = mu * bid_depth
        r_can_ask = mu * ask_depth

        r_total = r_mkt_buy + r_mkt_sell + r_lim_bid + r_lim_ask + r_can_bid + r_can_ask
        if r_total <= EPSILON:
            break

        dt = rng.exponential(1.0 / r_total)
        if t + dt > T:
            t = T
            break
        t += dt

        u = rng.uniform(0.0, r_total)

        if u < r_mkt_buy:
            # Market buy executes against best ask
            exec_size = 1.0
            ask_book[best_ask] = max(0.0, ask_book[best_ask] - exec_size)
            trade_events.append({"t": round(t, 4), "side": "BUY", "price": best_ask, "size": exec_size})

        elif u < r_mkt_buy + r_mkt_sell:
            # Market sell executes against best bid
            exec_size = 1.0
            bid_book[best_bid] = max(0.0, bid_book[best_bid] - exec_size)
            trade_events.append({"t": round(t, 4), "side": "SELL", "price": best_bid, "size": exec_size})

        elif u < r_mkt_buy + r_mkt_sell + r_lim_bid:
            # Limit buy placed at best bid or 1 tick deeper
            place_price = best_bid if rng.random() > 0.3 else round(best_bid - tick_size, 4)
            bid_book[place_price] = bid_book.get(place_price, 0.0) + 1.0

        elif u < r_mkt_buy + r_mkt_sell + r_lim_bid + r_lim_ask:
            # Limit ask placed at best ask or 1 tick higher
            place_price = best_ask if rng.random() > 0.3 else round(best_ask + tick_size, 4)
            ask_book[place_price] = ask_book.get(place_price, 0.0) + 1.0

        elif u < r_mkt_buy + r_mkt_sell + r_lim_bid + r_lim_ask + r_can_bid:
            # Cancellation on bid side (weighted by size)
            if valid_bids:
                can_price = rng.choice(valid_bids)
                bid_book[can_price] = max(0.0, bid_book[can_price] - 1.0)

        else:
            # Cancellation on ask side
            if valid_asks:
                can_price = rng.choice(valid_asks)
                ask_book[can_price] = max(0.0, ask_book[can_price] - 1.0)

        # Record metrics at time t
        cur_bids = sorted([(p, s) for p, s in bid_book.items() if s > EPSILON], key=lambda x: x[0], reverse=True)
        cur_asks = sorted([(p, s) for p, s in ask_book.items() if s > EPSILON], key=lambda x: x[0])
        if cur_bids and cur_asks:
            b_bid = cur_bids[0][0]
            b_ask = cur_asks[0][0]
            m_price = (b_bid + b_ask) / 2.0
            sp = b_ask - b_bid
            tot_depth = cur_bids[0][1] + cur_asks[0][1]
            u_price = (cur_asks[0][1] * b_bid + cur_bids[0][1] * b_ask) / tot_depth if tot_depth > 0 else m_price

            timestamps.append(round(t, 4))
            mid_prices.append(round(m_price, 4))
            spreads.append(round(sp, 4))
            micro_prices.append(round(u_price, 4))

    # Final snapshot
    final_bids = sorted([(p, s) for p, s in bid_book.items() if s > EPSILON], key=lambda x: x[0], reverse=True)
    final_asks = sorted([(p, s) for p, s in ask_book.items() if s > EPSILON], key=lambda x: x[0])
    final_snap = LOBSnapshot(timestamp=round(t, 4), bids=final_bids, asks=final_asks)

    return LOBDynamicsResult(
        valid=True,
        timestamps=timestamps,
        mid_prices=mid_prices,
        spreads=spreads,
        micro_prices=micro_prices,
        trade_events=trade_events,
        final_snapshot=final_snap,
    )


# ===========================================================================
# 6. Practical Slippage & Execution Timing Estimator
# ===========================================================================

def estimate_execution_slippage_and_timing(
    quote_bid: float,
    quote_ask: float,
    order_side: str,
    order_size: float,
    touch_queue_ahead: float,
    lambda_limit: float,
    mu_cancel: float,
    theta_market: float,
    urgency_alpha: float = 0.5,
    time_horizon_sec: float = 60.0,
    random_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Evaluates execution trade-offs between:
    1. **Passive Limit Order (Join Touch)**: Captures half-spread but bears queue waiting time and adverse selection risk.
    2. **Active Market Order (Cross Spread)**: Instant 100% fill but immediately incurs half-spread fee.
    3. **Midpoint Peg / Inside Penny**: Improved queue priority with balanced cost.

    Parameters:
    -----------
    quote_bid: Best bid price.
    quote_ask: Best ask price.
    order_side: 'BUY' or 'SELL'.
    order_size: Number of contracts/shares.
    touch_queue_ahead: Resting depth at best bid/ask ahead of us.
    lambda_limit: Limit arrival rate.
    mu_cancel: Cancellation rate.
    theta_market: Market arrival rate.
    urgency_alpha: Urgency weight in [0, 1] (0 = pure cost minimizer, 1 = immediate execution priority).
    time_horizon_sec: Evaluation window in seconds.
    random_seed: Random seed for simulation.

    Returns:
    --------
    Dict with recommendation ('PASSIVE_LIMIT', 'AGGRESSIVE_MARKET', or 'MIDPOINT_PEG'),
    expected savings, fill probability, and detailed comparison breakdown.
    """
    try:
        bid = float(quote_bid)
        ask = float(quote_ask)
        spread = max(0.0, ask - bid)
        half_spread = spread / 2.0
        mid = (bid + ask) / 2.0
        side = _normalize_side(order_side)
        size = max(EPSILON, float(order_size))
        alpha = float(np.clip(urgency_alpha, 0.0, 1.0))

        # Simulate passive queue at touch
        sim_touch = simulate_queue_position(
            price_level=bid if side == "BUY" else ask,
            order_size=size,
            queue_ahead=touch_queue_ahead,
            lambda_limit=lambda_limit,
            mu_cancel=mu_cancel,
            theta_market=theta_market,
            time_horizon_sec=time_horizon_sec,
            num_simulations=500,
            random_seed=random_seed,
        )

        p_fill = sim_touch.fill_probability
        p_adverse = sim_touch.prob_adverse_move_before_fill
        exp_time = sim_touch.expected_fill_time_sec or time_horizon_sec

        # Cost Analysis:
        # Active cross cost: size * half_spread
        active_spread_cost = size * half_spread

        # Passive expected spread capture: size * half_spread * p_fill
        # Passive expected adverse selection penalty: size * half_spread * p_adverse * 1.5
        # Passive delay penalty: urgency_alpha * (exp_time / time_horizon_sec) * half_spread * size
        passive_expected_savings = (size * half_spread * p_fill) - (size * half_spread * p_adverse * 1.5)
        passive_urgency_cost = alpha * (exp_time / time_horizon_sec) * active_spread_cost
        passive_net_score = passive_expected_savings - passive_urgency_cost

        # Policy recommendation
        if alpha >= 0.85 or p_fill < 0.25:
            rec = "AGGRESSIVE_MARKET"
            rationale = "High execution urgency or low passive fill probability (< 25%); market crossing recommended."
        elif passive_net_score > 0 and p_fill >= 0.50:
            rec = "PASSIVE_LIMIT"
            rationale = f"Passive limit captures expected spread edge (${round(passive_expected_savings, 2)}) with {round(p_fill * 100, 1)}% fill probability."
        else:
            rec = "MIDPOINT_PEG"
            rationale = "Midpoint peg balances queue priority and reduces adverse selection risk."

        return {
            "valid": True,
            "recommended_action": rec,
            "rationale": rationale,
            "quote_bid": bid,
            "quote_ask": ask,
            "spread": round(spread, 4),
            "order_side": side,
            "order_size": size,
            "passive_fill_probability": p_fill,
            "passive_expected_fill_time_sec": exp_time,
            "prob_adverse_move": p_adverse,
            "active_crossing_cost": round(active_spread_cost, 4),
            "passive_expected_savings": round(passive_expected_savings, 4),
            "passive_net_score": round(passive_net_score, 4),
            "urgency_alpha": alpha,
        }
    except Exception as e:
        return {
            "valid": False,
            "recommended_action": "AGGRESSIVE_MARKET",
            "rationale": f"Degraded fallback: {e}",
            "quote_bid": 0.0,
            "quote_ask": 0.0,
            "spread": 0.0,
            "order_side": "UNKNOWN",
            "order_size": 0.0,
            "passive_fill_probability": 0.0,
            "passive_expected_fill_time_sec": 0.0,
            "prob_adverse_move": 0.0,
            "active_crossing_cost": 0.0,
            "passive_expected_savings": 0.0,
            "passive_net_score": 0.0,
            "urgency_alpha": 0.5,
        }


# ===========================================================================
# 6. Order Placement Optimizer & Liquidity Slicing (Workstream 2)
# ===========================================================================

def calculate_cont_stoikov_fill_probability(
    queue_position: float,
    depth_at_price: float,
    target_size: float,
    lambda_market: float = DEFAULT_MARKET_ORDER_RATE,
    mu_cancel: float = DEFAULT_CANCEL_RATE,
    time_horizon: float = DEFAULT_TIME_HORIZON_SEC,
) -> float:
    """
    Calculates fill probability under the Cont, Stoikov, Talreja (2010) Markovian queue model.

    Given queue position $k$ ahead of the order and queue depth $D$, the order executes when
    the queue drains through market orders $\\theta_{\\text{market}}$ and cancellations $\\mu_{\\text{cancel}}$.

    Effective queue drain rate:
        $$R_{\\text{drain}} = \\lambda_{\\text{market}} + \\mu_{\\text{cancel}} \\cdot \\max(0, k)$$

    Fill probability within time $T$:
        $$P(\\text{Fill} \\mid k, T) = 1 - \\exp\\left(-\\frac{R_{\\text{drain}} \\cdot T}{k + 0.5 \\cdot \\text{target\\_size} + \\epsilon}\\right)$$
    """
    k = max(0.0, float(queue_position))
    target = max(1.0, float(target_size))
    horizon = max(0.1, float(time_horizon))
    mkt_rate = max(0.01, float(lambda_market))
    c_rate = max(0.0, float(mu_cancel))

    effective_drain_rate = mkt_rate + (c_rate * k)
    scale = k + (0.5 * target) + EPSILON

    exponent = -(effective_drain_rate * horizon) / scale
    prob = 1.0 - math.exp(exponent)
    return max(0.01, min(0.99, prob))


def calculate_expected_fill_latency(
    queue_position: float,
    target_size: float = 1.0,
    lambda_market: float = DEFAULT_MARKET_ORDER_RATE,
    mu_cancel: float = DEFAULT_CANCEL_RATE,
) -> float:
    """
    Calculates the expected time $\\mathbb{E}[\\tau]$ (in seconds) until an order at queue position $k$
    receives a complete fill.

    $$\\mathbb{E}[\\tau] = \\frac{k + 0.5 \\cdot \\text{target\\_size}}{\\lambda_{\\text{market}} + \\mu_{\\text{cancel}} \\cdot k + \\epsilon}$$
    """
    k = max(0.0, float(queue_position))
    target = max(1.0, float(target_size))
    mkt_rate = max(0.01, float(lambda_market))
    c_rate = max(0.0, float(mu_cancel))

    denominator = mkt_rate + (c_rate * k) + EPSILON
    latency = (k + 0.5 * target) / denominator
    return max(0.05, latency)


def _normalize_lob_levels(
    levels: Union[Sequence[Tuple[float, float]], Sequence[Dict[str, Any]], Sequence[Sequence[float]], np.ndarray],
    side: str = "buy",
) -> List[LOBLevel]:
    """
    Parses and normalizes raw bids or asks into a sorted list of LOBLevel objects.
    - Bids are sorted in descending order of price (highest bid first).
    - Asks are sorted in ascending order of price (lowest ask first).
    """
    parsed: List[Tuple[float, float, int]] = []

    if levels is None:
        return []

    if isinstance(levels, np.ndarray):
        levels = levels.tolist()

    for item in levels:
        price: float = 0.0
        size: float = 0.0
        orders: int = 1

        if isinstance(item, (tuple, list)):
            if len(item) >= 2:
                try:
                    price = float(item[0])
                    size = float(item[1])
                    if len(item) >= 3:
                        orders = int(item[2])
                except (ValueError, TypeError):
                    continue
        elif isinstance(item, dict):
            try:
                price = float(item.get("price", item.get("p", 0.0)))
                size = float(item.get("size", item.get("qty", item.get("quantity", item.get("s", 0.0)))))
                orders = int(item.get("orders", item.get("order_count", item.get("count", 1))))
            except (ValueError, TypeError):
                continue
        elif hasattr(item, "price") and hasattr(item, "size"):
            try:
                price = float(getattr(item, "price"))
                size = float(getattr(item, "size"))
                orders = int(getattr(item, "order_count", 1))
            except (ValueError, TypeError):
                continue

        if price > 0.0 and size > 0.0:
            parsed.append((price, size, max(1, orders)))

    if not parsed:
        return []

    is_buy = side.lower() in ("buy", "bid")
    parsed.sort(key=lambda x: x[0], reverse=is_buy)

    return [LOBLevel(price=p, size=s, order_count=o) for p, s, o in parsed]


def evaluate_optimal_queue_level(
    bids: Union[Sequence[Tuple[float, float]], Sequence[Dict[str, Any]], Sequence[Sequence[float]], np.ndarray],
    asks: Union[Sequence[Tuple[float, float]], Sequence[Dict[str, Any]], Sequence[Sequence[float]], np.ndarray],
    target_size: float,
    urgency: Union[str, UrgencyLevel] = "normal",
    *,
    side: str = "buy",
    tick_size: float = DEFAULT_TICK_SIZE,
    volatility: float = DEFAULT_VOLATILITY,
    time_horizon_sec: float = DEFAULT_TIME_HORIZON_SEC,
    market_order_rate: float = DEFAULT_MARKET_ORDER_RATE,
    cancel_rate: float = DEFAULT_CANCEL_RATE,
) -> OptimalPlacementResult:
    """
    Evaluates optimal limit order placement across LOB queue levels (Level 1 vs Level 2 vs Level 3).

    Balancing Trade-offs:
    ---------------------
    - **Level 1 (Touch / Inside Spread)**:
        - Fill Rate: High (approx 80-95%), Fast execution / Low latency.
        - Adverse Selection: Higher (informed market orders cross the touch).
        - Spread Capture: Base half-spread approx spread / 2.
    - **Level 2 / Level 3 (Deeper in Book)**:
        - Fill Rate: Lower, higher queueing delay.
        - Adverse Selection: Lower per share vs mid.
        - Spread Capture: Higher (captures extra spread / price improvement ticks).
        - Queue Hazard: Time-decay & risk of market moving away without execution.

    Parameters:
    -----------
    bids : Bid levels as list of (price, size) tuples or dicts.
    asks : Ask levels as list of (price, size) tuples or dicts.
    target_size : Desired quantity to buy or sell.
    urgency : 'passive' | 'normal' | 'aggressive' | 'immediate'.
    side : 'buy' | 'sell'.
    tick_size : Minimum price increment (default $0.01).
    volatility : Annualized asset volatility (default 0.25).
    time_horizon_sec : Maximum execution wait window (default 60.0s).
    market_order_rate : Poisson market order arrival rate (orders/sec).
    cancel_rate : Queue cancellation rate per unit depth.

    Returns:
    --------
    OptimalPlacementResult with recommended level, price, queue score, latency, and full candidate metrics.
    """
    order_side = side.lower()
    if order_side not in ("buy", "sell", "bid", "ask"):
        order_side = "buy"
    canonical_side = "buy" if order_side in ("buy", "bid") else "sell"

    urgency_str = urgency.value if isinstance(urgency, UrgencyLevel) else str(urgency).lower()
    if urgency_str not in ("passive", "normal", "aggressive", "immediate"):
        urgency_str = "normal"

    norm_bids = _normalize_lob_levels(bids, side="buy")
    norm_asks = _normalize_lob_levels(asks, side="sell")

    if not norm_bids or not norm_asks or target_size <= 0:
        return OptimalPlacementResult(
            valid=False,
            side=canonical_side,
            target_size=max(0.0, float(target_size)),
            urgency=urgency_str,
            recommended_level=1,
            recommended_price=0.0,
            queue_position_score=0.0,
            expected_fill_latency_sec=0.0,
            expected_fill_probability=0.0,
            expected_spread_capture=0.0,
            expected_adverse_selection=0.0,
            time_decay_hazard=0.0,
            net_edge=0.0,
            mid_price=0.0,
            spread=0.0,
            candidates=[],
            rationale="Cannot evaluate placement on empty order book.",
            reason="Empty book or invalid target size",
        )

    best_bid = norm_bids[0].price
    best_ask = norm_asks[0].price
    mid_price = (best_bid + best_ask) / 2.0
    spread = max(tick_size, best_ask - best_bid)

    same_side_levels = norm_bids if canonical_side == "buy" else norm_asks

    urgency_weights = {
        "passive": 0.20,
        "normal": 0.80,
        "aggressive": 2.0,
        "immediate": 4.5,
    }
    decay_multiplier = urgency_weights.get(urgency_str, 0.80)

    vol_per_sec = (volatility / math.sqrt(252.0 * 6.5 * 3600.0)) if volatility > 0 else 0.0001
    expected_mkt_vol = market_order_rate * time_horizon_sec

    max_eval_levels = min(5, len(same_side_levels))
    candidates: List[QueuePlacementCandidate] = []
    cumulative_depth_prior = 0.0

    for i in range(max_eval_levels):
        lvl = same_side_levels[i]
        level_idx = i + 1
        price = lvl.price

        depth_ahead = cumulative_depth_prior + (lvl.size * 0.5)

        # First-passage reach probability: Probability that market orders consume all depth ahead
        if cumulative_depth_prior == 0.0:
            p_reach = 1.0
            latency_sec = (depth_ahead + 0.5 * target_size) / (market_order_rate + EPSILON)
        else:
            p_reach = math.exp(-1.5 * cumulative_depth_prior / max(1.0, expected_mkt_vol))
            time_reach = cumulative_depth_prior / (market_order_rate + EPSILON)
            time_drain = (lvl.size * 0.5 + 0.5 * target_size) / (market_order_rate + cancel_rate * lvl.size * 0.5 + EPSILON)
            latency_sec = time_reach + time_drain

        cumulative_depth_prior += lvl.size

        # Queue fill probability given reach
        p_drain = calculate_cont_stoikov_fill_probability(
            queue_position=depth_ahead,
            depth_at_price=lvl.size,
            target_size=target_size,
            lambda_market=market_order_rate,
            mu_cancel=cancel_rate,
            time_horizon=time_horizon_sec,
        )
        fill_prob = max(0.01, min(0.99, p_reach * p_drain))

        # Spread capture ($/share): difference between mid and limit price
        if canonical_side == "buy":
            spread_capture = mid_price - price
        else:
            spread_capture = price - mid_price

        # Adverse selection cost
        level_adv_factor = 1.30 if level_idx == 1 else (0.80 if level_idx == 2 else 0.50)
        size_impact_ratio = math.sqrt(target_size / (depth_ahead + target_size + EPSILON))
        effective_latency_cap = min(latency_sec, time_horizon_sec)
        adverse_selection = level_adv_factor * vol_per_sec * mid_price * math.sqrt(effective_latency_cap) * size_impact_ratio
        adverse_selection = min(adverse_selection, spread * 1.5)

        # Time-decay & Non-execution hazard
        unfilled_prob = max(0.01, 1.0 - fill_prob)
        latency_ratio = latency_sec / max(1.0, time_horizon_sec)
        time_decay_hazard = decay_multiplier * (spread * unfilled_prob + (0.5 * spread * (latency_ratio ** 1.2)))

        # Net expected edge
        net_expected_edge = (fill_prob * (spread_capture - adverse_selection)) - time_decay_hazard

        # Normalized queue position score (0.0 to 1.0 via logistic transform)
        score_input = (net_expected_edge / (spread + EPSILON)) * 2.0
        queue_score = 1.0 / (1.0 + math.exp(-max(-10.0, min(10.0, score_input))))

        candidates.append(
            QueuePlacementCandidate(
                level_index=level_idx,
                price=round(price, 4),
                side=canonical_side,
                depth_ahead=round(depth_ahead, 2),
                fill_probability=round(fill_prob, 4),
                expected_fill_latency_sec=round(latency_sec, 2),
                spread_capture=round(spread_capture, 4),
                adverse_selection_cost=round(adverse_selection, 4),
                time_decay_hazard=round(time_decay_hazard, 4),
                net_expected_edge=round(net_expected_edge, 4),
                queue_position_score=round(queue_score, 4),
            )
        )

    best_candidate = max(candidates, key=lambda c: (c.net_expected_edge, c.fill_probability))

    lvl_name = f"Level {best_candidate.level_index}"
    if best_candidate.level_index == 1:
        rationale = (
            f"Recommended {lvl_name} @ ${best_candidate.price:.2f} (Touch). "
            f"High fill probability ({best_candidate.fill_probability * 100:.1f}%) and fast latency "
            f"({best_candidate.expected_fill_latency_sec:.1f}s) outweighs adverse selection for {urgency_str} urgency."
        )
    elif best_candidate.level_index == 2:
        rationale = (
            f"Recommended {lvl_name} @ ${best_candidate.price:.2f} (1-tick deep). "
            f"Captures enhanced spread (${best_candidate.spread_capture:.3f}/sh) with acceptable fill probability "
            f"({best_candidate.fill_probability * 100:.1f}%) and queue position score {best_candidate.queue_position_score:.2f}."
        )
    else:
        rationale = (
            f"Recommended {lvl_name} @ ${best_candidate.price:.2f} (Deep book). "
            f"Maximizes spread capture (${best_candidate.spread_capture:.3f}/sh) under passive execution profile "
            f"with low time-decay penalty."
        )

    return OptimalPlacementResult(
        valid=True,
        side=canonical_side,
        target_size=target_size,
        urgency=urgency_str,
        recommended_level=best_candidate.level_index,
        recommended_price=best_candidate.price,
        queue_position_score=best_candidate.queue_position_score,
        expected_fill_latency_sec=best_candidate.expected_fill_latency_sec,
        expected_fill_probability=best_candidate.fill_probability,
        expected_spread_capture=best_candidate.spread_capture,
        expected_adverse_selection=best_candidate.adverse_selection_cost,
        time_decay_hazard=best_candidate.time_decay_hazard,
        net_edge=best_candidate.net_expected_edge,
        mid_price=round(mid_price, 4),
        spread=round(spread, 4),
        candidates=[c.to_dict() for c in candidates],
        rationale=rationale,
    )


def slice_liquidity_order(
    target_size: float,
    bids: Union[Sequence[Tuple[float, float]], Sequence[Dict[str, Any]], Sequence[Sequence[float]], np.ndarray],
    asks: Union[Sequence[Tuple[float, float]], Sequence[Dict[str, Any]], Sequence[Sequence[float]], np.ndarray],
    *,
    side: str = "buy",
    max_participation_pct: float = 0.15,
    max_slice_depth_pct: float = 0.30,
    min_slice_size: float = 1.0,
    urgency: str = "normal",
) -> LiquiditySliceResult:
    """
    Slices a parent order into optimal child limit orders across LOB levels and time schedules
    to minimize market impact and queue queueing risk.
    """
    order_side = side.lower()
    canonical_side = "buy" if order_side in ("buy", "bid") else "sell"
    norm_bids = _normalize_lob_levels(bids, side="buy")
    norm_asks = _normalize_lob_levels(asks, side="sell")

    target = max(0.0, float(target_size))
    if target <= 0.0 or (not norm_bids and not norm_asks):
        return LiquiditySliceResult(
            valid=False,
            total_target_size=target,
            total_sliced_size=0.0,
            num_slices=0,
            side=canonical_side,
            estimated_duration_sec=0.0,
            average_target_price=0.0,
            estimated_market_impact=0.0,
            slices=[],
            reason="Invalid target size or empty order book",
        )

    same_side_levels = norm_bids if canonical_side == "buy" else norm_asks
    if not same_side_levels:
        return LiquiditySliceResult(
            valid=False,
            total_target_size=target,
            total_sliced_size=0.0,
            num_slices=0,
            side=canonical_side,
            estimated_duration_sec=0.0,
            average_target_price=0.0,
            estimated_market_impact=0.0,
            slices=[],
            reason="No quotes available for order side",
        )

    level_1_depth = same_side_levels[0].size
    max_slice_cap = max(min_slice_size, level_1_depth * max_slice_depth_pct)

    if urgency == "immediate":
        num_slices = max(1, min(3, int(math.ceil(target / (max_slice_cap * 2.0)))))
    elif urgency == "aggressive":
        num_slices = max(1, min(5, int(math.ceil(target / (max_slice_cap * 1.5)))))
    elif urgency == "passive":
        num_slices = max(2, min(10, int(math.ceil(target / (max_slice_cap * 0.75)))))
    else:
        num_slices = max(1, min(8, int(math.ceil(target / max_slice_cap))))

    base_slice_size = math.floor(target / num_slices)
    remainder = target - (base_slice_size * num_slices)

    slices: List[LiquiditySlice] = []
    current_delay = 0.0
    total_cost = 0.0

    for i in range(num_slices):
        slice_idx = i + 1
        s_size = base_slice_size + (remainder if i == 0 else 0.0)
        if s_size <= 0:
            continue

        if urgency in ("aggressive", "immediate") or i == 0:
            lvl_idx = 1
        else:
            lvl_idx = 2 if len(same_side_levels) > 1 and i % 2 == 1 else 1

        selected_lvl = same_side_levels[min(lvl_idx - 1, len(same_side_levels) - 1)]
        target_p = selected_lvl.price

        latency = calculate_expected_fill_latency(
            queue_position=selected_lvl.size * 0.5,
            target_size=s_size,
        )

        part_rate = min(1.0, s_size / (selected_lvl.size + s_size))

        slices.append(
            LiquiditySlice(
                slice_index=slice_idx,
                size=round(s_size, 2),
                target_price=round(target_p, 4),
                level_index=lvl_idx,
                delay_sec=round(current_delay, 1),
                expected_latency_sec=round(latency, 2),
                participation_rate=round(part_rate, 4),
                side=canonical_side,
            )
        )

        total_cost += s_size * target_p
        current_delay += max(5.0, latency * 0.8)

    total_sliced = sum(s.size for s in slices)
    avg_price = total_cost / total_sliced if total_sliced > 0 else 0.0
    est_duration = slices[-1].delay_sec + slices[-1].expected_latency_sec if slices else 0.0
    est_impact = 0.001 * math.sqrt(total_sliced / (level_1_depth + 1.0)) * avg_price

    return LiquiditySliceResult(
        valid=True,
        total_target_size=target,
        total_sliced_size=round(total_sliced, 2),
        num_slices=len(slices),
        side=canonical_side,
        estimated_duration_sec=round(est_duration, 2),
        average_target_price=round(avg_price, 4),
        estimated_market_impact=round(est_impact, 4),
        slices=[s.to_dict() for s in slices],
    )


def simulate_queue_fill(
    symbol: str,
    price_level: float,
    order_size: float,
    depth_ahead: float,
    lambda_limit: Optional[float] = None,
    mu_cancel: Optional[float] = None,
    theta_market: Optional[float] = None,
    time_horizon_sec: Optional[float] = 60.0,
    num_simulations: Optional[int] = 500,
    random_seed: Optional[int] = 42,
) -> Dict[str, Any]:
    """Top-level resolver for POST /pilots/options/lob/simulate-queue.

    Simulates Level-3 Limit Order Book (LOB) queue fill dynamics, expected wait time,
    and queue progression percentiles under the Cont-Stoikov-Talreja (2010) Markovian framework.
    """
    clean_sym = str(symbol or "SPY").strip().upper()
    p_lvl = float(price_level if price_level is not None else 100.0)
    s_size = float(order_size if order_size is not None else 1.0)
    d_ahead = float(depth_ahead if depth_ahead is not None else 0.0)

    sim_res = simulate_queue_position(
        price_level=p_lvl,
        order_size=s_size,
        queue_ahead=d_ahead,
        lambda_limit=float(lambda_limit) if lambda_limit is not None else 4.0,
        mu_cancel=float(mu_cancel) if mu_cancel is not None else 0.05,
        theta_market=float(theta_market) if theta_market is not None else 5.0,
        time_horizon_sec=float(time_horizon_sec) if time_horizon_sec is not None else 60.0,
        num_simulations=int(num_simulations) if num_simulations is not None else 500,
        random_seed=random_seed,
    )

    res_dict = sim_res.to_dict()
    res_dict["symbol"] = clean_sym
    res_dict["price_level"] = p_lvl
    res_dict["order_size"] = s_size
    res_dict["depth_ahead"] = d_ahead
    res_dict["fill_probability"] = sim_res.fill_probability
    res_dict["expected_wait_time"] = sim_res.expected_fill_time_sec
    res_dict["expected_wait_time_sec"] = sim_res.expected_fill_time_sec
    res_dict["expected_fill_time_sec"] = sim_res.expected_fill_time_sec
    res_dict["median_fill_time_sec"] = sim_res.median_fill_time_sec
    res_dict["queue_progression_percentiles"] = sim_res.percentiles_fill_time
    res_dict["progression_percentiles"] = sim_res.percentiles_fill_time
    res_dict["timestamp"] = datetime.now(timezone.utc).isoformat()
    return res_dict

