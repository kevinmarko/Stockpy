# Walkthrough: PR 872 Remediation (6 Agents)

## Overview

PR 872 ("Phase 3: Paper Closed Trades Implementation & Test Fixes", commit
`74346f99`) added `data/paper_account_store.py`'s `paper_closed_trades` ledger so
a flattened/expired paper position's realized PnL, entry time, and holding period
survive instead of being deleted along with the position row — closing a real gap
in `sizing/kelly.py`, `evaluation_engine.py`, and the ML training panel's paper-
feature inputs. The build shipped with real bugs. This was a 6-agent remediation
pass (5 fix agents + this documentation wrap-up agent) working in the same
worktree, on branch `feature/paper-closed-trades`, final commit `8433a635`.

## Changes Made

### Agent 1 — CI unblock, scope hygiene
Got the branch's test suite passing again and stripped changes that had crept
into the diff beyond PR 872's own stated scope. A follow-up audit
(`docs/known_issues/pr872_math_regression_sweep_2026.md`) later regex-swept the
same diff for numeric-guard/threshold-adjacent regressions and found/fixed two
(a test's data-loading path that had reverted to an unthrottled `yfinance` call,
and a docstring that lost its pointer to real registry numbers) — everything
else traced to pre-cleared annotation-only churn or a genuine, correctly-guarded
feature addition.

### Agent 2 — PnL arithmetic (`data/paper_account_store.py`)
Fixed five real bugs in `_record_closed_trade`'s realized-PnL math:
- **Option PnL was inflated 100x** — the options ×100 contract multiplier was
  re-applied on top of prices every writer in this codebase already stores as a
  per-contract dollar amount. Fixed by removing the double-multiply.
- `realized_pnl_pct` now reports `None` — never a fabricated `0.0` — when
  `avg_entry_price` is degenerate (`< 1e-12`).
- A new `PaperPosition.entry_ts` column feeds a real `holding_period_days`
  instead of it always being `None`.
- `settle_expired_options` now converts its per-share `intrinsic` value to
  per-contract before handing it to `_record_closed_trade`, so the ledger row
  agrees with the real cash settlement applied to `acc.cash_balance` by
  construction.
- Net-of-commission `realized_pnl` is now the single basis both
  `paper_hit_rate_30d` and `paper_avg_realized_pnl_30d` share (previously a mix
  of net and gross bases could count a trade as a loss for one and a gain for
  the other).

### Agent 3 — Migration atomicity + attribution safety (`data/paper_account_store.py`)
- `_migrate_paper_positions_schema` (rebuilding the legacy single-column-PK
  `paper_positions` table into the current composite `(symbol, strategy_id)` PK
  schema) is now genuinely atomic: a raw `sqlite3` connection in explicit
  manual-transaction mode drives RENAME → CREATE → INSERT → DROP, because
  Python's stdlib `sqlite3` driver implicitly commits before DDL under a plain
  `engine.begin()` (verified directly — a forced failure left the RENAME and
  CREATE committed and an orphaned `old_paper_positions` table before this fix).
  A pre-migration whole-file backup is taken first (best-effort, never blocks
  startup). The "already migrated?" check is now dialect-aware
  (`sqlalchemy.inspect`) instead of a SQLite-only `PRAGMA table_info` query that
  used to be silently swallowed on Postgres.
- `allow_untagged_fallback` (the legacy `'untagged'`-bucket auto-borrow that used
  to run unconditionally, silently misattributing PnL) is now strict opt-in,
  default `False`. `retag_position()` is the new explicit backfill primitive for
  moving a legacy position onto its real `strategy_id` by hand.

### Agent 5 — Strategy attribution + train/serve skew + a guard bug
- Threaded the real `strategy_id` through every paper-trade writer instead of a
  placeholder default.
- Fixed `pipeline/production_steps.py::StrategyEvalStep.run()`'s train/serve
  skew: `populate_live_paper_features()` used to run AFTER
  `global_registry.run_pre_compute()` and the PIT snapshot copy, and only inside
  `if settings.PIT_CAPTURE_ENABLED:` (a flag unrelated to live inference) — a
  pure no-op, since the columns it wrote were never read by anything that cycle.
  It now runs unconditionally at the top of the step, before both consumers.
- Closed a `None`-spot-price guard gap in `pilots/vol_mispricing.py`.

### Agent 4 — Cross-store contention + a dead call
- The `transactions_store` bridge (`_init_transactions_bridge`,
  `settings.PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED`, default `False`) now
  shares the caller's own session via `session.begin_nested()` (a SAVEPOINT)
  instead of opening a second connection mid-transaction — a standalone repro
  confirmed this doesn't hard-deadlock today but costs ~30-100x the baseline
  wall-clock time from real same-process WAL-writer lock contention.
- Fixed a wrong-database bug in the same bridge: a bare `TransactionsStore()`
  call re-resolves the default DB URL regardless of the paper store's own
  `db_url`, so a non-default-`db_url` store's bridge writes used to land in the
  wrong database entirely. The bridge is now constructed once, bound to the
  SAME `db_url`, at `PaperAccountStore.__init__` time.
- Fixed a dead `place_order` call discovered in `pilots/copula_stat_arb.py`
  while verifying the bridge fix's surrounding code (`apply_multi_leg_fill` is
  the real atomic execution path; the dead call never fired).

### Live-DB test-contamination incident (found during independent re-verification of Agent 4's work)
A follow-up agent verifying Agent 4's bridge fix found the real, shared
`~/.stockpy_local/quant_platform.db`'s `trades` table holding 260 rows of
synthetic test data — confirmed as 100% contamination (the table had 0 rows
before the bridge feature existed at all; every contaminating row carried the
bridge's own auto-generated `notes` tag). Root cause: both `PaperAccountStore`
and `TransactionsStore` resolve their default DB URL to the same live file when
no explicit `db_url` is passed, and the bridge's write fires implicitly deep
inside a normal paper-fill call with no way for a forgetful caller to opt out.

Fixed via a new autouse `conftest.py` fixture,
`_isolate_paper_and_transactions_db_in_tests`, routing both stores' default DB
resolution to an isolated per-test temp-file DB. A plain `sqlite:///:memory:`
and a SQLite shared-cache memory URI were both tried first and rejected (see
`docs/known_issues/pr872_live_db_test_contamination_2026.md` for why — in
short: `:memory:` breaks a pre-existing test that relies on two separate store
constructions seeing the same data, and the shared-cache URI fights
`db_config.create_db_engine`'s own file-vs-memory pooling detection). Three new
regression tests prove the fixture's contract, verified by temporarily
disabling the fixture and confirming they then fail against the real live path.
The live DB was backed up via `sqlite3 ... ".backup"` before cleanup; the
cleanup DELETE itself was run by the human operator, not the agent — blocked
for the agent by the sandbox's destructive-write safety gate.

### Agent 6b (this agent) — documentation wrap-up
- Rewrote `docs/architecture/execution.md`'s `data/paper_account_store.py`
  bullet to describe the current, post-remediation state (fill-price guard,
  real `paper_closed_trades` ledger with corrected PnL arithmetic, migration
  atomicity, strict-opt-in `allow_untagged_fallback`/`retag_position()`, and
  the `transactions_store` bridge's session-sharing/fails-open posture).
- Judged `docs/architecture/data-layer.md` does not need a new bullet:
  `execution.md` already covers `paper_account_store.py` per this repo's
  domain-based-documentation convention (durable stores are documented in the
  doc matching their domain, e.g. `RunHistoryStore`/`ValidationHistoryStore`
  live in `signal-engines.md`/`validation-and-signals.md`, not centrally in
  `data-layer.md`), and the physical DB file is already covered generically by
  the `settings.LOCAL_DATA_ROOT` subfolder table's `quant_platform.db` row.
- Added a dated sub-bullet to `docs/architecture/ml-and-reports.md` describing
  the shared `_paper_features_for_symbol` helper, the switch from the
  triple-barrier proxy to genuine `paper_closed_trades` data, the train/serve-
  skew fix, and an honest caveat that the `paper_*` features still need real
  closed-trade volume to become informative — this is data-availability
  latency, not a remaining bug.
- Wrote `docs/known_issues/pr872_live_db_test_contamination_2026.md` (full
  incident report) and indexed it in `docs/known_issues/README.md`, along with
  two previously-unindexed sibling files found while touching that table
  (`paper_options_zero_fill_price.md`, `pr872_math_regression_sweep_2026.md`) —
  and corrected the stale "7"/"8" write-up counts in `CLAUDE.md`/`docs/README.md`
  to the real total (28).
- Added one dated bullet (item 8) to `CLAUDE.md`'s existing Paper Execution
  numbered list, summarizing the full remediation honestly, including the
  live-DB test-contamination incident stated plainly rather than euphemized.
  Copied byte-identical into `AGENTS.md` (`cp` + `diff` verified empty).
- Checked `docs/VALIDATION_STRATEGY_FIX_LOG.md`: confirmed out of scope — its
  stated scope is `STRATEGY_REGISTRY` deployability-gate (PBO/DSR/Sharpe/MaxDD)
  changes, and this remediation is a data-integrity/plumbing fix with no
  strategy-parameter change. No entry added.
- Wrote this implementation plan, task list, and walkthrough.

## Validation Results

- `python3 -m pytest tests/test_paper_account_store.py tests/test_copula_stat_arb.py -q`
  — **71 passed**, 0 failures.
- `diff CLAUDE.md AGENTS.md` — **empty** (byte-identical).
- Checked for a test validating the `docs/known_issues/README.md` index itself —
  none exists in this repo as of this writing.
- Every specific claim in the touched docs (setting names, function names,
  default values, test names) was verified against the actual current source
  in this worktree before being written down, not assumed from any prior
  agent's description.
