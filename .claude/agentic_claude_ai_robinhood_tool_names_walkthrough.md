# Walkthrough: claude.ai Robinhood tool names for the agentic agents

## Problem
The launchd timers run the headless `claude -p` CLI. On this Mac its only Robinhood server is the
claude.ai connector, whose tools are named `mcp__claude_ai_Robinhood__*`. The agents' allowlists only
had `mcp__robinhood-trading__*` and `mcp__Robinhood__*`, so every unattended cycle and review was cut
off from Robinhood. Write-up:
`docs/known_issues/agentic_agents_blocked_claude_ai_robinhood_tool_names_2026_11.md`.

## Change
- **Agent configs.** Each Robinhood tool an `rh-*` agent already listed under `mcp__Robinhood__` gets a
  twin under `mcp__claude_ai_Robinhood__`:
  - scout: 10
  - reviewer: 5
  - risk manager: 6
  - trader: 11
  No agent gains a tool. `execution/agentic_cycle.py::agent_tools` parses these lines, so both
  launchers' `--allowedTools` follow automatically. No Python change.
- **Tests.**
  - `RH_SERVERS` gains the third prefix.
  - New parity test: identical Robinhood tool sets under all three prefixes, for all four agents.
  - The reviewer exact-set test in `tests/test_agentic_review.py` is updated.

## Verified
- `pytest` on the agentic suites and the allowlist guard: 352 passed.
- Headless `claude -p` from this branch with the Reviewer's `--allowedTools`: `get_accounts` returned 3
  accounts, 1 agentic.
