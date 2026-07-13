# Config & Schema Plan — COLUMN_SCHEMA Drift, Coverage & Migration Safety (Gemini-facing)

> **This document is self-contained.** It is handed to a separate AI agent
> (Gemini) who starts cold with no prior exploration of this codebase.
> Everything needed to execute is embedded below, including verbatim
> current-state API references, exact counts, and line numbers (re-verify
> with a fresh `grep`/`python3 -c` if they drift). You should not need to go
> spelunking through the source to begin — though you should of course read
> the actual files before editing them.

---

## (a) Context & goal

**Goal:** `config.py`'s `COLUMN_SCHEMA` is the platform's Single Source of
Truth (SSOT) for the dashboard's ~86 columns — every downstream sink
(Google Sheets, the SQLite `DailySignals` table, Pandera validation) derives
its shape from this one list. This plan closes three concrete, **verified**
gaps in how faithfully that SSOT promise is actually kept end-to-end:

1. **The advisory-path population gap.** `main.py`'s advisory pipeline (via
   `reporting/sheet_publisher.py::rec_to_sheet_row`) populates only **27 of
   86** `COLUMN_SCHEMA` columns with real data — the rest are blank-filled.
   Separately, **8 more fields are computed by `rec_to_sheet_row` but never
   reach the Sheet at all**, silently dropped by a column-name mismatch (see
   section (c) — this is a genuine bug, not an intentional gap, and is
   distinct from the 59 intentionally-blank orchestrator-only columns).
2. **The `DailySignals` table appears to be entirely unwritten.** A live
   grep across the whole repo (production code, not tests) finds **zero**
   `INSERT INTO DailySignals` or `.to_sql(...'DailySignals'...)` call
   sites — the table is built and schema-migrated on every
   `database_setup.py` run, but nothing populates it. This needs
   confirmation-in-depth (maybe a writer exists under a name this plan's
   author didn't grep for) and then either a real writer or an honest
   deprecation note.
3. **`config.py`'s own internal consistency check is never run
   automatically.** `Config.validate_config()` (duplicate key/header
   detection) only executes via `python config.py`'s `__main__` block — it
   is not called at import time, not in any test, not in CI. A duplicate key
   added to `COLUMN_SCHEMA` would silently produce broken behavior (see
   `tests/test_database_setup.py::TestMalformedColumnSchema` for what
   actually happens: SQLite raises `OperationalError: duplicate column
   name` — but only when someone happens to run `database_setup.py`, not at
   the point the bad schema entry was written).

**Why this matters:** `COLUMN_SCHEMA` is depended on by 4+ independent
consumers (`reporting/sheet_publisher.py`, `database_setup.py`,
`config.DashboardSchema` Pandera validation, `main_orchestrator.py`'s
`compile_dashboard()`) that were built at different times by different
agents. Nothing currently proves those consumers stay honest about which
columns they actually populate versus silently blank-fill — which is
exactly the kind of drift CONSTRAINT #4 ("no fabricated metrics") is
supposed to prevent when a "" placeholder is easy to mistake for a real
zero/empty result rather than "this pipeline path doesn't compute this."

---

## (b) Two-agent boundary

You (Gemini) own **`config.py` and `database_setup.py`** in this phase.

**This phase has NO known file overlap with the in-flight 8-agent
`execution/`/`gui/` hardening effort** (verified: none of that effort's
branch names — `exec-unified-alerting`, `exec-fill-stream-flatten`,
`exec-broker-branch-coverage`, `exec-alpaca-offline-tests`,
`gui-caching-analytics-diagnostics`, `gui-observability-helpers`,
`gui-report-viewer-helpers`, `gui-ml-model-monitoring` — touch `config.py`
or `database_setup.py`; those are all `execution/`/`gui/`/`ml/`-scoped).
**Unlike `docs/OBSERVABILITY_PLAN.md`'s sequencing gate, you may start this
phase immediately — no waiting, no merge-status check required.**

**You own (and may edit):**
- `config.py` — `COLUMN_SCHEMA` annotations/metadata (NOT column additions
  or removals — see the constraint below), `Config.validate_config()`,
  `get_headers()`/`get_internal_keys()`/`get_rename_mapping()`.
- `database_setup.py` — `initialize_database()`, `migrate_daily_signals_schema()`,
  `type_map()`.
- `reporting/sheet_publisher.py` — **read/audit only unless Phase C1
  explicitly asks you to fix the 8-key drop bug** (see Phase C1 below); do
  not otherwise touch its Sheets-write logic.
- New tests: `tests/test_config.py` (does not currently exist — you are
  creating it), extensions to `tests/test_database_setup.py`.

**You must NOT:**
- Add or remove `COLUMN_SCHEMA` entries as part of this phase — this plan is
  about *characterizing and testing* the existing 86 columns' producer
  coverage, not adding new dashboard metrics. (If Phase C1's fix requires
  adding the 8 dropped keys' correct `COLUMN_SCHEMA` entries because they
  turn out to be genuinely valuable and currently homeless, that is an
  explicit, called-out exception — see Phase C1.)
- Touch `observability/`, `alerting.py`, or anything under `execution/` —
  that is `docs/OBSERVABILITY_PLAN.md`'s scope (a separate, parallel
  Gemini assignment with its own sequencing gate; unrelated to this one).
- Touch `main_orchestrator.py`'s `compile_dashboard()` internals — you may
  *read* it to characterize orchestrator-side column coverage (Phase C1)
  but do not modify its calculation logic.

---

## (c) Current-state API map (verbatim — so you need no exploration)

### `config.py` (225 lines)

- **`COLUMN_SCHEMA`** — a flat `list[dict]`, each entry
  `{"header": str, "key": str, "format": str}` — `:25-164`. **Verified exact
  count: 86 entries** (re-verify: `python3 -c "import config;
  print(len(config.COLUMN_SCHEMA))"` — do not trust a stale count if this
  file has changed since this plan was written). Organized into 14 labeled
  sections by inline comment (`# --- IDENTITY & CLASSIFICATION ---`,
  `# --- TIME-SERIES TARGETS ---`, etc., `:26` onward).
- **`get_headers() -> list[str]`** — `:166`. Sheet display headers, in
  `COLUMN_SCHEMA` order.
- **`get_internal_keys() -> list[str]`** — `:170`. Internal dict keys, same
  order.
- **`get_rename_mapping() -> dict[str, str]`** — `:174`. `key -> header`,
  used by `reporting/sheet_publisher.py:180` (`df.rename(columns=rename_map)`)
  to translate internal keys to display headers before writing.
- **`MarketDataSchema(pa.DataFrameModel)`** — `:180`. Pandera schema for raw
  OHLCV bars (Open/High/Low/Close/Volume + a `High >= Low` dataframe-level
  check). Independent of `COLUMN_SCHEMA`.
- **`DashboardSchema`** — `:206`. **Dynamically built** from `COLUMN_SCHEMA`
  at import time (`:197-204`): `Symbol` gets a length-1-to-10 string check,
  `currency`/`currency_large`/`percent`/`number` formats become nullable
  `float` columns, everything else becomes nullable `str`. `coerce=True`.
  This IS the automatic-drift-safety mechanism for *types* — a new
  `COLUMN_SCHEMA` entry automatically gets a correctly-typed Pandera column
  with zero additional code. (Confirmed exercised by
  `tests/test_quantitative_models.py:749`,
  `config.DashboardSchema.validate(final_df)`.)
- **`Config.validate_config()`** — `:211-221` (a plain class, not the
  Pydantic `Settings` — do not confuse with `settings.py`'s `Settings`
  class). Checks `COLUMN_SCHEMA` for duplicate `key`s or duplicate
  `header`s, raises `ValueError` if found, otherwise logs an INFO success
  line. **Confirmed only invoked from the `if __name__ == "__main__":` block
  at `:223-224`** — `grep -rn "validate_config()" --include="*.py" .` finds
  zero callers outside `config.py` itself. Not called at import time, not in
  any test, not in `database_setup.py`, not in CI.

### `reporting/sheet_publisher.py::rec_to_sheet_row()` (`:35-121`) — the
advisory-path producer

Maps one `engine.advisory.Recommendation` + Robinhood position to a
Sheet-row dict. **Verified via AST parse** (re-run the snippet below if this
function changes):

```python
import config, ast
schema_keys = set(config.get_internal_keys())     # 86 keys
schema_headers = set(config.get_headers())         # 86 headers
# ... walk rec_to_sheet_row's return Dict literal ...
```

**Result: `rec_to_sheet_row` emits exactly 35 dict keys.**

- **27 of those 35 correctly match a real `COLUMN_SCHEMA` key** and survive
  `write_recommendations()`'s rename + column-filter steps
  (`sheet_publisher.py:180-188`) to actually land in the written Sheet:
  `Symbol, Price, Action Signal, Advice, Actionable Advice Signal, Kelly
  Target, Edge Ratio, RSI, RSI_2, MACD_Line, ATR, Aroon Oscillator, Sortino
  Ratio, Max Drawdown, RS vs SPY, GARCH_Vol, Forecast_30, buyRange,
  sellRange, Option Strategy, Robinhood Shares, Robinhood Avg Cost,
  Robinhood Dividends, Robinhood Advice, Strategy Explainer Notes, Macro
  Status, HMM_Risk_On_Probability`.
- **8 of those 35 match NEITHER a `COLUMN_SCHEMA` key NOR a header string** —
  meaning `write_recommendations()`'s final filter step
  (`df = df[[h for h in final_headers if h in df.columns]]` at `:188`)
  **silently drops them**. This is real, already-computed advisory data
  (conviction, position sizing, rationale, forecast percent, dividend
  yield) that is thrown away before ever reaching the Sheet:
  ```
  Score, Forecast_30_Pct, Dividend Yield, Advisory_Action,
  Advisory_Conviction, Advisory_Rationale, Advisory_Position_Pct,
  Advisory_Data_Quality
  ```
  **This is a bug, not an intentional gap** — the code computes these
  values, assigns them into the row dict, and they vanish silently with no
  warning/error. Compare to `rec.rationale` (already partially surfaced via
  `Advice` and `Strategy Explainer Notes`, which DO map correctly) —
  `Advisory_Rationale` is a pure duplicate-intent field that never lands
  anywhere.
- **The remaining 59 of 86 `COLUMN_SCHEMA` columns are NOT emitted by
  `rec_to_sheet_row` at all** — they are filled with `""` by
  `write_recommendations()`'s `for h in final_headers: if h not in
  df.columns: df[h] = ""` step (`:185-187`). These fall into two categories
  you must distinguish in Phase C0's audit (do not conflate them):
  - **Genuinely orchestrator-only** (full-pipeline metrics the lean advisory
    path structurally cannot compute without the full `main_orchestrator.py`
    engine stack — e.g. `Graham Num`, `Gordon Fair Value`, `MC_Target`,
    `Aroon Up`/`Down`, `Coppock Curve`, `VaR 95`, `Beta`, `MFE`/`MAE`,
    `BF_Allocation`/`BF_Selection`, `Value_Z`/`Quality_Z`/`LowVol_Z`/`Size_Z`,
    `News_Sentiment`, `Correlation_Cluster`). CLAUDE.md already documents
    `main_orchestrator.py` as the path for "production runs that need all
    50+ dashboard columns populated" — this is intentional and should be
    **documented as such, not "fixed."**
  - **Plausibly advisory-computable but simply not wired** — e.g.
    `Div Yield` (a `Dividend Yield` value IS computed and present in
    `rec.key_indicators["dividend_yield"]`, per the dropped-key list above —
    it's on the *wrong* key name, `Dividend Yield` vs. the schema's
    `Div Yield` header, or missing entirely as `Div Yield` the internal key
    which doesn't exist — check precisely), `P/E`, `Quality Score`. These
    are candidates for Phase C1's wiring fix, not the "intentional gap"
    bucket.

### `database_setup.py` (174 lines)

- **`type_map(col_format, col_key) -> str`** — `:40`. Maps `COLUMN_SCHEMA`
  `"format"` strings to SQLite types: `string→TEXT`, `number/currency/
  currency_large/percent→REAL`; `Target_Days`/`Volume` are special-cased to
  `INTEGER` regardless of declared format (`:46-47`); unknown formats
  default to `TEXT` (no crash — confirmed by
  `tests/test_database_setup.py::test_unknown_format_falls_back_to_text`).
- **`initialize_database(db_file=DB_FILE)`** — `:51`. Creates
  `ExecutionLogs` (`:74-84`), dynamically builds `DailySignals` from
  `COLUMN_SCHEMA` (`:96-109`, one column per entry via
  `f'"{key}" {col_type}'` — correctly quoted for keys containing spaces/
  slashes like `"P/E"`/`"Market Cap"`), calls
  `migrate_daily_signals_schema()` (`:112`), then creates a fixed-schema
  `Transactions` table (`:116-128`, NOT derived from `COLUMN_SCHEMA` — its
  own separate schema for trade journaling, distinct from
  `transactions_store.py`'s SQLAlchemy `trades` table — confirm this is
  intentional, not another accidental duplicate, in Phase C2).
- **`migrate_daily_signals_schema(cursor, conn)`** — `:139` (the "F-07 FIX").
  Reads existing `DailySignals` columns via `PRAGMA table_info`, issues
  `ALTER TABLE ... ADD COLUMN` for any `COLUMN_SCHEMA` key missing from the
  live table. **Additive only — confirmed no handling for renamed or
  removed `COLUMN_SCHEMA` keys**: if a key is renamed, the old column stays
  forever (orphaned, never dropped — SQLite's `ALTER TABLE DROP COLUMN` is
  even supported since SQLite 3.35 but this code never calls it) and a new
  column is added under the new name with no data migration between them.
  If a key is removed outright, its column is silently orphaned forever
  with no warning.
- **CONFIRMED: nothing writes rows to `DailySignals`.** Full-repo grep for
  `INSERT INTO DailySignals` and `.to_sql(` (any table) in production code
  (excluding `tests/`) returns **zero** writer call sites. The only
  production references to `DailySignals` at all are: `database_setup.py`
  itself (schema owner), `investyo_mcp_server.py:836` (`SELECT COUNT(*)
  FROM DailySignals` — a read-only MCP tool), `scripts/preflight_check.py`
  (`check_db_exists`, `:869-891` — checks the *file* exists/is non-empty,
  does not query `DailySignals` specifically), and `gui/panels/launcher.py:582`
  (a UI label string describing what "Rebuild Schema" does, not a writer).
  **This needs Phase C2 to either (a) find a writer this grep missed under
  an unexpected name/pattern, (b) implement one if the table is meant to be
  live, or (c) document it as intentionally superseded** by
  `transactions_store.py` (trades) + `data/historical_store.py` (bars/
  account snapshots/fundamentals/macro) — both of which post-date
  `database_setup.py`'s original "Step 6" framing per its own module
  docstring (`:1-6`) and may have simply obsoleted `DailySignals` without
  anyone removing the dead table-creation code.

### Existing test coverage

- **No `tests/test_config.py` exists today** (confirmed —
  `find . -iname "test_config*.py"` finds nothing). `COLUMN_SCHEMA` is
  exercised only indirectly:
  - `tests/test_quantitative_models.py:244-278` — a
    `test_..._all_column_schema_columns_generated`-style test confirming
    dashboard-compilation output covers `COLUMN_SCHEMA`'s keys (**this is
    the orchestrator path**, `main_orchestrator.py`'s `compile_dashboard()`
    — not the advisory path this plan's Phase C0/C1 focus on).
  - `tests/test_quantitative_models.py:749` — `config.DashboardSchema.validate(final_df)`.
  - `tests/test_dashboard_validation.py`, `tests/test_correlation_clusters.py`,
    `tests/test_sell_side_range.py` — each reference `COLUMN_SCHEMA`
    tangentially for their own specific column(s), not the schema as a
    whole.
- **`tests/test_database_setup.py`** (265 lines, thorough) — `TestTypeMap`,
  `TestInitializeDatabaseIdempotency` (including a preserved-rows-on-rerun
  test), `TestMalformedColumnSchema` (missing `"format"` key → `KeyError`;
  duplicate `"key"` → `sqlite3.OperationalError: duplicate column name` —
  this is the **actual failure mode** a duplicate key produces today, since
  `Config.validate_config()` never runs automatically to catch it earlier),
  `TestMigrateDailySignalsSchema` (additive migration + no-op-when-current +
  wired-in-via-`initialize_database` checks). **No test exercises column
  removal/rename** (consistent with the source having no such handling at
  all).
- **`tests/test_settings.py`** exists but covers `settings.py`'s Pydantic
  `Settings` class — unrelated to `config.py`'s `COLUMN_SCHEMA`, despite the
  similar filename; do not conflate the two when searching for existing
  coverage.

---

## (d) MCP verification notes

- **`mcp__investyo__query_investyo_db`** — after Phase C2, use this to
  directly inspect `DailySignals`'s row count (`SELECT COUNT(*) FROM
  DailySignals`) both before and after your change, to prove whether it was
  really empty and whether your fix (if you implement a writer) actually
  populates it.
- **`mcp__investyo__run_platform_tests`** — full suite must stay green after
  every phase.
- **`mcp__investyo__generate_html_report`** / **`mcp__investyo__get_portfolio_summary`** —
  useful for Phase C1 to visually confirm which advisory-path columns show
  real values vs. placeholders in an actual generated artifact, corroborating
  the static AST-based audit with a live run.
- **`mcp__investyo__trigger_data_engine`** — if Phase C1's fix requires
  wiring a currently-unwired-but-advisory-computable field (e.g. `Div
  Yield`), use this to confirm the underlying data is actually available in
  a live cycle before claiming the fix works.

For library API questions (Pandera, SQLAlchemy) use the `context7` docs
tools.

---

## (e) Phases C0–C3

### C0 — Full column-producer characterization (audit + tests, minimal code change)

**Problem.** No single source states, for each of the 86 `COLUMN_SCHEMA`
columns, whether it is populated by the advisory path (`main.py`), the
orchestrator path (`main_orchestrator.py`), both, or neither. Section (c)
above gives you the exact starting numbers (27 advisory-populated, 8
advisory-computed-but-dropped, 59 advisory-blank) — this phase turns that
one-time audit into a **regression-tested, machine-checkable table** so it
never silently drifts again.

**Change.** Add `tests/test_config.py` (new file) with:
- A `TestColumnSchemaIntegrity` class: `len(COLUMN_SCHEMA) == 86` (or
  whatever the current true count is — pin it, and add a comment instructing
  future editors to update this deliberately, not accidentally), no
  duplicate keys, no duplicate headers, every entry has all three of
  `header`/`key`/`format`, every `format` value is one of the five known
  strings `type_map()` understands (catches the `test_unknown_format_falls_back_to_text`
  degrade-to-TEXT case before it ships to `database_setup.py`, i.e. this is
  a *tighter* check than that test's documented tolerant behavior).
- A `TestAdvisoryColumnCoverage` class that AST-parses (or, more robustly,
  directly calls with a synthetic `Recommendation`/`AccountSnapshot`)
  `reporting/sheet_publisher.py::rec_to_sheet_row` and asserts: (a) the
  known-27 currently-mapped keys are present and correctly named against
  live `COLUMN_SCHEMA` keys (this test breaks — intentionally — the moment
  someone renames a `COLUMN_SCHEMA` key without updating
  `rec_to_sheet_row` to match, catching exactly the kind of silent drift
  this plan exists to prevent); (b) explicitly documents (via a
  `KNOWN_UNMAPPED_ORCHESTRATOR_ONLY_COLUMNS` frozenset the test asserts
  against `COLUMN_SCHEMA` minus the mapped set) which of the 59 blank
  columns are intentionally orchestrator-only, so a future PR that
  genuinely fixes one of them updates this test deliberately rather than
  the test just silently staying green either way.
- Call `Config.validate_config()` directly in a test (not just at
  `python config.py` CLI time) so CI actually exercises the duplicate-key/
  header guard — this closes gap #3 from section (a) with the minimal
  possible change (a test call, not wiring it into a hot path).

**Files.** `tests/test_config.py` (new).

**Verify.** New test file green; `Config.validate_config()` is exercised in
CI for the first time (confirm via `pytest -v tests/test_config.py` showing
the new test names, or `pytest --collect-only tests/test_config.py`).

### C1 — Fix the 8-key silent-drop bug in `rec_to_sheet_row`

**Problem.** Section (c) confirmed 8 real, already-computed advisory values
(`Score, Forecast_30_Pct, Dividend Yield, Advisory_Action,
Advisory_Conviction, Advisory_Rationale, Advisory_Position_Pct,
Advisory_Data_Quality`) are silently dropped before reaching the Sheet
because they match neither a `COLUMN_SCHEMA` key nor header.

**Change.** For each of the 8, decide (case by case, do not blanket-apply
one fix) whether it should:
- **(a) Map onto an existing `COLUMN_SCHEMA` key** it was probably intended
  to feed — e.g. check whether `Dividend Yield` was meant to populate the
  existing `Div Yield` header's key `Div Yield`... **wait, `Div Yield` the
  key differs from the dict key `"Div Yield"` — re-verify the exact
  `COLUMN_SCHEMA` key string at the time you implement this** (section (c)'s
  header list shows `{"header": "Div Yield", "key": "Div Yield", "format":
  "percent"}` — confirm `rec_to_sheet_row` should write to the key
  `"Div Yield"`, not invent `"Dividend Yield"`).
- **(b) Be genuinely new information with no current `COLUMN_SCHEMA` slot**
  (this looks true for `Advisory_Action`/`Advisory_Conviction`/
  `Advisory_Rationale`/`Advisory_Position_Pct`/`Advisory_Data_Quality` —
  these look like an advisory-specific metadata block someone intended to
  add columns for and never finished) — if so, **this is the one place this
  plan permits adding new `COLUMN_SCHEMA` entries**, under an
  `# --- ADVISORY METADATA ---` section, since they carry real signal
  (conviction, data quality, position sizing) not otherwise exposed
  end-to-end.
- **(c) Be a genuine duplicate of an already-mapped field** (e.g. confirm
  whether `Advisory_Action` is truly redundant with the already-correctly-
  mapped `Action Signal` before adding a new column for it — do not add a
  column that just repeats existing data under a second name).
Document your case-by-case decision in the PR description; do not
mechanically map all 8 without judgment.

**Files.** `config.py` (only if (b) applies to any of the 8),
`reporting/sheet_publisher.py`, `tests/test_config.py` (extend
`TestAdvisoryColumnCoverage`), a new/extended test in
`tests/test_run_once.py` or a dedicated `tests/test_sheet_publisher.py` if
one doesn't exist (check first) confirming the previously-dropped fields
now appear in the final written-row dict's header-keyed form.

**Verify.** A synthetic `rec_to_sheet_row()` call followed by the same
rename+filter steps `write_recommendations()` performs shows all 8
previously-dropped values now surviving to the final row; full suite green.

### C2 — `DailySignals` writer-or-deprecate decision

**Problem.** Section (c) confirms zero writers for `DailySignals` in
production code as currently grep-able. This table is fully schema-migrated
on every `database_setup.py` run for no apparent live purpose.

**Change.** First, **do a deeper search than the grep in section (c)** —
check for dynamic SQL construction (f-string `INSERT INTO {table_name}`
patterns that a plain grep for the literal string `DailySignals` might
miss), check `main.py`/`main_orchestrator.py` history via `git log -p --
database_setup.py DailySignals` for whether a writer existed and was
removed, and check `investyo_mcp_server.py` fully (not just the one
`SELECT COUNT(*)` line already found) for a write path. If a real writer
truly does not exist anywhere:
- **Do not silently delete `DailySignals`'s schema-creation code** — that is
  a scope decision for a human, not this docs-only characterization pass.
  Instead, add a clear docstring note directly above
  `initialize_database()`'s `DailySignals` block in `database_setup.py`
  stating (with evidence — reference this plan / your own grep) that no
  writer currently exists, and that `transactions_store.py` +
  `data/historical_store.py` appear to be the tables that superseded its
  original "Step 6" purpose (cite `database_setup.py`'s own module
  docstring, `:1-6`, which frames it as a from-flat-files migration that
  now itself looks superseded by two later, more capable stores).
  Explicitly flag this in your final PR description as a candidate for a
  **follow-up decision** (keep-as-dead-schema vs. wire-a-real-writer vs.
  remove) rather than resolving it yourself — this is a product decision
  about whether daily signal history should live in a table nobody
  currently writes.
- If you DO find a hidden writer, document exactly where and correct
  section (c)'s claim in this plan's own text (edit this file) so the next
  reader isn't misled by a stale audit.

**Files.** `database_setup.py` (docstring only), this plan document itself
if your findings differ from what's written above.

**Verify.** No functional code change if no writer is found (this phase is
audit + documentation); if a hidden writer surfaces, confirm its behavior
with a targeted test.

### C3 — Migration-safety hardening (rename/removal detection)

**Problem.** `migrate_daily_signals_schema()` only ever adds columns
(confirmed in section (c)) — a renamed or removed `COLUMN_SCHEMA` key
leaves permanently orphaned columns in the live `DailySignals` table with no
warning to the operator.

**Change.** Add a **non-destructive, warning-only** drift detector to
`migrate_daily_signals_schema()`: after the existing additive
`ALTER TABLE ADD COLUMN` loop, compute `existing_cols - current_schema_keys
- {"id", "timestamp"}` (columns present in the live table but no longer in
`COLUMN_SCHEMA`) and log a single `logger.warning` line listing them (e.g.
`"DailySignals has N orphaned column(s) no longer in COLUMN_SCHEMA: [...].
These are never dropped automatically (SQLite ALTER TABLE DROP COLUMN is
available since 3.35 but intentionally not used here to avoid destructive
migrations); review and drop manually if confirmed obsolete."`). **Do NOT
auto-drop columns** — this must be observability only, matching this
codebase's stated convention (from CLAUDE.md's "Historical/runtime data is
never committed" + general CONSTRAINT #4/#6 posture) that destructive schema
changes are a human decision, never an automatic one.

**Files.** `database_setup.py`, `tests/test_database_setup.py` (new test:
build a schema, add an extra unrelated column directly via raw SQL to
simulate a since-removed `COLUMN_SCHEMA` key, re-run
`migrate_daily_signals_schema`, assert the orphan-warning log line appears
and the column is NOT dropped).

**Verify.** New test green; existing `TestMigrateDailySignalsSchema` tests
unaffected (no behavior change to the additive path, only a new read-only
warning after it).

---

## (f) Done-definition

You are done when **all** of the following hold:

1. **C0** — `tests/test_config.py` exists, pins `COLUMN_SCHEMA`'s current
   shape (count, no dup keys/headers, valid formats), exercises
   `Config.validate_config()` in CI for the first time, and asserts the
   advisory-path column-coverage numbers (27 mapped / 8 dropped-before-fix /
   59 orchestrator-only) as a living, breakable contract.
2. **C1** — the 8 previously-silently-dropped advisory fields are resolved
   (mapped to an existing key, added as new documented `COLUMN_SCHEMA`
   entries, or confirmed-and-removed as true duplicates) with a test proving
   they now survive to the final written row — no field is left silently
   dropped without an explicit, documented decision.
3. **C2** — the `DailySignals`-has-no-writer finding is either corrected (a
   real writer was found and is now documented) or clearly flagged as an
   open follow-up decision in the PR description, with a docstring note in
   `database_setup.py` — not silently left as an undocumented dead table.
4. **C3** — `migrate_daily_signals_schema()` warns (never silently, never
   destructively) about orphaned columns from renamed/removed
   `COLUMN_SCHEMA` keys, covered by a test.
5. **`pytest -q`** (or `mcp__investyo__run_platform_tests`) is green,
   including the new `tests/test_config.py`.
6. Every numeric claim in your final PR description (column counts, mapped/
   dropped/orchestrator-only counts) is **freshly re-verified against the
   actual code at merge time**, not copy-pasted from this plan's
   possibly-stale snapshot numbers.
