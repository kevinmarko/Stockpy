"""pilots/alerts_feed.py — read-only tail of the alert JSONL for the PWA.
=======================================================================

Surfaces the structured alert log written by ``observability/alerts.py``'s
``file`` channel (JSON-lines at ``settings.ALERT_FILE_PATH``) for the mobile
"Activity" tab (``GET /alerts``).

Design invariants (identical to the rest of the Pilots read layer):

* **Read-only, no write-path import** — this tails a JSONL file. It deliberately
  does **not** import ``observability/alerts.py`` (that is the *dispatch* path and
  pulls ``smtplib``/``ssl``); it only reads ``settings.ALERT_FILE_PATH``. The
  ~20-line tail logic mirrors ``gui/panels/analytics.py::_read_alert_tail``.
* **Honesty (CONSTRAINT #4)** — when ``ALERT_FILE_PATH`` is unset (its default) or
  the file does not exist yet, the feed is honestly ``[]`` with a ``reason``, not a
  fabricated entry.
* **Never raises (CONSTRAINT #6)** — a malformed line is skipped; an unreadable
  file degrades to an empty feed.

Record shape (per ``observability/alerts.py``'s file channel): ``timestamp``
(ISO-8601 UTC str), ``level`` (``"INFO"|"WARNING"|"CRITICAL"``), ``message``
(str), plus any flattened ``extra`` keys.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from settings import settings

logger = logging.getLogger(__name__)

__all__ = ["alerts_feed"]

_DEFAULT_LIMIT = 50
_KNOWN_LEVELS = {"INFO", "WARNING", "CRITICAL", "ERROR", "DEBUG"}


def _read_alert_tail(path: Path, max_lines: int) -> List[Dict[str, Any]]:
    """Read + JSON-parse the last ``max_lines`` lines of the JSONL alert file.

    Malformed lines are skipped (never raises). Returns newest-first. Mirrors
    ``gui/panels/analytics.py::_read_alert_tail``.
    """
    entries: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("alert file read failed: %s", exc)
        return entries
    for line in lines[-max_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                entries.append(_normalize_entry(obj))
        except Exception:  # noqa: BLE001 — skip malformed line
            continue
    entries.reverse()  # newest first
    return entries


def _normalize_entry(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one raw alert dict to the PWA row shape.

    Keeps ``timestamp``/``level``/``message`` first-class (honest ``None`` when
    absent), and folds every OTHER key into an ``extra`` sub-dict so the UI has a
    stable schema regardless of which extra fields a given alert carried.
    """
    reserved = {"timestamp", "level", "message"}
    level = obj.get("level")
    level_str = str(level).upper() if level is not None else None
    extra = {k: v for k, v in obj.items() if k not in reserved}
    return {
        "timestamp": _clean_str(obj.get("timestamp")),
        "level": level_str if level_str in _KNOWN_LEVELS else level_str,
        "message": _clean_str(obj.get("message")),
        "extra": extra or None,
    }


def _clean_str(value: Any) -> Optional[str]:
    """Strip a display string; empty/None → ``None`` (CONSTRAINT #4)."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def alerts_feed(limit: int = _DEFAULT_LIMIT) -> Dict[str, Any]:
    """Return ``{entries, reason}`` — the newest ``limit`` alerts, or an honest
    empty feed with a ``reason`` when the alert file is unconfigured/absent.

    ``reason`` is ``None`` on a normal hit. Never raises (CONSTRAINT #6).
    """
    limit = max(1, int(limit))
    raw_path = getattr(settings, "ALERT_FILE_PATH", None)
    if not raw_path:
        return {
            "entries": [],
            "reason": "Alert file not configured (set ALERT_FILE_PATH to enable).",
        }
    try:
        path = Path(raw_path)
        if not path.exists():
            return {
                "entries": [],
                "reason": "No alerts yet — the file is created on the first dispatch.",
            }
        entries = _read_alert_tail(path, max_lines=limit)
        if not entries:
            return {"entries": [], "reason": "No parseable alert entries yet."}
        return {"entries": entries, "reason": None}
    except Exception as exc:  # noqa: BLE001 — never raise into the API
        logger.debug("alerts_feed failed: %s", exc)
        return {"entries": [], "reason": "Alert feed unavailable."}
