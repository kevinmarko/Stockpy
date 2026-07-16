"""CLI backfill script that pulls point-in-time historical fundamentals from SEC EDGAR (via data/edgar_fundamentals.py) for a ticker universe and persists them into the HistoricalStore fundamentals history."""

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import partial
from pathlib import Path

# Repo-root import shim so `python scripts/backfill_edgar_fundamentals.py` works
# from anywhere — WITHOUT it, direct-path invocation (what deploy/crontab.txt and
# the trigger_edgar_backfill MCP tool both use) dies with
# `ModuleNotFoundError: No module named 'data'` because `python scripts/x.py`
# puts scripts/ on sys.path[0], not the repo root. Mirrors scripts/retrain_models.py.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from data import edgar_fundamentals  # noqa: E402
from data.historical_store import HistoricalStore  # noqa: E402
from data.portfolio_sync import resolve_universe  # noqa: E402
from settings import settings  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_all_filed_dates(facts: dict, since: str) -> list[str]:
    """Extract all unique filed dates across all facts since the cutoff."""
    dates = set()
    for namespace in facts.get("facts", {}).values():
        for fact in namespace.values():
            for unit_arr in fact.get("units", {}).values():
                for point in unit_arr:
                    filed = point.get("filed")
                    if filed and filed >= since:
                        dates.add(filed)
    return sorted(list(dates))

def _process_one(symbol: str, store: HistoricalStore, since: str):
    """Fetch → compute → upsert one symbol's PIT fundamentals.

    Returns a small summary tuple ``(symbol, n_written, n_skipped, error)`` and
    NEVER raises — an exception escaping into ``ThreadPoolExecutor.map`` would
    abort the entire batch at the first bad ticker, re-introducing the very
    dead-letter regression the per-symbol try/except exists to prevent. The
    returned tuple stays tiny on purpose: ``pool.map`` retains all N results, so
    returning the parsed (50–150 MB) facts dict would blow up memory at scale.
    """
    try:
        logger.info("Processing %s...", symbol)
        cik = edgar_fundamentals.get_cik(symbol)
        if not cik:
            logger.warning("Could not resolve CIK for %s, skipping.", symbol)
            return (symbol, 0, 0, "no_cik")

        facts = edgar_fundamentals.fetch_companyfacts(cik)
        if not facts:
            logger.warning("No facts returned for %s (CIK %s), skipping.", symbol, cik)
            return (symbol, 0, 0, "no_facts")

        # Get historical bars for price lookup. get_bars()'s lookback_days
        # defaults to 504 (~2 years) -- far short of a multi-year PIT backfill
        # (--since defaults to 2015-01-01). Compute a lookback that actually
        # reaches "since", or every report_date older than ~2 years silently
        # gets no matching bar -> NaN price -> NaN pe_ratio/pb_ratio/market_cap.
        since_dt = datetime.strptime(since, "%Y-%m-%d")
        lookback_days = max((datetime.now() - since_dt).days + 30, 504)
        bars = store.get_bars(symbol, lookback_days=lookback_days)

        filed_dates = get_all_filed_dates(facts, since)

        # Incremental skip: filed dates already stored from a prior EDGAR run are
        # idempotent no-ops (upsert_fundamentals_pit is INSERT OR REPLACE on
        # (symbol, as_of=report_date)), so skipping them is a pure cost win that
        # can NEVER change which rows land. Restatements produce a new filed date
        # and a widened --since produces older dates -- both fall outside `stored`
        # and are still processed. Scoped to source="edgar" so the daily
        # yahoo_computed writer's rows never mask an EDGAR filed date.
        stored = store.get_pit_report_dates(symbol, source="edgar", since=since)
        pending = [d for d in filed_dates if d not in stored]

        for report_date in pending:
            # Find latest price ON OR BEFORE report_date
            price = float('nan')
            if not bars.empty:
                # pandas slicing. Bar columns are capitalized (Open/High/Low/
                # Close/Volume) -- see HistoricalStore.get_bars's shape contract.
                past_bars = bars[bars.index <= report_date]
                if not past_bars.empty:
                    price = float(past_bars.iloc[-1]["Close"])

            shares = edgar_fundamentals.extract_shares(facts, report_date)

            ratios = edgar_fundamentals.compute_pit_ratios(facts, report_date, price, shares)
            store.upsert_fundamentals_pit(
                symbol,
                ratios,
                # The computed ratios themselves (NOT the massive raw XBRL
                # payload -- that stays unpersisted by design) so fields with
                # no dedicated typed DB column (e.g. current_ratio) are still
                # retrievable via HistoricalStore.get_fundamentals_raw's
                # raw_json blob instead of being silently dropped.
                ratios,
                report_date=report_date,
                source="edgar",
            )

        n_written = len(pending)
        n_skipped = len(filed_dates) - n_written
        logger.info(
            "Finished %s: %d new, %d already stored", symbol, n_written, n_skipped,
        )
        return (symbol, n_written, n_skipped, None)
    except Exception as exc:
        logger.error("Backfill failed for %s: %s", symbol, exc, exc_info=True)
        return (symbol, 0, 0, str(exc))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tickers",
        required=True,
        help="Comma separated list of tickers, or 'all' for the operator's "
             "tracked universe (held ∪ watchlists ∪ DEFAULT_TICKERS).",
    )
    parser.add_argument("--since", default="2015-01-01", help="YYYY-MM-DD cutoff")
    args = parser.parse_args()

    # Shared resolver (data/portfolio_sync.resolve_universe) so the CLI and the
    # trigger_edgar_backfill MCP tool can never drift on what "all" means.
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

    store = HistoricalStore()

    # Pre-warm the CIK cache serially on the main thread (one HTTP request for
    # company_tickers.json; the rest are dict hits) so the worker pool is a pure
    # companyfacts fetcher and unresolvable tickers are logged deterministically
    # up front instead of interleaved across worker logs.
    unresolved = [t for t in tickers if not edgar_fundamentals.get_cik(t)]
    if unresolved:
        logger.warning(
            "Could not resolve CIK for %d ticker(s): %s",
            len(unresolved), ", ".join(sorted(unresolved)),
        )

    # ThreadPoolExecutor over the same shape as data_engine.py's per-symbol
    # fetch. EDGAR_MAX_CONCURRENCY defaults to 4 (a MEMORY knob, not a compliance
    # knob -- the throttle in edgar_fundamentals._throttle guarantees SEC's limit
    # for any worker count; large filers' parsed companyfacts are 50-150 MB each).
    # pool.map preserves input order, so `results` is deterministic regardless of
    # completion order. The DB write stays inside the worker: HistoricalStore is
    # thread-safe (self._lock), and hoisting the write would force all N parsed
    # facts dicts to be held live at once.
    workers = max(1, int(getattr(settings, "EDGAR_MAX_CONCURRENCY", 4)))
    if workers == 1 or len(tickers) <= 1:
        results = [_process_one(s, store, args.since) for s in tickers]
    else:
        worker = partial(_process_one, store=store, since=args.since)
        with ThreadPoolExecutor(max_workers=min(workers, len(tickers))) as pool:
            results = list(pool.map(worker, tickers))

    total_written = sum(r[1] for r in results)
    total_skipped = sum(r[2] for r in results)
    n_errors = sum(1 for r in results if r[3] is not None)
    logger.info(
        "Backfill complete: %d tickers, %d new rows, %d already stored, %d errored.",
        len(results), total_written, total_skipped, n_errors,
    )

if __name__ == "__main__":
    main()
