"""pilots/pairs.py — read the persisted pairs-trading radar for the PWA.
=======================================================================

Pure reader over ``output/pairs.json`` (written by
``reporting/pairs_snapshot.py`` during the pipeline when
``settings.PAIRS_SNAPSHOT_ENABLED`` is on). Powers the mobile ``GET /pairs``
endpoint.

Design invariants (identical to the rest of the Pilots read layer):

* **Read-only / persisted-state only** — imports only ``settings`` + stdlib.
  NEVER imports ``pairs.cointegration`` / ``signals.pairs_trading`` /
  ``statsmodels`` (heavy compute); it reads the artifact the pipeline persisted.
* **Advisory only** — surfaces a display label; no order code.
* **Honesty (CONSTRAINT #4)** — a missing artifact yields an empty radar with an
  honest ``reason``; persisted rows already carry ``null`` for uncomputable leaves.
* **Never raises (CONSTRAINT #6)** — a missing/corrupt file degrades to empty.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from settings import settings

logger = logging.getLogger(__name__)

__all__ = ["load_pairs_snapshot", "pairs_radar"]

_FILENAME = "pairs.json"
_DISABLED_REASON = (
    "Pairs radar not generated yet — enable PAIRS_SNAPSHOT_ENABLED and run the pipeline."
)


def _default_path() -> Path:
    return settings.OUTPUT_DIR / _FILENAME


def load_pairs_snapshot(path: Optional[str] = None) -> Optional[dict]:
    """Load the persisted pairs JSON, or ``None`` (never raises)."""
    p = Path(path) if path else _default_path()
    try:
        if not p.exists():
            return None
        obj = json.loads(p.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("load_pairs_snapshot failed: %s", exc)
        return None


def pairs_radar(path: Optional[str] = None) -> Dict[str, Any]:
    """Return ``{as_of, universe, pairs, reason}`` — the persisted cointegrated
    pair ranking + current spread state, or an honest empty radar + ``reason``."""
    obj = load_pairs_snapshot(path)
    if obj is None:
        return {"as_of": None, "universe": [], "pairs": [], "reason": _DISABLED_REASON}
    pairs = obj.get("pairs")
    if not isinstance(pairs, list):
        pairs = []
    universe = obj.get("universe")
    if not isinstance(universe, list):
        universe = []
    return {
        "as_of": obj.get("timestamp"),
        "universe": universe,
        "pairs": pairs,
        "reason": None if pairs else _DISABLED_REASON,
    }
