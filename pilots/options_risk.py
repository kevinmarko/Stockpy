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
import logging
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.stats import norm

from data.paper_account_store import PaperAccountStore, PaperPosition
from settings import settings

logger = logging.getLogger(__name__)


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
            "rho": 0.0,
            "rho_1pct": 0.0,
            "rho_raw": 0.0,
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


def _resolve_symbol_beta(ticker: str) -> Tuple[float, bool]:
    """Resolves regression beta vs SPY for ticker.

    Returns ``(beta, is_measured)``:
      - ``(1.0, True)`` for SPY/VOO/IVV -- a deliberate identity default, not
        a fallback (these ARE the market proxy, beta=1.0 by construction).
      - ``(beta, True)`` when ``pilots.rolling_beta.rolling_beta_view``
        resolves a real regression beta from >= 60 days of cached local
        price history for both this ticker and SPY.
      - ``(1.0, False)`` when no real beta could be resolved. This is NOT
        silently indistinguishable from a genuinely-measured beta of 1.0:
        it is logged at WARNING and callers can inspect the second element
        (surfaced by ``calculate_portfolio_greeks`` as
        ``position["beta_is_estimated"]`` / the top-level
        ``symbols_with_estimated_beta`` list).

    A second-tier live-fetch fallback via ``data.fmp_fundamentals.compute_beta``
    used to live here but was removed (2026-08): it was called with the wrong
    signature (a bare ticker string against a function requiring two
    ``pd.Series`` arguments), so it always raised and was silently swallowed,
    collapsing straight to the ``1.0`` default. A correctly-fixed version,
    mirroring the one real working caller (``api/ws_api.py``'s
    ``_compute_betas_sync``: ``HistoricalStore.get_bars(..., lookback_days=400)``
    + ``compute_beta(stock_returns, spy_returns, min_obs=60)``), would source
    the SAME ``HistoricalStore`` bars for the SAME two tickers as
    ``rolling_beta_view`` above, but with LESS lookback (400 days vs
    ``rolling_beta_view``'s ``max(504, window*3)`` = 504 days) for the SAME
    minimum-observation floor (60). It could not succeed in any case where
    the primary tier above already failed for lack of cached history --
    fixing its call signature would not add real coverage, only the
    appearance of a working fallback. Removed rather than repaired.
    """
    clean = str(ticker or "").strip().upper()
    if clean in ("SPY", "VOO", "IVV"):
        return 1.0, True
    try:
        from pilots.rolling_beta import rolling_beta_view
        view = rolling_beta_view(clean, window=60)
        series = view.get("series", [])
        if series and isinstance(series, list):
            latest = series[-1]
            if isinstance(latest, dict) and latest.get("beta") is not None:
                b = float(latest["beta"])
                if b == b and b not in (float("inf"), float("-inf")):
                    return b, True
    except Exception as exc:  # noqa: BLE001 — dead-letter (CONSTRAINT #6)
        logger.debug("_resolve_symbol_beta(%s): rolling_beta_view failed: %s", clean, exc)

    logger.warning(
        "_resolve_symbol_beta(%s): no measured beta available (insufficient "
        "cached local price history for a %s-day rolling regression) -- "
        "defaulting to beta=1.0. This is an estimate, not a measurement.",
        clean, 60,
    )
    return 1.0, False


def calculate_portfolio_greeks(
    positions: Optional[Sequence[Any]] = None,
    store: Optional[PaperAccountStore] = None,
    market_provider: Optional[Any] = None,
    spy_spot: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Computes aggregate portfolio Greeks: Net Delta (shares), Net Dollar Delta ($),
    Net Gamma, Net Daily Theta ($), Net Vega (1% IV change $), and Beta-Weighted SPY Delta.
    """
    if positions is None and store is not None:
        positions = store.get_open_positions()

    if positions is None:
        positions = []

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
            "symbols_with_estimated_beta": [],
            "spy_spot": None,
            # Vacuously True: an empty book has no delta to beta-weight, so
            # there is nothing an unresolved SPY quote could have distorted
            # (contrast with the False-on-genuine-failure case below).
            "spy_spot_resolved": True,
            "positions": [],
        }

    # Resolve spot quotes for distinct tickers. SPY is included here too
    # (when the caller didn't already supply spy_spot) so it goes through the
    # exact same market_provider.get_latest_quote() mechanism -- and the same
    # test seam -- as every other symbol, rather than a separate code path.
    # This function used to fall back to a hardcoded $500.0 whenever SPY
    # wasn't already among the held positions (CONSTRAINT #4 violation --
    # see docs/known_issues/options_risk_fabricated_spy_spot.md); it now
    # never fabricates a price.
    distinct_tickers = set()
    for p in positions:
        opt_info = parse_option_symbol(p.symbol)
        ticker = opt_info["ticker"] if opt_info else p.symbol.strip().upper()
        distinct_tickers.add(ticker)
    if spy_spot is None:
        distinct_tickers.add("SPY")

    spot_map: Dict[str, Optional[float]] = {}
    if market_provider is None:
        try:
            from data.market_data import get_provider
            market_provider = get_provider()
        except Exception:
            market_provider = None

    if market_provider is not None:
        # Batched (F6, docs/module_efficiency_redundancy_audit.md): one
        # get_quotes_batch() call for the whole distinct-ticker set instead
        # of N get_latest_quote() calls. Same per-ticker None-on-failure
        # contract as the prior loop -- get_quotes_batch() already
        # dead-letters a symbol that failed to resolve, so a ticker simply
        # absent from the result maps to None here exactly as an exception
        # from the old per-ticker try/except did.
        try:
            quotes = market_provider.get_quotes_batch(list(distinct_tickers))
        except Exception:
            quotes = {}
        for t in distinct_tickers:
            quote = quotes.get(t.upper())
            if quote and getattr(quote, "price", 0) and float(quote.price) > 0:
                spot_map[t] = float(quote.price)
            else:
                spot_map[t] = None

    # Resolve SPY spot -- a caller-supplied value always wins; otherwise use
    # the real quote just resolved above. When neither is available (a
    # genuine market-data outage), beta_weighted_delta_spy is reported via
    # spy_spot_resolved=False rather than silently computed off a fabricated
    # price.
    spy_spot_resolved = True
    if spy_spot is None or spy_spot <= 0:
        spy_spot = spot_map.get("SPY")
        if spy_spot is None or spy_spot <= 0:
            spy_spot_resolved = False
            spy_spot = None

    # Position calculations & aggregates
    pos_breakdowns: List[Dict[str, Any]] = []
    positions_with_missing_data: List[str] = []
    beta_excluded_symbols: List[str] = []
    symbols_with_estimated_beta: List[str] = []
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
        beta_val, beta_is_measured = _resolve_symbol_beta(ticker)
        if not beta_is_measured:
            symbols_with_estimated_beta.append(pos.symbol)

        if spot is None:
            positions_with_missing_data.append(pos.symbol)

        g = calculate_position_greeks(pos, spot_price=spot, now=now)
        g["symbol_beta"] = beta_val
        g["beta_is_estimated"] = not beta_is_measured

        if not g.get("missing_data", False) and g.get("position_dollar_delta") is not None:
            dollar_delta = float(g["position_dollar_delta"])
            beta_dollar_delta = dollar_delta * beta_val
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

    # Beta-weighted SPY Delta in SPY share equivalents: (sum_i DollarDelta_i * Beta_i) / SPY_Spot.
    # Never fabricated: 0.0 (not a divide against a fake price) whenever
    # spy_spot could not be honestly resolved.
    beta_weighted_delta_spy = (
        (net_beta_dollar_delta / spy_spot) if (spy_spot_resolved and spy_spot) else 0.0
    )

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
        "symbols_with_estimated_beta": symbols_with_estimated_beta,
        "spy_spot": spy_spot,
        "spy_spot_resolved": spy_spot_resolved,
        "positions": pos_breakdowns,
    }


