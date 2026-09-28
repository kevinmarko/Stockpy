"""Options Analytics & 0DTE Intraday Decay Engine (Phase 5).

No real options-chain / open-interest feed exists anywhere in this codebase
to compute genuine dealer gamma exposure from (technical_options_engine.py,
this codebase's only options pricing surface, is a Black-Scholes theoretical
pricer with no live OI data behind it — see CLAUDE.md's FMP integration
notes on Starter-tier data limitations: real options-chain data needs a
higher FMP tier than what's configured). Per CONSTRAINT #4 (never fabricate
data as if it were real), `get_options_analytics_summary()` reports Net
Dealer Premium / regime / the intraday series as UNAVAILABLE (`None`/`[]`),
not a plausible-looking number — a demo value dressed up with a "synthetic"
label still reads as a real, precise measurement to anyone who doesn't
notice the label. `_demo_net_dealer_premium`/`_demo_0dte_theta_decay` are
kept as private, ticker-hash-deterministic helpers (useful scaffolding for
whenever a real chain/OI source is actually wired in, and directly
unit-tested), but they are NOT called from the public summary.
"""

from typing import Dict, Any, List, Optional
import numpy as np


def _demo_net_dealer_premium(symbol: str) -> float:
    """Deterministic per-symbol Net Dealer Premium in $M — demo scaffolding
    only, NOT real dealer/GEX data (see module docstring). Never called from
    get_options_analytics_summary."""
    # Deterministic calculation based on symbol hash for stable diagnostic output
    h = sum(ord(c) for c in symbol)
    raw = ((h % 200) - 100) / 2.0
    return float(raw)


def _demo_0dte_theta_decay() -> List[Dict[str, Any]]:
    """13 hourly points for a demo intraday theta decay & gamma curve —
    demo scaffolding only, NOT a live measurement (see module docstring).
    Never called from get_options_analytics_summary."""
    results = []
    for i in range(13):
        hour = 9 + i
        time_str = f"{hour - 12 if hour > 12 else hour}:00 {'PM' if hour >= 12 else 'AM'}"
        theta = float((i / 12.0) ** 3 * 100.0)
        gamma = float(np.exp((i - 6.0) / 3.0) * 10.0)
        results.append({
            "time": time_str,
            "hour": hour,
            "theta": round(theta, 2),
            "gamma": round(gamma, 2),
        })
    return results


def get_options_analytics_summary(symbol: str) -> Dict[str, Any]:
    """Return the 0DTE & Net Dealer Gamma analytics dict.

    `is_synthetic=True` always, and every analytics field is `None`/`[]` —
    no real options-chain OI feed is wired in this codebase (see module
    docstring), so this honestly reports "unavailable" rather than a
    plausible-looking placeholder number.
    """
    return {
        "symbol": symbol.upper(),
        "net_dealer_premium": None,
        "regime": None,
        "intraday_series": [],
        "is_synthetic": True,
    }
