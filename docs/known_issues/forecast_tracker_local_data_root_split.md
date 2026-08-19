# Known issue (2026-08-13): `ForecastTracker` kept writing to the old repo-relative DB after the `LOCAL_DATA_ROOT` migration, splitting `forecast_errors` across two live databases

**Status: fully resolved (2026-08-19).** Code fixed in PR #720; the split data
was reconciled on 2026-08-19 — see "Reconciliation (2026-08-19)" below for the
full before/after numbers and verification.

## What happened

`settings.LOCAL_DATA_ROOT` (PR #718) moved every locally-generated model/data
artifact — trained models, `quant_platform.db`, caches, logs — out of the git
worktree and into a single external root (`~/.stockpy_local` by default)
shared across every worktree/checkout on the machine. Every store that reads
or writes `quant_platform.db` was migrated to resolve its path through
`db_config.resolve_database_url()` as part of that PR — `data/historical_store.py`,
`data/paper_account_store.py`, `transactions_store.py`, `sizing/cap_audit_store.py`,
and others all correctly went through this seam.

`forecasting/forecast_tracker.py::ForecastTracker.__init__` did not. Its
default was a hardcoded, CWD-relative literal:

```python
def __init__(self, db_path: str = "quant_platform.db", ...):
```

This bypassed `db_config.resolve_database_url()` / `settings.LOCAL_DATA_ROOT`
entirely. The module was simply missed during PR #718's migration — every
sibling store got the same fix, this one didn't.

This operator's real `.env` has `FORECAST_SKILL_WEIGHTING_ENABLED=true`, so
`main_orchestrator.py`'s `EngineContext.build()` constructs a bare
`ForecastTracker()` every cycle — the unfixed default is on the hot path for
every real pipeline run, not a dead corner.

## Real impact

After PR #718 merged and the live orchestrator daemon
(`desktop.orchestrator_daemon --interval 300`) restarted onto the new code,
**every other table** (`price_bars`, `account_snapshots`,
`symbol_rating_events`, etc.) correctly and exclusively moved to writing at
the new `settings.LOCAL_DATA_ROOT`-anchored database. The `forecast_errors`
table did not move with them — it kept writing to the old repo-relative
location (effectively `<main-checkout>/quant_platform.db`) for hours, via
this one unfixed default, creating a live split between two divergent SQLite
databases on a real production trading platform.

At the moment the split was found:

- **1,974,166** real `forecast_errors` rows existed in the **old** (stale)
  location.
- **0** rows existed yet in the **new** (`LOCAL_DATA_ROOT`-anchored)
  location.

`forecast_errors` is low-stakes relative to core trading logic — it backs
`FORECAST_SKILL_WEIGHTING_ENABLED`'s opt-in inverse-RMSE forecast-blending
feature, not order execution or position sizing — but the divergence is real
data, on a live account, not a hypothetical.

## How it was discovered — and the honest gap

This was caught by **direct manual inspection**: comparing `lsof` output for
the running daemon process against file mtimes and row counts in both
candidate database files. It was **not** caught by any automated test, alert,
or preflight check.

Specifically:

- No test exercised `ForecastTracker()`'s omitted-`db_path` default against a
  real `settings.LOCAL_DATA_ROOT` value — every sibling store's equivalent
  test does this; this one didn't exist.
- There is no automated check today — in the test suite, in
  `scripts/preflight_check.py`, or anywhere else — that would catch "two
  different SQLite files are both receiving real writes for what should be
  one logical database." That gap is still open; this incident did not add
  one.

## The fix

Fixed in [PR #720](https://github.com/kevinmarko/Stockpy/pull/720)
(branch `fix-forecast-tracker-db-path`). `ForecastTracker.__init__`'s default
now mirrors `data/historical_store.py`'s PR #718 fix exactly: the parameter
default changes from the bare literal to `None`, resolved at construction
time via `db_config.resolve_database_url()`. Because this class talks to
SQLite directly (`sqlite3.connect()`, never through SQLAlchemy), the
resolved `sqlite:///<path>` URL is parsed down to a bare filesystem path via
`sqlalchemy.engine.make_url(...).database`; a non-sqlite `DATABASE_URL`
override (this class has never supported any backend but sqlite) falls back
to the historical literal rather than raising or silently mis-resolving.
An explicit caller-supplied `db_path` still always wins.

Covered by 3 new regression tests in `tests/test_forecast_tracker.py`,
pinning: the default resolves via `db_config.resolve_database_url()`, an
explicit override still wins, and a non-sqlite `DATABASE_URL` falls back to
the historical literal instead of raising.

## Reconciliation (2026-08-19)

The operator gave explicit sign-off on approach (pause the live daemon,
merge, restart) six days after this doc was written, once the daemon had
been running continuously long enough that a live audit could re-confirm
the split was still exactly as described here. Live state at the time of
reconciliation:

- OLD (stale, repo-relative) DB: **1,974,166 rows**, `recorded_at` spanning
  `2026-07-10T16:04:24Z` → `2026-08-12T23:43:57Z`.
- NEW (`LOCAL_DATA_ROOT`-anchored, live) DB: **261,522 rows** (grown from the
  1,974,166/0 split originally observed, since the daemon kept running for
  six more days), `recorded_at` spanning `2026-08-13T00:14:14Z` → present.
- **Zero temporal overlap** between the two, re-verified live immediately
  before the merge (not assumed from this doc's earlier numbers): no OLD row
  has `recorded_at >=` the NEW DB's earliest timestamp, and no NEW row has
  `recorded_at <=` the OLD DB's latest timestamp. This is a clean temporal
  cutover, not an interleaved split, which is what made a straightforward
  append-merge safe (no per-row dedup logic was needed).
- Nothing else in the codebase treats `forecast_errors.id` as a foreign key
  (confirmed by a dedicated repo-wide search before merging), so merged rows
  were safely given fresh autoincrement ids in the NEW db rather than
  preserving the OLD ids.

**Mechanics:** `scripts/reconcile_forecast_errors_local_data_root_split.py`
(new, one-time script, kept in the repo as part of this incident's record —
see its own docstring for the full safety design: online WAL-safe backup
before any write, batched/resumable inserts with a generous `busy_timeout`,
and a post-merge verification pass). The live `com.investyo.stack` launchd
service was unloaded (stopping the daemon + APIs cleanly) before the merge
and reloaded immediately after, per the operator's chosen approach — the
script itself is also safe to run against a live daemon (WAL-safe backup +
busy_timeout), but pausing it first was the lower-risk choice for a
~2-million-row one-time merge.

**Result:** all 1,974,166 OLD rows copied into the NEW db in 99 batches
(20,000 rows/batch) in under 25 seconds. Post-merge verification: NEW db's
final `forecast_errors` count is 2,235,688 (261,522 + 1,974,166, plus zero
extra rows from the daemon since it was paused for the merge), OLD db count
unchanged (1,974,166 — it was opened strictly read-only throughout, verified
at the SQLite driver level via a `mode=ro` URI), and **zero new duplicate
`(symbol, model_name, horizon_days, forecast_ts)` tuples** were introduced.
An online backup of the NEW db was taken immediately before the merge
(`quant_platform.db.pre_reconcile_backup.20260819T191658Z`, alongside the
live db) and retained. The daemon was confirmed back up, holding both API
ports and the database, within ~35 seconds of being paused.

The OLD database file was renamed (not deleted) to
`quant_platform.db.pre-migration-2026-08-13.archived` at its original
location, per the operator's explicit choice, so it's unambiguously marked
historical rather than sitting there looking like it might still be live.

`FORECAST_SKILL_WEIGHTING_ENABLED`'s inverse-RMSE blending now has
continuous history back to 2026-07-10 to draw on, rather than starting over
from the 2026-08-13 cutover point.

## Related bug found during reconciliation (fixed separately)

While auditing for other instances of this same bug class before trusting
the merge, a **second, previously-undocumented instance** was found in
`investyo_mcp_server.py` (`_db_query()` and `get_database_schema()`): both
functions called `db_config.resolve_database_url()` but then discarded the
result and substituted a hardcoded cwd-relative `"quant_platform.db"`
literal specifically in the *default* (no custom `DATABASE_URL`) case —
backwards from every other correctly-fixed module. This meant every MCP
tool that reads through `_db_query` (`query_investyo_db`,
`read_platform_logs`, `get_universe_status`, `get_signal_breakdown`,
`generate_daily_signals`, `get_factor_attributions`,
`get_order_execution_history`, and the `get_database_schema` resource) was
silently serving a stale, days-old snapshot instead of the live database —
worse than a loud failure, since the stale file existed and returned
plausible-looking wrong answers with no error. Fixed on this same branch;
see the commit fixing `investyo_mcp_server.py` for detail. Also found: an
orphaned worktree at a since-abandoned parent-repo path still holds its own
479,029-row pre-migration slice of `forecast_errors` (harmless — not
receiving any writes, just disk debris) and a separate local checkout at
`/Users/kevinlee/Anti/Stockpy-main` is on a snapshot that predates PR #718
entirely, so every one of its SQLite-backed stores (not only
`forecast_tracker.py`) would reproduce this same cwd-relative bug class if
that checkout starts running real pipeline cycles — flagged for the
operator, not fixed here (out of scope for this repo/branch).

## Related

- CLAUDE.md's `settings.LOCAL_DATA_ROOT` bullet — the migration this
  incident was a gap in.
- `scripts/migrate_to_local_data_root.py` — the general-purpose migration
  script for pre-existing repo-relative artifacts; not used here since this
  was a code bug (a store never migrated) rather than a one-time data move
  the script's dry-run/`--apply`/`--verify` flow was designed for.
