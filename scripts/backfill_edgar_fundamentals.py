import argparse
import logging
from datetime import datetime
import time

from data import edgar_fundamentals
from data.historical_store import HistoricalStore

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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", required=True, help="Comma separated list of tickers")
    parser.add_argument("--since", default="2015-01-01", help="YYYY-MM-DD cutoff")
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",")]
    store = HistoricalStore()

    for symbol in tickers:
        try:
            logger.info("Processing %s...", symbol)
            cik = edgar_fundamentals.get_cik(symbol)
            if not cik:
                logger.warning("Could not resolve CIK for %s, skipping.", symbol)
                continue

            facts = edgar_fundamentals.fetch_companyfacts(cik)
            if not facts:
                logger.warning("No facts returned for %s (CIK %s), skipping.", symbol, cik)
                continue

            # Get historical bars for price lookup. get_bars()'s lookback_days
            # defaults to 504 (~2 years) -- far short of a multi-year PIT backfill
            # (--since defaults to 2015-01-01). Compute a lookback that actually
            # reaches "since", or every report_date older than ~2 years silently
            # gets no matching bar -> NaN price -> NaN pe_ratio/pb_ratio/market_cap.
            since_dt = datetime.strptime(args.since, "%Y-%m-%d")
            lookback_days = max((datetime.now() - since_dt).days + 30, 504)
            bars = store.get_bars(symbol, lookback_days=lookback_days)

            filed_dates = get_all_filed_dates(facts, args.since)
            logger.info("Found %d report_dates for %s since %s", len(filed_dates), symbol, args.since)

            for report_date in filed_dates:
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

            logger.info("Finished %s", symbol)
        except Exception as exc:
            logger.error("Backfill failed for %s: %s", symbol, exc, exc_info=True)
            continue

if __name__ == "__main__":
    main()
