# Known issue (2026-08-13): `ForecastTracker` kept writing to the old repo-relative DB after the `LOCAL_DATA_ROOT` migration, splitting `forecast_errors` across two live databases

**Status: code fixed (PR #720); data reconciliation between the old and new
database still pending — not yet done.**

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

## What is still open

**The already-diverged data has not been reconciled.** The 1,974,166 rows in
the old `forecast_errors` table and the (now-growing) rows in the new one
remain two separate sets on disk. This is deliberately deferred, not
resolved:

- It's a data-merge operation on a live trading platform's database, not a
  code change, and it needs the operator's explicit sign-off on approach
  before anything touches it.
- A naive overwrite or move is not safe either direction: overwriting the
  new DB with the old one would destroy whatever the new DB has genuinely
  accumulated in *other* tables since the restart; moving/merging only
  `forecast_errors` forward risks leaving primary-key or timestamp
  collisions unhandled.
- Until reconciled, `FORECAST_SKILL_WEIGHTING_ENABLED`'s inverse-RMSE
  blending is starting its skill history over from the new database — the
  1,974,166 historical rows are sitting unused at the old path, not
  contributing to current skill weights.

This is a real open item on this platform, stated plainly: **code fixed,
data not reconciled.**

## Related

- CLAUDE.md's `settings.LOCAL_DATA_ROOT` bullet — the migration this
  incident was a gap in.
- `scripts/migrate_to_local_data_root.py` — the general-purpose migration
  script for pre-existing repo-relative artifacts; not used here since this
  was a code bug (a store never migrated) rather than a one-time data move
  the script's dry-run/`--apply`/`--verify` flow was designed for.
