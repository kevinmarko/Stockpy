"""
llm/cache.py — JSON-file day-bucketed cache for LLM commentary results.
========================================================================

The on-demand cadence picked in the plan means cache writes are tiny —
on-demand only, a few dozen calls/day max.  A flat JSON file at
``output/llm_commentary_cache.json`` (gitignored) is enough; SQLite would
be overkill, and JSON survives manual deletion without a schema migration.

Key derivation (kept here so tests can pin it):

    sha256(provider + schema_name + symbol + iso_date_utc + score_bucket + action)

* ``provider`` — ``"claude"`` / ``"gemini"`` so a re-routed provider invalidates.
* ``schema_name`` — class name of the pydantic schema; bumping the schema invalidates.
* ``symbol`` — uppercase ticker.
* ``iso_date_utc`` — UTC calendar date (``YYYY-MM-DD``); the cache is good for
  the rest of the UTC trading day.
* ``score_bucket`` — ``floor(score / 5.0)`` so small numeric jitter doesn't
  invalidate but a meaningful change (e.g. 47 → 52) does.
* ``action`` — ``BUY`` / ``HOLD`` / ``SELL``.

Atomic write via temp-file rename so a crash mid-write never leaves a
corrupt JSON.  Read failures (missing file, corrupt JSON) degrade silently
to an empty dict (CONSTRAINT #6).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from settings import settings

logger = logging.getLogger(__name__)


def _utc_today_iso() -> str:
    """Return today's UTC date as ``YYYY-MM-DD`` (deterministic key bucket)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def make_cache_key(
    *,
    provider: str,
    schema_name: str,
    symbol: str,
    score: float,
    action: str,
    date_iso: Optional[str] = None,
    variant: str = "",
) -> str:
    """Build the canonical cache key — pure function, used by tests.

    ``variant`` is an optional extra discriminator appended to the key ONLY
    when non-empty, so callers that don't pass it get a byte-identical key to
    the pre-variant format (backward compatible — existing cache entries and
    tests are unaffected).  Callers use it to segregate otherwise-identical
    requests whose PROMPT differs on a dimension the base key doesn't capture
    — notably a Claude rationale generated WITH an Opal research brief in
    context vs. one generated without (Tier 9 Scope 4): the brief changes the
    user prompt but none of provider/schema/symbol/date/score/action, so
    without a variant the brief-augmented call would silently hit a
    brief-less cached entry (or vice-versa).
    """
    try:
        bucket = int(math.floor(float(score) / 5.0))
    except Exception:
        bucket = 0
    fields = [
        provider,
        schema_name,
        (symbol or "").upper(),
        date_iso or _utc_today_iso(),
        str(bucket),
        (action or "").upper(),
    ]
    if variant:
        fields.append(str(variant))
    parts = "|".join(fields)
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


def _cache_path() -> Path:
    raw = getattr(settings, "LLM_COMMENTARY_CACHE_PATH", None) or "output/llm_commentary_cache.json"
    return Path(raw)


def _read_all() -> Dict[str, Any]:
    path = _cache_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception as exc:
        logger.warning("LLM commentary cache read failed (%s) — treating as empty.", exc)
        return {}


def _write_all(data: Dict[str, Any]) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.warning("LLM commentary cache parent mkdir failed: %s", exc)
        return
    # Atomic write — temp file in the same directory then rename.
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".llm_cache.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except Exception as exc:
        logger.warning("LLM commentary cache write failed: %s", exc)


def cache_get(key: str) -> Optional[Dict[str, Any]]:
    """Return the cached payload dict for ``key`` or ``None`` on miss."""
    data = _read_all()
    entry = data.get(key)
    if not isinstance(entry, dict):
        return None
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return None
    return payload


def cache_put(key: str, payload: Dict[str, Any], *, meta: Optional[Dict[str, Any]] = None) -> None:
    """Store ``payload`` under ``key`` with optional ``meta`` (provider, etc.)."""
    data = _read_all()
    data[key] = {
        "payload": payload,
        "meta": meta or {},
        "stored_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_all(data)


def cache_clear() -> None:
    """Delete the cache file (test-only convenience)."""
    path = _cache_path()
    try:
        if path.exists():
            path.unlink()
    except Exception as exc:
        logger.warning("LLM commentary cache clear failed: %s", exc)
