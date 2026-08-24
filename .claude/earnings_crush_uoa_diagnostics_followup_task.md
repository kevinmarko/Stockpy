# Task Tracker: Earnings Crush / UOA Diagnostics Follow-Up (Finding #7)

- [x] `pilots/earnings_crush.py`: `diagnostics` kwarg on
      `evaluate_earnings_crush_candidates` (symbols_total, store_available,
      options_provider_available, symbols_errored).
- [x] `pilots/earnings_crush.py`: `diagnostics` kwarg forwarded through
      `get_earnings_crush_candidates`.
- [x] `api/pilots_api.py`: `get_options_flow_unusual` wires `diagnostics` into
      `get_unusual_options_activity`, returns `degraded`/`symbols_fetch_failed`.
- [x] `api/pilots_api.py`: `get_options_earnings_crush_candidates` wires
      `diagnostics` into `get_earnings_crush_candidates`, returns
      `degraded`/`symbols_errored`.
- [x] `webapp/src/api/types.ts`: `UnusualOptionsFlowResponse`,
      `EarningsCrushCandidatesResponse`, `UnusualOptionTrade` updated.
- [x] `tests/test_earnings_crush.py::TestEarningsCrushDiagnostics` (5 tests).
- [x] `tests/test_pilots_paper_broker.py`: 4 new endpoint tests.
- [x] `docs/signals/options_flow_sentiment.md`: "Defects found while
      analysing `pilots/unusual_options_flow.py`" section.
- [x] `docs/known_issues/earnings_crush_uoa_followup_audit_findings.md`:
      combined write-up, findings #2–#9.
- [x] `docs/known_issues/README.md`: index row added.
- [x] `pytest tests/test_earnings_crush.py -q` — 30 passed.
- [x] `pytest tests/test_pilots_paper_broker.py -q` — 183 passed (full file).
- [x] `npm run --prefix webapp typecheck` — clean.
- [x] `.claude/` PR artifacts (this file, implementation plan, walkthrough).
- [x] Commit and push `earnings-crush-uoa-diagnostics-followup`.

## Explicitly out of scope (owned by sibling branches)

- `pilots/unusual_options_flow.py` — owned entirely by
  `unusual-options-flow-engine-fixes` (findings #3, #4, #5, #6, #8, and the
  `get_unusual_options_activity` half of #7).
- `EarningsCrushCandidate`/`EarningsCrushExecutionResult` in
  `webapp/src/api/types.ts` — owned by
  `earnings-crush-followup-historical-moves-net-credit` (findings #2, #9).
