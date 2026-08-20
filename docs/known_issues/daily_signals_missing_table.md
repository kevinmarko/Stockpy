# Known issue (2026-08-20, resolved): `no such table: DailySignals` — `database_setup.py` never wired into any automated pipeline path

**Status: fixed.** `database_setup.py` was run against the live
`LOCAL_DATA_ROOT` database with a verified pre-migration backup; zero data
loss confirmed via a full table row-count diff; `get_signal_breakdown` and
the rest of `investyo_mcp_server.py`'s `DailySignals`-dependent tools now
return an honest no-data message instead of the table error; the full
`test_database_setup.py` + `test_investyo_mcp_server.py` suite stays green.

## What happened

Every `investyo_mcp_server.py` tool that reads `DailySignals` —
`get_signal_breakdown`, `get_universe_status`'s Database Metrics section,
`validate_order_compliance`'s Kelly-cap/VRP-regime checks, and others —
failed at the SQL layer with:

```
no such table: DailySignals
```

on this operator's live `~/.stockpy_local/quant_platform.db`.

## Root cause

`DailySignals` is defined and created entirely inside `database_setup.py`
(`CREATE TABLE IF NOT EXISTS DailySignals (...)`, schema generated from
`config.COLUMN_SCHEMA`, plus a supporting index and an idempotent
column-migration pass) — but `database_setup.py` is a **standalone script**,
never imported or invoked by any automated pipeline path:

- `main.py` does not call it.
- `main_orchestrator.py` does not call it.
- `desktop/orchestrator_daemon.py` / `desktop/daemon_runtime.py` (the
  always-on daemon) do not call it.

The documented usage (`docs/README.md`, `CLAUDE.md`'s Commands section) is
`python3 database_setup.py` as a one-time, manually-run "(re)build the
SQLite schema" step. On this operator's machine, `settings.LOCAL_DATA_ROOT`
(the shared, machine-global DB root — see CLAUDE.md's `LOCAL_DATA_ROOT`
bullet) had accumulated real data across 38 tables from months of daemon
runs, but `database_setup.py` itself had apparently never been (re-)run
against that specific `LOCAL_DATA_ROOT`-anchored file, so `DailySignals`
was simply never created there even though every table any orchestrator
path writes to (`price_bars`, `forecast_errors`, `account_positions`, etc.)
was present and populated.

This is a schema-provisioning gap, not a data-loss bug: nothing was ever
writing to `DailySignals` in the first place (no INSERT path targets it
from any live code, per `database_setup.py`'s own header comment), so the
table's total absence and its post-fix "exists but empty" state are
functionally the same for every existing caller — the fix only changes
whether the *table* exists, which is what determines whether the MCP tools
above get a clean "no rows" answer or a SQL error.

## The fix

Ran `python3 database_setup.py` against the live
`~/.stockpy_local/quant_platform.db`, after taking a full file-level backup
first:

```
/Users/kevinlee/.stockpy_local/quant_platform.db.pre_daily_signals_fix_backup.20260820T143622Z
```

### Schema verification

- `.schema DailySignals` confirms the table exists with a real, well-formed
  schema. `PRAGMA table_info(DailySignals)` returns 116 columns total —
  `id` (autoincrement PK) + `timestamp` (default `CURRENT_TIMESTAMP`) + 114
  data columns.
- Cross-referenced programmatically against this checkout's
  `config.COLUMN_SCHEMA`: it has exactly 114 entries, and a diff of the
  ordered key lists (`COLUMN_SCHEMA` `key` fields vs. `DailySignals` column
  names, both after stripping `id`/`timestamp`) is byte-identical — every
  key name **and** column order matches exactly. A supporting index
  `idx_daily_signals_symbol_ts` on `(Symbol, timestamp DESC)` is also
  present, matching `database_setup.py`'s definition.

### Zero data loss — full table row-count diff

Re-enumerated all 38 baseline tables live (read-only URI connection)
against the same file, before vs. after running `database_setup.py`:

| Table | Before | After | Diff |
|---|---:|---:|---:|
| analyst_history | 639 | 642 | +3 |
| earnings_events | 45,160 | 45,280 | +120 |
| finbert_score_cache | 24,895 | 25,404 | +509 |
| forecast_errors | 2,268,117 | 2,268,626 | +509 |
| fundamentals_history | 3,328 | 3,331 | +3 |
| insider_stats | 20,545 | 20,550 | +5 |
| iv_history | 784 | 787 | +3 |
| news_history | 973 | 976 | +3 |
| pipeline_runs | 1,419 | 1,420 | +1 |
| price_bars | 311,358 | 311,959 | +601 |
| rlhf_calibration_proposals | 83 | 84 | +1 |
| sector_correlations | 7,766 | 7,799 | +33 |
| sentiment_ingestion_audit | 620,223 | 620,571 | +348 |
| sizing_cap_events | 18,880 | 18,915 | +35 |
| DailySignals | 0 (didn't exist) | 0 | 0 |

Every one of the 38 pre-existing tables has a row count **≥** its baseline
— zero decreases anywhere. The 14 tables that grew are all plausible
organic daemon-write activity that happened during the verification window
(the live daemon kept running); the remaining 24 baseline tables, including
large/sensitive ones like `account_positions` (540), `macro_history`
(81,756), `oauth_access_tokens` (79), `symbol_rating_events` (13,561), and
`paper_orders`/`paper_positions`/`paper_account`, are byte-identical to
baseline (0 diff). `DailySignals` itself is `0 → 0` — expected, since
nothing populates it yet (schema-only migration; no live code path writes
to it — see Root cause above), not a failure.

Two tables exist in the live DB that were not part of the recorded 38-table
baseline — `ExecutionLogs` (0 rows) and `Transactions` (0 rows), both
currently empty — noted for completeness rather than omitted silently;
neither can represent data loss since both are empty in a fresh baseline
too.

**Conclusion: no data loss detected.** The migration produced a correct,
schema-verified table and caused zero row-count regression across every
pre-existing table in the live database.

### Functional verification

- `get_signal_breakdown(symbol="T")` → `{"result":"No signals found for T
  in the database."}` — an honest no-data message, not the old table error,
  and no fabricated signal breakdown.
- `get_signal_breakdown(symbol="AAPL")` → same honest no-data pattern:
  `{"result":"No signals found for AAPL in the database."}`.
- `get_universe_status`'s Database Metrics section now reads `Daily Signals
  Table Rows: 0` / `Trades Table Rows: 0` / `Execution Logs Table Rows: 0`
  with no `Error querying DB stats: no such table: DailySignals` text
  anywhere in the output; the rest of the dashboard (Active Trading
  Universe, Active Watch Rules) renders normally alongside it.
- `validate_order_compliance(ticker="T", side="buy", size=1)` (real,
  read-only, advisory-only — confirmed it never places or queues an order)
  returned an honest per-check `UNAVAILABLE` verdict — `kelly_sizing_cap`:
  "no DailySignals row found for T — cannot evaluate Kelly cap";
  `vrp_premium_selling_regime`: same pattern — rather than a table error or
  a fabricated `PASSED`.
- `generate_daily_signals` ("Runs the full signal aggregation pipeline")
  was deliberately **not** invoked during verification: its description
  implies a real side-effecting pipeline run with no documented
  dry-run/read-only mode, so it was confirmed available but skipped rather
  than risk an unintended write.

### Regression tests

Full `test_database_setup.py` + `test_investyo_mcp_server.py` suite:
**313 passed, 0 failed** (1 pre-existing, unrelated `FutureWarning` from a
`pd.concat` call in `test_investyo_mcp_server.py`'s order-history test).

## What is still open

- `database_setup.py` remains a manual, standalone step — this fix
  provisioned the missing table on this one operator's live DB, but did
  not wire schema provisioning into any automated pipeline path. A fresh
  `LOCAL_DATA_ROOT` on another machine, or a future new table added to
  `database_setup.py`, will reproduce this same "table doesn't exist until
  someone remembers to run the script" gap unless a caller
  (`main.py`/`main_orchestrator.py`/the daemon's own startup) is taught to
  ensure the schema exists idempotently. Not attempted here — flagged for
  a future change, since deciding where in the startup path an idempotent
  `CREATE TABLE IF NOT EXISTS` pass belongs is a design decision beyond
  the scope of this one-time fix.
- Nothing in this codebase currently writes rows *into* `DailySignals` —
  its presence now unblocks the read-side MCP tools from erroring, but
  every one of them still honestly reports "no data" until a write path is
  built (out of scope here; not fabricated, per CONSTRAINT #4).

## Related

- CLAUDE.md's `settings.LOCAL_DATA_ROOT` bullet — the shared-database
  design this table lives inside.
- `docs/known_issues/forecast_tracker_local_data_root_split.md` and
  `docs/known_issues/duplicate_orchestrator_daemon_processes.md` — other
  recent incidents involving this same live `LOCAL_DATA_ROOT` database,
  each surfaced by direct manual inspection rather than an automated
  check, matching this incident's discovery pattern.
