# Implementation Plan: Cache Long/Short wash-sale fix

Branch: `fix-cache-long-short-wash-sale`
Source: operator-supplied audit of `engine/cache_long_short_engine.py` (2 findings).

## Scope

1. **HIGH — `check_wash_sale` doesn't implement the wash-sale rule.** It checked
   "closed lot with a realized loss in the last 30 days" instead of "a
   substantially identical security ACQUIRED within 30 days before/after the
   sale" (IRS §1091). Reproduced both failure directions (false-negative on a
   recent open-lot purchase; false-positive on an old, resolved loss).
2. **Lower priority — `check_correlation_drift` docstring/CLAUDE.md claim a
   delegation to `pairs_ondemand.analyze_pair` that was never accurate** —
   `analyze_pair` returns cointegration/beta/z-score diagnostics, not a plain
   correlation coefficient; `_pearson_correlation` is the real, deliberate
   implementation.
3. **Optional — `record_tax_lot`/`close_tax_lot` strip `tzinfo` without first
   normalizing to UTC.** Benign today (every caller passes UTC) but the same
   bug class this repo already hit with FMP's Eastern-time `publishedDate`.

## Design decisions

- `check_wash_sale` reimplemented to query ANY `CacheLongShortTaxLot` (open
  or closed) for the ticker whose `acquisition_date` falls within a ±30
  calendar-day window of the sale date (`as_of`, default now) — status and
  realized-P&L sign are irrelevant to the real rule.
- Scoped to **exact-ticker match only** — not `find_correlated_proxy`'s
  correlated-proxy relationships. The IRS "substantially identical" test is
  narrower than mere price correlation; widening this check to a proxy
  ticker is a deliberate future policy call, stated explicitly rather than
  silently assumed.
- Only the backward-looking half of the 61-day window can be enforced at
  call time (a future repurchase has no row yet). `generate_sell_down_orders`
  gets a new `wash_sale_note` field on its `"approved"` response as the
  operator-facing mitigation for the half the code cannot enforce.
- `check_correlation_drift`/module docstring corrected to state
  `_pearson_correlation` as the deliberate implementation (not a `analyze_pair`
  delegation) — `analyze_pair`'s return shape doesn't carry a plain
  correlation coefficient, so no code-level fix is applicable there, only
  documentation accuracy.
- `data/cache_long_short_store.py` gets a `_naive_utc(dt)` helper mirroring
  `data/broker_fills_store.py`'s helper of the same name/contract.

## Files touched

- `engine/cache_long_short_engine.py` — `check_wash_sale`, `generate_sell_down_orders`,
  `check_correlation_drift` docstring, module docstring.
- `data/cache_long_short_store.py` — `_naive_utc` helper, used in
  `record_tax_lot`/`close_tax_lot`.
- `tests/test_cache_long_short_engine.py` — rewrote `TestCheckWashSale` (2 of
  the old cases encoded the wrong semantics and had to change; added 3 new
  cases: reacquisition-after-harvest, pnl-direction-irrelevant, `as_of` param).
- `tests/test_cache_long_short_store.py` — 2 new tz-normalization regression tests.
- `docs/architecture/signal-engines.md` — new Cache Long/Short entry
  (module had none before; created one per CLAUDE.md's Implementation Plan
  documentation-step requirement).

## Verification

- `python3 -m ruff check ... --select=F821,F822,F823,E9` (CI's actual gate) — clean.
- `pytest tests/test_cache_long_short_engine.py tests/test_cache_long_short_store.py tests/test_cache_long_short_api.py -m "not network and not slow"` — 67 passed.
- Confirmed via grep: `check_wash_sale`/`generate_sell_down_orders` have zero
  production callers today (only `scan_tlh_opportunities`, `find_correlated_proxy`,
  `check_correlation_drift` are wired to the background scanner/API) — so this
  fix has no live behavior-change blast radius, only closes the gap before
  it's wired up.

## Documentation

- `docs/architecture/signal-engines.md` — new entry added (see above).
- CLAUDE.md itself was NOT edited — its existing Cache Long/Short bullet
  doesn't misstate `check_wash_sale`'s behavior in enough detail to need a
  correction, and the module-level docstring/architecture doc are the more
  precise home for this level of detail.
