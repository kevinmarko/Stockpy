# PR 2: Paper Trade Strategy Attribution

This PR implements the second step of the 5-PR paper-trading learning loop and A/B framework rollout. By adding structural attribution metadata to paper trades, we enable accurate Kelly warm-up, backtest vs. live P&L tracking, and A/B experimentation arms.

## User Review Required
> [!IMPORTANT]
> **Database Schema Migration (SQLite):**
> SQLite has limited `ALTER TABLE` support. We can use `ALTER TABLE paper_orders ADD COLUMN ...` for adding columns since they will be nullable by default.
> However, for `PaperPosition`, if we want a strategy to hold a position independently of another strategy, its primary key must change from `symbol` to `(symbol, strategy_id)`. SQLite does NOT support altering primary keys via `ALTER TABLE`. We will need to either recreate the `paper_positions` table or just clear the paper DB (since it's a sandbox/paper DB).
> **Question:** Is it acceptable to drop and recreate the `paper_positions` table during the schema upgrade since this is paper trading?

## Open Questions
> [!WARNING]
> 1. Should we add these columns to `PaperPosition` as well and change its primary key to a composite `(symbol, strategy_id)`, or is the attribution exclusively required on `PaperOrder` for trade history tracking? (I will assume both unless advised otherwise, as isolated P&L tracking requires separate positions).
> 2. Should `leg_group_id` be generated via `uuid` at the executor level, or should it use the `client_order_id` of the parent order?

## Proposed Changes

---

### Database Layer (`data/`)

#### [MODIFY] [paper_account_store.py](file:///Users/kevinlee/Stockpy-live-agent4/data/paper_account_store.py)
- **PaperOrder Schema**: Add `strategy_id`, `pilot_id`, `experiment_arm`, `leg_group_id`, and `order_kind` (e.g. 'parent', 'leg') as `Column(String, nullable=True)`.
- **PaperPosition Schema**: Add `strategy_id`, `pilot_id`, and `experiment_arm`. Update the primary key to be `(symbol, strategy_id)` if positions are allowed to overlap between strategies.
- **`_ensure_account_exists()`**: Add `ALTER TABLE` statements (with try/except) to gracefully add the new columns to existing local databases.
- **`_insert_order()`**: Update signature to accept the new attribution kwargs and persist them.
- **`apply_fill()`, `apply_multi_leg_fill()`, `apply_roll_fill()`**: Update signatures to accept the new metadata and pass them to `_insert_order()`. Thread `strategy_id` through the position updates.

---

### Execution Layer (`execution/`)

#### [MODIFY] [options_paper_executor.py](file:///Users/kevinlee/Stockpy-live-agent4/execution/options_paper_executor.py)
- **Migrate Symbol Hack**: In `execute_strategy_directives()`, `apply_multi_leg_fill` is currently called with `strategy_name=strategy`, which `paper_account_store.py` then hacks into the symbol string: `f"{strategy_name} {symbol}"`. We will remove this hack. The `symbol` will cleanly be just the symbol, and `strategy_id` will be passed explicitly as attribution metadata.
- **Thread Metadata**: Pass the correct `order_kind` ("parent" for the aggregate spread order, "leg" for the constituent legs) and the new attribution fields when calling `apply_multi_leg_fill()`. 

#### [MODIFY] [queue_builder.py](file:///Users/kevinlee/Stockpy-live-agent4/execution/queue_builder.py) or `pilots/*.py`
- Find callers that issue paper trades and thread the `strategy_id` down to the paper account store where applicable.

---

### Testing Layer (`tests/`)

#### [MODIFY] [test_paper_account_store.py](file:///Users/kevinlee/Stockpy-live-agent4/tests/test_paper_account_store.py)
- Add tests to verify that `strategy_id` and `experiment_arm` are persisted correctly on `PaperOrder`.
- Verify that the symbol string hack is no longer present and that multi-leg orders can be uniquely identified by `leg_group_id`.

#### [MODIFY] [test_options_paper_executor.py](file:///Users/kevinlee/Stockpy-live-agent4/tests/test_options_paper_executor.py)
- Assert that the executor sets the appropriate metadata (e.g., `strategy_id` matching the directive) when passing orders to the store.

## Verification Plan

### Automated Tests
- Run `uv run pytest tests/test_paper_account_store.py` and `uv run pytest tests/test_options_paper_executor.py` to ensure backwards compatibility and verify the new assertions.

### Manual Verification
- Run a dry-run execution queue or dummy order in the sandbox to observe the `paper_orders` table locally (via sqlite3 shell) and ensure the attribution columns are correctly populated.
