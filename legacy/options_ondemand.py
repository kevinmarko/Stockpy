"""options_ondemand.py — on-demand (operator-triggered, synchronous) options
premium-directive compute for the Pilots PWA's "recompute with custom
parameters" action on ``OptionsMatrix.tsx`` (webapp porting backlog item 8b).

Distinct from ``reporting/options_snapshot.py`` (pipeline-side writer, gated
behind ``settings.OPTIONS_MATRIX_ENABLED``, always operates over the
operator's configured universe, persists ``output/options_matrix.json`` for
the read-only ``GET /options`` endpoint). This module is invoked directly from
an HTTP POST handler (``api/data_api.py``) over a capped, REQUEST-scoped
symbol list with operator-adjustable delta-scale/IVR/risk-free-rate/strike-
grid/DTE controls; it never reads or writes ``output/options_matrix.json``
and is intentionally NOT gated by ``OPTIONS_MATRIX_ENABLED``.

Thin wrapper around ``technical_options_engine.build_premium_directive`` —
mirrors ``gui/panels/options_matrix.py::_compute_directive_row`` minus the
``st.cache_data`` decorator (this is a one-shot stateless HTTP handler, not a
Streamlit rerun loop, so there is nothing to key a cache on across calls).

Honesty (CONSTRAINT #4) / dead-letter resilience (CONSTRAINT #6): identical
contract to the GUI panel this ports — a bad symbol degrades to an
error-shaped placeholder row, never aborts the batch, never raises.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "MACRO_DEFAULT_VIX",
    "MACRO_DEFAULT_REGIME",
    "RECOMPUTE_MIN_SYMBOLS",
    "RECOMPUTE_MAX_SYMBOLS",
    "macro_from_snapshot",
    "compute_directive_row",
]

# Mirrors gui/panels/options_matrix.py::_macro_from_snap's inline defaults —
# the VRP regime gate in build_premium_directive only fires on POSITIVE
# evidence of stress (VIX >= 30 or a CREDIT EVENT regime), so a neutral
# default here reproduces "no override" rather than silently gating.
MACRO_DEFAULT_VIX = 15.0
MACRO_DEFAULT_REGIME = "RISK ON"

# On-demand compute cap. Each symbol pays a GJR-GARCH(1,1) MLE fit + a full
# ATM Black-Scholes Greeks calc — the heaviest per-symbol compute in this
# codebase — so this stays "a handful of symbols" (an explicit operator
# action), not a whole-universe recompute (that remains the gated pipeline
# writer's job). 1 (a single-symbol lookup) to 8.
RECOMPUTE_MIN_SYMBOLS = 1
RECOMPUTE_MAX_SYMBOLS = 8


class _MacroProxy:
    """``MacroEconomicDTO``-shaped stub (``.vix``/``.market_regime`` only) so
    the VRP regime gate inside ``build_premium_directive`` fires without a
    live FRED round-trip. Mirrors
    ``gui/panels/options_matrix.py::_MacroProxy``."""

    def __init__(self, vix: float, market_regime: str):
        self.vix = vix
        self.market_regime = market_regime


def macro_from_snapshot(snapshot: Optional[dict]) -> Tuple[float, str]:
    """Extract ``(vix, market_regime)`` from a persisted state-snapshot dict,
    falling back to neutral defaults when either is absent/malformed. Mirrors
    ``gui/panels/options_matrix.py::_macro_from_snap``. Never raises."""
    if not isinstance(snapshot, dict):
        return MACRO_DEFAULT_VIX, MACRO_DEFAULT_REGIME
    raw_vix = snapshot.get("vix")
    try:
        vix = float(raw_vix) if raw_vix is not None else MACRO_DEFAULT_VIX
    except (TypeError, ValueError):
        vix = MACRO_DEFAULT_VIX
    regime = str(snapshot.get("market_regime") or MACRO_DEFAULT_REGIME)
    return vix, regime


def compute_directive_row(
    symbol: str,
    *,
    provider: Any,
    target_dte: int,
    vix: float,
    market_regime: str,
    risk_free_rate: float,
    ivr_sell_threshold: float = 50.0,
    ivr_buy_threshold: float = 30.0,
    delta_target_scale: float = 1.0,
    delta_tolerance: float = 0.05,
    strike_grid: float = 0.50,
) -> Dict[str, Any]:
    """Single-symbol premium-directive compute (no caching — a one-shot HTTP
    call, not a Streamlit rerun loop).

    Returns ``{"row": <directive dict>, "error": Optional[str]}``. Never
    raises (CONSTRAINT #6) — a bad symbol degrades to an error-shaped
    placeholder row, identical to the GUI panel's own dead-letter contract, so
    one bad symbol in a batch never aborts the others.
    """
    from technical_options_engine import build_premium_directive
    from data.market_data import MarketDataError

    macro_proxy = _MacroProxy(vix, market_regime)
    try:
        quote = provider.get_latest_quote(symbol)
        bars = provider.get_intraday_bars(symbol, lookback_days=252)
        row = build_premium_directive(
            symbol,
            bars,
            spot_price=float(quote.price),
            is_stale=bool(quote.is_stale),
            target_dte=int(target_dte),
            macro_dto=macro_proxy,
            vrp=None,  # VRP requires an options chain — left None to skip that gate
            risk_free_rate=risk_free_rate,
            ivr_sell_threshold=float(ivr_sell_threshold),
            ivr_buy_threshold=float(ivr_buy_threshold),
            delta_target_scale=float(delta_target_scale),
            delta_tolerance=float(delta_tolerance),
            strike_grid=float(strike_grid),
        )
        return {"row": row, "error": None}
    except MarketDataError as exc:
        logger.warning("options_ondemand: market data error for %s: %s", symbol, exc)
        row = {
            "Symbol": symbol,
            "Strategy": "—",
            "Action": "—",
            "Integrity_OK": False,
            "Integrity_Issues": [str(exc)],
        }
        return {"row": row, "error": f"{symbol}: market data unavailable ({exc})"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("options_ondemand: compute failed for %s: %s", symbol, exc)
        row = {
            "Symbol": symbol,
            "Strategy": "—",
            "Action": "—",
            "Integrity_OK": False,
            "Integrity_Issues": [str(exc)],
        }
        return {"row": row, "error": f"{symbol}: {exc}"}
