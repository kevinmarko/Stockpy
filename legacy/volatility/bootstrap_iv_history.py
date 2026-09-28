"""CLI script to backfill a REALIZED-VOLATILITY-DERIVED PROXY (NOT genuine
options-chain-derived implied volatility) into the iv_history table for the
lookahead-free IV-rank / VRP computations in volatility/iv_engine.py.

What this actually computes: 20-day rolling realized (historical) annualized
standard deviation of daily returns, plus a flat 3.8% Volatility Risk Premium
(VRP) offset -- ``estimated_iv = rolling_vol + 0.038``. This is a standard,
free-data proxy used because historical options-chain IV is not available at
no cost, but it is NOT a real ATM implied volatility reading. A genuine
options-chain-derived IV is written only by the live pipeline
(``pipeline/production_steps.py::OptionsAnalysisStep`` and
``technical_options_engine.py``'s opt-in ``settings.OPTIONS_TRUE_IVR_ENABLED``
path), both via ``volatility.iv_engine.get_30d_atm_iv()``.

Provenance (2026-09, see docs/known_issues/
bootstrap_iv_history_provenance_fabrication_risk.md): every row this script
writes is tagged ``source=IV_SOURCE_SYNTHETIC_BOOTSTRAP`` in the SAME
``iv_history`` table the live pipeline writes to. ``volatility.iv_engine.
IVHistoryStore.get_historical_ivs()`` -- and therefore ``calculate_true_ivr()``,
which gates real premium-selling trade decisions per this platform's
``True_IVR > 50`` convention -- excludes these synthetic rows from ranking by
default, so a genuine live IV reading is never silently ranked against this
proxy as if it were real (CONSTRAINT #4). Use this script only to warm-start
a rough sense of a ticker's historical realized-vol regime while genuine
chain-derived history accrues via the live pipeline -- never as a substitute
for real IV history in a live trade decision.
"""

import os
import argparse
import logging
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from volatility.iv_engine import IVHistoryStore, IV_SOURCE_SYNTHETIC_BOOTSTRAP

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Bootstrap_IV_History")

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Backfill a realized-volatility-derived PROXY for historical 30d "
            "ATM IV (rolling_vol + 3.8% VRP offset) -- NOT genuine "
            "options-chain-derived IV. Rows are tagged "
            "source='synthetic_bootstrap' in iv_history and are excluded by "
            "default from calculate_true_ivr()'s ranking history."
        )
    )
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

            # yfinance returns a tz-aware (America/New_York) DatetimeIndex; the
            # cutoff_date comparison below is tz-naive, so normalize here or every
            # ticker fails with "Invalid comparison between dtype=datetime64[ns, tz]
            # and Timestamp" before a single row is ever recorded.
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)

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
                # Explicit, never-defaulted provenance tag (CONSTRAINT #4) --
                # this is a realized-vol-derived proxy, not a genuine
                # options-chain-derived IV reading, and must never be
                # written in a way that lets it look like one. See the
                # module docstring and IVHistoryStore.get_historical_ivs().
                store.record_iv(
                    ticker, date_str, clamped_iv, source=IV_SOURCE_SYNTHETIC_BOOTSTRAP
                )
                recorded_count += 1
                
            logger.info(f"Successfully backfilled {recorded_count} days of IV history for {ticker}.")
            
        except Exception as e:
            logger.error(f"Error backfilling IV history for {ticker}: {e}")

if __name__ == "__main__":
    main()
