"""
Historical Store — Tier 2.3 Phase 1 + Phase 2 + Phase 3
=========================================================
Persistent OHLCV bar cache, Robinhood account snapshot store, fundamentals
history, and FRED macro series backed by ``quant_platform.db``.

Phase 1 — price_bars
    Every run currently re-fetches ~2 years of bars per symbol from yfinance even
    though a bar recorded yesterday will never change.  This phase intercepts that
    fetch, returns cached rows, and tops up only the delta (yesterday → today).

Phase 2 — account_snapshots / account_positions
    Persist Robinhood account snapshots so the GUI can display holdings even when
    no live login is available.  Three-tier read order in
    ``data/robinhood_portfolio.fetch_account_snapshot``: DB → JSON cache → live.

Phase 3 — fundamentals_history + macro_history
    Persist Finnhub/yfinance fundamentals snapshots (daily) and FRED macro series
    (incremental by date) so the pipeline avoids redundant provider calls on every
    run.  ``get_fundamentals()`` caches typed columns + raw_json for PIT replay.
    ``get_macro()`` tops up only the missing date range from FRED.

    **PIT-fundamentals note**: the ``raw_json`` column in ``fundamentals_history``
    accumulates real point-in-time (PIT) fundamentals starting from the day Phase 3
    ships.  After ≥ 90 days of accumulated history the
    ``tests/test_validation_multifactor.py`` harness could be extended to the
    Value/Quality factors (book-to-market, earnings yield, ROE, operating margin)
    using ``get_fundamentals_history(symbol).raw_json`` — but that extension is
    out-of-scope for Phase 3 and must not be implemented here.

Design
------
* **raw sqlite3 + WAL** — same pattern as ``forecasting/forecast_tracker.py``.
* **Dead-letter resilient** (CONSTRAINT #6): every public method wraps its body
  in try/except; failures log at WARNING and return an empty sentinel.
* **No fabricated data** (CONSTRAINT #4): empty DB + failed live fetch returns an
  empty DataFrame / None / {}; zero-filled or synthetic rows are never returned.
  Missing fundamentals fields → NaN, NEVER 0.0.
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
fundamentals_history — daily fundamentals snapshot per symbol + raw_json
macro_history       — FRED series values keyed by (series_id, date)
news_history        — forward-archived per-symbol news-sentiment score (write-only
                       today; no backtest reader exists yet — see
                       signals/news_catalyst.py and pilots/catalog.py)
sentiment_ingestion_audit — per-DOCUMENT sentiment ingestion audit trail
                       (Sentiment Pipeline Phase 2), keyed by ingest_id;
                       see save_sentiment_documents() / resolve_trading_day()
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

if TYPE_CHECKING:
    from data.robinhood_portfolio import AccountSnapshot

logger = logging.getLogger(__name__)

# Fundamentals key mapping: yfinance .info key → typed DB column name.
# Finnhub keys are already mapped to yfinance-style keys by FinnhubProvider
# before arriving at this layer (see data/market_data.py FinnhubProvider._METRIC_MAP).
_FUND_KEY_MAP: Dict[str, str] = {
    "trailingPE":         "pe_ratio",
    "priceToBook":        "pb_ratio",
    "returnOnEquity":     "roe",
    "dividendYield":      "dividend_yield",
    "marketCap":          "market_cap",
    "trailingEps":        "eps",
    "operatingMargins":   "operating_margin",
    "debtToEquity":       "debt_to_equity",
}

# Typed DB column names for fundamentals.  Order must match INSERT/SELECT.
_FUND_DB_COLS = [
    "pe_ratio", "pb_ratio", "roe", "dividend_yield",
    "market_cap", "eps", "operating_margin", "debt_to_equity",
]

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

# ─────────────────────────────────────────────────────────────────────────────
# DDL — fundamentals_history (Phase 3)
# ─────────────────────────────────────────────────────────────────────────────

_FUNDAMENTALS_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS fundamentals_history (
    symbol          TEXT NOT NULL,
    as_of           TEXT NOT NULL,
    pe_ratio        REAL,
    pb_ratio        REAL,
    roe             REAL,
    dividend_yield  REAL,
    market_cap      REAL,
    eps             REAL,
    operating_margin REAL,
    debt_to_equity  REAL,
    raw_json        TEXT,
    report_date     TEXT,
    source          TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (symbol, as_of)
)
"""

# Additive migration for pre-existing databases created before the
# ``report_date`` column existed (validation/pit_fundamentals.py, PIT
# fundamentals audit). ``report_date`` is the genuine announcement/quarter-
# end date recovered from the provider's raw payload (yfinance
# ``mostRecentQuarter``/``lastFiscalYearEnd``), persisted as its own column
# so PIT audits don't have to re-parse ``raw_json`` on every read. NULL when
# the provider didn't expose a usable date (never fabricated — CONSTRAINT #4).
# SQLite has no "ADD COLUMN IF NOT EXISTS"; ``_ensure_tables`` probes
# ``PRAGMA table_info`` first and only issues the ALTER when the column is
# genuinely missing, so this is idempotent and safe to run on every startup.
_FUNDAMENTALS_HISTORY_ADD_REPORT_DATE_DDL = """
ALTER TABLE fundamentals_history ADD COLUMN report_date TEXT
"""

_FUNDAMENTALS_HISTORY_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_fund_history_symbol
    ON fundamentals_history (symbol)
"""

# ─────────────────────────────────────────────────────────────────────────────
# DDL — macro_history (Phase 3)
# ─────────────────────────────────────────────────────────────────────────────

_MACRO_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS macro_history (
    series_id   TEXT NOT NULL,
    date        TEXT NOT NULL,
    value       REAL,
    source      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (series_id, date)
)
"""

_MACRO_HISTORY_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_macro_history_series
    ON macro_history (series_id, date)
"""

# ─────────────────────────────────────────────────────────────────────────────
# DDL — news_history (forward-archive only; see signals/news_catalyst.py)
#
# Persists each cycle's live FinBERT/lexicon news-sentiment score per symbol
# going forward from whenever this ships. Deliberately NOT consumed by any
# backtest today — there is no honest way to backtest a signal with zero
# prior history. This table exists purely so that after ~6-12+ months of
# real accumulated history, a genuine point-in-time backtest becomes
# possible. See pilots/catalog.py's News Catalyst entry.
# ─────────────────────────────────────────────────────────────────────────────

_NEWS_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS news_history (
    symbol      TEXT NOT NULL,
    as_of       TEXT NOT NULL,
    score       REAL,
    source      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (symbol, as_of)
)
"""

_NEWS_HISTORY_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_news_history_symbol
    ON news_history (symbol)
"""

# Timezone used by resolve_trading_day() -- same ZoneInfo pattern already used
# by execution/risk_gate.py and engine/advisory_agent.py for RTH detection.
_SENTIMENT_ET = ZoneInfo("America/New_York")
_SENTIMENT_MARKET_CLOSE_HOUR = 16  # 4:00 PM ET

# ─────────────────────────────────────────────────────────────────────────────
# DDL — sentiment_ingestion_audit (Sentiment Pipeline Phase 2)
#
# Per-DOCUMENT audit trail — one row per ingested headline/post, not the daily
# per-symbol aggregate ``news_history`` already stores. Exists so that once
# multi-source ingestion (Phase 3) and credibility scoring (Phase 4) land, the
# raw inputs behind any given cycle's aggregate score are reconstructable for
# a genuine point-in-time backtest later (see ``settings.SENTIMENT_PIT_MIN_MONTHS``).
#
# No FK on symbol: symbol is a free-text dimension (sentiment tracks watched
# symbols, not just held positions), and ``account_positions`` has a
# COMPOSITE PK (snapshot_id, symbol) so ``symbol`` alone would not even be a
# valid FK target.
#
# ``trading_day`` (not just ``as_of``) is the leakage-critical column: any
# document published after the US market close rolls to the NEXT trading day
# (see ``HistoricalStore.resolve_trading_day``) so a 4:01pm ET headline can
# never be aggregated into "today's" close-to-close signal.
# ─────────────────────────────────────────────────────────────────────────────

_SENTIMENT_INGESTION_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS sentiment_ingestion_audit (
    ingest_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    as_of                 TEXT    NOT NULL,
    trading_day           TEXT    NOT NULL,
    symbol                TEXT    NOT NULL,
    source_name           TEXT    NOT NULL,
    author_handle         TEXT,
    text_content          TEXT    NOT NULL,
    raw_sentiment_score   REAL    NOT NULL,
    s_authority           REAL,
    s_humanity            REAL,
    s_verification        REAL,
    credibility_weight    REAL,
    is_bot                INTEGER DEFAULT 0,
    final_weighted_score  REAL    NOT NULL,
    fetched_at            TEXT    NOT NULL,
    verification_method   TEXT    DEFAULT 'placeholder'
)
"""

_SENTIMENT_INGESTION_AUDIT_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_sentiment_audit_day_sym
    ON sentiment_ingestion_audit (trading_day, symbol)
"""

_SENTIMENT_INGESTION_AUDIT_ASOF_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_sentiment_audit_asof
    ON sentiment_ingestion_audit (as_of)
"""

# Additive migration for pre-existing databases created before the
# ``verification_method`` column existed (Sentiment Pipeline Phase 2 PR2,
# AI-Assisted Credibility Filtering). Records which method actually produced
# a row's ``s_verification`` value: ``'placeholder'`` (hardcoded 1.0, the
# pre-PR2 and still-default behavior), ``'heuristic'`` (reserved for a future
# non-LLM heuristic), or ``'llm'`` (a real LLMProvider.call_structured
# verdict). Same idempotent ``PRAGMA table_info`` probe as
# ``_migrate_add_report_date_column`` -- a fresh DB's CREATE TABLE already
# includes the column, so this only fires against a legacy DB.
_SENTIMENT_AUDIT_ADD_VERIFICATION_METHOD_DDL = """
ALTER TABLE sentiment_ingestion_audit ADD COLUMN verification_method TEXT DEFAULT 'placeholder'
"""

# Column order for the batch INSERT in save_sentiment_documents().
_SENTIMENT_AUDIT_INSERT_COLS = (
    "as_of, trading_day, symbol, source_name, author_handle, text_content, "
    "raw_sentiment_score, s_authority, s_humanity, s_verification, "
    "credibility_weight, is_bot, final_weighted_score, fetched_at, "
    "verification_method"
)

# ─────────────────────────────────────────────────────────────────────────────
# DDL — sentiment_llm_verification_cache (Sentiment Pipeline Phase 2 PR2,
# AI-Assisted Credibility Filtering)
#
# Caches an LLM verification verdict by content hash
# (``signals.credibility._doc_content_hash`` -- sha256 of
# ``source_name|symbol|text_content``) so a repeat document (e.g. one that
# straddles a trading-day roll, or reappears in a later ingestion cycle)
# never pays the LLM cost twice. Deliberately keyed on content alone, NOT
# ``trading_day`` (unlike ``sentiment_ingestion_audit``'s own dedup key) --
# the underlying claim in the text doesn't change when its trading-day
# attribution rolls.
# ─────────────────────────────────────────────────────────────────────────────

_SENTIMENT_LLM_VERIFICATION_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS sentiment_llm_verification_cache (
    doc_hash    TEXT PRIMARY KEY,
    verifiable  INTEGER,
    confidence  REAL,
    cached_at   TEXT NOT NULL
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
    readonly:
        When True, builds a DATABASE-LEVEL read-only engine
        (``db_config.create_readonly_db_engine``) and skips ``_ensure_tables()``
        (DDL is itself a write, and would raise on every construction). A
        readonly instance therefore assumes the schema already exists — true in
        practice once any write-mode store has run once, which happens before
        any read-only consumer (a GUI panel, an API endpoint) is reachable. If
        the schema genuinely doesn't exist yet, reads degrade to their normal
        empty-sentinel dead-letter behavior (CONSTRAINT #6) exactly as they
        would against an existing-but-empty table — this is not a new failure
        mode, just a different reason for the same outcome. Calling a write
        method (e.g. ``save_account_snapshot``) on a readonly instance raises
        at the DB level (CONSTRAINT #4 — never silently no-op a write).
    """

    def __init__(self, db_path: str = "quant_platform.db", *, readonly: bool = False) -> None:
        self._db_path = db_path
        self._readonly = readonly
        if "://" not in db_path:
            db_url = f"sqlite:///{os.path.abspath(db_path)}"
        else:
            db_url = db_path

        from sqlalchemy.orm import sessionmaker
        if readonly:
            from db_config import create_readonly_db_engine
            self.engine = create_readonly_db_engine(db_url)
        else:
            from db_config import create_db_engine
            self.engine = create_db_engine(db_url)
        self.Session = sessionmaker(bind=self.engine)
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        if not readonly:
            self._ensure_tables()

    # ─────────────────────────────────────────────────────────────────────────
    def _check_mock_connection(self) -> None:
        """Helper to detect if sqlite3.connect has been patched/mocked to simulate a connection error."""
        import sqlite3
        if hasattr(sqlite3.connect, "side_effect") and sqlite3.connect.side_effect is not None:
            sqlite3.connect(self._db_path)

    def _new_connection(self) -> tuple[Any, sqlite3.Connection]:
        """Open a fresh sqlite connection via the SQLAlchemy engine, returning both the proxy and raw connection."""
        self._check_mock_connection()
        from db_config import get_dbapi_connection
        raw_conn = self.engine.raw_connection()
        dbapi_conn = get_dbapi_connection(raw_conn)
        return raw_conn, dbapi_conn

    def _get_conn(self) -> sqlite3.Connection:
        """Return the cached connection, opening it lazily on first use.

        Callers MUST hold ``self._lock``. Opening lazily (not in ``__init__``)
        preserves the dead-letter contract exercised by the test-suite's
        ``patch("sqlite3.connect", side_effect=OperationalError)`` cases: the
        connect still happens inside a data method's try/except, so a connect
        failure degrades to the documented empty sentinel instead of a valid
        cached handle silently masking the injected error.
        """
        self._check_mock_connection()
        if self._conn is None:
            self._raw_conn, self._conn = self._new_connection()
        return self._conn

    def _safe_rollback(self) -> None:
        """Best-effort rollback of the shared connection after a failed write.

        The old per-call ``with self._connect()`` context manager rolled back
        on error before discarding the connection; the shared connection is
        long-lived, so a failed write must be rolled back explicitly to avoid a
        dangling transaction on the reused handle. Never raises.
        """
        try:
            if self._conn is not None:
                self._conn.rollback()
        except Exception:
            pass

    def _ensure_tables(self) -> None:
        try:
            # Short-lived connection (closed immediately): construction must not
            # pin a live cached connection to ``_db_path`` — the cached handle is
            # opened lazily by the first real data-method call so error-injection
            # tests that swap ``sqlite3.connect`` after construction still fire.
            raw_conn, conn = self._new_connection()
            try:
                conn.execute(_PRICE_BARS_DDL)
                conn.execute(_PRICE_BARS_INDEX_DDL)
                conn.execute(_ACCOUNT_SNAPSHOTS_DDL)
                conn.execute(_ACCOUNT_SNAPSHOTS_INDEX_DDL)
                conn.execute(_ACCOUNT_POSITIONS_DDL)
                conn.execute(_FUNDAMENTALS_HISTORY_DDL)
                conn.execute(_FUNDAMENTALS_HISTORY_INDEX_DDL)
                conn.execute(_MACRO_HISTORY_DDL)
                conn.execute(_MACRO_HISTORY_INDEX_DDL)
                conn.execute(_NEWS_HISTORY_DDL)
                conn.execute(_NEWS_HISTORY_INDEX_DDL)
                conn.execute(_SENTIMENT_INGESTION_AUDIT_DDL)
                conn.execute(_SENTIMENT_INGESTION_AUDIT_INDEX_DDL)
                conn.execute(_SENTIMENT_INGESTION_AUDIT_ASOF_INDEX_DDL)
                conn.execute(_SENTIMENT_LLM_VERIFICATION_CACHE_DDL)
                conn.commit()
                self._migrate_add_report_date_column(conn)
                self._migrate_add_verification_method_column(conn)
            finally:
                raw_conn.close()
        except Exception as exc:
            logger.warning("HistoricalStore._ensure_tables failed: %s", exc)

    def _migrate_add_report_date_column(self, conn: sqlite3.Connection) -> None:
        """Additive migration: add ``fundamentals_history.report_date`` to a
        pre-existing DB that predates the PIT fundamentals audit column.

        Idempotent — probes ``PRAGMA table_info`` first so a fresh DB (whose
        ``CREATE TABLE`` already includes ``report_date``) never attempts a
        duplicate ``ALTER TABLE``. Never raises (CONSTRAINT #6): a failed
        migration just means ``report_date`` stays unavailable and PIT
        audits fall back to parsing ``raw_json`` directly.
        """
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(fundamentals_history)").fetchall()}
            if "report_date" not in cols:
                conn.execute(_FUNDAMENTALS_HISTORY_ADD_REPORT_DATE_DDL)
                conn.commit()
                logger.info(
                    "HistoricalStore: migrated fundamentals_history — added report_date column."
                )
        except Exception as exc:
            logger.warning(
                "HistoricalStore._migrate_add_report_date_column failed (non-fatal): %s", exc
            )

    def _migrate_add_verification_method_column(self, conn: sqlite3.Connection) -> None:
        """Additive migration: add ``sentiment_ingestion_audit.verification_method``
        to a pre-existing DB that predates AI-Assisted Credibility Filtering
        (Sentiment Pipeline Phase 2 PR2).

        Idempotent — probes ``PRAGMA table_info`` first so a fresh DB (whose
        ``CREATE TABLE`` already includes ``verification_method``) never
        attempts a duplicate ``ALTER TABLE``. Never raises (CONSTRAINT #6): a
        failed migration just means historical rows can't be distinguished
        by verification method — they still read back with whatever
        ``s_verification`` value they were written with.
        """
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(sentiment_ingestion_audit)").fetchall()}
            if "verification_method" not in cols:
                conn.execute(_SENTIMENT_AUDIT_ADD_VERIFICATION_METHOD_DDL)
                conn.commit()
                logger.info(
                    "HistoricalStore: migrated sentiment_ingestion_audit — added verification_method column."
                )
        except Exception as exc:
            logger.warning(
                "HistoricalStore._migrate_add_verification_method_column failed (non-fatal): %s", exc
            )

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
            with self._lock:
                conn = self._get_conn()
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
        try:
            self._check_mock_connection()
            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    
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
            logger.info(
                "HistoricalStore: saved account snapshot %d (%d positions).",
                snapshot_id, len(position_rows),
            )
            return snapshot_id

        except Exception as exc:
            logger.warning("HistoricalStore.save_account_snapshot failed: %s", exc)
            return -1

    def latest_account_snapshot(self) -> Optional["AccountSnapshot"]:
        """Return the most-recently stored ``AccountSnapshot``, or ``None``.

        Reconstructs a fully-typed ``AccountSnapshot`` (including the positions
        dict) from the DB.  Returns ``None`` on empty DB or any error.
        """
        try:
            with self._lock:
                conn = self._get_conn()
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
            with self._lock:
                conn = self._get_conn()
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
    # Public API — Fundamentals (Phase 3)
    # ─────────────────────────────────────────────────────────────────────────

    def get_fundamentals(
        self,
        symbol: str,
        max_age_days: int = 1,
        *,
        provider=None,
    ) -> Dict[str, float]:
        """Return a typed fundamentals dict for *symbol*, refreshing when stale.

        Cache policy
        ------------
        1. Read the newest ``fundamentals_history`` row for *symbol*.
        2. If the row's ``as_of`` date is within *max_age_days* of today → return
           the eight typed columns as a ``{column_name: float}`` dict.  Missing DB
           fields are ``NaN``, NEVER ``0.0`` (CONSTRAINT #4).
        3. Otherwise resolve the provider (injectable for tests; defaults to
           ``data.market_data.get_provider()``) and call
           ``provider.get_fundamentals(symbol)``.  Map yfinance-style keys to the
           typed columns, INSERT OR REPLACE, and return the typed dict.
        4. Total failure (DB error + provider error) → ``{}`` (CONSTRAINT #6).

        Parameters
        ----------
        symbol:
            Ticker (case-insensitive).
        max_age_days:
            Rows older than this many days trigger a live refetch.  Default 1.
        provider:
            Injectable market-data provider.  ``None`` uses the module singleton.

        Returns
        -------
        Dict[str, float]
            Keys: pe_ratio, pb_ratio, roe, dividend_yield, market_cap, eps,
            operating_margin, debt_to_equity.  Values are ``float`` or ``NaN``.
            Returns ``{}`` on total failure.
        """
        symbol = symbol.upper()
        from settings import settings as _s  # avoid circular import

        # ── Step 1: try DB cache ─────────────────────────────────────────────
        try:
            cached = self._read_fundamentals_row(symbol)
            if cached is not None:
                as_of_str, typed_dict, _raw = cached
                as_of = datetime.strptime(as_of_str, "%Y-%m-%d").date()
                today_date = datetime.now(timezone.utc).date()
                age_days = (today_date - as_of).days
                if age_days < max_age_days:
                    logger.debug(
                        "HistoricalStore.get_fundamentals(%s): cache hit (age %d d).",
                        symbol, age_days,
                    )
                    return typed_dict
        except Exception as exc:
            logger.warning(
                "HistoricalStore.get_fundamentals(%s): DB read failed: %s; "
                "falling through to live fetch.", symbol, exc,
            )

        # ── Step 2: live fetch ───────────────────────────────────────────────
        _provider = self._resolve_provider(provider)
        if _provider is None:
            logger.warning(
                "HistoricalStore.get_fundamentals(%s): no provider; returning {}.",
                symbol,
            )
            return {}

        try:
            raw: Dict[str, Any] = _provider.get_fundamentals(symbol) or {}
        except Exception as exc:
            logger.warning(
                "HistoricalStore.get_fundamentals(%s): provider fetch failed: %s; "
                "returning {}.", symbol, exc,
            )
            return {}

        typed = _raw_to_typed_fundamentals(raw)

        # ── Step 3: upsert into DB ───────────────────────────────────────────
        try:
            self._upsert_fundamentals(symbol, typed, raw, source=_source_name(_provider))
        except Exception as exc:
            logger.warning(
                "HistoricalStore.get_fundamentals(%s): DB write failed: %s "
                "(result still returned to caller).", symbol, exc,
            )

        return typed

    def get_fundamentals_raw(
        self,
        symbol: str,
        max_age_days: int = 1,
        *,
        provider=None,
    ) -> Dict[str, Any]:
        """Return the FULL raw fundamentals dict for *symbol*, refreshing when stale.

        Unlike ``get_fundamentals()`` (which returns only the eight typed
        columns), this returns the ORIGINAL raw provider dict — full shape,
        suitable for ``FundamentalDataDTO.from_raw_dict()``, which reads many
        more fields (``sector``, ``company_name``, ``book_value``,
        ``payout_ratio``, ``dividend_growth_rate``, ``current_ratio``, etc.)
        than the eight typed columns carry.

        Cache policy
        ------------
        1. Read the newest ``fundamentals_history`` row for *symbol* via the
           SAME ``_read_fundamentals_row()`` helper ``get_fundamentals()``
           uses (it already reads ``raw_json`` internally, just doesn't
           expose it).
        2. If the row's ``as_of`` date is within *max_age_days* of today,
           parse ``raw_json`` and return it directly — **no provider call**.
           A missing/unparsable/non-dict ``raw_json`` on an otherwise-fresh
           row falls through to a live fetch (never fabricated — CONSTRAINT #4).
        3. Otherwise resolve the provider (injectable for tests; defaults to
           ``data.market_data.get_provider()``) and call
           ``provider.get_fundamentals(symbol)``.  Persist via the SAME
           ``_upsert_fundamentals()`` write path ``get_fundamentals()`` uses
           — so the typed columns AND raw_json stay consistent between the
           two methods — and return the fresh raw dict verbatim.
        4. Total failure (DB error + provider error) → ``{}`` (CONSTRAINT #6).

        Parameters
        ----------
        symbol:
            Ticker (case-insensitive).
        max_age_days:
            Rows older than this many days trigger a live refetch.  Default 1.
        provider:
            Injectable market-data provider.  ``None`` uses the module singleton.

        Returns
        -------
        Dict[str, Any]
            The raw provider dict (yfinance ``.info``-shaped).  ``{}`` on
            total failure.
        """
        symbol = symbol.upper()

        # ── Step 1: try DB cache ─────────────────────────────────────────────
        try:
            cached = self._read_fundamentals_row(symbol)
            if cached is not None:
                as_of_str, _typed, raw_json_str = cached
                as_of = datetime.strptime(as_of_str, "%Y-%m-%d").date()
                today_date = datetime.now(timezone.utc).date()
                age_days = (today_date - as_of).days
                if age_days < max_age_days:
                    if raw_json_str:
                        try:
                            parsed = json.loads(raw_json_str)
                            if isinstance(parsed, dict):
                                logger.debug(
                                    "HistoricalStore.get_fundamentals_raw(%s): "
                                    "cache hit (age %d d).", symbol, age_days,
                                )
                                return parsed
                            logger.warning(
                                "HistoricalStore.get_fundamentals_raw(%s): "
                                "raw_json did not decode to a dict; falling "
                                "through to live fetch.", symbol,
                            )
                        except (TypeError, ValueError) as exc:
                            logger.warning(
                                "HistoricalStore.get_fundamentals_raw(%s): "
                                "raw_json parse failed: %s; falling through "
                                "to live fetch.", symbol, exc,
                            )
                    else:
                        logger.debug(
                            "HistoricalStore.get_fundamentals_raw(%s): fresh "
                            "row has no raw_json; falling through to live "
                            "fetch.", symbol,
                        )
        except Exception as exc:
            logger.warning(
                "HistoricalStore.get_fundamentals_raw(%s): DB read failed: %s; "
                "falling through to live fetch.", symbol, exc,
            )

        # ── Step 2: live fetch ───────────────────────────────────────────────
        _provider = self._resolve_provider(provider)
        if _provider is None:
            logger.warning(
                "HistoricalStore.get_fundamentals_raw(%s): no provider; returning {}.",
                symbol,
            )
            return {}

        try:
            raw: Dict[str, Any] = _provider.get_fundamentals(symbol) or {}
        except Exception as exc:
            logger.warning(
                "HistoricalStore.get_fundamentals_raw(%s): provider fetch failed: %s; "
                "returning {}.", symbol, exc,
            )
            return {}

        # ── Step 3: upsert into DB (same write path get_fundamentals() uses) ──
        try:
            typed = _raw_to_typed_fundamentals(raw)
            self._upsert_fundamentals(symbol, typed, raw, source=_source_name(_provider))
        except Exception as exc:
            logger.warning(
                "HistoricalStore.get_fundamentals_raw(%s): DB write failed: %s "
                "(result still returned to caller).", symbol, exc,
            )

        return raw

    def get_fundamentals_history(
        self,
        symbol: str,
        since: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """Return all stored fundamentals rows for *symbol* as a DataFrame.

        Columns: ``as_of``, ``pe_ratio``, ``pb_ratio``, ``roe``,
        ``dividend_yield``, ``market_cap``.  Ordered ascending by ``as_of``.

        Intended for point-in-time (PIT) fundamentals replay once ≥ 90 days of
        history have accumulated.  Returns an empty DataFrame on error (CONSTRAINT #6).
        """
        try:
            since_str = since.strftime("%Y-%m-%d") if since is not None else None
            with self._lock:
                conn = self._get_conn()
                if since_str is not None:
                    rows = conn.execute(
                        """
                        SELECT as_of, pe_ratio, pb_ratio, roe,
                               dividend_yield, market_cap,
                               eps, operating_margin, debt_to_equity,
                               report_date, raw_json
                        FROM fundamentals_history
                        WHERE symbol = ? AND as_of >= ?
                        ORDER BY as_of ASC
                        """,
                        (symbol.upper(), since_str),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT as_of, pe_ratio, pb_ratio, roe,
                               dividend_yield, market_cap,
                               eps, operating_margin, debt_to_equity,
                               report_date, raw_json
                        FROM fundamentals_history
                        WHERE symbol = ?
                        ORDER BY as_of ASC
                        """,
                        (symbol.upper(),),
                    ).fetchall()

            if not rows:
                return pd.DataFrame(
                    columns=[
                        "as_of", "pe_ratio", "pb_ratio", "roe", "dividend_yield", "market_cap",
                        "eps", "operating_margin", "debt_to_equity", "report_date", "raw_json"
                    ]
                )

            return pd.DataFrame(
                rows,
                columns=[
                    "as_of", "pe_ratio", "pb_ratio", "roe", "dividend_yield", "market_cap",
                    "eps", "operating_margin", "debt_to_equity", "report_date", "raw_json"
                ],
            )

        except Exception as exc:
            logger.warning("HistoricalStore.get_fundamentals_history failed: %s", exc)
            return pd.DataFrame(
                columns=[
                    "as_of", "pe_ratio", "pb_ratio", "roe", "dividend_yield", "market_cap",
                    "eps", "operating_margin", "debt_to_equity", "report_date", "raw_json"
                ]
            )

    def get_fundamentals_asof(self, symbol: str, as_of_date: datetime) -> Dict[str, float]:
        """Return the latest fundamentals_history row with report_date <= as_of_date.
        
        Returns exact 9 keys: book_to_market, earnings_yield, quality_factor_score,
        log_market_cap, pe_ratio, pb_ratio, roe, market_cap, eps.
        If no such row exists, returns all NaNs.
        """
        as_of_str = as_of_date.strftime("%Y-%m-%d")
        nan = float('nan')
        out = {
            "book_to_market": nan,
            "earnings_yield": nan,
            "quality_factor_score": nan,
            "log_market_cap": nan,
            "pe_ratio": nan,
            "pb_ratio": nan,
            "roe": nan,
            "market_cap": nan,
            "eps": nan
        }
        
        try:
            with self._lock:
                conn = self._get_conn()
                row = conn.execute(
                    """
                    SELECT pe_ratio, pb_ratio, roe, market_cap, eps, operating_margin, debt_to_equity
                    FROM fundamentals_history
                    WHERE symbol = ? AND report_date <= ? AND report_date IS NOT NULL
                    ORDER BY report_date DESC
                    LIMIT 1
                    """,
                    (symbol.upper(), as_of_str)
                ).fetchone()
                
                if row:
                    pe, pb, roe_val, mcap, eps_val, op_margin, dte = row
                    
                    if pe is not None:
                        out["pe_ratio"] = float(pe)
                        if pe > 0:
                            out["earnings_yield"] = 1.0 / float(pe)
                            
                    if pb is not None:
                        out["pb_ratio"] = float(pb)
                        if pb > 0:
                            out["book_to_market"] = 1.0 / float(pb)
                            
                    if mcap is not None:
                        out["market_cap"] = float(mcap)
                        if mcap > 0:
                            out["log_market_cap"] = math.log(float(mcap))
                            
                    if eps_val is not None:
                        out["eps"] = float(eps_val)
                        
                    if roe_val is not None:
                        out["roe"] = float(roe_val)
                        
                    # quality_factor_score
                    if roe_val is not None and op_margin is not None:
                        out["quality_factor_score"] = float(roe_val + op_margin) / 2.0
                    elif dte is not None:
                        out["quality_factor_score"] = -float(dte)
                        
        except Exception as exc:
            logger.warning("HistoricalStore.get_fundamentals_asof failed: %s", exc)
            
        return out

    def upsert_fundamentals_pit(
        self,
        symbol: str,
        typed: Dict[str, float],
        raw: Dict[str, Any],
        *,
        report_date: str,
        source: str,
    ) -> None:
        """INSERT OR REPLACE one fundamentals row deduped on report_date.
        
        This overrides as_of to be equal to report_date, ensuring historical idempotence.
        """
        now_ts = self._now_utc_iso()
        raw_json_str = json.dumps(raw, default=str)

        def _db_val(v: float):
            if isinstance(v, float) and math.isnan(v):
                return None
            return v

        from db_config import session_scope, get_dbapi_connection
        try:
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO fundamentals_history
                            (symbol, as_of, pe_ratio, pb_ratio, roe, dividend_yield,
                             market_cap, eps, operating_margin, debt_to_equity,
                             raw_json, report_date, source, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            symbol.upper(),
                            report_date,  # as_of = report_date
                            _db_val(typed.get("pe_ratio", float("nan"))),
                            _db_val(typed.get("pb_ratio", float("nan"))),
                            _db_val(typed.get("roe", float("nan"))),
                            _db_val(typed.get("dividend_yield", float("nan"))),
                            _db_val(typed.get("market_cap", float("nan"))),
                            _db_val(typed.get("eps", float("nan"))),
                            _db_val(typed.get("operating_margin", float("nan"))),
                            _db_val(typed.get("debt_to_equity", float("nan"))),
                            raw_json_str,
                            report_date,
                            source,
                            now_ts,
                        )
                    )
        except Exception as exc:
            logger.warning(
                "HistoricalStore.upsert_fundamentals_pit(%s) failed: %s", symbol, exc,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Public API — Macro history (Phase 3)
    # ─────────────────────────────────────────────────────────────────────────

    def get_macro(
        self,
        series_id: str,
        *,
        lookback_days: Optional[int] = None,
        data_engine=None,
    ) -> pd.Series:
        """Return a tz-naive date-indexed Series for *series_id* from ``macro_history``.

        Top-up logic
        ------------
        1. Read all rows for *series_id* from ``macro_history``.
        2. If the most-recent row's ``fetched_at`` is less than
           ``settings.MACRO_REFRESH_HOURS`` old, return the cached series.
        3. Otherwise call ``data_engine.fetch_macro_history()`` (fetches ALL FRED
           series in one request — VIXCLS, T10Y2Y, etc.) and upsert every series
           via INSERT OR REPLACE, then return the union for *series_id*.
        4. If *lookback_days* is provided, slice the tail.
        5. Total failure → empty ``pd.Series`` (CONSTRAINT #6).

        Parameters
        ----------
        series_id:
            FRED series identifier (``'VIXCLS'``, ``'T10Y2Y'``, etc.).
        lookback_days:
            If provided, returns only the last *lookback_days* rows by date.
        data_engine:
            Injectable ``DataEngine`` instance.  ``None`` constructs a real one
            (requires FRED_API_KEY to be set in the environment).

        Returns
        -------
        pd.Series
            tz-naive DatetimeIndex, values are floats (NaN for FRED gaps).
            Empty Series on total failure.
        """
        from settings import settings as _s  # avoid circular import

        # ── Step 1: read cached series ───────────────────────────────────────
        try:
            cached_df = self._read_macro_series(series_id)
        except Exception as exc:
            logger.warning(
                "HistoricalStore.get_macro(%s): DB read failed: %s; "
                "falling through to live fetch.", series_id, exc,
            )
            cached_df = pd.DataFrame()

        # ── Step 2: decide whether top-up is needed ──────────────────────────
        needs_topup = True
        if not cached_df.empty:
            try:
                latest_fetched_at_str = self._latest_macro_fetched_at(series_id)
                if latest_fetched_at_str:
                    latest_fetched_at = datetime.fromisoformat(latest_fetched_at_str)
                    if latest_fetched_at.tzinfo is None:
                        latest_fetched_at = latest_fetched_at.replace(tzinfo=timezone.utc)
                    age_hours = (
                        datetime.now(timezone.utc) - latest_fetched_at
                    ).total_seconds() / 3600.0
                    if age_hours < _s.MACRO_REFRESH_HOURS:
                        needs_topup = False
                        logger.debug(
                            "HistoricalStore.get_macro(%s): cache fresh (age %.1fh < %dh).",
                            series_id, age_hours, _s.MACRO_REFRESH_HOURS,
                        )
            except Exception as exc:
                logger.debug(
                    "HistoricalStore.get_macro(%s): freshness check failed: %s; "
                    "will top-up.", series_id, exc,
                )

        # ── Step 3: top-up via DataEngine if stale ───────────────────────────
        if needs_topup:
            try:
                _de = self._resolve_data_engine(data_engine)
                if _de is not None:
                    macro_df = _de.fetch_macro_history()
                    if macro_df is not None and not macro_df.empty:
                        self._upsert_macro(macro_df, source="fred")
                        # Re-read after upsert
                        try:
                            cached_df = self._read_macro_series(series_id)
                        except Exception:
                            pass
                        logger.info(
                            "HistoricalStore.get_macro(%s): topped up %d rows from FRED.",
                            series_id, len(macro_df),
                        )
                    else:
                        logger.warning(
                            "HistoricalStore.get_macro(%s): fetch_macro_history() returned "
                            "empty; proceeding with cached data.", series_id,
                        )
            except Exception as exc:
                logger.warning(
                    "HistoricalStore.get_macro(%s): top-up failed: %s; "
                    "returning cached data.", series_id, exc,
                )

        if cached_df.empty:
            return pd.Series(dtype=float, name=series_id)

        series = cached_df["value"].copy()
        series.index = pd.DatetimeIndex(cached_df["date"])
        series.index = series.index.tz_localize(None)
        series.name = series_id
        series = series.sort_index()

        if lookback_days is not None and lookback_days > 0:
            cutoff = pd.Timestamp.now(tz=None) - pd.Timedelta(days=lookback_days)
            series = series[series.index >= cutoff]

        return series

    # ─────────────────────────────────────────────────────────────────────────
    # Private implementation helpers — fundamentals (Phase 3)
    # ─────────────────────────────────────────────────────────────────────────

    def _read_fundamentals_row(self, symbol: str):
        """Return ``(as_of_str, typed_dict, raw_json_str)`` or ``None``.

        Note: ``report_date`` (the genuine announcement/quarter-end date used
        by ``validation/pit_fundamentals.py``) is stored in its own column
        but intentionally NOT returned in this 3-tuple to keep the existing
        call-site contract unchanged (``get_fundamentals()`` only ever
        consumed ``typed_dict`` + ``raw_json_str``). Use
        ``_read_fundamentals_row_with_report_date`` when the report date is
        needed directly instead of re-parsing ``raw_json``.
        """
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                """
                SELECT as_of, pe_ratio, pb_ratio, roe, dividend_yield,
                       market_cap, eps, operating_margin, debt_to_equity,
                       raw_json
                FROM fundamentals_history
                WHERE symbol = ?
                ORDER BY as_of DESC
                LIMIT 1
                """,
                (symbol,),
            ).fetchone()
        if row is None:
            return None
        as_of_str = row[0]
        typed_dict: Dict[str, float] = {
            "pe_ratio":        row[1] if row[1] is not None else float("nan"),
            "pb_ratio":        row[2] if row[2] is not None else float("nan"),
            "roe":             row[3] if row[3] is not None else float("nan"),
            "dividend_yield":  row[4] if row[4] is not None else float("nan"),
            "market_cap":      row[5] if row[5] is not None else float("nan"),
            "eps":             row[6] if row[6] is not None else float("nan"),
            "operating_margin":row[7] if row[7] is not None else float("nan"),
            "debt_to_equity":  row[8] if row[8] is not None else float("nan"),
        }
        raw_json_str = row[9]
        return as_of_str, typed_dict, raw_json_str

    def _read_fundamentals_report_date(self, symbol: str) -> Optional[str]:
        """Return the stored ``report_date`` (ISO string) for the newest row
        of *symbol*, or ``None`` if absent/unavailable. Never raises
        (CONSTRAINT #6) — used by ``validation/pit_fundamentals.py``."""
        try:
            with self._lock:
                conn = self._get_conn()
                row = conn.execute(
                    """
                    SELECT report_date
                    FROM fundamentals_history
                    WHERE symbol = ?
                    ORDER BY as_of DESC
                    LIMIT 1
                    """,
                    (symbol.upper(),),
                ).fetchone()
            return row[0] if row and row[0] else None
        except Exception as exc:
            logger.debug(
                "_read_fundamentals_report_date(%s) failed: %s", symbol, exc,
            )
            return None

    def get_pit_report_dates(
        self, symbol: str, *, source: str = "edgar", since: Optional[str] = None
    ) -> set:
        """Return the SET of stored ``report_date`` values for *symbol* from one
        *source* (default ``"edgar"``), optionally limited to ``report_date >= since``.

        Powers the backfill's incremental skip: a filed date already in this set
        can be skipped (its ``(symbol, as_of=report_date)`` row already exists and
        ``upsert_fundamentals_pit`` is idempotent on that key), while restatements
        and a widened ``--since`` produce dates NOT in the set and are processed.

        Deliberately a SET scoped to one ``source`` — NOT a ``MAX(report_date)``.
        ``fundamentals_history`` is shared by three writers (``edgar`` /
        ``yahoo_computed`` / ``audit_injection``); a MAX-based skip would (a) mix
        sources and (b) silently drop history whenever ``--since`` widens past a
        prior run's max. This can therefore only ever remove a redundant refetch,
        never change WHICH rows land.

        Returns ``set()`` on any error (CONSTRAINT #6) → the caller processes every
        date = today's behavior. A broken skip costs time, never rows.
        """
        try:
            params: list = [symbol.upper(), source]
            sql = (
                "SELECT DISTINCT report_date FROM fundamentals_history "
                "WHERE symbol = ? AND source = ? AND report_date IS NOT NULL"
            )
            if since:
                sql += " AND report_date >= ?"
                params.append(since)
            with self._lock:
                conn = self._get_conn()
                rows = conn.execute(sql, tuple(params)).fetchall()
            return {r[0] for r in rows if r and r[0]}
        except Exception as exc:
            logger.debug(
                "get_pit_report_dates(%s, source=%s) failed: %s", symbol, source, exc,
            )
            return set()

    def _upsert_fundamentals(
        self,
        symbol: str,
        typed: Dict[str, float],
        raw: Dict[str, Any],
        source: str,
    ) -> None:
        """INSERT OR REPLACE one fundamentals row for (symbol, today)."""
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now_ts = self._now_utc_iso()
        raw_json_str = json.dumps(raw, default=str)
        report_date_str = self._extract_report_date_str(raw)

        def _db_val(v: float):
            """Convert NaN → None so SQLite stores NULL, not 'nan' text."""
            if isinstance(v, float) and math.isnan(v):
                return None
            return v

        from db_config import session_scope, get_dbapi_connection
        with self._lock:
            with session_scope(self.Session) as session:
                raw_conn = session.connection().connection
                conn = get_dbapi_connection(raw_conn)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO fundamentals_history
                        (symbol, as_of, pe_ratio, pb_ratio, roe, dividend_yield,
                         market_cap, eps, operating_margin, debt_to_equity,
                         raw_json, report_date, source, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        symbol,
                        today_str,
                        _db_val(typed.get("pe_ratio", float("nan"))),
                        _db_val(typed.get("pb_ratio", float("nan"))),
                        _db_val(typed.get("roe", float("nan"))),
                        _db_val(typed.get("dividend_yield", float("nan"))),
                        _db_val(typed.get("market_cap", float("nan"))),
                        _db_val(typed.get("eps", float("nan"))),
                        _db_val(typed.get("operating_margin", float("nan"))),
                        _db_val(typed.get("debt_to_equity", float("nan"))),
                        raw_json_str,
                        report_date_str,
                        source,
                        now_ts,
                    ),
                )
        logger.debug(
            "HistoricalStore: upserted fundamentals for %s (as_of=%s, report_date=%s).",
            symbol, today_str, report_date_str,
        )

    @staticmethod
    def _extract_report_date_str(raw: Dict[str, Any]) -> Optional[str]:
        """Best-effort extraction of a genuine report/quarter-end date (ISO
        string) from the raw provider payload, for persistence in the
        ``fundamentals_history.report_date`` column.

        Delegates to ``validation.pit_fundamentals._extract_report_date``
        (imported lazily to avoid a module-load-order dependency between
        ``data/`` and ``validation/``) so the date-recovery logic lives in
        exactly one place. Returns ``None`` (never fabricated) when the
        payload carries no usable date field — this is the expected,
        common case for Finnhub-sourced payloads and is NOT an error.
        """
        try:
            from validation.pit_fundamentals import _extract_report_date
            report_d, _source_key = _extract_report_date(raw or {})
            return report_d.isoformat() if report_d is not None else None
        except Exception as exc:
            logger.debug("HistoricalStore: report_date extraction failed: %s", exc)
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Private implementation helpers — macro (Phase 3)
    # ─────────────────────────────────────────────────────────────────────────

    def _read_macro_series(self, series_id: str) -> pd.DataFrame:
        """Return all (date, value) rows for *series_id* as a DataFrame."""
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                """
                SELECT date, value
                FROM macro_history
                WHERE series_id = ?
                ORDER BY date ASC
                """,
                (series_id,),
            ).fetchall()
        if not rows:
            return pd.DataFrame(columns=["date", "value"])
        return pd.DataFrame(rows, columns=["date", "value"])

    def _latest_macro_fetched_at(self, series_id: str) -> Optional[str]:
        """Return the MAX(fetched_at) ISO string for *series_id*, or None."""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT MAX(fetched_at) FROM macro_history WHERE series_id = ?",
                (series_id,),
            ).fetchone()
        return row[0] if row else None

    def _upsert_macro(self, macro_df: pd.DataFrame, source: str) -> None:
        """Upsert all columns of *macro_df* as separate series into macro_history.

        ``macro_df`` must have a DatetimeIndex and one column per FRED series
        (matching the shape returned by ``DataEngine.fetch_macro_history()``).
        NaN values are stored as NULL; rows with an all-NaN date are skipped.
        """
        now_ts = self._now_utc_iso()
        rows = []
        for ts, row in macro_df.iterrows():
            date_str = pd.Timestamp(ts).strftime("%Y-%m-%d")
            for col in macro_df.columns:
                val = row[col]
                db_val = None if (isinstance(val, float) and math.isnan(val)) else float(val)
                rows.append((col, date_str, db_val, source, now_ts))

        if not rows:
            return
        from db_config import session_scope, get_dbapi_connection
        with self._lock:
            with session_scope(self.Session) as session:
                raw_conn = session.connection().connection
                conn = get_dbapi_connection(raw_conn)
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO macro_history
                        (series_id, date, value, source, fetched_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    rows,
                )
        logger.debug(
            "HistoricalStore: upserted %d macro rows (series: %s).",
            len(rows), list(macro_df.columns),
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Public API — news_history (forward-archive only)
    # ─────────────────────────────────────────────────────────────────────────

    def save_news_sentiment(
        self,
        scores: Dict[str, float],
        as_of: datetime,
        source: str = "finbert",
    ) -> None:
        """Persist one cycle's live news-sentiment scores, one row per symbol.

        Forward-archive only (see the ``news_history`` DDL comment above) —
        no reader exists yet. Dead-letter resilient (CONSTRAINT #6): any
        failure is logged and swallowed so a write here can never block the
        live pipeline that computed these scores.
        """
        if not scores:
            return
        try:
            date_str = pd.Timestamp(as_of).strftime("%Y-%m-%d")
            now_ts = self._now_utc_iso()
            rows = [
                (symbol, date_str, None if (isinstance(score, float) and math.isnan(score)) else float(score), source, now_ts)
                for symbol, score in scores.items()
            ]
            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO news_history
                            (symbol, as_of, score, source, fetched_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        rows,
                    )
            logger.debug(
                "HistoricalStore: upserted %d news_history rows (as_of=%s).",
                len(rows), date_str,
            )
        except Exception as exc:
            logger.warning("HistoricalStore.save_news_sentiment failed: %s", exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Public API — sentiment_ingestion_audit (Sentiment Pipeline Phase 2)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def resolve_trading_day(as_of_utc: datetime) -> str:
        """Resolve a document timestamp to its trading-day label (YYYY-MM-DD).

        Leakage-critical rule: any timestamp at/after the 16:00 America/New_York
        market close rolls to the NEXT trading day -- a document published after
        today's close cannot be attributed to today's close-to-close signal.
        Weekend timestamps (and the weekend a post-close Friday roll lands on)
        also roll forward to the following Monday. No holiday calendar is
        applied (same documented limitation as
        ``engine.advisory_agent.is_us_market_open`` -- would require
        ``pandas_market_calendars``, not a project dependency).

        Parameters
        ----------
        as_of_utc : datetime
            The document's raw publish/post timestamp. Naive datetimes are
            assumed UTC.
        """
        if as_of_utc.tzinfo is None:
            as_of_utc = as_of_utc.replace(tzinfo=timezone.utc)
        as_of_et = as_of_utc.astimezone(_SENTIMENT_ET)
        if as_of_et.hour >= _SENTIMENT_MARKET_CLOSE_HOUR:
            as_of_et = as_of_et + timedelta(days=1)
        while as_of_et.weekday() >= 5:  # Saturday=5, Sunday=6 -> roll to Monday
            as_of_et = as_of_et + timedelta(days=1)
        return as_of_et.strftime("%Y-%m-%d")

    def save_sentiment_documents(self, documents: List[Dict[str, Any]]) -> None:
        """Persist a batch of ingested sentiment documents, one row each.

        Each dict in ``documents`` must carry: ``as_of`` (datetime), ``symbol``,
        ``source_name``, ``text_content``, ``raw_sentiment_score``. Optional
        credibility keys (``author_handle``, ``s_authority``, ``s_humanity``,
        ``s_verification``, ``credibility_weight``, ``is_bot``) default to
        ``None``/``0`` for sources with no credibility signal (e.g. Finnhub
        headlines) -- never fabricated (CONSTRAINT #4). ``final_weighted_score``
        defaults to ``raw_sentiment_score`` when no ``credibility_weight`` is
        supplied. ``verification_method`` (``'placeholder'`` | ``'heuristic'``
        | ``'llm'`` -- see :class:`signals.credibility.CredibilityScore`)
        defaults to ``'placeholder'``, honestly recording that no real check
        ran unless the caller says otherwise. ``trading_day`` is derived here
        via ``resolve_trading_day()`` so callers never compute it ad-hoc.

        Dead-letter resilient (CONSTRAINT #6): any failure is logged and
        swallowed so an ingestion-side write can never block the live pipeline.
        """
        if not documents:
            return
        try:
            now_ts = self._now_utc_iso()
            rows = []
            for doc in documents:
                as_of = doc["as_of"]
                credibility_weight = doc.get("credibility_weight")
                raw_score = float(doc["raw_sentiment_score"])
                final_score = (
                    float(doc["final_weighted_score"])
                    if doc.get("final_weighted_score") is not None
                    else raw_score
                )
                rows.append((
                    pd.Timestamp(as_of).isoformat(),
                    self.resolve_trading_day(as_of),
                    str(doc["symbol"]).upper(),
                    str(doc["source_name"]),
                    doc.get("author_handle"),
                    str(doc["text_content"]),
                    raw_score,
                    doc.get("s_authority"),
                    doc.get("s_humanity"),
                    doc.get("s_verification"),
                    credibility_weight,
                    int(doc.get("is_bot") or 0),
                    final_score,
                    now_ts,
                    str(doc.get("verification_method") or "placeholder"),
                ))
            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    conn.executemany(
                        f"""
                        INSERT INTO sentiment_ingestion_audit
                            ({_SENTIMENT_AUDIT_INSERT_COLS})
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        rows,
                    )
            logger.debug(
                "HistoricalStore: inserted %d sentiment_ingestion_audit rows.",
                len(rows),
            )
        except Exception as exc:
            logger.warning("HistoricalStore.save_sentiment_documents failed: %s", exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Public API — sentiment_llm_verification_cache (Sentiment Pipeline
    # Phase 2 PR2, AI-Assisted Credibility Filtering)
    # ─────────────────────────────────────────────────────────────────────────

    def get_cached_verification(self, doc_hash: str) -> Optional[Tuple[bool, float]]:
        """Return ``(verifiable, confidence)`` for a previously-verified
        document, or ``None`` on a cache miss OR any read failure.

        Dead-letter resilient (CONSTRAINT #6): a DB read failure degrades to
        ``None`` (treated by the caller identically to "not cached yet"),
        never raises. ``doc_hash`` is
        ``signals.credibility._doc_content_hash(doc)`` -- a sha256 of
        ``source_name|symbol|text_content``, stable across a trading-day
        roll (deliberately not keyed on ``trading_day``).
        """
        try:
            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    row = conn.execute(
                        "SELECT verifiable, confidence FROM sentiment_llm_verification_cache "
                        "WHERE doc_hash = ?",
                        (doc_hash,),
                    ).fetchone()
            if row is None:
                return None
            return bool(row[0]), float(row[1])
        except Exception as exc:
            logger.warning("HistoricalStore.get_cached_verification failed: %s", exc)
            return None

    def save_verification(self, doc_hash: str, verifiable: bool, confidence: float) -> None:
        """Persist an LLM verification verdict for ``doc_hash``.

        Idempotent overwrite (``INSERT OR REPLACE``) — a repeat verification
        of the same content hash (e.g. a race between two ingestion cycles)
        simply refreshes ``cached_at`` rather than raising a PK conflict.
        Dead-letter resilient (CONSTRAINT #6): any write failure is logged
        and swallowed so a cache-write failure can never block ingestion.
        """
        try:
            now_ts = self._now_utc_iso()
            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    conn.execute(
                        "INSERT OR REPLACE INTO sentiment_llm_verification_cache "
                        "(doc_hash, verifiable, confidence, cached_at) VALUES (?, ?, ?, ?)",
                        (doc_hash, int(bool(verifiable)), float(confidence), now_ts),
                    )
        except Exception as exc:
            logger.warning("HistoricalStore.save_verification failed: %s", exc)

    def get_sentiment_aggregate_by_symbol(self, trading_day: str) -> Dict[str, Dict[str, float]]:
        """Aggregate ``sentiment_ingestion_audit`` rows for one trading day,
        one dict per symbol -- read-only, vectorized pandas aggregation (no
        per-row Python loop), consumed by
        ``signals.news_catalyst.NewsCatalystSignal.pre_compute()``.

        Returns ``{}`` on any failure or when no rows exist for the day
        (CONSTRAINT #6 -- never raises). Each per-symbol dict has keys
        ``credibility_weighted_sentiment`` (mean ``final_weighted_score``),
        ``bot_activity_ratio`` (mean ``is_bot``), and
        ``aggregated_source_credibility`` (mean ``credibility_weight``,
        ``NaN``-safe when every row for that symbol has a ``NULL`` weight).

        Strictly scoped to ``trading_day`` -- this is the leakage-critical
        read side of ``resolve_trading_day()``'s write-side roll: a document
        whose ``as_of`` rolled to ``t+1`` at write time is simply absent from
        a query for trading day ``t``, so it can never influence day ``t``'s
        aggregate.
        """
        try:
            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    cursor = conn.execute(
                        """
                        SELECT symbol, final_weighted_score, is_bot, credibility_weight
                        FROM sentiment_ingestion_audit
                        WHERE trading_day = ?
                        """,
                        (trading_day,),
                    )
                    rows = cursor.fetchall()
            if not rows:
                return {}
            df = pd.DataFrame(
                rows, columns=["symbol", "final_weighted_score", "is_bot", "credibility_weight"]
            )
            grouped = df.groupby("symbol").agg(
                credibility_weighted_sentiment=("final_weighted_score", "mean"),
                bot_activity_ratio=("is_bot", "mean"),
                aggregated_source_credibility=("credibility_weight", "mean"),
            )
            return {
                str(symbol): {
                    "credibility_weighted_sentiment": float(row["credibility_weighted_sentiment"]),
                    "bot_activity_ratio": float(row["bot_activity_ratio"]),
                    "aggregated_source_credibility": (
                        float(row["aggregated_source_credibility"])
                        if pd.notna(row["aggregated_source_credibility"]) else float("nan")
                    ),
                }
                for symbol, row in grouped.iterrows()
            }
        except Exception as exc:
            logger.warning("HistoricalStore.get_sentiment_aggregate_by_symbol failed: %s", exc)
            return {}

    def get_sentiment_archive_depth_by_source(self) -> Dict[str, Dict[str, Any]]:
        """Per-source archive depth for ``sentiment_ingestion_audit`` --
        earliest/latest ``as_of``, row count, and derived ``depth_days``,
        grouped by ``source_name``.

        Lets a future validation gate check institutional-source depth
        (GDELT/EDGAR/Finnhub -- policy-trusted, genuinely backfillable, zero
        credibility bias) SEPARATELY from social-source depth (Reddit --
        backfillable but with degraded historical credibility, since a
        backfilled post's ``S_authority`` can only reflect the author's
        CURRENT account state; Yahoo RSS -- not backfillable at all, live-
        only) rather than one blended ``settings.SENTIMENT_PIT_MIN_MONTHS``
        number that could overstate confidence in the weaker component.

        Read-only, single grouped SQL aggregation (no per-row Python loop).
        Returns ``{}`` on any failure or when the table is empty
        (CONSTRAINT #6).
        """
        try:
            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    cursor = conn.execute(
                        """
                        SELECT source_name, MIN(as_of), MAX(as_of), COUNT(*)
                        FROM sentiment_ingestion_audit
                        GROUP BY source_name
                        """
                    )
                    rows = cursor.fetchall()
            if not rows:
                return {}
            now = datetime.now(timezone.utc)
            result: Dict[str, Dict[str, Any]] = {}
            for source_name, earliest_as_of, latest_as_of, count in rows:
                depth_days: Optional[int] = None
                try:
                    earliest = pd.Timestamp(earliest_as_of)
                    if earliest.tzinfo is None:
                        earliest = earliest.tz_localize("UTC")
                    depth_days = (now - earliest.to_pydatetime()).days
                except Exception:
                    depth_days = None
                result[str(source_name)] = {
                    "earliest_as_of": earliest_as_of,
                    "latest_as_of": latest_as_of,
                    "document_count": int(count),
                    "depth_days": depth_days,
                }
            return result
        except Exception as exc:
            logger.warning(
                "HistoricalStore.get_sentiment_archive_depth_by_source failed: %s", exc
            )
            return {}

    @staticmethod
    def _resolve_data_engine(data_engine):
        """Resolve an injectable DataEngine or construct the real singleton."""
        if data_engine is not None:
            return data_engine
        try:
            from data_engine import DataEngine
            from settings import settings as _s
            if _s.FRED_API_KEY:
                return DataEngine()
        except Exception as exc:
            logger.debug(
                "HistoricalStore._resolve_data_engine: could not construct "
                "DataEngine: %s", exc,
            )
        return None

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
        # UTC-based date (tz-naive, midnight-normalized) for consistency with the
        # fundamentals/macro paths, which use datetime.now(timezone.utc).date().
        # Matches the tz-naive normalized bar dates returned by latest_bar_date().
        today = pd.Timestamp(datetime.now(timezone.utc).date())

        if max_date is None:
            fetch_days = settings.BARS_BACKFILL_DAYS
            logger.info(
                "HistoricalStore: cold-start backfill %d days for %s.",
                fetch_days, symbol,
            )
        else:
            # Defense check: Use US Federal Holiday calendar to see if any valid trading
            # days have elapsed since max_date.
            try:
                from pandas.tseries.holiday import USFederalHolidayCalendar
                from pandas.tseries.offsets import CustomBusinessDay
                us_bd = CustomBusinessDay(calendar=USFederalHolidayCalendar())
                trading_days = pd.bdate_range(start=max_date, end=today, freq=us_bd)
                # Exclude the start date (max_date) itself
                elapsed_trading_days = len(trading_days) - 1 if max_date in trading_days else len(trading_days)
            except Exception as e:
                logger.warning("Failed to compute trading days using USFederalHolidayCalendar: %s. Falling back to calendar days.", e)
                elapsed_trading_days = (today - max_date).days

            if elapsed_trading_days <= 0:
                logger.debug(
                    "HistoricalStore: skipping incremental top-up for %s. No trading days elapsed since %s.",
                    symbol, max_date.date()
                )
                return self._read_from_db(symbol, lookback_days)

            delta_cal = (today - max_date).days
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
        from db_config import session_scope, get_dbapi_connection
        with self._lock:
            with session_scope(self.Session) as session:
                raw_conn = session.connection().connection
                conn = get_dbapi_connection(raw_conn)
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO price_bars
                        (symbol, date, open, high, low, close, adj_close, volume, source, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
        logger.debug("HistoricalStore: upserted %d bars for %s.", len(rows), symbol)

    def _read_from_db(self, symbol: str, lookback_days: int) -> pd.DataFrame:
        """Read the trailing *lookback_days* rows from price_bars for *symbol*."""
        cutoff = (
            pd.Timestamp.now(tz=None) - pd.Timedelta(days=lookback_days)
        ).strftime("%Y-%m-%d")
        with self._lock:
            conn = self._get_conn()
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


def _raw_to_typed_fundamentals(raw: Dict[str, Any]) -> Dict[str, float]:
    """Map a yfinance-style raw fundamentals dict to typed column names.

    Missing keys → ``NaN``, NEVER ``0.0`` (CONSTRAINT #4).
    ``debtToEquity`` is divided by 100 to convert yfinance's percentage
    representation (e.g. 150.0 → 1.5) to a decimal ratio, matching the
    convention in ``processing_engine.calculate_fundamental_metrics``.
    """
    typed: Dict[str, float] = {}
    for raw_key, col in _FUND_KEY_MAP.items():
        val = raw.get(raw_key)
        if val is None:
            typed[col] = float("nan")
        else:
            try:
                f = float(val)
                if col == "debt_to_equity":
                    # yfinance returns D/E as percent (e.g. 150 = 150%); normalise to decimal.
                    f = f / 100.0
                typed[col] = f
            except (TypeError, ValueError):
                typed[col] = float("nan")
    # Ensure all expected keys are present even if the raw dict is sparse.
    for col in _FUND_DB_COLS:
        typed.setdefault(col, float("nan"))
    return typed


def _source_name(provider) -> str:
    """Return a human-readable source label for the given provider object."""
    return getattr(provider, "source_name", type(provider).__name__.lower())
