"""
pilots/vol_mispricing.py — Volatility Mispricing Scanner & Multi-Leg Strategy Builder.
=====================================================================================

Institutional quantitative options engine for identifying relative volatility
mispricings across strike geometry and constructing defined-risk multi-leg trades.

Key Capabilities:
1. Strike-Dimension Mispricing Spread:
    For every strike in the options chain, calculates:
    Mispricing Spread = Market IV - Fair IV (where Fair IV is forecasted via
    HAR-RV, GJR-GARCH, or custom vol surfaces).
2. Rich / Cheap Classification:
    - Overvalued (Rich) Strikes: Spread >= +0.03 (+3.0% vol points)
      -> Recommends Credit Spreads (Bull Put, Bear Call) & Delta-Neutral Iron Condors.
    - Undervalued (Cheap) Strikes: Spread <= -0.03 (-3.0% vol points)
      -> Recommends Debit Spreads (Bull Call, Bear Put) & Long Straddles / Strangles / Convexity.
    - Fair / Neutral Strikes: -0.03 < Spread < +0.03.
3. Quantitative Multi-Leg Trade Construction:
    Builds actionable defined-risk multi-leg options candidate trades with
    comprehensive Greeks (Delta, Gamma, Vega, Theta), net debit/credit, max profit,
    max loss, risk/reward ratio, and breakeven boundaries.
4. Flexible Input Compatibility:
    Parses DataFrames, dicts, yfinance-style OptionsChain objects, and lists.

Design Invariants:
* **AST-Safe (CONSTRAINTS #1 & #3)** — Pure compute/read module. Never imports heavy engines
  (`processing_engine`, `technical_options_engine`, `strategy_engine`, `macro_engine`,
   `forecasting_engine`, `main_orchestrator`, `desktop`).
* **Honesty (CONSTRAINT #4)** — Preserves `None` for uncomputable metrics (missing quotes/IVs),
  never fabricates fake zeros or synthetic edge.
* **Never Raises (CONSTRAINT #6)** — Degrades gracefully on malformed, empty, or one-sided chains.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
import logging
import math
import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm

from pilots.har_volatility import get_har_volatility_forecast
from settings import settings

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_RICH_VOL_THRESHOLD",
    "DEFAULT_CHEAP_VOL_THRESHOLD",
    "StrikeMispricingRecord",
    "StrategyLeg",
    "CandidateStrategyTrade",
    "MispricingSummary",
    "MispricingAnalysis",
    "calculate_strike_mispricing_spread",
    "classify_strike_mispricing",
    "calculate_black_scholes_greeks_and_price",
    "implied_volatility_from_price",
    "extract_chain_contracts",
    "build_candidate_strategy_trades",
    "evaluate_strike_mispricing",
    "get_volatility_mispricing_data",
    "to_vol_mispricing_response",
    "execute_vol_mispricing_trade",
]

# Standard volatility spread thresholds (in vol decimal units)
DEFAULT_RICH_VOL_THRESHOLD = 0.03   # +3.0% vol points (e.g., 0.28 vs 0.25 -> +0.03 => Rich)
DEFAULT_CHEAP_VOL_THRESHOLD = -0.03 # -3.0% vol points (e.g., 0.22 vs 0.25 -> -0.03 => Cheap)
DEFAULT_RISK_FREE_RATE = 0.045
TRADING_DAYS_PER_YEAR = 252.0
_DEGENERATE_THRESHOLD = 1e-12


# ---------------------------------------------------------------------------
# Data Models / DTOs
# ---------------------------------------------------------------------------


@dataclass
class StrikeMispricingRecord:
    """Individual option strike mispricing evaluation record."""

    strike: float
    option_type: str  # "call" or "put"
    expiration: Optional[str] = None
    dte: Optional[float] = None
    market_iv: Optional[float] = None
    fair_iv: Optional[float] = None
    spread: Optional[float] = None
    spread_pct: Optional[float] = None
    valuation_tag: str = "NEUTRAL"  # "RICH", "CHEAP", "NEUTRAL", or "UNKNOWN"
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid_price: Optional[float] = None
    fair_price: Optional[float] = None
    price_edge: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    vega: Optional[float] = None
    theta: Optional[float] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    contract_symbol: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


@dataclass
class StrategyLeg:
    """Single leg in a multi-leg options strategy trade."""

    symbol: str
    action: str  # "buy" or "sell"
    option_type: str  # "call" or "put"
    strike: float
    expiration: str
    unit_price: float
    market_iv: Optional[float] = None
    fair_iv: Optional[float] = None
    spread: Optional[float] = None
    delta: Optional[float] = None
    contract_symbol: Optional[str] = None
    contract: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


@dataclass
class CandidateStrategyTrade:
    """Actionable defined-risk multi-leg strategy trade recommendation."""

    strategy_type: str  # e.g., "bull_put_spread", "bear_call_spread", "iron_condor", "bull_call_spread", "long_straddle"
    name: str
    bias: str  # "neutral", "bullish", "bearish", "long_vol", "short_vol"
    edge_type: str  # "RICH_VOLATILITY_HARVEST" or "CHEAP_CONVEXITY_CAPTURE"
    legs: List[Dict[str, Any]]
    net_premium: float  # Negative for net credit received, positive for net debit paid
    is_credit: bool
    max_profit: float
    max_loss: float
    risk_reward_ratio: Optional[float] = None
    breakeven_points: List[float] = field(default_factory=list)
    net_delta: float = 0.0
    net_gamma: float = 0.0
    net_vega: float = 0.0
    net_theta: float = 0.0
    mispricing_score: float = 0.0
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


@dataclass
class MispricingSummary:
    """Aggregated volatility mispricing summary metrics."""

    total_strikes: int = 0
    total_contracts: int = 0
    rich_strikes_count: int = 0
    cheap_strikes_count: int = 0
    neutral_strikes_count: int = 0
    mean_spread: Optional[float] = None
    median_spread: Optional[float] = None
    max_rich_spread: Optional[float] = None
    max_cheap_spread: Optional[float] = None
    regime: str = "BALANCED"  # "OVERVALUED_VOLATILITY", "UNDERVALUED_VOLATILITY", "SKEWED_VOLATILITY", "BALANCED"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


@dataclass
class MispricingAnalysis:
    """Full structured output of volatility mispricing analysis."""

    symbol: Optional[str] = None
    spot_price: float = 0.0
    baseline_fair_iv: Optional[float] = None
    expiration: Optional[str] = None
    dte: Optional[float] = None
    strikes: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    candidate_trades: List[Dict[str, Any]] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        rich_cands = [t for t in self.candidate_trades if t.get("edge_type") == "RICH_VOLATILITY_HARVEST" or t.get("is_credit")]
        cheap_cands = [t for t in self.candidate_trades if t.get("edge_type") == "CHEAP_CONVEXITY_CAPTURE" or not t.get("is_credit")]
        mean_sp = self.summary.get("mean_spread", 0.0) or 0.0

        return {
            "symbol": self.symbol,
            "spot_price": self.spot_price,
            "baseline_fair_iv": self.baseline_fair_iv,
            "fair_atm_iv": self.baseline_fair_iv,
            "market_atm_iv": round((self.baseline_fair_iv + mean_sp), 4) if self.baseline_fair_iv else None,
            "iv_mispricing_spread": mean_sp,
            "regime_bias": "PREMIUM_SELLING" if mean_sp > 0.015 else ("LONG_CONVEXITY" if mean_sp < -0.015 else "NEUTRAL"),
            "expiration": self.expiration,
            "dte": self.dte,
            "total_evaluated_strikes": len(self.strikes),
            "rich_candidates_count": len(rich_cands),
            "cheap_candidates_count": len(cheap_cands),
            "rich_candidates": rich_cands,
            "cheap_candidates": cheap_cands,
            "strike_mispricings": self.strikes,
            "strikes": self.strikes,
            "summary": self.summary,
            "candidate_trades": self.candidate_trades,
            "diagnostics": self.diagnostics,
        }

    def __getitem__(self, item: str) -> Any:
        data_map = self.to_dict()
        if item in data_map:
            return data_map[item]
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(f"'{item}' not found in MispricingAnalysis")

    def get(self, key: str, default: Any = None) -> Any:
        data_map = self.to_dict()
        return data_map.get(key, getattr(self, key, default))


# ---------------------------------------------------------------------------
# Black-Scholes Pricing & Greeks Math (Self-Contained & AST-Safe)
# ---------------------------------------------------------------------------


def _get_risk_free_rate(override_r: Optional[float] = None) -> float:
    """Resolves risk-free rate from argument or settings."""
    if override_r is not None:
        return float(override_r)
    return float(
        getattr(settings, "OPTIONS_RISK_FREE_RATE", getattr(settings, "RISK_FREE_RATE", DEFAULT_RISK_FREE_RATE))
    )


def calculate_black_scholes_greeks_and_price(
    spot: float,
    strike: float,
    t_years: float,
    sigma: float,
    option_type: str = "call",
    r: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    """
    Computes Black-Scholes European theoretical price and Greeks (delegates to canonical pilots.options_risk).
    Guards against zero/negative spot, strike, sigma, or expiration.
    """
    from pilots.options_risk import calculate_black_scholes_greeks

    rate = _get_risk_free_rate(r)
    res = calculate_black_scholes_greeks(
        spot=spot,
        strike=strike,
        t_years=t_years,
        sigma=sigma,
        option_type=option_type,
        r=rate,
    )
    return {
        "price": round(res["price"], 4),
        "delta": round(res["delta"], 4),
        "gamma": round(res["gamma"], 6),
        "vega": round(res["vega_1pct"], 4),
        "theta": round(res["theta_daily"], 4),
        "intrinsic": round(res["intrinsic"], 4),
        "extrinsic": round(res["extrinsic"], 4),
    }


def implied_volatility_from_price(
    price: float,
    spot: float,
    strike: float,
    t_years: float,
    option_type: str = "call",
    r: Optional[float] = None,
    max_iter: int = 50,
    tolerance: float = 1e-5,
) -> Optional[float]:
    """
    Inverts Black-Scholes formula using Newton-Raphson with Brent's method fallback.
    Returns annualized volatility in range [0.001, 5.0] or None if uncomputable.
    """
    if price is None or math.isnan(price) or price <= 0:
        return None
    if spot <= 0 or strike <= 0 or t_years <= _DEGENERATE_THRESHOLD:
        return None

    rate = _get_risk_free_rate(r)
    opt_type = str(option_type).lower().strip()
    intrinsic = max(0.0, spot - strike) if opt_type == "call" else max(0.0, strike - spot)
    if price < intrinsic:
        return None

    try:
        sigma = math.sqrt(2.0 * math.pi / t_years) * (price / spot)
        sigma = max(0.05, min(2.0, sigma))
    except Exception:
        sigma = 0.30

    for _ in range(max_iter):
        greeks = calculate_black_scholes_greeks_and_price(spot, strike, t_years, sigma, opt_type, rate)
        p_est = greeks.get("price", 0.0) or 0.0
        diff = p_est - price
        if abs(diff) < tolerance:
            return round(float(sigma), 4)

        vega_raw = (greeks.get("vega", 0.0) or 0.0) * 100.0
        if vega_raw < 1e-8:
            break
        step = diff / vega_raw
        sigma -= step
        if sigma <= 0.001 or sigma > 5.0:
            break

    def objective(s: float) -> float:
        res = calculate_black_scholes_greeks_and_price(spot, strike, t_years, s, opt_type, rate)
        return (res.get("price", 0.0) or 0.0) - price

    try:
        f_low = objective(0.001)
        f_high = objective(5.0)
        if f_low * f_high <= 0:
            sol = brentq(objective, 0.001, 5.0, xtol=tolerance, maxiter=max_iter)
            return round(float(sol), 4)
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Mispricing Math & Contract Extraction
# ---------------------------------------------------------------------------


def _safe_float(val: Any) -> Optional[float]:
    """Safely converts val to float, guarding against methods, None, NaN, and unconvertible types."""
    if val is None or callable(val):
        return None
    try:
        f = float(val)
        return f if not math.isnan(f) else None
    except (ValueError, TypeError):
        return None


def _safe_int(val: Any) -> Optional[int]:
    """Safely converts val to int."""
    if val is None or callable(val):
        return None
    try:
        f = float(val)
        return int(f) if not math.isnan(f) else None
    except (ValueError, TypeError):
        return None


def calculate_strike_mispricing_spread(
    market_iv: Optional[float],
    fair_iv: Optional[float],
) -> Tuple[Optional[float], Optional[float]]:
    """
    Computes Volatility Mispricing Spread = Market IV - Fair IV.
    Returns (spread, spread_pct) in decimal units, or (None, None) if uncomputable.
    """
    if market_iv is None or fair_iv is None:
        return None, None
    if math.isnan(market_iv) or math.isnan(fair_iv) or market_iv <= 0 or fair_iv <= 0:
        return None, None

    spread = round(market_iv - fair_iv, 4)
    spread_pct = round(spread / fair_iv, 4) if fair_iv > 0 else None
    return spread, spread_pct


def classify_strike_mispricing(
    spread: Optional[float],
    rich_threshold: float = DEFAULT_RICH_VOL_THRESHOLD,
    cheap_threshold: float = DEFAULT_CHEAP_VOL_THRESHOLD,
) -> str:
    """
    Classifies strike valuation into RICH, CHEAP, NEUTRAL, or UNKNOWN based on spread.
    - RICH: spread >= rich_threshold (e.g., >= +0.03)
    - CHEAP: spread <= cheap_threshold (e.g., <= -0.03)
    - NEUTRAL: -0.03 < spread < +0.03
    - UNKNOWN: spread is None
    """
    if spread is None or math.isnan(spread):
        return "UNKNOWN"
    if spread >= rich_threshold:
        return "RICH"
    if spread <= cheap_threshold:
        return "CHEAP"
    return "NEUTRAL"


def _extract_contract_record(row_or_dict: Any, default_type: str = "call") -> Optional[Dict[str, Any]]:
    """Extracts standardized contract fields from a row, dict, or object."""
    if row_or_dict is None:
        return None

    def get_field(keys: Sequence[str], default: Any = None) -> Any:
        if isinstance(row_or_dict, (dict, pd.Series)):
            for k in keys:
                if k in row_or_dict:
                    v = row_or_dict[k]
                    if v is not None and not callable(v):
                        return v
        else:
            for k in keys:
                if hasattr(row_or_dict, k):
                    v = getattr(row_or_dict, k)
                    if v is not None and not callable(v):
                        return v
        return default

    # Strike
    strike_val = get_field(["strike", "Strike", "strike_price", "strikePrice"])
    strike = _safe_float(strike_val)
    if strike is None or strike <= 0:
        return None

    # Option Type
    type_val = get_field(["option_type", "type", "contractType", "side", "putCall", "optionType"])
    if type_val:
        opt_type = str(type_val).lower().strip()
        opt_type = "call" if "call" in opt_type or opt_type == "c" else ("put" if "put" in opt_type or opt_type == "p" else default_type)
    else:
        opt_type = default_type

    # Expiration
    exp_val = get_field(["expiration", "exp", "expirationDate", "expiry", "date"])
    expiration = str(exp_val).strip() if exp_val else None

    # DTE
    dte = _safe_float(get_field(["dte", "daysToExpiration", "days_to_expiration"]))
    if dte is None and expiration:
        try:
            exp_date = datetime.strptime(expiration[:10], "%Y-%m-%d").date()
            today = datetime.now(timezone.utc).date()
            diff_days = (exp_date - today).days
            if diff_days >= 0:
                dte = float(diff_days)
        except Exception:
            pass

    # Implied Volatility
    market_iv = _safe_float(get_field(["impliedVolatility", "implied_volatility", "iv", "IV", "market_iv"]))
    if market_iv is not None and market_iv <= 0:
        market_iv = None

    # Pricing quotes
    bid = _safe_float(get_field(["bid", "Bid", "bid_price", "bidPrice"]))
    ask = _safe_float(get_field(["ask", "Ask", "ask_price", "askPrice"]))
    last_price = _safe_float(get_field(["lastPrice", "last_price", "trade_price", "price", "markPrice"]))
    if last_price is None and not isinstance(row_or_dict, (pd.Series, pd.DataFrame)):
        last_price = _safe_float(get_field(["last"]))
    mid_input = _safe_float(get_field(["mid_price", "midPrice", "mid"]))

    if mid_input is not None and mid_input > 0:
        mid_price = round(mid_input, 4)
    elif bid is not None and ask is not None and bid > 0 and ask > 0:
        mid_price = round((bid + ask) / 2.0, 4)
    elif last_price is not None and last_price > 0:
        mid_price = round(last_price, 4)
    elif ask is not None and ask > 0:
        mid_price = round(ask, 4)
    elif bid is not None and bid > 0:
        mid_price = round(bid, 4)
    else:
        mid_price = None

    volume = _safe_int(get_field(["volume", "totalVolume", "vol"]))
    open_interest = _safe_int(get_field(["openInterest", "open_interest", "oi"]))

    sym_val = get_field(["contract_symbol", "contractSymbol", "symbol", "ticker"])
    contract_symbol = str(sym_val) if sym_val else None

    return {
        "strike": strike,
        "option_type": opt_type,
        "expiration": expiration,
        "dte": dte,
        "market_iv": market_iv,
        "bid": bid,
        "ask": ask,
        "last_price": last_price,
        "mid_price": mid_price,
        "volume": volume,
        "open_interest": open_interest,
        "contract_symbol": contract_symbol,
    }


def extract_chain_contracts(chain_data: Any) -> List[Dict[str, Any]]:
    """
    Extracts a flat list of contract dictionaries from arbitrary chain containers:
    - Pandas DataFrame
    - Objects with .calls and .puts attributes (yfinance OptionsChain / mocks)
    - Dict with {"calls": [...], "puts": [...]} or {"options": [...]} or {"strikes": [...]}
    - Dict mapping expiration -> list of contracts
    - List/Sequence of dicts or records
    """
    contracts: List[Dict[str, Any]] = []
    if chain_data is None:
        return contracts

    # 1. Pandas DataFrame
    if isinstance(chain_data, pd.DataFrame):
        for _, row in chain_data.iterrows():
            rec = _extract_contract_record(row)
            if rec:
                contracts.append(rec)
        return contracts

    # 2. Object with .calls and/or .puts attributes
    if hasattr(chain_data, "calls") or hasattr(chain_data, "puts"):
        calls = getattr(chain_data, "calls", None)
        puts = getattr(chain_data, "puts", None)

        if isinstance(calls, pd.DataFrame):
            for _, r in calls.iterrows():
                rec = _extract_contract_record(r, default_type="call")
                if rec:
                    contracts.append(rec)
        elif isinstance(calls, (list, tuple)):
            for c in calls:
                rec = _extract_contract_record(c, default_type="call")
                if rec:
                    contracts.append(rec)

        if isinstance(puts, pd.DataFrame):
            for _, r in puts.iterrows():
                rec = _extract_contract_record(r, default_type="put")
                if rec:
                    contracts.append(rec)
        elif isinstance(puts, (list, tuple)):
            for p in puts:
                rec = _extract_contract_record(p, default_type="put")
                if rec:
                    contracts.append(rec)
        return contracts

    # 3. Dictionary
    if isinstance(chain_data, dict):
        if "calls" in chain_data or "puts" in chain_data:
            calls_list = chain_data.get("calls") or []
            puts_list = chain_data.get("puts") or []

            if isinstance(calls_list, pd.DataFrame):
                for _, r in calls_list.iterrows():
                    rec = _extract_contract_record(r, default_type="call")
                    if rec:
                        contracts.append(rec)
            elif isinstance(calls_list, (list, tuple)):
                for c in calls_list:
                    rec = _extract_contract_record(c, default_type="call")
                    if rec:
                        contracts.append(rec)

            if isinstance(puts_list, pd.DataFrame):
                for _, r in puts_list.iterrows():
                    rec = _extract_contract_record(r, default_type="put")
                    if rec:
                        contracts.append(rec)
            elif isinstance(puts_list, (list, tuple)):
                for p in puts_list:
                    rec = _extract_contract_record(p, default_type="put")
                    if rec:
                        contracts.append(rec)
            return contracts

        if "options" in chain_data and isinstance(chain_data["options"], (list, tuple)):
            for item in chain_data["options"]:
                rec = _extract_contract_record(item)
                if rec:
                    contracts.append(rec)
            return contracts

        if "strikes" in chain_data and isinstance(chain_data["strikes"], (list, tuple)):
            for item in chain_data["strikes"]:
                rec = _extract_contract_record(item)
                if rec:
                    contracts.append(rec)
            return contracts

        for exp_key, val in chain_data.items():
            if isinstance(val, (list, tuple)):
                for c in val:
                    rec = _extract_contract_record(c)
                    if rec:
                        if not rec.get("expiration"):
                            rec["expiration"] = str(exp_key)[:10]
                        contracts.append(rec)
            elif isinstance(val, dict):
                rec = _extract_contract_record(val)
                if rec:
                    if not rec.get("expiration"):
                        rec["expiration"] = str(exp_key)[:10]
                    contracts.append(rec)
        return contracts

    # 4. List / Sequence of dicts or records
    if isinstance(chain_data, (list, tuple)):
        for item in chain_data:
            rec = _extract_contract_record(item)
            if rec:
                contracts.append(rec)
        return contracts

    return contracts


def _resolve_fair_iv(
    fair_iv_forecast: Any,
    strike: float,
    option_type: str,
    default_fair_iv: Optional[float] = None,
) -> Optional[float]:
    """
    Resolves fair IV for a specific strike from various fair_iv_forecast formats:
    - Scalar float / int
    - Dict mapping strike -> fair_iv
    - Dict with "fair_iv", "forecast_vol", or "har_rv" key
    - Callable f(strike) -> float or f(strike, opt_type) -> float
    - Object with .fair_iv or .forecast_vol attribute
    """
    if fair_iv_forecast is None:
        return default_fair_iv

    if isinstance(fair_iv_forecast, (int, float)):
        val = float(fair_iv_forecast)
        return val if val > 0 and not math.isnan(val) else None

    if callable(fair_iv_forecast):
        try:
            val = fair_iv_forecast(strike)
            if isinstance(val, (int, float)) and val > 0:
                return float(val)
        except TypeError:
            try:
                val = fair_iv_forecast(strike, option_type)
                if isinstance(val, (int, float)) and val > 0:
                    return float(val)
            except Exception:
                pass
        except Exception:
            pass
        return default_fair_iv

    if isinstance(fair_iv_forecast, dict):
        if strike in fair_iv_forecast:
            v = fair_iv_forecast[strike]
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        if str(strike) in fair_iv_forecast:
            v = fair_iv_forecast[str(strike)]
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        if (strike, option_type.lower()) in fair_iv_forecast:
            v = fair_iv_forecast[(strike, option_type.lower())]
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        for k in ["fair_iv", "forecast_vol", "har_rv", "annualized_vol", "fair_vol", "forecast_annualized_vol"]:
            if k in fair_iv_forecast:
                v = fair_iv_forecast[k]
                if isinstance(v, (int, float)) and v > 0:
                    return float(v)

    if hasattr(fair_iv_forecast, "fair_iv"):
        v = getattr(fair_iv_forecast, "fair_iv")
        if isinstance(v, (int, float)) and v > 0:
            return float(v)

    if hasattr(fair_iv_forecast, "forecast_vol"):
        v = getattr(fair_iv_forecast, "forecast_vol")
        if isinstance(v, (int, float)) and v > 0:
            return float(v)

    if hasattr(fair_iv_forecast, "forecast_annualized_vol"):
        v = getattr(fair_iv_forecast, "forecast_annualized_vol")
        if isinstance(v, (int, float)) and v > 0:
            return float(v)

    return default_fair_iv


# ---------------------------------------------------------------------------
# Multi-Leg Strategy Trade Generation
# ---------------------------------------------------------------------------


def _create_strategy_leg(
    rec: Dict[str, Any],
    action: str,
    symbol: str,
    expiration: str,
    unit_price: float,
) -> Dict[str, Any]:
    """Helper to build a standardized strategy leg dictionary."""
    strike = float(rec["strike"])
    opt_type = str(rec["option_type"]).upper()
    leg_symbol = f"{symbol} {expiration} ${strike:.2f} {opt_type}"

    return {
        "symbol": leg_symbol,
        "contract_symbol": rec.get("contract_symbol"),
        "action": action.lower(),
        "type": opt_type,
        "strike": strike,
        "expiration": expiration,
        "unit_price": round(unit_price, 4),
        "market_iv": rec.get("market_iv"),
        "fair_iv": rec.get("fair_iv"),
        "spread": rec.get("spread"),
        "delta": rec.get("delta"),
        "contract": {
            "strike": strike,
            "type": opt_type.lower(),
            "expiration": expiration,
            "bid": rec.get("bid") or 0.0,
            "ask": rec.get("ask") or 0.0,
            "lastPrice": rec.get("last_price") or unit_price,
            "impliedVolatility": rec.get("market_iv") or 0.0,
        },
    }


def build_candidate_strategy_trades(
    evaluated_records: List[Dict[str, Any]],
    spot_price: float,
    symbol: str = "TICKER",
    expiration: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Constructs actionable candidate strategy trades based on rich and cheap strikes:
    - When Rich Strikes exist (spread >= +0.03):
        - Bull Put Credit Spread
        - Bear Call Credit Spread
        - Delta-Neutral Iron Condor
    - When Cheap Strikes exist (spread <= -0.03):
        - Bull Call Debit Spread
        - Bear Put Debit Spread
        - Long Straddle / Strangle (Convexity)
    """
    candidates: List[Dict[str, Any]] = []
    if not evaluated_records or spot_price <= 0:
        return candidates

    exp_str = expiration or evaluated_records[0].get("expiration") or "2026-09-18"

    calls = sorted([r for r in evaluated_records if r.get("option_type") == "call"], key=lambda x: x["strike"])
    puts = sorted([r for r in evaluated_records if r.get("option_type") == "put"], key=lambda x: x["strike"])

    rich_calls = [c for c in calls if c.get("valuation_tag") == "RICH"]
    rich_puts = [p for p in puts if p.get("valuation_tag") == "RICH"]
    cheap_calls = [c for c in calls if c.get("valuation_tag") == "CHEAP"]
    cheap_puts = [p for p in puts if p.get("valuation_tag") == "CHEAP"]

    bull_put_trade = None
    bear_call_trade = None

    # 1. Overvalued Volatility (Rich Strikes) -> Credit Spreads & Iron Condor
    otm_rich_puts = [p for p in rich_puts if p["strike"] < spot_price]
    if not otm_rich_puts and rich_puts:
        otm_rich_puts = rich_puts

    if otm_rich_puts and len(puts) >= 2:
        short_put = max(otm_rich_puts, key=lambda x: x.get("spread") or 0.0)
        short_strike = short_put["strike"]

        lower_puts = [p for p in puts if p["strike"] < short_strike]
        if lower_puts:
            long_put = lower_puts[-1] if len(lower_puts) == 1 else lower_puts[-min(2, len(lower_puts))]
            long_strike = long_put["strike"]
            wing_width = short_strike - long_strike

            if wing_width > 0:
                short_price = short_put.get("bid") or short_put.get("mid_price") or short_put.get("last_price") or 1.0
                long_price = long_put.get("ask") or long_put.get("mid_price") or long_put.get("last_price") or 0.5
                net_credit = max(0.05, round(short_price - long_price, 2))
                max_profit = round(net_credit * 100.0, 2)
                max_loss = round(max(0.0, (wing_width - net_credit) * 100.0), 2)
                rr_ratio = round(max_profit / max_loss, 2) if max_loss > 0 else None
                breakeven = round(short_strike - net_credit, 2)

                leg_short = _create_strategy_leg(short_put, "sell", symbol, exp_str, short_price)
                leg_long = _create_strategy_leg(long_put, "buy", symbol, exp_str, long_price)

                net_delta = round((long_put.get("delta") or 0.0) - (short_put.get("delta") or 0.0), 4)
                net_gamma = round((long_put.get("gamma") or 0.0) - (short_put.get("gamma") or 0.0), 6)
                net_vega = round((long_put.get("vega") or 0.0) - (short_put.get("vega") or 0.0), 4)
                net_theta = round((long_put.get("theta") or 0.0) - (short_put.get("theta") or 0.0), 4)
                score = round((short_put.get("spread") or 0.03) * 100.0, 1)

                bull_put_trade = CandidateStrategyTrade(
                    strategy_type="bull_put_spread",
                    name=f"Bull Put Credit Spread (${long_strike:.2f}/${short_strike:.2f}P)",
                    bias="bullish_to_neutral",
                    edge_type="RICH_VOLATILITY_HARVEST",
                    legs=[leg_short, leg_long],
                    net_premium=-net_credit,
                    is_credit=True,
                    max_profit=max_profit,
                    max_loss=max_loss,
                    risk_reward_ratio=rr_ratio,
                    breakeven_points=[breakeven],
                    net_delta=net_delta,
                    net_gamma=net_gamma,
                    net_vega=net_vega,
                    net_theta=net_theta,
                    mispricing_score=score,
                    rationale=(
                        f"Harvests overvalued put volatility at strike ${short_strike:.2f} "
                        f"(Spread: +{(short_put.get('spread') or 0)*100:.1f}%). Defined risk capped by ${long_strike:.2f} wing."
                    ),
                )
                candidates.append(bull_put_trade.to_dict())

    otm_rich_calls = [c for c in rich_calls if c["strike"] > spot_price]
    if not otm_rich_calls and rich_calls:
        otm_rich_calls = rich_calls

    if otm_rich_calls and len(calls) >= 2:
        short_call = max(otm_rich_calls, key=lambda x: x.get("spread") or 0.0)
        short_strike = short_call["strike"]

        higher_calls = [c for c in calls if c["strike"] > short_strike]
        if higher_calls:
            long_call = higher_calls[0] if len(higher_calls) == 1 else higher_calls[min(1, len(higher_calls)-1)]
            long_strike = long_call["strike"]
            wing_width = long_strike - short_strike

            if wing_width > 0:
                short_price = short_call.get("bid") or short_call.get("mid_price") or short_call.get("last_price") or 1.0
                long_price = long_call.get("ask") or long_call.get("mid_price") or long_call.get("last_price") or 0.5
                net_credit = max(0.05, round(short_price - long_price, 2))
                max_profit = round(net_credit * 100.0, 2)
                max_loss = round(max(0.0, (wing_width - net_credit) * 100.0), 2)
                rr_ratio = round(max_profit / max_loss, 2) if max_loss > 0 else None
                breakeven = round(short_strike + net_credit, 2)

                leg_short = _create_strategy_leg(short_call, "sell", symbol, exp_str, short_price)
                leg_long = _create_strategy_leg(long_call, "buy", symbol, exp_str, long_price)

                net_delta = round((long_call.get("delta") or 0.0) - (short_call.get("delta") or 0.0), 4)
                net_gamma = round((long_call.get("gamma") or 0.0) - (short_call.get("gamma") or 0.0), 6)
                net_vega = round((long_call.get("vega") or 0.0) - (short_call.get("vega") or 0.0), 4)
                net_theta = round((long_call.get("theta") or 0.0) - (short_call.get("theta") or 0.0), 4)
                score = round((short_call.get("spread") or 0.03) * 100.0, 1)

                bear_call_trade = CandidateStrategyTrade(
                    strategy_type="bear_call_spread",
                    name=f"Bear Call Credit Spread (${short_strike:.2f}/${long_strike:.2f}C)",
                    bias="bearish_to_neutral",
                    edge_type="RICH_VOLATILITY_HARVEST",
                    legs=[leg_short, leg_long],
                    net_premium=-net_credit,
                    is_credit=True,
                    max_profit=max_profit,
                    max_loss=max_loss,
                    risk_reward_ratio=rr_ratio,
                    breakeven_points=[breakeven],
                    net_delta=net_delta,
                    net_gamma=net_gamma,
                    net_vega=net_vega,
                    net_theta=net_theta,
                    mispricing_score=score,
                    rationale=(
                        f"Harvests overvalued call volatility at strike ${short_strike:.2f} "
                        f"(Spread: +{(short_call.get('spread') or 0)*100:.1f}%). Defined risk capped by ${long_strike:.2f} wing."
                    ),
                )
                candidates.append(bear_call_trade.to_dict())

    if bull_put_trade and bear_call_trade:
        put_legs = bull_put_trade.legs
        call_legs = bear_call_trade.legs

        total_credit = round(abs(bull_put_trade.net_premium) + abs(bear_call_trade.net_premium), 2)
        put_width = abs(put_legs[0]["strike"] - put_legs[1]["strike"])
        call_width = abs(call_legs[1]["strike"] - call_legs[0]["strike"])
        max_wing_width = max(put_width, call_width)

        condor_max_profit = round(total_credit * 100.0, 2)
        condor_max_loss = round(max(0.0, (max_wing_width - total_credit) * 100.0), 2)
        condor_rr = round(condor_max_profit / condor_max_loss, 2) if condor_max_loss > 0 else None

        short_p_strike = put_legs[0]["strike"]
        short_c_strike = call_legs[0]["strike"]
        be_lower = round(short_p_strike - total_credit, 2)
        be_upper = round(short_c_strike + total_credit, 2)

        all_legs = [put_legs[1], put_legs[0], call_legs[0], call_legs[1]]
        condor_net_delta = round(bull_put_trade.net_delta + bear_call_trade.net_delta, 4)
        condor_net_vega = round(bull_put_trade.net_vega + bear_call_trade.net_vega, 4)
        condor_net_theta = round(bull_put_trade.net_theta + bear_call_trade.net_theta, 4)
        condor_score = round((bull_put_trade.mispricing_score + bear_call_trade.mispricing_score) / 2.0, 1)

        iron_condor = CandidateStrategyTrade(
            strategy_type="iron_condor",
            name=f"Delta-Neutral Iron Condor (${put_legs[1]['strike']:.2f}P/${short_p_strike:.2f}P/${short_c_strike:.2f}C/${call_legs[1]['strike']:.2f}C)",
            bias="neutral",
            edge_type="RICH_VOLATILITY_HARVEST",
            legs=all_legs,
            net_premium=-total_credit,
            is_credit=True,
            max_profit=condor_max_profit,
            max_loss=condor_max_loss,
            risk_reward_ratio=condor_rr,
            breakeven_points=[be_lower, be_upper],
            net_delta=condor_net_delta,
            net_vega=condor_net_vega,
            net_theta=condor_net_theta,
            mispricing_score=condor_score,
            rationale=(
                f"Harvests dual overpricing across put (${short_p_strike:.2f}) and call (${short_c_strike:.2f}) wings. "
                f"Delta-neutral premium capture with defined risk."
            ),
        )
        candidates.insert(0, iron_condor.to_dict())

    # 2. Undervalued Volatility (Cheap Strikes) -> Debit Spreads & Long Vol
    if cheap_calls and len(calls) >= 2:
        long_call = min(cheap_calls, key=lambda x: x.get("spread") or 0.0)
        long_strike = long_call["strike"]

        higher_calls = [c for c in calls if c["strike"] > long_strike]
        if higher_calls:
            short_call = higher_calls[0] if len(higher_calls) == 1 else higher_calls[min(1, len(higher_calls)-1)]
            short_strike = short_call["strike"]
            spread_width = short_strike - long_strike

            if spread_width > 0:
                long_price = long_call.get("ask") or long_call.get("mid_price") or long_call.get("last_price") or 2.0
                short_price = short_call.get("bid") or short_call.get("mid_price") or short_call.get("last_price") or 1.0
                net_debit = max(0.05, round(long_price - short_price, 2))
                max_profit = round(max(0.0, (spread_width - net_debit) * 100.0), 2)
                max_loss = round(net_debit * 100.0, 2)
                rr_ratio = round(max_profit / max_loss, 2) if max_loss > 0 else None
                breakeven = round(long_strike + net_debit, 2)

                leg_long = _create_strategy_leg(long_call, "buy", symbol, exp_str, long_price)
                leg_short = _create_strategy_leg(short_call, "sell", symbol, exp_str, short_price)

                net_delta = round((long_call.get("delta") or 0.0) - (short_call.get("delta") or 0.0), 4)
                net_gamma = round((long_call.get("gamma") or 0.0) - (short_call.get("gamma") or 0.0), 6)
                net_vega = round((long_call.get("vega") or 0.0) - (short_call.get("vega") or 0.0), 4)
                net_theta = round((long_call.get("theta") or 0.0) - (short_call.get("theta") or 0.0), 4)
                score = round(abs(long_call.get("spread") or -0.03) * 100.0, 1)

                bull_call_trade = CandidateStrategyTrade(
                    strategy_type="bull_call_spread",
                    name=f"Bull Call Debit Spread (${long_strike:.2f}/${short_strike:.2f}C)",
                    bias="bullish",
                    edge_type="CHEAP_CONVEXITY_CAPTURE",
                    legs=[leg_long, leg_short],
                    net_premium=net_debit,
                    is_credit=False,
                    max_profit=max_profit,
                    max_loss=max_loss,
                    risk_reward_ratio=rr_ratio,
                    breakeven_points=[breakeven],
                    net_delta=net_delta,
                    net_gamma=net_gamma,
                    net_vega=net_vega,
                    net_theta=net_theta,
                    mispricing_score=score,
                    rationale=(
                        f"Exploits undervalued call volatility at strike ${long_strike:.2f} "
                        f"(Discount: {(long_call.get('spread') or 0)*100:.1f}%). Financed via ${short_strike:.2f} short call."
                    ),
                )
                candidates.append(bull_call_trade.to_dict())

    if cheap_puts and len(puts) >= 2:
        long_put = min(cheap_puts, key=lambda x: x.get("spread") or 0.0)
        long_strike = long_put["strike"]

        lower_puts = [p for p in puts if p["strike"] < long_strike]
        if lower_puts:
            short_put = lower_puts[-1] if len(lower_puts) == 1 else lower_puts[-min(2, len(lower_puts))]
            short_strike = short_put["strike"]
            spread_width = long_strike - short_strike

            if spread_width > 0:
                long_price = long_put.get("ask") or long_put.get("mid_price") or long_put.get("last_price") or 2.0
                short_price = short_put.get("bid") or short_put.get("mid_price") or short_put.get("last_price") or 1.0
                net_debit = max(0.05, round(long_price - short_price, 2))
                max_profit = round(max(0.0, (spread_width - net_debit) * 100.0), 2)
                max_loss = round(net_debit * 100.0, 2)
                rr_ratio = round(max_profit / max_loss, 2) if max_loss > 0 else None
                breakeven = round(long_strike - net_debit, 2)

                leg_long = _create_strategy_leg(long_put, "buy", symbol, exp_str, long_price)
                leg_short = _create_strategy_leg(short_put, "sell", symbol, exp_str, short_price)

                net_delta = round((long_put.get("delta") or 0.0) - (short_put.get("delta") or 0.0), 4)
                net_gamma = round((long_put.get("gamma") or 0.0) - (short_put.get("gamma") or 0.0), 6)
                net_vega = round((long_put.get("vega") or 0.0) - (short_put.get("vega") or 0.0), 4)
                net_theta = round((long_put.get("theta") or 0.0) - (short_put.get("theta") or 0.0), 4)
                score = round(abs(long_put.get("spread") or -0.03) * 100.0, 1)

                bear_put_trade = CandidateStrategyTrade(
                    strategy_type="bear_put_spread",
                    name=f"Bear Put Debit Spread (${short_strike:.2f}/${long_strike:.2f}P)",
                    bias="bearish",
                    edge_type="CHEAP_CONVEXITY_CAPTURE",
                    legs=[leg_long, leg_short],
                    net_premium=net_debit,
                    is_credit=False,
                    max_profit=max_profit,
                    max_loss=max_loss,
                    risk_reward_ratio=rr_ratio,
                    breakeven_points=[breakeven],
                    net_delta=net_delta,
                    net_gamma=net_gamma,
                    net_vega=net_vega,
                    net_theta=net_theta,
                    mispricing_score=score,
                    rationale=(
                        f"Exploits undervalued put volatility at strike ${long_strike:.2f} "
                        f"(Discount: {(long_put.get('spread') or 0)*100:.1f}%). Financed via ${short_strike:.2f} short put."
                    ),
                )
                candidates.append(bear_put_trade.to_dict())

    if (cheap_calls or cheap_puts) and calls and puts:
        target_call = min(cheap_calls, key=lambda x: abs(x["strike"] - spot_price)) if cheap_calls else min(calls, key=lambda x: abs(x["strike"] - spot_price))
        target_put = min(cheap_puts, key=lambda x: abs(x["strike"] - spot_price)) if cheap_puts else min(puts, key=lambda x: abs(x["strike"] - spot_price))

        call_price = target_call.get("ask") or target_call.get("mid_price") or target_call.get("last_price") or 2.0
        put_price = target_put.get("ask") or target_put.get("mid_price") or target_put.get("last_price") or 2.0
        total_debit = round(call_price + put_price, 2)

        is_straddle = target_call["strike"] == target_put["strike"]
        strategy_name = (
            f"Long Straddle (${target_call['strike']:.2f} ATM)" if is_straddle
            else f"Long Strangle (${target_put['strike']:.2f}P/${target_call['strike']:.2f}C)"
        )

        be_low = round(target_put["strike"] - total_debit, 2)
        be_high = round(target_call["strike"] + total_debit, 2)

        leg_c = _create_strategy_leg(target_call, "buy", symbol, exp_str, call_price)
        leg_p = _create_strategy_leg(target_put, "buy", symbol, exp_str, put_price)

        c_delta = target_call.get("delta") or 0.5
        p_delta = target_put.get("delta") or -0.5
        c_vega = target_call.get("vega") or 0.05
        p_vega = target_put.get("vega") or 0.05
        c_theta = target_call.get("theta") or -0.05
        p_theta = target_put.get("theta") or -0.05

        score = round(max(abs(target_call.get("spread") or 0.0), abs(target_put.get("spread") or 0.0)) * 100.0, 1)

        long_vol_trade = CandidateStrategyTrade(
            strategy_type="long_straddle" if is_straddle else "long_strangle",
            name=strategy_name,
            bias="long_vol",
            edge_type="CHEAP_CONVEXITY_CAPTURE",
            legs=[leg_c, leg_p],
            net_premium=total_debit,
            is_credit=False,
            max_profit=999999.0,
            max_loss=round(total_debit * 100.0, 2),
            risk_reward_ratio=None,
            breakeven_points=[be_low, be_high],
            net_delta=round(c_delta + p_delta, 4),
            net_gamma=round((target_call.get("gamma") or 0.0) + (target_put.get("gamma") or 0.0), 6),
            net_vega=round(c_vega + p_vega, 4),
            net_theta=round(c_theta + p_theta, 4),
            mispricing_score=score,
            rationale=(
                f"Captures explosive positive gamma and vega expansion at discounted market IV "
                f"(${target_put['strike']:.2f}P / ${target_call['strike']:.2f}C)."
            ),
        )
        candidates.append(long_vol_trade.to_dict())

    return candidates


# ---------------------------------------------------------------------------
# Main Evaluator Function
# ---------------------------------------------------------------------------


def evaluate_strike_mispricing(
    chain_data: Any,
    spot_price: float,
    fair_iv_forecast: Any,
    *,
    rich_threshold: float = DEFAULT_RICH_VOL_THRESHOLD,
    cheap_threshold: float = DEFAULT_CHEAP_VOL_THRESHOLD,
    symbol: Optional[str] = None,
    expiration: Optional[str] = None,
    dte: Optional[Union[int, float]] = None,
    risk_free_rate: Optional[float] = None,
) -> MispricingAnalysis:
    """
    Evaluates volatility mispricing across option strikes in chain_data against fair_iv_forecast:
    1. Computes Mispricing Spread = Market IV - Fair IV for every valid strike contract.
    2. Identifies Overvalued (Rich) strikes: Spread >= +0.03 (recommend Credit Spreads / Iron Condors).
    3. Identifies Undervalued (Cheap) strikes: Spread <= -0.03 (recommend Debit Spreads / Straddles / Convexity).
    4. Generates structured MispricingAnalysis with strike table, summary metrics, and candidate strategy trades.

    Parameters:
    - chain_data: DataFrame, dict, yfinance-style OptionsChain, or list of contract records.
    - spot_price: Current underlying spot price (float > 0).
    - fair_iv_forecast: Forecasted fair IV (scalar float, dict, callable, or object).
    - rich_threshold: Spread threshold for overvalued flags (default +0.03 = +3.0% vol points).
    - cheap_threshold: Spread threshold for undervalued flags (default -0.03 = -3.0% vol points).
    - symbol: Optional underlying ticker symbol.
    - expiration: Optional expiration date string.
    - dte: Optional days to expiration.
    - risk_free_rate: Optional risk-free interest rate override.

    Returns:
    - MispricingAnalysis structured container. Never raises on malformed or empty data.
    """
    diagnostics: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "notes": [],
    }

    if spot_price is None or math.isnan(spot_price) or spot_price <= 0:
        diagnostics["notes"].append("Invalid spot price (must be > 0).")
        return MispricingAnalysis(
            symbol=symbol,
            spot_price=0.0,
            summary=MispricingSummary(regime="INVALID_SPOT").to_dict(),
            diagnostics=diagnostics,
        )

    raw_contracts = extract_chain_contracts(chain_data)
    if not raw_contracts:
        diagnostics["notes"].append("Empty or unparsable options chain data provided.")
        return MispricingAnalysis(
            symbol=symbol,
            spot_price=spot_price,
            summary=MispricingSummary(regime="EMPTY_CHAIN").to_dict(),
            diagnostics=diagnostics,
        )

    inferred_exp = expiration or raw_contracts[0].get("expiration") or "2026-09-18"
    inferred_dte = dte if dte is not None else raw_contracts[0].get("dte")
    if inferred_dte is None:
        try:
            exp_d = datetime.strptime(str(inferred_exp)[:10], "%Y-%m-%d").date()
            today_d = datetime.now(timezone.utc).date()
            inferred_dte = max(1.0, float((exp_d - today_d).days))
        except Exception:
            inferred_dte = 30.0

    t_years = max(_DEGENERATE_THRESHOLD, float(inferred_dte) / 365.0)
    rate = _get_risk_free_rate(risk_free_rate)

    baseline_fair_iv = _resolve_fair_iv(fair_iv_forecast, spot_price, "call", default_fair_iv=None)

    strike_records: List[Dict[str, Any]] = []
    spreads_list: List[float] = []
    rich_count = 0
    cheap_count = 0
    neutral_count = 0

    for contract in raw_contracts:
        strike = float(contract["strike"])
        opt_type = str(contract["option_type"]).lower()
        exp = contract.get("expiration") or inferred_exp
        c_dte = contract.get("dte") if contract.get("dte") is not None else inferred_dte
        c_t_years = max(_DEGENERATE_THRESHOLD, float(c_dte) / 365.0)

        market_iv = contract.get("market_iv")
        mid_price = contract.get("mid_price")
        bid = contract.get("bid")
        ask = contract.get("ask")
        last_price = contract.get("last_price")

        if (market_iv is None or market_iv <= 0) and mid_price is not None and mid_price > 0:
            market_iv = implied_volatility_from_price(mid_price, spot_price, strike, c_t_years, opt_type, rate)

        fair_iv = _resolve_fair_iv(fair_iv_forecast, strike, opt_type, default_fair_iv=baseline_fair_iv)

        spread, spread_pct = calculate_strike_mispricing_spread(market_iv, fair_iv)
        valuation_tag = classify_strike_mispricing(spread, rich_threshold, cheap_threshold)

        if valuation_tag == "RICH":
            rich_count += 1
        elif valuation_tag == "CHEAP":
            cheap_count += 1
        elif valuation_tag == "NEUTRAL":
            neutral_count += 1

        if spread is not None:
            spreads_list.append(spread)

        effective_iv = fair_iv or market_iv or 0.25
        greeks_fair = calculate_black_scholes_greeks_and_price(spot_price, strike, c_t_years, effective_iv, opt_type, rate)
        fair_price = greeks_fair.get("price")

        price_edge = None
        if mid_price is not None and fair_price is not None:
            price_edge = round(mid_price - fair_price, 4)

        greeks_eval_iv = market_iv or effective_iv
        greeks_mkt = calculate_black_scholes_greeks_and_price(spot_price, strike, c_t_years, greeks_eval_iv, opt_type, rate)

        record = StrikeMispricingRecord(
            strike=strike,
            option_type=opt_type,
            expiration=exp,
            dte=c_dte,
            market_iv=market_iv,
            fair_iv=fair_iv,
            spread=spread,
            spread_pct=spread_pct,
            valuation_tag=valuation_tag,
            bid=bid,
            ask=ask,
            mid_price=mid_price,
            fair_price=fair_price,
            price_edge=price_edge,
            delta=greeks_mkt.get("delta"),
            gamma=greeks_mkt.get("gamma"),
            vega=greeks_mkt.get("vega"),
            theta=greeks_mkt.get("theta"),
            volume=contract.get("volume"),
            open_interest=contract.get("open_interest"),
            contract_symbol=contract.get("contract_symbol"),
        )
        strike_records.append(record.to_dict())

    total_contracts = len(strike_records)
    unique_strikes = len(set(r["strike"] for r in strike_records))

    mean_spread = round(float(np.mean(spreads_list)), 4) if spreads_list else None
    median_spread = round(float(np.median(spreads_list)), 4) if spreads_list else None

    rich_spreads = [s for s in spreads_list if s >= rich_threshold]
    cheap_spreads = [s for s in spreads_list if s <= cheap_threshold]
    max_rich = round(max(rich_spreads), 4) if rich_spreads else None
    max_cheap = round(min(cheap_spreads), 4) if cheap_spreads else None

    if rich_count > cheap_count and rich_count > 0:
        regime = "OVERVALUED_VOLATILITY"
    elif cheap_count > rich_count and cheap_count > 0:
        regime = "UNDERVALUED_VOLATILITY"
    elif rich_count > 0 and cheap_count > 0:
        regime = "SKEWED_VOLATILITY"
    else:
        regime = "BALANCED"

    summary = MispricingSummary(
        total_strikes=unique_strikes,
        total_contracts=total_contracts,
        rich_strikes_count=rich_count,
        cheap_strikes_count=cheap_count,
        neutral_strikes_count=neutral_count,
        mean_spread=mean_spread,
        median_spread=median_spread,
        max_rich_spread=max_rich,
        max_cheap_spread=max_cheap,
        regime=regime,
    )

    candidate_trades = build_candidate_strategy_trades(
        strike_records,
        spot_price=spot_price,
        symbol=symbol or "TICKER",
        expiration=inferred_exp,
    )

    diagnostics["total_contracts_evaluated"] = total_contracts
    diagnostics["unique_strikes"] = unique_strikes
    diagnostics["rich_threshold"] = rich_threshold
    diagnostics["cheap_threshold"] = cheap_threshold

    return MispricingAnalysis(
        symbol=symbol,
        spot_price=spot_price,
        baseline_fair_iv=baseline_fair_iv,
        expiration=inferred_exp,
        dte=inferred_dte,
        strikes=strike_records,
        summary=summary.to_dict(),
        candidate_trades=candidate_trades,
        diagnostics=diagnostics,
    )


def get_volatility_mispricing_data(
    symbol: str,
    market_provider: Optional[Any] = None,
    options_provider: Optional[Any] = None,
    horizon_days: int = 30,
) -> Dict[str, Any]:
    """
    Resolves live/historical data, HAR-RV volatility forecast, and chain quotes to evaluate options mispricing.
    """
    sym = symbol.upper().strip()
    spot_price = None

    if market_provider is None:
        try:
            from data.market_data import get_provider
            market_provider = get_provider()
        except Exception:
            market_provider = None

    if options_provider is None:
        try:
            from data.market_data import get_options_provider
            options_provider = get_options_provider()
        except Exception:
            options_provider = None

    if market_provider is not None:
        try:
            quote = market_provider.get_latest_quote(sym)
            if quote and getattr(quote, "price", 0) and float(quote.price) > 0:
                spot_price = float(quote.price)
        except Exception:
            spot_price = None

    if spot_price is not None and spot_price <= 0:
        spot_price = None

    har_forecast = get_har_volatility_forecast(sym, horizon_days=horizon_days, market_provider=market_provider)
    fair_iv = har_forecast.get("forecast_annualized_vol") or 0.20

    chain_data = None
    if options_provider is not None:
        try:
            expirations = options_provider.fetch_options_chain(sym)
            if expirations and isinstance(expirations, list):
                chain_map = {}
                for exp in expirations[:3]:
                    c = options_provider.fetch_options_chain(sym, exp)
                    if c:
                        chain_map[str(exp)] = c
                if chain_map:
                    chain_data = chain_map
        except Exception:
            chain_data = None

    raw_extracted = extract_chain_contracts(chain_data) if chain_data else []
    if not raw_extracted:
        if spot_price is None:
            return {"ok": False, "message": "No option chain contracts retrieved and spot price is unavailable for mock fallback", "symbol": sym, "data": None}
        exp_date = (date.today() + timedelta(days=horizon_days)).isoformat()
        moneyness_levels = [0.85, 0.90, 0.95, 0.98, 1.00, 1.02, 1.05, 1.10, 1.15]
        chain_list = []
        for m in moneyness_levels:
            k = round(spot_price * m, 2)
            log_m = math.log(m)
            skew_mult = 0.12 if m < 1.0 else -0.04
            convexity_mult = 0.35
            mkt_iv = max(0.06, fair_iv + (skew_mult * (-log_m)) + (convexity_mult * (log_m ** 2)))
            chain_list.append({
                "strike": k,
                "option_type": "call",
                "market_iv": round(mkt_iv, 4),
                "expiration": exp_date,
                "dte": horizon_days,
                "bid": max(0.05, round(abs(spot_price - k) * 0.1, 2)),
                "ask": max(0.10, round(abs(spot_price - k) * 0.1 + 0.05, 2)),
            })
            chain_list.append({
                "strike": k,
                "option_type": "put",
                "market_iv": round(mkt_iv + 0.015, 4),
                "expiration": exp_date,
                "dte": horizon_days,
                "bid": max(0.05, round(abs(spot_price - k) * 0.1, 2)),
                "ask": max(0.10, round(abs(spot_price - k) * 0.1 + 0.05, 2)),
            })
        chain_data = chain_list

    analysis = evaluate_strike_mispricing(
        chain_data=chain_data,
        spot_price=spot_price,
        fair_iv_forecast=fair_iv,
        symbol=sym,
        dte=horizon_days,
    )
    result = analysis.to_dict()
    result["as_of"] = datetime.now(timezone.utc).isoformat()
    result["har_forecast_summary"] = har_forecast
    return result


# Per-strike valuation_tag -> the frontend's suggested_action literal union. A faithful,
# deterministic relabeling of the SAME classification the backend already computed --
# not a fabricated recommendation (CONSTRAINT #4).
_VALUATION_TAG_TO_ACTION = {
    "RICH": "SELL_PREMIUM",
    "CHEAP": "BUY_GAMMA",
    "NEUTRAL": "NEUTRAL",
}


def to_vol_mispricing_response(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reshapes get_volatility_mispricing_data()'s internal result (MispricingAnalysis.to_dict()
    -- baseline_fair_iv/rich_candidates_count/strike_mispricings with valuation_tag/spread --
    the shape every existing test in tests/test_vol_mispricing.py asserts on) into the
    VolMispricingResponse contract webapp/src/api/types.ts and
    webapp/src/components/options/VolForecastScanner.tsx already agree on
    (fair_iv_baseline/rich_strikes_count/strikes with classification/iv_spread/
    suggested_action/trade_recommendations).

    Two field-NAME mismatches here were silently breaking a whole feature rather than
    crashing (CONSTRAINT #4 still applies -- a silently-wrong "0 rich strikes" or an
    always-empty Rich/Cheap filter is exactly the kind of unannounced-fabrication failure
    mode this constraint exists to prevent): every strike's real `valuation_tag` was never
    named `classification` on the wire, so `s.classification === "RICH"` was always false
    and the Rich/Cheap strike filter buttons silently returned zero results forever, no
    matter how many strikes the backend had genuinely classified.

    Kept as a separate step from get_volatility_mispricing_data() itself (applied at the
    API handler for GET /pilots/options/forecast/mispricing instead) so every existing
    caller/test of the pure evaluation function is unaffected.
    """
    summary = raw.get("summary") or {}

    def _to_strike(s: Dict[str, Any]) -> Dict[str, Any]:
        classification = s.get("valuation_tag") or "UNKNOWN"
        item: Dict[str, Any] = {
            "strike": s.get("strike"),
            "option_type": str(s.get("option_type") or "").upper(),
            "market_iv": s.get("market_iv"),
            "fair_iv": s.get("fair_iv"),
            "iv_spread": s.get("spread"),
            "classification": classification,
            "suggested_action": _VALUATION_TAG_TO_ACTION.get(classification, "HOLD"),
        }
        for key in ("bid", "ask", "delta", "gamma", "vega", "theta"):
            if s.get(key) is not None:
                item[key] = s[key]
        if s.get("mid_price") is not None:
            item["mid"] = s["mid_price"]
        return item

    strikes = [_to_strike(s) for s in (raw.get("strikes") or raw.get("strike_mispricings") or [])]

    trade_recommendations = []
    for t in raw.get("candidate_trades") or []:
        leg_strikes = sorted({
            leg["strike"] for leg in (t.get("legs") or []) if leg.get("strike") is not None
        })
        trade_recommendations.append({
            "strategy": t.get("name") or t.get("strategy_type") or "",
            "direction": "SELL_VOL" if t.get("is_credit") else "BUY_VOL",
            "strikes": leg_strikes,
            "reason": t.get("rationale") or "",
            "estimated_edge_pct": t.get("mispricing_score", 0.0),
        })

    expiration = raw.get("expiration") or ""

    response: Dict[str, Any] = {
        "symbol": raw.get("symbol") or "",
        "spot_price": raw.get("spot_price"),
        "expiration": expiration,
        "expirations": [expiration] if expiration else [],
        "dte": raw.get("dte"),
        "rich_strikes_count": summary.get("rich_strikes_count", 0),
        "cheap_strikes_count": summary.get("cheap_strikes_count", 0),
        "strikes": strikes,
        "trade_recommendations": trade_recommendations,
        "as_of": raw.get("as_of") or "",
    }

    fair_iv_baseline = raw.get("baseline_fair_iv", raw.get("fair_atm_iv"))
    if fair_iv_baseline is not None:
        response["fair_iv_baseline"] = fair_iv_baseline
    if raw.get("market_atm_iv") is not None:
        response["market_atm_iv"] = raw["market_atm_iv"]

    return response


# ---------------------------------------------------------------------------
# Paper-Broker Execution
# ---------------------------------------------------------------------------


def execute_vol_mispricing_trade(
    symbol: str,
    *,
    candidate: Optional[Dict[str, Any]] = None,
    contracts: int = 1,
    dry_run: bool = False,
    is_live: bool = False,
) -> Dict[str, Any]:
    """
    Executes a single already-built candidate multi-leg volatility-mispricing trade
    (one element of `build_candidate_strategy_trades()`'s output) in the paper broker.

    Unlike `execute_earnings_crush_trade`, this function does NOT derive candidates or
    strikes itself -- deriving candidates is `build_candidate_strategy_trades()`'s job.
    The caller (typically the API layer, passing through a request body built from a
    prior `GET /pilots/options/forecast/mispricing` call) must explicitly select which
    candidate trade to execute; this mirrors `execute_dispersion_trade`'s `basket`
    parameter more than `execute_earnings_crush_trade`'s optional-legs-with-strike-
    fallback pattern, since a vol_mispricing candidate's legs are always already
    populated by `_create_strategy_leg`.

    `vol_mispricing` is a MEASURED deployability failure (Sharpe -0.499, DSR 0.027,
    fails the Oct-2008 stress window -- see docs/signals/vol_mispricing.md's Backtest
    Validation section) -- the caller (`POST /pilots/options/mispricing/execute` in
    api/pilots_api.py) is responsible for gating this function behind
    OPTIONS_DESK_DEPLOYABILITY_GATES["vol_mispricing"] and an explicit per-request
    `override_deployability_gate` flag. This function itself performs no deployability
    check -- it is a pure execution primitive, same division of responsibility as
    `execute_earnings_crush_trade`/`execute_dispersion_trade`.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return {"ok": False, "message": "Symbol is required."}

    if is_live:
        return {
            "ok": False,
            "message": "Advisory-Only Mode: Live options order execution is disabled. Please use paper mode.",
        }

    if not candidate or not candidate.get("legs"):
        return {
            "ok": False,
            "message": "A candidate strategy trade (with legs) is required to execute a vol_mispricing trade.",
        }

    strategy_type = candidate.get("strategy_type") or candidate.get("name") or "Vol Mispricing"

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "symbol": sym,
            "strategy": strategy_type,
            "contracts": contracts,
            "message": f"Dry run: {strategy_type} vol mispricing order validated for {sym}.",
        }

    # Translate _create_strategy_leg's {"action", "unit_price" ($/share)} leg shape into
    # execute_earnings_crush_trade's generic-executor {"side", "fill_price" ($/contract)}
    # shape. Options premia are quoted per-share; one contract == 100 shares, so
    # fill_price = unit_price * 100.0. A leg with no resolvable unit_price is left
    # unpriced (fill_price omitted) rather than fabricated -- the shared executor's own
    # CONSTRAINT #4 guard refuses the whole trade if any leg ends up unpriced.
    translated_legs: List[Dict[str, Any]] = []
    expiration: Optional[str] = None
    for leg in candidate.get("legs") or []:
        unit_price = leg.get("unit_price")
        leg_out: Dict[str, Any] = {
            "symbol": leg.get("symbol"),
            "side": str(leg.get("action") or "buy").lower(),
            "strike": leg.get("strike"),
            "type": leg.get("type"),
        }
        if unit_price is not None:
            leg_out["fill_price"] = float(unit_price) * 100.0
        translated_legs.append(leg_out)
        if expiration is None and leg.get("expiration"):
            expiration = leg.get("expiration")

    executor_candidate = {
        "symbol": sym,
        "strategy": strategy_type,
        "expiration": expiration,
        "legs": translated_legs,
    }

    try:
        from execution.options_paper_executor import OptionsPaperExecutor
        executor = OptionsPaperExecutor()
        res = executor.execute_earnings_crush_trade(
            executor_candidate,
            contracts=contracts,
            strategy_name="Vol Mispricing",
        )
        if res.get("success"):
            return {
                "ok": True,
                "order_id": res.get("order_id") or f"vm_{uuid.uuid4().hex[:8]}",
                "symbol": sym,
                "strategy": strategy_type,
                "contracts": contracts,
                "message": f"Successfully executed {strategy_type} vol mispricing trade for {sym}.",
                "details": res,
            }
        return {
            "ok": False,
            "message": res.get("reason", "Failed to execute vol mispricing trade"),
            "details": res,
        }
    except Exception as exc:  # noqa: BLE001 -- never raises (CONSTRAINT #6)
        logger.warning("execute_vol_mispricing_trade failed for %s: %s", sym, exc)
        return {
            "ok": False,
            "message": f"Internal error executing vol mispricing trade for {sym}.",
        }
