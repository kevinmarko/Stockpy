"""data/fmp_feeds_market.py
=============================
Market-wide (not per-symbol-scoring) Financial Modeling Prep diagnostic
feeds: quarterly insider-trading statistics and dated sector PE /
performance snapshots.

Companion to ``data/fmp_feeds_company.py`` (per-symbol analyst + earnings
feeds, owned by a different wave-1 agent). The split follows a real cohesion
boundary, not an arbitrary file cut: insider stats are one FMP request PER
SYMBOL, while sector snapshots are exactly TWO requests per CYCLE for the
whole universe — very different rate-limit shapes that deserve independent
cadence gates (``settings.FMP_INSIDER_ENABLED`` / ``FMP_INSIDER_REFRESH_DAYS``
vs. ``settings.FMP_SECTOR_SNAPSHOT_ENABLED``, both consulted by the callers
in ``pipeline/production_steps.py``, not here).

This module is deliberately I/O-thin and does no persistence itself — it
only fetches from ``data/fmp_client.py`` and reshapes the vendor's raw JSON
into the row schema ``data/historical_store.py``'s ``upsert_insider_stats`` /
``upsert_sector_snapshots`` expect. Cadence gating, archival, the
minimum-lag filter, and the dashboard write-back all live in
``pipeline/production_steps.py::_apply_fmp_insider`` /
``_apply_fmp_sector`` — this module never reads ``HistoricalStore`` and
never reads ``dashboard_df``.

CONSTRAINT #6, everywhere in this module: every public function returns
``[]`` on ANY failure — a rejected key, an out-of-plan endpoint, an open
breaker cooldown, a malformed vendor row, an unexpected exception — and
never raises into the pipeline. CONSTRAINT #4, everywhere: a field the
vendor did not report is omitted/NaN, never a fabricated 0.0 or a guessed
ratio.

Honesty note on the sector-snapshot field names: the exact JSON keys
``/sector-pe-snapshot`` and ``/sector-performance-snapshot`` return could
not be verified against a live response from this sandbox (no live-market
network access here — see the plan's own "Cannot be verified in this
sandbox" section). ``pe`` is FMP's documented field name for the PE
snapshot and is used directly. For the performance snapshot's change
figure, several plausible vendor field names are tried in a fixed
preference order (``_CHANGE_PCT_KEYS`` below) rather than committing to one
unverified guess. Whichever key is used, THE VALUE IS STORED EXACTLY AS THE
VENDOR RETURNED IT — no assumed percent-vs-fraction conversion is applied,
because guessing that conversion wrong (see ``dividendYield``'s unit-guard
precedent in ``data/fmp_fundamentals.py``'s design) is a worse failure than
storing an unconverted-but-real number. This should be confirmed against a
live response before ``FMP_SECTOR_SNAPSHOT_ENABLED`` is ever flipped on for
real, exactly like every other FMP field-shape assumption in this series.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Preference-ordered candidate keys for the sector-performance-snapshot's
# change figure. `averageChange` is FMP's documented stable-API field name
# for this endpoint; the rest are defensive fallbacks in case the live shape
# differs from what could be probed. First non-null match wins.
_CHANGE_PCT_KEYS: tuple = (
    "averageChange",
    "changesPercentage",
    "changePercentage",
    "percentChange",
)


def _safe_float(value: Any) -> float:
    """``float(value)``, or NaN for ``None``/unparseable — never raises,
    never a fabricated 0.0 (CONSTRAINT #4)."""
    if value is None:
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _safe_int(value: Any) -> Optional[int]:
    """``int(value)``, or ``None`` for ``None``/unparseable. ``None`` (not a
    fabricated 0) is what ``HistoricalStore.upsert_insider_stats`` expects
    for an unreported integer field."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _as_row_list(raw: Any) -> List[Dict[str, Any]]:
    """Normalize a vendor payload to a list of dict rows.

    FMP's documented shape for both endpoints in this module is a bare JSON
    list; a single dict is tolerated (wrapped as a one-row list) in case a
    single-sector/single-quarter response is ever serialized unwrapped. Any
    other shape (``None``, an empty ``{}``/``[]``, a string, ...) degrades to
    ``[]`` rather than raising.
    """
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    if isinstance(raw, dict):
        return [raw]
    return []


def _first_present(row: Dict[str, Any], keys: tuple) -> Any:
    """First key in *keys* whose value in *row* is not ``None``."""
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def fetch_insider_stats(symbol: str) -> List[Dict[str, Any]]:
    """Fetch FMP's quarterly insider-transaction aggregates for one symbol.

    Source: ``fmp_client.insider_trade_statistics(symbol)``
    (``/insider-trading/statistics``), shaped for
    ``HistoricalStore.upsert_insider_stats``'s row schema: ``symbol``,
    ``year``, ``quarter``, ``acquired_transactions``, ``disposed_transactions``,
    ``acquired_disposed_ratio``, ``total_acquired``, ``total_disposed``,
    ``total_purchases``, ``total_sales``, ``source``.

    Includes EVERY quarter the vendor returns — deliberately does NOT apply
    the minimum-lag filter here. That belongs downstream, in
    ``pipeline/production_steps.py::_apply_fmp_insider``, so the archive
    itself stays a complete, auditable record of what was actually fetched
    (mirroring ``HistoricalStore.get_insider_stats``'s own docstring
    rationale for not filtering in storage either). Filtering at the fetch
    layer would make it impossible to later widen
    ``FMP_INSIDER_MIN_LAG_DAYS`` and recover the now-qualifying quarters
    without re-fetching from the vendor.

    ``acquired_disposed_ratio`` is taken from FMP's own
    ``acquiredDisposedRatio`` field when present; otherwise it is computed as
    ``total_acquired / total_disposed`` when ``total_disposed > 0``, and NaN
    otherwise — never a fabricated ratio (CONSTRAINT #4). A row missing
    ``year``/``quarter`` (the primary-key fields) is dropped — it cannot be
    stored or lag-filtered without them.

    ``[]`` on ANY failure — a rejected key, a malformed response, a symbol
    with zero rows, or an unexpected exception. Never raises (CONSTRAINT #6).
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return []
    try:
        from data.fmp_client import FMPUnavailable, insider_trade_statistics

        try:
            raw = insider_trade_statistics(sym)
        except FMPUnavailable as exc:
            logger.warning("FMP insider stats unavailable for %s: %s", sym, exc)
            return []

        rows_out: List[Dict[str, Any]] = []
        for row in _as_row_list(raw):
            year = _safe_int(row.get("year"))
            quarter = _safe_int(row.get("quarter"))
            if year is None or quarter is None:
                continue

            total_acquired = _safe_float(row.get("totalAcquired"))
            total_disposed = _safe_float(row.get("totalDisposed"))

            vendor_ratio = row.get("acquiredDisposedRatio")
            if vendor_ratio is not None:
                ratio = _safe_float(vendor_ratio)
            elif total_disposed == total_disposed and total_disposed > 0:  # not NaN
                ratio = total_acquired / total_disposed
            else:
                ratio = float("nan")

            rows_out.append({
                "symbol": (str(row.get("symbol") or "").strip().upper() or sym),
                "year": year,
                "quarter": quarter,
                "acquired_transactions": _safe_int(row.get("acquiredTransactions")),
                "disposed_transactions": _safe_int(row.get("disposedTransactions")),
                "acquired_disposed_ratio": ratio,
                "total_acquired": total_acquired,
                "total_disposed": total_disposed,
                "total_purchases": _safe_int(row.get("totalPurchases")),
                "total_sales": _safe_int(row.get("totalSales")),
                "source": "fmp",
            })
        return rows_out
    except Exception as exc:
        logger.warning("fetch_insider_stats(%s) failed: %s", sym, exc)
        return []


def fetch_sector_snapshot(
    as_of_date: str, *, exchange: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch ONE cycle's dated sector-PE + sector-performance snapshot.

    Exactly 2 requests total, for the WHOLE universe — never per symbol.
    Sources: ``fmp_client.sector_pe_snapshot(as_of_date, exchange)`` and
    ``fmp_client.sector_performance_snapshot(as_of_date, exchange)``, merged
    by sector name into rows shaped for
    ``HistoricalStore.upsert_sector_snapshots``: ``sector``, ``date``,
    ``pe``, ``change_pct``, ``source``.

    ALWAYS calls the dated endpoint form — ``as_of_date`` is the SOURCE's own
    snapshot date and is stamped into every row's ``date`` field verbatim,
    never replaced with today's fetch timestamp. This is the one FMP feed in
    the whole series with a genuine point-in-time story, and an undated call
    would throw that away.

    The two vendor calls are independently try/excepted so that ONE
    endpoint being unavailable (a plan-entitlement 403, a transient outage)
    does not blank the other's real data — a sector present in only one of
    the two responses still gets a row, with the missing metric left as NaN
    rather than the whole cycle degrading to nothing (CONSTRAINT #4: a
    partial vendor response is still real data for the fields it did
    report). Only when BOTH calls fail, or the merged result has no sectors
    at all, does this return ``[]``. Never raises (CONSTRAINT #6).
    """
    date_str = (as_of_date or "").strip()
    if not date_str:
        return []
    try:
        from data.fmp_client import (
            FMPUnavailable,
            sector_pe_snapshot,
            sector_performance_snapshot,
        )

        pe_map: Dict[str, float] = {}
        change_map: Dict[str, float] = {}

        try:
            pe_raw = sector_pe_snapshot(date_str, exchange=exchange)
        except FMPUnavailable as exc:
            logger.warning(
                "FMP sector PE snapshot unavailable for %s: %s", date_str, exc,
            )
            pe_raw = None
        for row in _as_row_list(pe_raw):
            sector = str(row.get("sector") or "").strip()
            if not sector:
                continue
            pe_map[sector] = _safe_float(row.get("pe"))

        try:
            perf_raw = sector_performance_snapshot(date_str, exchange=exchange)
        except FMPUnavailable as exc:
            logger.warning(
                "FMP sector performance snapshot unavailable for %s: %s",
                date_str, exc,
            )
            perf_raw = None
        for row in _as_row_list(perf_raw):
            sector = str(row.get("sector") or "").strip()
            if not sector:
                continue
            change_map[sector] = _safe_float(_first_present(row, _CHANGE_PCT_KEYS))

        sectors = sorted(set(pe_map) | set(change_map))
        if not sectors:
            return []

        return [
            {
                "sector": sector,
                "date": date_str,
                "pe": pe_map.get(sector, float("nan")),
                "change_pct": change_map.get(sector, float("nan")),
                "source": "fmp",
            }
            for sector in sectors
        ]
    except Exception as exc:
        logger.warning("fetch_sector_snapshot(%s) failed: %s", date_str, exc)
        return []
