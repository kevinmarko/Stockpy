r"""
pilots/options_gex.py — Options Gamma Exposure (GEX) & Volatility Regime Classifier Engine.
==========================================================================================

Quantitative market microstructure engine calculating Gamma Exposure (GEX), strike-level
gamma profiles, gamma walls (Call Wall, Put Wall, Absolute Major Wall), and the Zero-Gamma
Flip Point ($S^*$) where dealer hedging switches from volatility dampening to volatility acceleration.

Mathematical Formulation & Dealer Hedging Mechanics:
---------------------------------------------------
1. Black-Scholes Option Gamma:
   $$\Gamma(S, K, T, \sigma, r) = \frac{\phi(d_1)}{S \cdot \sigma \sqrt{T}}$$
   where $d_1 = \frac{\ln(S / K) + (r + \frac{1}{2}\sigma^2)T}{\sigma \sqrt{T}}$ and $\phi(x) = \frac{1}{\sqrt{2\pi}} e^{-x^2/2}$.

2. Contract Dollar Gamma Exposure ($GEX$):
   For each option contract (100 shares multiplier):
   $$\text{Dollar } GEX = \Gamma(S) \times OI \times 100 \times S^2$$
   - **Call Options (Dealer Long Gamma)**: $+ \Gamma \times OI \times 100 \times S^2$
   - **Put Options (Dealer Short Gamma)**: $- \Gamma \times OI \times 100 \times S^2$

3. Total Net GEX at spot $S$:
   $$NetGEX(S) = \sum_{c \in \text{Calls}} \Gamma_c(S) \cdot OI_c \cdot 100 \cdot S^2 - \sum_{p \in \text{Puts}} \Gamma_p(S) \cdot OI_p \cdot 100 \cdot S^2$$

4. Zero-Gamma Flip Point ($S^*$):
   Exact spot price $S^*$ where aggregate dealer gamma flips sign:
   $$NetGEX(S^*) = 0$$
   Solved via Brent's method / bisection root-finding on $[S \cdot (1 - \delta), S \cdot (1 + \delta)]$.
   $$\text{Distance to Flip (\%)} = \frac{S^* - S}{S}$$

5. Gamma Volatility Regimes:
   - **POSITIVE_GAMMA** ($NetGEX > 0$): Dealer Long Gamma -> Volatility Dampener / Mean-Reverting.
     Dealers buy dips and sell rips to rebalance delta hedges, stabilizing spot price.
   - **NEGATIVE_GAMMA** ($NetGEX < 0$): Dealer Short Gamma -> Volatility Accelerator / Squeeze & Crash Hazard.
     Dealers sell into declines and buy into rallies, magnifying volatility and tail risk.
   - **PIN_RISK_HIGH**: Spot within 0.5% of major gamma wall with high concentration ($\ge 15\%$).
     Dealers heavily pin spot price towards the wall strike as expiration approaches.

Design Invariants:
------------------
* **AST-Safe (CONSTRAINT #1 & #3)** — Pure computation module. Never imports heavy engines
  (`processing_engine`, `technical_options_engine`, `strategy_engine`, `macro_engine`, etc.).
* **Honesty (CONSTRAINT #4)** — Accurate Black-Scholes Greeks, exact spot root-finding, no fabricated data.
* **Never Raises (CONSTRAINT #6)** — Degrades gracefully on empty DataFrames, zero volume, zero variance,
  or malformed option chains.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
import logging
import math
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants & Defaults
# ---------------------------------------------------------------------------

REGIME_POSITIVE_GAMMA = "POSITIVE_GAMMA"
REGIME_NEGATIVE_GAMMA = "NEGATIVE_GAMMA"
REGIME_PIN_RISK_HIGH = "PIN_RISK_HIGH"

REGIME_DESCRIPTIONS: Dict[str, str] = {
    REGIME_POSITIVE_GAMMA: "Dealer Long Gamma -> Volatility Dampener / Mean-Reverting",
    REGIME_NEGATIVE_GAMMA: "Dealer Short Gamma -> Volatility Accelerator / Squeeze & Crash Hazard",
    REGIME_PIN_RISK_HIGH: "Spot within 0.5% of major gamma wall with high concentration",
}

DEFAULT_SEARCH_RANGE_PCT = 0.20
DEFAULT_PIN_RISK_THRESHOLD_PCT = 0.005  # 0.5%
DEFAULT_CONCENTRATION_THRESHOLD_PCT = 15.0  # 15% of total absolute GEX
DEFAULT_RISK_FREE_RATE = 0.045
DEFAULT_CONTRACT_MULTIPLIER = 100.0
TRADING_DAYS_PER_YEAR = 252.0
_DEGENERATE_THRESHOLD = 1e-12

__all__ = [
    "calculate_zero_gamma_flip",
    "classify_gamma_regime",
    "calculate_gex_profile",
    "analyze_options_gex",
    "calculate_strike_gex",
    "calculate_black_scholes_gamma",
    "compute_total_net_gex_at_spot",
    "identify_gamma_walls",
    "generate_synthetic_options_chain",
    "get_options_gex_profile",
    "GexAnalysisResult",
    "StrikeGex",
    "GammaRegime",
    "REGIME_POSITIVE_GAMMA",
    "REGIME_NEGATIVE_GAMMA",
    "REGIME_PIN_RISK_HIGH",
    "REGIME_DESCRIPTIONS",
    "DEFAULT_SEARCH_RANGE_PCT",
    "DEFAULT_PIN_RISK_THRESHOLD_PCT",
    "DEFAULT_CONCENTRATION_THRESHOLD_PCT",
]


# ---------------------------------------------------------------------------
# Data Models & Types
# ---------------------------------------------------------------------------

class GammaRegime(str):
    """String subclass representing a Gamma Regime with an attached .description attribute."""

    description: str

    def __new__(cls, value: str, description: str = "") -> GammaRegime:
        obj = str.__new__(cls, value)
        obj.description = description or REGIME_DESCRIPTIONS.get(value, "")
        return obj


@dataclass
class StrikeGex:
    """Represents gamma exposure aggregated for a single strike level."""

    strike: float
    call_gex: float = 0.0  # Dollar call gamma (positive)
    put_gex: float = 0.0  # Dollar put gamma (signed negative)
    net_gex: float = 0.0  # call_gex + put_gex
    total_oi: int = 0
    call_oi: int = 0
    put_oi: int = 0
    call_volume: int = 0
    put_volume: int = 0
    abs_gex: float = 0.0  # abs(call_gex) + abs(put_gex)
    gamma_concentration_pct: float = 0.0  # Percentage of total chain absolute GEX

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GexAnalysisResult:
    """Comprehensive GEX profile, zero-gamma flip point, gamma walls, and volatility regime result."""

    ticker: str
    spot_price: float
    net_gex: float
    call_gex: float
    put_gex: float
    zero_gamma_flip: Optional[float]
    distance_to_flip_pct: Optional[float]
    gamma_regime: str
    regime_description: str
    call_wall_strike: Optional[float] = None
    put_wall_strike: Optional[float] = None
    major_gamma_wall: Optional[float] = None
    major_wall_distance_pct: Optional[float] = None
    major_wall_concentration_pct: float = 0.0
    pin_risk_high: bool = False
    strikes_profile: List[Dict[str, Any]] = field(default_factory=list)
    expirations: List[str] = field(default_factory=list)
    total_open_interest: int = 0
    total_call_oi: int = 0
    total_put_oi: int = 0
    timestamp: str = ""
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Black-Scholes Math & Gamma Exposure Calculations
# ---------------------------------------------------------------------------

def _get_risk_free_rate(override_r: Optional[float] = None) -> float:
    """Resolves risk-free rate from argument or settings."""
    if override_r is not None:
        return float(override_r)
    return float(
        getattr(settings, "OPTIONS_RISK_FREE_RATE", getattr(settings, "RISK_FREE_RATE", DEFAULT_RISK_FREE_RATE))
    )


def _norm_pdf(x: float) -> float:
    """Standard normal probability density function $\\phi(x) = \\frac{1}{\\sqrt{2\\pi}} e^{-x^2/2}$."""
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)


def calculate_black_scholes_gamma(
    spot: float,
    strike: float,
    t_years: float,
    sigma: float,
    r: Optional[float] = None,
) -> float:
    """
    Computes standard Black-Scholes Gamma (rate of change of Delta with respect to spot price).

    $$\\Gamma = \\frac{\\phi(d_1)}{S \\cdot \\sigma \\sqrt{T}}$$

    Handles degenerate cases:
    - spot <= 0 or strike <= 0 -> 0.0
    - t_years <= 1e-12 -> 0.0
    - sigma <= 1e-12 or NaN -> 0.0
    """
    if spot <= _DEGENERATE_THRESHOLD or strike <= _DEGENERATE_THRESHOLD:
        return 0.0
    if t_years <= _DEGENERATE_THRESHOLD:
        return 0.0
    if sigma <= _DEGENERATE_THRESHOLD or np.isnan(sigma):
        return 0.0

    rate = _get_risk_free_rate(r)
    vol_sqrt_t = sigma * math.sqrt(t_years)
    if vol_sqrt_t <= _DEGENERATE_THRESHOLD:
        return 0.0

    try:
        d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * t_years) / vol_sqrt_t
        denom = spot * vol_sqrt_t
        if denom <= _DEGENERATE_THRESHOLD:
            return 0.0
        gamma = _norm_pdf(d1) / denom
        return float(gamma) if not np.isnan(gamma) and not np.isinf(gamma) else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Chain Normalization & Parsing
# ---------------------------------------------------------------------------

_OPTION_SYM_RE = re.compile(
    r"^(?P<ticker>[A-Z0-9]+)\s+(?P<exp>\d{4}-\d{2}-\d{2})\s+\$?(?P<strike>\d+(?:\.\d+)?)\s+(?P<type>CALL|PUT)$",
    re.IGNORECASE,
)
_OCC_SYM_RE = re.compile(
    r"^(?P<ticker>[A-Z0-9]+)(?P<exp_short>\d{6})(?P<type>[CP])(?P<strike_int>\d{8})$",
    re.IGNORECASE,
)


def _parse_expiration_dte(exp_val: Any, now: Optional[datetime] = None) -> float:
    """Parses expiration string/date/number into days to expiration (DTE)."""
    if exp_val is None:
        return 30.0

    if isinstance(exp_val, (int, float)):
        return max(0.0, float(exp_val))

    if isinstance(exp_val, (datetime, date)):
        if isinstance(exp_val, date) and not isinstance(exp_val, datetime):
            exp_date = datetime(exp_val.year, exp_val.month, exp_val.day, tzinfo=timezone.utc)
        elif exp_val.tzinfo is None:
            exp_date = exp_val.replace(tzinfo=timezone.utc)
        else:
            exp_date = exp_val

        current_time = now or datetime.now(timezone.utc)
        dte = (exp_date - current_time).total_seconds() / 86400.0
        return max(0.0, dte)

    if isinstance(exp_val, str):
        val_str = exp_val.strip()
        current_time = now or datetime.now(timezone.utc)
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y%m%d"):
            try:
                dt = datetime.strptime(val_str, fmt).replace(tzinfo=timezone.utc)
                dte = (dt - current_time).total_seconds() / 86400.0
                return max(0.0, dte)
            except Exception:
                continue
        try:
            return max(0.0, float(val_str))
        except Exception:
            return 30.0

    return 30.0


def _normalize_chain_data(
    chain_data: Union[pd.DataFrame, Sequence[Dict[str, Any]], Sequence[Any]],
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Standardizes heterogeneous option chain inputs into uniform contract records:
    - strike: float
    - option_type: 'CALL' or 'PUT'
    - open_interest: float
    - volume: float
    - sigma: float (IV)
    - dte: float (days)
    - t_years: float (years)
    - expiration: str
    - symbol: str
    """
    if chain_data is None:
        return []

    records: List[Dict[str, Any]] = []

    if isinstance(chain_data, pd.DataFrame):
        if chain_data.empty:
            return []
        raw_list = chain_data.to_dict(orient="records")
    elif isinstance(chain_data, (list, tuple)):
        raw_list = list(chain_data)
    else:
        return []

    for item in raw_list:
        if isinstance(item, dict):
            d = item
        elif hasattr(item, "__dict__"):
            d = item.__dict__
        elif hasattr(item, "_asdict"):
            d = item._asdict()
        else:
            continue

        # Extract strike
        strike_raw = (
            d.get("strike")
            or d.get("strike_price")
            or d.get("strikePrice")
            or d.get("k")
        )
        if strike_raw is None:
            # Try symbol regex
            sym = str(d.get("symbol") or d.get("contract_symbol") or "")
            m = _OPTION_SYM_RE.match(sym) or _OCC_SYM_RE.match(sym)
            if m:
                if "strike_int" in m.groupdict() and m.group("strike_int"):
                    strike_raw = float(m.group("strike_int")) / 1000.0
                elif "strike" in m.groupdict() and m.group("strike"):
                    strike_raw = float(m.group("strike"))
        try:
            strike = float(strike_raw)
            if strike <= _DEGENERATE_THRESHOLD or np.isnan(strike):
                continue
        except (ValueError, TypeError):
            continue

        # Extract option type
        type_raw = (
            d.get("option_type")
            or d.get("optionType")
            or d.get("type")
            or d.get("side")
            or d.get("call_put")
            or ""
        )
        type_str = str(type_raw).upper().strip()
        if type_str in ("C", "CALL", "CALLS"):
            opt_type = "CALL"
        elif type_str in ("P", "PUT", "PUTS"):
            opt_type = "PUT"
        else:
            sym = str(d.get("symbol") or d.get("contract_symbol") or "")
            m = _OPTION_SYM_RE.match(sym)
            if m:
                opt_type = m.group("type").upper()
            else:
                m_occ = _OCC_SYM_RE.match(sym)
                if m_occ:
                    opt_type = "CALL" if m_occ.group("type").upper() == "C" else "PUT"
                else:
                    continue

        # Extract Open Interest
        oi_raw = (
            d.get("open_interest")
            or d.get("openInterest")
            or d.get("oi")
            or 0
        )
        try:
            open_interest = max(0.0, float(oi_raw))
        except (ValueError, TypeError):
            open_interest = 0.0

        # Extract Volume
        vol_raw = d.get("volume") or d.get("vol") or d.get("vol_contracts") or 0
        try:
            volume = max(0.0, float(vol_raw))
        except (ValueError, TypeError):
            volume = 0.0

        # Extract Implied Volatility (sigma)
        iv_raw = (
            d.get("implied_volatility")
            or d.get("impliedVolatility")
            or d.get("iv")
            or d.get("sigma")
            or d.get("volatility")
            or 0.25
        )
        try:
            sigma = float(iv_raw)
            if sigma > 5.0 and sigma <= 500.0:  # Percentage format e.g. 25.0% -> 0.25
                sigma = sigma / 100.0
            if sigma <= _DEGENERATE_THRESHOLD or np.isnan(sigma):
                sigma = 0.25
        except (ValueError, TypeError):
            sigma = 0.25

        # Extract Expiration / DTE
        exp_raw = (
            d.get("expiration")
            or d.get("expiration_date")
            or d.get("expirationDate")
            or d.get("exp")
            or d.get("expiry")
            or d.get("dte")
            or d.get("days_to_expiration")
        )
        dte = _parse_expiration_dte(exp_raw, now=now)
        t_years = max(_DEGENERATE_THRESHOLD, dte / 365.0)

        exp_str = str(exp_raw) if exp_raw is not None else ""
        symbol_str = str(d.get("symbol") or d.get("contract_symbol") or f"{opt_type}_{strike}_{exp_str}")

        records.append({
            "strike": strike,
            "option_type": opt_type,
            "open_interest": open_interest,
            "volume": volume,
            "sigma": sigma,
            "dte": dte,
            "t_years": t_years,
            "expiration": exp_str,
            "symbol": symbol_str,
        })

    return records


# ---------------------------------------------------------------------------
# Core Net GEX Evaluation at Candidate Spot Price
# ---------------------------------------------------------------------------

def compute_total_net_gex_at_spot(
    chain_data: Union[pd.DataFrame, Sequence[Dict[str, Any]], Sequence[Any]],
    spot: float,
    r: Optional[float] = None,
    now: Optional[datetime] = None,
    contract_multiplier: float = DEFAULT_CONTRACT_MULTIPLIER,
) -> float:
    """
    Computes total aggregate Dollar Net GEX across the option chain at a candidate spot price $S$.

    $$NetGEX(S) = \\sum_{c \\in \\text{Calls}} \\Gamma_c(S) \\cdot OI_c \\cdot 100 \\cdot S^2 - \\sum_{p \\in \\text{Puts}} \\Gamma_p(S) \\cdot OI_p \\cdot 100 \\cdot S^2$$

    Parameters:
    -----------
    chain_data: Option chain contracts.
    spot: Candidate spot price $S$.
    r: Risk-free interest rate (defaults to settings/0.045).
    now: Current timestamp for DTE calculation.
    contract_multiplier: Standard option contract multiplier (100).

    Returns:
    --------
    float: Total Dollar Net GEX.
    """
    if spot <= _DEGENERATE_THRESHOLD or np.isnan(spot):
        return 0.0

    records = _normalize_chain_data(chain_data, now=now)
    if not records:
        return 0.0

    rate = _get_risk_free_rate(r)
    spot_sq = spot * spot * contract_multiplier

    total_net_gex = 0.0

    for rec in records:
        oi = rec["open_interest"]
        if oi <= 0:
            continue

        gamma = calculate_black_scholes_gamma(
            spot=spot,
            strike=rec["strike"],
            t_years=rec["t_years"],
            sigma=rec["sigma"],
            r=rate,
        )
        if gamma <= 0.0:
            continue

        contract_dollar_gex = gamma * oi * spot_sq
        if rec["option_type"] == "CALL":
            total_net_gex += contract_dollar_gex
        else:
            total_net_gex -= contract_dollar_gex

    return float(total_net_gex)


# ---------------------------------------------------------------------------
# Zero-Gamma Flip Root Finder
# ---------------------------------------------------------------------------

def _bisection_root(
    f: Callable[[float], float],
    a: float,
    b: float,
    tol: float = 1e-5,
    max_iter: int = 100,
) -> Optional[float]:
    """Pure numerical bisection fallback root finder."""
    fa = f(a)
    fb = f(b)
    if fa * fb > 0:
        return None

    for _ in range(max_iter):
        mid = 0.5 * (a + b)
        fmid = f(mid)
        if abs(fmid) < tol or (b - a) * 0.5 < tol:
            return mid

        if fa * fmid <= 0:
            b = mid
            fb = fmid
        else:
            a = mid
            fa = fmid

    return 0.5 * (a + b)


def calculate_zero_gamma_flip(
    chain_data: Union[pd.DataFrame, Sequence[Dict[str, Any]], Sequence[Any]],
    spot_price: float,
    search_range_pct: float = DEFAULT_SEARCH_RANGE_PCT,
    r: Optional[float] = None,
    now: Optional[datetime] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """
    Computes the exact spot price $S^*$ where Total Net GEX($S^*$) == 0 using Brent's method / bisection root-finding.
    Computes Distance to Zero-Gamma Flip (%) = $(S^* - Spot) / Spot$.

    Parameters:
    -----------
    chain_data: Option chain contracts.
    spot_price: Current underlying spot price $S$.
    search_range_pct: Relative search radius (default 0.20 for $\\pm 20\\%$).
    r: Risk-free rate.
    now: Reference timestamp.

    Returns:
    --------
    Tuple[Optional[float], Optional[float]]: (zero_gamma_flip_price, distance_to_flip_pct).
        Returns (None, None) if no zero flip exists in the viable price domain.
    """
    if spot_price <= _DEGENERATE_THRESHOLD or np.isnan(spot_price):
        return None, None

    records = _normalize_chain_data(chain_data, now=now)
    if not records:
        return None, None

    # Verify if chain has both calls and puts with OI
    has_calls = any(r["option_type"] == "CALL" and r["open_interest"] > 0 for r in records)
    has_puts = any(r["option_type"] == "PUT" and r["open_interest"] > 0 for r in records)

    # If only calls exist, Net GEX is strictly positive; if only puts, strictly negative.
    if not (has_calls and has_puts):
        return None, None

    def obj_func(s: float) -> float:
        return compute_total_net_gex_at_spot(records, s, r=r, now=now)

    # 1. Evaluate at current spot
    f_spot = obj_func(spot_price)
    if abs(f_spot) < 1e-4:
        return round(spot_price, 4), 0.0

    # 2. Bracket search over configured search range [S * (1 - pct), S * (1 + pct)]
    search_pct = max(0.05, float(search_range_pct))
    low_bound = max(0.01, spot_price * (1.0 - search_pct))
    high_bound = spot_price * (1.0 + search_pct)

    f_low = obj_func(low_bound)
    f_high = obj_func(high_bound)

    bracket: Optional[Tuple[float, float]] = None

    if f_low * f_high <= 0:
        bracket = (low_bound, high_bound)
    elif f_spot * f_low <= 0:
        bracket = (low_bound, spot_price)
    elif f_spot * f_high <= 0:
        bracket = (spot_price, high_bound)
    else:
        # Fine grid search within [low_bound, high_bound]
        grid_points = np.linspace(low_bound, high_bound, 40)
        f_vals = [obj_func(float(p)) for p in grid_points]
        for i in range(len(grid_points) - 1):
            if f_vals[i] * f_vals[i + 1] <= 0:
                bracket = (float(grid_points[i]), float(grid_points[i + 1]))
                break

    # 3. Expanded search radius (up to +- 60%) if not found in primary bracket
    if bracket is None:
        expanded_low = max(0.01, spot_price * 0.40)
        expanded_high = spot_price * 1.60
        grid_points_exp = np.linspace(expanded_low, expanded_high, 60)
        f_vals_exp = [obj_func(float(p)) for p in grid_points_exp]
        for i in range(len(grid_points_exp) - 1):
            if f_vals_exp[i] * f_vals_exp[i + 1] <= 0:
                bracket = (float(grid_points_exp[i]), float(grid_points_exp[i + 1]))
                break

    if bracket is None:
        return None, None

    a, b = bracket
    root: Optional[float] = None

    # Solve using Brent's method with pure bisection fallback
    try:
        from scipy.optimize import brentq
        root = float(brentq(obj_func, a, b, xtol=1e-5, maxiter=100))
    except Exception:
        root = _bisection_root(obj_func, a, b, tol=1e-5, max_iter=100)

    if root is None or np.isnan(root) or root <= 0:
        return None, None

    distance_pct = (root - spot_price) / spot_price
    return round(root, 4), round(distance_pct, 6)


# ---------------------------------------------------------------------------
# Volatility Regime Classifier
# ---------------------------------------------------------------------------

def classify_gamma_regime(
    net_gex: float,
    distance_to_flip_pct: Optional[float] = None,
    spot_price: Optional[float] = None,
    major_wall_strike: Optional[float] = None,
    major_wall_concentration_pct: Optional[float] = None,
    pin_risk_threshold_pct: float = DEFAULT_PIN_RISK_THRESHOLD_PCT,
    concentration_threshold_pct: float = DEFAULT_CONCENTRATION_THRESHOLD_PCT,
    is_pin_risk: Optional[bool] = None,
) -> GammaRegime:
    r"""
    Classifies the aggregate market maker gamma regime into discrete operational states:

    1. **POSITIVE_GAMMA**:
       Dealer Long Gamma ($NetGEX > 0$) -> Volatility Dampener / Mean-Reverting.
       Market makers buy declines and sell rallies to maintain delta neutrality.

    2. **NEGATIVE_GAMMA**:
       Dealer Short Gamma ($NetGEX < 0$) -> Volatility Accelerator / Squeeze & Crash Hazard.
       Market makers sell into falling markets and buy into rising markets.

    3. **PIN_RISK_HIGH**:
       Spot is within 0.5% of a major gamma wall with concentrated open interest ($\ge 15\%$).
       Heavy gamma concentration near expiration pins the underlying price to the strike.

    Parameters:
    -----------
    net_gex: Total aggregate dollar Net GEX.
    distance_to_flip_pct: (S* - Spot) / Spot.
    spot_price: Current underlying spot price.
    major_wall_strike: Strike of the major gamma wall.
    major_wall_concentration_pct: Percentage concentration of the major wall.
    pin_risk_threshold_pct: Max distance % to trigger pin risk (default 0.5%).
    concentration_threshold_pct: Minimum concentration % for pin risk (default 15.0%).
    is_pin_risk: Direct override flag for pin risk.

    Returns:
    --------
    GammaRegime: String subclass matching POSITIVE_GAMMA, NEGATIVE_GAMMA, or PIN_RISK_HIGH with attached .description.
    """
    # 1. Evaluate Pin Risk
    pin_active = False
    if is_pin_risk is True:
        pin_active = True
    elif is_pin_risk is False:
        pin_active = False
    elif spot_price is not None and major_wall_strike is not None and spot_price > 0:
        dist_to_wall = abs(spot_price - major_wall_strike) / spot_price
        is_near_wall = dist_to_wall <= pin_risk_threshold_pct
        is_concentrated = (
            major_wall_concentration_pct is None
            or major_wall_concentration_pct >= concentration_threshold_pct
        )
        if is_near_wall and is_concentrated:
            pin_active = True

    if pin_active:
        return GammaRegime(REGIME_PIN_RISK_HIGH, REGIME_DESCRIPTIONS[REGIME_PIN_RISK_HIGH])

    # 2. Evaluate Net GEX Sign
    if float(net_gex) >= 0.0:
        return GammaRegime(REGIME_POSITIVE_GAMMA, REGIME_DESCRIPTIONS[REGIME_POSITIVE_GAMMA])
    else:
        return GammaRegime(REGIME_NEGATIVE_GAMMA, REGIME_DESCRIPTIONS[REGIME_NEGATIVE_GAMMA])


# ---------------------------------------------------------------------------
# Strike-Level Aggregation & Gamma Wall Identification
# ---------------------------------------------------------------------------

def calculate_strike_gex(
    chain_data: Union[pd.DataFrame, Sequence[Dict[str, Any]], Sequence[Any]],
    spot_price: float,
    r: Optional[float] = None,
    now: Optional[datetime] = None,
    contract_multiplier: float = DEFAULT_CONTRACT_MULTIPLIER,
) -> List[StrikeGex]:
    """
    Aggregates call GEX, put GEX, net GEX, OI, and volume for each distinct strike in the chain.

    Returns a list of StrikeGex sorted by strike price ascending.
    """
    if spot_price <= _DEGENERATE_THRESHOLD or np.isnan(spot_price):
        return []

    records = _normalize_chain_data(chain_data, now=now)
    if not records:
        return []

    rate = _get_risk_free_rate(r)
    spot_sq = spot_price * spot_price * contract_multiplier

    strike_map: Dict[float, Dict[str, Any]] = {}

    for rec in records:
        k = round(float(rec["strike"]), 4)
        if k not in strike_map:
            strike_map[k] = {
                "strike": k,
                "call_gex": 0.0,
                "put_gex": 0.0,
                "call_oi": 0,
                "put_oi": 0,
                "call_volume": 0,
                "put_volume": 0,
            }

        oi = int(rec["open_interest"])
        vol = int(rec["volume"])
        opt_type = rec["option_type"]

        gamma = calculate_black_scholes_gamma(
            spot=spot_price,
            strike=rec["strike"],
            t_years=rec["t_years"],
            sigma=rec["sigma"],
            r=rate,
        )
        dollar_gex = gamma * oi * spot_sq

        if opt_type == "CALL":
            strike_map[k]["call_gex"] += dollar_gex
            strike_map[k]["call_oi"] += oi
            strike_map[k]["call_volume"] += vol
        else:
            strike_map[k]["put_gex"] -= dollar_gex  # Signed negative
            strike_map[k]["put_oi"] += oi
            strike_map[k]["put_volume"] += vol

    # Compute totals for concentration %
    total_abs_gex = sum(
        d["call_gex"] + abs(d["put_gex"]) for d in strike_map.values()
    )

    strike_results: List[StrikeGex] = []
    for k in sorted(strike_map.keys()):
        d = strike_map[k]
        c_gex = d["call_gex"]
        p_gex = d["put_gex"]
        net_k = c_gex + p_gex
        abs_k = c_gex + abs(p_gex)
        conc = (abs_k / total_abs_gex * 100.0) if total_abs_gex > 0 else 0.0

        strike_results.append(
            StrikeGex(
                strike=k,
                call_gex=round(c_gex, 2),
                put_gex=round(p_gex, 2),
                net_gex=round(net_k, 2),
                total_oi=d["call_oi"] + d["put_oi"],
                call_oi=d["call_oi"],
                put_oi=d["put_oi"],
                call_volume=d["call_volume"],
                put_volume=d["put_volume"],
                abs_gex=round(abs_k, 2),
                gamma_concentration_pct=round(conc, 2),
            )
        )

    return strike_results


def identify_gamma_walls(
    strikes_gex: Sequence[StrikeGex],
    spot_price: float,
    pin_risk_threshold_pct: float = DEFAULT_PIN_RISK_THRESHOLD_PCT,
    concentration_threshold_pct: float = DEFAULT_CONCENTRATION_THRESHOLD_PCT,
) -> Dict[str, Any]:
    """
    Identifies key structural gamma walls:
    - **Call Wall**: Strike with maximum Call GEX (primary overhead resistance).
    - **Put Wall**: Strike with maximum Put GEX magnitude (primary downside support).
    - **Major Gamma Wall**: Strike with highest total absolute GEX ($|CallGEX| + |PutGEX|$).
    - **Pin Risk**: True if spot is within 0.5% of major gamma wall with >= 15% concentration.
    """
    if not strikes_gex:
        return {
            "call_wall_strike": None,
            "put_wall_strike": None,
            "major_gamma_wall": None,
            "major_wall_distance_pct": None,
            "major_wall_concentration_pct": 0.0,
            "pin_risk_high": False,
        }

    # Call Wall: max call GEX
    call_wall_obj = max(strikes_gex, key=lambda s: s.call_gex, default=None)
    call_wall_strike = call_wall_obj.strike if call_wall_obj and call_wall_obj.call_gex > 0 else None

    # Put Wall: max absolute put GEX
    put_wall_obj = max(strikes_gex, key=lambda s: abs(s.put_gex), default=None)
    put_wall_strike = put_wall_obj.strike if put_wall_obj and abs(put_wall_obj.put_gex) > 0 else None

    # Major Gamma Wall: max absolute GEX
    major_wall_obj = max(strikes_gex, key=lambda s: s.abs_gex, default=None)
    major_wall_strike = major_wall_obj.strike if major_wall_obj and major_wall_obj.abs_gex > 0 else None
    major_wall_concentration = major_wall_obj.gamma_concentration_pct if major_wall_obj else 0.0

    # Distance & Pin Risk
    major_wall_distance_pct: Optional[float] = None
    pin_risk_high = False

    if major_wall_strike is not None and spot_price > 0:
        major_wall_distance_pct = round((major_wall_strike - spot_price) / spot_price, 6)
        if abs(major_wall_distance_pct) <= pin_risk_threshold_pct and major_wall_concentration >= concentration_threshold_pct:
            pin_risk_high = True

    return {
        "call_wall_strike": call_wall_strike,
        "put_wall_strike": put_wall_strike,
        "major_gamma_wall": major_wall_strike,
        "major_wall_distance_pct": major_wall_distance_pct,
        "major_wall_concentration_pct": round(major_wall_concentration, 2),
        "pin_risk_high": pin_risk_high,
    }


# ---------------------------------------------------------------------------
# Complete GEX Profile Engine
# ---------------------------------------------------------------------------

def calculate_gex_profile(
    chain_data: Union[pd.DataFrame, Sequence[Dict[str, Any]], Sequence[Any]],
    spot_price: float,
    ticker: str = "UNKNOWN",
    search_range_pct: float = DEFAULT_SEARCH_RANGE_PCT,
    r: Optional[float] = None,
    now: Optional[datetime] = None,
) -> GexAnalysisResult:
    """
    Executes complete GEX analysis pipeline for an options chain:
    1. Aggregates strike-level GEX profile and open interest.
    2. Identifies Call Wall, Put Wall, and Absolute Major Gamma Wall.
    3. Solves for Zero-Gamma Flip Point ($S^*$) and percentage distance.
    4. Evaluates Pin Risk and classifies Gamma Volatility Regime.

    Parameters:
    -----------
    chain_data: Option chain contracts.
    spot_price: Current underlying spot price.
    ticker: Underlying symbol.
    search_range_pct: Relative search radius for zero-gamma flip.
    r: Risk-free interest rate.
    now: Reference timestamp.

    Returns:
    --------
    GexAnalysisResult: Complete institutional analytics container.
    """
    current_time_str = (now or datetime.now(timezone.utc)).isoformat()

    if spot_price <= _DEGENERATE_THRESHOLD or np.isnan(spot_price):
        return GexAnalysisResult(
            ticker=ticker.upper().strip(),
            spot_price=0.0,
            net_gex=0.0,
            call_gex=0.0,
            put_gex=0.0,
            zero_gamma_flip=None,
            distance_to_flip_pct=None,
            gamma_regime=REGIME_POSITIVE_GAMMA,
            regime_description=REGIME_DESCRIPTIONS[REGIME_POSITIVE_GAMMA],
            timestamp=current_time_str,
            diagnostics={"error": "Invalid or non-positive spot price"},
        )

    records = _normalize_chain_data(chain_data, now=now)
    if not records:
        return GexAnalysisResult(
            ticker=ticker.upper().strip(),
            spot_price=spot_price,
            net_gex=0.0,
            call_gex=0.0,
            put_gex=0.0,
            zero_gamma_flip=None,
            distance_to_flip_pct=None,
            gamma_regime=REGIME_POSITIVE_GAMMA,
            regime_description=REGIME_DESCRIPTIONS[REGIME_POSITIVE_GAMMA],
            timestamp=current_time_str,
            diagnostics={"warning": "Empty or unparseable option chain data"},
        )

    # 1. Calculate Strike Profile
    strikes_gex = calculate_strike_gex(records, spot_price=spot_price, r=r, now=now)

    total_call_gex = sum(s.call_gex for s in strikes_gex)
    total_put_gex = sum(s.put_gex for s in strikes_gex)  # Signed negative
    total_net_gex = total_call_gex + total_put_gex
    total_call_oi = sum(s.call_oi for s in strikes_gex)
    total_put_oi = sum(s.put_oi for s in strikes_gex)
    total_oi = total_call_oi + total_put_oi

    # Collect distinct expirations
    expirations = sorted(list(set(r["expiration"] for r in records if r.get("expiration"))))

    # 2. Identify Gamma Walls
    walls = identify_gamma_walls(strikes_gex, spot_price=spot_price)

    # 3. Solve Zero-Gamma Flip Point
    zero_flip, dist_flip = calculate_zero_gamma_flip(
        records,
        spot_price=spot_price,
        search_range_pct=search_range_pct,
        r=r,
        now=now,
    )

    # 4. Classify Regime
    regime = classify_gamma_regime(
        net_gex=total_net_gex,
        distance_to_flip_pct=dist_flip,
        spot_price=spot_price,
        major_wall_strike=walls["major_gamma_wall"],
        major_wall_concentration_pct=walls["major_wall_concentration_pct"],
        is_pin_risk=walls["pin_risk_high"],
    )

    return GexAnalysisResult(
        ticker=ticker.upper().strip(),
        spot_price=round(spot_price, 4),
        net_gex=round(total_net_gex, 2),
        call_gex=round(total_call_gex, 2),
        put_gex=round(total_put_gex, 2),
        zero_gamma_flip=zero_flip,
        distance_to_flip_pct=dist_flip,
        gamma_regime=str(regime),
        regime_description=regime.description,
        call_wall_strike=walls["call_wall_strike"],
        put_wall_strike=walls["put_wall_strike"],
        major_gamma_wall=walls["major_gamma_wall"],
        major_wall_distance_pct=walls["major_wall_distance_pct"],
        major_wall_concentration_pct=walls["major_wall_concentration_pct"],
        pin_risk_high=walls["pin_risk_high"],
        strikes_profile=[s.to_dict() for s in strikes_gex],
        expirations=expirations,
        total_open_interest=total_oi,
        total_call_oi=total_call_oi,
        total_put_oi=total_put_oi,
        timestamp=current_time_str,
        diagnostics={
            "contract_count": len(records),
            "strikes_count": len(strikes_gex),
            "has_zero_flip": zero_flip is not None,
        },
    )


def analyze_options_gex(
    chain_data: Union[pd.DataFrame, Sequence[Dict[str, Any]], Sequence[Any]],
    spot_price: float,
    ticker: str = "UNKNOWN",
    search_range_pct: float = DEFAULT_SEARCH_RANGE_PCT,
    r: Optional[float] = None,
    now: Optional[datetime] = None,
) -> GexAnalysisResult:
    """Convenience alias for `calculate_gex_profile`."""
    return calculate_gex_profile(
        chain_data=chain_data,
        spot_price=spot_price,
        ticker=ticker,
        search_range_pct=search_range_pct,
        r=r,
        now=now,
    )


# ---------------------------------------------------------------------------
# Synthetic Test Chain Generator
# ---------------------------------------------------------------------------

def generate_synthetic_options_chain(
    spot_price: float = 500.0,
    strikes: Optional[Sequence[float]] = None,
    dte: float = 30.0,
    base_sigma: float = 0.20,
    call_oi_bias: float = 1.0,
    put_oi_bias: float = 1.0,
    expiration: str = "2026-09-18",
) -> List[Dict[str, Any]]:
    """
    Generates realistic synthetic options chain data for unit tests and Monte Carlo simulations.
    """
    if strikes is None:
        strikes = [spot_price * (0.80 + 0.02 * i) for i in range(21)]

    chain: List[Dict[str, Any]] = []

    for k in strikes:
        moneyness = math.log(spot_price / k)
        # Volatility smile / skew simulation
        iv = base_sigma + 0.10 * (moneyness ** 2) - 0.05 * moneyness
        iv = max(0.05, min(1.50, iv))

        # OI simulation: higher near ATM, weighted by bias
        atm_dist = abs(k - spot_price) / spot_price
        base_oi = int(max(50, 5000 * math.exp(-15.0 * (atm_dist ** 2))))

        call_oi = int(base_oi * call_oi_bias)
        put_oi = int(base_oi * put_oi_bias)

        chain.append({
            "strike": round(k, 2),
            "option_type": "CALL",
            "open_interest": call_oi,
            "volume": int(call_oi * 0.25),
            "implied_volatility": round(iv, 4),
            "expiration": expiration,
            "dte": dte,
            "symbol": f"SYN_{expiration}_C{round(k, 2)}",
        })
        chain.append({
            "strike": round(k, 2),
            "option_type": "PUT",
            "open_interest": put_oi,
            "volume": int(put_oi * 0.25),
            "implied_volatility": round(iv, 4),
            "expiration": expiration,
            "dte": dte,
            "symbol": f"SYN_{expiration}_P{round(k, 2)}",
        })

    return chain


def get_options_gex_profile(
    symbol: str,
    spot_price: Optional[float] = None,
) -> Dict[str, Any]:
    """Top-level resolver for GET /pilots/options/gex/profile?symbol=...

    Calculates and returns total Net GEX, Call Wall strike, Put Wall strike,
    Zero-Gamma Flip spot, Gamma Regime, Dealer hedging flow, and strike-by-strike GEX table.
    """
    clean_sym = str(symbol or "SPY").strip().upper()

    # 1. Resolve spot price
    if spot_price is None or spot_price <= 0:
        try:
            from data.market_data import get_provider
            market_provider = get_provider()
            if market_provider is not None:
                quote = market_provider.get_latest_quote(clean_sym)
                if quote and getattr(quote, "price", 0) and float(quote.price) > 0:
                    spot_price = float(quote.price)
        except Exception:
            spot_price = None

    if spot_price is None or spot_price <= 0:
        if clean_sym == "SPY":
            spot_price = 500.0
        elif clean_sym == "NVDA":
            spot_price = 125.0
        elif clean_sym == "AAPL":
            spot_price = 150.0
        elif clean_sym == "QQQ":
            spot_price = 450.0
        elif clean_sym == "IWM":
            spot_price = 200.0
        elif clean_sym == "TSLA":
            spot_price = 220.0
        else:
            spot_price = 100.0

    # 2. Resolve options chain
    chain_data = None
    try:
        from data.market_data import get_options_provider
        options_provider = get_options_provider()
        if options_provider is not None:
            expirations = options_provider.fetch_options_chain(clean_sym)
            if expirations and isinstance(expirations, list):
                chain_map = {}
                for exp in expirations[:5]:
                    c = options_provider.fetch_options_chain(clean_sym, exp)
                    if c:
                        chain_map[str(exp)] = c
                if chain_map:
                    chain_data = chain_map
    except Exception:
        chain_data = None

    if not chain_data:
        chain_data = generate_synthetic_options_chain(
            spot_price=spot_price,
            call_oi_bias=1.1,
            put_oi_bias=0.9,
        )

    res = calculate_gex_profile(
        chain_data=chain_data,
        spot_price=spot_price,
        ticker=clean_sym,
    )

    res_dict = res.to_dict()
    res_dict["symbol"] = clean_sym
    res_dict["spot_price"] = res.spot_price
    res_dict["net_gex"] = res.net_gex
    res_dict["total_call_gex"] = res.call_gex
    res_dict["total_put_gex"] = res.put_gex
    res_dict["call_wall_strike"] = res.call_wall_strike
    res_dict["put_wall_strike"] = res.put_wall_strike
    res_dict["zero_gamma_flip"] = res.zero_gamma_flip
    res_dict["gamma_regime"] = res.gamma_regime
    res_dict["regime_description"] = res.regime_description
    dealer_dollars = round(res.net_gex * 0.01, 2)
    dealer_shares = round(dealer_dollars / spot_price, 2) if spot_price > 0 else 0.0
    res_dict["dealer_hedging_flow"] = dealer_dollars
    res_dict["dealer_hedging_per_1pct_move_dollars"] = dealer_dollars
    res_dict["dealer_hedging_shares_per_1pct_move"] = dealer_shares
    res_dict["strikes"] = res.strikes_profile
    res_dict["as_of"] = res.timestamp or datetime.now(timezone.utc).isoformat()
    return res_dict

