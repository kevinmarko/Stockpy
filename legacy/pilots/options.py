"""pilots/options.py — read the persisted options premium matrix for the PWA.
=============================================================================

Pure reader over ``output/options_matrix.json`` (written by
``reporting/options_snapshot.py`` during the pipeline when
``settings.OPTIONS_MATRIX_ENABLED`` is on). Powers the mobile ``GET /options``
and ``GET /symbols/{ticker}/options`` endpoints.

Design invariants (identical to the rest of the Pilots read layer):

* **Read-only / persisted-state only** — imports only ``settings`` + stdlib.
  NEVER imports ``technical_options_engine`` (AST-forbidden on the API path); it
  reads the artifact the pipeline already persisted.
* **Honesty (CONSTRAINT #4)** — a missing artifact yields an empty matrix with an
  honest ``reason``; the persisted directives already carry ``null`` (not ``0.0``)
  for uncomputable legs.
* **Never raises (CONSTRAINT #6)** — a missing/corrupt file degrades to empty.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from settings import settings

logger = logging.getLogger(__name__)

__all__ = ["load_options_matrix", "options_matrix", "symbol_options"]

_FILENAME = "options_matrix.json"
_DISABLED_REASON = (
    "Options matrix not generated yet — enable OPTIONS_MATRIX_ENABLED and run the pipeline."
)


def _default_path() -> Path:
    return settings.OUTPUT_DIR / _FILENAME


def load_options_matrix(path: Optional[str] = None) -> Optional[dict]:
    """Load the persisted options-matrix JSON, or ``None`` (never raises)."""
    p = Path(path) if path else _default_path()
    try:
        if not p.exists():
            return None
        obj = json.loads(p.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("load_options_matrix failed: %s", exc)
        return None


def options_matrix(path: Optional[str] = None) -> Dict[str, Any]:
    """Return ``{as_of, directives, reason}`` — the full persisted matrix, or an
    honest empty matrix + ``reason`` when the artifact is absent."""
    obj = load_options_matrix(path)
    if obj is None:
        return {"as_of": None, "directives": [], "reason": _DISABLED_REASON}
    directives = obj.get("directives")
    if not isinstance(directives, list):
        directives = []
    return {
        "as_of": obj.get("timestamp"),
        "target_dte": obj.get("target_dte"),
        "vix": obj.get("vix"),
        "market_regime": obj.get("market_regime"),
        "directives": directives,
        "reason": None if directives else _DISABLED_REASON,
    }


def symbol_options(ticker: str, path: Optional[str] = None) -> Optional[dict]:
    """Return the single persisted directive for ``ticker`` (case-insensitive),
    or ``None`` when the matrix is absent or the symbol is not in it."""
    target = str(ticker or "").upper().strip()
    if not target:
        return None
    obj = load_options_matrix(path)
    if obj is None:
        return None
    directives = obj.get("directives")
    if not isinstance(directives, list):
        return None
    for row in directives:
        if isinstance(row, dict) and str(row.get("Symbol") or "").upper().strip() == target:
            return row
    return None
