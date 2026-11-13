# Forecasting rebuild F6b — task tracker

Plan: `.claude/forecasting_rebuild_f5_f6_implementation_plan.md` §5-6 (parent: `.claude/forecasting_rebuild_implementation_plan.md` F6).
F6a merged as #1144 (`c7d414a1`). Operator approved building this screen on 2026-11-13 (part of the approved rebuild,
allowed under the feature freeze). Branch `forecasting-rebuild-f6b` from `origin/main` @ `c7d414a1`, rebased onto the
current `origin/main` before push.

## This PR: F6b (webapp screen)
- [x] `types.ts`: `ForecastsView`, `ForecastRow`, `ForecastHorizonCell`, `ForecastImpact`, `ForecastGateSettings`,
      `ForecastZSettings`, `ForecastSummary`, `ForecastSummaryHorizon`, `ForecastSkillRow`, `ForecastGateDecision`,
      `ForecastGateEvaluation` (every nullable field `| null`); `ForecastResult` removed
- [x] `client.ts`: `getForecasts`, `getForecastSummary` (explicit `http<T>`); `getForecastResult` (refit) removed
- [x] `mock.ts`: `mockForecastsView` / `mockForecastSummary`, one honesty branch per symbol (see walkthrough)
- [x] `ForecastViewer.tsx` rework: universe table, gate + z lines from the response, `?symbol=` panel, impact card,
      ledger card; dropped the +/-1.5% literal and the refit source
- [x] Links: Symbol Detail "Forecast skill" card, `ModelHealthPanel` (both states)
- [x] Labels: "Forecast Viewer" -> "Forecasts" (nav, ResearchHub, command palette, Marketplace card)
- [x] Help: `TAB_HELP.forecast`, glossary `naive gate` (from `/thresholds`), `skill vs naive`, `forecast z`;
      `shared/help_content.py` GLOSSARY `naive gate` (live settings, anchored); `docs/HOW_TO_GUIDE.md` section
- [x] Tests: `ForecastViewer.test.tsx` (16), `ModelHealthPanel.test.tsx` (2), `SymbolDetail.test.tsx` (+1),
      `helpContent.test.ts` (+2), label updates in `App.test.tsx` / `ResearchHub.test.tsx`
- [x] Browser check (Playwright, 390 and 1280): mock; live backend cold start; live backend on a hand-built fixture snapshot
- [x] Docs: `docs/architecture/webapp-and-gui.md`, `observability-and-apis.md` pointer, `FEATURE_TIER_HISTORY.md`,
      both plan status notes, this tracker + walkthrough
- [x] Verification: typecheck, vitest, build, `make lint`, targeted pytest

## Not in this PR (open)
- [ ] F5-1 / F5-2 / F5-3 (data-blocked until about Feb 2027), and Q2, Q3, Q5, Q7, Q8 plus the Q6/Q9 flip decisions
- [ ] `GET /metrics/forecast/{symbol}` still refits `ForecastingEngine()` (F4 part 2b iii); the webapp no longer calls it
