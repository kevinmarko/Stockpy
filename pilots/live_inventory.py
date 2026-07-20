"""pilots/live_inventory.py — coverage-reconciliation diagnostic for the PWA.
================================================================================

Reads the persisted portfolio-sync cache (``data/portfolio_sync.py``'s
``cache/sync_report.json``, written by the GUI Live Inventory tab's
"Sync Now" button / ``async_sync_now``) and reshapes it into the payload for
``GET /universe/coverage`` — the read-only counterpart of
``gui/panels/live_inventory.py``'s coverage-reconciliation table. Ticker
add/remove itself is already covered by ``UniverseManager`` (``GET/PUT
/data/universe`` on ``api/data_api.py``); this module is only the missing
FULL/EQUITY_ONLY/UNCOVERED diagnostic breakdown.

Design invariants:

* **Read-only / persisted-cache only** — never triggers a live sync probe;
  that stays a GUI/CLI-triggered action (``data.portfolio_sync.async_sync_now``
  makes network calls and is intentionally NOT reachable from a `GET`). Only
  imports ``data.portfolio_sync`` (stdlib-only at module import time — no
  heavy engine or network library is imported until ``build_sync_report``/
  ``async_sync_now`` are actually called, which this module never does) plus
  stdlib — mirrors the precedent of ``api/pilots_api.py`` importing
  ``data.historical_store`` directly for the Portfolio Heat metric.
* **Honesty (CONSTRAINT #4)** — the cache file is written with plain
  ``json.dumps`` (``allow_nan=True``), so a ``NaN`` in e.g. ``current_price``
  round-trips through ``json.loads`` as a Python float NaN. Every numeric leaf
  is coerced through :func:`_clean_float`, which nulls non-finite values —
  otherwise a NaN would re-serialize as a literal ``NaN`` token in the API
  response, which is invalid JSON and breaks the frontend's ``JSON.parse``.
* **Never raises (CONSTRAINT #6)** — a missing/corrupt/empty cache degrades to
  an honest empty shape + ``reason``, never an exception.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from data.portfolio_sync import read_cache

__all__ = ["universe_coverage"]

# Mirrors data.portfolio_sync.CoverageStatus's values exactly (duplicated as
# plain strings rather than importing the Enum, since only str comparison is
# needed here and it keeps this reader stdlib-only).
_COVERAGE_KEYS = ("full", "stale", "quotes_only", "equity_only", "uncovered", "unknown")

_NO_CACHE_REASON = (
    "No sync report yet — use Sync Now in the GUI (or the equivalent CLI "
    "trigger) to discover and reconcile the tracked universe against "
    "market-data coverage."
)
_EMPTY_CACHE_REASON = "Sync report cache is empty."


def _clean_float(value: Any) -> Optional[float]:
    """Finite float, else ``None`` — never a NaN/inf JSON literal (CONSTRAINT #4)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _clean_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _clean_row(raw: Any) -> Optional[Dict[str, Any]]:
    """Reshape one raw ``SymbolStatus.to_dict()`` entry, or ``None`` when
    malformed (a symbol without an honest, non-empty ``symbol`` key is dropped
    rather than surfaced as a blank row)."""
    if not isinstance(raw, dict):
        return None
    symbol = _clean_str(raw.get("symbol"))
    if symbol is None:
        return None
    watchlists = raw.get("watchlists")
    coverage = _clean_str(raw.get("coverage")) or "unknown"
    return {
        "symbol": symbol.upper(),
        "coverage": coverage if coverage in _COVERAGE_KEYS else "unknown",
        "held": bool(raw.get("held")),
        "quantity": _clean_float(raw.get("quantity")),
        "avg_cost": _clean_float(raw.get("avg_cost")),
        "current_price": _clean_float(raw.get("current_price")),
        "cost_basis_delta_per_share": _clean_float(raw.get("cost_basis_delta_per_share")),
        "market_value": _clean_float(raw.get("market_value")),
        "is_stale_quote": bool(raw.get("is_stale_quote")),
        "quote_source": _clean_str(raw.get("quote_source")),
        "has_fundamentals": bool(raw.get("has_fundamentals")),
        "forecast_available": bool(raw.get("forecast_available")),
        "watchlists": list(watchlists) if isinstance(watchlists, list) else [],
        "diagnostic": _clean_str(raw.get("diagnostic")),
    }


def universe_coverage(cache_path: Optional[str] = None) -> Dict[str, Any]:
    """Portfolio-sync coverage-reconciliation diagnostic.

    Returns ``{"generated_at", "provider_source", "fundamentals_source",
    "counts": {"full"/"stale"/"quotes_only"/"equity_only"/"uncovered"/
    "unknown": int}, "n_total", "symbols": [...], "reason"}``. ``symbols`` is
    sorted by ticker. ``reason`` is set (and ``symbols``/``counts`` degrade to
    an empty/zeroed shape) when no cache exists yet, it's corrupt, or it's
    genuinely empty — never raises.
    """
    cached = read_cache(Path(cache_path) if cache_path else None)

    empty_counts = {k: 0 for k in _COVERAGE_KEYS}
    if not isinstance(cached, dict):
        return {
            "generated_at": None,
            "provider_source": None,
            "fundamentals_source": None,
            "counts": empty_counts,
            "n_total": 0,
            "symbols": [],
            "reason": _NO_CACHE_REASON,
        }

    raw_symbols = cached.get("symbols")
    rows: List[Dict[str, Any]] = []
    if isinstance(raw_symbols, dict):
        for raw in raw_symbols.values():
            row = _clean_row(raw)
            if row is not None:
                rows.append(row)
    rows.sort(key=lambda r: r["symbol"])

    counts = dict(empty_counts)
    for row in rows:
        counts[row["coverage"]] += 1

    return {
        "generated_at": _clean_str(cached.get("generated_at")),
        "provider_source": _clean_str(cached.get("provider_source")),
        "fundamentals_source": _clean_str(cached.get("fundamentals_source")),
        "counts": counts,
        "n_total": len(rows),
        "symbols": rows,
        "reason": None if rows else _EMPTY_CACHE_REASON,
    }
