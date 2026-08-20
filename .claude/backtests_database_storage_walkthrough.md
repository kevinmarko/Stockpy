# Walkthrough: Persist backtest/validation runs to the database

## What prompted this

The operator asked "does running the backtests add results to a database?" Investigation
showed `StrategyValidationHarness.run()` only wrote files —
`reports/<strategy>_validation_summary.json` (overwritten each run),
`reports/history/<strategy>_validation_history.jsonl` (append-only), and two HTML reports —
all under the repo's `reports/` directory, which is **worktree-local**. This repo runs many
simultaneous git worktrees (confirmed via `git worktree list` — 20+ active worktrees), so a
backtest run in one worktree is invisible to the webapp's Validation Trend chart running
against a different worktree's API process. The operator asked to fix that.

## What changed

**Write path** — new `validation/validation_history_store.py`: a `ValidationHistoryStore`
backed by an append-only `validation_runs` SQLite/Postgres table (resolved through the
existing `db_config.py`), mirroring `desktop/run_history_store.py`'s `RunHistoryStore` /
`sizing/cap_audit_store.py` / `data/sector_correlation_store.py` conventions exactly (own
`Base`, own table, a `readonly=True` database-level engine for readers). `StrategyValidationHarness.run()`
gained step 6d — a best-effort, dead-letter-resilient call to `_record_validation_run_to_db`
right after the existing JSONL history append — so every validated strategy now gets a
durable, cross-worktree row in addition to the existing files (nothing removed).

**Read path** — `pilots/validation_trend.py`'s `cross_strategy_snapshot()` and
`validation_history_trend()` now read the DB first (via a lazy, function-local import,
matching the established `pilots.sector_selection`/`data.sector_correlation_store` pattern)
and fall back to the worktree-local files only for a strategy the DB has no row for yet. The
`GET /strategy/validation-trend` response shape is byte-identical to before, so
`api/pilots_api.py` and `webapp/src/components/ValidationTrend.tsx` needed zero changes.

## A real bug found and fixed mid-implementation

Before wiring tests, a grep across `tests/` found ~25 pre-existing test files
(`tests/test_harness_*.py`, `tests/test_validation_*.py`, ...) construct
`StrategyValidationHarness` and call `.run()` for real. Since the new DB write happens
*implicitly* inside `run()` — unlike `TransactionsStore`/`RunHistoryStore`, whose every test
call site constructs the store directly with its own `db_url` — none of those 25 files had
any way to opt out. Running the affected test files during implementation confirmed this
empirically: `tests/test_validation_history.py::TestRunAppendsHistoryOnce`'s pre-existing
`run()`-level test wrote a real `"RunOnceTest"` row into the operator's actual
`~/.stockpy_local/quant_platform.db`.

Fixed with a session-wide autouse fixture in the **root** `conftest.py`
(`_isolate_validation_runs_db_in_tests`), mirroring the file's existing
`_no_gdelt_throttle_in_tests`/`_no_fmp_throttle_in_tests` pattern — the same reasoning applies:
a resource shared across dozens of pre-existing test files that a new test author would have
no reason to think to guard against individually. This is a deliberate, documented exception
to `tests/conftest.py`'s own "opt-in only" convention, which explicitly scopes that
convention to *that* file. The one stray `"RunOnceTest"` row was deleted from the real DB
before the full suite was re-run to confirm zero further pollution (verified: `validation_runs`
held exactly one row — the intentional manual end-to-end verification run — after the full
11,646-test offline suite completed).

## Verification performed

- **End-to-end**: `python -m scripts.refresh_validations --strategies rsi2_mean_reversion`
  against the real environment — confirmed a row landed in `validation_runs`
  (`sqlite3 ~/.stockpy_local/quant_platform.db`) and that
  `pilots.validation_trend.cross_strategy_snapshot()` read it back with the correct values.
- **Targeted tests**: `tests/test_validation_history_store.py` (12 new tests),
  `tests/test_validation_history.py`, `tests/test_pilots_validation_trend.py` (7 new tests),
  `tests/test_pilots_strategy_matrix.py`, `tests/test_pilots_api.py`, plus the ~13 harness/
  validation-registry test files that call `.run()` for real — all pass, with the DB
  confirmed clean of test pollution after each run.
- **Lint**: `ruff check . --select=F821,F822,F823,E9` — clean.
- **Full offline gate**: `pytest -m "not network and not slow" -n auto --dist loadgroup` —
  `11646 passed, 13 skipped, 21 failed`. Verified via `git stash` (running the same 21 tests
  against unmodified `main`) that all 21 failures are pre-existing and unrelated to this
  change (stale committed artifacts in `test_settings_liveness.py`/`test_measure_settings_census.py`,
  and an advisory dead-letter error-count mismatch in `test_run_once.py`/`test_pipeline_smoke.py`/
  `test_main_body_engine_injection.py` — none of which touch `validation/`, `pilots/validation_trend.py`,
  or `conftest.py`).

## Files touched

- New: `validation/validation_history_store.py`, `tests/test_validation_history_store.py`
- Modified: `validation/harness.py`, `pilots/validation_trend.py`, `conftest.py`,
  `tests/test_validation_history.py`, `tests/test_pilots_validation_trend.py`,
  `tests/test_pilots_strategy_matrix.py`, `tests/test_pilots_api.py`,
  `docs/architecture/validation-and-signals.md`, `CLAUDE.md`, `AGENTS.md` (auto-synced)
