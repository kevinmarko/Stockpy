# Task Tracker — earnings_crush.py follow-up findings #2 and #9

- [x] Verify HEAD is `aff69cad` (BMO/AMC bar-alignment fix) before starting.
- [x] Finding #2: wire `historical_moves`/`company_name` through
      `to_earnings_crush_candidate_response()`; resolve `company_name` defensively in
      `evaluate_earnings_crush_candidates()`.
- [x] Finding #2: confirm `report_timing` is deliberately NOT added; document why inline.
- [x] Confirm `webapp/src/api/types.ts` already has `historical_moves?`/`company_name?` —
      no change needed there for finding #2.
- [x] Finding #9: reconstruct `net_credit` in `execute_earnings_crush_trade()`'s success
      branch from real executor fields; `None` when unavailable.
- [x] `webapp/src/api/types.ts`: `EarningsCrushExecutionResult.net_credit` → optional.
- [x] `webapp/src/components/options/EarningsCrushScanner.tsx`: guard `res.net_credit`
      usage with optional chaining + fallback.
- [x] Add regression tests to `tests/test_earnings_crush.py` (7 scenarios from the task spec).
- [x] Update `docs/signals/earnings_crush.md` with a new "Defects found" item 2, referencing
      `docs/known_issues/earnings_crush_uoa_followup_audit_findings.md` (created elsewhere).
- [x] Run `pytest tests/test_earnings_crush.py tests/test_pilots_paper_broker.py -q` — pass.
- [x] Run `npm run --prefix webapp typecheck` — clean.
- [x] Create `.claude/` PR artifacts (this file, implementation plan, walkthrough).
- [x] Commit, push, open PR. Note: `fix-earnings-crush-bmo-amc-blindspot` (PR #889) was
      merged and its branch deleted mid-session, so PR #892 targets `main` instead — verified
      via `git diff origin/main..HEAD --stat` that the diff contains only this PR's own
      changes.
