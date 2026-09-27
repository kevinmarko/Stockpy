# Gravity suite's synthetic controls written to the real `validation_runs` table

**Status:** Fixed (2026-09-27). Existing rows need a one-time manual cleanup (below).

## What happened

`Gravity AI Review Suite.py`'s step 12 (`run_validation_harness_audit`) runs two
synthetic strategies through `StrategyValidationHarness` to check the
deployability gate is wired correctly:

- `Random_Audit`: random returns, a negative control that should fail.
- `Trending_Audit`: a constant-drift series, a positive control that should
  pass. It scores Sharpe of about 33.

The step passed an isolated temp `reports_dir`, which kept these runs out of
`reports/`. But `harness.run()` also writes a durable copy of every run to the
`validation_runs` DB table (added in #820), and that table lives at the shared
`settings.LOCAL_DATA_ROOT`, not under `reports_dir`. Every suite run therefore
added two fake strategies to the operator's real validation history.

By 2026-09-27 there were 25 rows of each. Anything reading the latest row per
strategy saw `Trending_Audit` as a deployable strategy, which inflated the
deployable count from 16 to 17.

pytest was never affected. `conftest.py::_isolate_validation_runs_db_in_tests`
has redirected this write since the DB write shipped. The Gravity suite is a
standalone script, not a pytest run, so that fixture never applied.

## Fix

`StrategyValidationHarness` takes `record_to_history_db: bool = True`. The
Gravity suite passes `False` for both controls. Regression test:
`tests/test_validation_history.py::TestRecordValidationRunToDb::test_record_to_history_db_false_writes_nothing`.

Any future sandbox that runs synthetic strategies through the harness outside
pytest must pass `record_to_history_db=False`.

## One-time cleanup

The fix stops new rows. The existing ones stay until removed. Besides the two
controls, there is one `strategy_id='s'` row from a pytest run on 2026-08-22.
It never recurred, so there is no ongoing leak behind it.

```bash
DB=~/.stockpy_local/quant_platform.db
sqlite3 "$DB" ".backup '$DB.backup-before-validation-runs-cleanup'"
sqlite3 "$DB" "DELETE FROM validation_runs WHERE strategy_id IN ('Random_Audit','Trending_Audit','s');"
```

This should delete 51 rows.
