"""
engine/portfolio_exposure.py — Portfolio Net-Exposure Classifier
==================================================================
Joins the live/persisted portfolio (an ``AccountSnapshot``) against the
static ticker→sector map (``forecasting/data/ticker_sectors.csv``) to
produce a per-sector net-exposure breakdown.

Phase 2 PR3 (RAG-Powered Portfolio Contextualizer) — 3a.

Design principles
------------------
* **Pure function, no I/O beyond a cached CSV read** (CONSTRAINT #6-style
  resilience): :func:`compute_sector_exposure` never raises. A missing/
  unreadable CSV degrades to every symbol landing in the ``"Unknown"``
  sector bucket rather than crashing or dropping symbols.
* **Never drop a symbol** (CONSTRAINT #4): a symbol absent from the sector
  map is classified ``sector="Unknown"``, not silently excluded — so
  ``pct_of_equity`` totals across all buckets still reconcile to ~100% of
  ``snapshot.total_equity`` (modulo cash/buying-power, which this module
  does not attempt to bucket).
* ``market_value`` already carries sign (long vs. short) per
  ``data/robinhood_portfolio.py``'s ``PortfolioPosition`` dataclass
  (``quantity * current_price``), so net long/short exposure per sector
  falls directly out of summing ``market_value`` — no separate signed-
  quantity handling is needed here.
"""

from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:
    from data.robinhood_portfolio import AccountSnapshot

logger = logging.getLogger(__name__)

_UNKNOWN_SECTOR = "Unknown"

_TICKER_SECTORS_CSV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "forecasting", "data", "ticker_sectors.csv",
)

# Module-level cache: {symbol: sector}. Populated lazily on first use by
# _load_sector_map() and reused for the lifetime of the process — the CSV is
# a small, rarely-changing static artifact (see
# scripts/build_ticker_sector_map.py), not per-cycle data.
_SECTOR_MAP_CACHE: Dict[str, str] = {}
_SECTOR_MAP_LOADED = False


@dataclass(frozen=True)
class SectorExposure:
    """Net exposure to one GICS-style sector across the current portfolio."""

    sector: str
    net_market_value: float
    pct_of_equity: float
    symbols: List[str] = field(default_factory=list)


def _load_sector_map(csv_path: str = _TICKER_SECTORS_CSV) -> Dict[str, str]:
    """Return a cached ``{symbol: sector}`` map, reading the CSV once.

    Never raises — a missing/unreadable/malformed CSV logs a warning and
    returns whatever was cached before (``{}`` on first-ever failure), so
    every symbol subsequently degrades to ``"Unknown"`` rather than crashing
    the caller (CONSTRAINT #6).
    """
    global _SECTOR_MAP_LOADED
    if _SECTOR_MAP_LOADED:
        return _SECTOR_MAP_CACHE
    try:
        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                symbol = (row.get("symbol") or "").strip().upper()
                sector = (row.get("sector") or "").strip()
                if symbol and sector:
                    _SECTOR_MAP_CACHE[symbol] = sector
        _SECTOR_MAP_LOADED = True
        logger.debug(
            "portfolio_exposure: loaded %d symbol->sector mappings from %s.",
            len(_SECTOR_MAP_CACHE), csv_path,
        )
    except Exception as exc:
        logger.warning(
            "portfolio_exposure: failed to load ticker_sectors.csv (%s): %s. "
            "All symbols will classify as 'Unknown'.", csv_path, exc,
        )
        _SECTOR_MAP_LOADED = True  # don't retry every call — CSV isn't expected to appear mid-process
    return _SECTOR_MAP_CACHE


def reset_sector_map_cache() -> None:
    """Clear the module-level sector-map cache (test-only helper)."""
    global _SECTOR_MAP_LOADED
    _SECTOR_MAP_CACHE.clear()
    _SECTOR_MAP_LOADED = False


def compute_sector_exposure(
    snapshot: "AccountSnapshot",
    *,
    csv_path: str = _TICKER_SECTORS_CSV,
) -> Dict[str, SectorExposure]:
    """Return per-sector net exposure for *snapshot*.

    Joins ``snapshot.positions`` (``{symbol: PortfolioPosition}``) against
    the static ticker→sector map. Unmapped symbols are classified
    ``sector="Unknown"`` — never dropped — so ``pct_of_equity`` totals
    across every returned bucket still reconcile to the portfolio's total
    invested market value (as a fraction of ``snapshot.total_equity``).

    Pure function: no network calls, no DB reads — the only I/O is the
    lazily-cached CSV read in :func:`_load_sector_map`. Never raises: any
    unexpected error degrades to ``{}`` (CONSTRAINT #6).

    Parameters
    ----------
    snapshot:
        The account snapshot to classify. ``snapshot.positions`` may be
        empty (returns ``{}``).
    csv_path:
        Override for the ticker→sector CSV path (test injection point).

    Returns
    -------
    Dict[str, SectorExposure]
        One entry per sector observed in the portfolio, keyed by sector
        name.
    """
    try:
        positions = getattr(snapshot, "positions", None) or {}
        if not positions:
            return {}

        sector_map = _load_sector_map(csv_path)
        total_equity = float(getattr(snapshot, "total_equity", 0.0) or 0.0)

        buckets: Dict[str, Dict[str, object]] = {}
        for symbol, position in positions.items():
            sym = str(symbol).upper()
            sector = sector_map.get(sym, _UNKNOWN_SECTOR)
            market_value = float(getattr(position, "market_value", 0.0) or 0.0)

            bucket = buckets.setdefault(
                sector, {"net_market_value": 0.0, "symbols": []}
            )
            bucket["net_market_value"] += market_value  # type: ignore[operator]
            bucket["symbols"].append(sym)  # type: ignore[union-attr]

        result: Dict[str, SectorExposure] = {}
        for sector, bucket in buckets.items():
            net_mv = float(bucket["net_market_value"])  # type: ignore[arg-type]
            pct = (net_mv / total_equity) if total_equity else 0.0
            result[sector] = SectorExposure(
                sector=sector,
                net_market_value=net_mv,
                pct_of_equity=pct,
                symbols=sorted(bucket["symbols"]),  # type: ignore[arg-type]
            )
        return result
    except Exception as exc:
        logger.warning("compute_sector_exposure failed: %s", exc)
        return {}
