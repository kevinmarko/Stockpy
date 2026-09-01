# Jules Capability Model Correction — Task Tracker

## Inventory phase
- [x] Full repo-wide search for every Jules capability reference (24 files found, 17 needed correction)

## Fix phase (5 parallel agents)
- [x] Agent A — Core client: `JulesCapabilityNotAvailable` added, `dispatch_session()` neutered (unconditional raise, no gating), dead code removed (confirm-gate exception, dedup ledger, advisory lock), `list_sources`/`format_sources` untouched (24/24 tests pass)
- [x] Agent B — CLI: `scripts/jules_dispatch.py`'s `create-session` help text/docstrings corrected up front, real exception still propagates cleanly, `list-sources` untouched, real end-to-end CLI invocation verified (11/11 tests pass)
- [x] Agent C — MCP tool: `dispatch_jules_task`'s docstring rewritten (most load-bearing false claim in the whole codebase), `list_jules_sources` untouched (5/5 tests pass)
- [x] Agent D — Primary docs: `docs/JULES_INTEGRATION.md` full rewrite, both `jules-delegation/SKILL.md` files (`.claude/` + `.agents/`) full rewrite, verification grep confirms clean
- [x] Agent E — Secondary docs: `CLAUDE.md`/`AGENTS.md`, `.env.example`, `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md`, `docs/README.md`, `settings.py`, `settings_keysets.py` — all corrected, verification grep + `cmp` clean

## Direct fixes (orchestrating session)
- [x] `review_summary.md` — correction note prepended (not a rewrite of the historical record)
- [x] Confirmed `.claude/agentic_safety_framework_docs_*.md` process notes need no fix (already benign/honest)

## Final verification (orchestrating session, independent of all fix agents' self-reports)
- [x] Re-ran full Jules test suite: 40/40 pass
- [x] Re-ran `tests/test_settings.py`: 45/45 pass (confirmed the 7 apparent failures were the pre-existing sandbox numba issue, not a regression)
- [x] Confirmed `settings.py`/`settings_keysets.py` still import cleanly
- [x] Confirmed `docs/settings_field_census.md`/`.json` need no regeneration (no diff produced)
- [x] Confirmed `CLAUDE.md`/`AGENTS.md` byte-identical via `cmp`
- [x] Confirmed `JULES_ENABLED` still correctly present in `settings_keysets.DANGEROUS_KEYS`

## Disclosed, not fixed in this PR
- [ ] `review_summary.md`'s implied Jules authorship of several merged PRs not independently re-verified
- [ ] Separate, unrelated finding: `review_summary.md` recommends rejecting PR #946 for a real transaction-nesting bug, but PR #946 appears merged anyway — flagged to operator, not investigated further here
- [ ] No working "Jules audits an existing PR/codebase" dispatch mechanism exists anywhere in this repo — building one is new work, not attempted in this pass
