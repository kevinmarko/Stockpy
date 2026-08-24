# Multi-Agent Audit Report: PR 1 & PR 2

## 1. Safety & Validation Auditor
**Focus**: Numeric bounds, leg prices, and validation gates.

**Findings**:
- The `fill_price > 0` validation blocks added in PR 1 successfully prevent corrupt options with negative or zero entry prices from entering the `PaperAccountStore`.
- The `ValueError` exceptions are correctly caught, and the transaction is cleanly aborted, ensuring no partial state is written to the database.
- **Bug Identified**: In `apply_multi_leg_fill` and `apply_roll_fill`, if `fill_price` is missing, it falls back to `0.0`, which correctly triggers the `ValueError`. However, in the subsequent execution loop, the code uses `leg_fill_price = float(leg["fill_price"])`. If `fill_price` was missing, this would raise a `KeyError`. Wait, the validation block would have already rejected the order, so the execution loop is never reached. This is safe, though using `leg.get("fill_price", 0.0)` in the execution loop (as it currently is in the branch) is slightly more defensive.

## 2. Attribution Auditor
**Focus**: `strategy_id` propagation and DB migration idempotency.

**Findings**:
- The schema migration correctly adds `strategy_id` to `paper_orders` and rebuilds the `paper_positions` table with a composite primary key `(symbol, strategy_id)`.
- **Bug Identified (Migration Edge Case)**: The migration assigns `strategy_id = 'untagged'` to all existing positions. When a strategy later tries to close one of these grandfathered positions, `session.query(PaperPosition).filter_by(symbol=symbol, strategy_id=strategy_id)` will return `None`. The engine will then incorrectly open a *new* opposite position instead of closing the existing one.
- **Bug Identified (Dropped Attribution)**: The new price validation rejection blocks added in PR 1 call `self._insert_order(..., status=OrderStatus.REJECTED)` but **fail to pass** the attribution metadata (`strategy_id`, `pilot_id`, `experiment_arm`, etc.). Rejected orders will be logged as un-attributed, breaking the learning loop's failure tracking.

## 3. Error Handling Auditor
**Focus**: Fail-closed atomicity and rejected order states.

**Findings**:
- The validation steps correctly ensure atomic "fail-closed" behavior (Constraint #6) by aborting the entire multi-leg order if any single leg has an invalid price.
- **Bug Identified**: As noted by the Attribution Auditor, the `_insert_order` calls in the `except (ValueError, TypeError):` blocks lack the full parameter signature. For example, in `apply_fill`:
  ```python
  self._insert_order(session, client_order_id, symbol, side, qty, 0.0, None, OrderStatus.REJECTED, target_qty)
  ```
  This is missing `strategy_id`, `pilot_id`, `experiment_arm`, `leg_group_id`, and `order_kind`.

---

## Recommended Fixes
1. **Fix Dropped Attribution**: Update the `except (ValueError, TypeError):` blocks in `apply_fill`, `apply_multi_leg_fill`, and `apply_roll_fill` to pass all attribution parameters to `self._insert_order`.
2. **Fix Migration Edge Case**: In `apply_fill`, `apply_multi_leg_fill`, and `apply_roll_fill`, if a position is not found for the given `strategy_id` during a closing action (e.g., selling to close), attempt a fallback query for `strategy_id='untagged'` to gracefully handle grandfathered positions.
