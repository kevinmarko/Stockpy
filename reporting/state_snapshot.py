"""Rating helpers the orchestrator's state-snapshot writer imports.

This module used to hold ``main.py``'s advisory state-snapshot writer
(``write_state_snapshot``) and Δ-diff loader (``load_snapshot_diff``). Their only
caller was ``reporting/html_publisher.py`` (``daily_report.html``), so both moved
to ``legacy/reporting/state_snapshot.py`` with ``main.py`` in shrink step 5.5.
What stays is imported by ``main_orchestrator.py``, the only snapshot writer now.
"""

from __future__ import annotations

from typing import Optional

from numeric_utils import safe_float as _safe_float_or_none  # noqa: F401 -- re-exported for main_orchestrator
from settings import settings


def _rating_consecutive_cycles(symbol: str) -> Optional[int]:
    """Best-effort read of ``rating.symbol_rating_store.SymbolRatingStore
    .get_consecutive_bad_cycles`` for *symbol*. ``None`` (never a fabricated
    0) on any failure -- CONSTRAINT #6, mirrors this module's own
    swallow-all-errors contract; a snapshot write must never fail on it."""
    try:
        from rating.symbol_rating_store import SymbolRatingStore

        return SymbolRatingStore(readonly=True).get_consecutive_bad_cycles(symbol)
    except Exception:
        return None


def _rating_is_excluded(symbol: str, *, is_held: bool) -> bool:
    """Diagnostic-only: would this symbol currently be dropped by
    ``settings.SYMBOL_RATING_AUTO_DROP_ENABLED`` (regardless of whether that
    flag is actually on)? A held symbol is never excluded (see
    ``rating/symbol_rating.py::should_exclude``'s non-negotiable invariant).
    Degrades to ``False`` when rating history is unavailable/unknown --
    never fabricates an exclusion."""
    if is_held:
        return False
    cycles = _rating_consecutive_cycles(symbol)
    if cycles is None:
        return False
    return cycles >= settings.SYMBOL_RATING_DROP_THRESHOLD_CYCLES
