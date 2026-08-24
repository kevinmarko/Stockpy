# PR 872 Remediation — Task List

- [x] Agent 1 — CI unblock, scope hygiene
  - [x] Get the branch's test suite running again.
  - [x] Strip out-of-scope changes that had crept into the diff against `origin/main`.

- [x] Agent 2 — PnL arithmetic fixes (`data/paper_account_store.py`)
  - [x] Fix the options ×100 contract-multiplier double-application in `_record_closed_trade`.
  - [x] Make `realized_pnl_pct` NaN-honest (`None`, never a fabricated `0.0`) on a degenerate `avg_entry_price`.
  - [x] Add `PaperPosition.entry_ts` and compute a real `holding_period_days`.
  - [x] Fix `settle_expired_options`'s per-share-vs-per-contract unit mismatch before calling `_record_closed_trade`.
  - [x] Fix net-of-commission vs. gross basis inconsistency feeding downstream features.

- [x] Agent 3 — Migration atomicity + attribution safety (`data/paper_account_store.py`)
  - [x] Make `_migrate_paper_positions_schema` atomic (raw `sqlite3` manual-transaction mode).
  - [x] Add a pre-migration whole-file backup (best-effort, never blocks startup).
  - [x] Make the "already migrated?" check dialect-aware (`sqlalchemy.inspect`), not SQLite-only.
  - [x] Make `allow_untagged_fallback` strict opt-in, default `False`.
  - [x] Add `retag_position()` as the explicit backfill primitive.

- [x] Agent 5 — Strategy attribution + train/serve skew + guard bug
  - [x] Thread real `strategy_id` through every paper-trade writer.
  - [x] Fix `pipeline/production_steps.py::StrategyEvalStep.run()`'s train/serve skew
        (move `populate_live_paper_features` before `run_pre_compute`/PIT snapshot, unconditionally).
  - [x] Close the `None`-spot-price guard gap in `pilots/vol_mispricing.py`.

- [x] Agent 4 — Cross-store contention + dead call
  - [x] Fix `transactions_store` bridge same-process WAL-writer contention (share session via SAVEPOINT).
  - [x] Fix the wrong-database bug (bind the bridge's `TransactionsStore` to the same `db_url`).
  - [x] Fix `pilots/copula_stat_arb.py`'s dead `place_order` call found while verifying the above.

- [x] Live-DB test-contamination incident (found during independent re-verification of Agent 4's work)
  - [x] Confirm 260 rows of synthetic test data in the live `trades` table as 100% contamination.
  - [x] Add autouse `conftest.py` fixture (`_isolate_paper_and_transactions_db_in_tests`).
  - [x] Add 3 regression tests proving the fixture's contract.
  - [x] Back up the live DB (`sqlite3 ... ".backup"`).
  - [x] Have the operator run the cleanup DELETE (blocked for the agent by the destructive-write safety gate).
  - [x] Write `docs/known_issues/pr872_live_db_test_contamination_2026.md`.

- [ ] Agent 6b (this agent) — documentation wrap-up
  - [x] Rewrite `docs/architecture/execution.md`'s `data/paper_account_store.py` bullet against the real current code.
  - [x] Add a dated sub-bullet to `docs/architecture/ml-and-reports.md` for `ml/training_data.py`'s fix.
  - [x] Evaluate whether `docs/architecture/data-layer.md` needs a bullet — judged: no, `execution.md` already covers it and the domain-based-doc convention holds; the physical DB file is already covered generically by the `LOCAL_DATA_ROOT` table.
  - [x] Write `docs/known_issues/pr872_live_db_test_contamination_2026.md` (this agent's own pass, following the doc's own root-cause account plus the existing established format).
  - [x] Index the new known_issues file, plus two previously-unindexed sibling files, in `docs/known_issues/README.md`.
  - [x] Add one dated bullet to `CLAUDE.md` covering the full honest remediation, including the live-DB incident stated plainly.
  - [x] Copy the bullet byte-identical into `AGENTS.md`; verify via `diff`.
  - [x] Check `docs/VALIDATION_STRATEGY_FIX_LOG.md` — confirmed out of scope (no strategy-parameter change), no entry added.
  - [x] Write `.claude/pr872_remediation_implementation_plan.md` / `_task.md` / `_walkthrough.md`.
  - [ ] Run required verification (see below) and commit.

## Verification

- [x] `diff CLAUDE.md AGENTS.md` — empty.
- [x] `python3 -m pytest tests/test_paper_account_store.py tests/test_copula_stat_arb.py -q` — passes.
- [x] Checked for a test validating the `docs/known_issues/` index — none found.
