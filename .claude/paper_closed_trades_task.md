# Phase 3 Task List

- [ ] `data/paper_account_store.py` Updates
  - [x] Add `PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED` toggle in `settings.py`.
  - [x] Add `PaperClosedTrade` model to `data/paper_account_store.py`.
  - [x] Create `_record_closed_trade()` helper method in `PaperAccountStore`.
  - [x] Intercept `apply_fill` flatten logic to capture closed trades.
  - [x] Intercept `apply_multi_leg_fill` flatten logic.
  - [x] Intercept `apply_roll_fill` flatten logic.
  - [x] Intercept `settle_expired_options` flatten logic.
  - [x] Ensure transaction bridge to `transactions_store.py` is safely wrapped.

- [ ] `settings.py` Updates
  - [x] Add `PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED` setting.

- [ ] `ml/training_data.py` Updates
  - [x] Update `ml/training_data.py` to use `paper_closed_trades` for `paper_avg_realized_pnl_30d` and `paper_hit_rate_30d`.

- [x] `pipeline/production_steps.py` Updates
  - [x] Populate the 6 `paper_*` columns in the live `universe_df` before feature engineering to fix train/serve skew.

- [x] Testing and Verification
  - [x] Run `make verify` and test suite.
  - [x] Verify `transactions_store.py`'s compatibility with the bridge.
  - [ ] Run `pytest tests/test_paper_account_store.py`.
  - [ ] Create and run `tests/test_paper_closed_trades.py`.
  - [ ] Run `pytest tests/test_training_panel.py`.
  - [ ] Run `pytest tests/test_order_manager.py` to verify drift is resolved.
