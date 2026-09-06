# Walkthrough: `copula_stat_arb` options-selling stress-gate exclusion

## What was asked

An audit flagged that `copula_stat_arb` (a `STRATEGY_REGISTRY` entry with an honest, already
measured/documented FAIL — Sharpe=-0.455, PBO=0.000, DSR=0.246, MaxDD=35.1%, deployable=False)
is not included in `_resolve_options_selling_stress_fn(name)`, the function
`scripts/refresh_validations.py` uses to decide whether a strategy runs through the mandatory
options-selling tail-scenario stress gate (`validation/stress_scenarios.py`). The task was to
investigate whether this is a real gap or a correct exclusion, and fix accordingly — not to
blindly wire in a stress test.

## Investigation

Read `pilots/copula_stat_arb.py` in full. The module implements copula-based (Clayton/Gumbel/
Frank/Gaussian) statistical arbitrage on cointegrated equity pairs with a Kalman-filter dynamic
hedge ratio. The actual trade-execution function, `execute_copula_spread_trade`:

- Computes `side_y`/`side_x` as plain `BUY`/`SELL` directions on the pair's two symbols.
- Sizes `qty_y`/`qty_x` as integer **share counts** (`leg_capital / price_y`, etc.).
- Submits both legs atomically via `PaperAccountStore.apply_multi_leg_fill`, with an inline
  comment stating explicitly: "`qty` here is SHARES, not options contracts, but the method
  itself makes no options-specific assumption in its per-leg cash/position bookkeeping."

There is no options contract construction, pricing, Greeks computation, or expiration handling
anywhere in the module. It is also correctly absent from `PAPER_BROKER_OPTIONS_STRATEGIES`
(`scripts/refresh_validations.py`'s own list of strategies that sell option premium in the live
Paper Broker), which already includes `call_credit_spread`, `call_debit_spread`, `covered_call`,
`put_credit_spread`, `put_debit_spread`, `vrp_premium_selling`.

**Conclusion**: `copula_stat_arb` is a pure equity pairs/stat-arb strategy, not an options-selling
strategy. CLAUDE.md's stress-gate rule ("Options-selling strategies carry an additional
tail-scenario stress gate...") does not apply to it by its own terms. Forcing a
`stress_returns_fn` onto it would misapply a gate designed to measure short-premium blowup risk
in dated shock windows to a strategy that never sells premium — its real risk (cointegration
breakdown) is already captured by the standard PBO/DSR/Sharpe/MaxDD gate, which it has already
been run through and honestly failed.

## Why this differs from `zero_dte_engine`/`gamma_scalper`

Those two ARE options-selling-shaped strategies but are excluded from the gate for a
**structurally different reason**: no reachable historical data exists to run the gate against
(no intraday history, no point-in-time options chain, no 1-minute bars for the four required
shock windows). `copula_stat_arb`'s exclusion reason is categorically different: the gate simply
doesn't apply to it at all, regardless of data availability, because it isn't an options-selling
strategy. Both distinctions are now spelled out in the code comment and docs so a future reader
doesn't conflate the two.

## Changes made

1. **`scripts/refresh_validations.py`** — added a code comment at the end of
   `_resolve_options_selling_stress_fn`, right before its final `return None`, explaining the
   `copula_stat_arb` exclusion with the trade-construction evidence and the contrast with
   `zero_dte_engine`/`gamma_scalper`. No behavioral change — the function's dispatch table is
   otherwise untouched; `copula_stat_arb` still resolves to `None` (verified) exactly as before.

2. **`docs/signals/copula_stat_arb.md`** — added an "Options-selling tail-scenario stress gate:
   not applicable (addendum, 2026-09)" bullet to the "Backtest Validation & Deployability Status"
   section, citing the same evidence.

3. **`docs/VALIDATION_STRATEGY_FIX_LOG.md`** — appended an addendum to the existing 2026-08-19
   `copula_stat_arb` entry (rather than creating a new dated entry) documenting the investigation,
   the finding, and the explicit decision to leave `is_options_selling=False`/`stress_returns_fn=None`
   unchanged for this strategy.

No `STRATEGY_REGISTRY` entry was touched. No new settings, no new tests (documentation-only
change to existing, already-correct runtime behavior).

## Verification

- `python -c "import ast; ast.parse(...)"` on the edited file — parses cleanly.
- Direct call: `_resolve_options_selling_stress_fn('copula_stat_arb')` → `None` (unchanged).
  `_resolve_options_selling_stress_fn('vrp_premium_selling')` → real function (unchanged).
  `'copula_stat_arb' in PAPER_BROKER_OPTIONS_STRATEGIES` → `False` (unchanged, confirms it was
  never miscategorized there either).
- `pytest tests/test_refresh_validations.py -q` → **151 passed**.
- `pytest tests/test_copula_stat_arb.py tests/test_command_manifest_freshness.py
  tests/test_stress_gate.py -q` → **47 passed** (no regressions across the copula module tests,
  the manifest-freshness test that also reads this registry, and the stress-gate tests
  themselves).

All run via the repo's `.venv` interpreter (`/Users/kevinlee/Stockpy-live/.venv/bin/pytest`)
inside an isolated scratch worktree at `/private/tmp/claude-501/fix-copula-stress-gate`, branched
from a fresh fetch of `origin/main` (`36a7d296`).
