# Walkthrough — Options Backtest Dedup + Redundant-Recompute Fix

Branch: `refactor-options-backtest-shared-mtm-cache`

## What changed

`validation/options_selling_backtest.py` (the sole production file touched) was refactored along
two independent axes, both purely internal (no public signature change, no behavior change):

### 1. Six near-duplicate mark-to-market loops → one shared helper

`simulate_options_strategy_returns()`'s big `if/elif` chain (Put Credit Spread, Call Credit
Spread, Iron Condor, Call Debit Spread, Put Debit Spread, Covered Call) each repeated ~25-40 lines
of per-day Black-Scholes mark-to-market + stop-loss logic that differ only in which legs are
long/short, the `max_risk` formula, and the stop-loss threshold formula.

All six strategies' daily P&L collapse to one algebraic expression:

```
cost_to_close  = sum(short-leg prices) - sum(long-leg prices)
stock_pnl      = (spot_t - entry_spot) * 100        # Covered Call only
pnl            = stock_pnl + (net_premium_raw - cost_to_close) * 100
```

(`net_premium_raw` is the directive's actual signed `Net_Premium` — positive for a credit
received, negative for a debit paid — never the `abs()`'d `net_debit` some branches computed
separately for their `max_risk` guard.)

New `_OptionLeg` dataclass + `_simulate_leg_mtm_pnl()` implement this once. Each of the 6 branches
now does only its own leg-count/premium validity guard and its own `max_risk`/stop-loss-threshold
computation (these three formulas are genuinely strategy-specific and correctly stayed
per-branch), then hands a small `List[_OptionLeg]` to the shared helper.

### 2. Redundant per-cycle recomputation across the 6 STRATEGY_REGISTRY adapters

`scripts/refresh_validations.py`'s 6 options-selling adapters each call
`simulate_options_strategy_returns` (or a named wrapper) independently over the same
`(ticker="SPY", start, end)` window — each one re-walking the ENTIRE window, redoing the same
GJR-GARCH fit / IVR proxy / VRP proxy / real macro DTO reconstruction / one
`generate_strategy_pricing_matrix()` call per cycle, and only keeping the cycle's return if that
adapter's target strategy happens to match what the pricing matrix recommended that cycle.

The outer `while pos < n` loop (everything up to, but not including, the per-strategy dispatch)
is now `_compute_cycle_plan()`, memoized via a process-local, content-keyed cache
(`_get_cycle_plan` / `_CYCLE_PLAN_CACHE`). The cache key is
`(ticker, start_date, end_date, sha256(closes.index + closes.values))` — content-based, so it
can never silently reuse a stale plan for two calls that share a nominal window but different
underlying price data, and correctly handles both a caller-supplied `closes` Series and one this
module downloads itself via `_download_spy_closes` (the fingerprint is computed on the resolved
Series either way).

`scripts/refresh_validations.py` itself needed NO changes — every adapter already calls the same
public `simulate_*_returns` functions with the same signatures; the cache is entirely transparent.

## Why this is safe

- **Golden-fixture regression** (`tests/fixtures/options_selling_backtest_golden.json`, captured
  from the pre-refactor code BEFORE any production edit landed): all 6 strategies match the
  post-refactor output with **max abs diff = 0.0** (not just within tolerance — bit-identical) for
  the fixture's window (4 of 6 strategies produce real nonzero trades in that window: PutCredit,
  CallCredit, PutDebit, CoveredCall).
- **Direct formula equivalence** (`TestSharedMtmHelperDirectFormulaEquivalence`): the golden
  fixture's chosen window happens not to activate Iron Condor or Call Debit Spread (real macro/
  trend/IVR gating), so two additional unit tests hand-construct synthetic legs/prices for exactly
  those two strategies and diff `_simulate_leg_mtm_pnl`'s output against an independently
  reimplemented copy of each strategy's ORIGINAL per-day formula — both pass to `1e-12`.
- **No-redundant-recompute proof** (`TestCyclePlanCacheAvoidsRedundantRecompute`): counts calls to
  `TechnicalOptionsEngine.estimate_gjr_garch_volatility` across a run of all 6 strategy simulators
  over the identical window and asserts the count equals exactly what ONE strategy alone costs
  (not 6x) — PASS. A second test proves the cache key correctly distinguishes two Series sharing a
  nominal window but different price content (2 distinct cache entries, not 1 wrongly-shared
  entry) — PASS. A third proves content-equal-but-distinct-object Series correctly hit the SAME
  cache entry (the `closes=None` vs. explicit-`closes` correctness requirement) — PASS.
- Full existing offline + network test suite for this module and its consumers re-run and green
  (see Verification output below).

## Documentation

- Added a new bullet to `docs/architecture/validation-and-signals.md` (this module previously had
  no dedicated architecture-doc bullet at all — only `validation/harness.py` and
  `validation/stress_scenarios.py` did) describing the shared MTM helper and process-local
  cycle-plan cache.
- Added a short exception note to `tests/fixtures/README.md` for the new
  `options_selling_backtest_golden.json` fixture (that README's stated scope is "hand-authored,
  Pilots-feature" fixtures; this one is machine-captured and unrelated to Pilots).
- No change to `docs/signals/vrp_premium_selling.md`'s Backtest Validation section or
  `docs/VALIDATION_STRATEGY_FIX_LOG.md` — this PR changes neither PBO/DSR/Sharpe/MaxDD for any
  strategy (proven bit-identical via the golden-fixture regression), so neither doc's change
  criteria are triggered.

## Verification output (all commands actually run, not paraphrased)

```
$ pytest tests/test_options_selling_backtest_stress.py -v   (includes network-marked tests)
55 passed in 6.26s

$ pytest tests/test_refresh_validations.py -v
95 passed, 1 warning in 57.62s
  (warning is a pre-existing, unrelated pandas FutureWarning in
   scripts/refresh_validations.py:1539, not touched by this PR)

$ pytest tests/test_stress_gate.py tests/test_technical_options_engine.py \
    tests/test_validation_vrp_premium_selling_registry.py tests/test_vrp_premium_selling.py -v
102 passed, 7 warnings in 7.16s
  (warnings are pre-existing arch/scipy convergence + pandas read_html warnings,
   unrelated to this PR)

$ python -m ruff check . --select=F821,F822,F823,E9
All checks passed!

Combined run of all 6 mandated test files together: 252 passed, 8 warnings in 65.72s
```

New tests added, all passing:
- `TestSharedMtmHelperByteIdentical` (7 tests: 6 parametrized strategies + 1 nonzero-activity guard)
- `TestSharedMtmHelperDirectFormulaEquivalence` (2 tests: Iron Condor, Call Debit Spread)
- `TestCyclePlanCacheAvoidsRedundantRecompute` (3 tests: no-redundant-recompute, cache-key
  content-distinguishing, closes=None-vs-explicit correctness)
