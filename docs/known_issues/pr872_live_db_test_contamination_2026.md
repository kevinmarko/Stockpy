# PR 872 Live-DB Test Contamination

**Status**: Fixed and verified
**Date**: 2026-08-24
**Incident Level**: High (live production ledger silently contaminated with synthetic test data)

## What happened

During the PR 872 remediation (a 6-agent effort to fix data-integrity bugs in the
paper-trading closed-trade ledger, `data/paper_account_store.py`), a follow-up agent
independently verifying Agent 4's `transactions_store` bridge work found that the
real, shared `~/.stockpy_local/quant_platform.db`'s `trades` table — the production
ledger `strategy_engine.py`, `main_orchestrator.py`, `pilots/mirror.py`, and MCP
reporting tools all read as real trading history — held **260 rows of synthetic test
data**. Entry/exit prices were obviously fabricated test fixtures (e.g. `"AAPL
$150.00 -> $150.00"`), option expiries were already in the past, and every row's
`notes` field carried the bridge's own auto-generated tag,
`notes="Paper bridge, reason: ..."`. Timestamps on the contaminating rows spanned
roughly a single day, starting well before the discovering session's own work began
— meaning test runs against this branch, from this or any of this repo's several
other concurrent agent worktrees, had been silently writing into the production
ledger for some time before anyone noticed.

## How it was confirmed as 100% contamination

Two independent checks ruled out any real trading data being mixed in:

1. The `trades` table had **0 rows before PR 872's bridge feature existed at all**
   — the bridge (`PaperAccountStore._record_closed_trade`'s `transactions_store`
   companion write, gated by `settings.PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED`)
   is the only code path in this branch's history that could have written to this
   table at all.
2. **Every single one of the 260 rows carried the bridge's own auto-generated
   `notes` tag.** There was no untagged row, no row with a plausible real
   entry/exit price, and no row a human trader could have plausibly placed by hand.

## Root Cause

Both `data.paper_account_store.PaperAccountStore` and
`transactions_store.TransactionsStore` resolve their database URL the same way in
`__init__`: `db_url or resolve_database_url()`. When no explicit `db_url` is passed,
both fall through to the SAME default — the real, shared, live
`~/.stockpy_local/quant_platform.db` (per `settings.LOCAL_DATA_ROOT`, which
deliberately lives outside every git worktree so all worktrees on a machine share
one physical DB).

Before this fix, nothing in this codebase's test fixtures pointed either store away
from that default. Most pre-existing tests happened to construct `PaperAccountStore`
with an explicit `db_url` (an in-memory or temp-file URL), so this had gone
unnoticed. PR 872's new `transactions_store` bridge changed the risk profile: it
added a new, **implicit** write path — `_record_closed_trade()`'s bridge call —
that fires deep inside a normal paper-fill call (`apply_fill`,
`apply_multi_leg_fill`, `apply_roll_fill`, `settle_expired_options`), with no way
for a caller/test author who simply forgot to pass `db_url` to `PaperAccountStore`
to opt out. Any test that constructed a default `PaperAccountStore` (or
`TransactionsStore`) with the bridge flag enabled, or that ran with
`PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED` set in the ambient environment,
silently wrote through to the live file.

This is the same risk class `conftest.py`'s pre-existing
`_isolate_validation_runs_db_in_tests` fixture already exists to prevent for
`validation_history_store` — a resource shared across dozens of pre-existing test
files, with no single call site to patch instead of a session-wide guard.

## Mitigation

### The fix: a new autouse `conftest.py` fixture

`_isolate_paper_and_transactions_db_in_tests` (session-wide, `autouse=True`, mirrors
the `_isolate_validation_runs_db_in_tests` pattern) patches both modules'
module-local `resolve_database_url` reference to a private, per-test temp-file DB
for every test that doesn't pass its own explicit `db_url`:

```python
isolated_url = f"sqlite:///{tmp_path / 'pytest_isolated_paper_and_transactions.db'}"
monkeypatch.setattr(_pas, "resolve_database_url", lambda: isolated_url)
monkeypatch.setattr(_ts, "resolve_database_url", lambda: isolated_url)
```

### Why plain `:memory:` and a shared-cache memory URI were both tried and rejected

**A plain `sqlite:///:memory:` was tried first and reverted.** SQLite gives each
separate `Engine`/connection its OWN private in-memory database even when they
share the identical URL string. A pre-existing test
(`tests/test_investyo_mcp_server.py::TestGetOrderExecutionHistory`) constructs TWO
independent store instances within one test and expects them to see each other's
writes — one `TransactionsStore()` to seed rows, and a SEPARATE
`TransactionsStore()` inside `get_order_execution_history()` (a real production
code path, not test scaffolding, so it can't be changed to share a session). Before
this fix, both constructions coincidentally resolved to the SAME real file, which
is exactly what let them see each other's data — and is the same coupling that
caused the live-DB contamination in the first place. Plain `:memory:` broke that
expectation and made the test silently read back "no execution history recorded
yet" instead of the seeded rows.

**A SQLite shared-cache in-memory URI (`file:<name>?mode=memory&cache=shared`) was
considered next and rejected too.** It would restore the "every construction in
this test sees the same db" property in principle, but fights
`db_config.create_db_engine`'s own pooling logic: that function's `is_memory` check
is a literal `url.database in (None, "", ":memory:")` test, which a
`file:...?mode=memory&cache=shared` URL fails (its `database` component is the
whole `file:...` string, not literally `":memory:"`). It would therefore get routed
onto the file-backed branch (`NullPool` + the WAL/`busy_timeout` PRAGMA hook)
instead of the memory branch — and under `NullPool`, connections are opened and
closed constantly, so the one guarantee a shared-cache memory db actually needs (at
least one connection to that name stays open at all times, or SQLite destroys it)
is not reliably held, risking a flaky "the data vanished between the write and the
read" failure mode strictly worse than the incident this fixture exists to fix.

**A real per-test temp file (`tmp_path`) sidesteps both problems.** It is correctly
recognized by `create_db_engine` as file-backed (the same PRAGMA/pooling treatment
production code gets — the only way to exercise the bridge's own WAL-lock-contention
fix at all, since that fix is specific to file-backed SQLite), naturally lets any
number of separate store constructions within one test see each other's writes
(matching what the pre-existing MCP test relies on), and is isolated from both the
live DB and every other test by pytest's own per-test `tmp_path` (a fresh,
automatically-cleaned-up temp directory).

One pre-existing test needed a direct update rather than being covered by the
fixture: `test_reset_account_readonly` constructed `PaperAccountStore(readonly=True)`
with no `db_url`, previously relying on harmlessly resolving to the live DB (it
never writes, being readonly) — it now takes its own explicit `tmp_path`-backed
`db_url`, matching the sibling `readonly_store` fixture already in the same file.

### Regression tests proving the fix

Three new tests in `tests/test_paper_account_store.py` pin the fixture's exact
contract:

- `test_paper_account_store_default_db_url_is_isolated_from_live_db`
- `test_transactions_store_default_db_url_is_isolated_from_live_db`
- `test_paper_account_store_and_transactions_store_share_the_same_isolated_db`
  (the specific property `tests/test_investyo_mcp_server.py`'s MCP test needs)

Verified by temporarily disabling the fixture and confirming the isolation tests
then fail against the real live path
(`sqlite:////Users/kevinlee/.stockpy_local/quant_platform.db`) — hard proof the
fixture would have caught the actual incident, not just a plausible-looking
assertion.

### Cleanup of the live DB

The 260 contaminating rows were removed from the live `trades` table. A
WAL-consistent backup was taken first via `sqlite3 ... ".backup"`. **The DELETE
itself was run by the human operator, not the agent** — the agent's sandbox
destructive-write classifier blocked the agent from running it directly, so the
operator ran the DELETE and confirmed 0 rows afterward (`PRAGMA quick_check: ok`).

### Verification

- `tests/test_paper_account_store.py` + `tests/test_copula_stat_arb.py`: 71 passed
  (up from 70 pre-fix; +1 new share-same-db regression test).
- A full 30-file sweep of every test touching `PaperAccountStore`/
  `TransactionsStore` across this repo (`test_perf_hotpath`, `test_kelly_*`,
  `test_advisory*`, `test_options_*`, `test_investyo_mcp_server`,
  `test_transactions_store`, `test_evaluation_*`, `test_orchestrator_e2e`,
  `test_pipeline_smoke`, `test_quantitative_models`, `test_decision_log`, and
  others): 891 passed, 0 failures.
- Live DB confirmed 0 rows in `trades` after both the manual cleanup and this whole
  verification pass.

## Lesson

A store whose default DB resolution has no test-isolation guard is a latent risk
the moment ANY new implicit write path is added inside it — the guard has to exist
at the store's own construction boundary (a session-wide autouse fixture, matching
`_isolate_validation_runs_db_in_tests`'s precedent for
`validation_history_store`), not be left to every individual test author to
remember to pass an explicit `db_url`. See CLAUDE.md's dated PR 872 remediation
bullet and `docs/architecture/execution.md`'s `data/paper_account_store.py` entry
for how this fits into the broader remediation.
