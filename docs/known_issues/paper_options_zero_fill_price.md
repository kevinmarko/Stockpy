# Paper Options Zero Fill Price

**Date**: 2026-08-23
**Status**: Fixed
**Symptom**: Corrupt paper options with a `fill_price` of $0.0, leading to a cost basis of zero. This causes failures or wildly inaccurate P&L and Greeks down the line, resulting in NaN or infinite metrics being surfaced in the portfolio view.
**Root Cause**: In `data/paper_account_store.py`, missing or zero `fill_price` attributes were previously converted via `float(leg.get("fill_price", 0.0))` without strict validation. This allowed orders to "fill" at $0.0 when external market data providers (e.g., mock endpoints, empty chains) failed to provide valid pricing.
**Mitigation/Fix**: `apply_fill`, `apply_multi_leg_fill`, and `apply_roll_fill` now explicitly validate `fill_price`. Any leg with a missing, non-numeric, or `<= 0` `fill_price` will reject the entire atomic order (`OrderStatus.REJECTED`). The fail-closed constraint ensures that incomplete data prevents corrupt position entries rather than generating a zero-cost asset.
**Cleanup**: The `scripts/purge_corrupt_paper_options.py` script was deployed to sweep and purge existing paper option positions in the database with an `avg_entry_price <= 0`.
