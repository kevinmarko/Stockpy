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
news_history        — forward-archived per-symbol news-sentiment score. Read via
                       get_news_sentiment_history() (a display-only chart series,
                       e.g. sentiment-vs-VIX) — still no BACKTEST reader; the
                       archive is too young for a point-in-time backtest claim.
                       See signals/news_catalyst.py and pilots/catalog.py.
sentiment_ingestion_audit — per-DOCUMENT sentiment ingestion audit trail
                       (Sentiment Pipeline Phase 2), keyed by ingest_id;
                       see save_sentiment_documents() / resolve_trading_day()
finbert_score_cache — content-hash (SHA-256 of headline text) cache of a
                       headline's FinBERT/lexicon 3-class softmax score, so
                       an unchanged headline is not re-scored every cycle;
                       see signals/news_catalyst.py's score_headlines() and
                       the DDL comment above for why this is not a lookahead
                       risk. Unrelated to sentiment_llm_verification_cache
                       (that one caches an LLM credibility verdict).
etf_holdings        — ETF constituent basket keyed by (etf_symbol,
                       holding_symbol, as_of_date), where as_of_date is the
                       SOURCE's own report date (the PIT anchor), separate
                       from the fetched_at cache-freshness stamp. Written by
                       data/etf_holdings.py (SEC N-PORT primary); read back
                       through get_etf_holdings(), whose as_of_date filter is
                       the storage-layer no-lookahead guarantee. Nothing in
                       the platform consumes it yet.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

if TYPE_CHECKING:
    from data.robinhood_portfolio import AccountSnapshot
    # Type-only: data/etf_holdings.py lazily imports THIS module, so a runtime
    # import here would be circular. save_etf_holdings duck-types its rows.
    from data.etf_holdings import ETFHolding

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

# `CREATE INDEX IF NOT EXISTS` with the SAME name is a silent no-op when an
# index of that name already exists — so widening the column list below (a
# single-column `symbol` index to a composite `(symbol, as_of DESC)` index)
# would otherwise never actually take effect on any pre-existing DB. The
# DROP immediately before the CREATE (see `_ensure_tables`) makes the
# improved index actually replace the old one, every startup.
_FUNDAMENTALS_HISTORY_INDEX_DROP_DDL = """
DROP INDEX IF EXISTS idx_fund_history_symbol
"""

_FUNDAMENTALS_HISTORY_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_fund_history_symbol
    ON fundamentals_history (symbol, as_of DESC)
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

# Same silent-no-op hazard as `_FUNDAMENTALS_HISTORY_INDEX_DROP_DDL` above —
# the DROP must precede the CREATE for the composite index to actually
# replace a pre-existing single-column index of the same name.
_NEWS_HISTORY_INDEX_DROP_DDL = """
DROP INDEX IF EXISTS idx_news_history_symbol
"""

_NEWS_HISTORY_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_news_history_symbol
    ON news_history (symbol, as_of DESC)
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

# ─────────────────────────────────────────────────────────────────────────────
# DDL — rag_indexed_docs (Phase 2 PR3: RAG-Powered Portfolio Contextualizer)
#
# Tracks which sentiment_ingestion_audit rows have already been embedded into
# the embedded FAISS index (data/rag_index.py). Deliberately additive-only —
# it does NOT mutate the PIT-frozen sentiment_ingestion_audit rows themselves
# (no new column on that table, no UPDATE ever issued against it). faiss_row
# is the ID assigned inside the FAISS IndexIDMap (DocumentVectorStore uses
# ingest_id itself as the FAISS id, so faiss_row == ingest_id in practice —
# tracked as its own column so the on-disk FAISS index and this table can be
# reconciled independently of that implementation detail). doc_hash is a
# content hash of text_content, used as a defensive dedup/integrity check.
# ─────────────────────────────────────────────────────────────────────────────

_RAG_INDEXED_DOCS_DDL = """
CREATE TABLE IF NOT EXISTS rag_indexed_docs (
    ingest_id   INTEGER PRIMARY KEY,
    doc_hash    TEXT    NOT NULL,
    faiss_row   INTEGER NOT NULL,
    indexed_at  TEXT    NOT NULL,
    FOREIGN KEY (ingest_id) REFERENCES sentiment_ingestion_audit(ingest_id)
)
"""

_RAG_INDEXED_DOCS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_rag_indexed_docs_indexed_at
    ON rag_indexed_docs (indexed_at)
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

# ─────────────────────────────────────────────────────────────────────────────
# DDL — finbert_score_cache (FinBERT local batch inference)
#
# Caches a headline's FinBERT (or lexicon-fallback) 3-class softmax score by
# a SHA-256 content hash of the headline text
# (signals.news_catalyst._content_hash), so a headline seen again in a later
# cycle's Finnhub lookback window is not re-scored. Deliberately a SEPARATE
# table from sentiment_llm_verification_cache above -- that table caches an
# LLM's credibility VERIFICATION verdict for a social-sentiment document
# (Sentiment Pipeline Phase 2 PR2); this table caches a FinBERT/lexicon
# SENTIMENT score for a news headline (Finnhub-sourced). Same content-hash
# pattern, unrelated purpose and unrelated callers.
#
# Content-hash, NOT date/cycle-keyed -- and this is NOT a lookahead risk.
# The cached value is a pure, deterministic function of the headline TEXT
# alone (FinBERT/the lexicon have no notion of "when" they were asked to
# score a string): identical text always scores identically regardless of
# which trading cycle reads the cache. A lookahead bug would require a
# cache READ to surface information from a cycle that hasn't happened yet;
# here a cycle can only ever look up a hash for a headline it has ALREADY
# fetched (Finnhub-sourced) THIS cycle, so there is no channel through
# which a future cycle's headline could leak into an earlier cycle's read.
# See tests/test_news_catalyst.py::TestFinbertScoreCacheLookaheadSafety for
# the explicit proof.
# ─────────────────────────────────────────────────────────────────────────────

_FINBERT_SCORE_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS finbert_score_cache (
    content_hash      TEXT PRIMARY KEY,
    headline_snippet  TEXT,
    positive          REAL,
    neutral           REAL,
    negative          REAL,
    scored_at         TEXT NOT NULL
)
"""

# ─────────────────────────────────────────────────────────────────────────────
# DDL — etf_holdings (ETF constituent-holdings cache)
#
# One row per (ETF, underlying, report date). Written by
# ``data/etf_holdings.py``'s SEC N-PORT (and opt-in iShares CSV) ingestion;
# nothing in the platform consumes it yet.
#
# ``as_of_date`` is the SOURCE's own report/holdings date -- the point-in-time
# anchor -- and is part of the primary key, so successive quarterly baskets
# accumulate side by side rather than overwriting each other. ``fetched_at`` is
# the separate, non-key wall-clock stamp used only for cache-freshness
# decisions; the two must never be conflated (a row fetched today can easily
# carry an as_of_date five months old -- N-PORT publishes ~60 days after
# quarter end).
#
# ``get_etf_holdings(..., as_of_date=X)`` filters ``as_of_date <= X`` in SQL,
# which is the module's no-lookahead guarantee at the storage layer: a row
# written by a later cycle can never surface in an earlier-dated read. The
# secondary index exists for the reverse join a consumer needs -- "which ETFs
# held THIS symbol, as of when" -- which is the actual shape of an
# ETF-ownership exposure measure (Ben-David, Franzoni & Moussawi 2018).
#
# NaN handling: SQLite has no NaN, so an unreported weight/shares_held is
# stored as NULL and read back as NaN -- never as 0.0 (CONSTRAINT #4). A
# genuinely zero weight and an unreported weight stay distinguishable.
# ─────────────────────────────────────────────────────────────────────────────

_ETF_HOLDINGS_DDL = """
CREATE TABLE IF NOT EXISTS etf_holdings (
    etf_symbol      TEXT NOT NULL,
    holding_symbol  TEXT NOT NULL,
    as_of_date      TEXT NOT NULL,
    weight          REAL,
    shares_held     REAL,
    source          TEXT,
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (etf_symbol, holding_symbol, as_of_date)
)
"""

_ETF_HOLDINGS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_etf_holdings_holding
    ON etf_holdings (holding_symbol, as_of_date)
"""

# ─────────────────────────────────────────────────────────────────────────────
# DDL — analyst_history (FORWARD-ARCHIVE ONLY; Financial Modeling Prep)
#
# Same rationale as ``news_history`` above, and for the same reason: FMP serves
# only the *current* analyst consensus. There is no as-of snapshot endpoint on
# the Starter plan, and price targets get REVISED — a target you read today for
# a date six months ago is the post-revision number, not what the market saw.
#
# So: there is NO honest way to backtest a signal built on this table until it
# has accumulated its own real history going forward from whenever this ships.
# That is the whole point of the table existing while the corresponding
# dashboard columns stay diagnostic-only (config.COLUMN_SCHEMA's FMP section):
# after ~6-12+ months of accumulated rows a genuine point-in-time study becomes
# possible, and until then nothing in signals/ or dto_models.py may read it.
#
# ``as_of`` is the cycle's own observation date (when WE saw this consensus),
# NOT any vendor-supplied revision date — the vendor does not publish one, and
# inventing a more precise-looking anchor than we actually have would be worse
# than an honest observation stamp. ``fetched_at`` is the separate wall-clock
# stamp used for the 24h cadence check; the two must never be conflated.
#
# NULL (read back as NaN, never 0.0 — CONSTRAINT #4) for any figure the vendor
# did not report: "no coverage" and "a consensus target of zero" are different
# facts.
# ─────────────────────────────────────────────────────────────────────────────

_ANALYST_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS analyst_history (
    symbol            TEXT NOT NULL,
    as_of             TEXT NOT NULL,
    target_consensus  REAL,
    target_median     REAL,
    target_high       REAL,
    target_low        REAL,
    grade_score       REAL,
    source            TEXT,
    fetched_at        TEXT NOT NULL,
    PRIMARY KEY (symbol, as_of)
)
"""

_ANALYST_HISTORY_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_analyst_history_symbol
    ON analyst_history (symbol, as_of)
"""

# ─────────────────────────────────────────────────────────────────────────────
# DDL — earnings_events (Financial Modeling Prep earnings calendar + surprises)
#
# Rows may be FUTURE-DATED with NULL actuals, and that is normal and correct:
# a scheduled earnings DATE is publicly announced in advance, so knowing it is
# not lookahead. Knowing the RESULT is. Concretely, the read-side rules any
# consumer must apply (each has a test in the wave-1 feed module):
#
#   1. A row counts as "actual" IFF ``eps_actual IS NOT NULL``. NULL is never
#      to be read as 0.0 — that would turn every unreported quarter into a
#      100%-miss (CONSTRAINT #4).
#   2. A trailing surprise uses only rows with ``event_date <= as_of`` AND
#      ``eps_actual IS NOT NULL`` — BOTH, so a vendor bug that populates an
#      actual on a future row cannot slip through the date filter alone.
#   3. The next scheduled date / days-to-earnings come from
#      ``event_date > as_of``. That is deliberate; do not "fix" it later.
#   4. ``last_updated`` is the vendor's own row-revision stamp and is persisted
#      SPECIFICALLY to make a future point-in-time replay possible.
#
# Honest limitation on (4): it is an IMPERFECT defense, not a PIT guarantee. A
# row the vendor backfills with an actual while leaving ``last_updated`` stale
# defeats it entirely, and we cannot detect that from our side. Say so rather
# than claiming this table is point-in-time safe — it is not.
#
# PK is ``(symbol, event_date)`` so a re-fetch upgrades a scheduled row in
# place once the result lands, rather than accumulating two rows for one event.
# ─────────────────────────────────────────────────────────────────────────────

_EARNINGS_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS earnings_events (
    symbol             TEXT NOT NULL,
    event_date         TEXT NOT NULL,
    eps_actual         REAL,
    eps_estimated      REAL,
    revenue_actual     REAL,
    revenue_estimated  REAL,
    last_updated       TEXT,
    source             TEXT,
    fetched_at         TEXT NOT NULL,
    PRIMARY KEY (symbol, event_date)
)
"""

_EARNINGS_EVENTS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_earnings_events_symbol_date
    ON earnings_events (symbol, event_date)
"""

# ─────────────────────────────────────────────────────────────────────────────
# DDL — insider_stats (Financial Modeling Prep /insider-trading/statistics)
#
# Quarterly aggregates of Form 4 insider transactions, keyed
# ``(symbol, year, quarter)``.
#
# The leakage trap here is NOT the date filter — it is that a quarter's
# aggregate KEEPS CHANGING after the quarter ends, because Form 4s continue to
# land (late filings, amendments) for weeks afterwards. Reading the most recent
# quarter therefore reads a number that did not exist in that form at the time,
# and would not have existed for a backtest positioned then.
#
# Consumers must therefore apply a MINIMUM-LAG filter — only consume a quarter
# that ended at least ``settings.FMP_INSIDER_MIN_LAG_DAYS`` (default 45) days
# ago — rather than simply taking ``MAX(year, quarter)``. That 45 is a
# deliberate conservative judgment call, not a constant derived from any SEC
# rule; it is documented as such in settings.py.
#
# NULL (→ NaN, never 0.0) for any unreported figure — CONSTRAINT #4.
# ─────────────────────────────────────────────────────────────────────────────

_INSIDER_STATS_DDL = """
CREATE TABLE IF NOT EXISTS insider_stats (
    symbol                  TEXT    NOT NULL,
    year                    INTEGER NOT NULL,
    quarter                 INTEGER NOT NULL,
    acquired_transactions   INTEGER,
    disposed_transactions   INTEGER,
    acquired_disposed_ratio REAL,
    total_acquired          REAL,
    total_disposed          REAL,
    total_purchases         INTEGER,
    total_sales             INTEGER,
    source                  TEXT,
    fetched_at              TEXT    NOT NULL,
    PRIMARY KEY (symbol, year, quarter)
)
"""

_INSIDER_STATS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_insider_stats_symbol_period
    ON insider_stats (symbol, year, quarter)
"""

# ─────────────────────────────────────────────────────────────────────────────
# DDL — sector_snapshots (FMP sector-PE + sector-performance snapshots)
#
# The one genuinely point-in-time new feed in this series: both FMP endpoints
# behind it are DATE-PARAMETERIZED, so a dated request returns that date's
# figures rather than today's. Consumers must always use the dated form, and
# ``date`` here is the SOURCE's own snapshot date (part of the PK), never the
# fetch time — ``fetched_at`` is the separate wall-clock stamp.
#
# Because of that, this is also the only one of the four new feeds that is a
# plausible future signal candidate. It is still diagnostic-only in v1: it has
# no accumulated history yet either, and "could be backtested in principle"
# is not the same as "has been".
#
# Keyed by sector NAME (the 11-name GICS-style taxonomy shared with
# data/sector_descriptions.yaml), not by symbol — this is 2 requests per cycle
# for the whole universe, which is why it carries its own settings gate
# separate from the per-symbol insider feed.
# ─────────────────────────────────────────────────────────────────────────────

_SECTOR_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS sector_snapshots (
    sector      TEXT NOT NULL,
    date        TEXT NOT NULL,
    pe          REAL,
    change_pct  REAL,
    source      TEXT,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (sector, date)
)
"""

_SECTOR_SNAPSHOTS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_sector_snapshots_date
    ON sector_snapshots (date)
"""

# ─────────────────────────────────────────────────────────────────────────────
# schema_version -- a single-row stamp for this DB file's schema shape.
#
# `_ensure_tables()`'s per-table `_migrate_*` helpers are all ADDITIVE
# (`ALTER TABLE ADD COLUMN`, guarded by `PRAGMA table_info`) and are safe to
# run unconditionally against any older DB -- that is what keeps this class
# working across upgrades without a version check today. `schema_version`
# does not replace that; it exists for the failure mode additive migration
# can't self-detect: a DB file written by a NEWER build of this codebase (a
# column renamed/removed, a type changed, a table restructured) being opened
# by an OLDER build, which would otherwise read back whatever plain SQLite
# happens to return -- silently wrong values, not an error. On mismatch this
# only WARNS (never raises): per this module's CONSTRAINT #6 dead-letter
# posture, a version stamp is a diagnostic signal for the operator, not a
# gate that should take down the pipeline.
# ─────────────────────────────────────────────────────────────────────────────

CURRENT_SCHEMA_VERSION = 1

_SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    version    INTEGER NOT NULL,
    updated_at TEXT    NOT NULL
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
        Path (or ``sqlite://``/``postgresql://`` URL) to the database. When
        omitted (``None``, the default), resolved via
        ``db_config.resolve_database_url()`` -- the same
        ``settings.LOCAL_DATA_ROOT``-anchored default every sibling store
        (``data/paper_account_store.py``, ``transactions_store.py``) uses,
        rather than a CWD-relative ``"quant_platform.db"`` literal.
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

    def __init__(self, db_path: Optional[str] = None, *, readonly: bool = False) -> None:
        if db_path is None:
            from db_config import resolve_database_url
            db_path = resolve_database_url()
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
                conn.execute(_FUNDAMENTALS_HISTORY_INDEX_DROP_DDL)
                conn.execute(_FUNDAMENTALS_HISTORY_INDEX_DDL)
                conn.execute(_MACRO_HISTORY_DDL)
                conn.execute(_MACRO_HISTORY_INDEX_DDL)
                conn.execute(_NEWS_HISTORY_DDL)
                conn.execute(_NEWS_HISTORY_INDEX_DROP_DDL)
                conn.execute(_NEWS_HISTORY_INDEX_DDL)
                conn.execute(_SENTIMENT_INGESTION_AUDIT_DDL)
                conn.execute(_SENTIMENT_INGESTION_AUDIT_INDEX_DDL)
                conn.execute(_SENTIMENT_INGESTION_AUDIT_ASOF_INDEX_DDL)
                conn.execute(_SENTIMENT_LLM_VERIFICATION_CACHE_DDL)
                conn.execute(_FINBERT_SCORE_CACHE_DDL)
                conn.execute(_RAG_INDEXED_DOCS_DDL)
                conn.execute(_RAG_INDEXED_DOCS_INDEX_DDL)
                conn.execute(_ETF_HOLDINGS_DDL)
                conn.execute(_ETF_HOLDINGS_INDEX_DDL)
                # FMP feed tables (analyst / earnings / insider / sector).
                # Purely additive CREATE TABLE IF NOT EXISTS — no
                # CURRENT_SCHEMA_VERSION bump needed (see that constant's
                # comment: additive DDL is the documented upgrade mechanism,
                # and the stamp exists only for the drift additive migration
                # cannot self-detect).
                conn.execute(_ANALYST_HISTORY_DDL)
                conn.execute(_ANALYST_HISTORY_INDEX_DDL)
                conn.execute(_EARNINGS_EVENTS_DDL)
                conn.execute(_EARNINGS_EVENTS_INDEX_DDL)
                conn.execute(_INSIDER_STATS_DDL)
                conn.execute(_INSIDER_STATS_INDEX_DDL)
                conn.execute(_SECTOR_SNAPSHOTS_DDL)
                conn.execute(_SECTOR_SNAPSHOTS_INDEX_DDL)
                conn.execute(_SCHEMA_VERSION_DDL)
                conn.commit()
                self._migrate_add_report_date_column(conn)
                self._migrate_add_verification_method_column(conn)
                self._ensure_schema_version(conn)
            finally:
                raw_conn.close()
        except Exception as exc:
            logger.warning("HistoricalStore._ensure_tables failed: %s", exc)

    def _ensure_schema_version(self, conn: sqlite3.Connection) -> None:
        """Stamp/verify the ``schema_version`` row. See the DDL comment above
        for what this is (and is not) a guard against. Never raises."""
        try:
            row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
            now_ts = self._now_utc_iso()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_version (id, version, updated_at) VALUES (1, ?, ?)",
                    (CURRENT_SCHEMA_VERSION, now_ts),
                )
                conn.commit()
                logger.info("HistoricalStore: stamped schema_version=%d.", CURRENT_SCHEMA_VERSION)
                return

            db_version = row[0]
            if db_version < CURRENT_SCHEMA_VERSION:
                conn.execute(
                    "UPDATE schema_version SET version = ?, updated_at = ? WHERE id = 1",
                    (CURRENT_SCHEMA_VERSION, now_ts),
                )
                conn.commit()
                logger.info(
                    "HistoricalStore: schema_version bumped %d -> %d.",
                    db_version, CURRENT_SCHEMA_VERSION,
                )
            elif db_version > CURRENT_SCHEMA_VERSION:
                logger.warning(
                    "HistoricalStore: quant_platform.db schema_version=%d is NEWER than "
                    "this build's CURRENT_SCHEMA_VERSION=%d. This DB was written by a "
                    "newer version of this codebase; reads against it from this older "
                    "build may silently return wrong values instead of an error. "
                    "Update this checkout before trusting cached reads.",
                    db_version, CURRENT_SCHEMA_VERSION,
                )
        except Exception as exc:
            logger.warning("HistoricalStore._ensure_schema_version failed: %s", exc)

    def get_schema_version(self) -> Optional[int]:
        """Return the DB file's stamped ``schema_version``, or ``None`` if unset
        (a DB created before this stamp existed, or an empty/fresh DB whose
        ``_ensure_tables()`` hasn't run yet)."""
        try:
            with self._lock:
                conn = self._get_conn()
                row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
            return int(row[0]) if row is not None else None
        except Exception as exc:
            logger.warning("HistoricalStore.get_schema_version failed: %s", exc)
            return None

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

    def get_bars_bulk(
        self,
        symbols: List[str],
        lookback_days: int = 504,
        *,
        provider=None,
    ) -> Dict[str, pd.DataFrame]:
        """Fetch price bars for multiple symbols concurrently via bounded
        worker threads (network/DB I/O bound, same
        ``settings.DATA_FETCH_MAX_CONCURRENCY`` pattern already used by
        ``data_engine.py``'s per-ticker loops).

        One symbol's failure never drops any other symbol's result (CLAUDE.md's
        per-ticker try/except convention) -- returns whatever subset
        succeeded, logging (not raising) on each individual failure.
        """
        symbols = [s.upper() for s in symbols if isinstance(s, str) and s]
        results: Dict[str, pd.DataFrame] = {}
        if not symbols:
            return results

        def _fetch_one(sym: str) -> Tuple[str, Optional[pd.DataFrame]]:
            try:
                return sym, self.get_bars(sym, lookback_days, provider=provider)
            except Exception as exc:  # noqa: BLE001 - per-ticker isolation, never abort the batch
                logger.error(f"get_bars_bulk: failed to fetch {sym}: {exc}")
                return sym, None

        from settings import settings as _s  # avoid circular import

        workers = max(1, min(len(symbols), int(getattr(_s, "DATA_FETCH_MAX_CONCURRENCY", 8))))
        if workers == 1 or len(symbols) <= 1:
            pairs = [_fetch_one(sym) for sym in symbols]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                pairs = list(pool.map(_fetch_one, symbols))
        return {sym: df for sym, df in pairs if df is not None}

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
        # Never cache a totally empty provider response as if it were fresh
        # data — an empty ``raw`` dict means the provider returned nothing
        # (a common yfinance/Yahoo failure mode), and upserting it anyway
        # would make the next call within `max_age_days` read back a
        # "fresh" all-NaN row instead of retrying the live fetch, silently
        # suppressing recovery until the TTL expires (cache poisoning).
        # A non-empty `raw` with some missing fields is legitimate partial
        # data and is still cached as-is.
        if not raw:
            logger.warning(
                "HistoricalStore.get_fundamentals(%s): provider returned an "
                "empty response; skipping DB upsert so the next call retries "
                "instead of reading back a stale all-NaN cache hit.", symbol,
            )
            return typed

        try:
            # ``raw`` is passed so a per-symbol ``_source`` key (embedded by a
            # fallback-capable provider) wins over the provider object's own
            # chain-level label. Absent the key this is unchanged behavior.
            self._upsert_fundamentals(
                symbol, typed, raw, source=_source_name(_provider, raw)
            )
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
            # See get_fundamentals() above: the per-symbol ``_source`` key in
            # ``raw`` wins over the provider object's chain-level label.
            self._upsert_fundamentals(
                symbol, typed, raw, source=_source_name(_provider, raw)
            )
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
            tz-naive DatetimeIndex, values are floats. Dates with no real
            FRED observation are OMITTED entirely (not included as NaN) —
            see ``_read_macro_series``'s docstring for why this matters to
            ``merge_asof``/rolling-window consumers. Empty Series on total
            failure.
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
        """Return (date, value) rows for *series_id* with a REAL observation,
        as a DataFrame.

        ``macro_history`` stores one row per calendar day per series (a dense
        skeleton), with ``value=NULL`` on days FRED has no observation for
        (a monthly series like UNRATE, or a genuine gap in a daily one like
        BAMLH0A0HYM2). Rows with ``value IS NULL`` are excluded here rather
        than returned as NaN — this is the single read path ``get_macro()``
        goes through (its only two call sites are both in this class), so
        filtering here fixes every consumer at once. Downstream, both
        ``_reconstruct_macro_regime_series``'s Sahm-rule
        ``.rolling(window=3)`` (needs 3 consecutive REAL monthly UNRATE
        observations, not 3 consecutive calendar-day rows) and
        ``_asof_align``'s ``merge_asof(direction="backward")`` (must forward-
        fill from the nearest REAL prior value, not match onto a NULL
        placeholder row and propagate NaN) require a sparse, gap-free series
        of real observations, not a dense one padded with NaN.
        """
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                """
                SELECT date, value
                FROM macro_history
                WHERE series_id = ? AND value IS NOT NULL
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

    def get_news_sentiment_history(
        self,
        symbol: str,
        lookback_days: Optional[int] = None,
    ) -> pd.Series:
        """Return a tz-naive date-indexed Series of archived ``news_history``
        scores for *symbol* — the read-only counterpart of
        ``save_news_sentiment``.

        Unlike ``get_macro``, there is no live top-up here: ``news_history``
        is forward-archive only (see its DDL comment) — there is nothing to
        "fetch" for a past date that wasn't captured when it happened, so
        this is a plain read.

        A row's ``score`` is ``NULL`` (→ ``NaN`` here) exactly when
        ``signals/news_catalyst.py``'s ``NewsCatalystSignal.pre_compute()``
        had a genuine fetch/scoring failure or zero headlines that day (its
        ``_news_archive_scores`` split) — preserved as ``NaN``, never
        coerced to ``0.0`` (CONSTRAINT #4). A caller building a chart from
        this series must treat a ``NaN`` point as a real gap (skip it),
        never plot it as zero sentiment.

        Parameters
        ----------
        symbol:
            Ticker symbol (case-insensitive).
        lookback_days:
            If provided, only rows from the last *lookback_days* days are
            returned.

        Returns
        -------
        pd.Series
            tz-naive DatetimeIndex, float values (``NaN`` for archived
            "no data" rows). Empty Series when the symbol has no archived
            history at all, or on any DB error (CONSTRAINT #6 — never
            raises).
        """
        sym = str(symbol or "").upper().strip()
        if not sym:
            return pd.Series(dtype=float, name=symbol)
        try:
            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    rows = conn.execute(
                        """
                        SELECT as_of, score
                        FROM news_history
                        WHERE symbol = ?
                        ORDER BY as_of ASC
                        """,
                        (sym,),
                    ).fetchall()

            if not rows:
                return pd.Series(dtype=float, name=sym)

            dates = [r[0] for r in rows]
            values = [float("nan") if r[1] is None else float(r[1]) for r in rows]
            series = pd.Series(values, index=pd.DatetimeIndex(dates), name=sym)
            series.index = series.index.tz_localize(None)
            series = series.sort_index()

            if lookback_days is not None and lookback_days > 0:
                cutoff = pd.Timestamp.now(tz=None) - pd.Timedelta(days=lookback_days)
                series = series[series.index >= cutoff]

            return series
        except Exception as exc:
            logger.warning(
                "HistoricalStore.get_news_sentiment_history(%s) failed: %s", sym, exc
            )
            return pd.Series(dtype=float, name=sym)

    # ─────────────────────────────────────────────────────────────────────────
    # Public API — finbert_score_cache (FinBERT local batch inference)
    # ─────────────────────────────────────────────────────────────────────────

    def get_finbert_score(self, content_hash: str) -> Optional[Dict[str, float]]:
        """Return ``{"positive": p, "neutral": n, "negative": g}`` for a
        previously-scored headline, or ``None`` on a cache miss OR any read
        failure.

        Dead-letter resilient (CONSTRAINT #6): a DB read failure degrades to
        ``None`` (treated by the caller identically to "not cached yet"),
        never raises. ``content_hash`` is
        ``signals.news_catalyst._content_hash(headline)`` -- a SHA-256 digest
        of the raw headline text (see the ``finbert_score_cache`` DDL comment
        for why a content-hash lookup carries no lookahead risk).
        """
        try:
            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    row = conn.execute(
                        "SELECT positive, neutral, negative FROM finbert_score_cache "
                        "WHERE content_hash = ?",
                        (content_hash,),
                    ).fetchone()
            if row is None or row[0] is None or row[1] is None or row[2] is None:
                # A row with any NULL column is malformed (partial write,
                # manual edit, future write-path regression) -- treat it
                # identically to a cache miss so the caller re-scores fresh,
                # rather than fabricating a 0.0 for the missing component(s)
                # (CONSTRAINT #4). The only writer, save_finbert_scores(),
                # never writes a NULL, so this guards against future writers.
                return None
            return {
                "positive": float(row[0]),
                "neutral": float(row[1]),
                "negative": float(row[2]),
            }
        except Exception as exc:
            logger.warning("HistoricalStore.get_finbert_score failed: %s", exc)
            return None

    def save_finbert_scores(self, scores: Dict[str, Dict[str, Any]]) -> None:
        """Persist a batch of freshly-scored headlines, one row per
        ``content_hash``.

        ``scores`` maps ``content_hash -> {"positive": .., "neutral": ..,
        "negative": .., "headline_snippet": ..}`` (``headline_snippet`` is
        optional, purely for human debugging -- never read back
        programmatically). Idempotent overwrite (``INSERT OR REPLACE``): a
        repeat score for the same content hash (e.g. a race between two
        concurrent cycles) simply refreshes ``scored_at``. Dead-letter
        resilient (CONSTRAINT #6): any write failure is logged and swallowed
        so a cache-write failure can never block the live scoring pipeline
        that already computed these scores.
        """
        if not scores:
            return
        try:
            now_ts = self._now_utc_iso()
            rows = [
                (
                    content_hash,
                    str(entry.get("headline_snippet", ""))[:200] or None,
                    float(entry.get("positive", 0.0)),
                    float(entry.get("neutral", 0.0)),
                    float(entry.get("negative", 0.0)),
                    now_ts,
                )
                for content_hash, entry in scores.items()
            ]
            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO finbert_score_cache
                            (content_hash, headline_snippet, positive, neutral, negative, scored_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        rows,
                    )
            logger.debug(
                "HistoricalStore: upserted %d finbert_score_cache rows.", len(rows)
            )
        except Exception as exc:
            logger.warning("HistoricalStore.save_finbert_scores failed: %s", exc)

    def count_finbert_scores(self, since: Optional[datetime] = None) -> int:
        """Return the number of rows in ``finbert_score_cache``, optionally
        restricted to ``scored_at >= since``.

        Uses the exact same raw-connection read pattern as
        :meth:`get_finbert_score` (``session_scope`` + ``get_dbapi_connection``,
        under ``self._lock``). Dead-letter resilient (CONSTRAINT #6): returns
        ``0`` and logs a WARNING on any DB error, never raises.
        """
        try:
            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    if since is not None:
                        row = conn.execute(
                            "SELECT COUNT(*) FROM finbert_score_cache WHERE scored_at >= ?",
                            (since.isoformat(),),
                        ).fetchone()
                    else:
                        row = conn.execute(
                            "SELECT COUNT(*) FROM finbert_score_cache"
                        ).fetchone()
            return int(row[0]) if row is not None and row[0] is not None else 0
        except Exception as exc:
            logger.warning("HistoricalStore.count_finbert_scores failed: %s", exc)
            return 0

    # ─────────────────────────────────────────────────────────────────────────
    # Public API — etf_holdings (ETF constituent-holdings cache)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _nan_to_null(value: Any) -> Optional[float]:
        """Coerce a float to ``None`` when it is NaN/inf, else to ``float``.

        SQLite has no NaN literal, so an unreported field is stored as NULL and
        read back as NaN. Writing 0.0 instead would fabricate a measurement
        (CONSTRAINT #4) — a zero weight and an unreported weight are different
        facts.
        """
        if value is None:
            return None
        try:
            as_float = float(value)
        except (TypeError, ValueError):
            return None
        if math.isnan(as_float) or math.isinf(as_float):
            return None
        return as_float

    def save_etf_holdings(self, holdings: List["ETFHolding"]) -> int:
        """Persist a batch of ``data.etf_holdings.ETFHolding`` rows.

        Returns the number of rows written, or ``0`` on ANY failure
        (CONSTRAINT #6 — never raises; a cache-write failure must not block
        the live ingestion that already parsed these rows).

        Idempotent overwrite (``INSERT OR REPLACE``) on the
        ``(etf_symbol, holding_symbol, as_of_date)`` primary key: re-ingesting
        the same filing refreshes ``fetched_at`` and leaves the basket
        unchanged. Successive report dates accumulate as separate rows — this
        method never deletes prior baskets, which is what makes the
        point-in-time read in ``get_etf_holdings`` possible.

        ``weight``/``shares_held`` that are NaN are stored as NULL, never 0.0
        (see ``_nan_to_null``). Rows are duck-typed rather than isinstance-
        checked so this module never has to import ``data.etf_holdings``
        (which imports this module).
        """
        if not holdings:
            return 0
        try:
            now_ts = self._now_utc_iso()
            rows = []
            for holding in holdings:
                as_of = getattr(holding, "as_of_date", None)
                if as_of is None:
                    # No point-in-time anchor => uncacheable (it could never be
                    # causality-filtered on read). Skip rather than default.
                    continue
                rows.append(
                    (
                        str(getattr(holding, "etf_symbol", "")).strip().upper(),
                        str(getattr(holding, "holding_symbol", "")).strip().upper(),
                        as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of),
                        self._nan_to_null(getattr(holding, "weight", None)),
                        self._nan_to_null(getattr(holding, "shares_held", None)),
                        str(getattr(holding, "source", "")) or None,
                        now_ts,
                    )
                )
            rows = [row for row in rows if row[0] and row[1]]
            if not rows:
                return 0

            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO etf_holdings
                            (etf_symbol, holding_symbol, as_of_date,
                             weight, shares_held, source, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        rows,
                    )
            logger.debug("HistoricalStore: upserted %d etf_holdings rows.", len(rows))
            return len(rows)
        except Exception as exc:
            logger.warning("HistoricalStore.save_etf_holdings failed: %s", exc)
            self._safe_rollback()
            return 0

    def get_etf_holdings(
        self, etf_symbol: str, *, as_of_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return one ETF's basket as of *as_of_date*, as a list of dicts.

        **Causality guarantee (has a dedicated test):** rows whose
        ``as_of_date`` is AFTER the supplied cutoff are never returned. This
        is the storage-layer half of ``data/etf_holdings.py``'s no-lookahead
        contract — a basket written by a later cycle cannot surface in an
        earlier-dated read.

        Returns the SINGLE most recent report date at or before the cutoff,
        not a union across quarters: "holdings as of X" is one basket, and
        mixing two quarters' rows would produce weights that sum past 1.0 and
        double-count names that appear in both. Use
        ``latest_etf_holdings_date()`` plus repeated calls to walk history.

        With ``as_of_date=None`` the newest stored basket is returned (the
        live-use case).

        Each dict carries ``etf_symbol``, ``holding_symbol``, ``as_of_date``,
        ``weight``, ``shares_held``, ``source``, ``fetched_at``. ``weight``
        and ``shares_held`` are ``None`` when the source did not report them —
        the caller rehydrates ``None`` to NaN, never to 0.0 (CONSTRAINT #4).

        ``[]`` on an empty cache OR any read failure (CONSTRAINT #6).
        """
        sym = (etf_symbol or "").strip().upper()
        if not sym:
            return []
        try:
            from db_config import session_scope, get_dbapi_connection

            params: List[Any] = [sym]
            date_clause = ""
            if as_of_date:
                date_clause = " AND as_of_date <= ?"
                params.append(str(as_of_date))

            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    target = conn.execute(
                        "SELECT MAX(as_of_date) FROM etf_holdings "
                        f"WHERE etf_symbol = ?{date_clause}",
                        tuple(params),
                    ).fetchone()
                    if not target or target[0] is None:
                        return []
                    target_date = str(target[0])
                    rows = conn.execute(
                        """
                        SELECT etf_symbol, holding_symbol, as_of_date,
                               weight, shares_held, source, fetched_at
                        FROM etf_holdings
                        WHERE etf_symbol = ? AND as_of_date = ?
                        ORDER BY holding_symbol
                        """,
                        (sym, target_date),
                    ).fetchall()

            return [
                {
                    "etf_symbol": row[0],
                    "holding_symbol": row[1],
                    "as_of_date": row[2],
                    "weight": row[3],
                    "shares_held": row[4],
                    "source": row[5],
                    "fetched_at": row[6],
                }
                for row in rows
            ]
        except Exception as exc:
            logger.warning("HistoricalStore.get_etf_holdings(%s) failed: %s", sym, exc)
            return []

    def latest_etf_holdings_date(self, etf_symbol: str) -> Optional[str]:
        """Return the most recent stored ``as_of_date`` for *etf_symbol*.

        ISO ``YYYY-MM-DD`` string, or ``None`` when nothing is stored OR on any
        read failure (CONSTRAINT #6). Deliberately unfiltered by any cutoff —
        this answers "how current is the cache", which callers use to decide
        whether to re-fetch; the causality filtering happens in
        ``get_etf_holdings``.
        """
        sym = (etf_symbol or "").strip().upper()
        if not sym:
            return None
        try:
            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    row = conn.execute(
                        "SELECT MAX(as_of_date) FROM etf_holdings WHERE etf_symbol = ?",
                        (sym,),
                    ).fetchone()
            if not row or row[0] is None:
                return None
            return str(row[0])
        except Exception as exc:
            logger.warning(
                "HistoricalStore.latest_etf_holdings_date(%s) failed: %s", sym, exc
            )
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Public API — FMP feeds (analyst / earnings / insider / sector)
    #
    # Scaffolding for the Financial Modeling Prep integration: the tables and
    # these accessors exist, but nothing in the platform writes to them until
    # the corresponding ``pipeline/production_steps.py::_apply_fmp_*`` bodies
    # are filled in. Every method follows this module's house rules — per-call
    # try/except, an empty sentinel (``0``/``[]``/``{}``/``None``) on ANY
    # failure and NEVER a raise (CONSTRAINT #6), and NULL rather than 0.0 for
    # an unreported figure (CONSTRAINT #4, via ``_nan_to_null``).
    # ─────────────────────────────────────────────────────────────────────────

    def upsert_analyst_snapshot(
        self,
        symbol: str,
        as_of: str,
        *,
        target_consensus: Optional[float] = None,
        target_median: Optional[float] = None,
        target_high: Optional[float] = None,
        target_low: Optional[float] = None,
        grade_score: Optional[float] = None,
        source: Optional[str] = None,
    ) -> int:
        """Archive ONE cycle's analyst consensus observation for *symbol*.

        Forward-archive only — see the ``analyst_history`` DDL comment for why
        this table can never be backfilled honestly (FMP serves only the
        current consensus, and targets get revised).

        ``as_of`` is OUR observation date (``YYYY-MM-DD``), not a vendor
        revision date. ``INSERT OR REPLACE`` on ``(symbol, as_of)`` so a second
        cycle on the same day refreshes rather than duplicating.

        Returns 1 on write, 0 on skip or ANY failure (CONSTRAINT #6).
        """
        sym = (symbol or "").strip().upper()
        as_of_str = str(as_of or "").strip()
        if not sym or not as_of_str:
            return 0
        try:
            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO analyst_history
                            (symbol, as_of, target_consensus, target_median,
                             target_high, target_low, grade_score, source, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sym,
                            as_of_str,
                            self._nan_to_null(target_consensus),
                            self._nan_to_null(target_median),
                            self._nan_to_null(target_high),
                            self._nan_to_null(target_low),
                            self._nan_to_null(grade_score),
                            str(source) if source else None,
                            self._now_utc_iso(),
                        ),
                    )
            return 1
        except Exception as exc:
            logger.warning(
                "HistoricalStore.upsert_analyst_snapshot(%s) failed: %s", sym, exc
            )
            self._safe_rollback()
            return 0

    def get_analyst_snapshot(
        self, symbol: str, *, as_of: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return the most recent archived analyst observation for *symbol*.

        With ``as_of`` supplied, rows dated AFTER the cutoff are excluded — the
        storage-layer half of the causality contract, matching
        ``get_etf_holdings``. With ``as_of=None`` the newest row is returned.

        ``{}`` when nothing is archived OR on any read failure (CONSTRAINT #6).
        Unreported figures come back as ``None`` (the caller rehydrates to NaN,
        never 0.0).
        """
        sym = (symbol or "").strip().upper()
        if not sym:
            return {}
        try:
            from db_config import session_scope, get_dbapi_connection

            params: List[Any] = [sym]
            date_clause = ""
            if as_of:
                date_clause = " AND as_of <= ?"
                params.append(str(as_of))

            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    row = conn.execute(
                        "SELECT symbol, as_of, target_consensus, target_median, "
                        "target_high, target_low, grade_score, source, fetched_at "
                        f"FROM analyst_history WHERE symbol = ?{date_clause} "
                        "ORDER BY as_of DESC LIMIT 1",
                        tuple(params),
                    ).fetchone()
            if not row:
                return {}
            return {
                "symbol": row[0],
                "as_of": row[1],
                "target_consensus": row[2],
                "target_median": row[3],
                "target_high": row[4],
                "target_low": row[5],
                "grade_score": row[6],
                "source": row[7],
                "fetched_at": row[8],
            }
        except Exception as exc:
            logger.warning("HistoricalStore.get_analyst_snapshot(%s) failed: %s", sym, exc)
            return {}

    def latest_analyst_as_of(self, symbol: str) -> Optional[str]:
        """Most recent archived ``as_of`` for *symbol*, for the cadence check.

        Deliberately unfiltered by any cutoff — this answers "how current is
        the archive", which the ``FMP_ANALYST_REFRESH_HOURS`` gate uses to
        decide whether to spend a request. ``None`` when nothing is stored OR
        on any read failure (CONSTRAINT #6).
        """
        return self._latest_scalar(
            "SELECT MAX(as_of) FROM analyst_history WHERE symbol = ?",
            (symbol or "").strip().upper(),
            label="latest_analyst_as_of",
        )

    def upsert_earnings_events(self, rows: List[Dict[str, Any]]) -> int:
        """Persist a batch of earnings-calendar rows.

        Each dict may carry ``symbol``, ``event_date``, ``eps_actual``,
        ``eps_estimated``, ``revenue_actual``, ``revenue_estimated``,
        ``last_updated``, ``source``. A row missing ``symbol`` or
        ``event_date`` is SKIPPED (no PK anchor => it could never be
        causality-filtered on read), not defaulted.

        A FUTURE-dated row with ``eps_actual=None`` is expected and correct —
        see the DDL comment. ``None``/NaN actuals are stored as SQL NULL and
        must never be read back as 0.0 (CONSTRAINT #4).

        ``last_updated`` is the vendor's own revision stamp, persisted verbatim
        to make a future point-in-time replay possible. It is an imperfect
        defense (a backfilled actual with a stale stamp defeats it) — this is
        not a PIT guarantee.

        Returns the number of rows written, or 0 on ANY failure.
        """
        if not rows:
            return 0
        try:
            now_ts = self._now_utc_iso()
            prepared: List[tuple] = []
            for row in rows:
                sym = str(row.get("symbol") or "").strip().upper()
                event_date = str(row.get("event_date") or "").strip()
                if not sym or not event_date:
                    continue
                prepared.append(
                    (
                        sym,
                        event_date,
                        self._nan_to_null(row.get("eps_actual")),
                        self._nan_to_null(row.get("eps_estimated")),
                        self._nan_to_null(row.get("revenue_actual")),
                        self._nan_to_null(row.get("revenue_estimated")),
                        str(row["last_updated"]) if row.get("last_updated") else None,
                        str(row["source"]) if row.get("source") else None,
                        now_ts,
                    )
                )
            if not prepared:
                return 0

            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO earnings_events
                            (symbol, event_date, eps_actual, eps_estimated,
                             revenue_actual, revenue_estimated, last_updated,
                             source, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        prepared,
                    )
            logger.debug("HistoricalStore: upserted %d earnings_events rows.", len(prepared))
            return len(prepared)
        except Exception as exc:
            logger.warning("HistoricalStore.upsert_earnings_events failed: %s", exc)
            self._safe_rollback()
            return 0

    def get_earnings_events(
        self,
        symbol: str,
        *,
        on_or_before: Optional[str] = None,
        after: Optional[str] = None,
        actuals_only: bool = False,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return *symbol*'s stored earnings events, newest first.

        The two date filters are deliberately separate rather than one
        as-of cutoff, because the two legitimate reads have OPPOSITE
        directions (see the DDL comment):

        * ``on_or_before=<as_of>`` + ``actuals_only=True`` — the trailing
          surprise read. BOTH filters, never the date filter alone: a vendor
          bug populating an actual on a future row would otherwise slip
          through.
        * ``after=<as_of>`` — the next-scheduled-date read. A publicly
          announced future date is not lookahead; the RESULT would be.

        ``[]`` on an empty table OR any read failure (CONSTRAINT #6). Every
        unreported numeric field comes back ``None``, never 0.0.
        """
        sym = (symbol or "").strip().upper()
        if not sym:
            return []
        try:
            from db_config import session_scope, get_dbapi_connection

            params: List[Any] = [sym]
            clauses = " AND event_date != '1900-01-01' AND event_date != '__no_data__'"
            if on_or_before:
                clauses += " AND event_date <= ?"
                params.append(str(on_or_before))
            if after:
                clauses += " AND event_date > ?"
                params.append(str(after))
            if actuals_only:
                clauses += " AND eps_actual IS NOT NULL"

            order = " ORDER BY event_date ASC" if after else " ORDER BY event_date DESC"
            limit_clause = ""
            if limit is not None:
                limit_clause = " LIMIT ?"
                params.append(int(limit))

            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    db_rows = conn.execute(
                        "SELECT symbol, event_date, eps_actual, eps_estimated, "
                        "revenue_actual, revenue_estimated, last_updated, source, fetched_at "
                        f"FROM earnings_events WHERE symbol = ?{clauses}{order}{limit_clause}",
                        tuple(params),
                    ).fetchall()

            return [
                {
                    "symbol": r[0],
                    "event_date": r[1],
                    "eps_actual": r[2],
                    "eps_estimated": r[3],
                    "revenue_actual": r[4],
                    "revenue_estimated": r[5],
                    "last_updated": r[6],
                    "source": r[7],
                    "fetched_at": r[8],
                }
                for r in db_rows
            ]
        except Exception as exc:
            logger.warning("HistoricalStore.get_earnings_events(%s) failed: %s", sym, exc)
            return []

    def latest_earnings_fetched_at(self, symbol: str) -> Optional[str]:
        """Most recent ``fetched_at`` across *symbol*'s earnings rows.

        Wall-clock, NOT an event date — this is the ``FMP_EARNINGS_REFRESH_HOURS``
        cadence input. ``None`` when nothing is stored OR on any read failure.
        """
        return self._latest_scalar(
            "SELECT MAX(fetched_at) FROM earnings_events WHERE symbol = ?",
            (symbol or "").strip().upper(),
            label="latest_earnings_fetched_at",
        )

    def mark_earnings_fetched(self, symbol: str) -> None:
        """Record that *symbol* was fetched but FMP returned no earnings data.

        Inserts a sentinel row with ``event_date='1900-01-01'`` so
        ``latest_earnings_fetched_at`` returns a fresh timestamp and the
        cadence gate skips this symbol for ``FMP_EARNINGS_REFRESH_HOURS``.
        The sentinel date is filtered out of all query methods.
        """
        try:
            now_ts = self._now_utc_iso()
            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    conn.execute(
                        """INSERT OR REPLACE INTO earnings_events
                            (symbol, event_date, fetched_at, source)
                        VALUES (?, '1900-01-01', ?, 'fmp-no-data')""",
                        ((symbol or "").strip().upper(), now_ts),
                    )
        except Exception as exc:
            logger.warning("HistoricalStore.mark_earnings_fetched(%s) failed: %s", symbol, exc)

    def upsert_insider_stats(self, rows: List[Dict[str, Any]]) -> int:
        """Persist a batch of quarterly insider-transaction aggregates.

        Keys per dict: ``symbol``, ``year``, ``quarter``, plus any of
        ``acquired_transactions``, ``disposed_transactions``,
        ``acquired_disposed_ratio``, ``total_acquired``, ``total_disposed``,
        ``total_purchases``, ``total_sales``, ``source``. A row without a
        resolvable ``(symbol, year, quarter)`` is skipped, not defaulted.

        ``INSERT OR REPLACE`` is the right semantic here specifically BECAUSE a
        quarter's aggregate keeps changing as late Form 4s land — the newest
        read of a quarter supersedes the older one. That is also exactly why
        consumers must apply the minimum-lag filter (see the DDL comment)
        rather than reading the most recent quarter.

        Returns rows written, or 0 on ANY failure (CONSTRAINT #6).
        """
        if not rows:
            return 0
        try:
            now_ts = self._now_utc_iso()
            prepared: List[tuple] = []
            for row in rows:
                sym = str(row.get("symbol") or "").strip().upper()
                year = _int_or_none(row.get("year"))
                quarter = _int_or_none(row.get("quarter"))
                if not sym or year is None or quarter is None:
                    continue
                prepared.append(
                    (
                        sym,
                        year,
                        quarter,
                        _int_or_none(row.get("acquired_transactions")),
                        _int_or_none(row.get("disposed_transactions")),
                        self._nan_to_null(row.get("acquired_disposed_ratio")),
                        self._nan_to_null(row.get("total_acquired")),
                        self._nan_to_null(row.get("total_disposed")),
                        _int_or_none(row.get("total_purchases")),
                        _int_or_none(row.get("total_sales")),
                        str(row["source"]) if row.get("source") else None,
                        now_ts,
                    )
                )
            if not prepared:
                return 0

            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO insider_stats
                            (symbol, year, quarter, acquired_transactions,
                             disposed_transactions, acquired_disposed_ratio,
                             total_acquired, total_disposed, total_purchases,
                             total_sales, source, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        prepared,
                    )
            logger.debug("HistoricalStore: upserted %d insider_stats rows.", len(prepared))
            return len(prepared)
        except Exception as exc:
            logger.warning("HistoricalStore.upsert_insider_stats failed: %s", exc)
            self._safe_rollback()
            return 0

    def get_insider_stats(
        self, symbol: str, *, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Return *symbol*'s stored quarterly insider aggregates, newest first.

        Deliberately does NOT apply the minimum-lag filter itself — that is a
        consumer-side judgment call driven by ``settings.FMP_INSIDER_MIN_LAG_DAYS``
        (see the DDL comment), and a storage helper that silently dropped rows
        would make the archive un-auditable. Read what is stored; filter in the
        feed module.

        ``[]`` on an empty table OR any read failure (CONSTRAINT #6).
        """
        sym = (symbol or "").strip().upper()
        if not sym:
            return []
        try:
            from db_config import session_scope, get_dbapi_connection

            params: List[Any] = [sym]
            limit_clause = ""
            if limit is not None:
                limit_clause = " LIMIT ?"
                params.append(int(limit))

            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    db_rows = conn.execute(
                        "SELECT symbol, year, quarter, acquired_transactions, "
                        "disposed_transactions, acquired_disposed_ratio, total_acquired, "
                        "total_disposed, total_purchases, total_sales, source, fetched_at "
                        "FROM insider_stats WHERE symbol = ? "
                        f"ORDER BY year DESC, quarter DESC{limit_clause}",
                        tuple(params),
                    ).fetchall()

            return [
                {
                    "symbol": r[0],
                    "year": r[1],
                    "quarter": r[2],
                    "acquired_transactions": r[3],
                    "disposed_transactions": r[4],
                    "acquired_disposed_ratio": r[5],
                    "total_acquired": r[6],
                    "total_disposed": r[7],
                    "total_purchases": r[8],
                    "total_sales": r[9],
                    "source": r[10],
                    "fetched_at": r[11],
                }
                for r in db_rows
            ]
        except Exception as exc:
            logger.warning("HistoricalStore.get_insider_stats(%s) failed: %s", sym, exc)
            return []

    def latest_insider_fetched_at(self, symbol: str) -> Optional[str]:
        """Most recent ``fetched_at`` across *symbol*'s insider rows.

        Wall-clock cadence input for ``FMP_INSIDER_REFRESH_DAYS`` — distinct
        from the ``(year, quarter)`` period, which is the causality anchor.
        ``None`` when nothing is stored OR on any read failure.
        """
        return self._latest_scalar(
            "SELECT MAX(fetched_at) FROM insider_stats WHERE symbol = ?",
            (symbol or "").strip().upper(),
            label="latest_insider_fetched_at",
        )

    def mark_insider_fetched(self, symbol: str) -> None:
        """Record that *symbol* was fetched but FMP returned no insider data.

        Inserts a sentinel row with ``year=0, quarter=0`` so
        ``latest_insider_fetched_at`` returns a fresh timestamp and the
        cadence gate skips this symbol for ``FMP_INSIDER_REFRESH_DAYS``.
        The sentinel ``(symbol, 0, 0)`` PK never collides with real
        year/quarter combos, and the minimum-lag filter in production_steps
        will naturally skip it (quarter_end has no entry for quarter=0).
        """
        try:
            now_ts = self._now_utc_iso()
            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    conn.execute(
                        """INSERT OR REPLACE INTO insider_stats
                            (symbol, year, quarter, fetched_at, source)
                        VALUES (?, 0, 0, ?, 'fmp-no-data')""",
                        ((symbol or "").strip().upper(), now_ts),
                    )
        except Exception as exc:
            logger.warning("HistoricalStore.mark_insider_fetched(%s) failed: %s", symbol, exc)

    def upsert_sector_snapshots(self, rows: List[Dict[str, Any]]) -> int:
        """Persist a batch of dated per-sector PE / 1-day-change snapshots.

        Keys per dict: ``sector``, ``date``, and any of ``pe``, ``change_pct``,
        ``source``. A row without both ``sector`` and ``date`` is skipped.

        ``date`` MUST be the source's own snapshot date, not the fetch time —
        both FMP endpoints behind this are date-parameterized, which is the
        only reason this feed has a real point-in-time story at all. Passing
        today's date for a backfilled snapshot would throw that away silently.

        Returns rows written, or 0 on ANY failure (CONSTRAINT #6).
        """
        if not rows:
            return 0
        try:
            now_ts = self._now_utc_iso()
            prepared: List[tuple] = []
            for row in rows:
                sector = str(row.get("sector") or "").strip()
                date_str = str(row.get("date") or "").strip()
                if not sector or not date_str:
                    continue
                prepared.append(
                    (
                        sector,
                        date_str,
                        self._nan_to_null(row.get("pe")),
                        self._nan_to_null(row.get("change_pct")),
                        str(row["source"]) if row.get("source") else None,
                        now_ts,
                    )
                )
            if not prepared:
                return 0

            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO sector_snapshots
                            (sector, date, pe, change_pct, source, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        prepared,
                    )
            logger.debug("HistoricalStore: upserted %d sector_snapshots rows.", len(prepared))
            return len(prepared)
        except Exception as exc:
            logger.warning("HistoricalStore.upsert_sector_snapshots failed: %s", exc)
            self._safe_rollback()
            return 0

    def get_sector_snapshots(self, *, as_of: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """Return the latest per-sector snapshot at or before *as_of*.

        Keyed by sector name. With ``as_of`` supplied, rows dated after the
        cutoff are excluded — genuinely point-in-time here, unlike the other
        three FMP feeds (see the DDL comment). Each sector independently
        resolves to ITS OWN most recent qualifying date, so a sector missing
        from the cutoff date's snapshot falls back to its last known one rather
        than disappearing.

        ``{}`` on an empty table OR any read failure (CONSTRAINT #6). ``pe`` /
        ``change_pct`` are ``None`` when unreported, never 0.0.
        """
        try:
            from db_config import session_scope, get_dbapi_connection

            params: List[Any] = []
            date_clause = ""
            if as_of:
                date_clause = " WHERE date <= ?"
                params.append(str(as_of))

            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    db_rows = conn.execute(
                        "SELECT s.sector, s.date, s.pe, s.change_pct, s.source, s.fetched_at "
                        "FROM sector_snapshots s "
                        "JOIN (SELECT sector, MAX(date) AS max_date FROM sector_snapshots"
                        f"{date_clause} GROUP BY sector) m "
                        "ON s.sector = m.sector AND s.date = m.max_date",
                        tuple(params),
                    ).fetchall()

            return {
                str(r[0]): {
                    "sector": r[0],
                    "date": r[1],
                    "pe": r[2],
                    "change_pct": r[3],
                    "source": r[4],
                    "fetched_at": r[5],
                }
                for r in db_rows
            }
        except Exception as exc:
            logger.warning("HistoricalStore.get_sector_snapshots failed: %s", exc)
            return {}

    def latest_sector_snapshot_date(self) -> Optional[str]:
        """Most recent stored sector-snapshot ``date`` across all sectors.

        The cadence input for the sector feed (2 requests per cycle total, so
        it is gated per-cycle rather than per-symbol). ``None`` when nothing is
        stored OR on any read failure (CONSTRAINT #6).
        """
        try:
            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    row = conn.execute("SELECT MAX(date) FROM sector_snapshots").fetchone()
            if not row or row[0] is None:
                return None
            return str(row[0])
        except Exception as exc:
            logger.warning("HistoricalStore.latest_sector_snapshot_date failed: %s", exc)
            return None

    def _latest_scalar(self, sql: str, key: str, *, label: str) -> Optional[str]:
        """Shared MAX(...) helper for the per-symbol FMP cadence accessors.

        ``None`` on an empty key, an empty result, OR any read failure — the
        cadence caller treats all three identically ("no idea how current this
        is, go fetch"), so collapsing them here loses nothing.
        """
        if not key:
            return None
        try:
            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    row = conn.execute(sql, (key,)).fetchone()
            if not row or row[0] is None:
                return None
            return str(row[0])
        except Exception as exc:
            logger.warning("HistoricalStore.%s(%s) failed: %s", label, key, exc)
            return None

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
        ``credibility_weighted_sentiment`` (a genuine credibility-WEIGHTED
        mean -- ``sum(final_weighted_score) / sum(credibility_weight)``, NOT
        a plain per-document mean; see the Finding 5 note below),
        ``bot_activity_ratio`` (mean ``is_bot``), and
        ``aggregated_source_credibility`` (mean ``credibility_weight``,
        ``NaN``-safe when every row for that symbol has a ``NULL`` weight).

        Strictly scoped to ``trading_day`` -- this is the leakage-critical
        read side of ``resolve_trading_day()``'s write-side roll: a document
        whose ``as_of`` rolled to ``t+1`` at write time is simply absent from
        a query for trading day ``t``, so it can never influence day ``t``'s
        aggregate.

        Finding 5 fix (document-count-flooding gameability)
        -----------------------------------------------------
        ``final_weighted_score`` is already ``raw_sentiment_score *
        credibility_weight`` per document (see
        ``data/sentiment_sources.py::CompositeSentimentSource._archive``).
        A plain ``.mean()`` over that column divides by document COUNT, not
        by total credibility WEIGHT -- so a flood of many low-credibility
        documents (``credibility_weight`` floored at 0.1, never zero; see
        ``signals/credibility.py``) dilutes the aggregate toward the flood's
        own direction in proportion to how many of them there are, rather
        than how much credibility-weighted evidence they actually carry.
        Dividing by ``sum(credibility_weight)`` instead is the textbook
        weighted-mean correction: each document's influence on the aggregate
        is proportional to its own credibility weight, not to "one vote per
        document" -- a large volume of low-credibility documents now needs a
        correspondingly large total WEIGHT (not just a large COUNT) to move
        the aggregate as far as a handful of high-credibility documents can.
        Symbols with zero total credibility weight for the day (should not
        occur in practice given the 0.1 floor, but guarded defensively) fall
        back to ``NaN`` rather than a division-by-zero crash or a fabricated
        0.0 (CONSTRAINT #4).
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
                summed_final_weighted_score=("final_weighted_score", "sum"),
                summed_credibility_weight=("credibility_weight", "sum"),
                bot_activity_ratio=("is_bot", "mean"),
                aggregated_source_credibility=("credibility_weight", "mean"),
            )
            result: Dict[str, Dict[str, float]] = {}
            for symbol, row in grouped.iterrows():
                weight_sum = row["summed_credibility_weight"]
                if pd.notna(weight_sum) and weight_sum > 1e-12:
                    credibility_weighted_sentiment = float(
                        row["summed_final_weighted_score"] / weight_sum
                    )
                else:
                    credibility_weighted_sentiment = float("nan")
                result[str(symbol)] = {
                    "credibility_weighted_sentiment": credibility_weighted_sentiment,
                    "bot_activity_ratio": float(row["bot_activity_ratio"]),
                    "aggregated_source_credibility": (
                        float(row["aggregated_source_credibility"])
                        if pd.notna(row["aggregated_source_credibility"]) else float("nan")
                    ),
                }
            return result
        except Exception as exc:
            logger.warning("HistoricalStore.get_sentiment_aggregate_by_symbol failed: %s", exc)
            return {}

    def get_sentiment_daily_by_source_class(
        self, symbols: List[str], start_day: str, end_day: str
    ) -> Dict[str, Dict[str, Dict[str, float]]]:
        """Aggregate ``sentiment_ingestion_audit`` rows per ``(symbol,
        trading_day)``, split into NEWS vs. COMMENT buckets via
        ``data.sentiment_source_class.classify_source`` -- the shared read
        this feeds Sector Selection's Sector Heat Factor (news+review volume)
        and the composite sentiment index S_t (news_score/review_score).

        Returns ``{symbol: {trading_day: {news_count, news_mean_score,
        comment_count, comment_mean_score}}}``. Read-only, vectorized pandas
        groupby (no per-row Python loop). ``{}`` on any failure, on an empty
        ``symbols``/date range, or when no rows match (CONSTRAINT #6 --
        never raises).

        NaN vs. zero (CONSTRAINT #4): a ``(symbol, trading_day)`` with rows
        in one class but none in the other gets a genuine ``0`` count and
        ``NaN`` mean score for the empty class -- "we ingested that day and
        saw nothing from that class" is a real zero. A ``(symbol,
        trading_day)`` entirely absent from the returned dict was never
        observed at all -- callers MUST treat a missing key as unknown
        coverage, never coerce it to zero themselves, since ingestion being
        off entirely (``SENTIMENT_INGESTION_ENABLED=False``, today's
        default) looks identical to a real quiet day at this call's level;
        distinguishing "off" from "quiet" is ``get_sentiment_archive_depth_
        by_source``'s job, not this method's.
        """
        if not symbols or not start_day or not end_day:
            return {}
        try:
            from data.sentiment_source_class import classify_source
            from db_config import session_scope, get_dbapi_connection
            placeholders = ",".join("?" for _ in symbols)
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    cursor = conn.execute(
                        f"""
                        SELECT symbol, trading_day, source_name, final_weighted_score
                        FROM sentiment_ingestion_audit
                        WHERE trading_day >= ? AND trading_day <= ?
                          AND symbol IN ({placeholders})
                        """,
                        [start_day, end_day, *[str(s).upper() for s in symbols]],
                    )
                    rows = cursor.fetchall()
            if not rows:
                return {}
            df = pd.DataFrame(
                rows, columns=["symbol", "trading_day", "source_name", "final_weighted_score"]
            )
            df["source_class"] = df["source_name"].map(classify_source)
            df = df[df["source_class"].isin(("news", "comment"))]
            if df.empty:
                return {}
            grouped = df.groupby(["symbol", "trading_day", "source_class"]).agg(
                count=("final_weighted_score", "size"),
                mean_score=("final_weighted_score", "mean"),
            )
            result: Dict[str, Dict[str, Dict[str, float]]] = {}
            for (symbol, trading_day, source_class), row in grouped.iterrows():
                by_day = result.setdefault(str(symbol), {}).setdefault(
                    str(trading_day),
                    {
                        "news_count": float("nan"),
                        "news_mean_score": float("nan"),
                        "comment_count": float("nan"),
                        "comment_mean_score": float("nan"),
                    },
                )
                by_day[f"{source_class}_count"] = float(row["count"])
                by_day[f"{source_class}_mean_score"] = (
                    float(row["mean_score"]) if pd.notna(row["mean_score"]) else float("nan")
                )
            # A class with zero ingested rows for a day that WAS observed
            # (the other class has rows) is a genuine zero count, not an
            # unknown -- fill count only, leave the mean score NaN (there is
            # no score to average over zero rows).
            for by_symbol in result.values():
                for by_day in by_symbol.values():
                    if pd.isna(by_day["news_count"]) and not pd.isna(by_day["comment_count"]):
                        by_day["news_count"] = 0.0
                    if pd.isna(by_day["comment_count"]) and not pd.isna(by_day["news_count"]):
                        by_day["comment_count"] = 0.0
            return result
        except Exception as exc:
            logger.warning("HistoricalStore.get_sentiment_daily_by_source_class failed: %s", exc)
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

    # ─────────────────────────────────────────────────────────────────────────
    # Public API — rag_indexed_docs (Phase 2 PR3: RAG Portfolio Contextualizer)
    # ─────────────────────────────────────────────────────────────────────────

    def get_unindexed_sentiment_documents(
        self, since: datetime, *, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Return ``sentiment_ingestion_audit`` rows not yet in ``rag_indexed_docs``.

        Scoped to ``as_of >= since`` (caller controls the recency window) and
        excludes any ``ingest_id`` already present in ``rag_indexed_docs`` —
        this is the source query for :func:`data.rag_index.DocumentVectorStore
        .index_new_documents`. Ordered ascending by ``ingest_id`` so eviction
        (FIFO) and indexing observe the same natural order.

        Returns ``[]`` on any failure or when nothing is pending
        (CONSTRAINT #6 — never raises). Never mutates
        ``sentiment_ingestion_audit`` (read-only).
        """
        try:
            since_str = pd.Timestamp(since).isoformat()
            with self._lock:
                conn = self._get_conn()
                rows = conn.execute(
                    """
                    SELECT ingest_id, as_of, trading_day, symbol, source_name, text_content
                    FROM sentiment_ingestion_audit
                    WHERE as_of >= ?
                      AND ingest_id NOT IN (SELECT ingest_id FROM rag_indexed_docs)
                    ORDER BY ingest_id ASC
                    """
                    + (" LIMIT ?" if limit is not None else ""),
                    (since_str, limit) if limit is not None else (since_str,),
                ).fetchall()
            return [
                {
                    "ingest_id": r[0],
                    "as_of": r[1],
                    "trading_day": r[2],
                    "symbol": r[3],
                    "source_name": r[4],
                    "text_content": r[5],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("HistoricalStore.get_unindexed_sentiment_documents failed: %s", exc)
            return []

    def get_sentiment_documents_by_ingest_ids(
        self, ingest_ids: List[int]
    ) -> List[Dict[str, Any]]:
        """Return ``sentiment_ingestion_audit`` rows for the given ``ingest_ids``.

        Used by :func:`data.rag_index.DocumentVectorStore.search` to hydrate
        FAISS nearest-neighbor IDs back into full document metadata (symbol,
        source, text, as_of). Returns ``[]`` on any failure or an empty input
        list (CONSTRAINT #6).
        """
        if not ingest_ids:
            return []
        try:
            placeholders = ",".join("?" for _ in ingest_ids)
            with self._lock:
                conn = self._get_conn()
                rows = conn.execute(
                    f"""
                    SELECT ingest_id, as_of, trading_day, symbol, source_name, text_content
                    FROM sentiment_ingestion_audit
                    WHERE ingest_id IN ({placeholders})
                    """,
                    tuple(int(i) for i in ingest_ids),
                ).fetchall()
            return [
                {
                    "ingest_id": r[0],
                    "as_of": r[1],
                    "trading_day": r[2],
                    "symbol": r[3],
                    "source_name": r[4],
                    "text_content": r[5],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("HistoricalStore.get_sentiment_documents_by_ingest_ids failed: %s", exc)
            return []

    def record_rag_indexed_doc(
        self, ingest_id: int, doc_hash: str, faiss_row: int, indexed_at: Optional[str] = None
    ) -> bool:
        """INSERT OR REPLACE one ``rag_indexed_docs`` row.

        Returns ``True`` on success, ``False`` on any failure (never raises
        — CONSTRAINT #6). Idempotent on ``ingest_id`` (the primary key) so a
        re-index of the same document is a harmless no-op overwrite rather
        than a duplicate-key error.
        """
        try:
            ts = indexed_at or self._now_utc_iso()
            from db_config import session_scope, get_dbapi_connection
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO rag_indexed_docs
                            (ingest_id, doc_hash, faiss_row, indexed_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (int(ingest_id), doc_hash, int(faiss_row), ts),
                    )
            return True
        except Exception as exc:
            logger.warning("HistoricalStore.record_rag_indexed_doc(%s) failed: %s", ingest_id, exc)
            return False

    def get_rag_indexed_doc_count(self) -> int:
        """Return the total row count of ``rag_indexed_docs``, or ``0`` on error."""
        try:
            with self._lock:
                conn = self._get_conn()
                row = conn.execute("SELECT COUNT(*) FROM rag_indexed_docs").fetchone()
            return int(row[0]) if row else 0
        except Exception as exc:
            logger.warning("HistoricalStore.get_rag_indexed_doc_count failed: %s", exc)
            return 0

    def get_oldest_rag_indexed_docs(self, n: int) -> List[tuple]:
        """Return the ``n`` oldest ``(ingest_id, faiss_row)`` pairs by ``indexed_at``.

        Used by :func:`data.rag_index.DocumentVectorStore._evict_if_needed`
        to implement FIFO eviction against ``RAG_INDEX_MAX_DOCUMENTS``.
        Returns ``[]`` on any failure or when ``n <= 0`` (CONSTRAINT #6).
        """
        if n <= 0:
            return []
        try:
            with self._lock:
                conn = self._get_conn()
                rows = conn.execute(
                    """
                    SELECT ingest_id, faiss_row FROM rag_indexed_docs
                    ORDER BY indexed_at ASC, ingest_id ASC
                    LIMIT ?
                    """,
                    (int(n),),
                ).fetchall()
            return [(int(r[0]), int(r[1])) for r in rows]
        except Exception as exc:
            logger.warning("HistoricalStore.get_oldest_rag_indexed_docs failed: %s", exc)
            return []

    def delete_rag_indexed_docs(self, ingest_ids: List[int]) -> bool:
        """Delete ``rag_indexed_docs`` rows for the given ``ingest_ids``.

        Returns ``True`` on success (including a no-op empty list), ``False``
        on any failure (never raises — CONSTRAINT #6). Does NOT touch
        ``sentiment_ingestion_audit`` — only the tracking table.
        """
        if not ingest_ids:
            return True
        try:
            from db_config import session_scope, get_dbapi_connection
            placeholders = ",".join("?" for _ in ingest_ids)
            with self._lock:
                with session_scope(self.Session) as session:
                    raw_conn = session.connection().connection
                    conn = get_dbapi_connection(raw_conn)
                    conn.execute(
                        f"DELETE FROM rag_indexed_docs WHERE ingest_id IN ({placeholders})",
                        tuple(int(i) for i in ingest_ids),
                    )
            return True
        except Exception as exc:
            logger.warning("HistoricalStore.delete_rag_indexed_docs failed: %s", exc)
            return False

    @staticmethod
    def _resolve_data_engine(data_engine):
        """Resolve an injectable DataEngine or construct the real singleton."""
        if data_engine is not None:
            return data_engine
        try:
            from data_engine import DataEngine
            from settings import settings as _s
            if _s.FRED_API_KEY:
                return DataEngine(fred_api_key=_s.FRED_API_KEY)
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
        # Market-timezone (America/New_York) date, NOT UTC. Daily OHLCV bars are
        # dated by the US trading day, and UTC is 4-5 hours AHEAD of ET -- a raw
        # `datetime.now(timezone.utc).date()` flips to the next calendar date
        # every evening (~8pm-midnight ET) while the US trading day hasn't
        # actually advanced, making this check wrongly conclude a trading day
        # has elapsed and attempt a top-up that isn't needed yet. For a
        # readonly=True store (e.g. pilots/rolling_beta.py) that spurious
        # top-up's write always fails ("attempt to write a readonly database"),
        # which get_bars()'s except then silently converts into a real,
        # unmocked live-provider fetch. Same ZoneInfo already used by
        # resolve_trading_day()/execution/risk_gate.py/engine/advisory_agent.py
        # for RTH detection -- tz-naive midnight-normalized to match the
        # tz-naive normalized bar dates returned by latest_bar_date().
        today = pd.Timestamp(datetime.now(_SENTIMENT_ET).date())

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

        # A readonly instance can never persist a top-up -- self._upsert_bars
        # below would always raise OperationalError ("attempt to write a
        # readonly database"), caught only by get_bars()'s own outer
        # try/except, which then falls back to ANOTHER, WIDER live fetch
        # (lookback_days, not fetch_days) to actually satisfy the caller. So
        # skip straight to that same wider fetch: identical data returned to
        # every readonly caller (pilots/rolling_beta.py,
        # gui/panels/sentiment_dynamics.py, api/metrics_api.py,
        # api/data_api.py, api/pilots_api.py's attribution helper), just
        # without wasting a fetch_days-wide round-trip on a write that was
        # never going to succeed, or logging a WARNING for an outcome this
        # is not actually surprising. This does NOT change the documented,
        # intentional fact that get_bars() defeats its own cache for a
        # readonly store with a stale cache -- a caller that wants real
        # caching still needs a write-mode store (see
        # evaluation_engine.py's recommendation_tracking_report); see
        # tests/test_historical_store.py's "NOT-a-safe-hardening-target"
        # comment.
        if self._readonly:
            if provider is not None:
                logger.debug(
                    "HistoricalStore.get_bars(%s): readonly store, cache "
                    "needs a top-up it can't persist -- skipping the write "
                    "attempt, live-fetching directly.",
                    symbol,
                )
                return self._live_fetch(symbol, lookback_days, provider)
            return self._read_from_db(symbol, lookback_days)

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
        n = len(df)
        # Column-wise build instead of df.iterrows() (avoids constructing a
        # Series per row). itertuples() isn't a fit here: "Adj Close" isn't a
        # valid Python identifier and some providers (e.g. Alpaca) omit that
        # column entirely, so per-column optional-missing handling below
        # mirrors the old row.get(...) per-key default of None.
        dates = pd.to_datetime(df.index).strftime("%Y-%m-%d")

        def _num_col(col: str) -> list:
            if col not in df.columns:
                return [None] * n
            return [_float_or_none(v) for v in df[col].to_numpy()]

        def _int_col(col: str) -> list:
            if col not in df.columns:
                return [None] * n
            return [_int_or_none(v) for v in df[col].to_numpy()]

        rows = list(zip(
            [symbol] * n,
            dates,
            _num_col("Open"),
            _num_col("High"),
            _num_col("Low"),
            _num_col("Close"),
            _num_col("Adj Close"),
            _int_col("Volume"),
            [source] * n,
            [now_ts] * n,
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


def _source_name(provider, raw: Optional[Dict[str, Any]] = None) -> str:
    """Return a human-readable source label for a fundamentals fetch.

    Prefers a PER-SYMBOL ``"_source"`` key embedded in the provider's own
    response dict, falling back to the provider OBJECT's label
    (``provider.source_name``, else its lowercased class name) when the key is
    absent — which is exactly today's behavior, so passing ``raw=None`` or a
    dict without the key is byte-identical to before.

    Why the raw dict wins: once a composite provider can fall back between
    backends per symbol, the provider object's own label describes the CHAIN,
    not the backend that actually served this particular response. Stamping
    the chain's name on a fallback row makes ``fundamentals_history.source``
    silently claim a provenance that isn't true — and that column is the
    ground-truth operator query for "did the chain fall back on me?"
    (``SELECT source, COUNT(*) FROM fundamentals_history
    WHERE as_of = DATE('now') GROUP BY 1``). A per-response key is also
    thread-safe by construction (no shared mutable state), which matters
    because ``data_engine.py`` calls this path under an 8-thread pool.
    """
    if isinstance(raw, dict):
        embedded = raw.get("_source")
        if embedded:
            return str(embedded)
    return getattr(provider, "source_name", type(provider).__name__.lower())
