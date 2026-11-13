# Task: allow the claude.ai Robinhood connector's headless tool names

- [x] Add `mcp__claude_ai_Robinhood__*` twins to rh-research-scout, rh-risk-manager, rh-trader and rh-reviewer (no new tools)
- [x] Tests: the three-prefix parity guard; scout, risk-manager and reviewer exact sets include the new prefix
- [x] Headless proof: Reviewer allowlist → `get_accounts` succeeds
- [x] Docs: known issue + index, RUNBOOK burn-in note, execution.md
- [x] `make local-ci`: all PASS except pip-audit (pre-existing dependency advisories), table in the PR
- [ ] After merge: sync the main checkout; re-run today's review
