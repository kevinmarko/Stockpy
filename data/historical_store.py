"""
Historical Store — Tier 2.3 Phase 1 + Phase 2
==============================================
Persistent OHLCV bar cache and Robinhood account snapshot store backed by
``quant_platform.db``.

Phase 1 — price_bars
    Every run currently re-fetches ~2 years of bars per symbol from yfinance even
    though a bar recorded yesterday will never change.  This phase intercepts that
    fetch, returns cached rows, and tops up only the delta (yesterday → today).

Phase 2 — account_snapshots / account_positions
    Persist Robinhood account snapshots so the GUI can display holdings even when
    no live login is available.  Three-tier read order in
    ``data/robinhood_portfolio.fetch_account_snapshot``: DB → JSON cache → live.

Design
------
* **raw sqlite3 + WAL** — same pattern as ``forecasting/forecast_tracker.py``.
* **Dead-letter resilient** (CONSTRAINT #6): every public method wraps its body
  in try/except; failures log at WARNING and return an empty sentinel.
* **No fabricated data** (CONSTRAINT #4): empty DB + failed live fetch returns an
  empty DataFrame / None / {}; zero-filled or synthetic rows are never returned.
* **Identical shape contract for bars**: ``get_bars()`` returns a tz-naive
  ``DatetimeIndex`` with columns ``[Open, High, Low, Close, Volume]``.
* **AccountSnapshot is the in-memory truth** (CONSTRAINT #1): the DB tables are
  derived FROM the dataclass; the dataclass shape is never modified here.
* **One module, one DB file**: all tables live in ``quant_platform.db`` alongside
  ``trades``, ``iv_history``, ``forecast_errors``.

Tables
------
price_bars          — OHLCV bars keyed by (symbol, date)
account_snapshots   — account-level snapshot (equity, buying power, dividends)
account_positions   — per-symbol positions linked to a snapshot_id FK
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, Optional

import pandas as pd

if TYPE_CHECKING:
    from data.robinhood_portfolio import AccountSnapshot

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# DDL — price_bars (Phase 1)
# ─────────────────────────────────────────────────────────────────────────────

_PRICE_BARS_DDL = """
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

_PRICE_BARS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_price_bars_symbol_date
    ON price_bars (symbol, date)
"""

# ─────────────────────────────────────────────────────────────────────────────
# DDL — account_snapshots + account_positions (Phase 2)
# ─────────────────────────────────────────────────────────────────────────────

_ACCOUNT_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS account_snapshots (
    snapshot_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at      TEXT    NOT NULL,
    buying_power    REAL,
    total_equity    REAL,
    total_dividends REAL,
    source          TEXT    NOT NULL
)
"""

_ACCOUNT_SNAPSHOTS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_acct_snap_ts ON account_snapshots(fetched_at)
"""

_ACCOUNT_POSITIONS_DDL = """
CREATE TABLE IF NOT EXISTS account_positions (
    snapshot_id      INTEGER NOT NULL,
    symbol           TEXT    NOT NULL,
    qty              REAL,
    avg_cost         REAL,
    current_price    REAL,
    market_value     REAL,
    unrealized_pl    REAL,
    dividends_received REAL,
    name             TEXT,
    PRIMARY KEY (snapshot_id, symbol),
    FOREIGN KEY (snapshot_id) REFERENCES account_snapshots(snapshot_id)
)
"""

# Column order returned by SELECT for price_bars reconstruction.
_SELECT_COLS = "open, high, low, close, adj_close, volume"

# The public DataFrame column names — must match DataEngine.fetch_technical_raw().
_DF_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

# Empty DataFrame returned on total failure — correct schema, zero rows.
_EMPTY_HISTORY_DF = pd.DataFrame(
    columns=["fetched_at", "buying_power", "total_equity", "total_dividends"]
)


class HistoricalStore:
    """Persistent OHLCV bar cache and account snapshot store.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file (default ``"quant_platform.db"``).
    """

    def __init__(self, db_path: str = "quant_platform.db") -> None:
        self._db_path = db_path
        self._ensure_tables()

    # ─────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_tables(self) -> None:
        try:
            with self._connect() as conn:
                conn.execute(_PRICE_BARS_DDL)
                conn.execute(_PRICE_BARS_INDEX_DDL)
                conn.execute(_ACCOUNT_SNAPSHOTS_DDL)
                conn.execute(_ACCOUNT_SNAPSHOTS_INDEX_DDL)
                conn.execute(_ACCOUNT_POSITIONS_DDL)
                conn.commit()
        except Exception as exc:
            logger.warning("HistoricalStore._ensure_tables failed: %s", exc)

    @staticmethod
    def _now_utc_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ─────────────────────────────────────────────────────────────────────────
    # Public API — Bars (Phase 1)
    # ─────────────────────────────────────────────────────────────────────────

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
        3. Otherwise request only the delta ``(max_date, today]``.
        4. Upsert every new row via ``INSERT OR REPLACE``.
        5. Return the trailing *lookback_days* rows from the DB.

        Fallback hierarchy
        ------------------
        * DB error: log WARNING, fall back to a direct provider fetch.
        * Total failure (DB error + provider error): return empty DataFrame
          (CONSTRAINT #4 — no fabricated rows).
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

    # ─────────────────────────────────────────────────────────────────────────
    # Public API — Account snapshots (Phase 2)
    # ─────────────────────────────────────────────────────────────────────────

    def save_account_snapshot(self, snapshot: "AccountSnapshot") -> int:
        """Persist *snapshot* and its positions in a single transaction.

        Returns the new ``snapshot_id`` on success, or ``-1`` on any error
        (never raises — CONSTRAINT #6).  The transaction is rolled back on
        any failure so a partial write never corrupts state.
        """
        conn = None
        try:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")

            cursor = conn.execute(
                """
                INSERT INTO account_snapshots
                    (fetched_at, buying_power, total_equity, total_dividends, source)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot.fetched_at.isoformat(),
                    snapshot.buying_power,
                    snapshot.total_equity,
                    snapshot.total_dividends,
                    "robinhood",
                ),
            )
            snapshot_id: int = cursor.lastrowid  # type: ignore[assignment]

            position_rows = [
                (
                    snapshot_id,
                    sym,
                    pos.quantity,
                    pos.average_cost,
                    pos.current_price,
                    pos.market_value,
                    pos.unrealized_pl,
                    pos.dividends_received,
                    pos.name,
                )
                for sym, pos in snapshot.positions.items()
            ]
            conn.executemany(
                """
                INSERT INTO account_positions
                    (snapshot_id, symbol, qty, avg_cost, current_price,
                     market_value, unrealized_pl, dividends_received, name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                position_rows,
            )
            conn.execute("COMMIT")
            logger.info(
                "HistoricalStore: saved account snapshot %d (%d positions).",
                snapshot_id, len(position_rows),
            )
            return snapshot_id

        except Exception as exc:
            if conn is not None:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
            logger.warning("HistoricalStore.save_account_snapshot failed: %s", exc)
            return -1
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def latest_account_snapshot(self) -> Optional["AccountSnapshot"]:
        """Return the most-recently stored ``AccountSnapshot``, or ``None``.

        Reconstructs a fully-typed ``AccountSnapshot`` (including the positions
        dict) from the DB.  Returns ``None`` on empty DB or any error.
        """
        try:
            with self._connect() as conn:
                snap_row = conn.execute(
                    """
                    SELECT snapshot_id, fetched_at, buying_power, total_equity, total_dividends
                    FROM account_snapshots
                    ORDER BY fetched_at DESC
                    LIMIT 1
                    """
                ).fetchone()
                if snap_row is None:
                    return None

                snapshot_id, fetched_at_str, buying_power, total_equity, total_dividends = snap_row

                pos_rows = conn.execute(
                    """
                    SELECT symbol, qty, avg_cost, current_price,
                           market_value, unrealized_pl, dividends_received, name
                    FROM account_positions
                    WHERE snapshot_id = ?
                    """,
                    (snapshot_id,),
                ).fetchall()

            # Reconstruct dataclasses — lazy import avoids circular dependency.
            from data.robinhood_portfolio import AccountSnapshot, PortfolioPosition

            positions: Dict[str, "PortfolioPosition"] = {}
            for row in pos_rows:
                sym, qty, avg_cost, current_price, market_value, unrealized_pl, divs, name = row
                qty = qty or 0.0
                avg_cost = avg_cost or 0.0
                cost_basis = qty * avg_cost
                unrealized_pl_pct = (
                    (unrealized_pl / cost_basis) * 100.0
                    if cost_basis and cost_basis > 0
                    else 0.0
                )
                positions[sym] = PortfolioPosition(
                    symbol=sym,
                    quantity=qty,
                    average_cost=avg_cost,
                    current_price=current_price or 0.0,
                    market_value=market_value or 0.0,
                    unrealized_pl=unrealized_pl or 0.0,
                    unrealized_pl_pct=unrealized_pl_pct,
                    dividends_received=divs or 0.0,
                    name=name or sym,
                )

            fetched_at = datetime.fromisoformat(fetched_at_str)
            return AccountSnapshot(
                positions=positions,
                buying_power=buying_power or 0.0,
                total_equity=total_equity or 0.0,
                total_dividends=total_dividends or 0.0,
                fetched_at=fetched_at,
            )

        except Exception as exc:
            logger.warning("HistoricalStore.latest_account_snapshot failed: %s", exc)
            return None

    def account_snapshot_history(
        self, since: Optional[datetime] = None
    ) -> pd.DataFrame:
        """Return a DataFrame of account-level metrics across all stored snapshots.

        Columns: ``fetched_at``, ``buying_power``, ``total_equity``,
        ``total_dividends``, ordered ascending by ``fetched_at``.

        Returns an empty DataFrame on error (never raises — CONSTRAINT #6).
        Useful for equity-curve panels (out of scope for Phase 2; unlocked here).
        """
        try:
            since_str = since.isoformat() if since is not None else None
            with self._connect() as conn:
                if since_str is not None:
                    rows = conn.execute(
                        """
                        SELECT fetched_at, buying_power, total_equity, total_dividends
                        FROM account_snapshots
                        WHERE fetched_at >= ?
                        ORDER BY fetched_at ASC
                        """,
                        (since_str,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT fetched_at, buying_power, total_equity, total_dividends
                        FROM account_snapshots
                        ORDER BY fetched_at ASC
                        """
                    ).fetchall()

            if not rows:
                return _EMPTY_HISTORY_DF.copy()

            return pd.DataFrame(
                rows,
                columns=["fetched_at", "buying_power", "total_equity", "total_dividends"],
            )

        except Exception as exc:
            logger.warning("HistoricalStore.account_snapshot_history failed: %s", exc)
            return _EMPTY_HISTORY_DF.copy()

    # ─────────────────────────────────────────────────────────────────────────
    # Private implementation helpers — bars
    # ─────────────────────────────────────────────────────────────────────────

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
        today = pd.Timestamp.now(tz=None).normalize()

        if max_date is None:
            fetch_days = settings.BARS_BACKFILL_DAYS
            logger.info(
                "HistoricalStore: cold-start backfill %d days for %s.",
                fetch_days, symbol,
            )
        else:
            delta_cal = (today - max_date).days
            if delta_cal <= 0:
                return self._read_from_db(symbol, lookback_days)
            fetch_days = max(delta_cal + 5, 7)
            logger.info(
                "HistoricalStore: incremental top-up %d days for %s (last bar: %s).",
                fetch_days, symbol, max_date.date(),
            )

        if provider is not None:
            raw_df = self._live_fetch(symbol, fetch_days, provider)
            if not raw_df.empty:
                self._upsert_bars(
                    symbol, raw_df,
                    source=getattr(provider, "source_name", "yfinance"),
                )

        return self._read_from_db(symbol, lookback_days)

    def _live_fetch(self, symbol: str, lookback_days: int, provider) -> pd.DataFrame:
        """Fetch bars from the provider; return empty DataFrame on any failure."""
        if provider is None:
            logger.warning(
                "HistoricalStore: no provider available for live fetch of %s.", symbol
            )
            return pd.DataFrame(columns=_DF_COLUMNS)
        try:
            df = provider.get_intraday_bars(symbol, lookback_days=lookback_days)
            if df is None or df.empty:
                return pd.DataFrame(columns=_DF_COLUMNS)
            return self._normalize_shape(df)
        except Exception as exc:
            logger.warning(
                "HistoricalStore: live fetch failed for %s: %s", symbol, exc
            )
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
        cutoff = (
            pd.Timestamp.now(tz=None) - pd.Timedelta(days=lookback_days)
        ).strftime("%Y-%m-%d")
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
            # r[5] = adj_close (stored but excluded from the public shape)
            "Volume": [r[6] for r in rows],
        }
        idx = pd.DatetimeIndex(dates)
        df = pd.DataFrame(data, index=idx)
        df.index = df.index.tz_localize(None)
        df.index.name = None
        return df

    @staticmethod
    def _normalize_shape(df: pd.DataFrame) -> pd.DataFrame:
        """Standardize a provider DataFrame to the public shape contract."""
        rename = {
            c: c.title()
            for c in df.columns
            if c.lower() in {"open", "high", "low", "close", "volume"}
        }
        df = df.rename(columns=rename)
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        return df


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────────

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
