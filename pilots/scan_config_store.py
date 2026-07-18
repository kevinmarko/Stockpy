"""Atomic JSON persistence of operator-defined Robinhood broker scan configs.

Backs the Agentic Trading tab's Discovery section: an operator defines named
scans (e.g. "high_momentum_breakout" with a filter set) here; the
``agentic-discovery`` Claude Code skill (the only actor that can reach the
Robinhood MCP's ``create_scan``/``run_scan`` tools — see that skill's
docstring) reads this store, runs the configured scans, and writes discovered
candidates to ``output/scan_candidates.json`` (read by :mod:`pilots.discovery`).

Deliberately a DEDICATED JSON file, not an ``.env`` key: scan configs are
structured, operator-editable, multi-row data (like Pilot follows), not a
global tunable — mirrors :class:`pilots.follows_store.FollowsStore` exactly,
including the atomic write-then-rename idiom so a concurrent reader (the
discovery skill, mid-scan) never sees a partially-written file.

Schema (``output/scan_configs.json``)::

    {
      "version": 1,
      "scan_configs": [
        {
          "name": "high_momentum_breakout",
          "filters": {"min_price": 5, "min_volume": 1000000, "rsi_min": 50, "rsi_max": 70},
          "enabled": true,
          "created_at": "2026-07-18T00:00:00+00:00",
          "updated_at": "2026-07-18T00:00:00+00:00"
        },
        ...
      ]
    }

Design constraints (identical to ``FollowsStore``):

* **Dependency-light** — stdlib + ``settings`` only. Safe to import on the API
  path.
* **Dead-letter resilient** (CONSTRAINT #6) — a missing or corrupt file is
  treated as an empty store on read, never an exception.
* **No fabrication** (CONSTRAINT #4) — ``filters`` is stored exactly as given;
  nothing here computes or guesses a filter value.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from settings import settings

logger = logging.getLogger(__name__)

__all__ = ["ScanConfigStore"]

SCHEMA_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScanConfigStore:
    """Read/write the local ``scan_configs.json`` store.

    Parameters
    ----------
    path:
        Override the JSON file location (tests pass a ``tmp_path``). ``None``
        -> ``settings.OUTPUT_DIR / "scan_configs.json"``.
    clock:
        Injectable zero-arg callable returning an ISO timestamp string, for
        deterministic tests. Defaults to :func:`_utc_now_iso`.
    """

    def __init__(
        self,
        path: Optional[str] = None,
        clock: Optional[Callable[[], str]] = None,
    ) -> None:
        self._path = Path(path) if path is not None else settings.OUTPUT_DIR / "scan_configs.json"
        self._clock: Callable[[], str] = clock or _utc_now_iso

    def _load(self) -> List[Dict[str, Any]]:
        """Return the raw scan-config list; empty on missing/corrupt file (never raises)."""
        if not self._path.exists():
            return []
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            logger.warning(
                "ScanConfigStore: corrupt/unreadable %s treated as empty: %s", self._path, exc
            )
            return []
        if not isinstance(data, dict):
            logger.warning("ScanConfigStore: %s is not a JSON object; treated as empty", self._path)
            return []
        configs = data.get("scan_configs", [])
        if not isinstance(configs, list):
            return []
        return [c for c in configs if isinstance(c, dict) and c.get("name")]

    def _save(self, configs: List[Dict[str, Any]]) -> None:
        """Atomically persist *configs* via write-then-rename."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": SCHEMA_VERSION, "scan_configs": configs}
        tmp = self._path.with_suffix(".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            tmp.replace(self._path)
        except Exception as exc:  # noqa: BLE001 - clean up temp on any failure
            logger.warning("ScanConfigStore: failed to write %s: %s", self._path, exc)
            tmp.unlink(missing_ok=True)
            raise

    def list_all(self) -> List[Dict[str, Any]]:
        """Return every scan config (enabled and disabled)."""
        return self._load()

    def list_enabled(self) -> List[Dict[str, Any]]:
        """Return only rows with ``enabled == True`` — what the discovery skill runs."""
        return [c for c in self._load() if c.get("enabled")]

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        for c in self._load():
            if c.get("name") == name:
                return c
        return None

    def upsert(self, name: str, filters: Dict[str, Any], enabled: bool = True) -> Dict[str, Any]:
        """Create or replace the scan config for *name*. Atomic.

        ``filters`` is stored verbatim (a dict of scan-parameter values the
        discovery skill passes through to the Robinhood MCP's
        ``create_scan``/``update_scan_filters`` tools) — never validated or
        interpreted here, since this store has no knowledge of the scanner's
        filter schema (``get_scanner_filter_specs`` on the Robinhood MCP is
        the source of truth for that, and only the skill calls it).
        """
        if not name or not str(name).strip():
            raise ValueError("name must be a non-empty string")
        name = str(name).strip()
        now = self._clock()

        configs = self._load()
        for c in configs:
            if c.get("name") == name:
                c["filters"] = dict(filters or {})
                c["enabled"] = bool(enabled)
                c["updated_at"] = now
                c.setdefault("created_at", now)
                self._save(configs)
                return dict(c)

        row: Dict[str, Any] = {
            "name": name,
            "filters": dict(filters or {}),
            "enabled": bool(enabled),
            "created_at": now,
            "updated_at": now,
        }
        configs.append(row)
        self._save(configs)
        return dict(row)

    def remove(self, name: str) -> bool:
        """Delete the scan config for *name* entirely. Returns ``True`` if removed."""
        configs = self._load()
        kept = [c for c in configs if c.get("name") != name]
        if len(kept) == len(configs):
            return False
        self._save(kept)
        return True
