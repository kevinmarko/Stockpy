"""
InvestYo Quant Platform - Point-in-Time Universe Loader
======================================================
This module scrapes Wikipedia to reconstruct the historical S&P 500 index constituents,
accounting for additions and deletions, to detect and report survivorship bias.
"""

import os
import logging
import argparse
from datetime import date, datetime, timedelta
from typing import List, Tuple, Dict, Any, Optional
import pandas as pd
import numpy as np
import requests

# Set up module logger
logger = logging.getLogger("Universe_Engine")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Anchor data paths to this module's directory, not the process CWD, so that
# get_delisted_tickers() / fetch_and_cache_universe() resolve the same files
# regardless of where the platform (or a test that changed CWD) is invoked from.
# Mirrors the Path(__file__)-anchored pattern in data/robinhood_portfolio.py.
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(_MODULE_DIR, "data", "universe_cache.parquet")
DELISTED_PATH = os.path.join(_MODULE_DIR, "data", "delisted_tickers.csv")

def clean_ticker(ticker: Any) -> Optional[str]:
    """Clean and standardize a ticker symbol for yfinance compatibility."""
    if pd.isna(ticker) or not isinstance(ticker, str):
        return None
    # Remove footnotes like [6], [a] or any extra spaces
    cleaned = ticker.split('[')[0].strip().upper()
    # Replace dot with hyphen for yfinance (e.g. BRK.B -> BRK-B)
    cleaned = cleaned.replace('.', '-')
    return cleaned if cleaned else None

def fetch_and_cache_universe() -> pd.DataFrame:
    """Scrapes Wikipedia and caches the combined data to a parquet file."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    logger.info("Fetching S&P 500 constituents from Wikipedia...")
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        tables = pd.read_html(resp.text)
    except Exception as e:
        logger.error(f"Error scraping Wikipedia: {e}")
        if os.path.exists(CACHE_PATH):
            logger.warning("Scraping failed. Loading stale cache as fallback.")
            return pd.read_parquet(CACHE_PATH)
        raise RuntimeError(f"Failed to scrape Wikipedia and no cache found: {e}")

    if len(tables) < 2:
        raise ValueError("Wikipedia page structure changed. S&P 500 tables not found.")

    # 1. Parse Current Constituents
    current_df = tables[0]
    symbol_col = None
    for col in current_df.columns:
        if col.lower() in ["symbol", "ticker"]:
            symbol_col = col
            break
    if not symbol_col:
        raise ValueError("Could not find Symbol/Ticker column in Wikipedia current table.")
    
    current_tickers = [clean_ticker(t) for t in current_df[symbol_col].dropna()]
    current_tickers = [t for t in current_tickers if t]

    # 2. Parse Changes
    changes_df = tables[1].copy()
    if isinstance(changes_df.columns, pd.MultiIndex):
        changes_df.columns = [f"{col[0]}_{col[1]}" for col in changes_df.columns]

    date_col = None
    added_col = None
    removed_col = None

    for col in changes_df.columns:
        col_lower = str(col).lower()
        if "date" in col_lower:
            date_col = col
        elif "added" in col_lower and "ticker" in col_lower:
            added_col = col
        elif "removed" in col_lower and "ticker" in col_lower:
            removed_col = col

    if not date_col or not added_col or not removed_col:
        raise ValueError("Could not identify Date, Added Ticker, or Removed Ticker columns in changes table.")

    # Convert to combined schema
    records = []
    # Add current tickers
    today_str = datetime.now().strftime("%Y-%m-%d")
    for t in current_tickers:
        records.append({
            "type": "current",
            "date": today_str,
            "added_ticker": t,
            "removed_ticker": None
        })

    # Add historical changes
    for _, row in changes_df.iterrows():
        try:
            raw_date = row[date_col]
            if pd.isna(raw_date):
                continue
            parsed_date = pd.to_datetime(raw_date).strftime("%Y-%m-%d")
            added = clean_ticker(row[added_col])
            removed = clean_ticker(row[removed_col])
            if added or removed:
                records.append({
                    "type": "change",
                    "date": parsed_date,
                    "added_ticker": added,
                    "removed_ticker": removed
                })
        except Exception as ex:
            logger.warning(f"Skipping malformed change row: {row.to_dict()} due to: {ex}")

    combined_df = pd.DataFrame(records)
    
    # Ensure directory exists and cache to parquet
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    combined_df.to_parquet(CACHE_PATH, index=False)
    logger.info(f"Successfully cached universe data to {CACHE_PATH}")
    return combined_df

def load_universe_data() -> pd.DataFrame:
    """Loads S&P 500 data from cache, refreshing it if it's older than a week."""
    refresh_needed = True
    if os.path.exists(CACHE_PATH):
        mtime = os.path.getmtime(CACHE_PATH)
        age = datetime.now() - datetime.fromtimestamp(mtime)
        if age < timedelta(days=7):
            refresh_needed = False
            
    if refresh_needed:
        try:
            return fetch_and_cache_universe()
        except Exception as e:
            logger.error(f"Failed to refresh cache, attempting to read existing cache: {e}")
            if os.path.exists(CACHE_PATH):
                return pd.read_parquet(CACHE_PATH)
            raise e
    else:
        return pd.read_parquet(CACHE_PATH)

def get_sp500_constituents(as_of_date: date) -> List[str]:
    """Reconstruct S&P 500 constituents for a given date by walking changes backward."""
    df = load_universe_data()
    
    current_tickers = set(df[df["type"] == "current"]["added_ticker"].dropna().unique())
    changes_df = df[df["type"] == "change"].copy()
    changes_df["date_parsed"] = pd.to_datetime(changes_df["date"]).dt.date
    
    # Sort changes chronologically descending (newest changes first)
    changes_sorted = changes_df.sort_values(by="date_parsed", ascending=False)
    
    target_date = as_of_date
    if isinstance(target_date, datetime):
         target_date = target_date.date()
         
    # Walk backward from today's list
    for _, row in changes_sorted.iterrows():
        change_date = row["date_parsed"]
        if change_date > target_date:
            added = row["added_ticker"]
            removed = row["removed_ticker"]
            # Going backward in time:
            # If it was added after target_date, it wasn't in the index on target_date
            if added and added in current_tickers:
                current_tickers.remove(added)
            # If it was removed after target_date, it was in the index on target_date
            if removed:
                current_tickers.add(removed)

    return sorted(list(current_tickers))

def get_delisted_tickers() -> pd.DataFrame:
    """Read delisted tickers from the local seed CSV file."""
    if not os.path.exists(DELISTED_PATH):
        logger.warning(f"Delisted tickers file {DELISTED_PATH} not found. Returning empty DataFrame.")
        return pd.DataFrame(columns=["ticker", "company", "delisting_date", "reason"])
    
    df = pd.read_csv(DELISTED_PATH)
    df["ticker"] = df["ticker"].apply(clean_ticker)
    df["delisting_date"] = pd.to_datetime(df["delisting_date"]).dt.date
    return df

def get_universe_with_survivorship_warning(
    as_of_date: date, 
    include_delisted: bool = True
) -> Tuple[List[str], Dict[str, Any]]:
    """Returns the S&P 500 constituents at as_of_date and computes a bias report."""
    constituents = get_sp500_constituents(as_of_date)
    
    # Calculate statistics for the bias report
    n_current = len(get_sp500_constituents(date.today()))
    n_at_date = len(constituents)
    
    # Check delistings in period
    delisted_df = get_delisted_tickers()
    target_date = as_of_date
    if isinstance(target_date, datetime):
         target_date = target_date.date()
         
    delisted_in_period = delisted_df[
        (delisted_df["delisting_date"] >= target_date) & 
        (delisted_df["delisting_date"] <= date.today())
    ]
    n_delisted_in_period = len(delisted_in_period)
    
    # Estimate bias percent: ~1.0% per year since as_of_date, bounded between 0.5% and 15%
    years = (date.today() - target_date).days / 365.25
    estimated_bias_pct = min(15.0, max(0.5, years * 1.0))
    
    bias_report = {
        "n_current": n_current,
        "n_at_date": n_at_date,
        "n_delisted_in_period": n_delisted_in_period,
        "estimated_bias_pct": round(estimated_bias_pct, 2)
    }
    
    # If include_delisted is requested, we can optionally merge active constituents 
    # with delisted ones or keep them as is (constituents are the true point-in-time universe).
    # We will keep constituents as the point-in-time active set.
    return constituents, bias_report

def print_survivorship_bias_warning(bias_report: Dict[str, Any]) -> None:
    """Print the institutional-grade survivorship bias warning."""
    print("=" * 80)
    print(" WARNING — SURVIVORSHIP BIAS: Free-data backtests systematically overstate returns")
    print(" by ~0.5-1.5%/year on US equities and far more on small-caps/emerging markets.")
    print(" Treat results accordingly.")
    print("-" * 80)
    print(f" Bias Report Details:")
    print(f"   - Current S&P 500 constituents: {bias_report['n_current']}")
    print(f"   - Constituents at target date:  {bias_report['n_at_date']}")
    print(f"   - Delisted tickers in period:  {bias_report['n_delisted_in_period']}")
    print(f"   - Estimated annualized bias:    {bias_report['estimated_bias_pct']}%")
    print("=" * 80)

def main() -> None:
    """CLI entrypoint for universe engine."""
    parser = argparse.ArgumentParser(description="InvestYo S&P 500 Universe Loader")
    parser.add_argument("--date", type=str, default=None, help="As-of date in YYYY-MM-DD format")
    parser.add_argument("--report", action="store_true", help="Print survivorship bias report")
    args = parser.parse_args()
    
    as_of = date.today()
    if args.date:
        as_of = datetime.strptime(args.date, "%Y-%m-%d").date()
        
    constituents, bias_report = get_universe_with_survivorship_warning(as_of)
    
    if args.report:
        print_survivorship_bias_warning(bias_report)
        print(f"\nPoint-in-Time Universe for {as_of} contains {len(constituents)} tickers:")
        print(", ".join(constituents[:25]) + " ...")
    else:
        for ticker in constituents:
            print(ticker)

if __name__ == "__main__":
    main()
