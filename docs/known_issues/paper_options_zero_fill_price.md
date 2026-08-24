# Paper Options Zero Fill Price

**Status**: Mitigated
**Date**: 2026-08-22
**Incident Level**: Medium (Corrupt paper trading performance tracking)

## Root Cause
`PaperAccountStore`'s atomic order processing functions (`apply_multi_leg_fill`, `apply_roll_fill`) silently defaulted to a zero fill price if a leg was missing one (`float(leg.get("fill_price", 0.0))`). Furthermore, they did not validate if a `fill_price` was genuinely positive. Because of this, certain client-supplied inputs (like the Dispersion Trading pilot) were able to push through multi-leg options orders where some legs had a zero price, completely destroying the integrity of their cost basis, Greeks, and P&L tracking (producing $150.00 constituent / $500.00 QQQ strike residue).

## Mitigation
1. Replaced all occurrences of `float(leg.get("fill_price", 0.0))` with a rigid validation check in `PaperAccountStore` that explicitly raises a `ValueError` if `fill_price <= 0`.
2. A single invalid leg now rejects the entire atomic order (Constraint #6 - Fail closed, do not skip the leg).
3. Client-supplied basket dicts in `pilots/dispersion_trading.py` now explicitly validate positive `fill_price` and strike consistency against real spot data before constructing a `DispersionBasket`.
4. Hardcoded spot price fabrications were removed in `pilots/vol_mispricing.py` to allow the platform's honest execution paths to handle absent data cleanly.
5. A purge script `scripts/purge_corrupt_paper_options.py` was introduced to scan the SQLite DB for any previously opened options with a non-positive `avg_entry_price`, delete them, and reverse any residual cash impact.
