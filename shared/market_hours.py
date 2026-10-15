"""US market-hours helpers shared by the orchestrator daemon, the pipeline and preflight.

Moved verbatim from ``engine/advisory_agent.py`` in shrink step 5.5, when that module's
``--agent`` loop was archived to ``legacy/`` with ``main.py`` (operator decision 4).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Timezone constants (mirror execution/risk_gate.py)
_ET = ZoneInfo("America/New_York")
_RTH_OPEN_HM = (9, 30)
_RTH_CLOSE_HM = (16, 0)

# ---------------------------------------------------------------------------
# Market-hours detection
# ---------------------------------------------------------------------------

def is_us_market_open(now_utc: datetime) -> bool:
    """Return True iff `now_utc` falls within NYSE regular trading hours.

    RTH = 09:30 – 16:00 America/New_York, Monday-Friday.  Holiday calendar
    is NOT applied (would require pandas_market_calendars or an external
    feed); the operator should set `enforce_market_hours=False` if running
    on a half-day or holiday.

    Parameters
    ----------
    now_utc :
        Timezone-aware UTC datetime.  Naive datetimes are promoted to UTC.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_et = now_utc.astimezone(_ET)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    open_t = now_et.replace(
        hour=_RTH_OPEN_HM[0], minute=_RTH_OPEN_HM[1], second=0, microsecond=0,
    )
    close_t = now_et.replace(
        hour=_RTH_CLOSE_HM[0], minute=_RTH_CLOSE_HM[1], second=0, microsecond=0,
    )
    return open_t <= now_et <= close_t


_MARKET_OPEN_CACHE: Dict[str, Any] = {"at": None, "value": None}
_MARKET_OPEN_CACHE_SECONDS = 60.0


def _fmp_market_is_open() -> Optional[bool]:
    """FMP's live ``isMarketOpen`` for NASDAQ, or None when unavailable."""
    try:
        from data import fmp_client

        payload = fmp_client.exchange_market_hours("NASDAQ")
        row = payload[0] if isinstance(payload, list) and payload else payload
        value = row.get("isMarketOpen") if isinstance(row, dict) else None
        return value if isinstance(value, bool) else None
    except Exception as exc:  # noqa: BLE001 -- fall back to the local calendar
        logger.debug("FMP market-hours lookup failed: %s", exc)
        return None


def is_us_market_open_now(now_utc: datetime) -> bool:
    """Holiday- and early-close-aware "is the US market open right now".

    Primary source: FMP ``exchange-market-hours`` ``isMarketOpen`` (covers
    holidays and early closes), cached for 60 s. Fallback when FMP is
    unavailable: :func:`is_us_market_open` (weekday 09:30-16:00 ET) AND today
    is not an NYSE holiday (``holidays.NYSE``). Disclosed gap: the fallback
    has no early-close data, so on a half day it reports open until 16:00.
    Use this for anything that SUBMITS orders; the plain clock check remains
    for display/cadence callers.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    cached_at = _MARKET_OPEN_CACHE.get("at")
    if cached_at is not None and abs((now_utc - cached_at).total_seconds()) < _MARKET_OPEN_CACHE_SECONDS:
        return bool(_MARKET_OPEN_CACHE["value"])

    value = _fmp_market_is_open()
    if value is None:
        value = is_us_market_open(now_utc)
        if value:
            try:
                import holidays

                if now_utc.astimezone(_ET).date() in holidays.NYSE():
                    value = False
            except Exception as exc:  # noqa: BLE001 -- no calendar: fail closed
                logger.warning("NYSE holiday calendar unavailable (%s); treating market as closed.", exc)
                value = False
    _MARKET_OPEN_CACHE["at"] = now_utc
    _MARKET_OPEN_CACHE["value"] = value
    return value


def reset_market_open_cache() -> None:
    """Clear the 60 s market-open cache (tests)."""
    _MARKET_OPEN_CACHE["at"] = None
    _MARKET_OPEN_CACHE["value"] = None


def is_extended_hours(now_utc: datetime) -> bool:
    """Return True iff `now_utc` is inside the 4 AM – 8 PM ET weekday window.

    Used to distinguish "premarket / aftermarket" cadence from
    "overnight / weekend" cadence.  RTH is a strict subset of extended hours.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_et = now_utc.astimezone(_ET)
    if now_et.weekday() >= 5:
        return False
    return 4 <= now_et.hour < 20


def is_automatic_run_gated(now_utc: datetime, *, extended_hours_only: bool) -> bool:
    """True when an automatic-trigger cycle (the daemon timer) should be
    skipped: the gate is enabled AND `now_utc` falls outside the extended-hours
    window. Single source of truth for the
    `settings.ORCHESTRATOR_EXTENDED_HOURS_ONLY and not is_extended_hours(now_utc)`
    expression. Never gates manual/API-triggered runs -- those call sites simply
    never call this function."""
    return bool(extended_hours_only) and not is_extended_hours(now_utc)
