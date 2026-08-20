# Task tracker — revert scope-creep default=True flips (7 flags)

Branch: `revert-diagnostic-flag-default-flips`

## Steps

- [x] Investigate: confirm commit `30d136ae8` flipped `SECTOR_HEAT_ENABLED` /
      `WIKIPEDIA_ATTENTION_ENABLED` to `default=True` with no corresponding
      docstring/CLAUDE.md update, as reported.
- [x] Diff the commit directly — found 5 more flags flipped the same way that
      weren't part of the named "frozen 16" admin/write/execution gate list:
      `SENTIMENT_INDEX_ENABLED`, `ETF_TRANSMISSION_ENABLED`,
      `EDGAR_FULLTEXT_ENABLED`, `ETF_HOLDINGS_ENABLED`,
      `MARKET_DATA_LATENCY_TRACKING_ENABLED`.
- [x] Confirm `BROKERAGE_CONNECT_ENABLED` (also flipped in the same commit)
      is NOT part of the pattern — its docstring was correctly updated,
      it's a genuine write/command endpoint.
- [x] Confirm via `tests/test_settings.py`'s self-contradictory
      `test_sentiment_attention_scaffolding_defaults` (comment says
      "preserve today's exact behavior" directly above `is True`
      assertions) that this was a mechanical mass-flip, not a deliberate
      per-flag decision.
- [x] Get user confirmation on scope (AskUserQuestion) — user confirmed all 7.
- [x] User sent a mid-turn "we want those flags to be true" message reversing
      course; confirmed via AskUserQuestion this was a miscommunication and
      the revert-to-False plan should proceed as originally confirmed.
- [x] `EnterPlanMode` — write and get approval for the Implementation Plan
      (`/Users/kevinlee/.claude/plans/woolly-soaring-liskov.md`).
- [x] `git fetch origin` + confirm already synced to `origin/main`
      (`d4d09502`).
- [x] `git checkout -b revert-diagnostic-flag-default-flips`.
- [x] `settings.py`: flip the 7 fields' `default=True` → `default=False`.
- [x] `settings.py`: fix the adjacent broken cross-reference in
      `SENTIMENT_INGESTION_ENABLED`'s docstring (dropped `PILOTS_API_ENABLED`
      from the "opt-in... default False" comparison list, since it now
      legitimately defaults `True` and no longer belongs in that list).
- [x] `tests/test_settings.py`: fix the 3 wrong `is True` assertions to
      `is False`; add `delenv` + assertion coverage for the 4 flags this
      test never covered (`SENTIMENT_INDEX_ENABLED`, `ETF_TRANSMISSION_ENABLED`,
      `ETF_HOLDINGS_ENABLED`, `MARKET_DATA_LATENCY_TRACKING_ENABLED`).
- [x] `webapp/src/api/mock.ts`: fix `FEATURE_FLAGS_TUNABLE_DEFS`'s
      "Diagnostic & Data Features" group — 7 entries `value`/`default`
      `true` → `false`.
- [x] Verify: `pytest tests/test_settings.py` (45 passed).
- [x] Verify: `pytest tests/test_quantitative_models.py::test_main_orchestrator_pipeline
      tests/test_feature_flags_registry.py tests/test_settings_keysets.py`
      (28 passed).
- [x] Verify: `npm run --prefix webapp typecheck` (clean).
- [x] Verify: `npx vitest run src/screens/FeatureFlagsScreen.test.tsx` (1 passed).
- [x] Verify: full offline suite `pytest -q -m "not network" -p no:randomly`
      — 11627 passed, 31 skipped, 88 deselected, 5 pre-existing failures
      confirmed unrelated (reproduce identically on unmodified `HEAD` —
      `test_data_api_chat.py`/`test_gemini_live_chat.py`, an `ImportError`
      in AI-chat-provider routing tests, nothing to do with the 7 flags
      touched here).
- [x] Copy PR artifacts to `.claude/` with unique scoped names.
- [x] Commit, push, open PR.
