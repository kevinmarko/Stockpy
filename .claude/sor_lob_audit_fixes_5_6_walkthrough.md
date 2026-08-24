# Walkthrough: options_sor.py / lob_simulator.py audit fixes #5 and #6

Branch: `fix-sor-lob-fillprob-and-commission-model`

## Summary

Fixes items #5 and #6 from an audit of `pilots/options_sor.py` and
`pilots/lob_simulator.py`. Items #1-#4 were already fixed and merged on
`main` (`8e5a25a3` / #904); see `.claude/sor_lob_audit_fixes_walkthrough.md`
on `main` for that fix's full detail and its "Deferred" section, which is
where #5/#6 were flagged.

## Fix #5 — `lob_simulator.py`: two divergent CST fill-probability formulas

`evaluate_optimal_queue_level()` (the live "optimal placement" recommender
behind `POST /pilots/options/lob/simulate-queue`) computed each candidate
level's `fill_probability` as `p_reach * p_drain`, where:

- `p_drain` called `calculate_cont_stoikov_fill_probability()` — a second,
  un-derived heuristic (`1 - exp(-drain_rate*T/scale)`) — instead of the
  module's own rigorous, cited `compute_cst_fill_probability()` (exact
  Poisson-tail math for `mu_cancel=0`, a disclosed diffusion approximation
  for `mu_cancel>0`).
- `p_reach` used an uncited magic constant:
  `math.exp(-1.5 * cumulative_depth_prior / max(1.0, expected_mkt_vol))`.

**Fix**: both legs now call `compute_cst_fill_probability()`. `p_reach` is
exactly that function's own definition — "probability that market orders
(net of cancellations) fully deplete `cumulative_depth_prior` within the
horizon" — computed as
`compute_cst_fill_probability(queue_ahead=0.0, order_size=cumulative_depth_prior, theta_market=market_order_rate, mu_cancel=cancel_rate, time_horizon_sec=time_horizon_sec)`,
so no separate constant is needed at all. `p_drain` is now
`compute_cst_fill_probability(queue_ahead=depth_ahead, order_size=target_size, theta_market=market_order_rate, mu_cancel=cancel_rate, time_horizon_sec=time_horizon_sec)`
— same call shape the function already used for the heuristic, same
semantics, rigorous formula. The dead `expected_mkt_vol` local (only used
by the old `p_reach` formula) was removed.

`calculate_cont_stoikov_fill_probability()` and
`calculate_expected_fill_latency()` remain in the module, still exported in
`__all__`, still directly unit-tested — only their use *inside*
`evaluate_optimal_queue_level()` was removed. Confirmed by grep: no other
production caller of `calculate_cont_stoikov_fill_probability()` exists.

### A real behavioral consequence, not silently absorbed

Swapping in the correct formula surfaced a genuine property of the CST
model at this module's DEFAULT calibration (`theta=5` orders/sec,
`time_horizon_sec=60.0`, `mu_cancel=0.02`): with ~300 expected market orders
over a full 60-second window, several shallow-to-moderate queue depths
(tens to a few hundred shares/contracts) are judged near-certain to fill
within the horizon. `fill_probability` — after the function's own existing
`max(0.01, min(0.99, ...))` display clamp — can now legitimately tie at
`0.99` across multiple adjacent levels instead of strictly decreasing with
depth, because the raw (pre-clamp) probabilities really are all close to
1.0 for this book/calibration. `expected_fill_latency_sec` (computed
independently of the fill-probability formula; unaffected by this fix)
remains strictly increasing with depth and is the reliable monotonic
metric.

Two pre-existing tests encoded assumptions that only held under the old,
un-derived heuristic's particular (steeper, more arbitrary) decay shape:

- `test_evaluate_optimal_queue_level_basic` asserted
  `c1["fill_probability"] > c2["fill_probability"] > c3["fill_probability"]`
  (strict). Changed to `>=` (non-increasing) with a comment explaining the
  saturation; the strict latency-ordering assertion on the same three
  candidates is untouched and still passes.
- `test_evaluate_optimal_queue_level_urgency_tradeoff` asserted
  `res_agg.recommended_level == 1` for `urgency="aggressive"` against the
  module's default 60s horizon. Under the corrected math, `aggressive`,
  `normal`, and `passive` urgency profiles all recommend the same Level 3 for
  this book (fill is near-certain at all three, so the extra spread capture
  wins for all of them); only `immediate` (the strongest
  `decay_multiplier`) recommends a shallower Level 2. Rewrote the test to
  assert the formula-agnostic invariant that actually matters and remains
  true regardless of any future recalibration: recommended-level latency is
  monotonically non-decreasing as urgency intensity relaxes
  (`immediate <= aggressive <= normal <= passive`). Verified this ordering
  holds for the fixture's real numbers (`20.48 <= 49.0 <= 49.0 <= 49.0`).

This is a legitimate, disclosed behavior change from using the correct
math — not a workaround to make tests pass. No change was made to
`DEFAULT_MARKET_ORDER_RATE`/`DEFAULT_TIME_HORIZON_SEC`/`DEFAULT_CANCEL_RATE`
or to the urgency-weighting mechanism itself; only the fill-probability
formula being called was corrected, per the audit's explicit ask.

### New regression tests (`tests/test_lob_simulator.py`)

- `test_evaluate_optimal_queue_level_wires_in_rigorous_cst_formula` —
  hand-recomputes Level 2's `fill_probability` directly from
  `compute_cst_fill_probability()` and asserts it matches what
  `evaluate_optimal_queue_level()` actually returns, proving the wiring is
  real (not just documented in a comment).
- `test_cst_heuristic_and_rigorous_formula_diverge_materially` — pins a
  concrete case (`queue_ahead=50, order_size=5, theta=1.0, mu=0.0, T=10.0`)
  where `compute_cst_fill_probability` returns `~0.0` and
  `calculate_cont_stoikov_fill_probability` returns `>0.15` — reproduces the
  audit's own divergence claim and would catch a regression back to calling
  the heuristic inside `evaluate_optimal_queue_level()`.

## Fix #6 — `options_sor.py`: no commission/fee model anywhere

Confirmed via grep before the fix: zero references to
`commission`/`TieredCostModel`/`cost_model` anywhere in the file.
`policies_comparison` compared `COB_NET_PACKAGE` (one atomic fill) against
`LEG_PASSIVE_FIRST`/`SPLIT_DIRECT` (more separate fills, real hung-leg
unwind risk) entirely gross of cost.

**Fix**: `analyze_routing_options()` now adds a `"commission_cost_dollars"`
field to each of the 3 `policies_comparison` entries, sourced from
`execution.cost_model.TieredCostModel.options_per_contract` (this repo's
single source of truth for trading costs — the same `$0.65`/contract/leg
default already used by `execution/options_paper_executor.py`,
`execution/fmp_paper_broker.py`, and `data/paper_account_store.py`'s
own hardcoded literals) via a lazy, function-local import:

```python
try:
    from execution.cost_model import TieredCostModel
    per_contract_fee = float(TieredCostModel().options_per_contract)
except Exception:
    per_contract_fee = 0.65  # TieredCostModel's own default; only reached if the import itself is broken
```

All three policies get the SAME base per-contract-per-leg commission — OCC/
exchange options fees are levied per contract per leg by regulation, not
per order ticket submitted, so `COB_NET_PACKAGE`'s single atomic order costs
the same base fee as `SPLIT_DIRECT`'s multiple direct orders for the same
total legs filled. The DIFFERENTIAL cost between policies is the *expected
extra round-trip commission* from unwinding a naked leg left behind by a
hung passive-first/split fill — the same real, additional risk
`simulate_legging_execution()` already prices on the spread side via its
own `naked_unwind_cost` treatment, applied here to the commission side of
that identical risk:

```python
base_commission = per_contract_fee * order_size * legs_count

def _commission_with_unwind_risk(hung_leg_risk: float) -> float:
    return base_commission + (max(0.0, float(hung_leg_risk)) * per_contract_fee * order_size)
```

- `COB_NET_PACKAGE`: `_commission_with_unwind_risk(0.0)` — zero hung-leg
  risk, so just the base commission.
- `LEG_PASSIVE_FIRST`: `_commission_with_unwind_risk(synthetic_legging["hung_leg_probability"])`.
- `SPLIT_DIRECT`: `_commission_with_unwind_risk(min(0.50, hung_prob * 1.3))`
  — reuses the SAME hung-leg-risk proxy this function already computes for
  that policy's existing `"hung_leg_risk"` field; no new hazard model was
  invented.

### Scope boundary (deliberate, not a partial job)

This is scoped as an **informational cost-completeness fix**: it makes the
commission differential visible/comparable in `policies_comparison`,
reusing the single source of truth. It deliberately does **not** rewire
`recommended_policy`'s selection thresholds (`is_edge_positive`,
`is_hazard_acceptable`, etc.) to subtract commission from `net_edge` — doing
that honestly would require converting a total-dollar commission (scaled by
`order_size`) into the function's existing $/contract edge units and
re-validating the recommendation logic end-to-end against real inputs,
which is a materially larger and riskier change than the audit's "add a
cost model" ask. Stating this explicitly rather than silently leaving it
half-done: `commission_cost_dollars` is currently pure information for the
caller, not yet a factor in the routing recommendation itself. A follow-up
task could fold it into the decision if desired.

### AST-safety allowlist update

`tests/test_pilots_strategy_matrix.py::test_pilots_read_helpers_stay_dependency_light`
auto-discovers every `pilots/*.py` file and checks its import roots (walked
via `ast.walk`, so it also catches lazy/function-local imports) against a
per-module allowlist. `options_sor`'s entry gained `"execution"`, mirroring
`pilots/mirror.py`'s and `pilots/earnings_crush.py`'s own `execution`
allowance for their lazy `execution.*` imports — `execution/cost_model.py`
itself is confirmed dependency-light (stdlib `logging`/`typing` + `numpy`,
with `backtrader` already optional/try-except-guarded inside that module).

### New regression tests (`tests/test_options_sor.py`)

- `test_policies_comparison_has_commission_cost_dollars` — every policy
  entry carries the new field, positive.
- `test_cob_net_package_commission_is_base_only` — `COB_NET_PACKAGE`'s
  commission equals exactly `TieredCostModel().options_per_contract * order_size * legs_count`.
- `test_leg_passive_first_commission_exceeds_cob_when_hung_leg_risk_positive`
  — `LEG_PASSIVE_FIRST`/`SPLIT_DIRECT` commission strictly exceeds
  `COB_NET_PACKAGE`'s whenever `hung_leg_probability > 0` — the differential
  the audit asked for.
- `test_commission_cost_reuses_tiered_cost_model_not_a_hardcoded_literal` —
  monkeypatches `TieredCostModel.options_per_contract` to a different value
  and confirms the computed commission changes proportionally, proving
  genuine reuse of the single source of truth rather than an independently
  hardcoded literal that happens to currently agree with it.

## Test results

```
python3 -m pytest tests/test_lob_simulator.py tests/test_options_sor.py tests/test_pilots_strategy_matrix.py -q
143 passed
```

(38 pre-existing in `test_lob_simulator.py` + 2 new = 40; 16 pre-existing in
`test_options_sor.py` + 4 new = 20; 83 in `test_pilots_strategy_matrix.py`
unchanged in count, one entry's allowlist widened.)

Also manually re-verified (scratch REPL, not committed) that
`evaluate_optimal_queue_level(...)`'s returned `candidates[2]["fill_probability"]`
for a 3-level book matches an independent recomputation via
`compute_cst_fill_probability(...)` with the same inputs — confirms the
formula swap is really wired in, not just present in a docstring/comment.
