#!/usr/bin/env python3
"""
scripts/repair_forecast_errors_horizon.py
==========================================
Historic-DB repair for the ``forecast_errors`` table's trading-day-aware
actualization fix (see ``docs/known_issues/forecast_ito_double_correction_
and_horizon_units.md``'s F5): rows previously actualized against whatever
price happened to be current when a backlog of pending forecasts was finally
caught up, rather than the real close on each row's own due date, or against
a calendar-day (not trading-day) horizon.

``--dry-run`` (the default) only reports how many rows would be NULLed
(early-actualized rows whose true due date hasn't elapsed yet in trading
days) and how many would be corrected (matured rows whose ``actual_price``
disagrees with the real due-date close) -- it never writes. ``--apply`` is
required to actually perform those UPDATEs. Neither mode ever DELETEs a row
from ``forecast_errors``; the 2.36M pre-existing rows are always preserved,
only the ``actual_price``/``squared_error`` columns are ever touched, and a
row that is genuinely not yet due is left/set to NULL rather than filled
with a fabricated value (CONSTRAINT #4/#6).
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# Add repo root to sys.path so we can import internal modules
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._bootstrap import bootstrap  # noqa: E402

bootstrap()

from data.historical_store import HistoricalStore  # noqa: E402

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

    # Per-symbol bars cache: this table can hold millions of rows across only
    # a few hundred distinct symbols, and a matured row's own historical bars
    # never change for the duration of one repair run -- calling
    # HistoricalStore.get_bars() fresh per ROW (rather than per SYMBOL) would
    # multiply a 750-day DB read by every row instead of by every symbol.
    bars_cache: dict = {}

    def _cached_closes(sym: str):
        """Return sym's Close series (index normalized, tz-naive, deduped,
        sorted) once per run, or None if it can't be resolved -- never
        fabricates a price on failure (CONSTRAINT #4)."""
        if sym in bars_cache:
            return bars_cache[sym]
        try:
            bars = hs.get_bars(sym, lookback_days=750)
            if bars is None or bars.empty or "Close" not in bars.columns:
                bars_cache[sym] = None
            else:
                closes = bars["Close"].copy()
                closes.index = pd.to_datetime(closes.index).normalize()
                closes = closes[~closes.index.duplicated(keep="last")].sort_index()
                bars_cache[sym] = closes
        except Exception as exc:
            logger.warning(f"Failed to fetch historical data for {sym}: {exc}")
            bars_cache[sym] = None
        return bars_cache[sym]

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
        target_dt = forecast_dt + pd.offsets.BDay(max(0, horizon))

        # Check if the target date has elapsed
        if target_dt > pd.Timestamp(now):
            # Has not actually elapsed in trading days.
            # If it was early-actualized, we need to NULL it out.
            if actual_price is not None:
                nulls.append(row_id)
                logger.info(f"ID {row_id} ({symbol} h={horizon}): Not matured yet (matures {target_dt.date()}). Nulling out early actualization.")
        else:
            # Has matured. Score against the close on the row's own due date
            # -- the nearest trading day AT OR BEFORE target_dt, NEVER after
            # it. This mirrors forecast_tracker.py::ForecastTracker.
            # _resolve_due_date_prices' exact convention (a bar after the due
            # date would be lookahead, and would also score this repaired
            # historical row differently than every row actualized going
            # forward by the corrected production path).
            closes = _cached_closes(symbol)
            if closes is None:
                continue
            try:
                due_ts = target_dt
                if due_ts.tzinfo is not None:
                    due_ts = due_ts.tz_convert(None)
                due_ts = due_ts.normalize()

                correct_price = None
                if due_ts in closes.index:
                    correct_price = float(closes.loc[due_ts])
                else:
                    prior = closes.index[closes.index <= due_ts]
                    if len(prior) > 0:
                        correct_price = float(closes.loc[prior[-1]])

                if correct_price is None:
                    # No bar at or before the due date is available (e.g. the
                    # symbol's history doesn't reach back far enough) --
                    # leave the row untouched rather than fabricate a price.
                    continue

                if actual_price is None or abs(correct_price - actual_price) > 0.01:
                    # Needs update
                    sq_err = (correct_price - forecast_price) ** 2
                    updates.append((correct_price, sq_err, row_id))
                    logger.info(f"ID {row_id} ({symbol} h={horizon}): Matured {due_ts.date()}. Fixing price {actual_price} -> {correct_price:.2f}")
            except Exception as exc:
                logger.warning(f"Failed to resolve due-date price for {symbol} (id={row_id}): {exc}")
                continue

    if not apply:
        logger.info(f"DRY RUN: Scanned {len(rows)} total rows. Found {len(nulls)} rows to NULL and {len(updates)} rows to update.")
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
    logger.info(f"APPLY: Scanned {len(rows)} total rows. Nulled {len(nulls)} rows, updated {len(updates)} rows.")
    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Repair forecast errors actualization logic to use trading days.")
    parser.add_argument("--apply", action="store_true", help="Apply the repairs to the database.")
    parser.add_argument("--dry-run", action="store_true", help="Dry run (default).")
    args = parser.parse_args()
    
    run_repair(apply=args.apply)
