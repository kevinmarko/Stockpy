# Today's Radar — Task Tracker

- [x] Setup: fetch origin, branch `radar-signal-feed` off origin/main
- [x] Write initial plan/task docs (draft, pre-verification)
- [x] §3 verification: find real DailySignals-equivalent, confirm schema, confirm ranking scalar, confirm credibility shape, confirm feasibility
  - DailySignals IS real but structurally permanently empty (no writer anywhere, documented in docs/known_issues/daily_signals_missing_table.md) — cannot be Radar's source.
  - Real source: output/state_snapshot.json's `signals[]` list, confirmed live in this sandbox (29 tickers, 30 signal entries incl. SPY benchmark row).
  - Multifactor_Composite confirmed as the ranking scalar (config.py L167-171, signals/multifactor.py).
  - credibility.py confirmed per-document, not per-symbol -- excluded from v1.
- [x] Update plan doc in place with verified findings (correct anything wrong in the draft above)
- [x] Phase 1 backend: `GET /signals/radar` via `pilots-endpoint` skill guidance (fail-open read tier, matching `/symbols/{ticker}`)
- [x] Phase 1 backend: `pilots/radar_ranking.py` pure ranking helper (zero-override dependency-light allowlist pass)
- [x] Phase 2 webapp: types → client+mock → screen/panel → route → test (per `new-pwa-screen` skill order) — no new route needed, panel embedded in Marketplace.tsx
- [x] Phase 2 webapp: TabGuide / helpContent.ts entry ("today's radar" glossary key + pilots keyConcepts)
- [x] Tests: empty-state test FIRST, then ranking unit test, then reason-string NaN-safety test (tests/test_pilots_radar_ranking.py, 23 tests)
- [x] Run `npm run --prefix webapp typecheck` — clean
- [x] Run relevant pytest suite(s) — 28 new/radar-touching tests pass; 27 pre-existing unrelated failures confirmed (numba/pandas_ta/vectorbt caching under this sandbox's system Python 3.14, no .venv)
- [x] Run relevant vitest suite(s) — full suite 177 files / 1989 tests pass
- [x] CLAUDE.md new bullet; verified sync_agent_docs.sh actually mirrors to AGENTS.md (diff -q confirmed identical after edit)
- [x] Final report: DailySignals reality, §7 resolutions, what was built/left out, exact test output, doc sync status, commit list
