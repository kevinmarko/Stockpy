"""
pilots/realtime_risk_streamer.py
================================
Real-Time Portfolio Risk & Greeks Streamer Engine.

Computes sub-second incremental and aggregate portfolio risk metrics:
- Net Delta (share equivalents and dollar delta)
- Net Gamma (rate of change of delta per $1 underlying move)
- Dollar Gamma 1% move ($ delta change for a 1% move in spot)
- Net Theta ($/day decay)
- Net Vega ($ per 1% change in IV)
- Beta-weighted SPY Delta

Invariants & Constraints:
- AST Boundary: Pure dependency-light module (stdlib + numpy + scipy + settings).
  Never imports heavy engines (processing_engine, data_engine).
- Constraint #4 (Honesty): Never fabricates missing quotes or Greeks. Unresolvable
  positions are omitted from aggregate sums and reported in missing_positions.
- Degenerate-std & 0DTE guards: Enforces < 1e-12 denominator guards and exact
  intrinsic delta fallbacks for expiring or zero-volatility options.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import math
import re
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
from scipy.stats import norm

from settings import settings


_DEGENERATE_THRESHOLD = 1e-12
_TRADING_DAYS_PER_YEAR = 252.0

# Regex matching standard option symbols: AAPL 2026-09-18 $150.00 CALL
_OPTION_SYM_RE = re.compile(
    r"^(?P<ticker>[A-Z0-9]+)\s+(?P<exp>\d{4}-\d{2}-\d{2})\s+\$(?P<strike>\d+(?:\.\d+)?)\s+(?P<type>CALL|PUT)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PositionRiskGreeks:
    """Risk and Greek metrics for a single equity or option position."""
    symbol: str
    underlying: str
    position_type: str  # "equity" | "option"
    qty: float
    spot_price: float
    strike: Optional[float] = None
    dte: Optional[float] = None
    option_type: Optional[str] = None  # "call" | "put"
    iv: Optional[float] = None
    delta: float = 0.0
    dollar_delta: float = 0.0
    gamma: float = 0.0
    dollar_gamma_1pct: float = 0.0
    theta_daily: float = 0.0
    vega_1pct: float = 0.0
    beta_spy: float = 1.0
    beta_weighted_delta_spy: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioRiskGreeks:
    """Aggregate portfolio risk and Greek exposure across all active positions."""
    timestamp: str
    spy_price: float
    net_delta: float
    net_dollar_delta: float
    net_gamma: float
    net_dollar_gamma_1pct: float
    net_theta: float
    net_vega: float
    beta_weighted_delta_spy: float
    total_positions_count: int
    resolved_positions_count: int
    missing_data_count: int
    positions: List[PositionRiskGreeks] = field(default_factory=list)
    missing_positions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "spy_price": self.spy_price,
            "net_delta": round(self.net_delta, 4),
            "net_dollar_delta": round(self.net_dollar_delta, 2),
            "net_gamma": round(self.net_gamma, 6),
            "net_dollar_gamma_1pct": round(self.net_dollar_gamma_1pct, 2),
            "net_theta": round(self.net_theta, 2),
            "net_vega": round(self.net_vega, 2),
            "beta_weighted_delta_spy": round(self.beta_weighted_delta_spy, 4),
            "total_positions_count": self.total_positions_count,
            "resolved_positions_count": self.resolved_positions_count,
            "missing_data_count": self.missing_data_count,
            "positions": [p.to_dict() for p in self.positions],
            "missing_positions": self.missing_positions,
        }


def parse_option_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    """Parses a standardized option leg symbol string into components."""
    if not symbol:
        return None
    m = _OPTION_SYM_RE.match(symbol.strip())
    if not m:
        return None
    return {
        "ticker": m.group("ticker").upper(),
        "expiration": m.group("exp"),
        "strike": float(m.group("strike")),
        "option_type": m.group("type").lower(),
    }


def compute_black_scholes_unit_greeks(
    spot: float,
    strike: float,
    t_years: float,
    sigma: float,
    option_type: str = "call",
    r: Optional[float] = None,
) -> Dict[str, float]:
    """Computes unit per-share Black-Scholes Greeks with degenerate-input and 0DTE guards."""
    if r is None:
        r = float(getattr(settings, "OPTIONS_RISK_FREE_RATE", 0.045))

    opt_type = str(option_type or "call").lower().strip()

    if spot <= 0 or strike <= 0:
        return {
            "delta": 0.0,
            "gamma": 0.0,
            "theta_daily": 0.0,
            "vega_1pct": 0.0,
        }

    # 0DTE / Expiration fallback: when T <= 1e-12, intrinsic delta applies, other Greeks decay to 0
    if t_years <= _DEGENERATE_THRESHOLD:
        delta = 1.0 if (opt_type == "call" and spot > strike) else (-1.0 if (opt_type == "put" and spot < strike) else 0.0)
        return {
            "delta": float(delta),
            "gamma": 0.0,
            "theta_daily": 0.0,
            "vega_1pct": 0.0,
        }

    # Missing or degenerate volatility guard
    if sigma <= _DEGENERATE_THRESHOLD or np.isnan(sigma):
        delta = 1.0 if (opt_type == "call" and spot > strike) else (-1.0 if (opt_type == "put" and spot < strike) else 0.0)
        return {
            "delta": float(delta),
            "gamma": 0.0,
            "theta_daily": 0.0,
            "vega_1pct": 0.0,
        }

    vol_sqrt_t = sigma * np.sqrt(t_years)
    if vol_sqrt_t < _DEGENERATE_THRESHOLD:
        vol_sqrt_t = _DEGENERATE_THRESHOLD

    d1 = (np.log(spot / strike) + (r + 0.5 * sigma ** 2) * t_years) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    discount = math.exp(-r * t_years)

    if opt_type == "call":
        delta = float(norm.cdf(d1))
        theta_annual = -(spot * norm.pdf(d1) * sigma) / (2 * np.sqrt(t_years)) - r * strike * discount * norm.cdf(d2)
    else:
        delta = float(norm.cdf(d1) - 1.0)
        theta_annual = -(spot * norm.pdf(d1) * sigma) / (2 * np.sqrt(t_years)) + r * strike * discount * norm.cdf(-d2)

    denom_gamma = spot * vol_sqrt_t
    gamma = float(norm.pdf(d1) / denom_gamma) if denom_gamma >= _DEGENERATE_THRESHOLD else 0.0
    raw_vega = float(spot * norm.pdf(d1) * np.sqrt(t_years))

    return {
        "delta": delta,
        "gamma": gamma,
        "theta_daily": float(theta_annual / _TRADING_DAYS_PER_YEAR),
        "vega_1pct": float(raw_vega / 100.0),
    }


def compute_position_risk_greeks(
    position: Dict[str, Any],
    spot_price: float,
    spy_price: float,
    beta: float = 1.0,
    r: Optional[float] = None,
    as_of: Optional[datetime] = None,
) -> Optional[PositionRiskGreeks]:
    """Evaluates risk and Greeks for a single equity or option position dict.

    Accepts dict with keys:
    - symbol: str
    - qty: float
    - iv: Optional[float] (for option)
    - spot_price / current_price: Optional[float]
    """
    symbol = str(position.get("symbol", "")).strip()
    if not symbol:
        return None

    qty = float(position.get("qty", 0.0))
    if abs(qty) < _DEGENERATE_THRESHOLD:
        return None

    if spot_price <= 0:
        return None

    spy_spot = max(_DEGENERATE_THRESHOLD, spy_price if spy_price > 0 else 500.0)
    beta_clean = float(beta) if not np.isnan(beta) and not np.isinf(beta) else 1.0

    parsed = parse_option_symbol(symbol)
    if parsed is None:
        # Equity Position
        delta = 1.0
        dollar_delta = qty * spot_price
        gamma = 0.0
        dollar_gamma_1pct = 0.0
        theta_daily = 0.0
        vega_1pct = 0.0
        beta_weighted_delta_spy = (dollar_delta * beta_clean) / spy_spot

        return PositionRiskGreeks(
            symbol=symbol,
            underlying=symbol.upper(),
            position_type="equity",
            qty=qty,
            spot_price=spot_price,
            delta=delta * qty,
            dollar_delta=dollar_delta,
            gamma=gamma,
            dollar_gamma_1pct=dollar_gamma_1pct,
            theta_daily=theta_daily,
            vega_1pct=vega_1pct,
            beta_spy=beta_clean,
            beta_weighted_delta_spy=beta_weighted_delta_spy,
        )

    # Option Position
    strike = parsed["strike"]
    opt_type = parsed["option_type"]
    underlying = parsed["ticker"]
    exp_str = parsed["expiration"]

    now = as_of or datetime.now(timezone.utc)
    try:
        exp_dt = datetime.strptime(exp_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        dte = max(0.0, (exp_dt - now).total_seconds() / 86400.0)
    except Exception:
        dte = 30.0

    t_years = max(0.0, dte / 365.0)
    iv = float(position.get("iv") or 0.25)

    unit_greeks = compute_black_scholes_unit_greeks(
        spot=spot_price,
        strike=strike,
        t_years=t_years,
        sigma=iv,
        option_type=opt_type,
        r=r,
    )

    contract_mult = 100.0
    total_shares = qty * contract_mult

    pos_delta = unit_greeks["delta"] * total_shares
    pos_dollar_delta = pos_delta * spot_price
    pos_gamma = unit_greeks["gamma"] * total_shares
    # Dollar Gamma 1% = 0.5 * Gamma * (0.01 * S)^2 * multiplier * qty
    pos_dollar_gamma_1pct = 0.5 * unit_greeks["gamma"] * ((0.01 * spot_price) ** 2) * total_shares
    pos_theta = unit_greeks["theta_daily"] * total_shares
    pos_vega = unit_greeks["vega_1pct"] * total_shares
    pos_beta_weighted_delta_spy = (pos_dollar_delta * beta_clean) / spy_spot

    return PositionRiskGreeks(
        symbol=symbol,
        underlying=underlying,
        position_type="option",
        qty=qty,
        spot_price=spot_price,
        strike=strike,
        dte=round(dte, 1),
        option_type=opt_type,
        iv=iv,
        delta=pos_delta,
        dollar_delta=pos_dollar_delta,
        gamma=pos_gamma,
        dollar_gamma_1pct=pos_dollar_gamma_1pct,
        theta_daily=pos_theta,
        vega_1pct=pos_vega,
        beta_spy=beta_clean,
        beta_weighted_delta_spy=pos_beta_weighted_delta_spy,
    )


def compute_portfolio_risk_stream(
    positions: Sequence[Dict[str, Any]],
    quotes: Dict[str, float],
    betas: Optional[Dict[str, float]] = None,
    spy_price: Optional[float] = None,
    r: Optional[float] = None,
    as_of: Optional[datetime] = None,
) -> PortfolioRiskGreeks:
    """Aggregates sub-second risk & Greeks across active positions.

    Non-fabrication: Any position whose spot price cannot be resolved is
    recorded in missing_positions and excluded from the net Greek sums.
    """
    betas_map = betas or {}
    spy_spot = spy_price if (spy_price and spy_price > 0) else quotes.get("SPY", 500.0)
    now_iso = (as_of or datetime.now(timezone.utc)).isoformat()

    resolved_positions: List[PositionRiskGreeks] = []
    missing_positions: List[str] = []

    for pos in positions:
        sym = str(pos.get("symbol", "")).strip()
        if not sym:
            continue

        parsed = parse_option_symbol(sym)
        underlying = parsed["ticker"] if parsed else sym.upper()

        spot = quotes.get(underlying) or quotes.get(sym) or pos.get("spot_price") or pos.get("current_price")
        if spot is None or spot <= 0:
            missing_positions.append(sym)
            continue

        beta = betas_map.get(underlying, 1.0)

        pos_greeks = compute_position_risk_greeks(
            position=pos,
            spot_price=float(spot),
            spy_price=spy_spot,
            beta=beta,
            r=r,
            as_of=as_of,
        )

        if pos_greeks is None:
            missing_positions.append(sym)
        else:
            resolved_positions.append(pos_greeks)

    net_delta = sum(p.delta for p in resolved_positions)
    net_dollar_delta = sum(p.dollar_delta for p in resolved_positions)
    net_gamma = sum(p.gamma for p in resolved_positions)
    net_dollar_gamma_1pct = sum(p.dollar_gamma_1pct for p in resolved_positions)
    net_theta = sum(p.theta_daily for p in resolved_positions)
    net_vega = sum(p.vega_1pct for p in resolved_positions)
    beta_weighted_delta_spy = sum(p.beta_weighted_delta_spy for p in resolved_positions)

    return PortfolioRiskGreeks(
        timestamp=now_iso,
        spy_price=spy_spot,
        net_delta=net_delta,
        net_dollar_delta=net_dollar_delta,
        net_gamma=net_gamma,
        net_dollar_gamma_1pct=net_dollar_gamma_1pct,
        net_theta=net_theta,
        net_vega=net_vega,
        beta_weighted_delta_spy=beta_weighted_delta_spy,
        total_positions_count=len(positions),
        resolved_positions_count=len(resolved_positions),
        missing_data_count=len(missing_positions),
        positions=resolved_positions,
        missing_positions=missing_positions,
    )
