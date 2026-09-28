"""
pilots/options_vpin.py — Volume-Synchronized Probability of Toxicity (VPIN) Engine.
===================================================================================

Quantitative market microstructure engine calculating VPIN (Volume-Synchronized Probability of
Informed Trading / Toxicity) for options contracts and chains.

Mathematical Formulation (Easley, López de Prado, O'Hara - 2011/2012):
----------------------------------------------------------------------
1. Option trade stream is synchronized into equal-volume buckets of size $V$:
   $V = \\frac{\\text{Total Volume}}{N}$ (or specified bucket size).

2. Trades are decomposed into Buy and Sell volumes via Bulk Volume Classification (BVC):
   $$\\Delta P_\\tau = P_\\tau - P_{\\tau-1}$$
   $$\\sigma_{\\Delta P} = \\text{std}(\\Delta P)$$
   $$V_\\tau^B = V_\\tau \\cdot \\Phi\\left(\\frac{\\Delta P_\\tau}{\\sigma_{\\Delta P}}\\right)$$
   $$V_\\tau^S = V_\\tau - V_\\tau^B = V_\\tau \\cdot \\left[1 - \\Phi\\left(\\frac{\\Delta P_\\tau}{\\sigma_{\\Delta P}}\\right)\\right]$$
   where $\\Phi(\\cdot)$ is the standard normal cumulative distribution function (CDF).

3. For each volume bucket $k$ containing volume $V$:
   $$V_k^B = \\sum_{\\tau \\in k} V_\\tau^B, \\quad V_k^S = \\sum_{\\tau \\in k} V_\\tau^S$$
   $$\\text{Order Imbalance}_k = |V_k^B - V_k^S|$$

4. Rolling VPIN metric over a window of $N$ volume buckets (default $N = 50$):
   $$VPIN_t = \\frac{\\sum_{k=t-N+1}^t |V_k^B - V_k^S|}{N \\cdot V} \\in [0.0, 1.0]$$

Toxicity Regimes:
-----------------
- **LOW** ($VPIN < 0.20$): Order flow dominated by uninformed liquidity/noise traders. Safe for market making and passive liquidity posting.
- **MODERATE** ($0.20 \\le VPIN \\le 0.35$): Normal market conditions with balanced order flow.
- **HIGH_TOXICITY** ($VPIN > 0.35$): Informed institutional traders or toxic directional flow detected. Triggers defensive spread widening, limit concessions, and adverse selection shields.

Design Invariants:
------------------
* **AST-Safe (CONSTRAINT #1 & #3)** — Pure computation module. Never imports heavy engines
  (`processing_engine`, `technical_options_engine`, `strategy_engine`, `macro_engine`, etc.).
  Only standard library, `numpy`, `scipy` (with pure math fallback), and `pandas`. `data.market_data`
  (a data-layer module, not a heavy engine) is imported lazily, inside
  `fetch_real_underlying_bar_trades()` only, to fetch real bars for the live endpoint.
* **Honesty (CONSTRAINT #4)** — No fabricated prices or volume. Missing or empty trades return
  clean sentinel 0.0 values without fabricating data.
* **Never Raises (CONSTRAINT #6)** — Degrades gracefully on empty DataFrames, zero volume, zero variance,
  or malformed records.

Live-endpoint data source (fixed 2026-08-22 — see `docs/known_issues/options_vpin_fabricated_live_data.md`):
--------------------------------------------------------------------------------------------------------------
`get_options_vpin_metrics()` (the function backing `GET /pilots/options/vpin/metrics`) used to call
`generate_synthetic_option_trades()` UNCONDITIONALLY — a random-walk generator whose own docstring says
"for testing and simulation" — so the live, non-mock VPIN endpoint always returned a number computed from
fabricated data, deterministic per-symbol, with no indication anywhere that it wasn't real. There is no
real per-trade options tick stream anywhere in this codebase today (`pilots/unusual_options_flow.py` only
has point-in-time chain snapshots, and no configured market-data provider exposes one — confirmed against
`docs/FMP_INTEGRATION.md` and `data/fmp_client.py`), so a genuinely tick-resolution VPIN is not available.

Per CONSTRAINT #4, `get_options_vpin_metrics()` now fetches REAL hourly OHLCV bars for the underlying via
`fetch_real_underlying_bar_trades()` and computes a coarse, BAR-LEVEL Bulk Volume Classification
approximation from them — the same honest pattern already used by
`desktop/daemon_runtime.py::OrchestratorDaemon.maybe_update_circuit_breaker` (see that method's own
docstring for the identical honest-scope writeup this mirrors). When real bars cannot be fetched (provider
failure, empty/malformed payload, or too little history), the function degrades to an explicit
`data_available: False` / `vpin: None` response instead of silently substituting synthetic data — a
missing measurement, not a fabricated one. `generate_synthetic_option_trades()` itself is unchanged and
remains legitimately used by `tests/test_options_vpin.py` for pure-math unit tests of the BVC/bucketing
logic; the fix is that the LIVE endpoint no longer reaches it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from settings import settings

logger = logging.getLogger(__name__)

# Default Constants
DEFAULT_NUM_BUCKETS = 50
# Operator-configurable via settings.OPTIONS_VPIN_TOXICITY_THRESHOLD (default 0.35,
# matching the Easley/Lopez de Prado/O'Hara literature's toxic-flow threshold). This
# module-level constant is kept as the hardcoded fallback/default value only; call
# sites read the live settings value (see evaluate_toxicity_regime/is_toxic_flow/
# apply_defensive_spread_concession) so an operator override actually takes effect.
DEFAULT_TOXICITY_THRESHOLD = 0.35
MODERATE_TOXICITY_THRESHOLD = 0.20
EPSILON = 1e-12


def _norm_cdf(x: Union[float, np.ndarray]) -> Union[float, np.ndarray]:
    """Vectorized standard normal CDF $\\Phi(x) = \\frac{1}{2}\\left[1 + \\text{erf}\\left(\\frac{x}{\\sqrt{2}}\\right)\\right]$.

    Uses `math.erf` or `scipy.special.ndtr` with numpy fallback to guarantee 100% precision
    without requiring external C libraries.
    """
    try:
        from scipy.special import ndtr
        return ndtr(x)
    except Exception:
        pass

    # Pure Python / Numpy erf implementation
    if isinstance(x, (np.ndarray, pd.Series)):
        # Numpy vectorized erf via vectorize or approximate formulation
        try:
            from scipy.special import erf
            return 0.5 * (1.0 + erf(x / np.sqrt(2.0)))
        except Exception:
            vec_erf = np.vectorize(lambda v: 0.5 * (1.0 + math.erf(float(v) / math.sqrt(2.0))))
            return vec_erf(x)
    else:
        try:
            return 0.5 * (1.0 + math.erf(float(x) / math.sqrt(2.0)))
        except Exception:
            return 0.5


@dataclass
class VPINBucket:
    """Represents a single volume-synchronized bucket of size $V$."""

    bucket_index: int
    volume: float
    buy_volume: float
    sell_volume: float
    order_imbalance: float
    vwap: float
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    trade_count: int = 0
    price_change: float = 0.0
    # Real first/last trade price observed within this bucket (not fabricated -- these are
    # already computed internally by compute_vpin_buckets to derive `price_change`, just not
    # previously surfaced on the dataclass).
    price_start: float = 0.0
    price_end: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bucket_index": self.bucket_index,
            "volume": round(float(self.volume), 4),
            "buy_volume": round(float(self.buy_volume), 4),
            "sell_volume": round(float(self.sell_volume), 4),
            "order_imbalance": round(float(self.order_imbalance), 4),
            "vwap": round(float(self.vwap), 4),
            "start_time": self.start_time,
            "end_time": self.end_time,
            "trade_count": self.trade_count,
            "price_change": round(float(self.price_change), 4),
            "price_start": round(float(self.price_start), 4),
            "price_end": round(float(self.price_end), 4),
        }


@dataclass
class VPINResult:
    """Comprehensive result of VPIN toxicity calculation."""

    vpin: Optional[float]
    rolling_vpin: List[float]
    total_trade_count: int
    total_volume: float
    bucket_size: float
    num_buckets: int
    total_buckets: int
    buckets: List[VPINBucket]
    mean_imbalance: float
    toxicity_regime: str
    is_toxic: bool
    symbol: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vpin": round(float(self.vpin), 4) if self.vpin is not None else None,
            "rolling_vpin": [round(float(v), 4) for v in self.rolling_vpin],
            "total_trade_count": self.total_trade_count,
            "total_volume": round(float(self.total_volume), 4),
            "bucket_size": round(float(self.bucket_size), 4),
            "num_buckets": self.num_buckets,
            "total_buckets": self.total_buckets,
            "mean_imbalance": round(float(self.mean_imbalance), 4),
            "toxicity_regime": self.toxicity_regime,
            "is_toxic": self.is_toxic,
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "buckets": [b.to_dict() for b in self.buckets],
        }


def _normalize_trades_df(
    trades: Union[pd.DataFrame, Sequence[Dict[str, Any]], Sequence[Any]]
) -> pd.DataFrame:
    """Normalizes various trade stream inputs into a clean DataFrame with ['price', 'volume', 'time']."""
    if trades is None:
        return pd.DataFrame(columns=["price", "volume", "time"])

    if isinstance(trades, pd.DataFrame):
        df = trades.copy()
    elif isinstance(trades, (list, tuple)):
        if not trades:
            return pd.DataFrame(columns=["price", "volume", "time"])
        if isinstance(trades[0], dict):
            df = pd.DataFrame(trades)
        else:
            # Attempt dataclass or object inspection
            records = []
            for item in trades:
                if hasattr(item, "__dict__"):
                    records.append(item.__dict__)
                elif hasattr(item, "_asdict"):
                    records.append(item._asdict())
                else:
                    try:
                        records.append(dict(item))
                    except Exception:
                        pass
            df = pd.DataFrame(records) if records else pd.DataFrame()
    else:
        return pd.DataFrame(columns=["price", "volume", "time"])

    if df.empty:
        return pd.DataFrame(columns=["price", "volume", "time"])

    # Resolve price column
    price_col = None
    for cand in ["price", "trade_price", "last_price", "last", "p", "close"]:
        if cand in df.columns:
            price_col = cand
            break

    # Resolve volume column
    vol_col = None
    for cand in ["volume", "trade_size", "size", "qty", "contracts", "v", "shares"]:
        if cand in df.columns:
            vol_col = cand
            break

    # Resolve time column
    time_col = None
    for cand in ["time", "timestamp", "datetime", "date", "t", "trade_time", "created_at"]:
        if cand in df.columns:
            time_col = cand
            break

    if price_col is None or vol_col is None:
        logger.debug("Trades input missing price or volume column: %s", df.columns.tolist())
        return pd.DataFrame(columns=["price", "volume", "time"])

    result_df = pd.DataFrame()
    result_df["price"] = pd.to_numeric(df[price_col], errors="coerce").fillna(0.0)
    result_df["volume"] = pd.to_numeric(df[vol_col], errors="coerce").fillna(0.0)

    if time_col is not None:
        result_df["time"] = df[time_col].astype(str)
    else:
        result_df["time"] = [None] * len(df)

    # Filter out invalid rows (zero or negative price, non-positive volume)
    valid_mask = (result_df["price"] > 0.0) & (result_df["volume"] > 0.0)
    return result_df[valid_mask].reset_index(drop=True)


def compute_vpin_buckets(
    trades_df: Union[pd.DataFrame, Sequence[Dict[str, Any]], Sequence[Any]],
    bucket_size: Optional[float] = None,
    num_buckets: int = DEFAULT_NUM_BUCKETS,
) -> List[VPINBucket]:
    """Groups option trades into equal-volume buckets of size $V$ using Bulk Volume Classification (BVC).

    Parameters:
    -----------
    trades_df: pd.DataFrame or Sequence[dict]
        Trade stream with price and volume fields.
    bucket_size: Optional[float]
        Volume $V$ per bucket. If None or <= 0, defaults to total_volume / num_buckets.
    num_buckets: int
        Number of buckets in rolling window (default 50).

    Returns:
    --------
    List[VPINBucket]: Complete volume-synchronized buckets.
    """
    df = _normalize_trades_df(trades_df)
    if df.empty or len(df) < 1:
        return []

    total_vol = float(df["volume"].sum())
    if total_vol <= 0.0:
        return []

    # Determine volume per bucket V. Guard against a degenerate near-zero (but not
    # exactly 0.0) explicit bucket_size using the repo's <1e-12 convention -- a
    # tiny positive value would otherwise make the fill loop below iterate roughly
    # total_vol / bucket_size times per trade (effectively hanging) and would also
    # blow up the imbalance/denom division in the rolling VPIN computation.
    if bucket_size is None or bucket_size <= EPSILON:
        v_target = max(1.0, total_vol / float(max(1, num_buckets)))
    else:
        v_target = float(bucket_size)

    prices = df["price"].to_numpy(dtype=np.float64)
    volumes = df["volume"].to_numpy(dtype=np.float64)
    times = df["time"].tolist()
    n_trades = len(prices)

    # 1. Compute price changes Delta P and sample standard deviation sigma_{Delta P}
    delta_p = np.zeros(n_trades, dtype=np.float64)
    if n_trades > 1:
        delta_p[1:] = np.diff(prices)

    sigma_dp = float(np.std(delta_p))

    # 2. Bulk Volume Classification (BVC) Buy/Sell fractions
    if sigma_dp <= EPSILON or np.isnan(sigma_dp):
        # Zero variance: trades split equally 50% Buy / 50% Sell
        p_buy = np.full(n_trades, 0.5, dtype=np.float64)
    else:
        z_scores = delta_p / sigma_dp
        p_buy = _norm_cdf(z_scores)
        if isinstance(p_buy, (list, tuple)):
            p_buy = np.array(p_buy, dtype=np.float64)
        elif not isinstance(p_buy, np.ndarray):
            p_buy = np.full(n_trades, float(p_buy), dtype=np.float64)

    # Clamp buy fraction in [0.0, 1.0]
    p_buy = np.clip(p_buy, 0.0, 1.0)
    p_sell = 1.0 - p_buy

    # 3. Synchronize trades into volume buckets of size V
    buckets: List[VPINBucket] = []

    current_bucket_idx = 0
    rem_bucket_capacity = v_target

    cur_vol = 0.0
    cur_buy_vol = 0.0
    cur_sell_vol = 0.0
    cur_dollar_vol = 0.0
    cur_trade_count = 0
    cur_start_time = times[0] if times else None
    cur_end_time = None
    bucket_first_price = prices[0]
    bucket_last_price = prices[0]

    for i in range(n_trades):
        trade_vol = volumes[i]
        trade_price = prices[i]
        trade_time = times[i]
        trade_p_buy = float(p_buy[i])
        trade_p_sell = float(p_sell[i])

        trade_rem = trade_vol

        while trade_rem > 0:
            if cur_trade_count == 0:
                bucket_first_price = trade_price
                cur_start_time = trade_time

            if trade_rem < rem_bucket_capacity - EPSILON:
                # Trade fits entirely in current bucket
                cur_vol += trade_rem
                cur_buy_vol += trade_rem * trade_p_buy
                cur_sell_vol += trade_rem * trade_p_sell
                cur_dollar_vol += trade_rem * trade_price
                cur_trade_count += 1
                bucket_last_price = trade_price
                cur_end_time = trade_time
                rem_bucket_capacity -= trade_rem
                trade_rem = 0.0
            else:
                # Fill remaining space in current bucket and close bucket
                fill_slice = rem_bucket_capacity
                cur_vol += fill_slice
                cur_buy_vol += fill_slice * trade_p_buy
                cur_sell_vol += fill_slice * trade_p_sell
                cur_dollar_vol += fill_slice * trade_price
                cur_trade_count += 1
                bucket_last_price = trade_price
                cur_end_time = trade_time

                # Create full bucket
                vwap = cur_dollar_vol / cur_vol if cur_vol > 0 else bucket_last_price
                imbalance = abs(cur_buy_vol - cur_sell_vol)
                price_change = bucket_last_price - bucket_first_price

                bucket = VPINBucket(
                    bucket_index=current_bucket_idx,
                    volume=cur_vol,
                    buy_volume=cur_buy_vol,
                    sell_volume=cur_sell_vol,
                    order_imbalance=imbalance,
                    vwap=vwap,
                    start_time=cur_start_time,
                    end_time=cur_end_time,
                    trade_count=cur_trade_count,
                    price_change=price_change,
                    price_start=bucket_first_price,
                    price_end=bucket_last_price,
                )
                buckets.append(bucket)

                # Advance to next bucket
                trade_rem -= fill_slice
                current_bucket_idx += 1
                rem_bucket_capacity = v_target
                cur_vol = 0.0
                cur_buy_vol = 0.0
                cur_sell_vol = 0.0
                cur_dollar_vol = 0.0
                cur_trade_count = 0
                cur_start_time = trade_time
                cur_end_time = None

    return buckets


def calculate_vpin(
    trades_df: Union[pd.DataFrame, Sequence[Dict[str, Any]], Sequence[Any]],
    bucket_size: Optional[float] = None,
    num_buckets: int = DEFAULT_NUM_BUCKETS,
    symbol: Optional[str] = None,
) -> VPINResult:
    """Calculates Volume-Synchronized Probability of Toxicity (VPIN) and rolling series.

    Parameters:
    -----------
    trades_df: pd.DataFrame or Sequence[dict]
        Trade records containing price and volume.
    bucket_size: Optional[float]
        Volume per bucket $V$.
    num_buckets: int
        Number of buckets $N$ in rolling window (default 50).
    symbol: Optional[str]
        Underlying or contract symbol for reporting context.

    Returns:
    --------
    VPINResult: Comprehensive toxicity metrics, rolling series, and bucket history.
    """
    df = _normalize_trades_df(trades_df)
    if df.empty:
        return VPINResult(
            vpin=None,
            rolling_vpin=[],
            total_trade_count=0,
            total_volume=0.0,
            bucket_size=0.0,
            num_buckets=num_buckets,
            total_buckets=0,
            buckets=[],
            mean_imbalance=0.0,
            toxicity_regime="LOW",
            is_toxic=False,
            symbol=symbol,
        )

    total_trade_count = len(df)
    total_volume = float(df["volume"].sum())

    if bucket_size is None or bucket_size <= EPSILON:
        v_target = max(1.0, total_volume / float(max(1, num_buckets)))
    else:
        v_target = float(bucket_size)

    buckets = compute_vpin_buckets(df, bucket_size=v_target, num_buckets=num_buckets)
    total_buckets = len(buckets)

    if total_buckets == 0:
        return VPINResult(
            vpin=None,
            rolling_vpin=[],
            total_trade_count=total_trade_count,
            total_volume=total_volume,
            bucket_size=v_target,
            num_buckets=num_buckets,
            total_buckets=0,
            buckets=[],
            mean_imbalance=0.0,
            toxicity_regime="LOW",
            is_toxic=False,
            symbol=symbol,
        )

    # Compute rolling VPIN series
    # VPIN_t = sum(|V^B - V^S|) / (N * V)
    rolling_vpin: List[float] = []
    imbalances = [b.order_imbalance for b in buckets]
    mean_imbalance = float(np.mean(imbalances)) if imbalances else 0.0

    for i in range(total_buckets):
        if i < num_buckets - 1:
            # Expanding window until we have N buckets
            window = imbalances[: i + 1]
            denom = len(window) * v_target
        else:
            # Fixed rolling N-bucket window
            window = imbalances[i - num_buckets + 1 : i + 1]
            denom = num_buckets * v_target

        sum_imbalance = sum(window)
        # Degenerate-denominator guard per repo convention: < 1e-12, never ==0/>0.
        val = sum_imbalance / denom if denom >= EPSILON else 0.0
        rolling_vpin.append(float(np.clip(val, 0.0, 1.0)))

    current_vpin = rolling_vpin[-1] if rolling_vpin else 0.0
    regime = evaluate_toxicity_regime(current_vpin)
    is_toxic = is_toxic_flow(current_vpin)

    return VPINResult(
        vpin=current_vpin,
        rolling_vpin=rolling_vpin,
        total_trade_count=total_trade_count,
        total_volume=total_volume,
        bucket_size=v_target,
        num_buckets=num_buckets,
        total_buckets=total_buckets,
        buckets=buckets,
        mean_imbalance=mean_imbalance,
        toxicity_regime=regime,
        is_toxic=is_toxic,
        symbol=symbol,
    )


def evaluate_toxicity_regime(vpin: float) -> str:
    """Classifies VPIN metric into discrete operational toxicity regimes.

    - LOW: VPIN < 0.20
    - MODERATE: 0.20 <= VPIN <= settings.OPTIONS_VPIN_TOXICITY_THRESHOLD (default 0.35)
    - HIGH_TOXICITY: VPIN > settings.OPTIONS_VPIN_TOXICITY_THRESHOLD (default 0.35)
    """
    toxicity_threshold = float(
        getattr(settings, "OPTIONS_VPIN_TOXICITY_THRESHOLD", DEFAULT_TOXICITY_THRESHOLD)
    )
    if vpin < MODERATE_TOXICITY_THRESHOLD:
        return "LOW"
    elif vpin <= toxicity_threshold:
        return "MODERATE"
    else:
        return "HIGH_TOXICITY"


def is_toxic_flow(vpin: float, threshold: Optional[float] = None) -> bool:
    """Returns True if VPIN exceeds the toxicity gating threshold.

    `threshold` defaults to `settings.OPTIONS_VPIN_TOXICITY_THRESHOLD` (0.35) when
    not explicitly provided by the caller, so an operator override actually applies.
    """
    if threshold is None:
        threshold = float(
            getattr(settings, "OPTIONS_VPIN_TOXICITY_THRESHOLD", DEFAULT_TOXICITY_THRESHOLD)
        )
    return float(vpin) > float(threshold)


def apply_defensive_spread_concession(
    base_spread: float,
    vpin: Optional[float],
    toxicity_threshold: Optional[float] = None,
    max_widening_mult: float = 2.0,
) -> float:
    """Applies defensive spread concession / widening when order flow is toxic ($VPIN > 0.35$).

    Parameters:
    -----------
    base_spread: float
        Original bid-ask spread or limit price concession ($).
    vpin: Optional[float]
        Current VPIN toxicity score $\\in [0.0, 1.0]$, or `None` when VPIN could
        not be computed (insufficient trade volume) -- returns `base_spread`
        unchanged rather than fabricating a widening decision from missing
        data (CONSTRAINT #4).
    toxicity_threshold: float
        Threshold above which spread widening activates (default 0.35).
    max_widening_mult: float
        Maximum multiplier on base spread at $VPIN = 1.0$ (default 2.0x).

    Returns:
    --------
    float: Defensively widened spread.
    """
    if base_spread <= 0.0:
        return 0.0

    if vpin is None:
        return base_spread

    if toxicity_threshold is None:
        toxicity_threshold = float(
            getattr(settings, "OPTIONS_VPIN_TOXICITY_THRESHOLD", DEFAULT_TOXICITY_THRESHOLD)
        )

    if vpin <= toxicity_threshold:
        return base_spread

    # Linear scaling between toxicity_threshold and 1.0
    excess_ratio = min(1.0, max(0.0, (vpin - toxicity_threshold) / (1.0 - toxicity_threshold)))
    multiplier = 1.0 + (max_widening_mult - 1.0) * excess_ratio
    return round(float(base_spread * multiplier), 4)


def generate_synthetic_option_trades(
    num_trades: int = 1000,
    initial_price: float = 5.0,
    volatility: float = 0.02,
    informed_fraction: float = 0.0,
    direction: float = 1.0,
    seed: Optional[int] = 42,
) -> pd.DataFrame:
    """Generates synthetic option trade stream for testing and simulation.

    Parameters:
    -----------
    num_trades: int
        Number of synthetic trades to generate.
    initial_price: float
        Starting option premium price.
    volatility: float
        Price step volatility.
    informed_fraction: float
        Fraction of trades that are informed directional orders ($0.0 = $ pure noise, $1.0 = $ 100% toxic).
    direction: float
        +1.0 for informed aggressive buying, -1.0 for informed aggressive selling.
    seed: Optional[int]
        Random seed for reproducibility.

    Returns:
    --------
    pd.DataFrame: Columns ['price', 'volume', 'time']
    """
    if seed is not None:
        np.random.seed(seed)

    prices = [initial_price]
    volumes = []
    times = []

    cur_p = initial_price
    base_time = 1700000000  # Unix timestamp

    for i in range(num_trades):
        is_informed = np.random.rand() < informed_fraction
        if is_informed:
            # Informed trade: pushes price in direction with larger volume
            price_step = abs(np.random.normal(0, volatility)) * direction
            size = float(np.random.randint(50, 200))
        else:
            # Uninformed noise trade: random walk with smaller volume
            price_step = np.random.normal(0, volatility)
            size = float(np.random.randint(1, 20))

        cur_p = max(0.05, cur_p + price_step)
        prices.append(cur_p)
        volumes.append(size)
        times.append(datetime.fromtimestamp(base_time + i * 2, tz=timezone.utc).isoformat())

    df = pd.DataFrame(
        {
            "price": prices[1:],
            "volume": volumes,
            "time": times,
        }
    )
    return df


def fetch_real_underlying_bar_trades(
    symbol: str,
    lookback_days: int = 10,
) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """Fetches real hourly OHLCV bars for `symbol` via the configured market-data provider
    (`data.market_data.get_provider()`) and reshapes them into a `['price', 'volume', 'time']`
    trade-stream proxy DataFrame -- the SAME coarse, bar-level Bulk Volume Classification (BVC)
    approximation already used by `desktop/daemon_runtime.py::maybe_update_circuit_breaker`
    (see that method's docstring for the full honest-scope writeup this mirrors).

    This is NOT a real per-trade options tick stream -- no configured provider (Alpaca/FMP/
    yfinance) exposes one anywhere in this codebase (CONSTRAINT #4: there is genuinely no better
    real signal available today). It IS real, non-fabricated market data, just at bar resolution
    rather than tick resolution.

    Returns `(df, None)` on success, where `df` has >= 2 rows and columns
    `['price', 'volume', 'time']`. Returns `(None, reason)` on any failure -- a provider
    import/construction error, a `MarketDataError`, an empty/malformed frame, a frame missing
    `Close`/`Volume`, or fewer than 2 usable rows after dropping NaNs. Never raises
    (CONSTRAINT #6) -- every failure mode is caught and reported via the `reason` string instead.
    """
    try:
        from data.market_data import get_provider

        provider = get_provider()
        bars = provider.get_intraday_bars(symbol, lookback_days=lookback_days, interval="1h")
    except Exception as exc:  # noqa: BLE001 - CONSTRAINT #6, dead-letter to the caller
        reason = f"market data fetch failed for {symbol}: {type(exc).__name__}: {exc}"
        logger.warning("fetch_real_underlying_bar_trades: %s", reason)
        return None, reason

    if bars is None or bars.empty:
        return None, f"no intraday bars returned for {symbol}"
    if "Close" not in bars.columns or "Volume" not in bars.columns:
        return None, f"intraday bars for {symbol} are missing Close/Volume columns"

    bars = bars.dropna(subset=["Close", "Volume"])
    if len(bars) < 2:
        return None, (
            f"insufficient intraday bar history for {symbol} "
            f"({len(bars)} usable rows, need >= 2)"
        )

    trades_df = pd.DataFrame(
        {
            "price": bars["Close"].to_numpy(dtype=np.float64),
            "volume": bars["Volume"].to_numpy(dtype=np.float64),
            "time": bars.index.astype(str),
        }
    )
    return trades_df, None


def _unavailable_vpin_metrics(symbol: str, num_buckets: int, reason: str) -> Dict[str, Any]:
    """Honest 'no measurement' response (CONSTRAINT #4) for `get_options_vpin_metrics()` when
    real underlying bars could not be fetched. `vpin`/`toxicity_regime`/`is_toxic`/
    `mean_imbalance`/`recommended_spread_concession` are all `None` -- never a fabricated
    plausible-looking value -- and `data_available=False`/`reason` explain why.
    """
    now = datetime.now(timezone.utc).isoformat()
    return {
        "vpin": None,
        "rolling_vpin": [],
        "total_trade_count": 0,
        "total_volume": 0.0,
        "bucket_size": 0.0,
        "num_buckets": num_buckets,
        "total_buckets": 0,
        "mean_imbalance": None,
        "toxicity_regime": None,
        "is_toxic": None,
        "symbol": symbol,
        "timestamp": now,
        "buckets": [],
        "bucket_history": [],
        "sample_time": now,
        "recommended_spread_concession": None,
        "data_available": False,
        "data_source": None,
        "reason": reason,
    }


def get_options_vpin_metrics(
    symbol: str,
    num_buckets: int = DEFAULT_NUM_BUCKETS,
    bucket_size: Optional[float] = None,
) -> Dict[str, Any]:
    """Top-level helper for GET /pilots/options/vpin/metrics?symbol=...
    Computes VPIN, toxicity regime, and volume bucket history from REAL market data -- a
    bar-level Bulk Volume Classification approximation (see
    `fetch_real_underlying_bar_trades()`'s docstring for the honest scope). Degrades to an
    explicit `data_available: False` / `vpin: None` response (never fabricated synthetic data)
    when real bars cannot be fetched -- see this module's docstring for the full history of why
    this changed.
    """
    clean_sym = str(symbol or "SPY").strip().upper()
    trades_df, fetch_error = fetch_real_underlying_bar_trades(clean_sym)

    if trades_df is None:
        return _unavailable_vpin_metrics(clean_sym, num_buckets, fetch_error or "unknown error")

    # Real bars are far sparser than a genuine tick stream -- DEFAULT_NUM_BUCKETS (50) assumes a
    # real trade stream, so the effective bucket count is sized down to fit the actual row count,
    # mirroring maybe_update_circuit_breaker's own `max(2, min(10, len(reactive_bars) // 2))`
    # precedent (floored at 2 so the rolling-VPIN window is never degenerate).
    effective_num_buckets = max(2, min(int(num_buckets), len(trades_df) // 2))

    result = calculate_vpin(
        trades_df=trades_df,
        bucket_size=bucket_size,
        num_buckets=effective_num_buckets,
        symbol=clean_sym,
    )
    if result.vpin is None:
        # calculate_vpin() hit its own empty-data/zero-bucket path even
        # though real bars were fetched (e.g. all-zero-volume bars for a
        # thinly-traded symbol) -- honor the same data_available=False
        # contract as the "bars couldn't be fetched at all" branch above
        # (CONSTRAINT #4) instead of reporting a fabricated success.
        return _unavailable_vpin_metrics(
            clean_sym, effective_num_buckets, "insufficient trade volume to compute VPIN"
        )
    res_dict = result.to_dict()
    res_dict["bucket_history"] = res_dict.get("buckets", [])
    res_dict["sample_time"] = res_dict.get("timestamp", datetime.now(timezone.utc).isoformat())
    concession = apply_defensive_spread_concession(0.05, result.vpin)
    res_dict["recommended_spread_concession"] = concession
    res_dict["data_available"] = True
    res_dict["data_source"] = "bar_level_bvc_approximation"
    res_dict["reason"] = None
    return res_dict


def get_options_vpin_metrics_for_frontend(
    symbol: str,
    num_buckets: int = DEFAULT_NUM_BUCKETS,
    bucket_size: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Adapts `get_options_vpin_metrics()`'s internal result into the shape
    `webapp/src/api/types.ts::VpinMetricsResponse` declares for the Pilots PWA's VpinGauge
    screen -- `get_options_vpin_metrics()`'s own return shape/keys are untouched by this
    function (`toxicity_regime`, `recommended_spread_concession`, per-bucket `volume`/
    `order_imbalance`, ...); this is the ONLY function `/pilots/options/vpin/metrics` should
    call.

    `toxicity_percentile` is never computed by this module (there is no historical VPIN
    distribution to rank against) and is honestly `None` (CONSTRAINT #4) rather than a
    fabricated median. `warning_message` is synthesized here from the real `toxicity_regime`
    the backend computed, since the frontend banner needs a message string, not a boolean.

    `data_available`/`data_source`/`reason` (CONSTRAINT #4, added 2026-08-22 -- see
    `docs/known_issues/options_vpin_fabricated_live_data.md`) surface whether `vpin` reflects a
    real bar-level BVC approximation (`data_available=True`, `data_source=
    "bar_level_bvc_approximation"`) or is honestly unavailable (`data_available=False`,
    `vpin=None`, `reason` explaining why real bars couldn't be fetched) -- never a fabricated
    number presented as a measurement either way. When unavailable, `warning_message` is
    overridden to state that plainly rather than describing a toxicity regime that was never
    actually computed.
    """
    result = get_options_vpin_metrics(symbol=symbol, num_buckets=num_buckets, bucket_size=bucket_size)

    sym = result.get("symbol") or symbol
    regime = result.get("toxicity_regime")
    vpin = result.get("vpin")
    data_available = bool(result.get("data_available"))

    warning_message = None
    if not data_available:
        warning_message = (
            f"VPIN is unavailable for {sym} -- {result.get('reason') or 'no real market data could be fetched'}. "
            "No toxicity measurement is being shown."
        )
    elif regime == "HIGH_TOXICITY" and vpin is not None:
        warning_message = (
            f"Elevated order-flow toxicity detected for {sym} (VPIN={vpin:.2f}) -- "
            "adverse selection risk is high; defensive spread widening is recommended."
        )

    buckets_out = [
        {
            "bucket_index": b.get("bucket_index"),
            "buy_volume": b.get("buy_volume"),
            "sell_volume": b.get("sell_volume"),
            "total_volume": b.get("volume"),
            "price_start": b.get("price_start"),
            "price_end": b.get("price_end"),
            "price_change": b.get("price_change"),
            "imbalance": b.get("order_imbalance"),
            "timestamp": b.get("end_time") or b.get("start_time"),
        }
        for b in (result.get("buckets") or [])
    ]

    return {
        "symbol": sym,
        "vpin": vpin,
        "regime": regime,
        "toxicity_percentile": None,
        "bucket_size": result.get("bucket_size"),
        "num_buckets": result.get("num_buckets"),
        "buckets": buckets_out,
        "defensive_spread_concession": result.get("recommended_spread_concession"),
        "warning_message": warning_message,
        "as_of": result.get("timestamp"),
        "data_available": data_available,
        "data_source": result.get("data_source"),
        "reason": result.get("reason"),
    }

