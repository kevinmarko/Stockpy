"""CLI remediation script for a bars-adjustment-convention mismatch already persisted into
`price_bars` (e.g. FMP_BARS_ADJUSTMENT was set to 'full'/'light' — split-only — instead of the
correct 'dividend-adjusted' convention for a period of time, or any future provider/config
change that similarly splices two adjustment conventions into one (symbol, date) series).

Where to add symbols for this process: the --tickers argument below. This is the durable,
discoverable home for that — not a hardcoded list in this script, and not a one-off inline
command. See --help for the three ways to specify a ticker set.

What it does per symbol
------------------------
1. Finds the earliest date in `price_bars` written under --source (default 'fmpprovider') --
   i.e. the first date that could have been fetched under the bad adjustment convention.
2. Deletes ALL rows for that symbol from that date forward (any source, not just --source) --
   the safe choice, because price_bars is keyed by (symbol, date) and a stale row from another
   source sitting inside the affected window would leave the same adjustment-convention splice
   the cleanup is meant to remove. Rows strictly BEFORE the first affected date are left alone
   (genuinely unaffected history is not thrown away just because the symbol also has some FMP
   rows).
3. Calls HistoricalStore.get_bars(symbol), which re-fetches from whatever provider chain is
   CURRENTLY configured (settings.MARKET_DATA_PROVIDER / FMP_BARS_ADJUSTMENT) via its existing
   incremental-top-up logic -- filling exactly the gap just opened, no bespoke date-range-fetch
   code needed.

If a symbol has no rows from --source at all, it's skipped (nothing to repair) and reported
separately -- never silently treated as "processed".

Never raises out of the per-symbol loop (dead-letter resilience, matching every other backfill
script in scripts/) -- one bad symbol does not abort the batch.
"""

import argparse
import logging
import random
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._bootstrap import bootstrap  # noqa: E402
bootstrap()

from data.historical_store import HistoricalStore  # noqa: E402
from data.portfolio_sync import resolve_universe  # noqa: E402
from db_config import session_scope, get_dbapi_connection  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _all_symbols_for_source(store: HistoricalStore, source: str) -> list[str]:
    with session_scope(store.Session) as session:
        raw_conn = session.connection().connection
        conn = get_dbapi_connection(raw_conn)
        cur = conn.execute(
            "SELECT DISTINCT symbol FROM price_bars WHERE source = ? ORDER BY symbol", (source,)
        )
        return [r[0] for r in cur.fetchall()]


def _first_affected_date(store: HistoricalStore, symbol: str, source: str) -> str | None:
    with session_scope(store.Session) as session:
        raw_conn = session.connection().connection
        conn = get_dbapi_connection(raw_conn)
        cur = conn.execute(
            "SELECT MIN(date) FROM price_bars WHERE symbol = ? AND source = ?", (symbol, source)
        )
        row = cur.fetchone()
        return row[0] if row else None


def _delete_from_date(store: HistoricalStore, symbol: str, first_date: str) -> int:
    with session_scope(store.Session) as session:
        raw_conn = session.connection().connection
        conn = get_dbapi_connection(raw_conn)
        cur = conn.execute(
            "DELETE FROM price_bars WHERE symbol = ? AND date >= ?", (symbol, first_date)
        )
        return cur.rowcount if cur.rowcount is not None else 0


def _repair_one(store: HistoricalStore, symbol: str, source: str, dry_run: bool):
    """Returns (symbol, status, detail) -- never raises."""
    try:
        first_date = _first_affected_date(store, symbol, source)
        if first_date is None:
            return (symbol, "skipped", f"no rows with source={source!r}")

        if dry_run:
            return (symbol, "dry_run", f"would delete rows >= {first_date} and re-backfill")

        n_deleted = _delete_from_date(store, symbol, first_date)
        # Re-fetch: HistoricalStore.get_bars sees the new max stored date (either the last
        # good pre-affected-window date, or none at all) and does the right thing -- a
        # forward top-up from that date, or a full BARS_BACKFILL_DAYS backfill if the
        # symbol's DB history is now empty. Uses whatever provider is CURRENTLY configured.
        df = store.get_bars(symbol)
        n_after = len(df) if df is not None else 0
        return (
            symbol, "repaired",
            f"deleted {n_deleted} rows from {first_date}, re-fetched {n_after} rows in DB now",
        )
    except Exception as exc:
        logger.error("Repair failed for %s: %s", symbol, exc, exc_info=True)
        return (symbol, "error", str(exc))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--tickers",
        required=True,
        help=(
            "Comma-separated ticker list (e.g. 'AAPL,MSFT'), 'universe' for the operator's "
            "tracked universe (held ∪ watchlists ∪ DEFAULT_TICKERS, via resolve_universe), or "
            "'all' for every symbol currently carrying a --source row in price_bars."
        ),
    )
    parser.add_argument(
        "--random", type=int, default=0,
        help="Add this many additional random symbols (from every symbol carrying a --source "
             "row in price_bars) on top of --tickers. Skips symbols already selected.",
    )
    parser.add_argument(
        "--source", default="fmpprovider",
        help="price_bars.source value that identifies the affected rows (default: 'fmpprovider').",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be deleted/re-fetched per symbol without touching the DB.",
    )
    args = parser.parse_args()

    store = HistoricalStore()

    if args.tickers == "universe":
        tickers = resolve_universe("all", allow_live_broker_fetch=False)
    elif args.tickers == "all":
        tickers = _all_symbols_for_source(store, args.source)
    else:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    if not tickers:
        print("No tickers resolved -- nothing to do.")
        return

    if args.random > 0:
        pool = [s for s in _all_symbols_for_source(store, args.source) if s not in tickers]
        extra = random.sample(pool, min(args.random, len(pool)))
        tickers = tickers + extra
        logger.info("Added %d random symbols (requested %d, pool had %d).", len(extra), args.random, len(pool))

    logger.info("Repairing %d symbol(s) [source=%s, dry_run=%s]: %s", len(tickers), args.source, args.dry_run, tickers)

    results = [_repair_one(store, sym, args.source, args.dry_run) for sym in tickers]

    by_status: dict[str, int] = {}
    for symbol, status, detail in results:
        by_status[status] = by_status.get(status, 0) + 1
        logger.info("%-8s %-9s %s", symbol, status, detail)

    logger.info("Summary: %s", by_status)


if __name__ == "__main__":
    main()
