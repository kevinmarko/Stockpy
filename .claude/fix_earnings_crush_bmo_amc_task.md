# Task Tracker: fix earnings_crush.py BMO/AMC bar-alignment blind spot

- [x] Explored `pilots/earnings_crush.py`'s realized-move calc and expiration picker; confirmed exact line numbers and bug mechanics.
- [x] Verified (WebFetch/WebSearch against FMP's own published API docs + 3 parallel Explore agents) that no real BMO/AMC field exists in this codebase's earnings data.
- [x] Entered plan mode; wrote and got user approval on the implementation plan (`/Users/kevinlee/.claude/plans/recursive-honking-turing.md`).
- [x] Fixed `get_historical_earnings_moves`: dual BMO/AMC gap hypothesis, take-the-larger, `reaction_session_inferred` field, `timing_data_available: False` on every return path.
- [x] Fixed `evaluate_earnings_crush_candidates`'s expiration picker: `ed >= event_date` → `ed > event_date`.
- [x] Added 3 regression tests to `tests/test_earnings_crush.py` (AMC reproduction, BMO no-regression, expiration-rejection).
- [x] Confirmed all 20 tests in `tests/test_earnings_crush.py` pass.
- [x] Confirmed all 751 tests in the 7 other test files referencing `earnings_crush` pass (zero regressions).
- [x] Confirmed CI's genuine-bug lint gate (`ruff --select=F821,F822,F823,E9`) is clean.
- [x] Added `docs/signals/earnings_crush.md` "Defects found while analysing this pilot" addendum.
- [x] Added `docs/known_issues/earnings_crush_bmo_amc_bar_alignment.md` full incident write-up + `docs/known_issues/README.md` index row.
- [x] Added `CLAUDE.md` bullet (auto-mirrored to `AGENTS.md` via the sync hook).
- [x] Full offline suite (`pytest -m "not network and not slow"`) — 12152 passed, 31 skipped, 5 failed (all 5 pre-existing/environment-only, confirmed via `git stash` re-run — unrelated `google-genai` import gap).
- [x] Flagged findings #2-9 as a follow-up task chip (`mcp__ccd_session__spawn_task`).
- [ ] Commit, push, open PR.
