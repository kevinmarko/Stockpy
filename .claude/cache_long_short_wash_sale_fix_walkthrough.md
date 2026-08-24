# Walkthrough: Cache Long/Short wash-sale fix

## What was wrong

`engine/cache_long_short_engine.py::check_wash_sale(ticker)` was supposed to
implement the IRS wash-sale rule (26 U.S.C. § 1091) — a loss is disallowed if
a "substantially identical" security was **acquired** within 30 days before
or after the sale. Instead it checked whether the ticker had a **closed lot
with a realized loss** in the last 30 days — an entirely different question
that neither implies nor is implied by an actual wash sale:

- **False negative (the dangerous direction):** an open lot purchased 25 days
  ago, no closed lot at all — the textbook wash-sale trigger — returned
  `False` ("safe to harvest") when it should return `True`.
- **False positive (over-conservative, not dangerous):** a closed loss lot
  from 20+ days ago with no repurchase since returned `True` (blocked) when
  it should return `False`.

Both `check_wash_sale`'s docstring and `generate_sell_down_orders`' blocked-
reason string invoked real IRS wash-sale terminology, so this read as
trustworthy compliance logic to anyone wiring it up later. It's currently
dead code (confirmed via grep — zero production callers), which is exactly
why this needed fixing now rather than being discovered live.

## The fix

`check_wash_sale(ticker, as_of=None)` now queries **any** tax lot (open or
closed, status/realized-P&L irrelevant) for the ticker whose
`acquisition_date` falls within a ±30-calendar-day window of the sale date.
Acquisition timing is the only thing that determines wash-sale eligibility.

Two structural limits, stated explicitly rather than glossed over:

1. **Only the backward-looking half of the window is a real check at call
   time** — a future repurchase has no database row to find before it
   happens. `generate_sell_down_orders`'s `"approved"` response now carries a
   `wash_sale_note` telling the operator not to repurchase for 30 days after
   the sale — the honest, operator-facing mitigation for the half the code
   structurally cannot enforce.
2. **Scoped to exact-ticker match only**, not the correlated-proxy
   relationships `find_correlated_proxy` tracks elsewhere in this module.
   The IRS's "substantially identical" test is narrower than price
   correlation. Widening this check to proxy tickers is left as a future,
   deliberate policy decision — not silently assumed.

## The second finding: `check_correlation_drift`/docstring

The module docstring (and CLAUDE.md) described `find_correlated_proxy` and
`check_correlation_drift` as both delegating their correlation number to
`pairs_ondemand.analyze_pair`. Only half true: `find_correlated_proxy` calls
`analyze_pair` to **rank** candidates by cointegration p-value, but the
persisted correlation coefficient always came from a separately-implemented
`_pearson_correlation` helper — because `analyze_pair` returns cointegration/
beta/z-score/half-life diagnostics, not a plain correlation coefficient.
There's no "the" `analyze_pair` correlation to reuse. Since `_pearson_correlation`
is the only viable implementation of "a plain correlation number" here, this
was a documentation-accuracy fix, not a code fix: both docstrings now state
this precisely instead of the previously-inaccurate delegation claim.

## Optional fix taken: tz-normalization

`record_tax_lot`/`close_tax_lot` stripped `tzinfo` via a bare
`.replace(tzinfo=None)` without first converting to UTC. Every real caller
already passes UTC today, so this was benign in practice — but it's the same
bug class this repo already hit with FMP's Eastern-time `publishedDate`
(CLAUDE.md's "FMP as the primary company-news provider" bullet). Fixed via a
`_naive_utc(dt)` helper, mirroring `data/broker_fills_store.py`'s helper of
the same name/contract exactly.

## Test changes

Two of the four pre-existing `TestCheckWashSale` cases encoded the *wrong*
semantics (they specifically tested the closed-lot/realized-P&L logic being
removed) and had to be rewritten rather than merely extended — this was
caught immediately by the repo's `PostToolUse` targeted-test hook the moment
the fix landed, confirming the change actually flips behavior rather than
being a no-op. Final suite: 8 `TestCheckWashSale` cases covering both
original failure directions from the audit, reacquisition-after-harvest
(the other classic trigger, never checkable via close_date alone), the
irrelevance of a closed lot's gain/loss sign, and an `as_of`-parametrized
historical check. Plus 2 new tz-normalization regression tests in
`tests/test_cache_long_short_store.py`.

## Verification

- `ruff check ... --select=F821,F822,F823,E9` (the actual CI gate per
  `.claude/commands/verify.md`) — clean.
- `pytest tests/test_cache_long_short_engine.py tests/test_cache_long_short_store.py tests/test_cache_long_short_api.py -m "not network and not slow"` — 67 passed, 0 failed.
- Confirmed via grep that `check_wash_sale`/`generate_sell_down_orders` have
  zero production callers today, so this fix has no live-behavior blast
  radius on the running system — only closes the correctness gap before a
  future PR wires this up to `main_orchestrator.py`'s background scanner or
  a Pilots API write endpoint.

## Documentation

Added a new `engine/cache_long_short_engine.py` entry to
`docs/architecture/signal-engines.md` (the module had no dedicated
architecture-doc entry before this — only a CLAUDE.md bullet) describing the
module's real structure and this fix in full, per CLAUDE.md's Implementation
Plan documentation-step requirement.
