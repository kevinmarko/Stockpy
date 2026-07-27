"""
data/sector_selection_heat.py — Semantic Related Sector Selection: Sector
Heat Factor
============================================================================
Computes the spec-faithful Sector Heat Factor (SHF) for the semantic
Related Sector Selection feature:

    SHF = a * exp(-(x - b)^2 / (2 * c^2))

where ``x`` is the min-max-normalized (numNews + Review) combined document
volume for a sector, normalized ACROSS the candidate sector set, over a
trailing 22-TRADING-day lookback. Empirically calibrated defaults
``a=0.8, b=1.0, c=0.6`` (``settings.SECTOR_SELECTION_HEAT_*``).

Two features, one name
-----------------------
This is a DIFFERENT feature from ``data.sentiment_sources.
compute_sector_heat_factors`` (the ``Sector_Heat_Factor`` dashboard column,
GDELT article-volume Gaussian-*smoothing*, 7 calendar days, unbounded
range). That feature answers "how much GDELT news volume is a sector
seeing right now" as a standalone attention signal. This feature answers
"how should this sector's document volume scale a semantic-similarity
ranking coefficient" -- a Gaussian *response* to a bounded, cross-sector-
normalized input, feeding ``sector_selection_engine.py``'s
``correlation_coefficient = cosine_similarity * SHF``. See
``docs/signals/sector_heat_factor.md``'s "Two features, one name" section
and ``docs/signals/sector_selection.md`` for the full disambiguation.

Data source: reuses ``sentiment_ingestion_audit`` via
``HistoricalStore.get_sentiment_daily_by_source_class`` (Sentiment Source
Class Phase 0) -- NOT a new ingestion source, NOT a new table.

Honest degradation (CONSTRAINT #4) -- the Review term
-------------------------------------------------------
The comment/investor-forum ("Review") volume term has no genuinely active
data source in this repository as of this feature's introduction (Reddit
credentials are typically unset; StockTwits doesn't exist yet -- see
Sentiment Source Class Phase 4/5). ``HistoricalStore.
get_sentiment_daily_by_source_class`` honestly zero-fills a class's count
for a day that WAS observed via the other class -- but that zero-fill
assumption only holds if the comment channel was actually capable of
producing data that day. This module checks whether the comment channel
has EVER produced a single document, via ``get_sentiment_archive_depth_by_
source`` (the method ``HistoricalStore``'s own docs designate for
distinguishing "off" from "quiet" -- see that method's docstring). If it
never has, review volume is treated as unavailable (not a trusted zero)
for every sector, the SHF input degrades to news-only volume, and every
sector's result carries ``degraded_reason="review_unavailable"``.

A sector whose member tickers were never ingested at all (independent of
the review question) gets ``news_volume=NaN``, ``review_volume=NaN``,
``shf=NaN``, and its own ``degraded_reason="no_volume_observed"`` -- it is
excluded from the cross-sector min-max normalization entirely (an unknown
quantity cannot be compared against known ones) rather than silently
folded in as a zero.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from data.sentiment_source_class import classify_source

_UNRANKABLE_REASON_NO_VOLUME = "no_volume_observed"
_UNRANKABLE_REASON_REVIEW_UNAVAILABLE = "review_unavailable"


def compute_spec_sector_heat(
    sectors: List[str],
    *,
    ticker_sector_map: Dict[str, str],
    as_of: Optional[datetime] = None,
    historical_store: Optional[Any] = None,
) -> Dict[str, Dict[str, Any]]:
    """Compute the spec-faithful Sector Heat Factor for each sector in
    ``sectors``.

    Parameters
    ----------
    sectors : candidate sector names to score (deduplicated, "" / "unknown"
        dropped -- matches ``compute_sector_heat_factors``' own filtering
        convention).
    ticker_sector_map : ``{symbol: sector}`` membership, e.g. loaded from
        ``forecasting/data/ticker_sectors.csv`` by the caller. Required --
        this module does no membership loading of its own.
    as_of : reference instant; defaults to ``datetime.now(timezone.utc)``.
        Resolved to a trading day via ``HistoricalStore.resolve_trading_day``
        (the same post-close roll used for document attribution), so a
        post-market-close run scores the NEXT trading session, matching
        the daily-engine cadence this feature is built for.
    historical_store : injected for testability; lazily constructed
        (``data.historical_store.HistoricalStore()``) when omitted.

    Returns ``{}`` immediately, with no DB read, when
    ``settings.SECTOR_SELECTION_ENABLED`` is False or ``sectors``/
    ``ticker_sector_map`` is empty -- mirrors ``compute_sector_heat_factors``'
    own gating convention. Never raises (CONSTRAINT #6); a per-sector
    aggregation failure degrades that sector to the unrankable/NaN shape
    rather than aborting the whole call.

    Each value is
    ``{"news_volume": float, "review_volume": float, "shf": float,
    "degraded_reason": str | None}``. ``news_volume``/``review_volume`` are
    ``NaN`` only when the sector was never observed at all; ``shf`` is
    ``NaN`` whenever it cannot be honestly computed (no observed volume, or
    it fell outside the valid cross-sector normalization set).
    """
    from settings import settings

    if not settings.SECTOR_SELECTION_ENABLED or not sectors or not ticker_sector_map:
        return {}

    distinct_sectors = sorted({
        str(s).strip() for s in sectors
        if s and str(s).strip() and str(s).strip().lower() != "unknown"
    })
    if not distinct_sectors:
        return {}

    if historical_store is None:
        from data.historical_store import HistoricalStore
        historical_store = HistoricalStore()

    try:
        _now = as_of or datetime.now(timezone.utc)
        end_day = historical_store.resolve_trading_day(_now)
        lookback = max(1, int(settings.SECTOR_SELECTION_HEAT_LOOKBACK_DAYS))
        window_days = _trailing_trading_days(end_day, lookback)
        start_day = window_days[0]

        member_symbols_by_sector: Dict[str, List[str]] = {sector: [] for sector in distinct_sectors}
        for symbol, sector in ticker_sector_map.items():
            sector_norm = str(sector).strip()
            if sector_norm in member_symbols_by_sector:
                member_symbols_by_sector[sector_norm].append(str(symbol).upper())

        all_symbols = sorted({
            sym for members in member_symbols_by_sector.values() for sym in members
        })
        if not all_symbols:
            return {
                sector: _unrankable_entry(_UNRANKABLE_REASON_NO_VOLUME)
                for sector in distinct_sectors
            }

        daily = historical_store.get_sentiment_daily_by_source_class(
            all_symbols, start_day, end_day
        )
        review_ever_observed = _review_channel_ever_observed(historical_store)

        news_volume: Dict[str, float] = {}
        review_volume: Dict[str, float] = {}
        for sector, members in member_symbols_by_sector.items():
            total_news = 0.0
            total_review = 0.0
            observed_any = False
            for symbol in members:
                for by_day in daily.get(symbol, {}).values():
                    observed_any = True
                    total_news += float(by_day["news_count"])
                    total_review += float(by_day["comment_count"])
            if not observed_any:
                news_volume[sector] = float("nan")
                review_volume[sector] = float("nan")
            else:
                news_volume[sector] = total_news
                review_volume[sector] = (
                    total_review if review_ever_observed else float("nan")
                )

        return _finalize(
            distinct_sectors, news_volume, review_volume, review_ever_observed, settings,
        )
    except Exception:
        return {
            sector: _unrankable_entry(_UNRANKABLE_REASON_NO_VOLUME)
            for sector in distinct_sectors
        }


def _finalize(
    distinct_sectors: List[str],
    news_volume: Dict[str, float],
    review_volume: Dict[str, float],
    review_ever_observed: bool,
    settings: Any,
) -> Dict[str, Dict[str, Any]]:
    combined: Dict[str, float] = {}
    for sector in distinct_sectors:
        n, r = news_volume[sector], review_volume[sector]
        if math.isnan(n):
            continue  # no volume observed at all -- excluded from normalization
        combined[sector] = n if math.isnan(r) else n + r

    result: Dict[str, Dict[str, Any]] = {}
    if not combined:
        return {sector: _unrankable_entry(_UNRANKABLE_REASON_NO_VOLUME) for sector in distinct_sectors}

    values = np.asarray(list(combined.values()), dtype=float)
    lo, hi = float(values.min()), float(values.max())
    a = float(settings.SECTOR_SELECTION_HEAT_A)
    b = float(settings.SECTOR_SELECTION_HEAT_B)
    c = float(settings.SECTOR_SELECTION_HEAT_C)

    for sector in distinct_sectors:
        if sector not in combined:
            result[sector] = _unrankable_entry(_UNRANKABLE_REASON_NO_VOLUME)
            continue
        x = 0.5 if hi == lo else (combined[sector] - lo) / (hi - lo)
        shf = a * math.exp(-((x - b) ** 2) / (2 * c * c))
        degraded_reason = None if review_ever_observed else _UNRANKABLE_REASON_REVIEW_UNAVAILABLE
        result[sector] = {
            "news_volume": news_volume[sector],
            "review_volume": review_volume[sector],
            "shf": shf,
            "degraded_reason": degraded_reason,
        }
    return result


def _unrankable_entry(reason: str) -> Dict[str, Any]:
    return {
        "news_volume": float("nan"),
        "review_volume": float("nan"),
        "shf": float("nan"),
        "degraded_reason": reason,
    }


def _review_channel_ever_observed(historical_store: Any) -> bool:
    """Whether ANY comment-classified source has ever produced a single
    ``sentiment_ingestion_audit`` row, per ``get_sentiment_archive_depth_
    by_source`` (the method designated for distinguishing "off" from
    "quiet" -- see ``HistoricalStore.get_sentiment_daily_by_source_class``'s
    own docstring). This is a global, all-time check, not scoped to the
    current lookback window -- a channel with real historical depth that
    happens to be quiet THIS window still yields a genuine zero from the
    per-window read; only a channel that has NEVER produced anything is
    untrustworthy as a "zero."
    """
    try:
        depth = historical_store.get_sentiment_archive_depth_by_source()
    except Exception:
        return False
    for source_name, info in depth.items():
        if classify_source(source_name) == "comment" and int(info.get("document_count") or 0) > 0:
            return True
    return False


def _trailing_trading_days(end_day: str, n: int) -> List[str]:
    """Return ``n`` trading-day labels (``YYYY-MM-DD``, weekdays only,
    ascending) ending at and including ``end_day``. No holiday calendar --
    same documented limitation as ``HistoricalStore.resolve_trading_day``
    and ``engine.advisory_agent.is_us_market_open``."""
    end = datetime.strptime(end_day, "%Y-%m-%d").date()
    days: List[str] = []
    cursor = end
    while len(days) < n:
        if cursor.weekday() < 5:  # Monday=0 .. Friday=4
            days.append(cursor.strftime("%Y-%m-%d"))
        cursor -= timedelta(days=1)
    return list(reversed(days))
