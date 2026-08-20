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

import copy
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from settings import settings

logger = logging.getLogger(__name__)

__all__ = ["ScanConfigStore"]

SCHEMA_VERSION = 1

# Process-wide, path-keyed cache (mtime, size, filtered-configs), guarded by
# _CACHE_LOCK. Deliberately module-level rather than an instance attribute:
# every real caller (api/pilots_api.py, pilots/discovery.py) constructs a
# fresh ScanConfigStore per request, so an instance-level cache would never
# see a second read on the same instance and would buy nothing. Keyed by the
# resolved path string so distinct ScanConfigStore(path=...) instances that
# point at the same file share one cache entry. (mtime, size) rather than
# bare mtime mirrors desktop/daemon_runtime.py's own change-detection idiom
# and narrows (without eliminating) the same-tick-external-edit blind spot a
# bare mtime-equality check would have.
_CACHE_LOCK = threading.Lock()
_CONFIG_CACHE: Dict[str, Tuple[Tuple[float, int], List[Dict[str, Any]]]] = {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_scans() -> List[Dict[str, Any]]:
    """Build the seeded default scan configs, timestamped at call time.

    A plain module-level list would freeze ``created_at``/``updated_at`` at
    process-import time (whenever this module was first imported) rather than
    the moment a fresh store is actually seeded — every operator's first-ever
    scan config would carry a stale, misleading timestamp. Called fresh from
    ``ScanConfigStore._load()``'s seeding branch instead.
    """
    now = _utc_now_iso()
    return [
        {
            "name": "momentum-leaders",
            "filters": {"min_relative_volume": 1.5, "min_price": 5, "min_volume": 1000000},
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "name": "trend-follower",
            "filters": {"price_above_sma200": True, "roc_12m_min": 0.1, "min_price": 5},
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "name": "dip-buyer",
            "filters": {"rsi2_max": 10, "price_above_sma200": True, "min_price": 5},
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "name": "edge-and-volatility",
            "filters": {"iv_rank_min": 50, "min_options_volume": 500},
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "name": "multifactor",
            "filters": {"min_market_cap": 300000000, "roe_min": 0.15, "pe_ratio_max": 20},
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "name": "forecast-aligned",
            "filters": {"analyst_rating_min": 4.0, "min_price": 5, "min_volume": 500000},
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "name": "news-catalyst",
            "filters": {"unusual_volume": True, "social_sentiment_min": 70},
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "name": "risk-adjusted",
            "filters": {"beta_max": 1.0, "max_drawdown_52w": -0.2},
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            # Robinhood's scanner has no dividend-yield or payout-ratio filter type at
            # all (confirmed against a live get_scanner_filter_specs call, 2026-08-20)
            # -- the original {"dividend_yield_min", "payout_ratio_max"} filters here
            # were silently unrunnable since this row was first seeded. This is a
            # sector-tilt PROXY (traditionally high-dividend sectors + an
            # established-company market-cap floor), not a real yield/payout screen --
            # the agentic-discovery skill translates these keys into real Robinhood
            # filter objects at scan time, same as every other row here.
            "name": "dividend-income",
            "filters": {
                "sector": ["Utilities", "Real Estate", "Energy", "Financial Services"],
                "market_cap_min": 1000000000,
            },
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "name": "balanced-blend",
            "filters": {"composite_score_min": 80, "min_price": 10, "min_volume": 1000000},
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        },
    ]


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
        seed_defaults: bool = True,
    ) -> None:
        self._path = Path(path) if path is not None else settings.OUTPUT_DIR / "scan_configs.json"
        self._clock: Callable[[], str] = clock or _utc_now_iso
        self._seed_defaults = seed_defaults

    def _cache_key(self) -> str:
        return str(self._path)

    def _stamp(self) -> Optional[Tuple[float, int]]:
        try:
            st = self._path.stat()
        except OSError:
            return None
        return (st.st_mtime, st.st_size)

    def _load(self) -> List[Dict[str, Any]]:
        """Return the raw scan-config list; empty on missing/corrupt file (never raises)."""
        stamp = self._stamp()
        if stamp is not None:
            with _CACHE_LOCK:
                cached = _CONFIG_CACHE.get(self._cache_key())
            if cached is not None and cached[0] == stamp:
                return copy.deepcopy(cached[1])

        if not self._path.exists():
            if self._seed_defaults:
                # Seed default scans on first run, timestamped now (not at
                # module-import time -- see _default_scans()'s docstring).
                defaults = _default_scans()
                self._save(defaults)
                return list(defaults)
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

        filtered = [c for c in configs if isinstance(c, dict) and c.get("name")]
        if stamp is not None:
            with _CACHE_LOCK:
                _CONFIG_CACHE[self._cache_key()] = (stamp, filtered)
        return copy.deepcopy(filtered)

    def _save(self, configs: List[Dict[str, Any]]) -> None:
        """Atomically persist *configs* via write-then-rename."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": SCHEMA_VERSION, "scan_configs": configs}
        tmp = self._path.with_suffix(".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            tmp.replace(self._path)

            filtered = [c for c in configs if isinstance(c, dict) and c.get("name")]
            stamp = self._stamp()
            with _CACHE_LOCK:
                if stamp is not None:
                    _CONFIG_CACHE[self._cache_key()] = (stamp, filtered)
                else:
                    # Can't stat the file we just wrote -- don't leave a
                    # stale/unstamped entry a later _load() could match.
                    _CONFIG_CACHE.pop(self._cache_key(), None)
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
