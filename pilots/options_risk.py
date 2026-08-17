"""
options_risk.py — Portfolio Risk & Aggregate Greeks Engine for Options and Equities.

Calculates position-level and portfolio-wide net Greeks:
- Net Delta (share equivalents and dollar delta)
- Net Gamma (rate of change of delta per $1 move in underlying)
- Net Theta ($/day decay income/cost)
- Net Vega ($ per 1% move in implied volatility)
- Beta-weighted SPY Delta
"""

from datetime import datetime, timezone
import math
import re
from typing import Any, Dict, List, Optional

import numpy as np
from scipy.stats import norm

from data.paper_account_store import PaperAccountStore, PaperPosition
from settings import settings


# Regex matching option symbol format: AAPL 2026-09-18 $150.00 CALL
_OPTION_SYM_RE = re.compile(
    r"^(?P<ticker>[A-Z0-9]+)\s+(?P<exp>\d{4}-\d{2}-\d{2})\s+\$(?P<strike>\d+(?:\.\d+)?)\s+(?P<type>CALL|PUT)$",
    re.IGNORECASE,
)

TRADING_DAYS_PER_YEAR = 252.0
_DEGENERATE_THRESHOLD = 1e-12


def parse_option_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    """Parses a standardized option leg symbol string into components."""
    m = _OPTION_SYM_RE.match(symbol.strip())
    if not m:
        return None
    return {
        "ticker": m.group("ticker").upper(),
        "expiration": m.group("exp"),
        "strike": float(m.group("strike")),
        "option_type": m.group("type").lower(),
    }


def calculate_black_scholes_greeks(
    spot: float,
    strike: float,
    t_years: float,
    sigma: float,
    option_type: str = "call",
    r: Optional[float] = None,
) -> Dict[str, float]:
    """
    Computes Black-Scholes Greeks and theoretical pricing for a single option contract.
    Enforces degenerate input guards (< 1e-12) and 0DTE intrinsic delta fallback.
    Returns per-share Greeks and pricing metrics:
    - delta: in (-1, 1) or exact [-1, 0, 1] at 0DTE
    - gamma: per $1 underlying move
    - theta_daily / theta: $/day decay (annual theta / 252)
    - theta_annual: $/year decay
    - vega_1pct / vega: $ per 1% change in IV (raw vega / 100)
    - vega_raw: raw vega (dV/dsigma)
    - price: theoretical unit option price
    - intrinsic: max(0, S-K) for calls, max(0, K-S) for puts
    - extrinsic: max(0, price - intrinsic)
    """
    if r is None:
        r = float(getattr(settings, "OPTIONS_RISK_FREE_RATE", 0.045))

    opt_type = str(option_type or "call").lower().strip()

    if spot <= 0 or strike <= 0:
        return {
            "delta": 0.0,
            "gamma": 0.0,
            "theta_daily": 0.0,
            "theta_annual": 0.0,
            "theta": 0.0,
            "vega_1pct": 0.0,
            "vega": 0.0,
            "vega_raw": 0.0,
            "price": 0.0,
            "intrinsic": 0.0,
            "extrinsic": 0.0,
        }

    intrinsic = max(0.0, spot - strike) if opt_type == "call" else max(0.0, strike - spot)

    # 0DTE / Expiration fallback: when T <= 1e-12, intrinsic delta applies, Greeks decay to 0
    if t_years <= _DEGENERATE_THRESHOLD:
        delta = 1.0 if (opt_type == "call" and spot > strike) else (-1.0 if (opt_type == "put" and spot < strike) else 0.0)
        return {
            "delta": float(delta),
            "gamma": 0.0,
            "theta_daily": 0.0,
            "theta_annual": 0.0,
            "theta": 0.0,
            "vega_1pct": 0.0,
            "vega": 0.0,
            "vega_raw": 0.0,
            "price": float(intrinsic),
            "intrinsic": float(intrinsic),
            "extrinsic": 0.0,
        }

    # Missing or degenerate volatility guard
    if sigma <= _DEGENERATE_THRESHOLD or np.isnan(sigma):
        delta = 1.0 if (opt_type == "call" and spot > strike) else (-1.0 if (opt_type == "put" and spot < strike) else 0.0)
        return {
            "delta": float(delta),
            "gamma": 0.0,
            "theta_daily": 0.0,
            "theta_annual": 0.0,
            "theta": 0.0,
            "vega_1pct": 0.0,
            "vega": 0.0,
            "vega_raw": 0.0,
            "rho": 0.0,
            "rho_1pct": 0.0,
            "rho_raw": 0.0,
            "price": float(intrinsic),
            "intrinsic": float(intrinsic),
            "extrinsic": 0.0,
        }

    vol_sqrt_t = sigma * np.sqrt(t_years)
    if vol_sqrt_t < _DEGENERATE_THRESHOLD:
        vol_sqrt_t = _DEGENERATE_THRESHOLD

    d1 = (np.log(spot / strike) + (r + 0.5 * sigma ** 2) * t_years) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    discount = math.exp(-r * t_years)

    if opt_type == "call":
        price = spot * norm.cdf(d1) - strike * discount * norm.cdf(d2)
        delta = float(norm.cdf(d1))
        theta_annual = -(spot * norm.pdf(d1) * sigma) / (2 * np.sqrt(t_years)) - r * strike * discount * norm.cdf(d2)
        raw_rho = float(strike * t_years * discount * norm.cdf(d2))
    else:
        price = strike * discount * norm.cdf(-d2) - spot * norm.cdf(-d1)
        delta = float(norm.cdf(d1) - 1.0)
        theta_annual = -(spot * norm.pdf(d1) * sigma) / (2 * np.sqrt(t_years)) + r * strike * discount * norm.cdf(-d2)
        raw_rho = float(-strike * t_years * discount * norm.cdf(-d2))

    price = float(max(0.0, price))
    denom_gamma = spot * vol_sqrt_t
    gamma = float(norm.pdf(d1) / denom_gamma) if denom_gamma >= _DEGENERATE_THRESHOLD else 0.0
    raw_vega = float(spot * norm.pdf(d1) * np.sqrt(t_years))
    vega_1pct = raw_vega / 100.0  # dollar change per 1% change in vol
    rho_1pct = raw_rho / 100.0   # dollar change per 1% change in interest rate
    theta_daily = float(theta_annual / TRADING_DAYS_PER_YEAR)
    extrinsic = float(max(0.0, price - intrinsic))

    return {
        "delta": delta,
        "gamma": gamma,
        "theta_daily": theta_daily,
        "theta_annual": float(theta_annual),
        "theta": theta_daily,
        "vega_1pct": vega_1pct,
        "vega": vega_1pct,
        "vega_raw": raw_vega,
        "rho": rho_1pct,
        "rho_1pct": rho_1pct,
        "rho_raw": raw_rho,
        "price": price,
        "intrinsic": float(intrinsic),
        "extrinsic": extrinsic,
    }


def calculate_position_greeks(
    position: PaperPosition,
    spot_price: Optional[float],
    *,
    sigma: float = 0.25,
    r: Optional[float] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Computes total Greek exposures for a single paper position (stock or option leg).
    Takes into account position quantity sign (long > 0 vs short < 0) and option contract multiplier (100).
    """
    if r is None:
        r = float(getattr(settings, "OPTIONS_RISK_FREE_RATE", 0.045))

    sym = position.symbol.strip()
    qty = float(position.qty)
    opt_info = parse_option_symbol(sym)

    if spot_price is None or spot_price <= 0:
        return {
            "symbol": sym,
            "asset_type": "option" if opt_info else "stock",
            "base_ticker": opt_info["ticker"] if opt_info else sym,
            "qty": qty,
            "spot_price": None,
            "delta_per_unit": None,
            "gamma_per_unit": None,
            "theta_daily_per_unit": None,
            "vega_1pct_per_unit": None,
            "position_delta": None,
            "position_dollar_delta": None,
            "position_gamma": None,
            "position_theta_daily": None,
            "position_vega_1pct": None,
            "market_value": None,
            "missing_data": True,
        }

    if not opt_info:
        # Stock position
        delta_shares = qty
        dollar_delta = qty * spot_price
        return {
            "symbol": sym,
            "asset_type": "stock",
            "base_ticker": sym,
            "qty": qty,
            "spot_price": spot_price,
            "delta_per_unit": 1.0,
            "gamma_per_unit": 0.0,
            "theta_daily_per_unit": 0.0,
            "vega_1pct_per_unit": 0.0,
            "position_delta": delta_shares,
            "position_dollar_delta": dollar_delta,
            "position_gamma": 0.0,
            "position_theta_daily": 0.0,
            "position_vega_1pct": 0.0,
            "market_value": qty * spot_price,
            "missing_data": False,
        }

    # Option position
    ticker = opt_info["ticker"]
    strike = opt_info["strike"]
    exp_str = opt_info["expiration"]
    opt_type = opt_info["option_type"]

    if now is None:
        now = datetime.now(timezone.utc)
    try:
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        dte = max(0.0, (exp_date - now).total_seconds() / 86400.0)
    except Exception:
        dte = 30.0

    t_years = dte / 365.0

    bs = calculate_black_scholes_greeks(
        spot=spot_price,
        strike=strike,
        t_years=t_years,
        sigma=sigma,
        option_type=opt_type,
        r=r,
    )

    multiplier = 100.0
    effective_qty = qty * multiplier  # Negative for short options

    pos_delta = effective_qty * bs["delta"]
    pos_dollar_delta = pos_delta * spot_price
    pos_gamma = effective_qty * bs["gamma"]
    pos_theta = effective_qty * bs["theta_daily"]
    pos_vega = effective_qty * bs["vega_1pct"]
    market_val = qty * bs["price"] * multiplier

    return {
        "symbol": sym,
        "asset_type": "option",
        "base_ticker": ticker,
        "expiration": exp_str,
        "strike": strike,
        "option_type": opt_type,
        "dte": round(dte, 1),
        "qty": qty,
        "spot_price": spot_price,
        "delta_per_unit": round(bs["delta"], 4),
        "gamma_per_unit": round(bs["gamma"], 4),
        "theta_daily_per_unit": round(bs["theta_daily"], 4),
        "vega_1pct_per_unit": round(bs["vega_1pct"], 4),
        "position_delta": round(pos_delta, 2),
        "position_dollar_delta": round(pos_dollar_delta, 2),
        "position_gamma": round(pos_gamma, 4),
        "position_theta_daily": round(pos_theta, 2),
        "position_vega_1pct": round(pos_vega, 2),
        "market_value": round(market_val, 2),
        "missing_data": False,
    }


def _resolve_symbol_beta(ticker: str) -> float:
    """Resolves regression beta of ticker vs SPY. Defaults to 1.0 for SPY or missing data."""
    if not ticker or str(ticker).upper() == "SPY":
        return 1.0
    try:
        from data.historical_store import HistoricalStore
        store = HistoricalStore()
        beta = store.get_symbol_beta(str(ticker).upper())
        if beta is not None and not math.isnan(beta) and beta > 0:
            return float(beta)
    except Exception:
        pass
    return 1.0


def calculate_portfolio_greeks(
    store: Optional[PaperAccountStore] = None,
    positions: Optional[List[PaperPosition]] = None,
    market_provider: Optional[Any] = None,
    spy_spot: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Computes aggregate portfolio Greeks across all open paper positions.
    Excludes positions with missing quotes/IV from sums and reports them in positions_with_missing_data.
    """
    if store is None and positions is None:
        store = PaperAccountStore()

    if positions is None and store is not None:
        positions = store.get_open_positions()

    if not positions:
        return {
            "total_positions": 0,
            "stock_positions_count": 0,
            "option_positions_count": 0,
            "net_delta_shares": 0.0,
            "net_dollar_delta": 0.0,
            "net_gamma": 0.0,
            "net_theta_daily": 0.0,
            "net_vega_1pct": 0.0,
            "beta_weighted_delta_spy": 0.0,
            "positions_with_missing_data": [],
            "beta_excluded_symbols": [],
            "positions": [],
        }

    # Resolve spot quotes for distinct tickers
    distinct_tickers = set()
    for p in positions:
        opt_info = parse_option_symbol(p.symbol)
        ticker = opt_info["ticker"] if opt_info else p.symbol.strip().upper()
        distinct_tickers.add(ticker)

    spot_map: Dict[str, Optional[float]] = {}
    if market_provider is None:
        try:
            from data.market_data import get_provider
            market_provider = get_provider()
        except Exception:
            market_provider = None

    if market_provider is not None:
        for t in distinct_tickers:
            try:
                quote = market_provider.get_latest_quote(t)
                if quote and getattr(quote, "price", 0) and float(quote.price) > 0:
                    spot_map[t] = float(quote.price)
                else:
                    spot_map[t] = None
            except Exception:
                spot_map[t] = None

    # Resolve SPY spot
    if spy_spot is None:
        spy_spot = spot_map.get("SPY") or 500.0

    # Position calculations & aggregates
    pos_breakdowns: List[Dict[str, Any]] = []
    positions_with_missing_data: List[str] = []
    beta_excluded_symbols: List[str] = []
    net_delta_shares = 0.0
    net_dollar_delta = 0.0
    net_beta_dollar_delta = 0.0
    net_gamma = 0.0
    net_theta_daily = 0.0
    net_vega_1pct = 0.0
    stock_count = 0
    option_count = 0

    now = datetime.now(timezone.utc)

    for pos in positions:
        opt_info = parse_option_symbol(pos.symbol)
        ticker = opt_info["ticker"] if opt_info else pos.symbol.strip().upper()
        spot = spot_map.get(ticker)
        beta = _resolve_symbol_beta(ticker)

        if spot is None:
            positions_with_missing_data.append(pos.symbol)

        g = calculate_position_greeks(pos, spot_price=spot, now=now)
        g["beta"] = beta

        if not g.get("missing_data", False) and g.get("position_dollar_delta") is not None:
            dollar_delta = float(g["position_dollar_delta"])
            beta_dollar_delta = dollar_delta * beta
            g["beta_dollar_delta"] = round(beta_dollar_delta, 2)
            pos_breakdowns.append(g)

            net_delta_shares += g["position_delta"]
            net_dollar_delta += dollar_delta
            net_beta_dollar_delta += beta_dollar_delta
            net_gamma += g["position_gamma"]
            net_theta_daily += g["position_theta_daily"]
            net_vega_1pct += g["position_vega_1pct"]

            if g["asset_type"] == "option":
                option_count += 1
            else:
                stock_count += 1
        else:
            g["beta_dollar_delta"] = None
            pos_breakdowns.append(g)
            beta_excluded_symbols.append(pos.symbol)


    # Beta-weighted SPY Delta: (sum_i DollarDelta_i * Beta_i) / SPY_Spot
    beta_weighted_delta_spy = (net_beta_dollar_delta / spy_spot) if spy_spot > 0 else 0.0

    return {
        "total_positions": len(positions),
        "stock_positions_count": stock_count,
        "option_positions_count": option_count,
        "net_delta_shares": round(net_delta_shares, 2),
        "net_dollar_delta": round(net_dollar_delta, 2),
        "net_gamma": round(net_gamma, 4),
        "net_theta_daily": round(net_theta_daily, 2),
        "net_vega_1pct": round(net_vega_1pct, 2),
        "beta_weighted_delta_spy": round(beta_weighted_delta_spy, 2),
        "positions_with_missing_data": positions_with_missing_data,
        "beta_excluded_symbols": beta_excluded_symbols,
        "positions": pos_breakdowns,
    }


