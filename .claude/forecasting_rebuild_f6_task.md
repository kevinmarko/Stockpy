# Forecasting rebuild F6 — task tracker

Plan: `.claude/forecasting_rebuild_f5_f6_implementation_plan.md` §5-6 (parent: `.claude/forecasting_rebuild_implementation_plan.md`).
Operator go-ahead 2026-11-13: "go with your recommendations" (Q1 F6 before F5; F6 "(proposed)" options; Q10 flags OK, default off).
Branch: `forecasting-rebuild-f6` (from `origin/main` @ `d8e53dfc`).

## This PR: F6a (read contract + backend)
- [x] Re-verify the plan's `file:line` citations against `origin/main` (see walkthrough, "Citations re-checked")
- [x] Lane A: `pilots/forecasts.py` (`forecasts_view`, `forecast_summary`), dependency-light allowlist line
- [x] Lane A: `ForecastTracker.latest_gate_decisions` (read-only)
- [x] Lane B: `GET /forecasts`, `GET /symbols/{ticker}/forecast-summary` (read tier), `/thresholds` gate keys
- [x] Lane B: webapp `Thresholds` type + mock + fixtures (parity for the `/thresholds` change only)
- [x] Lane C: `Recommendation.forecast_effects`, `advisory_forecast_effects` in the primary snapshot
- [x] Snapshot: `forecast_{h}`, `forecast_{h}_is_fallback`, `forecast_{h}_gated_naive` (needed; not in the plan)
- [x] Tests: `tests/test_pilots_forecasts.py`, parity, api, advisory, dependency guard
- [x] No-lookahead perturbation test (gate evaluation) + non-vacuous companion
- [x] Decision-neutral proof (AST guard + 180-scenario comparison vs `origin/main`)
- [x] Mutation checks (5)
- [x] Docs: observability-and-apis, testing, orchestration-entrypoints, FEATURE_TIER_HISTORY, plan status notes
- [x] Verification: targeted tests, `make lint`, `make ci`, webapp typecheck + touched vitest files

## Not in this PR (open)
- [ ] F6b: webapp Forecasts screen (types/client/mock for the two routes, `ForecastViewer.tsx` rework, links from
      Symbol Detail / ModelHealthPanel, help text, browser check against mock and a live backend)
- [ ] F5-1 / F5-2 / F5-3 (data-blocked until about Feb 2027; Q2, Q3, Q5, Q7, Q8, and the Q6/Q9 flip decisions)
