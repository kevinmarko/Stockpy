# Options-Selling Backtest: Dedup + Redundant-Recompute Fix

Follow-up PR to #734 ("backtest missing options & standalone signal strategies"), closing the
two code-review findings deliberately deferred there as "too large for a minimal-edit pass."

## Scope

1. **Finding A** — `validation/options_selling_backtest.py::simulate_options_strategy_returns()`
   has 6 near-identical per-strategy `if/elif` branches (Put Credit Spread, Call Credit Spread,
   Iron Condor, Call Debit Spread, Put Debit Spread, Covered Call), each repeating the same
   per-day mark-to-market + stop-loss loop skeleton with only the leg construction / `max_risk`
   / stop-loss-threshold formula differing.
2. **Finding B** — `scripts/refresh_validations.py`'s 6 options-selling `STRATEGY_REGISTRY`
   adapters each independently call `simulate_options_strategy_returns()` (or one of its
   wrappers) over the SAME `(ticker, start, end)` window, so a full-registry
   `refresh_validations.py` sweep repeats the same expensive per-cycle GARCH fit / IVR-proxy /
   VRP-proxy / macro-DTO reconstruction / `generate_strategy_pricing_matrix()` call up to 6x.

Both findings live entirely inside `validation/options_selling_backtest.py`; no change to
`scripts/refresh_validations.py` itself is required (its adapters keep calling the same public
functions with the same signatures — the caching is transparent to callers).

## Design

### Finding A — shared per-day MTM/stop-loss helper

**Algebraic unification.** Every one of the 6 strategies' bespoke daily P&L formula reduces to
one expression:

```
cost_to_close   = sum(price for SHORT legs) - sum(price for LONG legs)
stock_pnl       = (spot_t - entry_spot) * 100.0   [only Covered Call; 0 otherwise]
cumulative_pnl  = stock_pnl + (net_premium_raw - cost_to_close) * 100.0
```

Verified per-strategy on paper before writing code:
- **Credit spreads** (Put/Call Credit Spread, Iron Condor): `net_premium_raw` = the real credit
  received (positive). `cost_to_close` is literally each branch's existing
  `mtm_short - mtm_long` (or the Iron Condor's 4-leg sum) — no algebra needed, it's the same
  expression already in the code today.
- **Debit spreads** (Call/Put Debit Spread): the directive's raw `Net_Premium` is negative for a
  debit strategy (`net_debit = abs(net_premium)` is only used for the `max_risk`/validity guard,
  never the P&L math). `position_value = mtm_long - mtm_short = -cost_to_close`, so
  `(position_value - net_debit) == (net_premium_raw - cost_to_close)` — the identical expression,
  substituting the RAW (signed) `net_premium` for `net_debit`.
- **Covered Call**: with a single short-call leg, `cost_to_close = mtm_short`, so
  `stock_pnl + (net_premium - cost_to_close)*100` collapses to the existing
  `stock_pnl + short_pnl` exactly.

This is proved empirically, not just on paper, by a golden-output regression test captured
BEFORE the refactor lands (see Verification below).

**New code:**
- `_OptionLeg` (frozen dataclass): `side: "long"|"short"`, `option_type: "call"|"put"`,
  `strike: float`.
- `_simulate_leg_mtm_pnl(ohlcv, cycle_dates, legs, sigma, net_premium, max_risk,
  stop_loss_threshold, *, entry_spot=None)` — the single shared per-day loop (Black-Scholes
  price each leg via the existing `OptionsPricingRecommender`, apply the unified formula above,
  divide by `max_risk`, apply the stop-loss check against the caller-supplied absolute-dollar
  `stop_loss_threshold`).

Each of the 6 branches in `simulate_options_strategy_returns` keeps ONLY its own leg-count/
premium guard and its own `max_risk`/`stop_loss_threshold` computation (these genuinely differ:
`STOP_LOSS_CREDIT_MULTIPLE * net_premium * 100` vs `STOP_LOSS_DEBIT_RATIO * max_risk` vs
`STOP_LOSS_COVERED_CALL_RATIO * max_risk`), then builds a `List[_OptionLeg]` and delegates to
`_simulate_leg_mtm_pnl`.

### Finding B — process-local cycle-plan cache

Chosen design: **option (a)** from the task brief — a process-local cache inside
`validation/options_selling_backtest.py`, keyed on `(ticker, start, end, content-fingerprint of
closes)`, wrapping the expensive, strategy-INDEPENDENT part of the per-cycle computation.
Rejected option (b) (a new "compute all 6 at once" entry point) because it would require
`scripts/refresh_validations.py` call-site changes across all 6 adapters and duplicate the
existing `Dict[str, pd.Series]` `STRATEGY_REGISTRY` adapter-shape convention for no real benefit
— (a) is fully transparent to every existing caller (adapters, tests, `stress_scenarios.py`'s
`ReturnsFn` contract), which the task explicitly asks to preserve untouched.

**Refactor `simulate_options_strategy_returns`'s outer `while pos < n` loop** (currently walking
the whole window, computing GARCH vol / IVR proxy / VRP proxy / macro DTO / trend bias /
`generate_strategy_pricing_matrix()` per cycle, THEN dispatching per-strategy) into two pieces:

1. `_compute_cycle_plan(ticker, start, end, closes) -> _CyclePlan` — the exact original while-loop
   body, stopping right before the per-strategy `if/elif` dispatch. Returns `_CyclePlan(ohlcv,
   entries: List[_CycleEntry])`, where each `_CycleEntry` is one of:
   - `kind="warmup"` (one calendar day, pre-`WARMUP_TRADING_DAYS`),
   - `kind="flat"` (a cycle where GARCH/IVR/VRP computation failed — NaN sentinel, matches
     CONSTRAINT #4),
   - `kind="priced"` (carries `rec_strategy`, `directive`, `sigma`, `entry_spot` — the real
     computed cycle).
2. `_get_cycle_plan(ticker, start, end, closes)` — dict cache lookup (`_CYCLE_PLAN_CACHE`,
   module-level, process-local, never evicted mid-run — matches the CLI's short-lived-process
   lifetime, exactly the framing the task brief itself endorses for option (a)) wrapping
   `_compute_cycle_plan`.
3. `simulate_options_strategy_returns` becomes: resolve `target_strategy` → fetch/download
   `closes` (unchanged) → `plan = _get_cycle_plan(...)` → iterate `plan.entries`, zero-filling
   `warmup`/`flat`/non-matching-strategy/`"Cash"` entries exactly as today, and for a matching
   `"priced"` entry, run the SAME per-branch guard + `max_risk` computation as before, then call
   `_simulate_leg_mtm_pnl` (Finding A).

**Cache-key correctness** (the task's explicit requirement — "must key correctly even when
`closes` is explicitly passed vs. downloaded"): the key is `(ticker, start-date, end-date,
sha256(closes.index.values.tobytes() + closes.values.tobytes()))`. Content-based, not
identity-based or `(ticker,start,end)`-only — two calls sharing a nominal window but different
underlying price data (e.g. a live yfinance download racing a test's synthetic fixture) can never
collide; the only cost of a false miss is one extra (correct) computation, never a wrong answer.
`_download_spy_closes` inside `simulate_options_strategy_returns` still runs BEFORE the cache
lookup, so the key is always built from the actual resolved `closes` Series regardless of origin.

`_reset_cycle_plan_cache()` — test-only utility, not called by production code, used by the new
regression/instrumentation tests to keep them isolated from each other and from the rest of the
suite.

**Why this doesn't change `stress_scenarios.py`'s calling contract**: `ReturnsFn = Callable[[str,
str], pd.Series]` calls each `simulate_*_returns(start, end)` with `closes=None` (per-scenario
download). Each dated stress window (OCT_2008, FEB_2018, MAR_2020, AUG_2024) has a distinct
`(start, end)`, so distinct cache keys — no behavior change, and repeated stress-window calls
across the now-4 stress-eligible strategies (`vrp_premium_selling`/`iron_condor`,
`put_credit_spread`, `call_credit_spread`, `covered_call`) get the same redundant-recompute fix
as a bonus, not a target of this PR.

## Why NOT touch `scripts/refresh_validations.py`

Every one of the 6 adapters (`_build_put_credit_spread_adapter`, etc., all already routed through
the shared `_build_options_spread_adapter` helper post-#734/588b324b) calls its
`simulate_*_returns` wrapper with `closes=spy_close` — the SAME `pd.Series` object (or an
identical column-slice of the same downloaded `closes_df`) for every strategy within one
`run_validations()` sweep. The cache keys on content, so all 6 hit the same cache entry
regardless of adapter identity. No adapter-level change needed; this is what "transparent"
caching means per the task brief.

## Files touched

- `validation/options_selling_backtest.py` — the only production code change (both findings).
- `tests/test_options_selling_backtest_stress.py` — new byte-identical regression test (Finding
  A) + new cache-hit/no-redundant-recompute instrumentation test (Finding B).
- `docs/architecture/validation-and-signals.md` — new bullet documenting the shared MTM helper
  and process-local cycle-plan cache (see Documentation below).
- `.claude/implementation_plan.md`, `.claude/task.md`, `.claude/walkthrough.md` — this PR's own
  plan artifacts (overwriting PR #734's, per the repo's per-PR-artifact convention).

## Documentation update step (per CLAUDE.md)

Checked: `docs/signals/vrp_premium_selling.md`'s "Backtest Validation" section documents PBO/DSR/
Sharpe/MaxDD numbers and causal levers — this PR changes neither (pure internal refactor, no
behavior change, verified via a byte-identical regression test), so that file needs NO edit, and
`docs/VALIDATION_STRATEGY_FIX_LOG.md` needs no new dated entry either (that log is reserved for
deployability-gate-affecting changes; explicitly checked, N/A here).
`docs/architecture/validation-and-signals.md` currently has no dedicated bullet for
`validation/options_selling_backtest.py` itself (only `validation/harness.py` and
`validation/stress_scenarios.py` do) — adding one, briefly describing the shared MTM helper and
the process-local cycle-plan cache, so a future reader of that architecture doc knows both exist
before they go looking at the 700-line source file. This is the one doc edit this PR makes.

## Verification plan

1. **Golden capture**: before writing any refactor code, run all 6 `simulate_*_returns` wrappers
   against the SAME deterministic synthetic SPY closes (seed=42, `_synthetic_spy` convention
   already used by `tests/test_options_selling_backtest_stress.py`) over a window chosen to
   activate MULTIPLE strategies (not just guard-rejection paths) and save the exact output
   `Dict[str, List[float]]` keyed by strategy name to a JSON fixture.
2. Implement the refactor.
3. **Byte-identical regression test**: re-run the same 6 calls against the SAME synthetic input,
   compare to the captured golden JSON via `np.allclose(..., atol=1e-12, rtol=1e-12)` (plus index
   equality) — added permanently as
   `TestSharedMtmHelperByteIdentical` in `tests/test_options_selling_backtest_stress.py`, with the
   golden values inlined as a fixture (not an external file, so the test is self-contained and
   the fixture regenerated once, not left as a mystery JSON blob in the repo).
4. **No-redundant-recompute test**: monkeypatch `TechnicalOptionsEngine.estimate_gjr_garch_volatility`
   with a call-counting wrapper, run 1 strategy's simulator, record the call count, clear the
   cache, run all 6 strategies' simulators over the identical window, assert the total call count
   equals the single-strategy count (not 6x) — proving the cache actually eliminates the
   redundant computation, not just "should be faster."
5. Run the full mandated verification command list (see `task.md`) and paste PASS/FAIL output,
   not paraphrased, into the PR description / final report.

## Risk / rollback

Pure internal refactor behind existing public function signatures; no settings flag needed (no
behavior change to gate). Rollback = revert the PR; no data migration, no `STRATEGY_REGISTRY`
change, no config change.
