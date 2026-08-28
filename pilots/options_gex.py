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

2. Contract Dollar Gamma Exposure ($GEX$), expressed per the industry-standard
   convention of "dollar hedging flow dealers must transact for a 1% move in
   the underlying" (SqueezeMetrics / SpotGamma convention -- see the 0.01
   factor derivation below):
   For each option contract (100 shares multiplier):
   $$\text{Dollar } GEX = \Gamma(S) \times OI \times 100 \times S^2 \times 0.01$$
   - **Call Options (Dealer Long Gamma)**: $+ \Gamma \times OI \times 100 \times S^2 \times 0.01$
   - **Put Options (Dealer Short Gamma)**: $- \Gamma \times OI \times 100 \times S^2 \times 0.01$

   Derivation of the $0.01$ factor: $\Gamma$ is the change in delta per $1
   move in $S$. A 1% move in the underlying is $0.01 \times S$ dollars, so
   the number of shares dealers must transact for a 1% move is
   $\Gamma \times OI \times 100 \times (0.01 \times S)$, and the dollar value
   of that share flow is that quantity times $S$ again, i.e.
   $\Gamma \times OI \times 100 \times S^2 \times 0.01$. Omitting the $0.01$
   (as an earlier version of this module did) overstates every dollar GEX
   figure by exactly 100x relative to this convention -- see
   `docs/known_issues/options_gex_100x_dollar_scaling.md`.

3. Total Net GEX at spot $S$:
   $$NetGEX(S) = 0.01 \left[ \sum_{c \in \text{Calls}} \Gamma_c(S) \cdot OI_c \cdot 100 \cdot S^2 - \sum_{p \in \text{Puts}} \Gamma_p(S) \cdot OI_p \cdot 100 \cdot S^2 \right]$$

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

# Operator-configurable via settings.OPTIONS_GEX_SEARCH_RANGE_PCT (default 0.20,
# i.e. +/- 20% of spot) -- DEFAULT_SEARCH_RANGE_PCT below is the fallback literal
# used only when the settings attribute is unavailable. Read dynamically at call
# time (not baked into the function-signature default) so a runtime settings
# change takes effect without re-importing this module, matching
# pilots/options_vpin.py's DEFAULT_TOXICITY_THRESHOLD convention.
DEFAULT_SEARCH_RANGE_PCT = 0.20
DEFAULT_PIN_RISK_THRESHOLD_PCT = 0.005  # 0.5%
DEFAULT_CONCENTRATION_THRESHOLD_PCT = 15.0  # 15% of total absolute GEX
DEFAULT_RISK_FREE_RATE = 0.045
DEFAULT_CONTRACT_MULTIPLIER = 100.0
# Converts a raw Gamma*OI*multiplier*S^2 dollar-gamma sum into the
# industry-standard "dollar hedging flow per 1% move in the underlying"
# convention (SqueezeMetrics/SpotGamma). See the module docstring's
# "Derivation of the 0.01 factor" note. Applied once, at the point every GEX
# dollar figure is aggregated (`compute_total_net_gex_at_spot`,
# `calculate_strike_gex`) so every downstream figure (net/call/put GEX,
# per-strike GEX, dealer_hedging_flow) is consistently scaled -- omitting it
# was a confirmed 100x overstatement, see
# docs/known_issues/options_gex_100x_dollar_scaling.md.
PERCENT_MOVE_SCALING_FACTOR = 0.01
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
        # DELIBERATE DIVERGENCE from pilots/options_risk.py::calculate_black_scholes_greeks
        # -- investigated and kept, not an unnoticed drift (docs/
        # module_efficiency_redundancy_audit.md's F4). options_risk.py
        # instead CLAMPS vol_sqrt_t to _DEGENERATE_THRESHOLD and continues
        # computing d1/gamma. Empirically, that clamp-and-continue path can
        # produce a spuriously enormous gamma in this same edge case -- e.g.
        # spot=strike=100, t_years=1e-11, sigma=1e-7 (an ATM contract with a
        # genuinely negligible vol_sqrt_t) returns gamma ~ 3.6e9, not a
        # meaningful number. Returning 0.0 here is the numerically safer
        # choice for a gamma-exposure aggregate (this function feeds
        # portfolio-wide dealer GEX sums, where one spurious multi-billion
        # value would dominate and invalidate the whole aggregate), even
        # though it makes this function's degenerate-input floor slightly
        # more conservative than the canonical Greeks calculator's. The
        # canonical function's own clamp-and-continue behavior is NOT
        # changed here -- it has 7+ reuse sites and any fix there needs its
        # own dedicated, carefully-tested PR, out of scope for this one.
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

# Matches the SAME standardized option leg symbol format documented by
# pilots/options_risk.py::_OPTION_SYM_RE ("AAPL 2026-09-18 $150.00 CALL")
# and pilots/realtime_risk_streamer.py's copy of the same regex -- the `$`
# is REQUIRED, not optional. It was optional here until this fix (F3,
# docs/module_efficiency_redundancy_audit.md): a symbol string lacking `$`
# parsed successfully in this file and returned None in the two siblings --
# a real behavioral fork on the same nominal format, not a deliberate
# looser grammar for a different data source (the no-`$` OCC ticker format
# is a genuinely different shape, already handled separately by
# _OCC_SYM_RE below).
_OPTION_SYM_RE = re.compile(
    r"^(?P<ticker>[A-Z0-9]+)\s+(?P<exp>\d{4}-\d{2}-\d{2})\s+\$(?P<strike>\d+(?:\.\d+)?)\s+(?P<type>CALL|PUT)$",
    re.IGNORECASE,
)
_OCC_SYM_RE = re.compile(
    r"^(?P<ticker>[A-Z0-9]+)(?P<exp_short>\d{6})(?P<type>[CP])(?P<strike_int>\d{8})$",
    re.IGNORECASE,
)


def _parse_expiration_dte(exp_val: Any, now: Optional[datetime] = None) -> Optional[float]:
    """Parses expiration string/date/number into days to expiration (DTE).

    Returns None (never a fabricated placeholder like 30.0) when `exp_val` is
    missing or unparseable -- CONSTRAINT #4. A contract whose real expiration
    can't be determined has no real gamma/theta either; the caller
    (`_normalize_chain_data`) excludes such a contract rather than pricing it
    as if it were a 30-day option. See
    docs/known_issues/options_gex_100x_dollar_scaling.md for the historical
    default this replaces.
    """
    if exp_val is None:
        return None

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
            return None

    return None


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
    excluded_missing_iv = 0
    excluded_missing_dte = 0

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

        # Extract Implied Volatility (sigma). CONSTRAINT #4: a missing,
        # non-positive, or unparseable IV means this contract's gamma is
        # genuinely uncomputable -- exclude the contract rather than
        # substituting a fabricated 25% placeholder, which previously
        # silently mis-stated its real contribution to every downstream GEX
        # figure with no diagnostic trace. A real yfinance chain routinely
        # reports impliedVolatility=0.0 for illiquid/stale-quote strikes, so
        # this path is reachable on live data, not just malformed test
        # fixtures. See docs/known_issues/options_gex_100x_dollar_scaling.md.
        iv_raw = (
            d.get("implied_volatility")
            or d.get("impliedVolatility")
            or d.get("iv")
            or d.get("sigma")
            or d.get("volatility")
        )
        if iv_raw is None:
            excluded_missing_iv += 1
            continue
        try:
            sigma = float(iv_raw)
            if sigma > 5.0 and sigma <= 500.0:  # Percentage format e.g. 25.0% -> 0.25
                sigma = sigma / 100.0
            if sigma <= _DEGENERATE_THRESHOLD or np.isnan(sigma):
                excluded_missing_iv += 1
                continue
        except (ValueError, TypeError):
            excluded_missing_iv += 1
            continue

        # Extract Expiration / DTE. Same CONSTRAINT #4 reasoning as IV above
        # -- a contract whose expiration can't be determined has no real
        # theta/gamma either, and is excluded rather than priced as if it
        # were a fabricated 30-day option.
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
        if dte is None:
            excluded_missing_dte += 1
            continue
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

    if excluded_missing_iv or excluded_missing_dte:
        logger.warning(
            "options_gex: excluded %d contract(s) with unusable IV and %d "
            "with unresolvable expiration from a %d-record chain (never "
            "fabricated a placeholder sigma/DTE for them)",
            excluded_missing_iv,
            excluded_missing_dte,
            len(raw_list),
        )

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
    Computes total aggregate Dollar Net GEX (per 1% underlying move -- see
    module docstring's "Derivation of the 0.01 factor") across the option
    chain at a candidate spot price $S$.

    $$NetGEX(S) = 0.01 \\left[ \\sum_{c \\in \\text{Calls}} \\Gamma_c(S) \\cdot OI_c \\cdot 100 \\cdot S^2 - \\sum_{p \\in \\text{Puts}} \\Gamma_p(S) \\cdot OI_p \\cdot 100 \\cdot S^2 \\right]$$

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
    spot_sq = spot * spot * contract_multiplier * PERCENT_MOVE_SCALING_FACTOR

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
    search_range_pct: Optional[float] = None,
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
    search_range_pct: Relative search radius. Defaults to
        `settings.OPTIONS_GEX_SEARCH_RANGE_PCT` (0.20 for $\\pm 20\\%$) when
        not explicitly provided.
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
    resolved_search_range_pct = (
        float(search_range_pct)
        if search_range_pct is not None
        else float(getattr(settings, "OPTIONS_GEX_SEARCH_RANGE_PCT", DEFAULT_SEARCH_RANGE_PCT))
    )
    search_pct = max(0.05, resolved_search_range_pct)
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
    spot_sq = spot_price * spot_price * contract_multiplier * PERCENT_MOVE_SCALING_FACTOR

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
    search_range_pct: Optional[float] = None,
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
    search_range_pct: Optional[float] = None,
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


def _flatten_provider_chain_entry(chain_obj: Any, expiration: str) -> List[Dict[str, Any]]:
    """Normalizes ONE per-expiration options-chain provider response into a flat list
    of dict records tagged with `option_type` + `expiration`, ready for
    `_normalize_chain_data`.

    `data.market_data.CompositeOptionsProvider.fetch_options_chain(symbol, expiration)`
    is backed by yfinance's `Ticker.option_chain(expiration)`, which returns an
    `Options` namedtuple carrying SEPARATE `.calls`/`.puts` DataFrames (neither a bare
    `pd.DataFrame` nor a `list`/`tuple` of records) -- a shape `_normalize_chain_data`
    has never understood (it only accepts a `pd.DataFrame` or a `list`/`tuple`,
    `else: return []`). Passing that namedtuple straight through silently produced an
    "Empty or unparseable option chain data" diagnostic on every real (non-empty,
    correctly-entitled) chain fetch -- this is the fix, not a `_normalize_chain_data`
    contract change, so every existing caller of that function is unaffected.

    Also tolerates a bare DataFrame or a list of dict-like records so a future
    options-chain provider swap (e.g. FMP) degrades gracefully rather than silently
    re-breaking this path. Never raises (CONSTRAINT #6) -- any unrecognized shape
    degrades to an empty list.
    """
    if chain_obj is None:
        return []

    out: List[Dict[str, Any]] = []
    try:
        calls_df = getattr(chain_obj, "calls", None)
        puts_df = getattr(chain_obj, "puts", None)
        if isinstance(calls_df, pd.DataFrame) or isinstance(puts_df, pd.DataFrame):
            # yfinance-shaped: Options(calls=DataFrame, puts=DataFrame, underlying=...)
            if isinstance(calls_df, pd.DataFrame) and not calls_df.empty:
                for rec in calls_df.to_dict(orient="records"):
                    rec["option_type"] = "CALL"
                    rec.setdefault("expiration", expiration)
                    out.append(rec)
            if isinstance(puts_df, pd.DataFrame) and not puts_df.empty:
                for rec in puts_df.to_dict(orient="records"):
                    rec["option_type"] = "PUT"
                    rec.setdefault("expiration", expiration)
                    out.append(rec)
            return out

        if isinstance(chain_obj, pd.DataFrame):
            if chain_obj.empty:
                return []
            for rec in chain_obj.to_dict(orient="records"):
                rec.setdefault("expiration", expiration)
                out.append(rec)
            return out

        if isinstance(chain_obj, (list, tuple)):
            for item in chain_obj:
                if isinstance(item, dict):
                    d = dict(item)
                    d.setdefault("expiration", expiration)
                    out.append(d)
            return out
    except Exception:
        return []

    return out


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
    # CONSTRAINT #4 (honesty): a live quote failure must degrade to an honest
    # "no price available" state, never a fabricated per-symbol literal --
    # matching pilots/options_hedging.py's refuse-rather-than-fabricate fix for
    # its own hardcoded $500 SPY fallback. A hardcoded table here (previously
    # SPY=500.0, NVDA=125.0, AAPL=150.0, QQQ=450.0, IWM=200.0, TSLA=220.0,
    # else 100.0) would silently drift further from reality every day and
    # produce a Net GEX / Zero-Gamma-Flip / Gamma Wall profile presented as if
    # it were real market structure for that symbol. `calculate_gex_profile`
    # already has a tested, honest degenerate-spot-price path (spot_price <=
    # 0 -> net_gex=0.0, zero_gamma_flip=None, diagnostics.error set) that this
    # now falls through to instead.
    if spot_price is None or spot_price <= 0:
        try:
            from data.market_data import get_provider
            market_provider = get_provider()
            if market_provider is not None:
                quote = market_provider.get_latest_quote(clean_sym)
                if quote and getattr(quote, "price", 0) and float(quote.price) > 0:
                    spot_price = float(quote.price)
                else:
                    spot_price = None
        except Exception:
            spot_price = None

    spot_price_unavailable = spot_price is None or spot_price <= 0
    if spot_price_unavailable:
        spot_price = 0.0

    # 2. Resolve options chain
    chain_data = None
    chain_source = "live"
    if not spot_price_unavailable:
        try:
            from data.market_data import get_options_provider
            options_provider = get_options_provider()
            if options_provider is not None:
                expirations = options_provider.fetch_options_chain(clean_sym)
                if expirations and isinstance(expirations, list):
                    flat_records: List[Dict[str, Any]] = []
                    for exp in expirations[:5]:
                        c = options_provider.fetch_options_chain(clean_sym, exp)
                        flat_records.extend(_flatten_provider_chain_entry(c, str(exp)))
                    if flat_records:
                        chain_data = flat_records
        except Exception:
            chain_data = None

    if not chain_data:
        # No real chain resolvable (no live spot, provider failure, or empty
        # chain) -- fall back to an illustrative synthetic chain rather than
        # raising (CONSTRAINT #6), but flag it via the response's `chain_source`
        # key (below) so callers never mistake this for a genuine
        # market-structure read (CONSTRAINT #4). Uses spot_price=0 only when
        # no real price is available either; generate_synthetic_options_chain
        # requires a positive spot to build a strike ladder around, so use its
        # own documented default (500.0) purely as a strike-grid anchor for the
        # illustrative chain -- never presented as the resolved `spot_price`
        # in the response, which stays honestly 0.0 below.
        chain_source = "synthetic"
        chain_data = generate_synthetic_options_chain(
            spot_price=spot_price if spot_price > 0 else 500.0,
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
    # res.net_gex is already the "dollar hedging flow per 1% move" figure --
    # PERCENT_MOVE_SCALING_FACTOR (0.01) is now applied once, upstream, at
    # aggregation (calculate_strike_gex / compute_total_net_gex_at_spot), not
    # here. Previously this line re-applied *0.01 on top of an unscaled
    # net_gex, which happened to make dealer_hedging_flow correct while
    # net_gex/call_gex/put_gex/strikes[].*_gex were left 100x too large --
    # see docs/known_issues/options_gex_100x_dollar_scaling.md.
    dealer_dollars = round(res.net_gex, 2)
    dealer_shares = round(dealer_dollars / spot_price, 2) if spot_price > 0 else 0.0
    res_dict["dealer_hedging_flow"] = dealer_dollars
    res_dict["dealer_hedging_per_1pct_move_dollars"] = dealer_dollars
    res_dict["dealer_hedging_shares_per_1pct_move"] = dealer_shares
    res_dict["strikes"] = res.strikes_profile
    res_dict["as_of"] = res.timestamp or datetime.now(timezone.utc).isoformat()
    res_dict["spot_price_source"] = "unavailable" if spot_price_unavailable else "live"
    res_dict["chain_source"] = chain_source
    return res_dict

