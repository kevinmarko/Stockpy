#!/usr/bin/env python3
import sys
import os

if not os.environ.get("VIRTUAL_ENV") and os.path.exists(".venv/bin/python"):
    os.execv(".venv/bin/python", [".venv/bin/python"] + sys.argv)

import argparse
import logging
import sqlite3
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
import sys
import os

# Add repo root to sys.path so we can import internal modules
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from data.historical_store import HistoricalStore
DB_PATH = os.path.expanduser("~/.stockpy_local/quant_platform.db")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("repair_forecast")

def run_repair(apply: bool):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    cur = conn.cursor()
    cur.execute("SELECT * FROM forecast_errors")
    rows = cur.fetchall()
    
    hs = HistoricalStore()
    
    updates = []
    nulls = []
    
    now = datetime.now(timezone.utc)
    
    for row in rows:
        row_id = row["id"]
        symbol = row["symbol"]
        horizon = row["horizon_days"]
        forecast_ts_str = row["forecast_ts"]
        forecast_price = row["forecast_price"]
        actual_price = row["actual_price"]
        
        # Parse forecast_ts
        try:
            forecast_dt = pd.to_datetime(forecast_ts_str)
        except Exception:
            continue
            
        # Target date is forecast_dt + horizon trading days
        target_dt = forecast_dt + pd.offsets.BDay(horizon)
        
        # Check if the target date has elapsed
        if target_dt > pd.Timestamp(now):
            # Has not actually elapsed in trading days.
            # If it was early-actualized, we need to NULL it out.
            if actual_price is not None:
                nulls.append(row_id)
                logger.info(f"ID {row_id} ({symbol} h={horizon}): Not matured yet (matures {target_dt.date()}). Nulling out early actualization.")
        else:
            # Has matured. We need to set the actual_price to the historic price exactly at target_dt.
            # (or the closest available trading day on/after target_dt)
            try:
                bars = hs.get_bars(symbol, lookback_days=750)
                if bars is None or bars.empty or "Close" not in bars.columns:
                    continue
                # Get the price at or immediately after the target date
                # We can use asof or similar, but since we want the closing price around that day:
                # subset to bars on or after target_dt.date()
                target_date_str = target_dt.strftime("%Y-%m-%d")
                future_bars = bars.loc[target_date_str:]
                if not future_bars.empty:
                    correct_price = float(future_bars["Close"].iloc[0])
                    
                    if actual_price is None or abs(correct_price - actual_price) > 0.01:
                        # Needs update
                        sq_err = (correct_price - forecast_price) ** 2
                        updates.append((correct_price, sq_err, row_id))
                        logger.info(f"ID {row_id} ({symbol} h={horizon}): Matured {target_date_str}. Fixing price {actual_price} -> {correct_price:.2f}")
            except Exception as exc:
                logger.warning(f"Failed to fetch historical data for {symbol}: {exc}")
                continue

    if not apply:
        logger.info(f"DRY RUN: Found {len(nulls)} rows to NULL and {len(updates)} rows to update.")
        logger.info("Run with --apply to execute the repair.")
        return

    # Execute updates
    if nulls:
        cur.executemany(
            "UPDATE forecast_errors SET actual_price = NULL, squared_error = NULL WHERE id = ?",
            [(rid,) for rid in nulls]
        )
    
    if updates:
        cur.executemany(
            "UPDATE forecast_errors SET actual_price = ?, squared_error = ? WHERE id = ?",
            updates
        )
        
    conn.commit()
    logger.info(f"APPLY: Nulled {len(nulls)} rows, updated {len(updates)} rows.")
    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Repair forecast errors actualization logic to use trading days.")
    parser.add_argument("--apply", action="store_true", help="Apply the repairs to the database.")
    parser.add_argument("--dry-run", action="store_true", help="Dry run (default).")
    args = parser.parse_args()
    
    run_repair(apply=args.apply)
