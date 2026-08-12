# Tier 2.3 — Historical Persistence & Incremental Fetch

**Status: SHIPPED — all three phases complete.** This document is retained as the
historical work order / design record. `data/historical_store.py` implements Phase 1
(`price_bars`, wired into `main.py`/`main_orchestrator.py`), Phase 2
(`account_snapshots`/`account_positions`, three-tier DB → JSON cache → live read order
in `data/robinhood_portfolio.py`), and Phase 3 (`fundamentals_history` + `macro_history`,
wired into `processing_engine.py` and `macro_engine.py`). See the `data/historical_store.py`
bullet in `CLAUDE.md` for the live, maintained description. Treat any "Phase N — not
started" / "agent prompt" language below as describing the pre-implementation state only.
**Branch convention:** `agent/claude-code/tier-2-3-historical-persistence-phase-N`
**Storage backend:** raw `sqlite3` in `quant_platform.db` (matches `forecasting/forecast_tracker.py`; not SQLAlchemy ORM)

---

## Problem

Every `run_once()` / `main_orchestrator` cycle currently refetches:
- ~2 years of OHLCV per symbol from Yahoo (slow, rate-limit-prone, identical to yesterday's bars)
- All Finnhub/yfinance fundamentals (cache is in-process only — gone on restart)
- All FRED macro series (VIX, yield curve, Sahm Rule — also discarded each restart)

The Robinhood account snapshot already persists across launches as `cache/account_snapshot.json` (20 h TTL), but a single overwritten JSON file is not queryable, joinable with trades, or historical.

## Goal

At launch, hydrate everything from the local DB. Only **current/intraday prices** hit Yahoo live (the existing 30-second quote cache path is untouched). Bars/fundamentals/macro are append-only and topped up incrementally.

## Principles

1. **Backward-compatible.** A new `HISTORICAL_STORE_ENABLED` flag (default `True`) gates all routing; setting it `False` reproduces today's behavior exactly.
2. **No fabricated data (CONSTRAINT #4).** A cache miss returns `None` / empty DataFrame; the caller decides whether to live-fetch.
3. **Dead-letter resilient (CONSTRAINT #6).** Every DB op is in try/except → degrades to live-fetch, never crashes the pipeline.
4. **Identical shape contract.** `HistoricalStore.get_bars()` returns the same OHLCV DataFrame shape as `DataEngine.fetch_technical_raw()` (timezone-naive `DatetimeIndex`, columns `Open, High, Low, Close, Volume`) so every signal/forecasting/strategy module runs unchanged.
5. **One module, one DB file.** All new tables live in `quant_platform.db` alongside `trades`, `iv_history`, `forecast_errors`. New module: `data/historical_store.py`.

## Tables

All created via `CREATE TABLE IF NOT EXISTS` from `HistoricalStore.__init__` (lazy; `database_setup.py`'s config-driven tables are not touched).

### `price_bars`
```sql
CREATE TABLE IF NOT EXISTS price_bars (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,           -- ISO date 'YYYY-MM-DD'
    open REAL, high REAL, low REAL, close REAL, adj_close REAL,
    volume INTEGER,
    source TEXT NOT NULL,         -- 'yfinance', 'alpaca', etc.
    fetched_at TEXT NOT NULL,     -- UTC ISO timestamp of the row's last write
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_price_bars_symbol_date ON price_bars(symbol, date);
```
**Incremental rule:** `SELECT MAX(date) FROM price_bars WHERE symbol = ?` → fetch `(max_date, today]` from the provider. First call for a symbol = `BARS_BACKFILL_DAYS` (default 504 ≈ 2 years) backfill. Daily call = 1–5 new bars.

### `fundamentals_history`
```sql
CREATE TABLE IF NOT EXISTS fundamentals_history (
    symbol TEXT NOT NULL,
    as_of TEXT NOT NULL,          -- ISO date the snapshot was taken
    pe_ratio REAL, pb_ratio REAL, roe REAL, dividend_yield REAL,
    market_cap REAL, eps REAL, operating_margin REAL, debt_to_equity REAL,
    raw_json TEXT,                -- full fundamentals dict for PIT replay
    source TEXT NOT NULL,         -- 'finnhub' or 'yfinance'
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (symbol, as_of)
);
CREATE INDEX IF NOT EXISTS idx_fund_history_symbol ON fundamentals_history(symbol);
```
**Incremental rule:** if a row for `(symbol, today)` exists AND `FUNDAMENTALS_REFRESH_DAYS` hasn't elapsed, return cached. Else 1 snapshot/day.

### `macro_history`
```sql
CREATE TABLE IF NOT EXISTS macro_history (
    series_id TEXT NOT NULL,       -- 'VIXCLS', 'T10Y2Y', 'SAHMREALTIME', etc.
    date TEXT NOT NULL,
    value REAL,
    source TEXT NOT NULL,          -- typically 'fred'
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (series_id, date)
);
CREATE INDEX IF NOT EXISTS idx_macro_history_series ON macro_history(series_id, date);
```
**Incremental rule:** same as bars — `MAX(date) WHERE series_id=?` → delta fetch only.

### `account_snapshots` + `account_positions`
```sql
CREATE TABLE IF NOT EXISTS account_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at TEXT NOT NULL,         -- UTC ISO timestamp
    buying_power REAL,
    total_equity REAL,
    total_dividends REAL,
    source TEXT NOT NULL              -- 'robinhood'
);
CREATE INDEX IF NOT EXISTS idx_acct_snap_ts ON account_snapshots(fetched_at);

CREATE TABLE IF NOT EXISTS account_positions (
    snapshot_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    qty REAL, avg_cost REAL, current_price REAL,
    market_value REAL, unrealized_pl REAL,
    dividends_received REAL,
    name TEXT,
    PRIMARY KEY (snapshot_id, symbol),
    FOREIGN KEY (snapshot_id) REFERENCES account_snapshots(snapshot_id)
);
```
**Append-only.** Every successful live RH fetch writes one row + N position rows. The existing `cache/account_snapshot.json` stays as the secondary fallback.

## Public API (`data/historical_store.py`)

```python
class HistoricalStore:
    def __init__(self, db_path: str = "quant_platform.db") -> None: ...

    # ── Bars (Phase 1) ────────────────────────────────────────────────
    def get_bars(
        self,
        symbol: str,
        lookback_days: int = 504,
        *,
        provider=None,           # injectable; defaults to data.market_data.get_provider()
    ) -> pd.DataFrame:
        """DB-cached OHLCV with incremental top-up. Shape identical to
        DataEngine.fetch_technical_raw(): tz-naive DatetimeIndex, columns
        [Open, High, Low, Close, Volume]. Empty DataFrame on total failure."""

    def latest_bar_date(self, symbol: str) -> Optional[pd.Timestamp]: ...

    # ── Account snapshots (Phase 2) ────────────────────────────────────
    def save_account_snapshot(self, snapshot: "AccountSnapshot") -> int:
        """Returns snapshot_id."""

    def latest_account_snapshot(self) -> Optional["AccountSnapshot"]: ...

    def account_snapshot_history(
        self, since: Optional[datetime] = None
    ) -> pd.DataFrame: ...

    # ── Fundamentals (Phase 3) ─────────────────────────────────────────
    def get_fundamentals(
        self,
        symbol: str,
        max_age_days: int = 1,
        *,
        provider=None,
    ) -> Dict[str, float]:
        """Cached daily; returns {} if neither DB nor live succeeds."""

    # ── Macro (Phase 3) ────────────────────────────────────────────────
    def get_macro(
        self,
        series_id: str,
        *,
        lookback_days: Optional[int] = None,
        data_engine=None,
    ) -> pd.Series: ...
```

All methods wrapped in try/except; failures logged at WARNING and returned as the "empty" sentinel (empty DataFrame / `None` / `{}`).

## New settings (`settings.py`)

| Setting | Default | Purpose |
|---|---|---|
| `HISTORICAL_STORE_ENABLED` | `True` | Master flag; `False` reproduces today's behavior |
| `BARS_BACKFILL_DAYS` | `504` | First-fetch backfill window (~2 years) |
| `FUNDAMENTALS_REFRESH_DAYS` | `1` | Skip refetch within this many days |
| `MACRO_REFRESH_HOURS` | `12` | Skip macro top-up within this window |

## Metrics this unlocks (no new vendor)

Once `price_bars` is persisted, these become trivially cheap and worth surfacing as new `COLUMN_SCHEMA` entries (separate task, not in this scope):
- Rolling beta vs SPY
- Per-symbol max drawdown / Sortino / Calmar
- Sector relative strength (symbol vs sector ETF)
- Dividend-adjusted total return from `adj_close`

Plus the documented PIT-fundamentals gap in `tests/test_validation_multifactor.py` is closed by `fundamentals_history.raw_json`.

---

# Phase-by-phase agent prompts

Each prompt below is self-contained — a fresh agent must be able to act on it without seeing this doc's introduction. Paste the entire prompt block into the agent invocation.

---

## Phase 1 — `price_bars` table + incremental bars (highest-value, smallest)

**Branch:** `agent/claude-code/tier-2-3-phase-1-price-bars`

```
Implement Tier 2.3 Phase 1 — persistent price-bar storage with incremental
fetch. The goal: every run currently refetches ~2 years of OHLCV per symbol
from yfinance, even though yesterday's bar for AAPL will never change.
This phase moves bars into quant_platform.db and tops up only the delta.

Files to create:
  - data/historical_store.py — new module, raw sqlite3 (NOT SQLAlchemy).
    Mirror the pattern in forecasting/forecast_tracker.py:97-110:
      conn = sqlite3.connect(self._db_path)
      conn.execute("PRAGMA journal_mode=WAL")
    Class HistoricalStore with __init__(db_path="quant_platform.db") that
    lazily runs CREATE TABLE IF NOT EXISTS for price_bars. Schema:
      CREATE TABLE IF NOT EXISTS price_bars (
          symbol TEXT NOT NULL,
          date TEXT NOT NULL,
          open REAL, high REAL, low REAL, close REAL, adj_close REAL,
          volume INTEGER,
          source TEXT NOT NULL,
          fetched_at TEXT NOT NULL,
          PRIMARY KEY (symbol, date)
      );
      CREATE INDEX IF NOT EXISTS idx_price_bars_symbol_date
          ON price_bars(symbol, date);

    Public methods:
      latest_bar_date(symbol) -> Optional[pd.Timestamp]
      get_bars(symbol, lookback_days=504, *, provider=None) -> pd.DataFrame

    get_bars contract — MUST satisfy:
      1. Returns a DataFrame with tz-naive DatetimeIndex sorted ascending and
         columns exactly [Open, High, Low, Close, Volume] — byte-identical to
         the shape DataEngine.fetch_technical_raw() returns. Existing
         signal/forecasting/strategy code must run unchanged on the result.
      2. Incremental rule: compute max_date = latest_bar_date(symbol). If
         max_date < today, fetch (max_date, today] from
         data.market_data.get_provider().get_intraday_bars(symbol,
         lookback_days=delta). If max_date is None, full backfill =
         settings.BARS_BACKFILL_DAYS bars.
      3. Every fetched bar is upserted via INSERT OR REPLACE (so a same-day
         retry overwrites cleanly). source='yfinance' (or whatever the
         provider reports).
      4. Returns the trailing `lookback_days` rows from the DB after the
         top-up. NEVER returns fabricated/zero rows — if the DB is empty AND
         the live fetch fails, return an empty DataFrame (CONSTRAINT #4).
      5. All DB operations wrapped in try/except; on failure, log WARNING
         and FALL BACK to a direct provider fetch (no DB write), returning
         whatever the provider yields. Never raises (CONSTRAINT #6).

Settings to add (settings.py):
  HISTORICAL_STORE_ENABLED: bool = True
  BARS_BACKFILL_DAYS: int = 504

Wiring (behind the flag):
  - In main.py's _fetch_bars_for_universe (the function that prepares the
    per-symbol bars dict feeding engine.advisory.evaluate), route through
    HistoricalStore.get_bars when settings.HISTORICAL_STORE_ENABLED is True.
    When False, call the provider directly as today.
  - The 30-second live quote path in data/market_data.py is UNTOUCHED.
    The user wants: "only pull current prices from Yahoo." Persisted bars
    feed indicators/forecasts; live quotes still hit the provider every run.

Tests to write (tests/test_historical_store.py):
  - test_table_created_on_init: __init__ on a temp db creates price_bars
    + idx_price_bars_symbol_date.
  - test_first_fetch_full_backfill: empty DB + mock provider returning a
    504-row OHLCV frame → exactly one provider call with lookback ~= 504;
    DB now has 504 rows.
  - test_incremental_delta_only: pre-seed DB with rows up to 5 days ago,
    call get_bars again → provider called with lookback ~= 5 (NOT 504).
    Assert via a counter on the mock that the second call's lookback is
    materially smaller than the first.
  - test_shape_matches_data_engine: returned DataFrame has tz-naive
    DatetimeIndex (assert index.tz is None), columns exactly
    ["Open","High","Low","Close","Volume"], sorted ascending.
  - test_no_fabrication_on_total_failure: empty DB + provider raises →
    get_bars returns an empty DataFrame, NOT a zero-filled frame.
  - test_dead_letter_db_error: monkeypatch sqlite3.connect to raise →
    get_bars still returns provider data (live fallback).
  - test_upsert_idempotent: call get_bars twice in a row with the same
    provider data → DB row count unchanged on the second call.

Gravity audit step to add (Gravity AI Review Suite.py):
  step_60_historical_persistence_audit_phase1 — verify:
    - HistoricalStore importable from data.historical_store
    - price_bars table exists after instantiation against a temp db
    - settings.HISTORICAL_STORE_ENABLED is True by default
    - settings.BARS_BACKFILL_DAYS == 504
    - get_bars returns tz-naive DatetimeIndex with the 5 OHLCV cols
    - get_bars on a DB-error path falls back to live provider (no raise)
    - main.py source references HistoricalStore (wiring guard)
    - tests/test_historical_store.py exists

CLAUDE.md updates:
  - Add a new bullet under the "data/" architecture section describing
    HistoricalStore, the price_bars schema, the incremental rule, and the
    HISTORICAL_STORE_ENABLED flag.
  - Add a "Historical persistence invariants" line to the Conventions
    section: "When HISTORICAL_STORE_ENABLED=True, OHLCV bars MUST come from
    HistoricalStore.get_bars (which handles incremental top-up). A direct
    provider.get_intraday_bars call in pipeline code is a regression —
    wrap it in get_bars or extend HistoricalStore."

Do NOT in this phase:
  - Touch fundamentals or macro (Phase 3).
  - Touch the Robinhood snapshot path (Phase 2).
  - Add new COLUMN_SCHEMA columns (separate task — beta/sortino/etc. are
    follow-ups, not this phase).
  - Change main_orchestrator.py — wiring is main.py only for Phase 1
    so the smoke test surface stays small. Phase 2 will revisit.

End of phase: open a PR titled "feat: Tier 2.3 Phase 1 — price_bars
incremental fetch (#XX)". The PR body should show: (1) cold-start
get_bars call count vs warm-start (proving the delta-only top-up), (2)
the get_bars shape contract test passing, (3) Gravity step 60 passing.
```

---

## Phase 2 — `account_snapshots` table (RH snapshot in DB)

**Branch:** `agent/claude-code/tier-2-3-phase-2-account-db`

```
Implement Tier 2.3 Phase 2 — persist Robinhood account snapshots into
quant_platform.db so the GUI and reports can show holdings at launch
without any live login. The existing cache/account_snapshot.json daily
cache stays as the secondary fallback.

PREREQUISITE: Phase 1 (data/historical_store.py with HistoricalStore
class + price_bars table) must be merged. This phase EXTENDS that class.

Files to modify:
  - data/historical_store.py — add two tables + four methods to the
    existing HistoricalStore class.

Tables (added in __init__ via CREATE TABLE IF NOT EXISTS):
  CREATE TABLE IF NOT EXISTS account_snapshots (
      snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
      fetched_at TEXT NOT NULL,
      buying_power REAL,
      total_equity REAL,
      total_dividends REAL,
      source TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_acct_snap_ts ON account_snapshots(fetched_at);

  CREATE TABLE IF NOT EXISTS account_positions (
      snapshot_id INTEGER NOT NULL,
      symbol TEXT NOT NULL,
      qty REAL, avg_cost REAL, current_price REAL,
      market_value REAL, unrealized_pl REAL,
      dividends_received REAL,
      name TEXT,
      PRIMARY KEY (snapshot_id, symbol),
      FOREIGN KEY (snapshot_id) REFERENCES account_snapshots(snapshot_id)
  );

Methods to add:
  save_account_snapshot(snapshot: AccountSnapshot) -> int
    INSERT one row into account_snapshots (returns snapshot_id), then bulk
    insert positions via executemany. Wrap in a single transaction
    (BEGIN/COMMIT) so a partial write never corrupts state. On any error,
    log WARNING, ROLLBACK, return -1 (sentinel; never raises).

  latest_account_snapshot() -> Optional[AccountSnapshot]
    SELECT the row with MAX(fetched_at) from account_snapshots + JOIN
    account_positions on snapshot_id. Reconstruct an AccountSnapshot
    dataclass (data/robinhood_portfolio.py:76, 130 area) including the
    positions dict. Returns None on empty / error.

  account_snapshot_history(since: Optional[datetime] = None) -> pd.DataFrame
    SELECT fetched_at, buying_power, total_equity, total_dividends from
    account_snapshots WHERE fetched_at >= since (or all if None) ORDER BY
    fetched_at. Returns empty DataFrame on error. Used by future
    equity-curve panels (out of scope here).

Wiring (data/robinhood_portfolio.py):
  - In fetch_account_snapshot(), AFTER a successful live fetch (current
    line 460 _write_cache(snapshot) area), also call:
      store = HistoricalStore()
      store.save_account_snapshot(snapshot)
    Wrap in try/except — a DB failure must NEVER break the live-fetch
    return path. The JSON cache stays.
  - At the top of fetch_account_snapshot(), BEFORE the existing cached
    check at line 448 (cached = _read_cache()), add a DB-first read path:
      if not force:
          try:
              store = HistoricalStore()
              db_snap = store.latest_account_snapshot()
              if db_snap is not None and not db_snap.is_stale(max_age_hours):
                  logger.info("Using DB-cached account snapshot (age %.1fh)",
                              db_snap.age_hours())
                  return db_snap
          except Exception as exc:
              logger.debug("DB snapshot read failed, falling through: %s", exc)
    The existing JSON cache then acts as the third tier (DB → JSON → live).

Tests to add (tests/test_historical_store.py):
  Class TestAccountSnapshotPersistence:
    - test_save_and_load_round_trip: build a synthetic AccountSnapshot
      with 3 positions, save it, latest_account_snapshot() returns an
      equal AccountSnapshot (same positions, equity, dividends, fetched_at
      within microsecond tolerance).
    - test_save_failure_does_not_raise: monkeypatch sqlite3.connect to
      raise → save_account_snapshot returns -1, no exception propagates.
    - test_latest_with_empty_db: returns None.
    - test_multiple_snapshots_returns_newest: save two snapshots 1 hour
      apart, latest_account_snapshot returns the newer one.
    - test_history_dataframe_shape: save 3 snapshots, history() returns
      a 3-row DataFrame with the 4 metric columns.
    - test_no_secrets_in_db: AccountSnapshot.to_dict() already filters
      secrets; assert no column in either table is named like "password"
      / "mfa" / "token" (regression guard).

Also add to tests/test_robinhood_portfolio.py a TestDBIntegration class:
    - test_db_read_path_used_when_fresh: pre-populate the DB with a fresh
      snapshot, call fetch_account_snapshot(force=False) → no live fetch
      attempted (assert robin_stocks.login not called), returned snapshot
      matches the DB row.
    - test_falls_through_to_json_on_db_error: monkeypatch HistoricalStore
      to raise on instantiation → fetch_account_snapshot still works via
      the JSON cache fallback (no regression).

Gravity audit (extend or add):
  step_60_historical_persistence_audit_phase2 (separate from Phase 1's
  step) — verify:
    - account_snapshots and account_positions tables exist after init
    - save_account_snapshot + latest_account_snapshot round-trip works
    - save_account_snapshot returns -1 on DB error (no raise)
    - data/robinhood_portfolio.py source references HistoricalStore in
      both fetch_account_snapshot's read path and post-live-fetch write
    - tests/test_robinhood_portfolio.py::TestDBIntegration exists

CLAUDE.md updates:
  - Extend the HistoricalStore bullet to cover account_snapshots /
    account_positions and the three-tier read order (DB → JSON → live).
  - Note in the data/robinhood_portfolio.py bullet that the JSON cache
    is now the secondary fallback, with the DB as primary.

Do NOT:
  - Modify the AccountSnapshot dataclass shape (data/robinhood_portfolio.py
    line 130 area). The DB tables are derived FROM the dataclass, not the
    reverse. CONSTRAINT #1 (source of truth: AccountSnapshot remains the
    in-memory truth; DB persists it).
  - Add live-fetch logic to HistoricalStore. It's purely a storage layer;
    fetch_account_snapshot remains the orchestrator.

End of phase: PR titled "feat: Tier 2.3 Phase 2 — account snapshots in
DB". Body should demo: GUI relaunch with no internet → Holdings & P&L
still populated from DB.
```

---

## Phase 3 — `fundamentals_history` + `macro_history`

**Branch:** `agent/claude-code/tier-2-3-phase-3-fundamentals-macro`

```
Implement Tier 2.3 Phase 3 — persist Finnhub/yfinance fundamentals and
FRED macro series. Same incremental pattern as Phase 1 bars: daily
snapshots, skip fetch when fresh.

PREREQUISITE: Phases 1 and 2 merged. Extends data/historical_store.py.

Tables to add (CREATE TABLE IF NOT EXISTS in __init__):
  CREATE TABLE IF NOT EXISTS fundamentals_history (
      symbol TEXT NOT NULL,
      as_of TEXT NOT NULL,
      pe_ratio REAL, pb_ratio REAL, roe REAL, dividend_yield REAL,
      market_cap REAL, eps REAL, operating_margin REAL, debt_to_equity REAL,
      raw_json TEXT,
      source TEXT NOT NULL,
      fetched_at TEXT NOT NULL,
      PRIMARY KEY (symbol, as_of)
  );
  CREATE INDEX IF NOT EXISTS idx_fund_history_symbol
      ON fundamentals_history(symbol);

  CREATE TABLE IF NOT EXISTS macro_history (
      series_id TEXT NOT NULL,
      date TEXT NOT NULL,
      value REAL,
      source TEXT NOT NULL,
      fetched_at TEXT NOT NULL,
      PRIMARY KEY (series_id, date)
  );
  CREATE INDEX IF NOT EXISTS idx_macro_history_series
      ON macro_history(series_id, date);

Methods to add:
  get_fundamentals(symbol, max_age_days=1, *, provider=None) -> Dict[str, float]
    1. SELECT the newest row from fundamentals_history for symbol.
    2. If row exists AND age < max_age_days → return the typed-column dict
       (pe_ratio, pb_ratio, roe, dividend_yield, market_cap, eps,
       operating_margin, debt_to_equity). Missing fields → NaN, NEVER 0.0.
    3. Else: provider = provider or data.market_data.get_provider();
       raw = provider.get_fundamentals(symbol).
       Map known keys into typed columns (handle yfinance .info-style keys
       AND Finnhub keys per data/market_data.py FinnhubProvider; both feed
       FundamentalDataDTO.from_raw_dict today, so just reuse those key names).
       INSERT a new row with as_of=today (UTC), raw_json=json.dumps(raw),
       source from the provider.
       Return the typed dict.
    4. Total failure (DB error + provider error) → return {}.

  get_fundamentals_history(symbol, since=None) -> pd.DataFrame
    For future PIT-fundamentals replay (closes the gap documented in
    tests/test_validation_multifactor.py). Returns columns: as_of,
    pe_ratio, pb_ratio, roe, dividend_yield, market_cap.

  get_macro(series_id, *, lookback_days=None, data_engine=None) -> pd.Series
    1. SELECT date, value FROM macro_history WHERE series_id=? ORDER BY date.
       Build a tz-naive DatetimeIndex Series.
    2. Top-up rule: if max(date) is today or yesterday AND last fetched_at
       is < settings.MACRO_REFRESH_HOURS old, return the cached series.
    3. Else: data_engine = data_engine or DataEngine();
       df = data_engine.fetch_macro_history()  # already implemented
       For each series_id in the returned frame's columns, upsert new rows
       (INSERT OR REPLACE) into macro_history. Return the union as a Series.
    4. lookback_days, if provided, slices the tail.
    5. Total failure → empty Series.

Settings to add:
  FUNDAMENTALS_REFRESH_DAYS: int = 1
  MACRO_REFRESH_HOURS: int = 12

Wiring (behind HISTORICAL_STORE_ENABLED):
  - processing_engine.calculate_fundamental_metrics — at the point it
    consumes the raw fundamentals dict per symbol, route through
    HistoricalStore.get_fundamentals(symbol). The raw provider call
    becomes the inner fallback inside get_fundamentals, not the outer
    code path. CRITICAL: do not change the dict KEYS this function reads
    — keep FundamentalDataDTO.from_raw_dict compatible.
  - macro_engine.MacroEngine — replace the direct
    self.data_engine.fetch_macro_history() call in
    compute_hmm_risk_on_probability with HistoricalStore.get_macro for
    each needed series_id (VIXCLS, T10Y2Y, SAHMREALTIME, BAMLH0A0HYM2).
    The single-snapshot _build_macro_dto path stays on the existing live
    fetch — it's a current-state read, not a historical series.

Tests (tests/test_historical_store.py):
  TestFundamentalsHistory:
    - test_first_fetch_writes_row: empty DB + mock provider returning
      {trailingPE: 25, priceToBook: 4.5, ...} → get_fundamentals returns
      typed dict; DB has one row with as_of=today and raw_json set.
    - test_within_max_age_skips_provider: seed DB with today's row,
      call get_fundamentals with max_age_days=1 → mock provider NOT
      called.
    - test_stale_row_refetches: seed DB with row 5 days old,
      max_age_days=1 → provider IS called, new row inserted.
    - test_missing_fields_are_nan_not_zero: mock provider returns
      {trailingPE: 18} only → returned dict has pe_ratio=18.0,
      pb_ratio=NaN (not 0.0). CONSTRAINT #4.
    - test_total_failure_returns_empty_dict: provider raises + DB error
      → get_fundamentals returns {}.

  TestMacroHistory:
    - test_macro_round_trip: seed via a mock DataEngine returning a
      synthetic 100-row macro frame, get_macro('VIXCLS') returns a
      100-element Series with the right values.
    - test_macro_incremental: seed DB with 90 days, mock DataEngine
      called → only the delta is INSERT OR REPLACE'd.
    - test_macro_total_failure_empty_series: DB error + DataEngine error
      → empty Series, no raise.

Gravity audit:
  step_60_historical_persistence_audit_phase3 — verify:
    - fundamentals_history + macro_history tables exist after init
    - get_fundamentals returns NaN for missing fields (not 0.0)
    - get_fundamentals respects max_age_days (no refetch when fresh)
    - get_macro round-trip via mock DataEngine works
    - settings.FUNDAMENTALS_REFRESH_DAYS == 1, MACRO_REFRESH_HOURS == 12
    - processing_engine.py source references HistoricalStore
    - macro_engine.py source references HistoricalStore

CLAUDE.md updates:
  - Extend the HistoricalStore bullet to cover fundamentals_history +
    macro_history schemas, the cache-window rules, and the
    raw_json-for-PIT-replay convention.
  - Add to Conventions: "Fundamentals and macro reads in pipeline code
    MUST go through HistoricalStore.get_fundamentals / get_macro when
    HISTORICAL_STORE_ENABLED=True. A direct provider.get_fundamentals or
    DataEngine.fetch_macro_history call in processing_engine /
    macro_engine is a regression."
  - Update tests/test_validation_multifactor.py's docstring: note that
    PIT fundamentals are now available via fundamentals_history.raw_json
    once enough days have accumulated, and the harness test could be
    extended to Value/Quality factors after ~3 months of accumulated
    history (do NOT implement that extension in this phase — out of
    scope; just document the path).

Do NOT:
  - Backfill historical fundamentals (no free vendor provides them; the
    PIT story starts the day Phase 3 ships and accumulates forward).
  - Touch the single-snapshot _build_macro_dto path. The HMM uses
    historical macro; the gate uses current macro. Two distinct reads.

End of phase: PR titled "feat: Tier 2.3 Phase 3 — fundamentals + macro
history". Body should show DB row counts before/after a fresh run (proving
incremental-only writes on the 2nd run) + Gravity step 60 phase-3 passing.
```

---

## Phasing rationale

| Phase | Why this order |
|---|---|
| 1 (bars) | Biggest single win — kills the slow refetch loop that's the root cause of rate-limiting. Self-contained: one new module, one wiring point in `main.py`, no schema touch. Easy to revert if anything breaks. |
| 2 (RH snapshot) | Directly answers your launch UX request ("holdings always there"). Depends on Phase 1 only for the shared `HistoricalStore` class skeleton. |
| 3 (fundamentals + macro) | Closes the largest persistence gap and unblocks the documented PIT-fundamentals test extension. Larger blast radius (touches `processing_engine` + `macro_engine`) — saved for last when Phase 1's pattern is proven. |

## Post-Tier-2.3 follow-ups (out of scope here)

These are unlocked by the new tables but should be separate tickets:
- **New `COLUMN_SCHEMA` derived metrics**: rolling beta vs SPY, per-symbol Sortino/Calmar, sector relative strength, dividend-adjusted total return. All cheap once `price_bars` is populated.
- **Equity-curve panel in GUI** using `account_snapshot_history()`.
- **Multifactor harness extension** to Value/Quality once `fundamentals_history.raw_json` has accumulated ≥ 90 days.
- **Manual backfill CLI**: `python -m data.historical_store backfill --symbols AAPL,MSFT --days 5000` for users who want long backtests against the local cache.
