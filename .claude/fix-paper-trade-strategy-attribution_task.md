# PR 2: Paper Trade Strategy Attribution Tasks

- [x] **Phase 2: Execution & Paper DB Layer Migration**
  - [x] **`data/paper_account_store.py` Updates:**
      - [x] Expand `PaperOrder` schema with `strategy_id`, `pilot_id`, `experiment_arm`, `leg_group_id`, `order_kind`.
      - [x] Change `PaperPosition` primary key to `(symbol, strategy_id)`.
      - [x] Implement dynamic `ALTER TABLE` / table recreation to upgrade existing `.db` seamlessly during engine start.
      - [x] Update `apply_multi_leg_fill`, `apply_roll_fill` and `apply_fill` to consume attribution metadata and persist it.
  - [x] **`execution/options_paper_executor.py` Updates:**
      - [x] Extract `strategy_id`, `pilot_id`, and `experiment_arm` from generation candidates.
      - [x] Remove string-concat hacks (e.g. `f"{strategy_name} {symbol}"`).
      - [x] Pass attribution metadata directly to store methods.
  - [x] **Tests & Verification:**
      - `[x]` Pass `make ci` or relevant local execution test battery.

- [ ] **Thread Metadata in Other Callers**
  - [ ] Inspect other users of `apply_fill()` (e.g. `queue_builder.py`, `pilots/*.py`) to ensure metadata is passed.

- [ ] **Testing & Verification**
  - [ ] Update `tests/test_paper_account_store.py`.
  - [ ] Update `tests/test_options_paper_executor.py`.
  - [ ] Verify `pytest` passes cleanly.
