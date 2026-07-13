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

def extract_shares(facts: dict, report_date: str) -> float:
    dei = facts.get("facts", {}).get("dei", {})
    val = edgar_fundamentals.extract_latest_fact(dei, "EntityCommonStockSharesOutstanding", report_date)
    if val is not None:
        return float(val)
    # fallback to us-gaap if missing in dei
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    val = edgar_fundamentals.extract_latest_fact(us_gaap, "CommonStockSharesOutstanding", report_date)
    if val is not None:
        return float(val)
    return 0.0

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", required=True, help="Comma separated list of tickers")
    parser.add_argument("--since", default="2015-01-01", help="YYYY-MM-DD cutoff")
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",")]
    store = HistoricalStore()

    for symbol in tickers:
        logger.info("Processing %s...", symbol)
        cik = edgar_fundamentals.get_cik(symbol)
        if not cik:
            logger.warning("Could not resolve CIK for %s, skipping.", symbol)
            continue
            
        facts = edgar_fundamentals.fetch_companyfacts(cik)
        if not facts:
            logger.warning("No facts returned for %s (CIK %s), skipping.", symbol, cik)
            continue
            
        # Get historical bars for price lookup
        # Lookback sufficiently to cover the "since" date if possible, but store.get_bars
        # only fetches recent by default unless we already backfilled. We will just load
        # what is in the DB.
        bars = store.get_bars(symbol)
        
        filed_dates = get_all_filed_dates(facts, args.since)
        logger.info("Found %d report_dates for %s since %s", len(filed_dates), symbol, args.since)
        
        for report_date in filed_dates:
            # Find latest price ON OR BEFORE report_date
            price = float('nan')
            if not bars.empty:
                # pandas slicing
                past_bars = bars[bars.index <= report_date]
                if not past_bars.empty:
                    price = float(past_bars.iloc[-1]["close"])
                    
            shares = extract_shares(facts, report_date)
            
            ratios = edgar_fundamentals.compute_pit_ratios(facts, report_date, price, shares)
            store.upsert_fundamentals_pit(
                symbol,
                ratios,
                {}, # Raw empty because we don't need to persist the massive XBRL
                report_date=report_date,
                source="edgar"
            )
            
        logger.info("Finished %s", symbol)

if __name__ == "__main__":
    main()
