"""
Historical Store — Tier 2.3 Phase 1
=====================================
Persistent OHLCV bar cache backed by ``quant_platform.db``.

Every run currently re-fetches ~2 years of bars per symbol from yfinance even
though a bar recorded yesterday will never change.  This module intercepts that
fetch, returns cached rows, and tops up only the delta (yesterday → today) from
the live provider.

Design
------
* **raw sqlite3 + WAL** — same pattern as ``forecasting/forecast_tracker.py``;
  no SQLAlchemy ORM overhead for time-series I/O.
* **Dead-letter resilient** (CONSTRAINT #6): every public method wraps its body
  in try/except and falls back to a live provider fetch (or returns an empty
  DataFrame) on any DB error — never raises.
* **No fabricated data** (CONSTRAINT #4): an empty DB + failed live fetch
  returns an empty DataFrame; zero-filled or synthetic rows are never returned.
* **Identical shape contract**: ``get_bars()`` returns a tz-naive
  ``DatetimeIndex`` with columns ``[Open, High, Low, Close, Volume]`` —
  byte-identical to ``DataEngine.fetch_technical_raw()`` so all downstream
  signal/forecasting/strategy code runs unchanged.
* **Incremental top-up**: ``SELECT MAX(date)`` per symbol → fetch only
  ``(max_date, today]`` on each call.  First call = full
  ``settings.BARS_BACKFILL_DAYS`` backfill (~2 years).

Table: ``price_bars``
---------------------
+------------+---------+------------------------------------------------+
| Column     | Type    | Notes                                          |
+------------+---------+------------------------------------------------+
| symbol     | TEXT    | Ticker, uppercase                              |
| date       | TEXT    | ISO date "YYYY-MM-DD"                          |
| open       | REAL    |                                                |
| high       | REAL    |                                                |
| low        | REAL    |                                                |
| close      | REAL    |                                                |
| adj_close  | REAL    | Adjusted close (for total-return metrics)      |
| volume     | INTEGER |                                                |
| source     | TEXT    | "yfinance", "alpaca", etc.                     |
| fetched_at | TEXT    | UTC ISO-8601 timestamp of the row's last write |
+------------+---------+------------------------------------------------+
Primary key: (symbol, date)
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS price_bars (
    symbol     TEXT    NOT NULL,
    date       TEXT    NOT NULL,
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    adj_close  REAL,
    volume     INTEGER,
    source     TEXT    NOT NULL,
    fetched_at TEXT    NOT NULL,
    PRIMARY KEY (symbol, date)
)
"""

_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_price_bars_symbol_date
    ON price_bars (symbol, date)
"""

# Column order returned by SELECT and used to construct the DataFrame.
_SELECT_COLS = "open, high, low, close, adj_close, volume"

# The public DataFrame column names — must match DataEngine.fetch_technical_raw().
_DF_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


class HistoricalStore:
    """Persistent OHLCV bar cache with incremental top-up.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file (default ``"quant_platform.db"``).
    """

    def __init__(self, db_path: str = "quant_platform.db") -> None:
        self._db_path = db_path
        self._ensure_table()

    # ------------------------------------------------------------------ #
    # Private helpers                                                       #
    # ------------------------------------------------------------------ #

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")  # concurrent read-write safe
        return conn

    def _ensure_table(self) -> None:
        try:
            with self._connect() as conn:
                conn.execute(_TABLE_DDL)
                conn.execute(_INDEX_DDL)
                conn.commit()
        except Exception as exc:
            logger.warning("HistoricalStore._ensure_table failed: %s", exc)

    @staticmethod
    def _now_utc_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------ #
    # Public API — Bars (Phase 1)                                          #
    # ------------------------------------------------------------------ #

    def latest_bar_date(self, symbol: str) -> Optional[pd.Timestamp]:
        """Return the most-recent stored date for *symbol*, or ``None``.

        Never raises — returns ``None`` on any DB error.
        """
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT MAX(date) FROM price_bars WHERE symbol = ?",
                    (symbol.upper(),),
                ).fetchone()
            raw = row[0] if row else None
            return pd.Timestamp(raw) if raw else None
        except Exception as exc:
            logger.debug("latest_bar_date(%s) failed: %s", symbol, exc)
            return None

    def get_bars(
        self,
        symbol: str,
        lookback_days: int = 504,
        *,
        provider=None,
    ) -> pd.DataFrame:
        """Return a tz-naive OHLCV DataFrame for *symbol* with incremental top-up.

        Shape contract (identical to ``DataEngine.fetch_technical_raw()``)
        ------------------------------------------------------------------
        * Index  : tz-naive ``pd.DatetimeIndex``, sorted ascending.
        * Columns: ``["Open", "High", "Low", "Close", "Volume"]``

        Fetch logic
        -----------
        1. Read the most-recent stored date (``latest_bar_date``).
        2. If the DB is empty for this symbol, request a full
           ``settings.BARS_BACKFILL_DAYS`` backfill from the provider.
        3. Otherwise request only the delta ``(max_date, today]``
           (typically 1–5 bars on a daily cadence).
        4. Upsert every new row via ``INSERT OR REPLACE``.
        5. Return the trailing *lookback_days* rows from the DB.

        Fallback hierarchy
        ------------------
        * DB error during read/write: log WARNING, fall back to a direct
          provider fetch (no DB write), return whatever the provider yields.
        * Total failure (DB error + provider error): return empty DataFrame
          (CONSTRAINT #4 — never fabricated rows).

        Parameters
        ----------
        symbol:
            Ticker (case-insensitive).
        lookback_days:
            How many calendar days of bars to return (tail-slice of the DB).
        provider:
            Injectable ``MarketDataProvider``; defaults to
            ``data.market_data.get_provider()``.
        """
        symbol = symbol.upper()
        _provider = self._resolve_provider(provider)

        try:
            return self._get_bars_db_path(symbol, lookback_days, _provider)
        except Exception as exc:
            logger.warning(
                "HistoricalStore.get_bars(%s) DB path failed (%s); falling back to live.",
                symbol, exc,
            )
            return self._live_fetch(symbol, lookback_days, _provider)

    # ------------------------------------------------------------------ #
    # Private implementation helpers                                        #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_provider(provider):
        if provider is not None:
            return provider
        try:
            from data.market_data import get_provider
            return get_provider()
        except Exception as exc:
            logger.debug("_resolve_provider: could not load default provider: %s", exc)
            return None

    def _get_bars_db_path(
        self,
        symbol: str,
        lookback_days: int,
        provider,
    ) -> pd.DataFrame:
        """Main code path: DB read → incremental top-up → DB read."""
        from settings import settings  # avoid circular import at module top

        max_date = self.latest_bar_date(symbol)
        today = pd.Timestamp.now(tz=None).normalize()  # midnight, tz-naive

        if max_date is None:
            # First ever fetch for this symbol — full backfill.
            fetch_days = settings.BARS_BACKFILL_DAYS
            logger.info(
                "HistoricalStore: cold-start backfill %d days for %s.",
                fetch_days, symbol,
            )
        else:
            # Delta: business days between max_date and today.
            delta_cal = (today - max_date).days
            if delta_cal <= 0:
                # Already up to date — skip the network round-trip.
                return self._read_from_db(symbol, lookback_days)
            # Request slightly more calendar days to account for weekends/holidays
            # (delta_cal calendar days ≈ delta_cal days of provider lookback).
            fetch_days = max(delta_cal + 5, 7)  # buffer for weekends
            logger.info(
                "HistoricalStore: incremental top-up %d days for %s (last bar: %s).",
                fetch_days, symbol, max_date.date(),
            )

        if provider is not None:
            raw_df = self._live_fetch(symbol, fetch_days, provider)
            if not raw_df.empty:
                self._upsert_bars(symbol, raw_df, source=getattr(provider, "source_name", "yfinance"))

        return self._read_from_db(symbol, lookback_days)

    def _live_fetch(self, symbol: str, lookback_days: int, provider) -> pd.DataFrame:
        """Fetch bars from the provider; return empty DataFrame on any failure."""
        if provider is None:
            logger.warning("HistoricalStore: no provider available for live fetch of %s.", symbol)
            return pd.DataFrame(columns=_DF_COLUMNS)
        try:
            df = provider.get_intraday_bars(symbol, lookback_days=lookback_days)
            if df is None or df.empty:
                return pd.DataFrame(columns=_DF_COLUMNS)
            return self._normalize_shape(df)
        except Exception as exc:
            logger.warning("HistoricalStore: live fetch failed for %s: %s", symbol, exc)
            return pd.DataFrame(columns=_DF_COLUMNS)

    def _upsert_bars(self, symbol: str, df: pd.DataFrame, source: str) -> None:
        """INSERT OR REPLACE rows from *df* into price_bars."""
        now_ts = self._now_utc_iso()
        rows = []
        for ts, row in df.iterrows():
            date_str = pd.Timestamp(ts).strftime("%Y-%m-%d")
            rows.append((
                symbol,
                date_str,
                _float_or_none(row.get("Open")),
                _float_or_none(row.get("High")),
                _float_or_none(row.get("Low")),
                _float_or_none(row.get("Close")),
                _float_or_none(row.get("Adj Close")),
                _int_or_none(row.get("Volume")),
                source,
                now_ts,
            ))
        if not rows:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO price_bars
                    (symbol, date, open, high, low, close, adj_close, volume, source, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        logger.debug("HistoricalStore: upserted %d bars for %s.", len(rows), symbol)

    def _read_from_db(self, symbol: str, lookback_days: int) -> pd.DataFrame:
        """Read the trailing *lookback_days* rows from price_bars for *symbol*."""
        cutoff = (pd.Timestamp.now(tz=None) - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT date, {_SELECT_COLS}
                FROM price_bars
                WHERE symbol = ? AND date >= ?
                ORDER BY date ASC
                """,
                (symbol, cutoff),
            ).fetchall()

        if not rows:
            return pd.DataFrame(columns=_DF_COLUMNS)

        dates = [r[0] for r in rows]
        data = {
            "Open":   [r[1] for r in rows],
            "High":   [r[2] for r in rows],
            "Low":    [r[3] for r in rows],
            "Close":  [r[4] for r in rows],
            # r[5] = adj_close (stored but not in the public shape)
            "Volume": [r[6] for r in rows],
        }
        idx = pd.DatetimeIndex(dates)
        df = pd.DataFrame(data, index=idx)
        df.index = df.index.tz_localize(None)  # enforce tz-naive
        df.index.name = None
        return df

    @staticmethod
    def _normalize_shape(df: pd.DataFrame) -> pd.DataFrame:
        """Standardize a provider DataFrame to the public shape contract.

        Ensures the result has exactly the five columns in ``_DF_COLUMNS``
        (Open/High/Low/Close/Volume), case-insensitively matched, and a
        tz-naive index.  Extra provider columns (e.g. ``Adj Close``) are
        dropped so callers always see the canonical shape.
        """
        # Normalize column names (e.g. "open" → "Open", "adj close" → title but will be dropped)
        rename = {c: c.title() for c in df.columns if c.lower() in {"open", "high", "low", "close", "volume"}}
        df = df.rename(columns=rename)

        # Enforce exactly _DF_COLUMNS — drop any extras (Adj Close, Dividends, etc.)
        present = [c for c in _DF_COLUMNS if c in df.columns]
        df = df[present]

        # Drop timezone info from the index
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        return df


# ------------------------------------------------------------------ #
# Helpers                                                               #
# ------------------------------------------------------------------ #

def _float_or_none(v) -> Optional[float]:
    try:
        f = float(v)
        return None if (f != f) else f  # NaN check
    except (TypeError, ValueError):
        return None


def _int_or_none(v) -> Optional[int]:
    try:
        f = float(v)
        if f != f:
            return None  # NaN
        return int(f)
    except (TypeError, ValueError):
        return None
