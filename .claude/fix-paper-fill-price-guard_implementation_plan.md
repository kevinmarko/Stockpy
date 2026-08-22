# Pilots / Paper-Trading Data Integrity, Learning Loop, and A/B Framework

## Context
The paper-trading learning loop does not exist because `PaperAccountStore` has no closed-trade record and no strategy column. This prevents Kelly warm-up, A/B testing, and proper evaluation.

## Proposed Changes
This plan consists of 5 PRs, strictly sequenced.

### PR 1 — fix-paper-fill-price-guard (safety, land first)
- [MODIFY] data/paper_account_store.py: Replace `float(leg.get("fill_price", 0.0))` with a required, validated read. Reject atomic order on zero price.
- [MODIFY] pilots/dispersion_trading.py: Validate client-supplied basket dict.
- [NEW] scripts/purge_corrupt_paper_options.py: Script to purge zero-price options.
- [MODIFY] pilots/vol_mispricing.py: Remove hardcoded spot price fabrications.
- [MODIFY] tests/test_paper_account_store.py: Add reject-on-zero-price tests.
- [NEW] tests/test_purge_corrupt_paper_options.py: Test for purge script.
- [MODIFY] tests/test_dispersion_trading.py: Test for basket validation.
- [MODIFY] tests/test_vol_mispricing.py: Add test for degraded spot.
- [MODIFY] docs/architecture/execution.md, docs/known_issues/paper_options_zero_fill_price.md, CLAUDE.md: Docs updates.

### PR 2 — paper-trade-strategy-attribution
- [MODIFY] data/paper_account_store.py: Add columns `strategy_id`, `pilot_id`, `experiment_arm`, `leg_group_id`, `order_kind`.
- [MODIFY] pilots/*.py & execution/*.py: Thread real `strategy_id` through all writers.
- [MODIFY] execution/options_paper_executor.py: Migrate from symbol string hack to column.
- [MODIFY] tests/*: Add attribution assertions.

### PR 3 — paper-closed-trades-and-transactions-bridge
- [MODIFY] data/paper_account_store.py: Add `paper_closed_trades` table. Write to it on flatten.
- [MODIFY] transactions_store.py: Bridge closed trades to existing `trades` table.
- [MODIFY] config.py: Add `PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED=True`.
- [MODIFY] ml/training_data.py, pipeline/production_steps.py: Read from closed trades, close train/serve skew.
- [NEW] tests/test_paper_closed_trades.py and other tests.
- [MODIFY] docs/*: Documentation updates.

### PR 4 — options-meta-labeler-honest-gate
- [MODIFY] api/pilots_api.py: Rewrite `/pilots/options/meta-model/retrain` to use real paper closes.
- [MODIFY] ml/options_meta_labeler.py: Purged walk-forward split.
- [MODIFY] ml/registry.yaml: Add real CPCV DSR/PBO.
- [MODIFY] execution/options_paper_executor.py: Fail closed when model absent.
- [MODIFY] scripts/retrain_models.py: Add OptionsMetaLabeler.
- [MODIFY] validation/options_harness.py: Fix fail-open gates.
- [MODIFY] OPTIONS_DESK_DEPLOYABILITY_GATES: Register missing strategies.
- [NEW] tests/test_options_harness_gate_honesty.py and others.
- [MODIFY] docs/*: Documentation updates.

### PR 5 — experiment-framework (A/B testing)
- [NEW] experiments/*: `registry.py`, `assignment.py`, `store.py`, `compare.py`.
- [MODIFY] validation/multiple_testing.py: Integrate for DSR.
- [MODIFY] config.py: Add `EXPERIMENTS_ENABLED=False` and others.
- [MODIFY] api/pilots_api.py: Add experiment endpoints.
- [NEW] webapp/src/screens/Experiments.tsx and related files.
- [NEW] tests/test_experiments_registry.py and others.
- [NEW] docs/architecture/experiments.md and others.

## User Review Required
> [!IMPORTANT]
> Since this is a 5-PR rollout, please review the breakdown. We will start with PR 1 in the new worktree `Stockpy-live-agent4` on branch `fix-paper-fill-price-guard`.

## Verification Plan
1. `pytest` for all modified test files.
2. `make verify` before final PR in each pair.
3. specific steps per PR (e.g. running purge script, executing paper round trip).
