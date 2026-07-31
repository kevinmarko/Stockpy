"""pilots/dead_letter.py — dependency-light read of the pipeline's dead-letter
queue, backing ``GET /dead-letter``.

Ports ``gui/dead_letter.py``'s read logic (``read_dead_letter``) into a plain,
JSON-serializable dict rather than importing that module directly — mirrors
this package's established "port the tiny stable read, don't import a
sibling package" convention (see e.g. ``pilots/run_status.py`` porting
``scripts/preflight_check.py``'s freshness check, and
``pilots/strategy_health.py`` porting its own JSONL history read rather than
importing ``validation.harness``). Two differences from
``gui/dead_letter.py``'s own resolution, both deliberate:

* Reads ``settings.OUTPUT_DIR`` live rather than a module-level constant
  resolved off ``__file__`` — matching every other reader in this package
  (``pilots/run_status.py``, ``pilots/reports.py``, ...) and letting tests
  monkeypatch ``settings.OUTPUT_DIR`` the same way they already do for every
  other endpoint in ``api/pilots_api.py``.
* Returns a plain ``dict`` (JSON-ready), not the frozen dataclasses
  ``gui/dead_letter.py`` uses for its own Streamlit rendering.

Dependency-light (stdlib + ``settings`` only) — pinned by
``tests/test_pilots_strategy_matrix.py::test_pilots_read_helpers_stay_dependency_light``.

Honesty (CONSTRAINT #4/#6): a missing/corrupt ``output/dead_letter.json``
degrades to ``entries: []``, ``is_clean: None`` (NOT ``True`` — "no run has
completed yet" is not the same claim as "the last run was clean"), and an
honest ``reason`` — never an exception, never a fabricated clean/dirty
verdict.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from settings import settings

logger = logging.getLogger(__name__)

__all__ = ["read_dead_letter"]

_MISSING_REASON = "No dead-letter report yet — run the pipeline once to populate it."
_CORRUPT_REASON = "output/dead_letter.json is unreadable or malformed."


def _empty(reason: str) -> Dict[str, Any]:
    return {
        "run_id": None,
        "generated_at": None,
        "entries": [],
        "is_clean": None,
        "reason": reason,
    }


def read_dead_letter() -> Dict[str, Any]:
    """Parse ``output/dead_letter.json`` into a plain dict.

    Shape (success): ``{run_id, generated_at, entries: [{symbol, stage,
    error, timestamp}, ...], is_clean, reason: None}``. On a missing/corrupt
    file: the empty shape above with ``is_clean: None`` and an honest
    ``reason`` — never raises."""
    path = settings.OUTPUT_DIR / "dead_letter.json"
    if not path.exists():
        return _empty(_MISSING_REASON)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw_entries = data.get("entries", [])
        if not isinstance(raw_entries, list):
            raise TypeError("entries is not a list")
        entries: List[Dict[str, Any]] = [
            {
                "symbol": str(e.get("symbol", "")),
                "stage": str(e.get("stage", "unknown")),
                "error": str(e.get("error", "")),
                "timestamp": str(e.get("timestamp", "")),
            }
            for e in raw_entries
        ]
    except (json.JSONDecodeError, TypeError, KeyError, AttributeError) as exc:
        logger.warning("pilots.dead_letter: corrupt or unreadable %s: %s", path, exc)
        return _empty(_CORRUPT_REASON)

    return {
        "run_id": str(data.get("run_id", "")) or None,
        "generated_at": str(data.get("generated_at", "")) or None,
        "entries": entries,
        "is_clean": len(entries) == 0,
        "reason": None,
    }
