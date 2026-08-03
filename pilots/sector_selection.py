"""pilots/sector_selection.py — semantic Related Sector Selection ranking
for the PWA.
=========================================================================
Surfaces the most recently computed candidate-sector ranking
(``data/sector_correlation_store.py``, written by
``sector_selection_engine.py``) for ``GET /sector/selection``: cosine
similarity, ingestion volume, Sector Heat Factor, and the final
``correlation_coefficient`` per candidate sector, re-derived ``selected``
status for whatever top-N the caller's slider currently asks for. Also
bulk-attaches each candidate sector's dated FMP P/E + 1-day-change snapshot
(``data/historical_store.py::get_sector_snapshots``, populated only when
``settings.FMP_SECTOR_SNAPSHOT_ENABLED`` — a DIFFERENT concern entirely from
the semantic similarity ranking above) as ``pe``/``change_pct`` — a pure
valuation-context decoration, never fed back into ``correlation_coefficient``
or the rank ordering.

Design invariants (identical to the rest of the Pilots read layer):

* **Light-module read** — ``data.sector_correlation_store`` is SQLAlchemy +
  ``db_config`` only; it does not import any of the AST-forbidden heavy
  engines, so this is safe on the ``api/pilots_api.py`` import path. Reads
  persisted DB state only — no network, no engine, no live embedding call.
  ``data.historical_store.HistoricalStore(readonly=True)`` (imported lazily,
  this codebase's established convention for that class) is the same kind of
  light, persisted-DB-only read.
* **Honesty (CONSTRAINT #4)** — every numeric field is whatever was
  persisted (``None`` where the engine recorded a degraded/unavailable
  value); nothing is fabricated here. ``pe``/``change_pct`` are ``None`` when
  the row's sector has no snapshot at all (either the feed is disabled, or
  this particular sector name was never covered by it) — never a
  neighboring/default sector's value.
* **Never raises (CONSTRAINT #6)** — any DB/import failure degrades to the
  empty view with an honest ``reason``; a ``get_sector_snapshots`` failure
  independently degrades ``pe``/``change_pct`` to ``None`` on every row
  without affecting the similarity ranking at all (the bulk fetch is wrapped
  in its own try/except, called ONCE per request, never per row).
* **N-slider re-ranking without re-computing** — the persisted rows already
  carry a numerically-final ``rank`` (1..K by ``correlation_coefficient``
  descending, computed once by the engine). Changing the UI's N slider
  only needs to re-derive ``selected = rank is not None and rank <= n``
  against that already-correct ordering — it does NOT re-run cosine
  similarity or Sector Heat Factor, which is why this stays a light,
  synchronous read.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["sector_selection_view"]

_DEFAULT_TOP_N = 3


def _empty_view(symbol: str, n: int) -> Dict[str, Any]:
    return {
        "target_symbol": symbol,
        "as_of": None,
        "top_n": int(n),
        "rows": [],
        "embedder": None,
        "pooling": None,
        "reason": "No sector selection has been computed for this symbol yet.",
    }


def sector_selection_view(
    target: str, n: int = _DEFAULT_TOP_N, *, store: Optional[Any] = None,
) -> Dict[str, Any]:
    """Return the most recent candidate-sector ranking for ``target``,
    re-selecting the top ``n`` from the already-persisted ordering.

    Shape::

        {target_symbol, as_of, top_n, rows: [{sector, cosine_similarity,
         ingestion_volume, sector_heat_factor, correlation_coefficient,
         rank, selected, degraded_reason, pe, change_pct}], embedder,
         pooling, reason}

    ``reason`` is ``None`` on a normal hit, else an honest "nothing
    computed yet" string. ``store`` is injectable for tests; ``None``
    lazily constructs a real, read-only ``SectorCorrelationStore``.

    ``pe``/``change_pct`` are attached from ONE bulk
    ``HistoricalStore(readonly=True).get_sector_snapshots(as_of=<today>)``
    call (never per-row), matched onto each row by its ``sector`` name.
    ``None`` on both fields when the sector has no snapshot, OR when the
    bulk fetch itself fails — this never affects the similarity ranking or
    raises (CONSTRAINT #6/#4).
    """
    sym = str(target or "").upper().strip()
    top_n = int(n)
    if not sym:
        return _empty_view(sym, top_n)

    if store is None:
        try:
            from data.sector_correlation_store import SectorCorrelationStore
            # A GET endpoint never writes; read-only also means a fresh
            # install doesn't silently create the table as a side effect
            # of a read.
            store = SectorCorrelationStore(readonly=True)
        except Exception as exc:  # noqa: BLE001 — dead-letter
            logger.debug("SectorCorrelationStore unavailable: %s", exc)
            return _empty_view(sym, top_n)

    try:
        rows = store.get_latest(sym)
    except Exception as exc:  # noqa: BLE001
        logger.debug("get_latest failed for %s: %s", sym, exc)
        rows = []

    if not rows:
        return _empty_view(sym, top_n)

    # Bulk sector-valuation-snapshot fetch — ONE call for the whole request,
    # never per row. Independently try/excepted (CONSTRAINT #6): a
    # HistoricalStore failure degrades pe/change_pct to None on every row
    # rather than blanking the similarity ranking above, which already
    # succeeded. No settings gate here — this is a pure DB read of a table
    # that's only ever non-empty when settings.FMP_SECTOR_SNAPSHOT_ENABLED is
    # already on elsewhere (pipeline/production_steps.py::_apply_fmp_sector);
    # when that flag is off the table is empty and every row correctly gets
    # None for both fields.
    sector_snapshots: Dict[str, Dict[str, Any]] = {}
    try:
        from data.historical_store import HistoricalStore

        sector_snapshots = HistoricalStore(readonly=True).get_sector_snapshots(
            as_of=date.today().isoformat(),
        )
    except Exception as exc:  # noqa: BLE001 — dead-letter, CONSTRAINT #6
        logger.debug("get_sector_snapshots unavailable: %s", exc)
        sector_snapshots = {}

    view_rows: List[Dict[str, Any]] = []
    for row in rows:
        rank = row.get("rank")
        sector_name = row.get("sector")
        snap = sector_snapshots.get(sector_name) if sector_name else None
        view_rows.append({
            "sector": sector_name,
            "cosine_similarity": row.get("cosine_similarity"),
            "ingestion_volume": row.get("ingestion_volume"),
            "sector_heat_factor": row.get("sector_heat_factor"),
            "correlation_coefficient": row.get("correlation_coefficient"),
            "rank": rank,
            "selected": rank is not None and rank <= top_n,
            "degraded_reason": row.get("degraded_reason"),
            # Valuation-context decoration, unrelated to the semantic ranking
            # above — never a fabricated/neighboring-sector value
            # (CONSTRAINT #4).
            "pe": snap.get("pe") if snap else None,
            "change_pct": snap.get("change_pct") if snap else None,
        })

    first = rows[0]
    return {
        "target_symbol": sym,
        "as_of": first.get("as_of"),
        "top_n": top_n,
        "rows": view_rows,
        "embedder": first.get("embedder"),
        "pooling": first.get("pooling"),
        "reason": None,
    }
