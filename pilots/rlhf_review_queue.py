"""pilots/rlhf_review_queue.py — RLHF Calibration Review Queue reader (READ-ONLY).
====================================================================================

Surfaces ``rlhf_calibration_store.py``'s pending-proposal queue and summary
stats for the Pilots PWA (``GET /rlhf/proposals``, ``GET /rlhf/summary`` --
wired up by a later round). Every proposal is a hypothetical, paper-only AI
trade the operator rates 1-5 stars -- no capital, no broker, no
``TransactionsStore`` involvement (see ``rlhf_calibration_store.py``'s module
docstring for why this is a deliberately separate table).

Design invariants (identical to the rest of the Pilots read layer, see e.g.
``pilots/forecast_skill.py`` / ``pilots/calibration.py``):

* **Read-only** — both views construct ``RlhfCalibrationStore(readonly=True)``,
  never the write-mode store, so a GET request can never create the table as a
  side effect nor accidentally mutate a row.
* **Never raises (CONSTRAINT #6)** — any import/construction/query failure
  degrades to an honest empty/neutral shape + a short ``reason`` string.
* **Never fabricates (CONSTRAINT #4)** — nothing here invents a rating, a
  count, or an average; ``rlhf_calibration_store.get_summary_stats`` already
  returns ``average_human_rating: None`` over zero rated rows, and that
  ``None`` is passed straight through.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

__all__ = ["pending_queue_view", "summary_stats_view"]

_NO_QUEUE_REASON = "RLHF calibration queue is unavailable or empty."


def _empty_summary_stats() -> Dict[str, Any]:
    # Mirrors rlhf_calibration_store._empty_summary_stats's shape exactly --
    # duplicated (not imported) so this module still degrades honestly even
    # when rlhf_calibration_store itself fails to import.
    return {
        "pending_count": 0,
        "reviewed_count": 0,
        "average_human_rating": None,
        "rating_distribution": {str(i): 0 for i in range(1, 6)},
        "auto_approved_count": 0,
        "sft_exported_count": 0,
    }


def pending_queue_view(limit: int = 50) -> Dict[str, Any]:
    """Newest-first pending proposals for the review queue screen.

    Returns ``{"proposals": [...], "reason": str | None}`` — ``reason`` is
    ``None`` on a normal hit (even an honestly empty queue with no failure),
    else a short human-readable explanation. Never raises."""
    try:
        from rlhf_calibration_store import RlhfCalibrationStore

        store = RlhfCalibrationStore(readonly=True)
    except Exception as exc:  # noqa: BLE001 — dead-letter: import/construction failure
        logger.debug("pending_queue_view: store unavailable: %s", exc)
        return {"proposals": [], "reason": _NO_QUEUE_REASON}

    try:
        proposals = store.get_pending(limit=limit)
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.warning("pending_queue_view: get_pending failed: %s", exc)
        return {"proposals": [], "reason": _NO_QUEUE_REASON}

    return {
        "proposals": proposals,
        "reason": None if proposals else "No pending proposals.",
    }


def summary_stats_view() -> Dict[str, Any]:
    """The ``RlhfCalibrationStore.get_summary_stats()`` shape, read-only.

    Degrades to the same zeroed/``None`` shape ``get_summary_stats`` itself
    returns on a DB failure -- this wrapper's own try/except only needs to
    cover import/construction, since the store method already never raises."""
    try:
        from rlhf_calibration_store import RlhfCalibrationStore

        store = RlhfCalibrationStore(readonly=True)
    except Exception as exc:  # noqa: BLE001 — dead-letter: import/construction failure
        logger.debug("summary_stats_view: store unavailable: %s", exc)
        return _empty_summary_stats()

    try:
        return store.get_summary_stats()
    except Exception as exc:  # noqa: BLE001 — dead-letter (belt-and-suspenders)
        logger.warning("summary_stats_view: get_summary_stats failed: %s", exc)
        return _empty_summary_stats()
