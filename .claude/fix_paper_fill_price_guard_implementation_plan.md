# PR 1 — fix-paper-fill-price-guard (safety, land first)

## Goal
Implement strict guardrails to prevent zero or missing option leg prices from booking free positions in the `PaperAccountStore`. Add validations to dispersion basket parsing, purge existing corrupt positions, and remove fabricated spot prices from volatility mispricing.

## Proposed Changes

### `data/paper_account_store.py`
- **[MODIFY]** `apply_fill`, `apply_multi_leg_fill`, and `apply_roll_fill`: Add pre-validation blocks to enforce `fill_price > 0`. A missing, non-numeric, or `<= 0` `fill_price` will reject the entire atomic order (Constraint #6), returning `False` and logging a `REJECTED` order row with the appropriate metadata (strategy_id, pilot_id, etc.). Replace the current `leg.get("fill_price", 0.0)` in the actual execution loops with a strict read (since it's pre-validated).

### `pilots/dispersion_trading.py`
- **[MODIFY]** `execute_dispersion_basket` (lines 1026-1031): Intercept client-supplied `basket` dicts before passing to `DispersionBasket(**basket)`. Validate that every leg carries a positive `fill_price` and a strike consistent with a real resolved spot. On failure, refuse execution and return `{"ok": False, "error": ...}` instead of falling back to a fabricated empty basket.

### `pilots/vol_mispricing.py`
- **[MODIFY]** `evaluate_vol_mispricing` (line 1355): Remove the fallback `spot_price = 500.0 if sym == "SPY" else (130.0 if sym == "NVDA" else 150.0)`. Allow `spot_price` to remain `None` so the honest failure path runs, matching the `options_gex.py` standard.

### `tests/` (Test Additions)
- **[MODIFY]** `tests/test_paper_account_store.py`: Add tests asserting that a zero/missing price rejects the atomic order without partial leg writes.
- **[MODIFY]** `tests/test_dispersion_trading.py`: Add tests for client-supplied basket validation.
- **[MODIFY]** `tests/test_vol_mispricing.py`: Add coverage for the removed spot price fabrication.
- **[NEW]** `tests/test_purge_corrupt_paper_options.py`: Test the purge script's logic.

### `scripts/purge_corrupt_paper_options.py`
- **[EXECUTE]** The script will be run with `--apply` (with a prior backup of `quant_platform.db`) to delete corrupt option rows (`avg_entry_price <= 0`) from `paper_positions`, reverse the corresponding cash impact, and leave valid positions untouched.

### Docs
- **[MODIFY]** `CLAUDE.md` and `docs/architecture/execution.md` to note the paper-broker fill-price validation behavior.
- **[NEW]** `docs/known_issues/paper_options_zero_fill_price.md`: Write up the known issue and its resolution.

## Open Questions
- None. This plan adheres strictly to the exact requirements laid out in the PR 1 instructions.

## User Review Required
Please review the plan to confirm we are ready to implement the `fix-paper-fill-price-guard` changes.
