# Paper-Trading Data Integrity & Strategy Attribution Fixes (Audit & Remediation)

## Context
Following the implementation of PR 1 (Safety/Validation) and PR 2 (Strategy Attribution), an independent multi-agent audit identified integration bugs. Specifically:
1. **Dropped Attribution Metadata**: The new `fill_price <= 0` validation blocks reject invalid orders but fail to thread the new `strategy_id` and other attribution metadata to the `_insert_order` call for the rejected record. This breaks failure tracking in the learning loop.
2. **Migration Edge Case**: Existing positions were migrated with `strategy_id = 'untagged'`. If a strategy attempts to close one of these grandfathered positions, the engine will fail to find it under the strategy's real ID, resulting in a new, unhedged opposite position being opened instead.

## User Review Required
> [!IMPORTANT]
> Please review the proposed fixes below. Once approved, I will implement them directly in `data/paper_account_store.py`.

## Proposed Changes

### `data/paper_account_store.py`

#### [MODIFY] `data/paper_account_store.py`
1. **Fix Dropped Attribution**:
   - Update the `except (ValueError, TypeError):` blocks in `apply_fill`, `apply_multi_leg_fill`, and `apply_roll_fill`.
   - Ensure the `self._insert_order` call within these blocks receives all attribution metadata: `strategy_id`, `pilot_id`, `experiment_arm`, `leg_group_id` (where applicable), and `order_kind` (where applicable).

2. **Fix Migration Edge Case (Fallback to 'untagged')**:
   - In `apply_fill`, after querying for `pos` using the specific `strategy_id`:
     ```python
     if not pos:
         untagged_pos = session.query(PaperPosition).filter_by(symbol=symbol.upper(), strategy_id="untagged").with_for_update().first()
         if untagged_pos:
             # Only fallback if we are attempting a closing action
             if side == "buy" and untagged_pos.qty < -_QTY_EPSILON:
                 pos = untagged_pos
             elif side == "sell" and untagged_pos.qty > _QTY_EPSILON:
                 pos = untagged_pos
     ```
   - Replicate this identical fallback logic in `apply_multi_leg_fill` and `apply_roll_fill` right after fetching the constituent leg's `pos`.

## Verification Plan
### Automated Tests
- Run `pytest tests/test_paper_account_store.py` to ensure core state transitions and safety guards remain intact.
- If necessary, add a quick test case demonstrating the `'untagged'` fallback behavior.
- Ensure the rejection tracking tests still pass.
