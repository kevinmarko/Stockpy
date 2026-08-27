# Secondary Audit Items — Task Tracker

- [x] Load `stockpy-quant-integrity` skill
- [x] Dispatch 4 parallel audit agents (LLM commentary, sector selection, GEX,
      multi-broker NBBO)
- [x] Synthesize findings, verify highest-severity ones directly
- [x] Fix GEX 100x dollar-scaling bug (`pilots/options_gex.py`) + regression tests
- [x] Fix GEX IV/DTE CONSTRAINT #4 fabrication fallbacks + regression tests
- [x] Fix `GexProfileView.tsx` honesty banner (synthetic/mock chain_source) +
      webapp tests
- [x] Fix SEC 606 `price_improvement` fabricated-zero (`nbbo_available` column,
      coverage-aware reporter rates) + regression tests
- [x] Wire `classify_limit_order` into the write path
- [x] Add crossed-market guard to `fix_gateway.py::synthesize_nbbo` (extracted to
      `derive_nbbo_from_venue_quotes` for testability) + regression tests
- [x] Fix sector-selection similarity-term lookahead gap (`get_fundamentals_raw_json_asof`
      point-in-time lookup) + regression tests, including a real-provider-shape
      verification test (not just asserted safe)
- [x] Fix sector-selection `degraded_reason` operand-order masking bug + regression
      test
- [x] Fix LLM prompt-injection delimiter gap (`<headline>`/`<research_context>`
      fencing) + regression tests
- [x] Clean up `llm/chart_insight.py` leftover debug statement
- [x] Write 4 `docs/known_issues/*.md` write-ups
- [x] Update `docs/known_issues/README.md` index
- [x] Update `CLAUDE.md`/`AGENTS.md` (auto-synced via hook)
- [x] Update `docs/architecture/execution.md` (5 entries)
- [x] Update `docs/signals/sector_selection.md` (correct stale claims + document fix)
- [x] Run targeted test suites (405 passed)
- [x] Run full offline suite (12,358 passed, 1 pre-existing unrelated failure)
- [x] Run narrow CI ruff gate on touched files (clean)
- [x] Run webapp typecheck + vitest (clean)
- [x] Flag pre-existing `docs/settings_liveness.json` staleness via `spawn_task`
      (unrelated to this branch's changes, confirmed by history)
- [x] Write PR artifacts (this file + implementation plan + walkthrough)
- [x] Commit and open PR — [#910](https://github.com/kevinmarko/Stockpy/pull/910)
