# Walkthrough: options_sor.py / lob_simulator.py audit fixes

Branch: `fix-sor-lob-simulator-audit-findings`

## Summary

Fixes items #1, #2, #3, #4 from an audit of `pilots/options_sor.py` and
`pilots/lob_simulator.py`. Items #5/#6 deferred to a follow-up (flagged via
`spawn_task`, not silently dropped).

## Fix #1 — `options_sor.py`: unsigned delta silently floored PUT hung-leg probability

`analyze_routing_options()`'s hung-leg-hazard formula (line ~558, pre-fix)
used the active leg's raw signed Black-Scholes delta as a stochastic scale
parameter:

```python
sigma_opt = max(0.001, active_leg["delta"] * spot_val * (volatility or 0.25) * math.sqrt(tau_years))
```

For a PUT active leg, `delta` is negative, so this expression goes negative
and gets floored to `0.001` by the `max()` — collapsing `hung_leg_probability`
to its `0.02` clip floor regardless of real hazard. An economically identical
CALL active leg (same `|delta|`) computed the correct, much higher value.
Confirmed the correct pattern already existed two lines earlier for
`unhedged_delta` (`abs(passive_sign * ...)`) — this was an isolated oversight,
not a deliberate asymmetry.

**Fix**: wrapped `active_leg["delta"]` in `abs()`, matching the existing
pattern.

**Verification**: new test
`test_hung_leg_probability_symmetric_for_put_and_call_active_leg` builds two
economically-symmetric 2-leg spreads (`|delta|=0.80`, identical spreads/vol/
tau, differing only in active-leg sign/type) and asserts
`hung_leg_probability` matches within `1e-4` and neither is pinned at the
0.02 floor. Confirmed the test FAILS against the pre-fix code
(`0.02 != 0.8084 ± 1e-4`) by temporarily reverting the fix in isolation and
re-running just this test, then restored the fix and confirmed it passes.

Impact: this feeds the live `POST /pilots/options/sor/analyze` endpoint's
`is_hazard_acceptable = hung_prob < 0.35` policy gate and
`SmartOrderRouterView.tsx`'s displayed hazard — for any 2-leg options
strategy whose active (tighter-spread) leg is a put (bear put spreads, many
straddles/strangles, put-side iron condor legs), the router previously
systematically understated legging hazard and over-recommended
`LEG_PASSIVE_FIRST`.

## Fix #2 + #3 — `lob_simulator.py`: `mu_cancel` unit/scale bugs

Two bugs in the same formula (`compute_lob_arrival_rates()`'s per-share
cancellation-rate calculation, $\mu = N_{\text{cancel}} / (T \cdot \bar{Q})$):

- **#3 (primary branch, depth observed)**: divided by cancel event *count*
  instead of canceled *volume* (`total_sizes["CANCEL"]`) — the formula's own
  stated derivation requires canceled shares, not event count. Mis-scales
  $\mu$ by the average cancel size whenever it isn't exactly 1 share.
- **#2 (fallback branch, no depth observed)**: returned a raw events/sec rate.
  Every downstream caller (`simulate_queue_position`,
  `compute_cst_fill_probability`) multiplies `mu_cancel` by a queue depth
  measured in shares, expecting units of events/(sec·share) — an events/sec
  value is dimensionally wrong and, empirically, catastrophic: flips
  `fill_probability` from ~0.03% to 100% and collapses expected wait time
  ~59s → ~12.5s the moment `queue_ahead` is nontrivial.

**Fix**: primary branch now divides by `total_sizes["CANCEL"]`; fallback
branch normalizes by the average canceled-order size as a best-effort
per-share proxy (still an approximation absent real depth data, but no
longer orders of magnitude too large).

**Verification**:
- `test_compute_lob_arrival_rates_mu_cancel_uses_canceled_shares_not_event_count`
  — 5 cancels of 10 shares each; asserts `mu_cancel == 0.25` (post-fix) vs.
  `0.025` (pre-fix, 10x too small).
- `test_compute_lob_arrival_rates_mu_cancel_fallback_is_unit_consistent_with_downstream_use`
  — reproduces this issue's own repro: feeds both the pre-fix raw rate and
  the post-fix normalized rate through the real `simulate_queue_position`
  with `queue_ahead=100`, asserts the pre-fix rate reproduces the
  catastrophic ~100% fill probability while the post-fix rate does not.
- Both confirmed to FAIL against the pre-fix code (temporarily reverted the
  fix in isolation, re-ran just these two tests, confirmed failure with the
  exact predicted numbers) and PASS against the fix.
- Confirmed the existing `test_compute_lob_arrival_rates_synthetic_exact`
  (asserts `mu_cancel == 0.20`) is unaffected: its 20 CANCEL records all
  carry `size=1.0`, so `total_sizes["CANCEL"] == counts["CANCEL"] == 20` —
  Fix #3 is a no-op for that test's specific data.

## Fix #4 — documentation correction, not live-calibration wiring

The module docstring claimed "True Poisson arrival rates derived from input
order flow records; zero fabricated fills" as a blanket honesty guarantee.
That's accurate for `compute_lob_arrival_rates()` as a function, but
misleading about the live path: grepped the whole repo and confirmed **zero
production callers** of `compute_lob_arrival_rates()`. The live endpoint
(`simulate_queue_fill` → `POST /pilots/options/lob/simulate-queue`) runs on
fixed Pydantic-default constants (`lambda_limit=4.0`, `mu_cancel=0.05`,
`settings.OPTIONS_LOB_DEFAULT_MARKET_ORDER_RATE=5.0`), and confirmed by
direct read of `LobDepthView.tsx` that the frontend never sends override
values either.

**Scope decision — did not attempt full live wiring**: investigated whether
a real order-flow data source exists to calibrate from. Confirmed it does
not: no L2/L3 order-book or bid/ask-size data exists anywhere in this
codebase's data layer (grepped `data/market_data.py`/`data/fmp_client.py`),
and this is a previously-settled finding — CLAUDE.md's
`execution/dynamic_circuit_breaker.py` Phase 32 section already documents
"no configured market-data provider... populates bid/ask size anywhere in
this codebase's `Quote` type... confirmed by direct research, not an
oversight." Unlike VPIN's bar-level BVC approximation (a defensible
academic formula computable from real OHLCV bars alone), there is no
equivalent formula for deriving per-order LIMIT/CANCEL/MARKET event *rates*
from bar data — attempting one would risk creating a new fabrication bug
rather than fixing the existing docstring-accuracy one. This matches the
audit's own offered fallback for this exact scenario.

**Fix**: corrected the module docstring's CONSTRAINT #4 bullet,
`docs/architecture/execution.md`'s `lob_simulator.py` entry, and added
`docs/known_issues/lob_simulator_uncalibrated_live_arrival_rates.md` (indexed
in that directory's `README.md`) documenting the gap, why it wasn't
attempted, and what would need to change (a real order-flow/tick data
source) before it can be honestly fixed. No regression test for this fix
(documentation-only, no behavior change) — noted explicitly rather than
silently omitted.

## Deferred — items #5 and #6 (flagged via `spawn_task`, not silently dropped)

- **#5**: `evaluate_optimal_queue_level` (the live "optimal placement"
  recommender) calls the less-rigorous `calculate_cont_stoikov_fill_probability`
  heuristic instead of the exact `compute_cst_fill_probability` — the two
  diverge materially (one example: exact=0.0000 vs heuristic=0.1361) and
  nothing currently enforces they stay close. `p_reach`'s magic constant
  `1.5` is also uncited/undived.
- **#6**: `options_sor.py` has no commission/fee model anywhere — grepped,
  confirmed no `commission`/`TieredCostModel`/`cost_model` reference in the
  file. `policies_comparison` compares `SPLIT_DIRECT`/`LEG_PASSIVE_FIRST`
  (more separate fills) against `COB_NET_PACKAGE` (one atomic fill) gross of
  cost, never reusing `execution/cost_model.py::TieredCostModel` (this
  codebase's single source of truth for trading costs).

## Test results

```
python3 -m pytest tests/test_options_sor.py tests/test_lob_simulator.py -q
54 passed
```

(52 pre-existing + 2 new in `test_lob_simulator.py`; `test_options_sor.py`
went from 16 to 17 tests.)
