"""pilots/sector_selection.py — semantic Related Sector Selection ranking
for the PWA.
=========================================================================
Surfaces the most recently computed candidate-sector ranking
(``data/sector_correlation_store.py``, written by
``sector_selection_engine.py``) for ``GET /sector/selection``: cosine
similarity, ingestion volume, Sector Heat Factor, and the final
``correlation_coefficient`` per candidate sector, re-derived ``selected``
status for whatever top-N the caller's slider currently asks for.

Design invariants (identical to the rest of the Pilots read layer):

* **Light-module read** — ``data.sector_correlation_store`` is SQLAlchemy +
  ``db_config`` only; it does not import any of the AST-forbidden heavy
  engines, so this is safe on the ``api/pilots_api.py`` import path. Reads
  persisted DB state only — no network, no engine, no live embedding call.
* **Honesty (CONSTRAINT #4)** — every numeric field is whatever was
  persisted (``None`` where the engine recorded a degraded/unavailable
  value); nothing is fabricated here.
* **Never raises (CONSTRAINT #6)** — any DB/import failure degrades to the
  empty view with an honest ``reason``.
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
         rank, selected, degraded_reason}], embedder, pooling, reason}

    ``reason`` is ``None`` on a normal hit, else an honest "nothing
    computed yet" string. ``store`` is injectable for tests; ``None``
    lazily constructs a real, read-only ``SectorCorrelationStore``. Never
    raises (CONSTRAINT #6).
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

    view_rows: List[Dict[str, Any]] = []
    for row in rows:
        rank = row.get("rank")
        view_rows.append({
            "sector": row.get("sector"),
            "cosine_similarity": row.get("cosine_similarity"),
            "ingestion_volume": row.get("ingestion_volume"),
            "sector_heat_factor": row.get("sector_heat_factor"),
            "correlation_coefficient": row.get("correlation_coefficient"),
            "rank": rank,
            "selected": rank is not None and rank <= top_n,
            "degraded_reason": row.get("degraded_reason"),
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
