# Task Tracker: Copula Kalman lookahead leak + HRP/CVaR status honesty fix

**Branch:** `fix-copula-kalman-lookahead-hrp-status`

| # | Step | Status |
|---|------|--------|
| 1 | Fix `pilots/copula_stat_arb.py::estimate_kalman_dynamic_hedge_ratio` — replace the fixed, lookahead-tainted `mean_x2 = mean(x[:20])**2` with a causal, expanding-window-capped-at-20 `_causal_mean_x2(t_idx)`; `P0` seeded from `_causal_mean_x2(0)`, `Q` recomputed per-timestep inside the filter loop instead of once, outside it | ✅ Done |
| 2 | New perturbation regression test `tests/test_copula_stat_arb.py::test_kalman_hedge_ratio_mean_x2_causal_no_lookahead` proving the leak is closed — mutates `x[19]` and asserts alpha/beta/spread/spread_std/z_score for all `t < 19` are unaffected, plus a sanity check that `t=19` legitimately diverges (not a vacuous test). Verified this test **fails against the pre-fix code** via `git stash` and passes cleanly against the fix | ✅ Done |
| 3 | `docs/architecture/execution.md` — extend `pilots/copula_stat_arb.py`'s "Lookahead-bias fix history" note with the `mean_x2` finding, fix mechanism, bit-identical-for-`t>=19` correctness claim, practical-impact assessment, and the new test name | ✅ Done |
| 4 | `api/pilots_api.py::post_portfolio_optimize_hrp_cvar` — add `status` and `hrp_fallback` fields to the response dict, sourced from `opt_res` (already computed by `sizing/hrp_cvar_optimizer.py`, previously dropped at serialization) | ✅ Done |
| 5 | Webapp plumbing: `webapp/src/api/types.ts` (`HrpCvarOptimizeResponse.status`/`.hrp_fallback`), `webapp/src/api/mock.ts` (always reports `status: "optimal"`/`hrp_fallback: false` — mock never runs a real SLSQP solve), `HrpPortfolioOptimizerView.tsx` (new warning banner, `data-testid="hrp-fallback-banner"`, distinguishing solver-non-convergence copy from HRP-clustering-degradation copy), `HrpPortfolioOptimizerView.test.tsx` (3 new tests: no banner on clean optimum, banner on `status !== "optimal"`, banner on `hrp_fallback`) | ✅ Done |
| 6 | New API-level regression test `tests/test_pilots_api.py::TestHrpCvarOptimize::test_infeasible_constraints_surface_fallback_status_honestly` — forces a genuinely infeasible sector-cap combination (3 symbols, all one sector, cap 20% < equal-weight floor) through the **real HTTP endpoint** and asserts `status == "fallback"`, `hrp_fallback` is a bool, and weights still sum to ~1.0 (graceful degradation, not a broken response). Also strengthened the existing happy-path test to assert `status == "optimal"` / `hrp_fallback is False` explicitly, not just presence | ✅ Done |
| 7 | `docs/architecture/signal-engines.md` — extend `sizing/hrp_cvar_optimizer.py`'s entry with the "Endpoint status honesty" finding, the fix, and an explicit note that the sibling `optimize_hrp_cvar`/`constrain_cvar` entry point has the same gap but is unreachable from any live caller today and is deliberately left unfixed | ✅ Done |
| 8 | Delete `test_opt.py` — an uncommitted, untracked debug script at the repo root reproducing the same silent-fallback issue against the *other*, out-of-scope `constrain_cvar` entry point; dead code, never collected by pytest (`pytest.ini`'s `testpaths=tests`) | ✅ Done |
| 9 | Full verification sweep, all clean: targeted `pytest tests/test_copula_stat_arb.py tests/test_hrp_cvar_optimizer.py tests/test_pilots_api.py -q` (487 passed); full offline `pytest -m "not network" -q` (clean aside from a small number of confirmed pre-existing failures, verified identical with/without this diff via `git stash`); `npm run --prefix webapp typecheck` (clean, zero errors); `npx vitest run src/components/portfolio/HrpPortfolioOptimizerView.test.tsx` (7 passed) | ✅ Done |
| — | Mechanical regen of `docs/settings_liveness.json` / `docs/settings_field_census.{json,md}` (`scripts/settings_liveness.py --write`, `scripts/measure_settings_census.py --write`) — line-number/commit-hash drift only, no logic change, triggered automatically by the code edits above | ✅ Done |

## PR artifact naming note

Per CLAUDE.md's "PR Artifacts & Unique Naming" rule, this task's three artifacts are task/feature-scoped, not generic:
- `.claude/copula_kalman_hrp_status_fix_implementation_plan.md`
- `.claude/copula_kalman_hrp_status_fix_task.md` (this file)
- `.claude/copula_kalman_hrp_status_fix_walkthrough.md`
