# Phase 3 Implementation Plan - Paper-Closed Trades and Transactions Bridge

This plan implements Phase 3 of the paper trading data integrity sequence, focusing on creating a real feedback loop for strategy evaluation and ML models by persisting closed trades.

## Context

Currently, the `PaperAccountStore` deletes a position row when flattened or expired, effectively destroying entry price and holding period. There is no realized PnL record. This breaks `sizing/kelly.py`, `evaluation_engine.py`, and leads to a reconciliation drift in `order_manager.py`. The machine learning pipelines are also starved, leading to a train/serve skew for paper features. 

## User Review Required
> [!IMPORTANT]
> Please review the proposed fixes below. Once approved, I will implement them directly in `data/paper_account_store.py`, `transactions_store.py`, `ml/training_data.py`, and `pipeline/production_steps.py`.

## Proposed Changes

### `data/paper_account_store.py`
#### [MODIFY] `data/paper_account_store.py`
1. **New `PaperClosedTrade` Table**:
   - Define a SQLAlchemy declarative model for `paper_closed_trades` tracking `trade_id`, `strategy_id`, `pilot_id`, `experiment_arm`, `symbol`, `side`, `qty`, `entry_ts`, `entry_price`, `exit_ts`, `exit_price`, `commission`, `realized_pnl`, `realized_pnl_pct`, `holding_period_days`, `close_reason`, `leg_group_id`.
   - Update `_ensure_account_exists` to create the table.
2. **Record Trades on Flatten**:
   - In `apply_fill`, `apply_multi_leg_fill`, `apply_roll_fill` and `settle_expired_options`, immediately before a position is completely deleted or reduced (`session.delete(pos)` or `pos.qty` reduction), construct and insert a `PaperClosedTrade` record.
3. **Transactions Bridge**:
   - When recording a closed trade, if `settings.PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED` is true, wrap a call to `transactions_store.record_trade(...)` and `transactions_store.close_trade(...)` inside a bare `try...except Exception:` block so that failure logs an error but does NOT abort the transaction (Constraint #6 fail closed).

### `settings.py`
#### [MODIFY] `settings.py`
- Add `PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED = bool(os.getenv("PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", "true"))`.

### `transactions_store.py`
#### [MODIFY] `transactions_store.py`
- Expose methods `record_trade` and `close_trade` if not already present or ensure they are callable from the bridge. *(No expected changes here, but verifying compatibility)*.

### `ml/training_data.py`
#### [MODIFY] `ml/training_data.py`
1. **Rewrite `_paper_avg_realized_pnl_30d` and `_paper_hit_rate_30d`**:
   - Read directly from the `paper_closed_trades` table instead of doing triple-barrier simulations on `paper_orders`. Keep the Point-In-Time (PIT) filter (`exit_ts < as_of`).

### `pipeline/production_steps.py`
#### [MODIFY] `pipeline/production_steps.py`
1. **Close the train/serve skew**:
   - Ensure the live `universe_df` is populated with the 6 `paper_*` columns before feature engineering.

## Verification Plan

### Automated Tests
- Run `pytest tests/test_paper_account_store.py`
- Create and run `pytest tests/test_paper_closed_trades.py` to ensure round-trip PnL correctness for long, short, multi-leg, roll, and expiry.
- Verify `tests/test_training_panel.py` to ensure features feed off real closed trades.
- Verify `tests/test_order_manager.py` resolves the drift.

### Manual Verification
- Execute a paper round-trip end-to-end via the Paper Broker screen, and check the DB tables directly.
