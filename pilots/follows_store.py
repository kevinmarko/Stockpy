"""Atomic JSON persistence of Pilot follows ("user follows Pilot X with $Y").

Local, single-operator persistence backing the marketplace's honest
``aum_proxy`` / ``followers_proxy`` and the follow write-path. There is no
database and no network — one small JSON file at ``output/follows.json`` written
with the same **atomic write-then-rename** idiom used by
``execution/kill_switch.py::GlobalKillSwitch.activate`` and
``data/robinhood_portfolio._write_cache`` so a concurrent reader never sees a
partially-written file.

Schema (``output/follows.json``)::

    {
      "version": 1,
      "follows": [
        {
          "pilot_id": "trend-following",
          "amount": 500.0,
          "created_at": "2026-07-12T00:00:00+00:00",
          "updated_at": "2026-07-12T00:00:00+00:00",
          "status": "active"        # "active" | "cancelled"
        },
        ...
      ]
    }

Design constraints:

* **Dependency-light** — stdlib + ``settings`` only. Safe to import on the API
  path.
* **Dead-letter resilient** (CONSTRAINT #6) — a missing or corrupt file is
  treated as an empty store on read, never an exception.
* **No fabrication** (CONSTRAINT #4) — a cancelled follow (amount 0) keeps its
  row with ``status="cancelled"`` rather than inventing an amount; the AUM /
  followers proxies count only ``active`` rows.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from settings import settings

logger = logging.getLogger(__name__)

__all__ = ["FollowsStore"]

SCHEMA_VERSION = 1
STATUS_ACTIVE = "active"
STATUS_CANCELLED = "cancelled"


def _utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class FollowsStore:
    """Read/write the local ``follows.json`` store.

    Parameters
    ----------
    path:
        Override the JSON file location (tests pass a ``tmp_path``). ``None`` ->
        ``settings.OUTPUT_DIR / "follows.json"``.
    clock:
        Injectable zero-arg callable returning an ISO timestamp string, for
        deterministic tests. Defaults to :func:`_utc_now_iso`.
    """

    def __init__(
        self,
        path: Optional[str] = None,
        clock: Optional[Callable[[], str]] = None,
    ) -> None:
        if path is not None:
            self._path = Path(path)
        else:
            self._path = settings.OUTPUT_DIR / "follows.json"
        self._clock: Callable[[], str] = clock or _utc_now_iso

    # ------------------------------------------------------------------
    # Low-level (de)serialization
    # ------------------------------------------------------------------
    def _load(self) -> List[Dict[str, Any]]:
        """Return the raw follows list; empty on missing/corrupt file (never raises)."""
        if not self._path.exists():
            return []
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            logger.warning("FollowsStore: corrupt/unreadable %s treated as empty: %s", self._path, exc)
            return []
        if not isinstance(data, dict):
            logger.warning("FollowsStore: %s is not a JSON object; treated as empty", self._path)
            return []
        follows = data.get("follows", [])
        if not isinstance(follows, list):
            return []
        # Defensive: keep only well-formed dict rows carrying a pilot_id.
        return [f for f in follows if isinstance(f, dict) and f.get("pilot_id")]

    def _save(self, follows: List[Dict[str, Any]]) -> None:
        """Atomically persist *follows* via write-then-rename."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": SCHEMA_VERSION, "follows": follows}
        tmp = self._path.with_suffix(".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            tmp.replace(self._path)
        except Exception as exc:  # noqa: BLE001 - clean up temp on any failure
            logger.warning("FollowsStore: failed to write %s: %s", self._path, exc)
            tmp.unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------
    def list_all(self) -> List[Dict[str, Any]]:
        """Return every follow row (active and cancelled)."""
        return self._load()

    def list_active(self) -> List[Dict[str, Any]]:
        """Return only rows with ``status == "active"``."""
        return [f for f in self._load() if f.get("status") == STATUS_ACTIVE]

    def get(self, pilot_id: str) -> Optional[Dict[str, Any]]:
        """Return the follow row for *pilot_id*, or ``None`` if not present."""
        for f in self._load():
            if f.get("pilot_id") == pilot_id:
                return f
        return None

    # ------------------------------------------------------------------
    # Write API
    # ------------------------------------------------------------------
    def upsert(self, pilot_id: str, amount: float) -> Dict[str, Any]:
        """Create or update the follow for *pilot_id*.

        ``amount > 0`` -> status ``"active"``; ``amount == 0`` -> status
        ``"cancelled"`` (the row is retained, not deleted). ``created_at`` is
        stamped once on first insert; ``updated_at`` on every write. Atomic.

        Returns the resulting follow row.
        """
        if not pilot_id:
            raise ValueError("pilot_id must be a non-empty string")
        amount = float(amount)
        if amount < 0:
            raise ValueError("amount must be >= 0")

        now = self._clock()
        status = STATUS_ACTIVE if amount > 0 else STATUS_CANCELLED

        follows = self._load()
        for f in follows:
            if f.get("pilot_id") == pilot_id:
                f["amount"] = amount
                f["status"] = status
                f["updated_at"] = now
                # created_at preserved; backfill if a legacy row lacks it.
                f.setdefault("created_at", now)
                self._save(follows)
                return dict(f)

        row: Dict[str, Any] = {
            "pilot_id": pilot_id,
            "amount": amount,
            "created_at": now,
            "updated_at": now,
            "status": status,
        }
        follows.append(row)
        self._save(follows)
        return dict(row)

    def remove(self, pilot_id: str) -> bool:
        """Delete the follow row for *pilot_id* entirely.

        Returns ``True`` if a row was removed, ``False`` if none existed.
        Atomic.
        """
        follows = self._load()
        kept = [f for f in follows if f.get("pilot_id") != pilot_id]
        if len(kept) == len(follows):
            return False
        self._save(kept)
        return True

    # ------------------------------------------------------------------
    # Marketplace proxies (honest, derived only from active follows)
    # ------------------------------------------------------------------
    def aum_proxy(self) -> float:
        """Sum of all active follow amounts."""
        return float(sum(f.get("amount", 0.0) for f in self.list_active()))

    def followers_proxy(self) -> int:
        """Count of distinct pilots with at least one active follow."""
        return len({f.get("pilot_id") for f in self.list_active()})

    def aum_for(self, pilot_id: str) -> float:
        """Sum of active follow amounts for a single Pilot."""
        return float(
            sum(f.get("amount", 0.0) for f in self.list_active() if f.get("pilot_id") == pilot_id)
        )

    def followers_for(self, pilot_id: str) -> int:
        """Count of active follows for a single Pilot (0 or 1 in v1 single-operator)."""
        return sum(1 for f in self.list_active() if f.get("pilot_id") == pilot_id)
