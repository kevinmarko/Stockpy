"""
scripts/backfill_news_history.py
=================================
Backfills ``HistoricalStore``'s ``news_history`` table -- the daily
per-symbol sentiment series the webapp's Sentiment Dynamics screen charts
via ``GET /data/sentiment/{symbol}/history`` -- using REAL historical
Finnhub headlines and REAL historical Finnhub earnings dates, scored with
the exact same FinBERT/lexicon + earnings-proximity methodology
``signals/news_catalyst.py``'s live ``NewsCatalystSignal.pre_compute()``
uses.

Why this is honest, not a hindsight shortcut
---------------------------------------------
``news_history``'s own DDL comment (``data/historical_store.py``) documents
it as "forward-archive only" -- the live pipeline has only been writing to
it since 2026-07, so a fresh install has just weeks of real depth. But
Finnhub's ``company_news`` and ``earnings_calendar`` endpoints both accept
arbitrary historical date ranges and return genuinely real records:
headlines that were actually published on that date, and earnings dates
that were actually scheduled/reported. Scoring that real historical text
with the platform's existing deterministic FinBERT/lexicon classifier is
not fabricating point-in-time data (CONSTRAINT #4) -- it is applying a
fixed function to real historical records, exactly the reasoning
``scripts/backfill_sentiment_history.py``'s module docstring already
documents for its own (separate) ``sentiment_ingestion_audit`` backfill.

Rows written here use ``source="finbert_backfill"`` (not the live
pipeline's ``"finbert"``) so a FUTURE point-in-time backtest built on this
table can filter out backfilled rows if same-day-computed provenance turns
out to matter -- today's display chart doesn't care about provenance and
shows both.

The Finnhub free tier caps real depth at ~3 months
-----------------------------------------------------
``settings.NEWS_LOOKBACK_DAYS``'s own description says it plainly: "the
free Finnhub tier provides ~3 months of history." Requesting further back
than that does not error -- Finnhub just returns nothing for the older
portion of the range. With the default ``--months 6``, expect the most
recent ~3 months to genuinely fill in and the older ~3 months to archive
as honest ``NaN`` gaps (``HistoricalStore.get_news_sentiment_history()``'s
documented contract -- a real "no data" day, never a fabricated 0.0). This
script does not attempt to work around that cap; it reports what Finnhub
actually returned so the operator isn't misled about coverage.

One call per symbol, not per day or per window
-------------------------------------------------
Unlike ``scripts/backfill_sentiment_history.py``'s GDELT windowing (needed
because GDELT's ``artlist`` mode caps at 250 records per 7-day call), this
script issues exactly ONE ``company_news`` call and ONE
``earnings_calendar`` call per symbol -- mirroring
``signals/news_catalyst.py``'s existing ``fetch_company_news``/
``fetch_next_earnings`` helpers and ``data/sentiment_sources.py``'s
``FinnhubSentimentSource``, both of which already fetch an arbitrarily wide
date range in a single call with no chunking. Each historical trading day's
score is then reconstructed LOCALLY from the one already-fetched,
already-scored headline list by replaying
``NewsCatalystSignal.pre_compute()``'s trailing ``NEWS_LOOKBACK_DAYS``-day
window average + earnings-proximity multiplier for that day instead of
"as of now" -- so a 6-month, 33-symbol backfill costs ~66 Finnhub calls
total, not thousands.

Sequential by design
---------------------
Mirrors ``scripts/backfill_sentiment_history.py``'s reasoning: a one-time,
operator-invoked backfill is not a latency-sensitive path, and the shared
FinBERT pipeline / ``finbert_score_cache`` DB session are simplest to reuse
single-threaded rather than made thread-safe for a use case that doesn't
need it.
"""

import argparse
import logging
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Repo-root import shim so `python scripts/backfill_news_history.py` works
# from anywhere -- mirrors scripts/backfill_sentiment_history.py's identical
# shim and its documented rationale.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd  # noqa: E402

from data.historical_store import HistoricalStore  # noqa: E402
from data.portfolio_sync import resolve_universe  # noqa: E402
from settings import settings  # noqa: E402
from signals.news_catalyst import (  # noqa: E402
    _distribution_to_signed,
    _earnings_proximity_multiplier,
    _get_finbert_pipeline,
    build_finnhub_client,
    score_headlines,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Finnhub free-tier courtesy delay between symbols (2 calls/symbol: one
# company_news, one earnings_calendar) -- generous since this is a one-time
# backfill, not a latency-sensitive live cycle.
_COURTESY_DELAY_SECONDS = 1.0


def _fetch_headlines(
    client: Any, symbol: str, start: datetime, end: datetime,
) -> List[Tuple[datetime, str]]:
    """One wide-range ``company_news`` call. Returns ``[(as_of, headline)]``,
    real Finnhub records only -- never raises (CONSTRAINT #6)."""
    try:
        result = client.company_news(
            symbol, _from=start.strftime("%Y-%m-%d"), to=end.strftime("%Y-%m-%d"),
        )
    except Exception as exc:
        logger.warning("%s: company_news fetch failed: %s", symbol, exc)
        return []
    items = result if isinstance(result, list) else []
    out: List[Tuple[datetime, str]] = []
    for item in items:
        headline = item.get("headline", "")
        ts = item.get("datetime")
        if not headline or not ts:
            continue
        try:
            as_of = datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            continue
        out.append((as_of, headline))
    return out


def _fetch_earnings_dates(
    client: Any, symbol: str, start: datetime, end: datetime,
) -> List[datetime]:
    """One wide-range ``earnings_calendar`` call. Returns sorted real
    scheduled/reported earnings dates -- never raises (CONSTRAINT #6)."""
    try:
        data = client.earnings_calendar(
            _from=start.strftime("%Y-%m-%d"), to=end.strftime("%Y-%m-%d"), symbol=symbol,
        ) or {}
    except Exception as exc:
        logger.warning("%s: earnings_calendar fetch failed: %s", symbol, exc)
        return []
    entries = data.get("earningsCalendar", [])
    dates: List[datetime] = []
    for entry in entries:
        date_str = entry.get("date", "")
        if not date_str:
            continue
        try:
            dates.append(datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc))
        except ValueError:
            continue
    return sorted(dates)


def _next_earnings_on(day: datetime, earnings_dates: List[datetime]) -> Optional[datetime]:
    """Earliest earnings date >= ``day`` - 24h, mirroring
    ``fetch_next_earnings``'s own 24h look-back grace window (it keeps an
    earnings date that fell within the last 24h so the post-earnings dampen
    band in ``_earnings_proximity_multiplier`` still engages)."""
    cutoff = day - timedelta(hours=24)
    future = [d for d in earnings_dates if d >= cutoff]
    return min(future) if future else None


def _backfill_symbol(
    symbol: str,
    client: Any,
    pipeline: Optional[Any],
    start_date: datetime,
    end_date: datetime,
    lookback_days: int,
    suppress_hours: float,
    dampen_days: float,
) -> Tuple[Dict[str, float], int]:
    """Backfill one symbol. Returns ``({"YYYY-MM-DD": score, ...}, n_real_headlines)``.

    ``score`` is ``NaN`` for a trading day whose trailing lookback window
    contained zero real headlines -- an honest "no data" gap, matching
    ``NewsCatalystSignal.pre_compute()``'s own archive-vs-live split
    (CONSTRAINT #4).
    """
    fetch_start = start_date - timedelta(days=lookback_days)
    headlines_raw = _fetch_headlines(client, symbol, fetch_start, end_date)
    earnings_dates = _fetch_earnings_dates(
        client, symbol, fetch_start, end_date + timedelta(days=30),
    )

    texts = [h for _, h in headlines_raw]
    distributions = score_headlines(texts, pipeline=pipeline)
    scored = [
        (as_of, _distribution_to_signed(dist))
        for (as_of, _), dist in zip(headlines_raw, distributions)
    ]

    day_scores: Dict[str, float] = {}
    for day_ts in pd.bdate_range(start_date, end_date):
        day = day_ts.to_pydatetime().replace(tzinfo=timezone.utc)
        window_start = day - timedelta(days=lookback_days)
        window_scores = [s for (as_of, s) in scored if window_start <= as_of <= day]
        day_str = day_ts.strftime("%Y-%m-%d")
        if not window_scores:
            day_scores[day_str] = float("nan")
            continue
        raw = sum(window_scores) / len(window_scores)
        next_earnings = _next_earnings_on(day, earnings_dates)
        multiplier = _earnings_proximity_multiplier(
            next_earnings, day, suppress_hours, dampen_days,
        )
        day_scores[day_str] = max(-1.0, min(1.0, raw * multiplier))

    return day_scores, len(headlines_raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tickers", default="all",
        help="Comma-separated tickers, or 'all' for the operator's tracked "
             "universe (held ∪ watchlists ∪ DEFAULT_TICKERS).",
    )
    parser.add_argument(
        "--months", type=float, default=6.0,
        help="How many months back to backfill (default: 6). See module "
             "docstring: Finnhub's free tier only has ~3 months of real "
             "depth -- the remainder archives as honest NaN gaps.",
    )
    args = parser.parse_args()

    tickers = resolve_universe(args.tickers)
    if not tickers:
        logger.error(
            "No tickers to process: --tickers=%r resolved to an empty universe. "
            "Pass explicit tickers, or configure DEFAULT_TICKERS / a Robinhood "
            "snapshot / watchlist files (SYNC_WATCHLIST_FILES).",
            args.tickers,
        )
        return
    logger.info("Resolved %d tickers from --tickers=%r", len(tickers), args.tickers)

    client = build_finnhub_client()
    if client is None:
        logger.error(
            "FINNHUB_API_KEY is not set in settings (or finnhub-python is not "
            "installed) -- nothing to backfill. Configure FINNHUB_API_KEY in "
            ".env and retry."
        )
        return

    if not settings.NEWS_HISTORY_CAPTURE_ENABLED:
        logger.warning(
            "settings.NEWS_HISTORY_CAPTURE_ENABLED is False. That flag only "
            "gates the LIVE pipeline's forward-going writes -- this backfill "
            "will still run and write real historical rows -- but the live "
            "pipeline will not keep extending this table going forward until "
            "the flag is set True."
        )

    pipeline = _get_finbert_pipeline() if settings.FINBERT_ENABLED else None

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=int(args.months * 30))
    lookback_days = int(settings.NEWS_LOOKBACK_DAYS)
    suppress_hours = float(settings.NEWS_EARNINGS_SUPPRESS_HOURS)
    dampen_days = float(settings.NEWS_EARNINGS_DAMPEN_DAYS)

    logger.info(
        "Backfilling news_history: %d tickers, %.1f months (%s to %s), "
        "lookback=%dd, FinBERT=%s.",
        len(tickers), args.months, start_date.date(), end_date.date(),
        lookback_days, pipeline is not None,
    )

    # Accumulate as day -> {symbol: score} so writes go through
    # save_news_sentiment() once PER DAY across all symbols (its natural
    # per-cycle shape), not once per (symbol, day) pair.
    by_day: Dict[str, Dict[str, float]] = {}
    total_headlines = 0
    n_errors = 0
    for symbol in tickers:
        try:
            day_scores, n_headlines = _backfill_symbol(
                symbol, client, pipeline, start_date, end_date,
                lookback_days, suppress_hours, dampen_days,
            )
            total_headlines += n_headlines
            for day_str, score in day_scores.items():
                by_day.setdefault(day_str, {})[symbol] = score
            logger.info(
                "%s: %d trading days scored (%d real headlines fetched).",
                symbol, len(day_scores), n_headlines,
            )
        except Exception as exc:
            logger.error("Backfill failed for %s: %s", symbol, exc, exc_info=True)
            n_errors += 1
        time.sleep(_COURTESY_DELAY_SECONDS)

    store = HistoricalStore()
    for day_str, scores in sorted(by_day.items()):
        as_of = datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        store.save_news_sentiment(scores, as_of, source="finbert_backfill")

    n_real = sum(
        1 for scores in by_day.values() for v in scores.values()
        if not (isinstance(v, float) and math.isnan(v))
    )
    n_total = sum(len(scores) for scores in by_day.values())
    logger.info(
        "Backfill complete: %d tickers, %d trading days written, "
        "%d/%d symbol-days have a real score (rest are honest NaN gaps -- "
        "see module docstring on Finnhub's ~3-month free-tier depth), "
        "%d real headlines fetched total, %d symbols errored.",
        len(tickers), len(by_day), n_real, n_total, total_headlines, n_errors,
    )


if __name__ == "__main__":
    main()
