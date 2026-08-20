# Persist backtest/validation runs to the database

## Context

The user asked whether running the strategy validation ("backtest") fleet adds results to
a database. It doesn't: `StrategyValidationHarness.run()` (`validation/harness.py`) only
writes files — `reports/<strategy>_validation_summary.json` (overwritten each run),
`reports/history/<strategy>_validation_history.jsonl` (append-only trend), and two HTML
reports. All of that lives under the repo's `reports/` directory, which is **worktree-local**
— exactly the class of bug `CLAUDE.md` already documents for `settings.LOCAL_DATA_ROOT`
("this repo runs many simultaneous git worktrees and untracked files are worktree-local in
git, so ... invisible from every other one"). A validation run in one worktree is invisible
to the webapp's Validation Trend chart (`pilots/validation_trend.py` → `GET
/strategy/validation-trend` → `ValidationTrend.tsx`) running against a different worktree/API
process.

The codebase already has the exact pattern for this: `desktop/run_history_store.py`'s
`RunHistoryStore` gives pipeline runs "a second, durable home" (SQLite/Postgres via
`db_config.py`, shared across worktrees at `settings.LOCAL_DATA_ROOT`) alongside the
existing in-memory ring, without removing it. `sizing/cap_audit_store.py` and
`data/sector_correlation_store.py` are two more instances of the identical convention. This
plan mirrors that pattern for validation/backtest runs: add a durable `validation_runs`
table (write path), and make the webapp's read path prefer it (read path), while leaving
every existing file output untouched.

## Write path — `validation/validation_history_store.py` (new file)

Mirror `desktop/run_history_store.py` / `data/sector_correlation_store.py` exactly:

- `Base = declarative_base()`; `ValidationRun` model, table `validation_runs`:
  `id` (Integer PK autoincrement — this is an append-only log like the JSONL history, not
  an upsert-by-key table like `pipeline_runs`), `strategy_id` (String, indexed),
  `recorded_at` (DateTime, UTC, stamped at write time), `report_date`, `start_date`,
  `end_date` (String), `deployable`, `family_deployable`, `family_bh_significant`,
  `is_options_selling`, `stress_gate_passed` (Boolean, nullable), `pbo`, `dsr`, `sharpe`,
  `max_drawdown` (Float, nullable — CONSTRAINT #4, never fabricate a missing metric as 0),
  `n_trials` (Integer), and `summary_json` (Text — the full `ValidationReport.to_summary_dict()`
  payload, matching `PipelineRun.progress_json`'s "promoted scalar columns + raw JSON blob"
  shape, so the equity/benchmark/macro curves and `family_multiple_testing` detail are never
  lost even though they're not individually columned).
- `ValidationHistoryStore(db_url=None, *, readonly=False)`: write mode resolves via
  `db_config.resolve_database_url()` + `create_db_engine()` + `Base.metadata.create_all()`;
  `readonly=True` uses `create_readonly_db_engine()` and skips DDL.
- `record_run(summary: dict) -> None`: inserts one row from a `ValidationReport.to_summary_dict()`
  dict (never upserts — a new row per run, matching the JSONL semantics). Raises on failure
  (mirrors `RunHistoryStore.record_run` / `TransactionsStore` — CONSTRAINT #4, never silently
  no-op a write); the caller is responsible for the best-effort wrapper.
- `get_recent(strategy_id: Optional[str] = None, limit: int = 50) -> List[Dict]`: most-recent-first,
  optionally filtered to one strategy. Degrades to `[]` on any read failure (CONSTRAINT #6),
  matching `RunHistoryStore.get_recent`.
- `get_latest_per_strategy() -> Dict[str, Dict]`: one row per `strategy_id` (the most recent
  `recorded_at`) — backs the "current snapshot" table. Same `[]`/`{}`-on-failure contract.

## Wire the harness to write

In `validation/harness.py::StrategyValidationHarness.run()`, right after the existing step 6c
(`self._append_validation_history(report)`), add a new step 6d: `self._record_validation_run_to_db(report)`.
New method:

```python
def _record_validation_run_to_db(self, report: "ValidationReport") -> None:
    """Best-effort durable copy of this run's summary in validation_runs — a
    second, durable home alongside reports/history/*.jsonl (which is
    worktree-local and NOT shared across the many git worktrees this repo
    runs). Dead-letter resilient: a DB hiccup must never abort an otherwise-
    successful validation run, matching _append_validation_history's contract."""
    try:
        from validation.validation_history_store import ValidationHistoryStore
        ValidationHistoryStore().record_run(report.to_summary_dict())
    except Exception as exc:
        logger.warning("Failed to record validation run to DB for %s: %s", report.name, exc)
```

Lazy import (matches this file's existing style for optional/heavy dependencies) so importing
`validation.harness` doesn't hard-fail if `sqlalchemy`/the DB is unavailable in some
environment — it just logs and moves on, same as the JSONL append already does.

## Read path — `pilots/validation_trend.py`

This module is pinned dependency-light by `tests/test_pilots_strategy_matrix.py::test_pilots_read_helpers_stay_dependency_light`
(stdlib + `settings` + `scripts.snapshot_diff` only). Follow the exact precedent already used
there for `pilots/sector_selection.py` (`from data.sector_correlation_store import
SectorCorrelationStore` inside a function body) and for `pilots/strategy_health.py` (`from
validation import thresholds` — `validation` has no `__init__.py`, so importing
`validation.validation_history_store` does NOT transitively pull `validation.harness`'s heavy
top-level imports (`yfinance`, `universe_engine`, ...) — confirmed by inspection, `validation/`
is an implicit namespace package with no `__init__.py`):

- `cross_strategy_snapshot()`: add a lazy `from validation.validation_history_store import
  ValidationHistoryStore` inside the function, call `ValidationHistoryStore(readonly=True).get_latest_per_strategy()`.
  For each strategy_id, prefer the DB row; fall back to the existing file-parsed row when the
  DB has nothing for that strategy (dead-letter merge — old/local-only data still renders,
  matching this module's existing "degrade honestly, never drop good data" ethos). Wrap the
  DB call in try/except so an unavailable DB degrades to today's pure-file behavior, not a 500.
- `validation_history_trend()`: same idea — `ValidationHistoryStore(readonly=True).get_recent(strategy_id,
  limit=trend_limit)` per strategy discovered via the snapshot step, falling back to
  `_read_validation_history_rows()` (today's JSONL read) when the DB returns nothing for that
  strategy. Still requires >= 2 points before a strategy appears (CONSTRAINT #4, unchanged).
- Both functions gain an optional `db_url: Optional[str] = None` parameter (threaded through
  `validation_trend_snapshot()` too) for test isolation, matching how `reports_dir`/`history_dir`
  are already parameterized in this file.
- Response shape (`{"strategies": [...], "trend": {...}, ...}`) is **unchanged** — this is a
  backend source swap, not a contract change, so `api/pilots_api.py`'s `GET
  /strategy/validation-trend` handler and `webapp/src/components/ValidationTrend.tsx` need no
  code changes.
- Update the module docstring's "PORTED locally" section to note the new DB-primary,
  file-fallback behavior.

## Test-guard update

`tests/test_pilots_strategy_matrix.py`'s `test_pilots_read_helpers_stay_dependency_light`
allowlist: add a `validation_trend` entry allowing root `validation` (mirroring the existing
`strategy_health` entry's comment) — document that `validation.validation_history_store` is
confirmed dependency-light by inspection (only `sqlalchemy`, `db_config`, `settings`, stdlib),
deliberately distinct from `validation.harness`.

## Tests

- New `tests/test_validation_history_store.py`, mirroring `tests/test_run_history_store.py`'s
  shape: record/get_recent round trip, most-recent-first ordering, `get_latest_per_strategy`
  one-row-per-strategy, readonly store reads what a write-mode store wrote, readonly write
  raises rather than fabricating success, readonly degrades to `[]`/`{}` on a missing table.
- Extend `tests/test_pilots_validation_trend.py`: DB-primary-over-file-fallback for both
  `cross_strategy_snapshot` and `validation_history_trend` (seed the store with a tmp sqlite
  `db_url`, confirm DB rows win over conflicting file rows, confirm file rows still surface for
  a strategy absent from the DB, confirm an unreachable/corrupt DB degrades to today's
  pure-file behavior rather than raising).
- Extend `validation/harness.py`'s existing test file (`tests/test_harness.py` or equivalent —
  confirm exact name) with a case asserting `run()` calls `ValidationHistoryStore.record_run`
  once, and that a DB failure there doesn't prevent the JSONL/HTML outputs from being written.

## Documentation updates (per CLAUDE.md's "Implementation Plan must include an explicit
documentation-update step")

- `docs/architecture/validation-and-signals.md`: extend the existing `validation/harness.py`
  bullet with the new `_record_validation_run_to_db` step and add a new bullet for
  `validation/validation_history_store.py`, following the file's established bullet style
  (see the `validation/harness.py` / `validation/stress_scenarios.py` bullets already there).
- `CLAUDE.md` (auto-synced to `AGENTS.md` by the existing hook): add a short bullet describing
  the new `validation_runs` table, mirroring the existing `RunHistoryStore` framing —
  particularly the "second, durable home ... worktree-local reports/history is NOT shared"
  motivation, so a future agent doesn't reintroduce a file-only store for something that needs
  cross-worktree visibility.

## Verification

- Re-run one strategy through the harness (`python -m scripts.refresh_validations --strategies
  rsi2_mean_reversion`) and confirm a new row lands in `validation_runs` (query via the store
  or `sqlite3 ~/.stockpy_local/quant_platform.db "select strategy_id, sharpe, deployable,
  recorded_at from validation_runs order by id desc limit 5;"`).
- `pytest tests/test_validation_history_store.py tests/test_pilots_validation_trend.py
  tests/test_pilots_strategy_matrix.py -q` — new/updated tests pass.
- `npm run --prefix webapp typecheck` — confirms the (unchanged) API contract still satisfies
  `webapp/src/api/types.ts`/`ValidationTrend.tsx` with zero webapp edits required.
- `make verify` / the repo's fast offline gate before calling this done, per CLAUDE.md's
  "Verification is mandatory, not advisory."
- Branch/PR: already on `claude/backtests-database-storage-7c2eb0` (not `main`), matching the
  Start-of-session checklist's requirement for anything touching `validation/`.
