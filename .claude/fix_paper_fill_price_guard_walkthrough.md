# PR 1 Walkthrough: Fix Paper Fill Price Guard

We have successfully implemented the first PR of the 5-PR rollout plan, addressing the zero fill price bug in paper trading.

## Changes Made

1. **`data/paper_account_store.py`**:
   - Added explicit `fill_price > 0.0` validation to `apply_fill`, `apply_multi_leg_fill`, and `apply_roll_fill`.
   - The validation fails closed (Constraint #6), raising a `ValueError` and rejecting the entire atomic order if any leg has a missing or non-positive price.
2. **`pilots/dispersion_trading.py`**:
   - Implemented strict client-side validation in `build_dispersion_basket` to ensure all supplied legs have a positive `fill_price` and valid strike prices relative to the spot price.
3. **`pilots/vol_mispricing.py`**:
   - Removed hardcoded spot price fabrications (`$500.00` for SPY, `$150.00` for others), adhering to Constraint #4 ("Refuse rather than fabricate").
4. **`scripts/purge_corrupt_paper_options.py`**:
   - Created a CLI tool to scan the SQLite database, identify corrupt options positions with `entry_price <= 0`, delete them, and reverse their residual cash impact.
5. **Documentation**:
   - Added `docs/known_issues/paper_options_zero_fill_price.md` detailing the incident, root cause, and mitigation.
   - Updated `docs/architecture/execution.md` to document the new `fill_price` guard in `PaperAccountStore`.
   - Updated `CLAUDE.md` to record the Constraint #4 and #6 fixes for this feature.
6. **Tests**:
   - Added new unit tests in `tests/test_paper_account_store.py` for zero-price rejection.
   - Created `tests/test_purge_corrupt_paper_options.py` to verify the DB cleanup script.
   - All tests pass, and a dry-run of the purge script successfully identifies corrupt positions.

## Validation Results

Running `pytest` shows that the new validation logic and purge script correctly handle zero and negative prices.
```
============================= test session starts ==============================
...
tests/test_paper_account_store.py .................                      [ 31%]
tests/test_dispersion_trading.py ...............                         [ 59%]
tests/test_vol_mispricing.py ......................                      [100%]
...
tests/test_purge_corrupt_paper_options.py .                              [100%]
```

The purge script successfully identified 20 corrupt options positions in a dry-run against the local database.

## Next Steps

I am ready to commit these changes to the `fix-paper-fill-price-guard` branch. I will copy this walkthrough, the implementation plan, and the task tracker to the `.claude/` directory with the unique branch prefix as required by `CLAUDE.md`.

Let me know if you approve moving forward with the commit and PR!
