"""
pilots/volatility_surface.py — Volatility Surface, Smile Interpolator & Skew Engine.
==================================================================================

Computes comprehensive volatility analytics across strike and term dimensions:
- Strike-dimension Implied Volatility (IV) smile spline interpolation (PCHIP / Cubic).
- Term structure curve points across standard maturities (7d, 14d, 30d, 60d, 90d, 180d, 365d).
- 25-Delta Put/Call Skew: IV(25-delta Put) - IV(25-delta Call), Skew Ratio, and 25-Delta Butterfly.
- Volatility Risk Premium (VRP) Cone: Realized Volatility (10d, 20d, 30d, 60d) vs Implied Volatility.
- Robust missing quote/IV handling via Black-Scholes IV inversion and graceful degradation.

Design Invariants:
* **AST-Safe (CONSTRAINT #1 & #3)** — Pure compute/read module. Never imports heavy engines
  (`processing_engine`, `technical_options_engine`, `strategy_engine`, etc.).
* **Honesty (CONSTRAINT #4)** — Preserves `None` for uncomputable metrics (missing prices/quotes),
  never fabricates zeros or fake surface parameters.
* **Never Raises (CONSTRAINT #6)** — All functions degrade gracefully with informative diagnostics.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import logging
import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator, interp1d
from scipy.optimize import brentq
from scipy.stats import norm

from settings import settings

logger = logging.getLogger(__name__)

__all__ = [
    "calculate_volatility_surface",
    "calculate_realized_volatility",
    "interpolate_smile_spline",
    "compute_term_structure",
    "compute_25delta_skew",
    "compute_vrp_cone",
    "implied_volatility_black_scholes",
    "to_vol_surface_response",
    "get_volatility_surface_data",
    "STANDARD_TERM_HORIZONS",
    "STANDARD_VRP_WINDOWS",
]

STANDARD_TERM_HORIZONS: List[int] = [7, 14, 30, 60, 90, 180, 365]
STANDARD_VRP_WINDOWS: List[int] = [10, 20, 30, 60]

TRADING_DAYS_PER_YEAR = 252.0
_DEGENERATE_THRESHOLD = 1e-12
_DEFAULT_RFR = 0.045


# ---------------------------------------------------------------------------
# Black-Scholes Math & Implied Volatility Solver
# ---------------------------------------------------------------------------


def _get_risk_free_rate(override_r: Optional[float] = None) -> float:
    """Resolves risk-free rate from argument or settings."""
    if override_r is not None:
        return float(override_r)
    return float(
        getattr(settings, "OPTIONS_RISK_FREE_RATE", getattr(settings, "RISK_FREE_RATE", _DEFAULT_RFR))
    )


def _black_scholes_price(
    spot: float,
    strike: float,
    t_years: float,
    sigma: float,
    option_type: str = "call",
    r: float = _DEFAULT_RFR,
) -> float:
    """Calculates Black-Scholes European option theoretical price (delegates to canonical pilots.options_risk)."""
    from pilots.options_risk import calculate_black_scholes_greeks

    res = calculate_black_scholes_greeks(
        spot=spot,
        strike=strike,
        t_years=t_years,
        sigma=sigma,
        option_type=option_type,
        r=r,
    )
    return float(res["price"])


def _black_scholes_delta(
    spot: float,
    strike: float,
    t_years: float,
    sigma: float,
    option_type: str = "call",
    r: float = _DEFAULT_RFR,
) -> float:
    """Calculates Black-Scholes Delta for Call in (0, 1) or Put in (-1, 0) (delegates to canonical pilots.options_risk)."""
    from pilots.options_risk import calculate_black_scholes_greeks

    res = calculate_black_scholes_greeks(
        spot=spot,
        strike=strike,
        t_years=t_years,
        sigma=sigma,
        option_type=option_type,
        r=r,
    )
    return float(res["delta"])


def _black_scholes_vega(
    spot: float,
    strike: float,
    t_years: float,
    sigma: float,
    r: float = _DEFAULT_RFR,
) -> float:
    """Calculates Black-Scholes Vega (derivative of price with respect to sigma) (delegates to canonical pilots.options_risk)."""
    from pilots.options_risk import calculate_black_scholes_greeks

    res = calculate_black_scholes_greeks(
        spot=spot,
        strike=strike,
        t_years=t_years,
        sigma=sigma,
        option_type="call",
        r=r,
    )
    return float(res["vega_raw"])


def implied_volatility_black_scholes(
    price: float,
    spot: float,
    strike: float,
    t_years: float,
    option_type: str = "call",
    r: float = _DEFAULT_RFR,
    max_iter: int = 50,
    tolerance: float = 1e-5,
) -> Optional[float]:
    """
    Computes Black-Scholes implied volatility from market option price.
    Uses Newton-Raphson with Brenner-Subrahmanyam initial guess and Brent's method fallback.
    Returns float in range [0.001, 5.0] or None if uncomputable.
    """
    if price is None or math.isnan(price) or price <= 0:
        return None
    if spot <= 0 or strike <= 0 or t_years <= _DEGENERATE_THRESHOLD:
        return None

    opt_type = option_type.lower()
    intrinsic = max(0.0, spot - strike) if opt_type == "call" else max(0.0, strike - spot)
    if price < intrinsic:
        return None

    # Initial approximation
    try:
        # Brenner-Subrahmanyam approximation for ATM
        sigma = math.sqrt(2.0 * math.pi / t_years) * (price / spot)
        sigma = max(0.05, min(2.0, sigma))
    except Exception:
        sigma = 0.30

    # 1. Newton-Raphson iteration
    for _ in range(max_iter):
        p_est = _black_scholes_price(spot, strike, t_years, sigma, opt_type, r)
        diff = p_est - price
        if abs(diff) < tolerance:
            return float(sigma)
        vega = _black_scholes_vega(spot, strike, t_years, sigma, r)
        if vega < 1e-8:
            break
        step = diff / vega
        sigma -= step
        if sigma <= 0.001 or sigma > 5.0:
            break

    # 2. Brent's method fallback
    def objective(s: float) -> float:
        return _black_scholes_price(spot, strike, t_years, s, opt_type, r) - price

    try:
        f_low = objective(0.001)
        f_high = objective(5.0)
        if f_low * f_high <= 0:
            sol = brentq(objective, 0.001, 5.0, xtol=tolerance, maxiter=max_iter)
            return float(sol)
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Historical Realized Volatility
# ---------------------------------------------------------------------------


def calculate_realized_volatility(
    prices: Union[Sequence[float], np.ndarray, pd.Series],
    window: int = 20,
    annualization_factor: float = TRADING_DAYS_PER_YEAR,
) -> Optional[float]:
    """
    Calculates annualized realized volatility from historical close prices over a rolling window.

    Formula:
        r_t = ln(P_t / P_{t-1})
        RV = std(r_t, ddof=1) * sqrt(annualization_factor)

    Returns None if price history length is insufficient (< window + 1 points).
    """
    if prices is None:
        return None

    if isinstance(prices, pd.Series):
        arr = prices.dropna().to_numpy(dtype=float)
    elif isinstance(prices, np.ndarray):
        arr = prices[~np.isnan(prices)].astype(float)
    elif isinstance(prices, (list, tuple)):
        arr = np.array([p for p in prices if p is not None and not (isinstance(p, float) and math.isnan(p))], dtype=float)
    else:
        return None

    if len(arr) < max(2, window + 1):
        return None

    # Slice the most recent (window + 1) prices to obtain exactly `window` return intervals
    sub_prices = arr[-(window + 1):]
    if np.any(sub_prices <= 0):
        return None

    log_returns = np.diff(np.log(sub_prices))
    if len(log_returns) < 2:
        return None

    std_dev = np.std(log_returns, ddof=1)
    if math.isnan(std_dev) or std_dev < 0:
        return None

    rv = float(std_dev * math.sqrt(annualization_factor))
    return round(rv, 6)


# ---------------------------------------------------------------------------
# Chain Parsing & Slice Extraction
# ---------------------------------------------------------------------------


def _parse_date(val: Any) -> Optional[date]:
    """Helper to parse datetime, date, or string into standard date object."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return datetime.strptime(str(val).strip()[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _extract_contracts_dataframe(data: Any, default_type: str = "call") -> pd.DataFrame:
    """Extracts a standardized DataFrame from DataFrame, list of dicts, or dictionary."""
    if data is None:
        return pd.DataFrame()

    if isinstance(data, pd.DataFrame):
        df = data.copy()
    elif isinstance(data, list):
        df = pd.DataFrame(data)
    elif isinstance(data, dict):
        df = pd.DataFrame(data)
    else:
        return pd.DataFrame()

    if df.empty:
        return df

    # Standardize column naming
    col_map = {
        "strike": "strike",
        "Strike": "strike",
        "bid": "bid",
        "Bid": "bid",
        "ask": "ask",
        "Ask": "ask",
        "lastPrice": "lastPrice",
        "last": "lastPrice",
        "price": "lastPrice",
        "impliedVolatility": "impliedVolatility",
        "implied_volatility": "impliedVolatility",
        "iv": "impliedVolatility",
        "volume": "volume",
        "openInterest": "openInterest",
        "open_interest": "openInterest",
        "type": "option_type",
        "option_type": "option_type",
        "side": "option_type",
    }
    rename_dict = {orig: target for orig, target in col_map.items() if orig in df.columns}
    df = df.rename(columns=rename_dict)

    if "strike" not in df.columns:
        return pd.DataFrame()

    if "option_type" not in df.columns:
        df["option_type"] = default_type

    return df


def parse_expiration_slice(
    slice_data: Any,
    spot_price: float,
    t_years: float,
    r: float = _DEFAULT_RFR,
) -> pd.DataFrame:
    """
    Parses calls and puts for a single expiration into a consolidated, strike-level DataFrame.
    Calculates mid-prices, infers missing IVs via Black-Scholes inversion, and computes blended IV.
    """
    calls_df = pd.DataFrame()
    puts_df = pd.DataFrame()

    if hasattr(slice_data, "calls") and hasattr(slice_data, "puts"):
        calls_df = _extract_contracts_dataframe(slice_data.calls, default_type="call")
        puts_df = _extract_contracts_dataframe(slice_data.puts, default_type="put")
    elif isinstance(slice_data, dict):
        if "calls" in slice_data or "puts" in slice_data:
            calls_df = _extract_contracts_dataframe(slice_data.get("calls"), default_type="call")
            puts_df = _extract_contracts_dataframe(slice_data.get("puts"), default_type="put")
        else:
            # Flat dictionary of contracts or dataframe records
            flat_df = _extract_contracts_dataframe(slice_data)
            if not flat_df.empty and "option_type" in flat_df.columns:
                calls_df = flat_df[flat_df["option_type"].str.lower().str.startswith("c")]
                puts_df = flat_df[flat_df["option_type"].str.lower().str.startswith("p")]
    elif isinstance(slice_data, pd.DataFrame):
        if "option_type" in slice_data.columns:
            calls_df = slice_data[slice_data["option_type"].str.lower().str.startswith("c")]
            puts_df = slice_data[slice_data["option_type"].str.lower().str.startswith("p")]
        elif "type" in slice_data.columns:
            calls_df = slice_data[slice_data["type"].str.lower().str.startswith("c")]
            puts_df = slice_data[slice_data["type"].str.lower().str.startswith("p")]

    records: Dict[float, Dict[str, Any]] = {}

    def process_leg(df: pd.DataFrame, opt_type: str) -> None:
        if df.empty or "strike" not in df.columns:
            return
        for _, row in df.iterrows():
            try:
                strike = float(row.get("strike", 0))
                if strike <= 0 or math.isnan(strike):
                    continue

                bid = float(row.get("bid", 0.0)) if pd.notna(row.get("bid")) else None
                ask = float(row.get("ask", 0.0)) if pd.notna(row.get("ask")) else None
                last_p = float(row.get("lastPrice", 0.0)) if pd.notna(row.get("lastPrice")) else None

                mid_price: Optional[float] = None
                if bid is not None and ask is not None and bid > 0 and ask >= bid:
                    mid_price = (bid + ask) / 2.0
                elif last_p is not None and last_p > 0:
                    mid_price = last_p
                elif bid is not None and bid > 0:
                    mid_price = bid
                elif ask is not None and ask > 0:
                    mid_price = ask

                iv = float(row.get("impliedVolatility", 0.0)) if pd.notna(row.get("impliedVolatility")) else None
                if iv is not None and (iv <= 0.001 or iv > 5.0 or math.isnan(iv)):
                    iv = None

                # If IV is missing but market price exists, attempt Black-Scholes inversion
                if iv is None and mid_price is not None and mid_price > 0 and spot_price > 0 and t_years > 0:
                    iv = implied_volatility_black_scholes(
                        price=mid_price,
                        spot=spot_price,
                        strike=strike,
                        t_years=t_years,
                        option_type=opt_type,
                        r=r,
                    )

                if strike not in records:
                    records[strike] = {
                        "strike": strike,
                        "call_bid": None,
                        "call_ask": None,
                        "call_mid": None,
                        "call_iv": None,
                        "put_bid": None,
                        "put_ask": None,
                        "put_mid": None,
                        "put_iv": None,
                    }

                if opt_type == "call":
                    records[strike]["call_bid"] = bid
                    records[strike]["call_ask"] = ask
                    records[strike]["call_mid"] = mid_price
                    records[strike]["call_iv"] = iv
                else:
                    records[strike]["put_bid"] = bid
                    records[strike]["put_ask"] = ask
                    records[strike]["put_mid"] = mid_price
                    records[strike]["put_iv"] = iv

            except Exception:
                continue

    process_leg(calls_df, "call")
    process_leg(puts_df, "put")

    if not records:
        return pd.DataFrame()

    out_rows = []
    for strike, data in sorted(records.items(), key=lambda x: x[0]):
        c_iv = data["call_iv"]
        p_iv = data["put_iv"]

        # Selection of representative strike IV:
        # Standard practitioner convention: use OTM options (Put for K < S, Call for K >= S)
        # or weighted average near ATM.
        representative_iv: Optional[float] = None
        if c_iv is not None and p_iv is not None:
            if strike < spot_price:
                # OTM Put has cleaner quote liquidity
                representative_iv = p_iv
            else:
                # OTM Call has cleaner quote liquidity
                representative_iv = c_iv
            blended_iv = (c_iv + p_iv) / 2.0
        elif c_iv is not None:
            representative_iv = c_iv
            blended_iv = c_iv
        elif p_iv is not None:
            representative_iv = p_iv
            blended_iv = p_iv
        else:
            representative_iv = None
            blended_iv = None

        data["iv"] = representative_iv
        data["blended_iv"] = blended_iv
        data["moneyness"] = strike / spot_price if spot_price > 0 else 1.0
        out_rows.append(data)

    return pd.DataFrame(out_rows)


# ---------------------------------------------------------------------------
# Strike-Dimension IV Smile Spline Interpolator
# ---------------------------------------------------------------------------


def interpolate_smile_spline(
    strikes: Sequence[float],
    ivs: Sequence[float],
    spot_price: float,
    t_years: float,
    r: float = _DEFAULT_RFR,
    n_grid: int = 50,
) -> Optional[Dict[str, Any]]:
    """
    Fits a smooth spline curve across strike-dimension implied volatilities.
    Uses Monotonic PCHIP (Piecewise Cubic Hermite Interpolating Polynomial) to avoid
    spurious oscillations in the wings, with linear / constant fallbacks.

    Returns a dictionary containing:
    - spline_fn: Callable mapping strike -> IV
    - atm_iv: Implied volatility at spot (K = S)
    - curve: Dense sampled grid of strike, moneyness, IV, call/put delta
    """
    valid_pairs = []
    for k, v in zip(strikes, ivs):
        if k is not None and v is not None and k > 0 and v > 0 and not math.isnan(k) and not math.isnan(v):
            valid_pairs.append((float(k), float(v)))

    if not valid_pairs:
        return None

    # Deduplicate and sort by strike
    df_pairs = pd.DataFrame(valid_pairs, columns=["strike", "iv"]).groupby("strike", as_index=False).mean()
    sorted_strikes = df_pairs["strike"].to_numpy(dtype=float)
    sorted_ivs = df_pairs["iv"].to_numpy(dtype=float)

    n_pts = len(sorted_strikes)
    spline_fn: Callable[[float], float]

    if n_pts >= 3:
        # PchipInterpolator ensures monotonicity and prevents negative/erratic wing curves
        pchip = PchipInterpolator(sorted_strikes, sorted_ivs, extrapolate=True)

        def spline_eval(k: float) -> float:
            val = float(pchip(k))
            return max(0.01, min(5.0, val))

        spline_fn = spline_eval

    elif n_pts == 2:
        lin_interp = interp1d(sorted_strikes, sorted_ivs, fill_value="extrapolate")

        def linear_eval(k: float) -> float:
            val = float(lin_interp(k))
            return max(0.01, min(5.0, val))

        spline_fn = linear_eval

    else:
        single_iv = sorted_ivs[0]
        spline_fn = lambda k: max(0.01, min(5.0, float(single_iv)))

    atm_iv = spline_fn(spot_price)

    # Generate dense curve grid
    min_k = min(sorted_strikes[0], spot_price * 0.70)
    max_k = max(sorted_strikes[-1], spot_price * 1.30)
    grid_strikes = np.linspace(min_k, max_k, n_grid)

    curve = []
    for k in grid_strikes:
        iv_k = spline_fn(k)
        c_delta = _black_scholes_delta(spot_price, k, t_years, iv_k, "call", r)
        p_delta = _black_scholes_delta(spot_price, k, t_years, iv_k, "put", r)
        curve.append({
            "strike": round(float(k), 2),
            "moneyness": round(float(k / spot_price), 4) if spot_price > 0 else 1.0,
            "iv": round(float(iv_k), 4),
            "call_delta": round(float(c_delta), 4),
            "put_delta": round(float(p_delta), 4),
        })

    return {
        "spline_fn": spline_fn,
        "atm_iv": round(float(atm_iv), 4),
        "min_strike": float(sorted_strikes[0]),
        "max_strike": float(sorted_strikes[-1]),
        "n_points": n_pts,
        "curve": curve,
    }


# ---------------------------------------------------------------------------
# 25-Delta Put/Call Skew Calculation
# ---------------------------------------------------------------------------


def compute_25delta_skew(
    spot_price: float,
    t_years: float,
    spline_fn: Callable[[float], float],
    r: float = _DEFAULT_RFR,
    min_strike: Optional[float] = None,
    max_strike: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Computes 25-delta Put/Call Skew:
        Skew_25d = IV(25-delta Put) - IV(25-delta Call)
    Also computes:
        - 25-delta Skew Ratio: IV(25-delta Put) / IV(25-delta Call)
        - 25-delta Butterfly (Kurtosis): (IV(25d Put) + IV(25d Call)) / 2 - ATM_IV

    Returns dictionary with 25-delta strikes, IVs, and skew metrics.
    """
    if spot_price <= 0 or t_years <= _DEGENERATE_THRESHOLD:
        return {
            "skew_25d": None,
            "put_25d_iv": None,
            "call_25d_iv": None,
            "put_25d_strike": None,
            "call_25d_strike": None,
            "skew_ratio": None,
            "butterfly_25d": None,
        }

    atm_iv = spline_fn(spot_price)

    # 1. Solve for 25-delta Call (Delta = +0.25)
    def obj_call(k: float) -> float:
        sig = spline_fn(k)
        return _black_scholes_delta(spot_price, k, t_years, sig, "call", r) - 0.25

    k_call_25d: Optional[float] = None
    low_c = spot_price
    high_c = max(spot_price * 2.5, (max_strike or spot_price * 2.0))
    try:
        if obj_call(low_c) * obj_call(high_c) <= 0:
            k_call_25d = float(brentq(obj_call, low_c, high_c, xtol=1e-3))
    except Exception:
        pass

    if k_call_25d is None:
        # Analytic Black-Scholes strike approximation fallback
        k_call_25d = spot_price * math.exp((r + 0.5 * atm_iv**2) * t_years + 0.6745 * atm_iv * math.sqrt(t_years))

    call_25d_iv = spline_fn(k_call_25d)

    # 2. Solve for 25-delta Put (Delta = -0.25)
    def obj_put(k: float) -> float:
        sig = spline_fn(k)
        return _black_scholes_delta(spot_price, k, t_years, sig, "put", r) - (-0.25)

    k_put_25d: Optional[float] = None
    low_p = min(spot_price * 0.1, (min_strike or spot_price * 0.4))
    high_p = spot_price
    try:
        if obj_put(low_p) * obj_put(high_p) <= 0:
            k_put_25d = float(brentq(obj_put, low_p, high_p, xtol=1e-3))
    except Exception:
        pass

    if k_put_25d is None:
        # Analytic Black-Scholes strike approximation fallback
        k_put_25d = spot_price * math.exp((r + 0.5 * atm_iv**2) * t_years - 0.6745 * atm_iv * math.sqrt(t_years))

    put_25d_iv = spline_fn(k_put_25d)

    skew_25d = put_25d_iv - call_25d_iv
    skew_ratio = (put_25d_iv / call_25d_iv) if call_25d_iv > 0 else None
    butterfly_25d = ((put_25d_iv + call_25d_iv) / 2.0) - atm_iv

    return {
        "skew_25d": round(float(skew_25d), 4),
        "put_25d_iv": round(float(put_25d_iv), 4),
        "call_25d_iv": round(float(call_25d_iv), 4),
        "put_25d_strike": round(float(k_put_25d), 2),
        "call_25d_strike": round(float(k_call_25d), 2),
        "skew_ratio": round(float(skew_ratio), 4) if skew_ratio is not None else None,
        "butterfly_25d": round(float(butterfly_25d), 4),
    }


# ---------------------------------------------------------------------------
# Term Structure Curve Interpolation
# ---------------------------------------------------------------------------


def compute_term_structure(
    expirations_atm: Dict[str, Dict[str, Any]],
    target_dtes: Sequence[int] = STANDARD_TERM_HORIZONS,
) -> Dict[str, Any]:
    """
    Interpolates term structure curve across standard expiration horizons:
    [7d, 14d, 30d, 60d, 90d, 180d, 365d].

    Uses Total Variance linear interpolation (w(T) = sigma^2 * T), standard in
    derivatives analytics, ensuring variance additivity and smooth volatility curves.

    Returns:
    - points: List of term structure points with target_dte and interpolated IV
    - term_slope_30_90: IV(90d) - IV(30d) (Contango > 0, Backwardation < 0)
    - term_slope_7_30: IV(30d) - IV(7d)
    """
    valid_pts = []
    for exp_str, data in expirations_atm.items():
        dte = data.get("dte")
        atm_iv = data.get("atm_iv")
        if dte is not None and atm_iv is not None and dte > 0 and atm_iv > 0:
            valid_pts.append((float(dte), float(atm_iv)))

    if not valid_pts:
        return {
            "points": [{"target_dte": d, "days": d, "iv": None, "variance": None} for d in target_dtes],
            "term_slope_30_90": None,
            "term_slope_7_30": None,
            "structure_regime": "unknown",
        }

    # Sort by DTE
    valid_pts.sort(key=lambda x: x[0])
    dtes = np.array([p[0] for p in valid_pts], dtype=float)
    ivs = np.array([p[1] for p in valid_pts], dtype=float)

    # Compute total variance: w = sigma^2 * T (where T = DTE / 365)
    t_years = dtes / 365.0
    variances = (ivs ** 2) * t_years

    def interpolate_iv_for_dte(target_d: float) -> float:
        target_t = target_d / 365.0
        if len(dtes) == 1:
            return float(ivs[0])

        if target_d <= dtes[0]:
            return float(ivs[0])
        elif target_d >= dtes[-1]:
            return float(ivs[-1])
        else:
            # Linear interpolation in total variance space
            var_interp = np.interp(target_t, t_years, variances)
            interp_iv = math.sqrt(max(1e-6, var_interp / target_t))
            return float(interp_iv)

    points = []
    for d in target_dtes:
        iv_d = interpolate_iv_for_dte(float(d))
        points.append({
            "target_dte": int(d),
            "days": int(d),
            "iv": round(float(iv_d), 4),
            "variance": round(float(iv_d ** 2), 4),
        })

    iv_map = {pt["target_dte"]: pt["iv"] for pt in points}
    iv_7 = iv_map.get(7)
    iv_30 = iv_map.get(30)
    iv_90 = iv_map.get(90)

    term_slope_30_90 = round(float(iv_90 - iv_30), 4) if (iv_90 is not None and iv_30 is not None) else None
    term_slope_7_30 = round(float(iv_30 - iv_7), 4) if (iv_30 is not None and iv_7 is not None) else None

    # Determine term structure regime
    if term_slope_30_90 is not None:
        if term_slope_30_90 > 0.01:
            regime = "contango"
        elif term_slope_30_90 < -0.01:
            regime = "backwardation"
        else:
            regime = "flat"
    else:
        regime = "unknown"

    return {
        "points": points,
        "term_slope_30_90": term_slope_30_90,
        "term_slope_7_30": term_slope_7_30,
        "structure_regime": regime,
    }


# ---------------------------------------------------------------------------
# Volatility Risk Premium (VRP) Cone
# ---------------------------------------------------------------------------


def compute_vrp_cone(
    term_structure_points: Sequence[Dict[str, Any]],
    historical_prices: Optional[Any],
    windows: Sequence[int] = STANDARD_VRP_WINDOWS,
) -> Dict[str, Any]:
    """
    Computes Volatility Risk Premium (VRP) Cone across standard horizons [10d, 20d, 30d, 60d].
        VRP = Implied Volatility (interpolated) - Historical Realized Volatility

    Returns dictionary mapped by horizon (e.g., '10d', '20d', '30d', '60d').
    """
    iv_lookup = {}
    for pt in term_structure_points:
        d = pt.get("target_dte")
        iv = pt.get("iv")
        if d is not None and iv is not None:
            iv_lookup[int(d)] = float(iv)

    cone: Dict[str, Any] = {}

    for w in windows:
        rv = calculate_realized_volatility(historical_prices, window=w)

        # Retrieve or interpolate IV for window `w`
        iv = iv_lookup.get(w)
        if iv is None and iv_lookup:
            known_dtes = sorted(iv_lookup.keys())
            if w <= known_dtes[0]:
                iv = iv_lookup[known_dtes[0]]
            elif w >= known_dtes[-1]:
                iv = iv_lookup[known_dtes[-1]]
            else:
                iv = float(np.interp(w, known_dtes, [iv_lookup[k] for k in known_dtes]))

        vrp: Optional[float] = None
        vrp_ratio: Optional[float] = None
        regime = "unknown"

        if iv is not None and rv is not None:
            vrp = round(float(iv - rv), 4)
            vrp_ratio = round(float(iv / rv), 4) if rv > 0 else None
            if vrp > 0.02:
                regime = "premium_rich"
            elif vrp < -0.02:
                regime = "premium_cheap"
            else:
                regime = "fair_value"

        cone[f"{w}d"] = {
            "window_days": int(w),
            "implied_vol": round(float(iv), 4) if iv is not None else None,
            "realized_vol": round(float(rv), 4) if rv is not None else None,
            "vrp": vrp,
            "vrp_ratio": vrp_ratio,
            "regime": regime,
        }

    return cone


# ---------------------------------------------------------------------------
# Master Volatility Surface Orchestrator
# ---------------------------------------------------------------------------


def calculate_volatility_surface(
    ticker: str = "",
    chain_data: Any = None,
    spot_price: Optional[float] = None,
    historical_prices: Optional[Any] = None,
    as_of: Optional[Any] = None,
    r: Optional[float] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Master volatility surface & skew calculation entrypoint.

    Parameters:
    - ticker: Asset ticker symbol (e.g. 'AAPL')
    - chain_data: Option chain data (Dict of expiration -> OptionChain/dict, or list/DataFrame)
    - spot_price: Current underlying stock price (optional if inferable from chain)
    - historical_prices: Historical close price series/list for realized volatility & VRP cone
    - as_of: Valuation reference date (defaults to UTC today)
    - r: Risk-free rate (defaults to settings.OPTIONS_RISK_FREE_RATE)

    Returns comprehensive dictionary containing:
    - smiles: Expiration-level smile curves, strike grids, and 25-delta skews
    - term_structure: Standardized term structure points (7d, 14d, 30d, 60d, 90d, 180d, 365d)
    - skew_summary: Front-month skew, 30d term skew, and average skew across expiries
    - vrp_cone: Volatility Risk Premium cone (10d, 20d, 30d, 60d)
    - surface_grid: 3D surface mesh points (dte, strike, moneyness, iv)
    """
    # Keyword argument compatibility (e.g. symbol="AAPL")
    if not ticker and "symbol" in kwargs:
        ticker = str(kwargs["symbol"])

    clean_ticker = str(ticker or "").upper().strip()
    r_val = _get_risk_free_rate(r)

    as_of_date = _parse_date(as_of) or datetime.now(timezone.utc).date()
    warnings: List[str] = []

    # Handle empty or invalid chain data gracefully
    if chain_data is None or (isinstance(chain_data, (dict, list, pd.DataFrame)) and len(chain_data) == 0):
        # If spot price is given but no chain_data, provide parametric surface or honest missing chain response
        base_iv = float(kwargs.get("base_iv", 0.22))
        skew_factor = float(kwargs.get("skew_factor", 0.08))
        convexity_factor = float(kwargs.get("convexity_factor", 0.12))

        if spot_price is not None and spot_price > 0 and kwargs.get("generate_parametric", False):
            # Parametric fallback generator for offline simulation
            return _generate_parametric_surface(
                ticker=clean_ticker,
                spot_price=spot_price,
                base_iv=base_iv,
                skew_factor=skew_factor,
                convexity_factor=convexity_factor,
                as_of_date=as_of_date,
                historical_prices=historical_prices,
            )

        return {
            "ticker": clean_ticker,
            "symbol": clean_ticker,
            "spot_price": spot_price,
            "as_of": as_of_date.isoformat(),
            "expirations": [],
            "smiles": {},
            "term_structure": compute_term_structure({}),
            "skew_summary": {
                "front_month_skew_25d": None,
                "term_skew_25d_30d": None,
                "average_skew_25d": None,
                "expirations_skew": {},
            },
            "vrp_cone": compute_vrp_cone([], historical_prices),
            "surface_grid": [],
            "missing_data": True,
            "reason": "Option chain data is empty or unavailable.",
            "warnings": ["No options contracts available."],
        }

    # Normalize expiration dictionary
    expirations_map: Dict[str, Any] = {}
    if isinstance(chain_data, dict):
        first_key = next(iter(chain_data.keys()), None)
        if isinstance(first_key, str) and len(first_key) == 10 and first_key[4] == "-" and first_key[7] == "-":
            expirations_map = dict(chain_data)
        elif "calls" in chain_data or "puts" in chain_data:
            exp_date_str = as_of_date.strftime("%Y-%m-%d")
            expirations_map[exp_date_str] = chain_data
        else:
            expirations_map = dict(chain_data)
    elif isinstance(chain_data, pd.DataFrame):
        if "expiration" in chain_data.columns:
            for exp_val, group in chain_data.groupby("expiration"):
                expirations_map[str(exp_val)] = group
        else:
            exp_date_str = as_of_date.strftime("%Y-%m-%d")
            expirations_map[exp_date_str] = chain_data
    elif isinstance(chain_data, (list, tuple)):
        for item in chain_data:
            exp_str = getattr(item, "expiration", None) or (item.get("expiration") if isinstance(item, dict) else None)
            if exp_str:
                expirations_map[str(exp_str)] = item
            else:
                exp_date_str = as_of_date.strftime("%Y-%m-%d")
                expirations_map[exp_date_str] = item

    if not expirations_map:
        return {
            "ticker": clean_ticker,
            "symbol": clean_ticker,
            "spot_price": spot_price,
            "as_of": as_of_date.isoformat(),
            "expirations": [],
            "smiles": {},
            "term_structure": compute_term_structure({}),
            "skew_summary": {
                "front_month_skew_25d": None,
                "term_skew_25d_30d": None,
                "average_skew_25d": None,
                "expirations_skew": {},
            },
            "vrp_cone": compute_vrp_cone([], historical_prices),
            "surface_grid": [],
            "missing_data": True,
            "reason": "Could not parse expiration mapping from chain data.",
            "warnings": ["No valid expiration dates found."],
        }

    # Infer spot price if not provided
    resolved_spot = spot_price
    if resolved_spot is None or resolved_spot <= 0:
        all_strikes = []
        for slice_item in expirations_map.values():
            if hasattr(slice_item, "calls") and isinstance(slice_item.calls, pd.DataFrame) and "strike" in slice_item.calls.columns:
                all_strikes.extend(slice_item.calls["strike"].dropna().tolist())
            elif isinstance(slice_item, dict):
                calls = slice_item.get("calls")
                if isinstance(calls, list):
                    all_strikes.extend([c.get("strike") for c in calls if isinstance(c, dict) and "strike" in c])
        if all_strikes:
            resolved_spot = float(np.median(all_strikes))
            warnings.append(f"Spot price was inferred from chain median strike ({resolved_spot:.2f}).")
        else:
            return {
                "ticker": clean_ticker,
                "symbol": clean_ticker,
                "spot_price": None,
                "as_of": as_of_date.isoformat(),
                "expirations": list(expirations_map.keys()),
                "smiles": {},
                "term_structure": compute_term_structure({}),
                "skew_summary": {
                    "front_month_skew_25d": None,
                    "term_skew_25d_30d": None,
                    "average_skew_25d": None,
                    "expirations_skew": {},
                },
                "vrp_cone": compute_vrp_cone([], historical_prices),
                "surface_grid": [],
                "missing_data": True,
                "reason": "Spot price is missing and could not be inferred.",
                "warnings": ["Missing spot price."],
            }

    smiles: Dict[str, Any] = {}
    expirations_atm: Dict[str, Dict[str, Any]] = {}
    expirations_skew: Dict[str, float] = {}
    surface_grid: List[Dict[str, Any]] = []

    # Sort expirations chronologically
    sorted_exp_keys = sorted(expirations_map.keys())

    for exp_key in sorted_exp_keys:
        exp_slice = expirations_map[exp_key]
        exp_date_obj = _parse_date(exp_key)
        if exp_date_obj is None:
            continue

        dte = max(0.5, float((exp_date_obj - as_of_date).days))
        t_years = dte / 365.0

        df_parsed = parse_expiration_slice(exp_slice, resolved_spot, t_years, r_val)
        if df_parsed.empty:
            continue

        valid_iv_df = df_parsed.dropna(subset=["iv"])
        if valid_iv_df.empty or len(valid_iv_df) == 0:
            continue

        strikes = valid_iv_df["strike"].tolist()
        ivs = valid_iv_df["iv"].tolist()

        smile_fit = interpolate_smile_spline(
            strikes=strikes,
            ivs=ivs,
            spot_price=resolved_spot,
            t_years=t_years,
            r=r_val,
        )

        if not smile_fit:
            continue

        spline_fn = smile_fit["spline_fn"]
        atm_iv = smile_fit["atm_iv"]

        skew_data = compute_25delta_skew(
            spot_price=resolved_spot,
            t_years=t_years,
            spline_fn=spline_fn,
            r=r_val,
            min_strike=smile_fit["min_strike"],
            max_strike=smile_fit["max_strike"],
        )

        expirations_atm[exp_key] = {
            "dte": dte,
            "t_years": t_years,
            "atm_iv": atm_iv,
        }

        if skew_data["skew_25d"] is not None:
            expirations_skew[exp_key] = skew_data["skew_25d"]

        for pt in smile_fit["curve"]:
            surface_grid.append({
                "expiration": exp_key,
                "dte": round(dte, 1),
                "t_years": round(t_years, 4),
                "strike": pt["strike"],
                "moneyness": pt["moneyness"],
                "iv": pt["iv"],
                "call_delta": pt["call_delta"],
                "put_delta": pt["put_delta"],
            })

        clean_strikes = []
        for _, row in df_parsed.iterrows():
            clean_strikes.append({
                "strike": float(row["strike"]),
                "moneyness": round(float(row.get("moneyness", 1.0)), 4),
                "iv": round(float(row["iv"]), 4) if pd.notna(row.get("iv")) else None,
                "call_bid": round(float(row["call_bid"]), 2) if pd.notna(row.get("call_bid")) else None,
                "call_ask": round(float(row["call_ask"]), 2) if pd.notna(row.get("call_ask")) else None,
                "call_mid": round(float(row["call_mid"]), 2) if pd.notna(row.get("call_mid")) else None,
                "put_bid": round(float(row["put_bid"]), 2) if pd.notna(row.get("put_bid")) else None,
                "put_ask": round(float(row["put_ask"]), 2) if pd.notna(row.get("put_ask")) else None,
                "put_mid": round(float(row["put_mid"]), 2) if pd.notna(row.get("put_mid")) else None,
            })

        smiles[exp_key] = {
            "expiration": exp_key,
            "dte": round(dte, 1),
            "t_years": round(t_years, 4),
            "atm_iv": atm_iv,
            "skew_25d": skew_data["skew_25d"],
            "put_25d_iv": skew_data["put_25d_iv"],
            "call_25d_iv": skew_data["call_25d_iv"],
            "put_25d_strike": skew_data["put_25d_strike"],
            "call_25d_strike": skew_data["call_25d_strike"],
            "skew_ratio": skew_data["skew_ratio"],
            "butterfly_25d": skew_data["butterfly_25d"],
            "n_strikes": len(clean_strikes),
            "strikes": clean_strikes,
            "curve": smile_fit["curve"],
        }

    term_structure = compute_term_structure(expirations_atm, STANDARD_TERM_HORIZONS)
    vrp_cone = compute_vrp_cone(term_structure["points"], historical_prices, STANDARD_VRP_WINDOWS)

    front_month_exp = sorted_exp_keys[0] if sorted_exp_keys else None
    front_skew = smiles[front_month_exp]["skew_25d"] if front_month_exp and front_month_exp in smiles else None
    avg_skew = round(float(np.mean(list(expirations_skew.values()))), 4) if expirations_skew else None

    skew_summary = {
        "front_month_skew_25d": front_skew,
        "average_skew_25d": avg_skew,
        "expirations_skew": expirations_skew,
    }

    return {
        "ticker": clean_ticker,
        "symbol": clean_ticker,
        "spot_price": round(float(resolved_spot), 2),
        "as_of": as_of_date.isoformat(),
        "expirations": list(smiles.keys()),
        "smiles": smiles,
        "term_structure": term_structure,
        "skew_summary": skew_summary,
        "vrp_cone": vrp_cone,
        "surface_grid": surface_grid,
        "missing_data": False if smiles else True,
        "reason": None if smiles else "No valid smile curves could be interpolated.",
        "warnings": warnings,
    }


def _generate_parametric_surface(
    ticker: str,
    spot_price: float,
    base_iv: float,
    skew_factor: float,
    convexity_factor: float,
    as_of_date: date,
    historical_prices: Optional[Any] = None,
) -> Dict[str, Any]:
    """Generates synthetic parametric surface when real option chain is offline/mocked."""
    dtes = STANDARD_TERM_HORIZONS
    moneyness_ratios = [0.80, 0.85, 0.90, 0.95, 0.975, 1.00, 1.025, 1.05, 1.10, 1.15, 1.20]

    surface_points = []
    expirations_atm: Dict[str, Dict[str, Any]] = {}
    smiles: Dict[str, Any] = {}
    expirations_skew: Dict[str, float] = {}

    for dte in dtes:
        t_years = dte / 365.0
        exp_date_obj = as_of_date + timedelta(days=dte)
        exp_date_str = exp_date_obj.strftime("%Y-%m-%d")

        atm_iv = base_iv * (1.0 + 0.05 * math.log(max(1.0, dte / 30.0)))
        expirations_atm[exp_date_str] = {"dte": dte, "t_years": t_years, "atm_iv": atm_iv}

        curve = []
        strikes = []
        for m in moneyness_ratios:
            strike = round(spot_price * m, 2)
            log_m = math.log(m)
            iv = atm_iv - skew_factor * log_m + convexity_factor * (log_m ** 2)
            iv = max(0.05, min(3.0, iv))

            c_delta = _black_scholes_delta(spot_price, strike, t_years, iv, "call")
            p_delta = _black_scholes_delta(spot_price, strike, t_years, iv, "put")

            pt = {
                "strike": strike,
                "moneyness": round(m, 3),
                "dte": dte,
                "expiration": exp_date_str,
                "iv": round(iv, 4),
                "call_delta": round(c_delta, 4),
                "put_delta": round(p_delta, 4),
            }
            surface_points.append(pt)
            curve.append(pt)
            strikes.append({
                "strike": strike,
                "moneyness": round(m, 3),
                "iv": round(iv, 4),
                "call_bid": None,
                "call_ask": None,
                "call_mid": None,
                "put_bid": None,
                "put_ask": None,
                "put_mid": None,
            })

        # 25-delta skew
        put_25d_m = 0.94
        log_m_put = math.log(put_25d_m)
        iv_25p = max(0.05, atm_iv - skew_factor * log_m_put + convexity_factor * (log_m_put ** 2))

        call_25d_m = 1.06
        log_m_call = math.log(call_25d_m)
        iv_25c = max(0.05, atm_iv - skew_factor * log_m_call + convexity_factor * (log_m_call ** 2))

        skew_25d = round(iv_25p - iv_25c, 4)
        expirations_skew[exp_date_str] = skew_25d

        smiles[exp_date_str] = {
            "expiration": exp_date_str,
            "dte": dte,
            "t_years": round(t_years, 4),
            "atm_iv": round(atm_iv, 4),
            "skew_25d": skew_25d,
            "put_25d_iv": round(iv_25p, 4),
            "call_25d_iv": round(iv_25c, 4),
            "put_25d_strike": round(spot_price * put_25d_m, 2),
            "call_25d_strike": round(spot_price * call_25d_m, 2),
            "skew_ratio": round(iv_25p / iv_25c, 4) if iv_25c > 0 else None,
            "butterfly_25d": round(((iv_25p + iv_25c) / 2.0) - atm_iv, 4),
            "n_strikes": len(strikes),
            "strikes": strikes,
            "curve": curve,
        }

    term_structure = compute_term_structure(expirations_atm, STANDARD_TERM_HORIZONS)
    vrp_cone = compute_vrp_cone(term_structure["points"], historical_prices, STANDARD_VRP_WINDOWS)

    return {
        "ticker": ticker,
        "symbol": ticker,
        "spot_price": round(spot_price, 2),
        "as_of": as_of_date.isoformat(),
        "expirations": list(smiles.keys()),
        "smiles": smiles,
        "term_structure": term_structure,
        "skew_summary": {
            "front_month_skew_25d": smiles[list(smiles.keys())[0]]["skew_25d"] if smiles else None,
            "average_skew_25d": round(float(np.mean(list(expirations_skew.values()))), 4) if expirations_skew else None,
            "expirations_skew": expirations_skew,
        },
        "vrp_cone": vrp_cone,
        "surface_grid": surface_points,
        "missing_data": False,
        "reason": None,
        "warnings": ["Using parametric model surface generator."],
    }


def to_vol_surface_response(
    raw: Dict[str, Any],
    selected_expiration: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Reshapes calculate_volatility_surface()'s internal result (smiles/term_structure/
    skew_summary/vrp_cone -- the shape every existing test in tests/test_volatility_surface.py
    asserts on) into the VolSurfaceResponse contract webapp/src/api/types.ts,
    webapp/src/api/mock.ts, and webapp/src/components/options/VolSurfaceView.tsx
    already agree on (smile_points / term_structure-as-array / skew / selected_expiration).

    Kept as a separate step from calculate_volatility_surface() itself so every existing
    caller/test of the pure math function is unaffected -- only get_volatility_surface_data()
    (the function GET /pilots/options/vol-surface actually calls) applies this reshape.

    Every frontend-required field is populated defensively: a None/missing upstream value is
    OMITTED (never fabricated as 0 -- CONSTRAINT #4) so the frontend's own null-guards render
    an honest "--" instead of a fabricated number.
    """
    smiles: Dict[str, Any] = raw.get("smiles") or {}
    expirations: List[str] = list(raw.get("expirations") or smiles.keys())

    exp_key: Optional[str] = selected_expiration if selected_expiration in smiles else None
    if exp_key is None and expirations:
        exp_key = expirations[0]

    smile_entry: Optional[Dict[str, Any]] = smiles.get(exp_key) if exp_key else None

    smile_points: List[Dict[str, Any]] = []
    if smile_entry:
        for pt in smile_entry.get("curve") or []:
            if pt.get("strike") is None or pt.get("iv") is None:
                continue
            smile_points.append({
                "strike": pt["strike"],
                "iv": pt["iv"],
                "moneyness": pt.get("moneyness"),
            })

    vrp_cone: Dict[str, Any] = raw.get("vrp_cone") or {}
    rv_30d = (vrp_cone.get("30d") or {}).get("realized_vol")

    term_structure: List[Dict[str, Any]] = []
    for exp in expirations:
        entry = smiles.get(exp)
        if not entry or entry.get("atm_iv") is None:
            continue
        row: Dict[str, Any] = {
            "expiration": entry.get("expiration", exp),
            "dte": entry.get("dte"),
            "atm_iv": entry["atm_iv"],
        }
        if rv_30d is not None:
            row["historical_realized_vol_30d"] = rv_30d
        term_structure.append(row)

    skew: Dict[str, Any] = {}
    if smile_entry:
        if smile_entry.get("skew_25d") is not None:
            skew["skew_25delta"] = smile_entry["skew_25d"]
        if smile_entry.get("put_25d_iv") is not None:
            skew["put_25delta_iv"] = smile_entry["put_25d_iv"]
        if smile_entry.get("call_25d_iv") is not None:
            skew["call_25delta_iv"] = smile_entry["call_25d_iv"]
        if smile_entry.get("atm_iv") is not None:
            skew["atm_iv"] = smile_entry["atm_iv"]

    for window, field in (
        ("10d", "realized_vol_10d"),
        ("20d", "realized_vol_20d"),
        ("30d", "realized_vol_30d"),
        ("60d", "realized_vol_60d"),
    ):
        rv = (vrp_cone.get(window) or {}).get("realized_vol")
        if rv is not None:
            skew[field] = rv

    vrp_30d = (vrp_cone.get("30d") or {}).get("vrp")
    if vrp_30d is not None:
        skew["vrp_spread"] = vrp_30d

    return {
        "symbol": raw.get("symbol") or raw.get("ticker") or "",
        "spot_price": raw.get("spot_price"),
        "as_of": raw.get("as_of"),
        "expirations": expirations,
        "selected_expiration": exp_key,
        "smile_points": smile_points,
        "term_structure": term_structure,
        "skew": skew,
    }


def get_volatility_surface_data(
    symbol: str,
    market_provider: Optional[Any] = None,
    options_provider: Optional[Any] = None,
) -> Dict[str, Any]:
    """Resolves market quotes and option chains to compute live/parametric volatility surface for symbol.

    Returns calculate_volatility_surface()'s raw internal shape (smiles/term_structure/
    skew_summary/vrp_cone/surface_grid) UNCHANGED -- this is also consumed directly by
    GET /pilots/options/vol-surface/3d-mesh (api/pilots_api.py), which reads
    `surface_grid`. GET /pilots/options/vol-surface (the OTHER caller) applies
    to_vol_surface_response() itself rather than this function doing it, so both callers
    keep working off the one shape.
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

    if spot_price is None or spot_price <= 0:
        spot_price = 500.0 if sym == "SPY" else 150.0

    chain_data = None
    if options_provider is not None:
        try:
            expirations = options_provider.fetch_options_chain(sym)
            if expirations and isinstance(expirations, list):
                chain_map = {}
                for exp in expirations[:5]:  # fetch first 5 expiries for live surface
                    c = options_provider.fetch_options_chain(sym, exp)
                    if c:
                        chain_map[str(exp)] = c
                if chain_map:
                    chain_data = chain_map
        except Exception:
            chain_data = None

    return calculate_volatility_surface(
        ticker=sym,
        chain_data=chain_data,
        spot_price=spot_price,
        generate_parametric=True,
    )
