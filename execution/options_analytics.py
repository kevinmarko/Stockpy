"""Options Analytics & 0DTE Intraday Decay Engine (Phase 5).

SYNTHETIC DEMO DATA ONLY. This module has no real options-chain / open-interest
feed to compute genuine dealer gamma exposure from (this codebase's only options
pricing surface, technical_options_engine.py, is a Black-Scholes theoretical
pricer with no live OI data behind it — see CLAUDE.md's FMP integration notes
on Starter-tier data limitations). Every value here is either a deterministic
hash of the ticker string or a fixed intraday curve shape, NOT a live
computation — never present these numbers to an operator as real Net Dealer
Premium / GEX. `get_options_analytics_summary()`'s `is_synthetic` field exists
so callers can render this honestly (e.g. a "Demo Data" badge) instead of
implying it's live (CONSTRAINT #4 — never fabricate data as if it were real).
"""

from typing import Dict, Any, List
import numpy as np


def compute_net_dealer_premium(symbol: str) -> float:
    """Synthetic Net Dealer Premium in $M, deterministic per-symbol (NOT real dealer/GEX data — see module docstring)."""
    # Deterministic calculation based on symbol hash for stable diagnostic output
    h = sum(ord(c) for c in symbol)
    raw = ((h % 200) - 100) / 2.0
    return float(raw)


def compute_0dte_theta_decay() -> List[Dict[str, Any]]:
    """Generate 13 hourly points for intraday theta decay & gamma spikes."""
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
    """Return aggregated 0DTE & Net Dealer Gamma analytics dict.

    `is_synthetic=True` always — see module docstring. No real options-chain
    OI feed is wired in this codebase, so this is demo/placeholder data, not
    a live measurement.
    """
    net_premium = compute_net_dealer_premium(symbol)
    regime = "Negative Gamma (Volatile)" if net_premium < 0 else "Positive Gamma (Dampened)"
    return {
        "symbol": symbol.upper(),
        "net_dealer_premium": net_premium,
        "regime": regime,
        "intraday_series": compute_0dte_theta_decay(),
        "is_synthetic": True,
    }
