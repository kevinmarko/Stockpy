# Walkthrough: Copula Kalman lookahead leak + HRP/CVaR status honesty fix

**Branch:** `fix-copula-kalman-lookahead-hrp-status`

Two independent, narrow fixes from a math audit of `pilots/` quant modules. Neither touches `STRATEGY_REGISTRY`, `SIGNAL_WEIGHTS`, or the sizing pipeline — read them as two separate diffs stitched into one small PR.

---

## 1. `pilots/copula_stat_arb.py` — causal `mean_x2` in the Kalman hedge-ratio filter

**What it was:** `estimate_kalman_dynamic_hedge_ratio`'s prior covariance `P0` and process noise `Q` were both scaled by

```python
mean_x2 = max(1.0, float(np.mean(x[:20]) ** 2))
```

computed **once**, from a fixed slice of the *whole* input array, then applied identically at every timestep of the filter loop — including `t < 19`. For those early timesteps, `x[:20]` bakes in observations `x[t+1 .. 19]`, which are strictly in the future relative to the decision being made at `t`. This directly contradicts the module's own docstring claim of "100% lookahead-free online updating."

**What it is now:** a new local helper, `_causal_mean_x2(t_idx)`, returns `max(1.0, mean(x[0 .. min(t_idx, 19)])**2)` — an expanding window capped at 20 observations, using only data available up to and including `t_idx`. `P0` is now seeded from `_causal_mean_x2(0)`; `Q` — previously computed once outside the per-timestep loop — is now recomputed from `_causal_mean_x2(t)` *inside* the loop, once per timestep.

**Why this is safe / bounded:** for `t >= 19`, `min(t+1, 20) == 20`, so `_causal_mean_x2(t)` reduces to the exact same `x[:20]` slice the old code used — bit-identical output for every window longer than ~20 bars, which is the common production case. The leak was only live for `t < 19`, and `generate_copula_stat_arb_signals` doesn't start emitting trade entries until `t >= 15` — so the practical exposure was backtest-metric quality in the first ~5-15 signal-eligible bars per analysis window, not a live "today" decision on any window of realistic length.

**Test added:** `tests/test_copula_stat_arb.py::test_kalman_hedge_ratio_mean_x2_causal_no_lookahead` — a perturbation test that mutates `x[19]` (a future observation relative to any `t < 19` decision) by a large amount and asserts `alpha`/`beta`/`spread`/`spread_std`/`z_score` for every `t < 19` are numerically unchanged (`rtol=atol=1e-10`), plus a sanity assertion that `beta[19]` itself *does* legitimately diverge between the base and perturbed runs — proving the test isn't vacuously passing on an inert perturbation. Verified this assertion **fails against the pre-fix code** by stashing the fix and re-running the test.

---

## 2. `api/pilots_api.py` — surfacing `status`/`hrp_fallback` on `POST /pilots/portfolio/optimize/hrp-cvar`

**What it was:** `sizing/hrp_cvar_optimizer.py::optimize_turnover_regularized_hrp_cvar` already computes two honesty fields in its result dict — `status` (`"optimal"` if SLSQP actually converged, `"fallback"` if it degraded to the clipped/normalized initial HRP guess) and `hrp_fallback` (whether the earlier HRP quasi-diagonalization step itself degraded to equal-weight). `post_portfolio_optimize_hrp_cvar` hand-picked fields off that result dict for its JSON response and dropped both — so a genuinely non-convergent solve (e.g. an infeasible sector-cap/beta-range combination) was indistinguishable, over the wire, from a clean optimum. The computation was already correct and fail-aware at the sizing-module layer; the dishonesty was introduced one layer up, at serialization.

**Fix:**
```python
"status": opt_res["status"],
"hrp_fallback": bool(opt_res.get("hrp_fallback", False)),
```
added to the response dict, 8 lines total including the explanatory comment.

**Scope note:** `sizing/hrp_cvar_optimizer.py` has a second, separate entry point (`optimize_hrp_cvar`/`constrain_cvar`) with the identical silent-fallback gap. It has no live API caller today, so there is nothing to fix at the serialization layer yet — left unfixed and documented as a deferred follow-up rather than silently ignored.

---

## 3. Tests — production-code coverage first

- `tests/test_pilots_api.py::TestHrpCvarOptimize`: the existing happy-path test now explicitly asserts `data["status"] == "optimal"` and `data["hrp_fallback"] is False` (previously it asserted neither field existed at all, since neither was returned). A new test, `test_infeasible_constraints_surface_fallback_status_honestly`, forces a genuinely infeasible sector-cap combination (3 symbols all mapped to one sector, sector cap 20%, which cannot sum to 100%) through the **real HTTP endpoint** — not just the sizing module directly — and asserts `status == "fallback"`, `hrp_fallback` is a bool, and the returned weights still sum to ~1.0 (graceful degradation, not a broken response). This is the meaningful assertion: the sizing-module-level test (`tests/test_hrp_cvar_optimizer.py::test_graceful_degradation_infeasible`) already existed and already passed — what was missing was proof the API layer didn't throw the honest signal away.
- `tests/test_copula_stat_arb.py`: see the Kalman section above.

---

## 4. Webapp — types, mock, component, tests

- `webapp/src/api/types.ts`: `HrpCvarOptimizeResponse` gains `status: "optimal" | "fallback"` (required) and `hrp_fallback?: boolean` (optional, matches the API's own `.get(..., False)` defensiveness), each with a doc comment pointing back at the audit finding and the sizing-module source of truth.
- `webapp/src/api/mock.ts`: the mock handler always returns `status: "optimal"` / `hrp_fallback: false` — it never runs a real SLSQP solve, so it cannot genuinely fail to converge. A comment makes explicit that this is for type/contract parity only, and that the fallback-banner UI behavior is exercised via an ad hoc fixture in the component test, not the shared mock.
- `webapp/src/components/portfolio/HrpPortfolioOptimizerView.tsx`: a new `role="alert"` warning banner (`data-testid="hrp-fallback-banner"`) renders whenever `data.status !== "optimal"` or `data.hrp_fallback` is true, placed above the existing KPI cards so a degraded result can't be silently read as a clean optimum. The copy distinguishes the two failure modes (solver non-convergence vs. HRP clustering degradation) since they mean different things to an operator, and tells them to relax constraints and re-run before acting on the allocation.
- `webapp/src/components/portfolio/HrpPortfolioOptimizerView.test.tsx`: the shared `mockHrpResponse` fixture gains `status: "optimal"`/`hrp_fallback: false` (keeping every pre-existing test's fixture honest under the new required field). Three new tests: no banner on a genuinely optimal result, banner + correct copy for `status: "fallback"`, banner + correct copy for `hrp_fallback: true`.

---

## 5. Docs

- `docs/architecture/execution.md` — `pilots/copula_stat_arb.py`'s entry already carried a "Lookahead-bias fix history" note from a prior PR; extended it in place with the `mean_x2` finding, the causal fix mechanism, the bit-identical-for-`t>=19` correctness claim, the bounded practical-impact assessment, and the new test name.
- `docs/architecture/signal-engines.md` — `sizing/hrp_cvar_optimizer.py`'s entry gains an "Endpoint status honesty" note covering the finding, the fix, the regression test, and the explicit, undisguised statement that the sibling `optimize_hrp_cvar`/`constrain_cvar` entry point has the same gap but is unreachable from any live caller today and was deliberately left unfixed.
- `docs/settings_liveness.json` / `docs/settings_field_census.{json,md}` shifted by a handful of lines and a commit-hash stamp — this is the mechanical output of `python3 scripts/settings_liveness.py --write` / `python3 scripts/measure_settings_census.py --write` re-deriving line numbers after the `pilots/copula_stat_arb.py` edit moved code around. No logic change; not reviewed line-by-line, just regenerated.

---

## 6. Cleanup

- Deleted `test_opt.py` from the repo root — an uncommitted, untracked debug script that manually reproduced the *other* (out-of-scope) `constrain_cvar` entry point's identical silent-fallback issue via a standalone `print(result)` script. It was dead code: not collected by pytest (`pytest.ini`'s `testpaths=tests` excludes the repo root), and superseded by the real regression test added in `tests/test_pilots_api.py` above. Directly related to this PR's subject matter, so bundled in rather than left as stray root-level clutter.

---

## Verification performed

- **Targeted:** `pytest tests/test_copula_stat_arb.py tests/test_hrp_cvar_optimizer.py tests/test_pilots_api.py -q` — **487 passed**, 0 failures.
- **Kalman regression test isolation check:** confirmed `test_kalman_hedge_ratio_mean_x2_causal_no_lookahead` fails against the pre-fix code (`git stash` the production diff, re-run, observe failure; `git stash pop`).
- **Full offline sweep:** `pytest -m "not network" -q` — clean aside from a small, pre-existing set of failures unrelated to this change: 3 tests in `tests/test_data_api_chat.py::TestMultiProviderRouting` (`test_openai_routing_invokes_openai_client`, `test_local_routing_uses_configured_base_url`, `test_local_routing_ignores_client_supplied_base_url`) and 2 in `tests/test_gemini_live_chat.py::TestLiveChatSession` (`test_full_bidirectional_flow`, `test_live_tool_call_execution`). Verified via `git stash` that these fail identically with and without this diff applied — confirmed pre-existing, not introduced by this change.
- **Webapp typecheck:** `npm run --prefix webapp typecheck` — clean, zero errors.
- **Webapp component tests:** `npx vitest run src/components/portfolio/HrpPortfolioOptimizerView.test.tsx` — **7 passed** (4 pre-existing + 3 new).
