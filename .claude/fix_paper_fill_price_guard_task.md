# Fix Paper Fill Price Guard (PR 1) Tasks

- `[ ]` 1. `data/paper_account_store.py`: Enforce `fill_price > 0` validation for `apply_fill`, `apply_multi_leg_fill`, and `apply_roll_fill`, rejecting atomic orders on failure.
- `[ ]` 2. `pilots/dispersion_trading.py`: Validate client-supplied `basket` dictionaries before creating `DispersionBasket`.
- `[ ]` 3. `pilots/vol_mispricing.py`: Remove the fabricated `spot_price = 500.0` logic.
- `[ ]` 4. Create and implement `tests/test_purge_corrupt_paper_options.py`.
- `[ ]` 5. Update test coverage in `tests/test_paper_account_store.py`, `tests/test_dispersion_trading.py`, and `tests/test_vol_mispricing.py`.
- `[ ]` 6. Execute `scripts/purge_corrupt_paper_options.py` with `--apply` after backing up the DB.
- `[ ]` 7. Update docs: `CLAUDE.md`, `docs/architecture/execution.md`, and `docs/known_issues/paper_options_zero_fill_price.md`.
