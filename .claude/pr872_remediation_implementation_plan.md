# PR 872 Remediation — Implementation Plan

This plan covers a 6-agent remediation of PR 872 ("Phase 3: Paper Closed Trades
Implementation & Test Fixes", commit `74346f99`), the feature that added
`data/paper_account_store.py`'s `paper_closed_trades` ledger so a flattened/expired
paper position's realized PnL, entry time, and holding period survive instead of
being destroyed with the position row (see
`.claude/paper_closed_trades_implementation_plan.md` for that original plan).
PR 872 shipped with real arithmetic, migration-safety, and attribution-safety bugs.
This plan covers finding and fixing all of them, plus documenting the effort.

## Context

The original PR 872 build added:

- A `PaperClosedTrade` SQLAlchemy model / `paper_closed_trades` table.
- `_record_closed_trade()`, called from every position-flattening path
  (`apply_fill`, `apply_multi_leg_fill`, `apply_roll_fill`,
  `settle_expired_options`) immediately before a position is deleted or reduced.
- An opt-in `transactions_store` bridge
  (`settings.PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED`) so `sizing.kelly` and
  `evaluation_engine`'s MAE/MFE/calibration warm up on simulated paper fills.
- `ml/training_data.py` reads from the new table for `paper_avg_realized_pnl_30d`/
  `paper_hit_rate_30d` instead of a triple-barrier price-simulation proxy.
- `pipeline/production_steps.py` populates the live `paper_*` columns before
  feature engineering to avoid a train/serve skew.

A CI-blocking issue and several real correctness/safety bugs were found in this
build before it could be trusted. This plan assigns each to its own agent so they
can proceed with isolated file scope and be independently verified.

## Agent assignments

1. **Agent 1 — CI unblock, scope hygiene.** Get the branch's test suite running
   again and strip any out-of-scope changes that had crept into the PR's diff
   against `origin/main` (verified via a targeted regex sweep for numeric-guard/
   threshold-adjacent changes — see
   `docs/known_issues/pr872_math_regression_sweep_2026.md`).
2. **Agent 2 — PnL arithmetic.** Audit and fix `_record_closed_trade`'s realized-PnL
   math: the options ×100 contract-multiplier double-application, `realized_pnl_pct`'s
   NaN-honesty on a degenerate `avg_entry_price`, real `entry_ts`/`holding_period_days`
   instead of always `None`, and `settle_expired_options`'s per-share-vs-per-contract
   unit mismatch when handing its computed value to `_record_closed_trade`.
3. **Agent 3 — Migration atomicity and attribution safety.** Fix
   `_migrate_paper_positions_schema`'s fail-open partial-commit risk (raw `sqlite3`
   manual-transaction mode plus a pre-migration file backup) and make the legacy
   `'untagged'`-position auto-borrow fallback (`allow_untagged_fallback`) strict
   opt-in, default `False`, with an explicit `retag_position()` backfill primitive
   as its replacement.
4. **Agent 5 — Strategy attribution + train/serve skew + a real guard bug.** Thread
   the real `strategy_id` through every paper-trade writer instead of a default
   placeholder, fix `pipeline/production_steps.py::StrategyEvalStep.run()`'s
   train/serve skew (the `paper_*` feature population ran after the two consumers
   that needed it, and only inside an unrelated flag), and close a `None`-spot-price
   guard gap in `pilots/vol_mispricing.py`.
5. **Agent 4 — Cross-store contention + a dead call.** Fix the `transactions_store`
   bridge's same-process WAL-writer contention (share the caller's own session via
   a SAVEPOINT instead of opening a second connection mid-transaction) and the
   wrong-database bug it also carried (a bare `TransactionsStore()` re-resolving the
   default URL regardless of the paper store's own `db_url`). Also fixed a dead
   `place_order` call discovered in `pilots/copula_stat_arb.py` while verifying the
   bridge fix's surrounding code.
6. **A same-session follow-up (found while independently re-verifying Agent 4's
   work)** — a live-DB test-contamination incident: the bridge's new implicit write
   path had no test-isolation guard, and the real, shared
   `~/.stockpy_local/quant_platform.db` was found holding 260 rows of synthetic test
   data. Fixed via a new autouse `conftest.py` fixture routing both
   `PaperAccountStore` and `TransactionsStore` to an isolated per-test DB by
   default. See `docs/known_issues/pr872_live_db_test_contamination_2026.md`.
7. **Agent 6b (this agent) — documentation wrap-up.** Bring
   `docs/architecture/execution.md`, `docs/architecture/ml-and-reports.md`,
   `CLAUDE.md`/`AGENTS.md`, and the `docs/known_issues/` index up to date with the
   actual final state of the code across all of the above; write this plan/task/
   walkthrough set; confirm `docs/VALIDATION_STRATEGY_FIX_LOG.md` doesn't apply.

## Proposed documentation changes (this agent's own scope)

### `docs/architecture/execution.md`
Rewrite the `data/paper_account_store.py` bullet in place to describe the fill-price
guard, the real `paper_closed_trades` ledger and its corrected PnL arithmetic, the
migration-atomicity fix, the strict-opt-in `allow_untagged_fallback` +
`retag_position()`, and the `transactions_store` bridge's session-sharing/
fails-open posture — all verified against the actual current file, not assumed
from this prompt.

### `docs/architecture/ml-and-reports.md`
Add a dated sub-bullet under the existing `ml/training_data.py::build_training_panel`
entry describing the shared `_paper_features_for_symbol` helper, the switch from
the triple-barrier proxy to genuine `paper_closed_trades` data, the train/serve-skew
fix in `pipeline/production_steps.py::StrategyEvalStep.run()`, and an honest
data-availability caveat (the six `paper_*` NaN-defaulted columns still need real
trade-history volume to become informative — this is latency, not a bug).

### `CLAUDE.md` / `AGENTS.md`
One new dated bullet (item 8 in the existing Paper Execution numbered list)
summarizing the whole remediation honestly, including the live-DB test-contamination
incident stated plainly. Copied byte-identical into both files (verified via `diff`).

### `docs/known_issues/`
New `pr872_live_db_test_contamination_2026.md`, following the established
Status/Date/Incident-Level/Root-Cause/Mitigation format. Indexed in
`docs/known_issues/README.md`, along with two previously-unindexed sibling files
found while doing so (`paper_options_zero_fill_price.md`,
`pr872_math_regression_sweep_2026.md`) — closing that index-drift gap while
touching the same table.

### `docs/VALIDATION_STRATEGY_FIX_LOG.md`
Checked, not touched: this log's stated scope is `STRATEGY_REGISTRY`
deployability-gate (PBO/DSR/Sharpe/MaxDD) changes. This remediation is a
data-integrity/plumbing fix with no strategy-parameter change, so no entry
applies.

## Verification Plan

- `diff CLAUDE.md AGENTS.md` — must be empty.
- Every specific claim (setting name, function name, default value, test name) in
  every touched doc verified against the real source before writing it down.
- `python3 -m pytest tests/test_paper_account_store.py tests/test_copula_stat_arb.py -q`
  — must still pass.
- Check whether any test validates the `docs/known_issues/` index itself; none
  found as of this writing.
