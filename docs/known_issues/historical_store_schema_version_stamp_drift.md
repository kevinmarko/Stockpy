# HistoricalStore `schema_version=2` stamp drift and log flood

**Status**: Code fix in PR (`fix-historical-store-schema-version-noise`). The live DB's stamp correction is **proposed and waits for operator approval**.
**Date found**: 2026-09-30. **Root-caused**: 2026-10-02.

## Symptom

The live daemon log (`/tmp/stockpy_webapp_logs/orchestrator_daemon.log`) logged
about 3,000 lines a day of:

> HistoricalStore: quant_platform.db schema_version=2 is NEWER than this build's
> CURRENT_SCHEMA_VERSION=1 ...

The live DB held `schema_version = (1, 2, '2026-08-23T19:12:21.911812+00:00')`.
No reachable commit on any branch has ever declared `CURRENT_SCHEMA_VERSION = 2`
in `data/historical_store.py`.

## Root cause

**Two stores shared the `schema_version` table name.** The unreachable
Antigravity commit `bb7de48e` ("Phase 2 Audit Fixes: Complete schema versioning
in paper_account_store", 2026-08-23 14:28 EDT, from the PR #872 work) gave
`data/paper_account_store.py` its own `CURRENT_SCHEMA_VERSION = 2`. It also
created and bumped a `schema_version` table with the same name and shape as
`HistoricalStore`'s, in the same `quant_platform.db`. Running that branch's
`PaperAccountStore` (a test run or a live run) 44 minutes later found
HistoricalStore's row at `1` and "bumped" it to `2`. That is the stamp's
`updated_at` of 15:12 EDT. The commit was dropped before #872 merged, but the
row it wrote stayed.

This happened in the same window as
[`pr872_live_db_test_contamination_2026.md`](pr872_live_db_test_contamination_2026.md).
`PaperAccountStore` had no test isolation until #872 merged on 2026-08-24, so a
test run on that branch is the most likely writer. A manual run of the branch
can't be ruled out, but either way the cause is the same table-name collision.

**Why it logged so much:** `HistoricalStore()` is constructed per call site, and
every construction ran `_ensure_schema_version()`, which logged the warning each
time.

## Schema comparison (live vs. this build's DDL)

A fresh DB built by `HistoricalStore(db_path=<tmp>)` was compared with the live
DB, opened read-only, using `PRAGMA table_info` and `PRAGMA index_list` on all 16
tables `HistoricalStore` owns. **There are 0 differences** in columns, types,
nullability, defaults, PKs or indexes. Only the stamp is wrong. Reads under
`CURRENT_SCHEMA_VERSION=1` are correct.

## Related gap found while investigating: HistoricalStore had no test isolation

An instrumented run of the offline suite found **172 tests in about 30 files**
constructing a bare `HistoricalStore()` against the live
`~/.stockpy_local/quant_platform.db`. They reached it through production code
(`processing_engine`, `pilots/*`, the MCP server, pipeline steps, ...), and each
one ran DDL plus whatever upserts the test drove. `tests/test_store_isolation_contract.py`
only catches *literal* constructions inside test files, so it missed all of these.

## Fix (this PR)

1. `data/historical_store.py` imports `resolve_database_url` at module level, so
   `conftest.py` can patch it. This is the same pattern every sibling store uses.
2. The new `conftest.py` autouse fixture `_isolate_historical_store_db_in_tests`
   sends the baseline resolution to a per-test temp file. A test that sets its
   own `settings.DATABASE_URL` still gets that URL. After the fix, the
   instrumented re-run found **0** live-DB constructions.
3. `tests/test_store_isolation_contract.py::_EXPECTED_CONFTEST_ISOLATED` now
   includes `data/historical_store.py`.
4. The NEWER warning is logged once per process per `(db_path, version)`. Later
   constructions log it at DEBUG. The message now names the DB path.
5. Regression tests in `tests/test_historical_store.py::TestSchemaVersion`
   (`test_newer_version_warns_once_per_process`,
   `test_bare_construction_never_resolves_to_live_db`). With the fixture
   disabled, the second one fails, and so do two contract-test checks.

## Proposed live-DB correction (NOT executed; needs operator approval)

Back up first, then make a one-row update:

```bash
mkdir -p ~/.stockpy_local/db_backups
sqlite3 ~/.stockpy_local/quant_platform.db ".backup '$HOME/.stockpy_local/db_backups/quant_platform.before-schema-version-fix-$(date -u +%Y%m%dT%H%M%SZ).db'"
sqlite3 ~/.stockpy_local/quant_platform.db "UPDATE schema_version SET version = 1, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = 1 AND version = 2; SELECT * FROM schema_version;"
```

The `.backup` command takes a consistent online copy even while the daemon has
the DB open in WAL mode. A plain `cp` of the main file alone could miss pages
that are still in the WAL. The `AND version = 2` guard makes the update a no-op
if anything else has changed the row in the meantime.

## Follow-up worth considering

Any future per-store version stamp must use its **own** table name (for example
`paper_schema_version`). A shared, generic `schema_version` table in a DB that
several stores write to is what caused this.
