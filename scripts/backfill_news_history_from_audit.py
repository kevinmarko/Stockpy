"""
scripts/backfill_news_history_from_audit.py
=============================================
Backfills ``HistoricalStore``'s ``news_history`` table (the daily
per-symbol series the webapp's Sentiment Dynamics screen charts) from the
platform's FREE multi-source sentiment pipeline's own historical archive --
``sentiment_ingestion_audit`` (GDELT / SEC EDGAR / Reddit / Google News,
populated by ``scripts/backfill_sentiment_history.py``) -- rather than a
paid/keyed source.

Prerequisite
------------
Run ``scripts/backfill_sentiment_history.py`` FIRST to populate
``sentiment_ingestion_audit`` with real historical documents. No API key
is required for the gdelt/edgar/google_news sources (Reddit needs
``REDDIT_CLIENT_ID``/``REDDIT_CLIENT_SECRET``; Finnhub is optional and can
be dropped)::

    python3 scripts/backfill_sentiment_history.py --months 6 \\
        --sources gdelt,edgar,reddit,google_news

This script then does a SECOND pass over the same date range: pure local DB
aggregation, zero network calls, zero API keys required. For each trading
day, it calls ``HistoricalStore.get_sentiment_aggregate_by_symbol()`` --
the exact same read ``signals.news_catalyst.NewsCatalystSignal.pre_compute()``
already runs live every cycle -- to compute that day's credibility-weighted
sentiment aggregate per symbol from whatever real documents are already
archived, and writes the result into ``news_history``.

Why this is honest, not a hindsight shortcut
---------------------------------------------
The ``credibility_weight``/``final_weighted_score`` values already stored
in ``sentiment_ingestion_audit`` were computed by ``signals/credibility.py``
at ingestion time from each document's OWN metadata (source type, and for
Reddit, author state AT INGESTION TIME -- see
``scripts/backfill_sentiment_history.py``'s own Reddit caveat). This script
performs no new scoring of its own -- it only re-runs the SAME aggregation
query the live pipeline already runs every cycle, against real historical
documents. A trading day with zero archived documents (GDELT was
rate-limited that day, EDGAR had nothing, etc.) archives as an honest
``NaN`` gap in ``news_history``, never a fabricated neutral score
(CONSTRAINT #4).

Complements, does not replace, scripts/backfill_news_history.py
------------------------------------------------------------------
That script backfills the SAME ``news_history`` table from Finnhub headline
sentiment specifically (requires ``FINNHUB_API_KEY``). THIS script
backfills it from the free multi-source credibility-weighted aggregate
instead. Running both is fine -- ``news_history``'s primary key is
``(symbol, as_of)``, so for a given symbol+day whichever backfill runs LAST
wins that row; there is no blending across separate script invocations
(``signals/news_catalyst.py``'s LIVE pipeline blend, by contrast, DOES
blend both within a single cycle -- see ``NewsCatalystSignal.
_build_archive_scores``). An operator with no Finnhub key still gets real
``news_history`` coverage entirely from free sources via this script alone.
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Set

# Repo-root import shim so `python scripts/backfill_news_history_from_audit.py`
# works from anywhere -- mirrors scripts/backfill_sentiment_history.py's
# identical shim and its documented rationale.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Venv re-exec + .env loading -- must run before any third-party/project
# import below (see scripts/_bootstrap.py's module docstring for why).
from scripts._bootstrap import bootstrap  # noqa: E402
bootstrap()

import pandas as pd  # noqa: E402

from data.historical_store import HistoricalStore  # noqa: E402
from data.portfolio_sync import resolve_universe  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _backfill_day(store: HistoricalStore, trading_day: str, tickers: Set[str]) -> int:
    """Aggregate one trading day's ``sentiment_ingestion_audit`` rows and
    write them to ``news_history``. Returns the number of symbols written
    (0 on an empty day or any failure -- CONSTRAINT #6, never raises)."""
    try:
        agg = store.get_sentiment_aggregate_by_symbol(trading_day)
    except Exception as exc:
        logger.warning("%s: aggregate read failed: %s", trading_day, exc)
        return 0
    if not agg:
        return 0
    scores = {
        symbol: entry.get("credibility_weighted_sentiment", float("nan"))
        for symbol, entry in agg.items()
        if symbol in tickers
    }
    if not scores:
        return 0
    try:
        as_of = datetime.strptime(trading_day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        store.save_news_sentiment(scores, as_of, source="credibility_backfill")
    except Exception as exc:
        logger.warning("%s: news_history write failed: %s", trading_day, exc)
        return 0
    return len(scores)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tickers", default="all",
        help="Comma-separated tickers, or 'all' for the operator's tracked "
             "universe (held ∪ watchlists ∪ DEFAULT_TICKERS).",
    )
    parser.add_argument(
        "--months", type=float, default=6.0,
        help="How many months back to aggregate (default: 6). Only days "
             "already covered by sentiment_ingestion_audit (see "
             "scripts/backfill_sentiment_history.py) produce a real value; "
             "the rest archive as honest NaN gaps.",
    )
    args = parser.parse_args()

    # allow_live_broker_fetch=False: a headless backfill script must
    # never attempt a live Robinhood TOTP/MFA login (absent RH_MFA_SECRET
    # this falls back to a blocking interactive prompt on a real TTY, or
    # raises immediately headless) just to resolve the universe -- the
    # best available cached snapshot is fine here. See
    # data.portfolio_sync.resolve_universe's own docstring.
    tickers = resolve_universe(args.tickers, allow_live_broker_fetch=False)
    if not tickers:
        logger.error(
            "No tickers to process: --tickers=%r resolved to an empty universe. "
            "Pass explicit tickers, or configure DEFAULT_TICKERS / a Robinhood "
            "snapshot / watchlist files (SYNC_WATCHLIST_FILES).",
            args.tickers,
        )
        return
    tickers_set = {str(t).upper() for t in tickers}
    logger.info("Resolved %d tickers from --tickers=%r", len(tickers), args.tickers)

    store = HistoricalStore()
    depth = store.get_sentiment_archive_depth_by_source()
    if not depth:
        logger.error(
            "sentiment_ingestion_audit is empty -- nothing to aggregate. Run "
            "`python3 scripts/backfill_sentiment_history.py --months %.1f` "
            "first to populate real historical documents from GDELT/EDGAR/"
            "Reddit/Google News.", args.months,
        )
        return
    logger.info("sentiment_ingestion_audit depth by source: %s", depth)

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=int(args.months * 30))
    trading_days = [
        d.strftime("%Y-%m-%d") for d in pd.bdate_range(start_date, end_date)
    ]
    logger.info(
        "Aggregating %d trading days (%s to %s) for %d tickers.",
        len(trading_days), start_date.date(), end_date.date(), len(tickers),
    )

    days_with_data = 0
    total_written = 0
    for trading_day in trading_days:
        n = _backfill_day(store, trading_day, tickers_set)
        if n:
            days_with_data += 1
            total_written += n

    logger.info(
        "Backfill complete: %d trading days scanned, %d had real sentiment "
        "data, %d symbol-day rows written to news_history (rest are honest "
        "NaN gaps where sentiment_ingestion_audit had nothing for that day).",
        len(trading_days), days_with_data, total_written,
    )


if __name__ == "__main__":
    main()
