"""
scripts/backfill_sentiment_history.py
======================================
CLI backfill script that pulls HISTORICAL sentiment documents from the
sources with genuine point-in-time archives (GDELT, SEC EDGAR, Finnhub,
Reddit) into ``sentiment_ingestion_audit`` -- so the credibility-weighted
sentiment signal's point-in-time archive doesn't have to wait for calendar
time alone before ``settings.SENTIMENT_PIT_MIN_MONTHS`` of real depth exists.

Why this is honest, not a hindsight shortcut
---------------------------------------------
GDELT article tone, EDGAR filing dates, and Finnhub headlines are all
GENUINELY historical records -- GDELT's tone score was computed at or near
publish time (not reconstructed today), a filing's date is permanent fact,
and Finnhub's ``company_news`` API accepts arbitrary historical date ranges.
Backfilling these is not fabricating point-in-time data (CONSTRAINT #4); it
is ingesting real historical records that already existed. All three are
policy-trusted institutional sources (``credibility_weight=1.0`` regardless
of when they're scored -- see ``signals/credibility.py``'s
``_INSTITUTIONAL_SOURCES``), so backfilling them introduces no credibility
bias at all.

Yahoo RSS is EXCLUDED from the default backfill set: it is a live feed with
no historical archive to query at all (see ``YahooRSSSource``'s docstring)
-- passing ``--sources yahoo_rss`` explicitly is harmless but will
contribute zero documents for every symbol, every time.

Reddit carries a real, documented caveat (not hidden): its posts ARE
genuinely searchable historically, but a backfilled post's credibility
sub-scores (``S_authority``, from author follower count) can only ever
reflect the author's CURRENT account state -- Reddit's API has no way to
answer "what was this account's standing 5 months ago." This is included in
the default backfill set anyway (an explicit operator choice), but
``HistoricalStore.get_sentiment_archive_depth_by_source()`` lets a future
validation gate weigh institutional depth separately from Reddit's, rather
than one blended number that would overstate confidence in the weaker
component.

Sequential by design
---------------------
Unlike ``scripts/backfill_edgar_fundamentals.py`` (which parallelizes across
symbols via a ``ThreadPoolExecutor``), this script processes symbols
SEQUENTIALLY. ``CompositeSentimentSource``'s per-cycle document budget /
circuit breaker / wall-clock deadline are plain instance attributes with no
locking (they're designed for a single-threaded per-cycle loop, not
concurrent access) -- sharing one instance across worker threads would race
on that state. A one-time, operator-invoked backfill is not a latency-
sensitive path, so sequential execution is the correct, simpler tradeoff
here rather than adding thread-safety to a component that doesn't need it
for its actual (live, single-threaded) use case.
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Repo-root import shim so `python scripts/backfill_sentiment_history.py` works
# from anywhere -- mirrors scripts/backfill_edgar_fundamentals.py /
# scripts/retrain_models.py's identical shim and its documented rationale.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from data.historical_store import HistoricalStore  # noqa: E402
from data.portfolio_sync import resolve_universe  # noqa: E402
from data.sentiment_sources import CompositeSentimentSource  # noqa: E402
from settings import settings  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# yahoo_rss deliberately excluded -- see module docstring.
_DEFAULT_BACKFILL_SOURCES = "gdelt,edgar,finnhub,reddit"


def _process_one(
    symbol: str, source: CompositeSentimentSource, since: datetime,
) -> tuple:
    """Backfill one symbol. Never raises (CONSTRAINT #6) -- returns
    ``(symbol, n_documents, error)``. ``reset_cycle()`` gives this symbol its
    own fresh document budget / circuit breaker / wall-clock deadline,
    reinterpreting the per-cycle bounds as per-symbol for backfill purposes.
    """
    try:
        source.reset_cycle()
        docs = source.fetch_and_archive(symbol, since=since)
        logger.info("%s: %d documents backfilled", symbol, len(docs))
        return (symbol, len(docs), None)
    except Exception as exc:
        logger.error("Backfill failed for %s: %s", symbol, exc, exc_info=True)
        return (symbol, 0, str(exc))


def _print_depth_report(store: HistoricalStore) -> None:
    depth = store.get_sentiment_archive_depth_by_source()
    if not depth:
        logger.info("Archive depth report: sentiment_ingestion_audit is empty.")
        return
    logger.info("Archive depth by source (sentiment_ingestion_audit):")
    for source_name, info in sorted(depth.items()):
        logger.info(
            "  %-10s %5d docs, earliest=%s, ~%s days deep",
            source_name, info["document_count"], info["earliest_as_of"],
            info["depth_days"],
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tickers", default="all",
        help="Comma-separated tickers, or 'all' for the operator's tracked "
             "universe (held ∪ watchlists ∪ DEFAULT_TICKERS).",
    )
    parser.add_argument(
        "--months", type=float, default=5.0,
        help="How many months back to backfill (default: 5).",
    )
    parser.add_argument(
        "--sources", default=_DEFAULT_BACKFILL_SOURCES,
        help="Comma-separated source names to backfill (default excludes "
             "yahoo_rss -- it has no historical archive; see module docstring).",
    )
    parser.add_argument(
        "--max-seconds-per-symbol", type=float, default=300.0,
        help="Wall-clock budget per symbol for this run, overriding "
             "settings.SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE (whose 60s "
             "default is sized for a live per-cycle refresh, not a real "
             "multi-month historical backfill).",
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

    if "yahoo_rss" in {s.strip() for s in args.sources.split(",")}:
        logger.warning(
            "yahoo_rss has no historical archive to query -- it will "
            "contribute zero documents to this backfill regardless of "
            "--months (see YahooRSSSource's docstring)."
        )
    if "reddit" in {s.strip() for s in args.sources.split(",")}:
        logger.warning(
            "Reddit backfill caveat: credibility sub-scores for backfilled "
            "posts reflect each author's CURRENT account state, not their "
            "state at post time (Reddit's API cannot answer that). See "
            "RedditSource's docstring. Institutional sources (GDELT/EDGAR/"
            "Finnhub) carry no such caveat."
        )

    # Overrides for the duration of THIS standalone process only -- a
    # real historical backfill needs a source list and wall-clock budget the
    # live per-cycle defaults were never sized for. Safe: this script owns
    # its own process, nothing else reads these settings concurrently.
    settings.SENTIMENT_SOURCES = args.sources
    settings.SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE = args.max_seconds_per_symbol

    since = datetime.now(timezone.utc) - timedelta(days=int(args.months * 30))
    logger.info(
        "Backfilling %.1f months (since %s) from sources: %s",
        args.months, since.date().isoformat(), args.sources,
    )

    source = CompositeSentimentSource()
    store = HistoricalStore()
    results = [_process_one(symbol, source, since) for symbol in tickers]

    total_docs = sum(r[1] for r in results)
    n_errors = sum(1 for r in results if r[2] is not None)
    logger.info(
        "Backfill complete: %d tickers, %d documents archived, %d errored.",
        len(results), total_docs, n_errors,
    )
    _print_depth_report(store)


if __name__ == "__main__":
    main()
