# Data Layer Plan — PIT Historical Fundamentals from SEC EDGAR (Gemini-facing)

> **This document is self-contained.** It is handed to a separate AI agent
> (Gemini) who starts cold with no prior exploration of this codebase.
> Everything needed to execute is embedded below, including verbatim
> current-state API references. You should not need to go spelunking through the
> source to begin — though you should of course read the actual files before
> editing them.

---

## (a) Context & goal

**Goal:** build a real, multi-year **point-in-time (PIT)** historical
fundamentals feed, keyed by **SEC filing date**, stored so it can be *replayed
as-of any past date*. This unblocks honest **Value / Quality factor validation**
and feeds the ML training panel (owned by a separate Claude agent — see the
boundary in section (b)).

**Why PIT matters:** a fundamentals value must be attributed to the date it
*became public* (the filing date), not to today. If you score a 2019 decision
using fundamentals that were only filed in 2020, that is lookahead bias and every
downstream backtest is silently inflated. The whole point of this feed is that
`report_date <= decision_date` is enforced structurally.

**Data source (free-data-only):** **SEC EDGAR** (`data.sec.gov`). EDGAR's
`companyfacts` API exposes historical XBRL financial statement line items, and
every reported fact carries a `filed` date = when that data became public.
**No paid vendor.** This is the only compliant way to get real multi-year PIT
fundamentals for free. yfinance's `.info` is current-snapshot only (no history),
which is exactly why the ML training panel's fundamentals columns currently come
out all-NaN (see section (c), "THE GAP").

**Honesty rules (load-bearing, from the platform's global constraints):**
- **NaN is never fabricated.** A missing input → `NaN`, never a placeholder
  `0.0`. If a symbol has no filing on or before a decision date, the as-of query
  returns NaN for that field.
- **`report_date <= as_of_date` is the no-lookahead rule.** The as-of query must
  return the latest filing whose effective date is on or before the decision
  date — never a later one.
- **Free-data-only.** SEC EDGAR only; no paid data vendor may be introduced.

---

## (b) Two-agent boundary

You (Gemini) own the **data layer**. A separate Claude agent owns the **ML
pipeline**. Stay strictly on your side.

**You own (and may edit):**
- `data/edgar_fundamentals.py` — **new module** you create (SEC EDGAR client + ratio math).
- `data/historical_store.py` — **fundamentals seams only** (`fundamentals_history` table, its readers/writers, and the new PIT methods below). Do NOT touch bars/account-snapshot/macro code paths.
- `scripts/backfill_edgar_fundamentals.py` — **new backfill script** you create.
- `validation/pit_fundamentals.py` — extend the existing PIT audit tool into a coverage/freshness report.
- Their tests: `tests/test_historical_store.py`, `tests/test_edgar_fundamentals.py` (new), `tests/test_pit_fundamentals.py`.

**You must NOT edit `ml/` at all.** That is Claude's ML-pipeline phase.
Specifically do not touch `ml/training_data.py`, `ml/feature_engineering.py`, or
`ml/registry*`. The ML side *consumes* your contract; it does not need you to
wire it in.

**The contract you deliver** — this is the single interface the ML side depends
on:

```python
HistoricalStore.get_fundamentals_asof(symbol: str, as_of_date) -> Dict[str, float]
```

Returns the **latest** `fundamentals_history` row whose `report_date <= as_of_date`,
projected to a dict with **these exact key names** (already expected by
`processing_engine.calculate_fundamental_metrics` and the ML consumers — do not
rename):

```
book_to_market, earnings_yield, quality_factor_score, log_market_cap,
pe_ratio, pb_ratio, roe, market_cap, eps
```

If there is no row with `report_date <= as_of_date`, every value is `NaN`
(never fabricated). This is the ONLY new public method the ML side needs; keep
its signature stable.

---

## (c) Current-state API map (verbatim — so you need no exploration)

### `data/historical_store.py`
- **`fundamentals_history` DDL — lines 157–175.** Columns:
  `symbol, as_of, pe_ratio, pb_ratio, roe, dividend_yield, market_cap, eps,
  operating_margin, debt_to_equity, raw_json, report_date, source, fetched_at`.
  PRIMARY KEY `(symbol, as_of)`.
- **`as_of` is written as TODAY's UTC date** in `_upsert_fundamentals`
  (~`:1058–1071`). It is therefore **forward-accumulating and NOT backfillable** —
  every write stamps "today," so you cannot represent historical filings by
  re-using `as_of` as a key.
- **`report_date` IS a genuine effective-date column** but is currently
  **write-only metadata** — nothing reads it or keys on it yet. This is the
  column your PIT feed will key on.
- **Readers:**
  - `get_fundamentals(symbol, max_age_days=1, *, provider=None)` — `:599` (typed columns).
  - `get_fundamentals_raw(...)` — `:690` (full raw dict, the `FundamentalDataDTO.from_raw_dict` shape).
  - `get_fundamentals_history(symbol, since=None)` — `:812`, currently returns only **6 columns** `[as_of, pe_ratio, pb_ratio, roe, dividend_yield, market_cap]`.
- **Helpers:** typed column list `_FUND_DB_COLS` — `:88–91`; key map `_FUND_KEY_MAP` — `:76–85`; raw→typed `_raw_to_typed_fundamentals` — `:1392` (note **`debt_to_equity` is divided by 100 at `:1408`**).

### `data/yahoo_fundamentals.py`
Pure function (reuse its formulas + scale rules so `FundamentalDataDTO.from_raw_dict`, `dto_models.py:191`, stays unchanged):

```python
compute_fundamentals(ticker, *, price, shares_current, shares_diluted,
    income_stmt, income_stmt_quarterly, balance_sheet, cashflow,
    cashflow_quarterly, dividends, inst_holders, stock_returns,
    market_returns, sector, company_name)   # :288
```

Emits `.info`-style keys (line refs):
`currentPrice`, `trailingEps` `:343`, `trailingPE` `:348`, `bookValue` `:361`,
`priceToBook` `:366`, `dividendYield` (**FRACTION**) `:394`, `payoutRatio` `:405`,
`marketCap` `:412`, `beta` `:443`, `returnOnEquity` `:450`, `debtToEquity`
(**×100 PERCENT**) `:462`, `grossMargins` `:479`, `operatingMargins` `:490`,
`currentRatio` `:501`, `heldPercentInstitutions` `:531`.

**Two SCALE RULES (do not "fix"):** `dividendYield` is emitted as a **fraction**
(e.g. `0.0257`); `debtToEquity` is emitted **×100** (percent, e.g. `150.0`).
Reuse these exact scale rules when you compute ratios from EDGAR data.

### `processing_engine.calculate_fundamental_metrics` — `:319`
Ratio formulas your PIT rows must match:
- `book_to_market = 1/pb if pb > 0 else NaN` — `:485`
- `earnings_yield = 1/pe if pe > 0 else NaN` — `:488`
- `quality_factor_score = mean(returnOnEquity, operatingMargins, grossMargins present) else -debt_to_equity else NaN` — `:500–513`
- `low_vol_score = -vol_60d` (price-derived; not your concern — it is filled from bars, not fundamentals)
- `log_market_cap = log(market_cap) if market_cap > 0 else NaN`

### Consumers already expecting these columns (no rename needed)
- `ml/feature_engineering.py`: `_FUNDAMENTAL_COLS = ["book_to_market","earnings_yield","quality_factor_score","low_vol_score"]` — `:30`; `_FACTOR_COLS = ["Value_Z","Quality_Z","LowVol_Z","Size_Z"]` — `:32`.
- `signals/multifactor.py`: `RAW_INPUT_COLS` — `:80`.

### Existing PIT audit tool
- `validation/pit_fundamentals.py`:
  - `audit_from_historical_store(store, symbol, decision_date, *, fields_checked=None)` — `:304`.
  - `_extract_report_date(raw_payload)` — `:146` (checks keys `mostRecentQuarter` / `lastFiscalYearEnd` + the platform `report_date`).
- Tests: `tests/test_pit_fundamentals.py` exists.

### THE GAP (why this whole plan exists)
`ml/training_data.build_training_panel` (`:278`) builds each per-date
`universe_df` in its per-date loop (`:369–411`) purely from
`_pit_ticker_row(prior_close)` (`:205`) — **price-derived only**. So the
fundamentals / factor-Z columns come out **NaN** (confirmed *not* a lookahead
bug — just missing data). The ML-side injection seam is **~line 388, before the
`build_pit_feature_matrix` call** — **but that edit is Claude's (ML Phase M3),
NOT yours.** You deliver `get_fundamentals_asof`; Claude wires it in.

---

## (d) Phases D1–D5

### D1 — PIT-capable store methods (offline)

**Change.** Add to `HistoricalStore`:
- `get_fundamentals_asof(symbol, as_of_date) -> Dict[str, float]` — the contract
  from section (b): latest row with `report_date <= as_of_date`, projected to the
  nine exact keys; all-NaN when no such row exists.
- Expand `get_fundamentals_history(symbol, since=None)` to **also** return
  `report_date` + the full typed columns + `raw_json` (purely **additive /
  backward-compatible** — existing callers reading the original 6 columns must
  keep working).
- `upsert_fundamentals_pit(symbol, typed, raw, *, report_date, source)` — a new
  writer **keyed/deduped on `report_date`** (NOT on today's `as_of`), so historical
  filings can be persisted at their true effective dates and re-running the
  backfill is idempotent.

**Files.** `data/historical_store.py`, `tests/test_historical_store.py`.

**Verify.** Offline unit tests (no network): insert several PIT rows at distinct
`report_date`s; `get_fundamentals_asof` returns the correct latest-≤-cutoff row;
returns all-NaN before the earliest filing; `upsert_fundamentals_pit` is
idempotent on repeat (same `report_date` → one row); the expanded
`get_fundamentals_history` still satisfies old callers.

### D2 — SEC EDGAR fundamentals client (net-new module)

**Change.** Create `data/edgar_fundamentals.py`:
- Resolve **ticker → CIK** via SEC `company_tickers.json`.
- Pull the **`companyfacts` XBRL** payload for the CIK.
- Extract `us-gaap` line items — net income, revenue, stockholders' equity,
  diluted EPS, total-debt components, current assets / current liabilities,
  dividends paid, plus `dei:EntityCommonStockSharesOutstanding` — each **keyed by
  its `filed` date**.
- Compute ratios reusing the **`compute_fundamentals`-style math + the two SCALE
  RULES** from `data/yahoo_fundamentals.py` (dividendYield fraction;
  debtToEquity ×100) so the emitted dict matches the platform's `.info`-style
  shape and `FundamentalDataDTO.from_raw_dict` is unchanged.
- **Dead-letter per symbol → `{}`** (one bad ticker never aborts a batch).

**HTTP etiquette (SEC-required):** send a **descriptive `User-Agent` header with
real contact info** (SEC rejects generic/absent UAs) and respect the **~10
requests/second** rate limit. **Tests use mocked HTTP fixtures — no live network
in tests** (per the `tests/test_market_data.py` convention in this repo).

**Files.** `data/edgar_fundamentals.py`, `tests/test_edgar_fundamentals.py` (mocked HTTP).

**Verify.** Offline tests over a captured `companyfacts` fixture: CIK resolution,
line-item extraction keyed by `filed` date, ratio/scale-rule correctness, and the
per-symbol `{}` dead-letter path on a malformed/empty payload.

### D3 — Backfill script

**Change.** Create `scripts/backfill_edgar_fundamentals.py`:
- Walk each symbol's filing history from EDGAR; at **each `report_date`**, compute
  PIT ratios — **price** from `HistoricalStore.get_bars` at that date, **shares**
  from EDGAR (`EntityCommonStockSharesOutstanding`) — and persist via
  `upsert_fundamentals_pit`.
- **Idempotent / resumable** (safe to re-run; deduped on `report_date`).
- CLI flags: `--tickers`, `--since`.

**Files.** `scripts/backfill_edgar_fundamentals.py`.

**Verify.** Live run on **~10 tickers, ~5 years**; spot-check a known value (e.g.
**AAPL FY2020 book value vs. the actual 10-K**) to confirm the computed PIT
figure is real, not fabricated. Re-run and confirm no duplicate rows.

### D4 — Coverage / freshness report

**Change.** Extend `validation/pit_fundamentals.audit_from_historical_store`
into a coverage/freshness report: per symbol — **PIT row count**, **earliest /
latest `report_date`**, and a **no-lookahead audit sample** (verify a sampled
decision date returns a row with `report_date <= decision_date` and that
perturbing later filings does not change it).

**Files.** `validation/pit_fundamentals.py`, `tests/test_pit_fundamentals.py`.

**Verify.** Report renders per-symbol coverage; no-lookahead sample passes for
the backfilled universe; NaN honestly reported where coverage is absent.

### D5 — Un-defer Value/Quality validation

**Change.** Un-defer the Value/Quality factor validation in
`tests/test_validation_multifactor.py` (its docstring at `:33–51` names the exact
seam and current scope limitation) by driving it through `validation.harness`
against the now-real PIT fundamentals. **Report the HONEST `deployable`
verdict** — if Value/Quality genuinely fails a gate, that is the correct output;
do not loosen thresholds to force a pass.

**Files.** `tests/test_validation_multifactor.py`.

**Verify.** The previously price-only-restricted harness now exercises real
Value/Quality factors over PIT history and emits an honest deployable/not-deployable result.

---

## (e) MCP verification

Use the InvestYo MCP tools to verify against live-ish platform state (same tools
the ML side uses):

- **`mcp__investyo__query_investyo_db`** — inspect `fundamentals_history` rows
  after a backfill (confirm distinct `report_date`s, no dupes, real values).
- **`mcp__investyo__run_backtest`** — sanity-check factor signal once PIT data lands.
- **`mcp__investyo__run_platform_tests`** — full test suite; must be green.
- **`mcp__investyo__trigger_data_engine`** — refresh underlying data for a verification run.

For **library** API/config questions use the `context7` docs tools. **Note: SEC
EDGAR is a plain REST API, not a library** — read its developer docs at
`https://www.sec.gov/edgar/sec-api-documentation` directly rather than via
`context7`.

---

## (f) Done-definition (the signal for Claude to start ML Phase M3)

You are done when **all** of the following hold:

1. `HistoricalStore.get_fundamentals_asof(symbol, as_of_date)` returns **real,
   multi-year PIT values** for the core ticker universe, with the exact nine keys
   from section (b), and NaN (never fabricated) where there is no prior filing.
2. **Offline tests are green** (D1, D2, D4, D5 test surfaces; mocked HTTP for EDGAR).
3. The **coverage report (D4) shows ≥ N years of PIT history** for the core
   tickers (spot-checked against a real 10-K per D3).

When those are satisfied, that is the explicit **signal for the Claude ML agent
to begin Phase M3** (consuming `get_fundamentals_asof` at
`ml/training_data.py` ~`:388`). Do not perform that wiring yourself.
