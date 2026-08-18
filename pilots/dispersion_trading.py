"""
pilots/dispersion_trading.py — Cross-Asset Options Dispersion & Implied Correlation Arbitrage Engine.
======================================================================================================

Implements institutional options dispersion trading capabilities:
1. Implied Correlation Decomposition (Driessen, Maenhout, Vilkov 2009):
     rho_implied = (sigma_index^2 - sum(w_i^2 * sigma_i^2)) / (sum_{i!=j} w_i * w_j * sigma_i * sigma_j)
2. Dispersion Alpha & Regime Classification:
     Spread = rho_implied - rho_realized
     - Long Dispersion (Spread >= 0.15): Short Index Straddle + Long Vega-Weighted Basket of Constituent Straddles.
     - Short Dispersion (Spread <= -0.15): Long Index Straddle + Short Vega-Weighted Basket of Constituent Straddles.
3. Vega-Neutral Basket Calibration:
     Allocates constituent contracts such that sum(constituent_vegas) == index_vega.
4. Atomic Execution:
     Submits multi-leg index and constituent fills into PaperAccountStore under strategy_name="Dispersion Arbitrage".

Design Invariants:
* **AST-Safe (CONSTRAINTS #1 & #3)**: Pure compute/execution module. Never imports heavy engines
  (`processing_engine`, `strategy_engine`, `forecasting_engine`, `macro_engine`,
   `technical_options_engine`, `main_orchestrator`, `desktop`).
* **Honesty (CONSTRAINT #4)**: Degenerate math guarded (< 1e-12), never fabricates false zeros.
* **Never Raises (CONSTRAINT #6)**: Degrades gracefully on missing data, returning structured error/fallback.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
import logging
import math
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd
from scipy.stats import norm

from data.paper_account_store import OrderStatus, PaperAccountStore
from settings import settings

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_DISPERSION_INDEX",
    "DEFAULT_DISPERSION_CONSTITUENTS",
    "DEFAULT_WEIGHTS",
    "OPTION_FEE_PER_CONTRACT_LEG",
    "DispersionBasket",
    "calculate_default_expiration",
    "calculate_straddle_vega",
    "calculate_option_price",
    "compute_implied_correlation",
    "compute_realized_correlation_matrix",
    "evaluate_dispersion_opportunity",
    "get_dispersion_opportunities",
    "build_dispersion_basket",
    "execute_dispersion_trade",
]

DEFAULT_DISPERSION_INDEX = "SPY"
DEFAULT_DISPERSION_CONSTITUENTS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "TSLA",
    "AVGO",
]
DEFAULT_WEIGHTS = {
    "AAPL": 0.18,
    "MSFT": 0.17,
    "NVDA": 0.16,
    "AMZN": 0.12,
    "GOOGL": 0.11,
    "META": 0.09,
    "TSLA": 0.09,
    "AVGO": 0.08,
}

# Distinct index constituent weight allocations per index (QQQ vs SPY)
INDEX_CONSTITUENTS_MAP = {
    "SPY": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO"],
    "QQQ": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA"],
}
INDEX_WEIGHTS_MAP = {
    "SPY": {
        "AAPL": 0.18,
        "MSFT": 0.17,
        "NVDA": 0.16,
        "AMZN": 0.12,
        "GOOGL": 0.11,
        "META": 0.09,
        "TSLA": 0.09,
        "AVGO": 0.08,
    },
    "QQQ": {
        "AAPL": 0.20,
        "MSFT": 0.19,
        "NVDA": 0.18,
        "AMZN": 0.14,
        "GOOGL": 0.12,
        "META": 0.09,
        "AVGO": 0.05,
        "TSLA": 0.03,
    },
}
OPTION_FEE_PER_CONTRACT_LEG = 0.65  # $0.65 per contract leg


def _round_or_none(value: Optional[float], ndigits: int = 4) -> Optional[float]:
    """`round()` that passes an honest None through unchanged instead of raising."""
    return round(value, ndigits) if value is not None else None


def calculate_default_expiration(target_dte: int = 30) -> str:
    """Calculates target expiration date string (YYYY-MM-DD) target_dte days in future on a Friday."""
    target = datetime.now(timezone.utc).date() + timedelta(days=target_dte)
    weekday = target.weekday()
    days_to_friday = (4 - weekday) % 7
    if days_to_friday > 3:
        days_to_friday -= 7
    friday = target + timedelta(days=days_to_friday)
    return friday.strftime("%Y-%m-%d")


def calculate_straddle_vega(
    spot: float,
    strike: float,
    iv: float,
    dte: int,
    r: Optional[float] = None,
) -> float:
    """
    Calculates total straddle vega (ATM Call Vega + ATM Put Vega) per 1 option contract (100 multiplier).
    Returns vega in $ per 1.0 (100%) change in implied volatility ($/vol).
    """
    if r is None:
        r = float(getattr(settings, "OPTIONS_RISK_FREE_RATE", 0.045))

    if spot <= 0 or strike <= 0 or iv <= 1e-12 or dte <= 0:
        return 0.0

    t_years = max(1, dte) / 365.0
    vol_sqrt_t = iv * math.sqrt(t_years)
    if vol_sqrt_t <= 1e-12:
        return 0.0

    d1 = (math.log(spot / strike) + (r + 0.5 * (iv ** 2)) * t_years) / vol_sqrt_t
    pdf_d1 = float(norm.pdf(d1))
    vega_per_share = spot * math.sqrt(t_years) * pdf_d1
    # Straddle = 1 Call + 1 Put = 2 * vega_per_share. Multiplier = 100 shares / contract.
    straddle_vega_contract = 2.0 * vega_per_share * 100.0
    return max(0.0, float(straddle_vega_contract))


def calculate_option_price(
    spot: float,
    strike: float,
    dte: int,
    iv: float,
    opt_type: str = "call",
    r: Optional[float] = None,
) -> float:
    """Calculates Black-Scholes unit price for an option contract ($/contract, multiplier=100) using canonical pilots.options_risk."""
    from pilots.options_risk import calculate_black_scholes_greeks

    t_years = max(0, dte) / 365.0
    sigma = max(0.01, iv) if (iv is not None and iv > 0) else 0.0
    res = calculate_black_scholes_greeks(
        spot=spot,
        strike=strike,
        t_years=t_years,
        sigma=sigma,
        option_type=opt_type,
        r=r,
    )
    return max(0.01, round(res["price"], 4)) * 100.0


def compute_implied_correlation(
    index_iv: float,
    constituent_ivs: Dict[str, float],
    weights: Dict[str, float],
) -> Optional[float]:
    """
    Calculates implied correlation (rho_implied) using the Driessen, Maenhout, Vilkov (2009) model:
        rho_implied = (sigma_index^2 - sum(w_i^2 * sigma_i^2)) / (sum_{i!=j} w_i * w_j * sigma_i * sigma_j)
                    = (sigma_index^2 - sum(w_i^2 * sigma_i^2)) / ((sum(w_i * sigma_i))^2 - sum(w_i^2 * sigma_i^2))

    Returns None (never a fabricated "typical" correlation guess -- CONSTRAINT #4) when the
    inputs are missing/insufficient or the math is degenerate (near-zero denominator). A caller
    must treat `None` as "cannot be computed from real data" and refuse the actionable
    recommendation rather than silently substituting a plausible-looking number.
    """
    if index_iv <= 0 or not constituent_ivs or not weights:
        return None

    # Match constituents present in both ivs and weights with positive values
    common_symbols = [
        s for s in constituent_ivs
        if s in weights and constituent_ivs[s] > 0 and weights[s] > 0
    ]
    if not common_symbols:
        return None

    total_weight = sum(weights[s] for s in common_symbols)
    if total_weight <= 0:
        return None

    # Normalize weights
    w_norm = {s: weights[s] / total_weight for s in common_symbols}

    # Sum(w_i^2 * sigma_i^2)
    weighted_var_sum = sum((w_norm[s] ** 2) * (constituent_ivs[s] ** 2) for s in common_symbols)
    # (Sum(w_i * sigma_i))^2
    weighted_vol_sum_sq = (sum(w_norm[s] * constituent_ivs[s] for s in common_symbols)) ** 2

    numerator = (index_iv ** 2) - weighted_var_sum
    denominator = weighted_vol_sum_sq - weighted_var_sum

    if abs(denominator) <= 1e-12:
        return None

    implied_corr = numerator / denominator
    return max(0.0, min(1.0, float(implied_corr)))


def compute_realized_correlation_matrix(
    returns_df: pd.DataFrame,
    weights: Optional[Dict[str, float]] = None,
) -> Tuple[pd.DataFrame, Optional[float]]:
    """
    Computes pairwise realized correlation matrix and weighted average realized correlation.

    Returns `(pd.DataFrame(), None)` (never a fabricated "typical" correlation guess --
    CONSTRAINT #4) when there isn't enough real return history to compute a correlation.
    """
    if returns_df is None or returns_df.empty or len(returns_df.columns) < 2:
        return pd.DataFrame(), None

    corr_matrix = returns_df.corr().fillna(0.0)
    cols = list(corr_matrix.columns)
    n = len(cols)

    if weights:
        total_w = sum(weights.get(c, 0.0) for c in cols)
        if total_w > 0:
            w_vec = np.array([weights.get(c, 0.0) / total_w for c in cols])
            w_var_sum = np.sum(w_vec ** 2)
            denom = 1.0 - w_var_sum
            if abs(denom) > 1e-12:
                weighted_corr = (np.dot(w_vec, np.dot(corr_matrix.values, w_vec)) - w_var_sum) / denom
                return corr_matrix, float(max(-1.0, min(1.0, weighted_corr)))

    # Default unweighted mean of upper triangle
    triu_indices = np.triu_indices(n, k=1)
    if len(triu_indices[0]) > 0:
        avg_corr = float(np.mean(corr_matrix.values[triu_indices]))
        return corr_matrix, float(max(-1.0, min(1.0, avg_corr)))

    return corr_matrix, None


def evaluate_dispersion_opportunity(
    index_symbol: str = "SPY",
    constituent_symbols: Optional[List[str]] = None,
    index_iv: Optional[float] = None,
    constituent_ivs: Optional[Dict[str, float]] = None,
    weights: Optional[Dict[str, float]] = None,
    realized_correlation: Optional[float] = None,
    threshold: float = 0.15,
) -> Dict[str, Any]:
    """
    Evaluates whether a dispersion arbitrage opportunity exists based on the correlation spread:
        Spread = rho_implied - rho_realized
    If Spread >= threshold (default 0.15): Long Dispersion (Sell Index Straddle, Buy Basket Straddles).
    If Spread <= -threshold: Short Dispersion (Buy Index Straddle, Sell Basket Straddles).

    `index_iv`, `constituent_ivs`, and `realized_correlation` must all be real, caller-supplied
    market data. When any is missing (or `compute_implied_correlation` cannot compute a real
    number from what was supplied), this refuses to fabricate a recommendation (CONSTRAINT #4)
    and instead returns `regime="Insufficient Data"` / `is_actionable=False` with a `reason`.
    """
    constituents = [s.upper().strip() for s in (constituent_symbols or DEFAULT_DISPERSION_CONSTITUENTS)]
    w = weights or {s: DEFAULT_WEIGHTS.get(s, 1.0 / len(constituents)) for s in constituents}

    insufficient_reason: Optional[str] = None
    implied_corr: Optional[float] = None

    if index_iv is None or index_iv <= 0:
        insufficient_reason = "No real index ATM implied volatility supplied."
    elif not constituent_ivs:
        insufficient_reason = "No real constituent implied volatility data supplied."
    elif realized_correlation is None:
        insufficient_reason = "No real realized-correlation estimate supplied."
    else:
        implied_corr = compute_implied_correlation(index_iv=index_iv, constituent_ivs=constituent_ivs, weights=w)
        if implied_corr is None:
            insufficient_reason = (
                "Implied correlation could not be computed from the supplied IV/weights "
                "(degenerate or incomplete inputs)."
            )

    if insufficient_reason is not None:
        return {
            "index_symbol": index_symbol.upper().strip(),
            "constituent_symbols": constituents,
            "implied_correlation": None,
            "realized_correlation": (round(realized_correlation, 4) if realized_correlation is not None else None),
            "correlation_spread": None,
            "threshold": threshold,
            "regime": "Insufficient Data",
            "direction": "unknown",
            "is_actionable": False,
            "description": (
                "Real market data required to evaluate a dispersion opportunity is unavailable; "
                "refusing to fabricate a recommendation."
            ),
            "reason": insufficient_reason,
        }

    real_corr = float(realized_correlation)
    spread = implied_corr - real_corr

    if spread >= threshold:
        regime = "Long Dispersion"
        direction = "long_dispersion"
        is_actionable = True
        description = (
            "Implied correlation is overpriced relative to realized correlation. "
            "Sell Index Straddle, Buy Vega-Neutral Constituent Straddles."
        )
    elif spread <= -threshold:
        regime = "Short Dispersion"
        direction = "short_dispersion"
        is_actionable = True
        description = (
            "Implied correlation is underpriced relative to realized correlation. "
            "Buy Index Straddle, Sell Vega-Neutral Constituent Straddles."
        )
    else:
        regime = "Neutral"
        direction = "neutral"
        is_actionable = False
        description = "Correlation spread is within fair value band. No dispersion trade recommended."

    return {
        "index_symbol": index_symbol.upper().strip(),
        "constituent_symbols": constituents,
        "implied_correlation": round(implied_corr, 4),
        "realized_correlation": round(real_corr, 4),
        "correlation_spread": round(spread, 4),
        "threshold": threshold,
        "regime": regime,
        "direction": direction,
        "is_actionable": is_actionable,
        "description": description,
        "reason": None,
    }


@dataclass
class DispersionBasket:
    """Structured container for a calibrated dispersion trade basket."""
    index_symbol: str
    constituent_symbols: List[str]
    target_dte: int
    expiration: str
    is_long_dispersion: bool
    index_contracts: int
    index_vega: float
    basket_vega: float
    vega_neutrality_ratio: float
    vega_imbalance_pct: float
    implied_correlation: float
    realized_correlation: Optional[float]
    correlation_spread: Optional[float]
    index_leg_requests: List[Dict[str, Any]]
    constituent_leg_requests: Dict[str, List[Dict[str, Any]]]
    index_net_cash_impact: float
    constituent_net_cash_impact: float
    total_net_cash_impact: float
    total_commission: float
    constituent_allocations: Dict[str, Dict[str, Any]]
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_dispersion_basket(
    index_symbol: str = "SPY",
    constituent_symbols: Optional[List[str]] = None,
    spot_map: Optional[Dict[str, float]] = None,
    iv_map: Optional[Dict[str, float]] = None,
    weights: Optional[Dict[str, float]] = None,
    index_contracts: int = 1,
    target_dte: int = 30,
    expiration: Optional[str] = None,
    is_long_dispersion: bool = True,
    realized_correlation: Optional[float] = None,
    r: Optional[float] = None,
) -> Optional[DispersionBasket]:
    """
    Constructs a calibrated vega-neutral dispersion trade basket.

    1. Computes total vega for the index straddle (ATM Call + ATM Put).
    2. For each constituent stock, computes stock straddle vega and allocates contracts
       such that sum(constituent_vegas) == index_vega (Vega Neutrality).
    3. Formats multi-leg order requests for index and all constituents with Black-Scholes pricing.

    Returns None (never a basket priced off a fabricated spot/IV -- CONSTRAINT #4; matches
    `pilots/options_hedging.py::execute_delta_hedge`'s "refuse rather than fabricate"
    convention) when `spot_map`/`iv_map` is missing a real value for the index or any
    constituent. A caller must not treat a missing quote as license to price (and potentially
    execute) a trade off an invented number. `realized_correlation` is diagnostic only (it feeds
    `correlation_spread`, not the leg pricing/vega-neutrality math below) -- when it isn't
    supplied, the basket still prices normally off real spot/IV and `realized_correlation`/
    `correlation_spread` are honestly `None` rather than computed off a fabricated 0.45.
    """
    idx_sym = (index_symbol or DEFAULT_DISPERSION_INDEX).upper().strip()
    constituents = [
        s.upper().strip() for s in (constituent_symbols or DEFAULT_DISPERSION_CONSTITUENTS)
    ]
    if not constituents:
        return None
    target_exp = expiration or calculate_default_expiration(target_dte)

    # Resolve spot prices -- real data only, no fabricated defaults.
    spots = spot_map or {}
    idx_spot = float(spots.get(idx_sym, 0.0) or 0.0)
    if idx_spot <= 0:
        logger.info(
            "build_dispersion_basket: no real spot price for index %s; refusing to price a "
            "basket off a fabricated spot.", idx_sym,
        )
        return None

    # Resolve IVs -- real data only, no fabricated defaults.
    ivs = iv_map or {}
    idx_iv = float(ivs.get(idx_sym, 0.0) or 0.0)
    if idx_iv <= 0:
        logger.info(
            "build_dispersion_basket: no real ATM IV for index %s; refusing to price a basket "
            "off a fabricated IV.", idx_sym,
        )
        return None

    # Normalize weights across active constituents
    raw_weights = weights or DEFAULT_WEIGHTS
    valid_constituents = [s for s in constituents if s in raw_weights or s in DEFAULT_WEIGHTS or True]
    if not valid_constituents:
        valid_constituents = list(constituents)

    total_w = sum(raw_weights.get(s, DEFAULT_WEIGHTS.get(s, 1.0 / len(valid_constituents))) for s in valid_constituents)
    if total_w <= 0:
        total_w = float(len(valid_constituents))
    normalized_weights = {
        s: float(raw_weights.get(s, DEFAULT_WEIGHTS.get(s, 1.0 / len(valid_constituents)))) / total_w
        for s in valid_constituents
    }

    # Constituent spots/IVs -- real data only; any missing/non-positive value means the basket
    # cannot be honestly priced (CONSTRAINT #4).
    const_ivs: Dict[str, float] = {}
    const_spots: Dict[str, float] = {}
    for s in valid_constituents:
        s_spot_check = float(spots.get(s, 0.0) or 0.0)
        if s_spot_check <= 0:
            logger.info(
                "build_dispersion_basket: no real spot price for constituent %s; refusing to "
                "price a basket off a fabricated spot.", s,
            )
            return None
        const_spots[s] = s_spot_check

        s_iv = float(ivs.get(s, 0.0) or 0.0)
        if s_iv <= 0:
            logger.info(
                "build_dispersion_basket: no real ATM IV for constituent %s; refusing to price "
                "a basket off a fabricated IV.", s,
            )
            return None
        const_ivs[s] = s_iv

    # Correlation fields are diagnostic only (they do not feed the leg pricing/vega-neutrality
    # math below), so a missing/non-computable value degrades to an honest None rather than a
    # fabricated 0.45/blocking the basket entirely (CONSTRAINT #4).
    implied_corr = compute_implied_correlation(index_iv=idx_iv, constituent_ivs=const_ivs, weights=normalized_weights)
    real_corr = float(realized_correlation) if realized_correlation is not None else None
    corr_spread = (implied_corr - real_corr) if (implied_corr is not None and real_corr is not None) else None

    # 1. Index Straddle Vega & Legs
    idx_strike = round(idx_spot, 2)
    idx_straddle_contract_vega = calculate_straddle_vega(
        spot=idx_spot, strike=idx_strike, iv=idx_iv, dte=target_dte, r=r
    )
    total_index_vega = float(index_contracts) * idx_straddle_contract_vega

    idx_call_price = calculate_option_price(idx_spot, idx_strike, target_dte, idx_iv, "call", r=r)
    idx_put_price = calculate_option_price(idx_spot, idx_strike, target_dte, idx_iv, "put", r=r)
    idx_straddle_price = idx_call_price + idx_put_price

    # Side convention:
    # Long Dispersion => Short Index Straddle (Sell Call + Put), Long Constituent Straddles (Buy Call + Put)
    # Short Dispersion => Long Index Straddle (Buy Call + Put), Short Constituent Straddles (Sell Call + Put)
    idx_side = "sell" if is_long_dispersion else "buy"
    const_side = "buy" if is_long_dispersion else "sell"

    idx_legs = [
        {
            "symbol": f"{idx_sym} {target_exp} ${idx_strike:.2f} CALL",
            "side": idx_side,
            "action": idx_side,
            "type": "call",
            "strike": idx_strike,
            "qty": float(index_contracts),
            "fill_price": idx_call_price,
            "expiration": target_exp,
            "ratio": 1,
        },
        {
            "symbol": f"{idx_sym} {target_exp} ${idx_strike:.2f} PUT",
            "side": idx_side,
            "action": idx_side,
            "type": "put",
            "strike": idx_strike,
            "qty": float(index_contracts),
            "fill_price": idx_put_price,
            "expiration": target_exp,
            "ratio": 1,
        },
    ]

    idx_commission = 2 * index_contracts * OPTION_FEE_PER_CONTRACT_LEG
    idx_gross_cash = index_contracts * idx_straddle_price
    idx_net_cash_impact = (idx_gross_cash - idx_commission) if idx_side == "sell" else (-idx_gross_cash - idx_commission)

    # 2. Constituent Straddles Vega Allocation
    constituent_leg_requests: Dict[str, List[Dict[str, Any]]] = {}
    constituent_allocations: Dict[str, Dict[str, Any]] = {}
    total_basket_vega = 0.0
    const_total_cash_impact = 0.0
    total_const_commission = 0.0

    for s in valid_constituents:
        s_spot = const_spots[s]
        s_strike = round(s_spot, 2)
        s_iv = const_ivs[s]
        s_weight = normalized_weights[s]

        s_straddle_contract_vega = calculate_straddle_vega(
            spot=s_spot, strike=s_strike, iv=s_iv, dte=target_dte, r=r
        )
        target_constituent_vega = s_weight * total_index_vega

        if s_straddle_contract_vega > 1e-12:
            raw_contracts = target_constituent_vega / s_straddle_contract_vega
            sized_contracts = max(1, int(round(raw_contracts)))
        else:
            raw_contracts = 1.0
            sized_contracts = 1

        actual_constituent_vega = sized_contracts * s_straddle_contract_vega
        total_basket_vega += actual_constituent_vega

        s_call_price = calculate_option_price(s_spot, s_strike, target_dte, s_iv, "call", r=r)
        s_put_price = calculate_option_price(s_spot, s_strike, target_dte, s_iv, "put", r=r)
        s_straddle_price = s_call_price + s_put_price

        s_commission = 2 * sized_contracts * OPTION_FEE_PER_CONTRACT_LEG
        s_gross_cash = sized_contracts * s_straddle_price
        s_net_cash_impact = (-s_gross_cash - s_commission) if const_side == "buy" else (s_gross_cash - s_commission)

        const_total_cash_impact += s_net_cash_impact
        total_const_commission += s_commission

        s_legs = [
            {
                "symbol": f"{s} {target_exp} ${s_strike:.2f} CALL",
                "side": const_side,
                "action": const_side,
                "type": "call",
                "strike": s_strike,
                "qty": float(sized_contracts),
                "fill_price": s_call_price,
                "expiration": target_exp,
                "ratio": 1,
            },
            {
                "symbol": f"{s} {target_exp} ${s_strike:.2f} PUT",
                "side": const_side,
                "action": const_side,
                "type": "put",
                "strike": s_strike,
                "qty": float(sized_contracts),
                "fill_price": s_put_price,
                "expiration": target_exp,
                "ratio": 1,
            },
        ]
        constituent_leg_requests[s] = s_legs

        constituent_allocations[s] = {
            "symbol": s,
            "spot": s_spot,
            "strike": s_strike,
            "iv": round(s_iv, 4),
            "weight": round(s_weight, 4),
            "raw_contracts": round(raw_contracts, 4),
            "contracts": sized_contracts,
            "contract_vega": round(s_straddle_contract_vega, 2),
            "target_vega": round(target_constituent_vega, 2),
            "actual_vega": round(actual_constituent_vega, 2),
            "straddle_unit_price": round(s_straddle_price, 2),
            "net_cash_impact": round(s_net_cash_impact, 2),
            "commission": round(s_commission, 2),
        }

    # 3. Vega Balance Metrics
    if total_index_vega > 1e-12:
        vega_neutrality_ratio = total_basket_vega / total_index_vega
        vega_imbalance_pct = ((total_basket_vega - total_index_vega) / total_index_vega) * 100.0
    else:
        vega_neutrality_ratio = 1.0
        vega_imbalance_pct = 0.0

    total_commission = idx_commission + total_const_commission
    total_net_cash_impact = idx_net_cash_impact + const_total_cash_impact

    summary = {
        "strategy": "Dispersion Arbitrage",
        "direction": "Long Dispersion" if is_long_dispersion else "Short Dispersion",
        "index_symbol": idx_sym,
        "index_contracts": index_contracts,
        "constituent_count": len(valid_constituents),
        "target_dte": target_dte,
        "expiration": target_exp,
        "index_vega": round(total_index_vega, 2),
        "basket_vega": round(total_basket_vega, 2),
        "vega_neutrality_ratio": round(vega_neutrality_ratio, 4),
        "vega_imbalance_pct": round(vega_imbalance_pct, 2),
        "implied_correlation": _round_or_none(implied_corr),
        "realized_correlation": _round_or_none(real_corr),
        "correlation_spread": _round_or_none(corr_spread),
        "total_commission": round(total_commission, 2),
        "total_net_cash_impact": round(total_net_cash_impact, 2),
    }

    return DispersionBasket(
        index_symbol=idx_sym,
        constituent_symbols=valid_constituents,
        target_dte=target_dte,
        expiration=target_exp,
        is_long_dispersion=is_long_dispersion,
        index_contracts=index_contracts,
        index_vega=round(total_index_vega, 2),
        basket_vega=round(total_basket_vega, 2),
        vega_neutrality_ratio=round(vega_neutrality_ratio, 4),
        vega_imbalance_pct=round(vega_imbalance_pct, 2),
        implied_correlation=_round_or_none(implied_corr),
        realized_correlation=_round_or_none(real_corr),
        correlation_spread=_round_or_none(corr_spread),
        index_leg_requests=idx_legs,
        constituent_leg_requests=constituent_leg_requests,
        index_net_cash_impact=round(idx_net_cash_impact, 2),
        constituent_net_cash_impact=round(const_total_cash_impact, 2),
        total_net_cash_impact=round(total_net_cash_impact, 2),
        total_commission=round(total_commission, 2),
        constituent_allocations=constituent_allocations,
        summary=summary,
    )


def _closest_atm_iv_from_df(df: Any, spot: float) -> Optional[float]:
    """Returns the implied vol of the strike closest to `spot` in a calls/puts chain DataFrame.
    None (never a fabricated vol -- CONSTRAINT #4) on any missing/degenerate data."""
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None
    if "strike" not in df.columns or "impliedVolatility" not in df.columns:
        return None
    try:
        work = df[["strike", "impliedVolatility"]].apply(pd.to_numeric, errors="coerce").dropna()
        work = work[(work["strike"] > 0) & (work["impliedVolatility"] > 0)]
        if work.empty:
            return None
        idx = (work["strike"] - spot).abs().idxmin()
        return float(work.loc[idx, "impliedVolatility"])
    except Exception:
        return None


def _resolve_atm_iv(options_provider: Any, symbol: str, spot: float) -> Optional[float]:
    """
    Fetches the nearest available options chain for `symbol` and returns the ATM implied
    volatility (average of nearest-strike call + put IV, or whichever side is available).
    Returns None (never a fabricated vol guess -- CONSTRAINT #4) when the chain, spot, or a
    usable IV quote is unavailable.
    """
    if options_provider is None or spot is None or spot <= 0:
        return None
    try:
        exp_list = options_provider.fetch_options_chain(symbol)
    except Exception as exc:
        logger.debug("fetch_options_chain(%s) failed: %s", symbol, exc)
        return None
    if not isinstance(exp_list, (list, tuple)) or not exp_list:
        return None
    try:
        chain = options_provider.fetch_options_chain(symbol, str(exp_list[0]))
    except Exception as exc:
        logger.debug("fetch_options_chain(%s, %s) failed: %s", symbol, exp_list[0], exc)
        return None
    if chain is None:
        return None

    calls_df = getattr(chain, "calls", None)
    puts_df = getattr(chain, "puts", None)
    if calls_df is None and isinstance(chain, dict):
        calls_df = chain.get("calls")
        puts_df = chain.get("puts")

    call_iv = _closest_atm_iv_from_df(calls_df, spot)
    put_iv = _closest_atm_iv_from_df(puts_df, spot)
    if call_iv is not None and put_iv is not None:
        return (call_iv + put_iv) / 2.0
    return call_iv if call_iv is not None else put_iv


def _source_real_dispersion_inputs(
    idx_sym: str,
    constituents: List[str],
    weights: Dict[str, float],
) -> Tuple[Dict[str, float], Dict[str, float], Optional[float]]:
    """
    Best-effort sources real spot prices (`pilots.price_provider.get_current_price`), real ATM
    implied vol from the options chain (`data.market_data.get_options_provider`), and a real
    trailing realized-correlation estimate (`data.historical_store.HistoricalStore` daily bars
    via `compute_realized_correlation_matrix`) for one index + its constituent universe.

    Returns `(spot_map, iv_map, realized_correlation)` -- any symbol/value that couldn't be
    resolved from real data is simply absent/None (CONSTRAINT #4: never a fabricated stand-in).
    Never raises (CONSTRAINT #6): any provider failure degrades that piece to empty/None.
    """
    spot_map: Dict[str, float] = {}
    iv_map: Dict[str, float] = {}
    realized_corr: Optional[float] = None

    try:
        from pilots.price_provider import get_current_price
    except Exception:
        get_current_price = None  # type: ignore[assignment]

    try:
        from data.market_data import get_options_provider
        options_provider = get_options_provider()
    except Exception as exc:
        logger.debug("OptionsProvider unavailable for dispersion inputs: %s", exc)
        options_provider = None

    try:
        from data.historical_store import HistoricalStore
        store = HistoricalStore()
    except Exception as exc:
        logger.debug("HistoricalStore unavailable for dispersion inputs: %s", exc)
        store = None

    if get_current_price is not None:
        idx_spot = get_current_price(idx_sym)
        if idx_spot and idx_spot > 0:
            spot_map[idx_sym] = idx_spot
        for s in constituents:
            s_spot = get_current_price(s)
            if s_spot and s_spot > 0:
                spot_map[s] = s_spot

    if idx_sym in spot_map:
        idx_iv = _resolve_atm_iv(options_provider, idx_sym, spot_map[idx_sym])
        if idx_iv is not None:
            iv_map[idx_sym] = idx_iv
    for s in constituents:
        if s in spot_map:
            s_iv = _resolve_atm_iv(options_provider, s, spot_map[s])
            if s_iv is not None:
                iv_map[s] = s_iv

    if store is not None:
        try:
            returns: Dict[str, pd.Series] = {}
            for s in constituents:
                bars = store.get_bars(s, lookback_days=90)
                if bars is not None and len(bars) > 10 and "Close" in bars.columns:
                    returns[s] = bars["Close"].astype(float).pct_change().dropna()
            if len(returns) >= 2:
                returns_df = pd.DataFrame(returns).dropna(how="any")
                if len(returns_df) >= 5:
                    _, realized_corr = compute_realized_correlation_matrix(returns_df, weights=weights)
        except Exception as exc:
            logger.debug("Realized correlation computation failed for %s basket: %s", idx_sym, exc)

    return spot_map, iv_map, realized_corr


def get_dispersion_opportunities(
    indices: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Returns dispersion arbitrage analysis and opportunities across index baskets.

    Sources real market data per index via `_source_real_dispersion_inputs`. When it is
    unavailable for an index, that index's entry honestly reports `is_actionable=False` /
    `regime="Insufficient Data"` (CONSTRAINT #4) rather than falling back to a fabricated
    IV/correlation guess.
    """
    idx_list = indices or ["QQQ", "SPY"]

    opportunities = []
    for idx in idx_list:
        try:
            idx_sym = str(idx).upper().strip()
            constituents = INDEX_CONSTITUENTS_MAP.get(idx_sym, list(DEFAULT_DISPERSION_CONSTITUENTS))
            weights = INDEX_WEIGHTS_MAP.get(idx_sym, dict(DEFAULT_WEIGHTS))

            spot_map, iv_map, realized_corr = _source_real_dispersion_inputs(idx_sym, constituents, weights)
            const_ivs = {s: iv_map[s] for s in constituents if s in iv_map}

            opp = evaluate_dispersion_opportunity(
                index_symbol=idx_sym,
                constituent_symbols=constituents,
                index_iv=iv_map.get(idx_sym),
                constituent_ivs=const_ivs or None,
                weights=weights,
                realized_correlation=realized_corr,
            )

            if opp.get("regime") != "Insufficient Data":
                # build_dispersion_basket is a second, independent honesty gate: it will
                # itself refuse (return None) if per-symbol spot/IV coverage is incomplete.
                basket_obj = build_dispersion_basket(
                    index_symbol=idx_sym,
                    constituent_symbols=constituents,
                    spot_map=spot_map,
                    iv_map=iv_map,
                    weights=weights,
                    is_long_dispersion=(opp.get("direction") != "short_dispersion"),
                    realized_correlation=realized_corr,
                )
                opp["basket"] = basket_obj.to_dict() if basket_obj is not None else None
            else:
                opp["basket"] = None

            opportunities.append(opp)
        except Exception as exc:
            logger.warning("Error evaluating dispersion opportunity for %s: %s", idx, exc)

    first_opp = opportunities[0] if opportunities else {}
    return {
        "count": len(opportunities),
        "opportunities": opportunities,
        "implied_correlation": first_opp.get("implied_correlation"),
        "realized_correlation": first_opp.get("realized_correlation"),
        "correlation_spread": first_opp.get("correlation_spread"),
        "regime": first_opp.get("regime"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def execute_dispersion_trade(
    basket: Optional[Union[DispersionBasket, Dict[str, Any]]] = None,
    store: Optional[PaperAccountStore] = None,
    index_symbol: Optional[str] = None,
    dry_run: bool = False,
    is_live: bool = False,
) -> Dict[str, Any]:
    """
    Executes a calibrated dispersion trade basket into PaperAccountStore.

    Submits multi-leg index fill and individual constituent multi-leg fills
    atomically into PaperAccountStore under strategy_name="Dispersion Arbitrage".
    """
    if is_live:
        return {
            "ok": False,
            "message": "Advisory-Only Mode: Live options order execution is disabled. Please use paper mode.",
        }

    idx_sym = index_symbol or "SPY"
    if basket is None:
        # No basket supplied -- source real market data (same path get_dispersion_opportunities
        # uses) rather than calling build_dispersion_basket with empty spot/iv maps, which would
        # always refuse (CONSTRAINT #4: no fabricated fallback spot/IV exists to fall through to).
        constituents = INDEX_CONSTITUENTS_MAP.get(idx_sym, list(DEFAULT_DISPERSION_CONSTITUENTS))
        weights = INDEX_WEIGHTS_MAP.get(idx_sym, dict(DEFAULT_WEIGHTS))
        spot_map, iv_map, realized_corr = _source_real_dispersion_inputs(idx_sym, constituents, weights)
        const_ivs = {s: iv_map[s] for s in constituents if s in iv_map}

        # Evaluate opportunity first to derive the actual spread sign dynamically
        opp = evaluate_dispersion_opportunity(
            index_symbol=idx_sym,
            constituent_symbols=constituents,
            index_iv=iv_map.get(idx_sym),
            constituent_ivs=const_ivs or None,
            weights=weights,
            realized_correlation=realized_corr,
        )
        is_long = opp.get("direction") != "short_dispersion"

        basket = build_dispersion_basket(
            index_symbol=idx_sym,
            constituent_symbols=constituents,
            spot_map=spot_map,
            iv_map=iv_map,
            weights=weights,
            is_long_dispersion=is_long,
            realized_correlation=realized_corr,
        )
    elif isinstance(basket, dict):
        try:
            idx_sym = basket.get("index_symbol", idx_sym)
            basket = DispersionBasket(**basket)
        except Exception:
            basket = build_dispersion_basket(index_symbol=idx_sym)

    if basket is None:
        # build_dispersion_basket refused (no real spot/IV data for the index or a
        # constituent) -- refuse to execute rather than fill a trade priced off fabricated
        # inputs (CONSTRAINT #4; matches
        # `pilots/options_hedging.py::execute_delta_hedge`'s "refuse rather than fabricate").
        return {
            "ok": False,
            "index_symbol": idx_sym,
            "message": (
                f"Cannot build a dispersion basket for {idx_sym}: no real spot price or "
                "implied volatility was supplied for the index or one or more constituents. "
                "Refusing to execute a trade priced off fabricated inputs."
            ),
        }

    basket_id = f"disp_{uuid.uuid4().hex[:8]}"

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "basket_id": f"disp_dry_{uuid.uuid4().hex[:8]}",
            "index_symbol": basket.index_symbol,
            "constituent_count": len(basket.constituent_symbols),
            "message": (
                f"Dry run: Dispersion Arbitrage basket validated for {basket.index_symbol} "
                f"with {len(basket.constituent_symbols)} constituents."
            ),
            "basket": basket.to_dict() if hasattr(basket, "to_dict") else basket,
        }

    try:
        paper_store = store or PaperAccountStore()
    except Exception as exc:
        # Full exception + traceback goes to the server log only (never echoed back to the
        # caller) -- CodeQL py/stack-trace-exposure flagged this dict flowing straight through
        # api/pilots_api.py's post_options_dispersion_execute `return res`, matching the same
        # generic-message-to-client / detail-to-log-only convention execute_vol_mispricing_trade
        # already follows for its own store-failure path.
        logger.exception("Failed to initialize PaperAccountStore for dispersion trade: %s", exc)
        return {
            "ok": False,
            "basket_id": basket_id,
            "message": "Paper account storage unavailable; see server logs for detail.",
        }

    strategy_name = "Dispersion Arbitrage"
    idx_sym = basket.index_symbol
    idx_coid = f"{basket_id}_IDX_{idx_sym}"

    # 1. Execute Index Straddle Order
    idx_success = paper_store.apply_multi_leg_fill(
        client_order_id=idx_coid,
        symbol=idx_sym,
        strategy_name=strategy_name,
        contracts=basket.index_contracts,
        legs=basket.index_leg_requests,
        net_cash_impact=basket.index_net_cash_impact,
        commission_and_fees=2 * basket.index_contracts * OPTION_FEE_PER_CONTRACT_LEG,
        status=OrderStatus.FILLED,
    )

    if not idx_success:
        return {
            "ok": False,
            "basket_id": basket_id,
            "index_symbol": idx_sym,
            "message": f"Index order for {idx_sym} rejected (insufficient funds or collateral).",
        }

    # 2. Execute Constituent Straddle Orders
    constituent_order_ids: Dict[str, str] = {}
    failed_constituents: Dict[str, str] = {}
    total_legs_filled = len(basket.index_leg_requests)

    for sym, legs in basket.constituent_leg_requests.items():
        alloc = basket.constituent_allocations.get(sym, {})
        c_contracts = int(alloc.get("contracts", 1))
        c_cash = float(alloc.get("net_cash_impact", 0.0))
        c_comm = float(alloc.get("commission", 2 * c_contracts * OPTION_FEE_PER_CONTRACT_LEG))
        c_coid = f"{basket_id}_{sym}"

        c_success = paper_store.apply_multi_leg_fill(
            client_order_id=c_coid,
            symbol=sym,
            strategy_name=strategy_name,
            contracts=c_contracts,
            legs=legs,
            net_cash_impact=c_cash,
            commission_and_fees=c_comm,
            status=OrderStatus.FILLED,
        )

        if c_success:
            constituent_order_ids[sym] = c_coid
            total_legs_filled += len(legs)
        else:
            failed_constituents[sym] = "Constituent fill rejected (insufficient funds or collateral)"

    all_passed = len(failed_constituents) == 0

    return {
        "ok": all_passed,
        "strategy": strategy_name,
        "basket_id": basket_id,
        "execution_id": basket_id,
        "executed_orders_count": total_legs_filled,
        "index_symbol": idx_sym,
        "index_order_id": idx_coid,
        "constituent_order_ids": constituent_order_ids,
        "failed_constituents": failed_constituents,
        "index_contracts": basket.index_contracts,
        "total_legs_filled": total_legs_filled,
        "total_net_cash_impact": basket.total_net_cash_impact,
        "total_commission": basket.total_commission,
        "vega_neutrality_ratio": basket.vega_neutrality_ratio,
        "message": (
            f"Successfully executed Dispersion Arbitrage basket for {idx_sym} "
            f"with {len(constituent_order_ids)}/{len(basket.constituent_leg_requests)} constituents filled."
            if all_passed else
            f"Partial fill on Dispersion Arbitrage basket for {idx_sym}: "
            f"{len(failed_constituents)} constituents failed."
        ),
    }

