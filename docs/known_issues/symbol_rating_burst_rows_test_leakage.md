# symbol_rating_events: test-suite rows leaked into the live DB

**Status**: Isolation fixed (this PR). Cleanup of existing live rows is a separate, operator-approved step
(`scripts/cleanup_symbol_rating_test_rows.py`, dry-run by default; not run by the PR).
**Date found**: 2026-10-05. **Severity**: Medium (real auto-drop decisions were delayed; no proven wrong drop).

## What happened
`symbol_rating_events` in `~/.stockpy_local/quant_platform.db` held many `cycle_id`s written within the same
second (e.g. 16 cycles between 2026-10-03 10:08:46 and 10:08:55 UTC), each 1-8 rows of fixture symbols
(AAPL, MSFT, GOOG, SPY, AGNC, JNJ, NVDA, TSLA) with a score of exactly 55.0 (GOOD) or 1.0 (BAD).

## Root cause
`SymbolRatingStore()` resolves the shared live DB through `db_config.resolve_database_url()`;
`settings.SYMBOL_RATING_ENABLED` defaults to **True**; and the store is written from `pipeline/steps.py`
(`run_once`) and `pipeline/production_steps.py::_record_symbol_ratings`. The root `conftest.py` had no
isolation fixture for it, and `tests/test_store_isolation_contract.py` only sees *direct* `ClassName(`
constructions in tests (and its 2026-08-29 hand audit assumed gated stores default their flag to False).
Reproduced by running the offline suite with `LOCAL_DATA_ROOT` pointed at a scratch dir and a spy on
`record_ratings`; the leakers were `tests/test_run_once.py` (score 55.0, identical symbol sequence to the
live burst), `tests/test_pipeline_smoke.py` (AAPL 55.0) and `tests/test_progress_emission.py` (AAPL/MSFT 1.0).

## Extent (live DB, read-only dry run on 2026-10-05)
Test cycles = cycles whose rows share exactly one distinct score of 55.0 or 1.0. 5,725 cycles / 26,723 rows
matched at max id 27,656, since 2026-08-13; 933 genuine rows (32 daily advisory cycles) remain. Contamination
also used real watchlist tickers (209 matched cycles / 7,113 rows were large or non-fixture-symbol bursts).

## Impact on auto-drop (`SYMBOL_RATING_AUTO_DROP_ENABLED` on since 2026-08-07, threshold 5)
Streaks read per symbol by `id desc`, so interleaved fake rows alter them.
- Fake GOOD (55.0) rows reset real BAD streaks: AQN reached the threshold on 2026-08-19 but the fake rows reset
  it, delaying its exclusion ~19 days (real streak now 10 vs 5 observed); CBRS one cycle (6 vs 5).
- Fake BAD (1.0, not held) rows pushed AAPL/MSFT to >=5 for brief windows (13 and 78 row-states); no read is
  known to have landed in those windows.
- The current exclusion set is the same with and without the fake rows ({AQN, CBRS}); dry run reports no flips.

## Fix
- `conftest.py::_isolate_symbol_rating_db_in_tests` (autouse): bare `SymbolRatingStore()` resolves to a per-test
  temp SQLite file with the schema pre-created (a file, not `:memory:`, so write-mode and `readonly=True` stores in
  one test share rows).
- `tests/test_store_isolation_contract.py`: `rating/symbol_rating_store.py` added to the expected conftest set and a
  `DEFAULT_TRUE_GATED_STORES` guard (a store written from a default-True flag path must have a root fixture).
- `tests/test_symbol_rating_test_isolation.py`: behavioral regression tests.

## Cleanup (operator-gated)
`python scripts/cleanup_symbol_rating_test_rows.py` is a dry run (read-only connection). `--apply` takes a
`sqlite3` backup into `<db dir>/backups/` first, deletes in one transaction bounded by `--max-id`, and rolls back if
the count differs from the prediction. Run it only after other worktrees have rebased onto the fix, otherwise
their pytest runs keep leaking.

## Rule going forward
A store written from a production path whose gating flag defaults to True needs a root `conftest.py`
`_isolate_*_db_in_tests` fixture; add it to `DEFAULT_TRUE_GATED_STORES`.
