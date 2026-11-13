# Plan: allow the claude.ai Robinhood connector's headless tool names

**Operator-chosen option (2026-11-13):** allow the claude.ai names, rather than registering a separate
`robinhood-trading` server for `~/Stockpy-live`.

## Context
The headless CLI the timers run names the connected claude.ai Robinhood connector's tools
`mcp__claude_ai_Robinhood__*`. The `rh-*` agents' allowlists didn't include that spelling, so every
unattended cycle and review was cut off from Robinhood.

## Steps
1. For each of `rh-research-scout`, `rh-risk-manager`, `rh-trader` and `rh-reviewer`: add a
   `mcp__claude_ai_Robinhood__X` twin for every `mcp__Robinhood__X` already listed. Add nothing else.
2. Tests:
   - `RH_SERVERS` gains `claude_ai_Robinhood`.
   - A parity test checks the same tool set under every prefix.
   - The reviewer exact-set test is updated.
   - The existing only-`rh-trader`-has-order-power guard already matches any prefix.
3. Docs: the known-issue write-up and index row, a RUNBOOK burn-in note and an `execution.md` sentence.
4. Verify:
   - the agentic suites and the allowlist guard
   - a headless `claude -p` with the Reviewer's `--allowedTools` calling `get_accounts`
   - `make local-ci`
