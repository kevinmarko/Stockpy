# Task Tracker: Create Stockpy Master Prompt Skill

- [x] Create `.agents/skills/stockpy-master-prompt/SKILL.md` for Antigravity <!-- id: 0 -->
- [x] Create `.claude/skills/stockpy-master-prompt/SKILL.md` for Claude Code <!-- id: 1 -->
- [x] Ensure `.agents/skills/stockpy-quant-integrity/SKILL.md` exists for parity <!-- id: 2 -->
  - **Correction (2026-09-01):** checked off prematurely — the file existed
    but was NOT at content parity (stale `STRATEGY_REGISTRY` section). Fixed
    by PR #972 (`c274c34a`, concurrent with the review that found this).
- [x] Update architecture docs (`docs/architecture/simulation-eval-reporting.md`) to document new skill <!-- id: 3 -->
- [x] Verify existing skill invariant tests pass <!-- id: 4 -->
- [x] Create scoped PR artifacts and walkthrough <!-- id: 5 -->

## Follow-up pass (2026-09-01) — after PR #972

PR #972 fixed the two core findings (stale `stockpy-quant-integrity` mirror,
stale `stockpy-master-prompt` §7) independently, concurrently with a code
review that found the same bugs. See
`docs/known_issues/skill_directory_manual_copy_drift.md` for the full,
reconciled write-up. This pass adds what PR #972 didn't have:

- [x] Add `tests/test_skill_directory_parity.py` regression test (byte-identity for `stockpy-master-prompt`/`stockpy-quant-integrity`, the only two confirmed true mirrors); verified it passes against the fixed state and fails on a reintroduced drift <!-- id: 6 -->
- [x] Sweep the 8 other skills shared by both `.agents/skills/` and `.claude/skills/` for content drift beyond their shared porting-note preamble <!-- id: 7 -->
- [x] Found and fixed a real, previously-unknown divergence in `.agents/skills/agentic-discovery/SKILL.md` — wrongly claimed `WATCHLIST` takes precedence over `watchlist.txt`; `main._load_watchlist()`'s own docstring says neither takes precedence, both are unioned. Verified the other 7 skills are body-identical beyond the preamble (no fix needed) <!-- id: 8 -->
