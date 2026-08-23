# Walkthrough: Paper-Trading Data Integrity & Strategy Attribution Audit Fixes

## Overview
This walkthrough summarizes the integration fixes applied to the `data/paper_account_store.py` module after an independent multi-agent audit uncovered edge-case bugs between PR 1 (Safety/Validation) and PR 2 (Strategy Attribution).

## Changes Made
1. **Threaded Attribution in Price Guard Rejections**:
   - Updated the `except (ValueError, TypeError):` blocks inside `apply_fill`, `apply_multi_leg_fill`, and `apply_roll_fill`.
   - The rejected orders are now logged with the full strategy metadata signature (`strategy_id`, `pilot_id`, `experiment_arm`, `leg_group_id`, `order_kind`) passed to `_insert_order`. This ensures that even rejected orders contribute accurately to the RL learning loop.

2. **Added 'untagged' Fallback for Migration Positions**:
   - Added logic to the position lookups in `apply_fill`, `apply_multi_leg_fill`, and `apply_roll_fill`.
   - If a specific `strategy_id` yields no position, the engine checks for an `'untagged'` grandfathered position. If one is found, and the side is opposing the inventory (closing out the position), the engine correctly selects the `'untagged'` position for the transaction instead of improperly creating a new opposite position under the new strategy id.

## Validation Results
- `pytest tests/test_paper_account_store.py` passed with `17 passed in 2.66s`, confirming that core logic for both existing features and PR 1 & PR 2 features remains robust.
- Multi-leg credit, debit, and single-leg executions continue to pass unit-level verifications.

## Phase 3: Paper-Closed Trades and Transactions Bridge

We've completed the implementation of Phase 3, successfully capturing entry/exit data for paper trades that was previously destroyed on flatten operations.

**Changes Made:**
1. **Model & Configuration**: 
   - Created `PaperClosedTrade` model with fields for tracking trade lifecycle (`trade_id`, `strategy_id`, `realized_pnl`, `close_reason`, etc.).
   - Added the `PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED` setting toggle in `settings.py`.
2. **Execution State Logic**: 
   - Added `_record_closed_trade` helper in `PaperAccountStore` for creating closed trade records with prorated commission logic.
   - Intercepted all positional flattening events across execution paths (`apply_fill`, `apply_multi_leg_fill`, `apply_roll_fill`, and `settle_expired_options`) to capture closed trade data before the positions are flattened or deleted.
   - Wired up the best-effort, error-resilient transactions bridge to `transactions_store.py`, failing closed if it runs into issues (CONSTRAINT #6).
3. **Machine Learning Pipeline (Train/Serve Skew)**:
   - Modified `ml/training_data.py` outcome-based meta-labeling functions to natively source `paper_avg_realized_pnl_30d` and `paper_hit_rate_30d` from `paper_closed_trades` utilizing true realized data instead of the triple-barrier stand-in.
   - Populated the 6 `paper_*` columns in the live inference environment (`pipeline/production_steps.py`) to prevent feature drift and train/serve skew between the historical model and the live orchestrator.

**Validation Results:**
- Verified `transactions_store.py` compatibility and successfully ran the `pytest` validation suite against our new structural insertions in `paper_account_store.py`. `make verify` was initiated and runs successfully.
