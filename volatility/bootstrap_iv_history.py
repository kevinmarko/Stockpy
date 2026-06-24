import os
import argparse
import logging
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from volatility.iv_engine import IVHistoryStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Bootstrap_IV_History")

def main():
    parser = argparse.ArgumentParser(description="Backfill historical 30d ATM IV estimates using realized volatility proxy + VRP.")
    parser.add_argument("--tickers", nargs="+", required=True, help="List of ticker symbols to backfill.")
    parser.add_argument("--days", type=int, default=365, help="Number of historical days to backfill.")
    args = parser.parse_args()

    store = IVHistoryStore()
    
    # We fetch a bit extra data to compute the 20-day rolling volatility starting from Day 1
    fetch_days = args.days + 50
    start_date = (datetime.now() - timedelta(days=fetch_days)).strftime("%Y-%m-%d")

    logger.info(f"Starting IV history backfill for tickers: {args.tickers} over {args.days} days.")
    
    for ticker in args.tickers:
        ticker = ticker.upper().strip()
        try:
            logger.info(f"Fetching historical prices for {ticker} starting from {start_date}...")
            t = yf.Ticker(ticker)
            df = t.history(start=start_date)
            
            if df.empty:
                logger.warning(f"No price history found for {ticker}. Skipping.")
                continue
                
            # Compute 20-day rolling annualized standard deviation
            returns = df['Close'].pct_change().dropna()
            rolling_vol = returns.rolling(window=20).std() * np.sqrt(252)
            
            # Estimate IV = Realized Volatility + 3.8% Volatility Risk Premium (VRP) proxy
            # This is a standard and robust proxy since historical options data is not free.
            estimated_iv = (rolling_vol + 0.038).dropna()
            
            # Slice to only the requested lookback period
            cutoff_date = datetime.now() - timedelta(days=args.days)
            estimated_iv = estimated_iv[estimated_iv.index >= pd.to_datetime(cutoff_date)]
            
            recorded_count = 0
            for dt, iv_val in estimated_iv.items():
                date_str = dt.strftime("%Y-%m-%d")
                # Clamp to realistic bounds [5%, 200%]
                clamped_iv = max(0.05, min(2.0, float(iv_val)))
                store.record_iv(ticker, date_str, clamped_iv)
                recorded_count += 1
                
            logger.info(f"Successfully backfilled {recorded_count} days of IV history for {ticker}.")
            
        except Exception as e:
            logger.error(f"Error backfilling IV history for {ticker}: {e}")

if __name__ == "__main__":
    main()
