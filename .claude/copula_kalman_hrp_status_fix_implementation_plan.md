# Implementation Plan: Copula Kalman lookahead leak + HRP/CVaR status honesty fix

**Branch:** `fix-copula-kalman-lookahead-hrp-status`
**Scope tier:** "Everything else" (touches a signal-adjacent pilots module and a live API response contract) — plan-before-build per CLAUDE.md's Agent Workflow section.

## Background

A math audit of `pilots/` quant modules surfaced two independent findings, unrelated except that both concern honesty of a computed value:

1. A genuine lookahead leak in the Kalman dynamic hedge-ratio estimator used by the copula stat-arb engine.
2. A silently-dropped API response field that already existed in the underlying optimizer but never reached the wire, making a non-convergent solve indistinguishable from a clean optimum.

Both are narrow, mechanical fixes with a clear causal boundary — no strategy re-registration, no `SIGNAL_WEIGHTS` change, no sizing-pipeline change.

## Finding 1 — Kalman `mean_x2` lookahead leak (`pilots/copula_stat_arb.py`)

**Root cause:** `estimate_kalman_dynamic_hedge_ratio` scales the Kalman filter's prior covariance `P0` and process noise `Q` by `mean_x2`, computed once as `max(1.0, mean(x[:20])**2)` from a **fixed slice of the whole input array**, then applied identically to the state update at *every* timestep `t`, including `t < 19`. For those early timesteps, `x[:20]` contains observations `x[t+1 .. 19]` — strictly future relative to the decision being made at `t`. This directly contradicts the module's own docstring claim of "100% lookahead-free online updating."

**Fix:** Replace the single fixed `mean_x2` constant with a causal helper `_causal_mean_x2(t_idx)` computing `mean(x[0 .. min(t_idx, 19)])**2` — an expanding window capped at 20 observations. `P0` is now seeded from `_causal_mean_x2(0)` and `Q` is recomputed from `_causal_mean_x2(t)` inside the per-timestep filter loop (previously `Q` was computed once, outside the loop, from the same lookahead-tainted constant).

**Correctness proof obligation:** the fix must be bit-identical to the old behavior for `t >= 19` (`min(t+1, 20) == 20` for all `t >= 19`, same slice `x[:20]`) — this is the common production case (analysis windows longer than ~20 bars), so no behavior change is expected there. For `t < 19` the new helper uses strictly fewer, strictly causal observations.

**Verification plan:** a perturbation regression test that mutates a single future observation (`x[19]`, chosen because it is the boundary observation only reachable by the old code's lookahead, not the new causal one for any `t < 19`) and asserts the Kalman state/spread/z-score for every `t < 19` is unaffected — plus a sanity assertion that the perturbation does legitimately change `t=19` onward, so the test isn't vacuous. Confirm the test actually fails against the pre-fix code (via `git stash`) before trusting it as a real regression guard.

**Practical impact assessment:** bounded to backtest-metric quality in the first ~5-15 signal-eligible bars per analysis window (`generate_copula_stat_arb_signals` only starts emitting entries at `t >= 15`); a live "today" decision on a window longer than ~20 bars was already numerically unaffected by the leak. Document this honestly in `docs/architecture/execution.md` rather than overstating severity.

## Finding 2 — HRP/CVaR `status`/`hrp_fallback` dropped by the API handler (`api/pilots_api.py`)

**Root cause:** `sizing/hrp_cvar_optimizer.py::optimize_turnover_regularized_hrp_cvar` already computes and returns two honesty fields in its result dict:
- `status`: `"optimal"` if SLSQP actually converged, `"fallback"` if it fell back to the clipped/normalized initial HRP guess (e.g. on an infeasible sector-cap or beta-range combination).
- `hrp_fallback`: whether HRP quasi-diagonalization itself (a separate, earlier step) degraded to equal-weight.

`api/pilots_api.py::post_portfolio_optimize_hrp_cvar` builds its JSON response by hand-picking fields off `opt_res` and never included either one — so a genuinely non-convergent solve was indistinguishable, over the wire, from a clean optimum. This is a CONSTRAINT #4/#6-adjacent honesty gap: the computation was already correct and fail-aware at the sizing-module layer, but the dishonesty was introduced one layer up, at serialization.

**Fix, end to end:**
1. `api/pilots_api.py`: add `"status": opt_res["status"]` and `"hrp_fallback": bool(opt_res.get("hrp_fallback", False))` to the response dict.
2. `webapp/src/api/types.ts`: add `status: "optimal" | "fallback"` and `hrp_fallback?: boolean` to `HrpCvarOptimizeResponse`.
3. `webapp/src/api/mock.ts`: the mock endpoint always reports `status: "optimal"`/`hrp_fallback: false` for type/contract parity — it does not run a real SLSQP solve and so can never genuinely fail to converge; a "fallback" fixture is supplied ad hoc in the component test instead of faked in the shared mock.
4. `webapp/src/components/portfolio/HrpPortfolioOptimizerView.tsx`: render a visible warning banner whenever `status !== "optimal"` or `hrp_fallback` is true, distinguishing the two failure modes in the copy (solver non-convergence vs. HRP clustering degradation) and telling the operator to relax constraints and re-run before acting on the allocation.
5. Tests at every layer: an API-level regression test that forces a *genuinely infeasible* sector-cap combination (all symbols in one sector, cap far below 100%) through the real HTTP endpoint and asserts `status == "fallback"`; a happy-path assertion that `status == "optimal"`/`hrp_fallback is False` is also asserted (not just present) on the existing passing test; three new component tests (no banner on a clean optimum, banner text for `status != "optimal"`, banner text for `hrp_fallback`).

**Explicitly out of scope:** `sizing/hrp_cvar_optimizer.py` has a second, separate entry point (`optimize_hrp_cvar`/`constrain_cvar`) with the identical silent-fallback gap. It is not reachable from any live API caller today (confirmed by grep — no `pilots_api.py` route calls it), so fixing its serialization would be speculative work with no observable effect. Left unfixed, noted as a deferred follow-up in `docs/architecture/signal-engines.md`'s `sizing/hrp_cvar_optimizer.py` entry, rather than silently left undocumented.

## Documentation-update step (scoped into this plan per CLAUDE.md)

- `docs/architecture/execution.md` — extend `pilots/copula_stat_arb.py`'s existing "Lookahead-bias fix history" note with the `mean_x2` finding, the fix mechanism, the bit-identical-for-t>=19 correctness claim, the practical-impact assessment, and the regression test name.
- `docs/architecture/signal-engines.md` — extend `sizing/hrp_cvar_optimizer.py`'s entry with the "Endpoint status honesty" finding, the fix, and the explicit note that the sibling `optimize_hrp_cvar`/`constrain_cvar` entry point is a known, deliberately-deferred gap.
- No `docs/signals/<name>.md` touch needed — neither module is a registered `SignalModule`, and neither is a `STRATEGY_REGISTRY` entry, so the `docs/VALIDATION_STRATEGY_FIX_LOG.md` deployability-gate documentation convention does not apply here.

## Verification plan (must pass before considering this done)

1. Targeted: `pytest tests/test_copula_stat_arb.py tests/test_hrp_cvar_optimizer.py tests/test_pilots_api.py -q`.
2. Confirm the new Kalman regression test fails against the pre-fix code (`git stash` the fix, re-run, confirm failure; unstash).
3. Full offline sweep: `pytest -m "not network" -q`, and diff any failures against a pre-fix baseline (via `git stash`) to confirm they are pre-existing and unrelated, not newly introduced.
4. `npm run --prefix webapp typecheck` clean.
5. `npx vitest run src/components/portfolio/HrpPortfolioOptimizerView.test.tsx` (or the full webapp suite) green.
6. Delete `test_opt.py` (an uncommitted, untracked debug script at the repo root reproducing the same `constrain_cvar` silent-fallback issue this PR fixes elsewhere for the primary entry point — dead code, not collected by pytest per `pytest.ini`'s `testpaths=tests`) as light, directly-related cleanup.
7. Regenerate `docs/settings_liveness.json` / `docs/settings_field_census.{json,md}` (`python3 scripts/settings_liveness.py --write`, `python3 scripts/measure_settings_census.py --write`) since the code edits shift line numbers those audit artifacts track — mechanical, not a logic change.
