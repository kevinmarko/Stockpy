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

## Code-review pass (2026-09-01) — 8 findings, all fixed

`/code-review` at high effort over `640f0f03..2038dd6e` found 8 more issues
in this chain's own files. All 8 fixed; see
`.claude/create_stockpy_master_prompt_walkthrough.md`'s matching section
for the full detail and verification evidence.

- [x] Fixed CVaR-placeholder misattribution in both `stockpy-quant-integrity/SKILL.md` mirror copies (`sizing/hrp_cvar_optimizer.py` -> `api/pilots_api.py`), confirmed via `git log -S` <!-- id: 9 -->
- [x] Fixed `test_mirrored_skill_set_is_non_empty`'s coverage-gap blind spot (now checks full `EXACT_MIRROR_SKILLS` coverage, not just non-empty intersection); verified it fires on a simulated partial-coverage-loss <!-- id: 10 -->
- [x] Reworded `stockpy-master-prompt/SKILL.md`'s startup-ritual step 1 (both mirror copies) — no longer asserts a `memory_read`/`/areas/` tool that doesn't exist in this session <!-- id: 11 -->
- [x] Replaced `test_skill_directory_parity.py`'s docstring claim about pytest's empty-`parametrize` behavior with the verified-correct one (SKIPPED, not silently passed) <!-- id: 12 -->
- [x] Widened `test_robinhood_e2e.py::TestSkillMdInvariantsPinned` to check every existing copy of `robinhood-execution/SKILL.md`, not just `.claude`'s with an `.agents` fallback-only-if-missing; verified both copies pass via a standalone script <!-- id: 13 -->
- [x] Corrected the walkthrough's overstated CLAUDE.md direct-to-main carve-out claim re: `.agents/` <!-- id: 14 -->
- [x] Replaced the manual first-differing-line scan in `test_skill_directory_parity.py` with `difflib.unified_diff`; verified it now surfaces multiple divergent regions in one run (scratch mutation, reverted) <!-- id: 15 -->
- [x] Extended `.claude/hooks/sync_agent_docs.sh` and `.agents/hooks/sync_agent_docs.sh` to also auto-sync the `stockpy-master-prompt`/`stockpy-quant-integrity` SKILL.md mirror pairs (the altitude-level fix, initially skipped as "can't safely trigger-test a live hook," then actually fixed and verified end-to-end in the same session — both scripts read stdin JSON and `cp`, which can be exercised directly without a real harness) <!-- id: 16 -->
