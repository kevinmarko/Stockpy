# Walkthrough: HistoricalStore schema_version stamp drift + log flood

Branch: `fix-historical-store-schema-version-noise`. Full write-up:
`docs/known_issues/historical_store_schema_version_stamp_drift.md`.

## Investigation
- No reachable commit declares `CURRENT_SCHEMA_VERSION = 2`. Searching unreachable
  objects (`git fsck --unreachable --no-reflogs`) for `CURRENT_SCHEMA_VERSION = 2`
  found `bb7de48e` (2026-08-23 14:28 EDT, an Antigravity commit from the PR #872
  work). It gave `PaperAccountStore` its own version 2 stamp in the **same**
  `schema_version` table. The live stamp's `updated_at` is 15:12 EDT the same day.
- Live schema vs. this build's DDL: all 16 HistoricalStore tables match, with 0
  differences. Only the stamp is wrong.
- An instrumented offline-suite run found 172 tests constructing bare
  `HistoricalStore()` against the live DB.

## Changes
- `data/historical_store.py`: `resolve_database_url` is now a module-level
  binding, and the NEWER warning is logged once per process per
  `(db_path, version)`, then at DEBUG.
- `conftest.py`: new `_isolate_historical_store_db_in_tests` autouse fixture.
  It redirects the baseline resolution to a per-test temp file and keeps any
  test-set `DATABASE_URL`.
- `tests/test_store_isolation_contract.py`: registers `data/historical_store.py`.
- `tests/test_historical_store.py`: 2 regression tests.
- `docs/settings_liveness.json`: regenerated, because the line numbers moved.
- Docs: a known-issues write-up plus its index row.

## Verification
- `tests/test_historical_store.py`, `test_store_isolation_contract.py`,
  `test_macro_snapshot.py` and `test_flatten_proposal.py`: 147 passed.
- With the fixture disabled, the regression test and two contract checks fail.
- Offline suite (`-m "not network and not slow" -n auto --dist loadgroup`):
  11679 passed, 0 failed. The probe re-run found 0 live-DB constructions.

## Not done (operator approval needed)
- The one-row live stamp correction (2 → 1), with a `.backup` taken first. The
  commands are in the known-issues doc.
