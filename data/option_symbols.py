"""Option-symbol parsing and Black-Scholes pricing for paper option marking.

The paper store (``data/paper_account_store.py``) still has to value any
legacy option position honestly, and ``execution/fmp_paper_broker.py`` still
has to parse a multi-leg order's option symbols. Both used to borrow these two
helpers from ``pilots/options_risk.py``, which belongs to the retired options
desk and is being archived to ``legacy/`` (2026-09, step 4). They are copied
here unchanged -- same regex, same guards, same math -- so equity paper
trading has no options-desk dependency. ``tests/test_option_symbols.py``
proves the copies match the originals on a grid of inputs, including 0DTE
and zero volatility.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Optional

# Regex matching option symbol format: AAPL 2026-09-18 $150.00 CALL
_OPTION_SYM_RE = re.compile(
    r"^(?P<ticker>[A-Z0-9]+)\s+(?P<exp>\d{4}-\d{2}-\d{2})\s+\$(?P<strike>\d+(?:\.\d+)?)\s+(?P<type>CALL|PUT)$",
    re.IGNORECASE,
)

_DEGENERATE_THRESHOLD = 1e-12


def parse_option_symbol(symbol: str) -> Optional[Dict[str, Any]]:
    """Parses a standardized option leg symbol string into components.

    Returns ``{"ticker", "expiration", "strike", "option_type"}`` or ``None``
    when ``symbol`` is not an option leg symbol.
    """
    m = _OPTION_SYM_RE.match(symbol.strip())
    if not m:
        return None
    return {
        "ticker": m.group("ticker").upper(),
        "expiration": m.group("exp"),
        "strike": float(m.group("strike")),
        "option_type": m.group("type").lower(),
    }


def black_scholes_price(
    spot: float,
    strike: float,
    t_years: float,
    sigma: float,
    option_type: str = "call",
    r: Optional[float] = None,
) -> float:
    """Theoretical per-share Black-Scholes price of one option contract.

    Identical to the ``"price"`` field of the retired
    ``pilots.options_risk.calculate_black_scholes_greeks``:

    - non-positive spot or strike -> ``0.0``
    - ``t_years <= 1e-12`` (0DTE / expired) -> intrinsic value
    - ``sigma <= 1e-12`` or NaN -> intrinsic value
    - otherwise the Black-Scholes price, floored at ``0.0``

    ``r`` defaults to ``settings.OPTIONS_RISK_FREE_RATE``.
    """
    import numpy as np
    from scipy.stats import norm

    if r is None:
        from settings import settings

        r = float(getattr(settings, "OPTIONS_RISK_FREE_RATE", 0.045))

    opt_type = str(option_type or "call").lower().strip()

    if spot <= 0 or strike <= 0:
        return 0.0

    intrinsic = max(0.0, spot - strike) if opt_type == "call" else max(0.0, strike - spot)

    if t_years <= _DEGENERATE_THRESHOLD:
        return float(intrinsic)

    if sigma <= _DEGENERATE_THRESHOLD or np.isnan(sigma):
        return float(intrinsic)

    vol_sqrt_t = sigma * np.sqrt(t_years)
    if vol_sqrt_t < _DEGENERATE_THRESHOLD:
        vol_sqrt_t = _DEGENERATE_THRESHOLD

    d1 = (np.log(spot / strike) + (r + 0.5 * sigma ** 2) * t_years) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    discount = math.exp(-r * t_years)

    if opt_type == "call":
        price = spot * norm.cdf(d1) - strike * discount * norm.cdf(d2)
    else:
        price = strike * discount * norm.cdf(-d2) - spot * norm.cdf(-d1)

    return float(max(0.0, price))
