# Agentic agents blocked from Robinhood under the headless CLI (2026-11)

**Status:** Fixed in config (2026-11-13).

## Symptom
On the burn-in's first day the headless Reviewer (`claude -p /rh-agentic-review`, from the
`com.investyo.rh-agentic-review` timer) recorded no review, even after the operator logged the CLI in
again. Its final message said the Robinhood MCP "failed to connect", so it saved no
orders, quotes or approval-setting inputs, and `run` exited 2 (inputs unusable).

## Root cause
This Mac's CLI has one Robinhood server for `~/Stockpy-live`: the **claude.ai connector**
(`claude mcp list` shows `claude.ai Robinhood: https://agent.robinhood.com/mcp/trading`). The headless
CLI names its tools `mcp__claude_ai_Robinhood__<tool>`.

The `rh-*` agents' `tools:` lines, and so the launchers' `--allowedTools` (parsed from them by
`execution/agentic_cycle.py::agent_tools`), listed only `mcp__robinhood-trading__*` and
`mcp__Robinhood__*`. A `robinhood-trading` server was registered only for the old
`/Users/kevinlee/Desktop/Stockpy` project. Every Robinhood call from an unattended cycle or review was
therefore outside the allowlist. That covers all five agents, so the 15-minute cycle was affected too.

## Fix
- Each of `rh-research-scout`, `rh-risk-manager`, `rh-trader` and `rh-reviewer` now also lists every
  Robinhood tool it already had under `mcp__claude_ai_Robinhood__`. No agent gained a new tool.
- `tests/test_rh_agent_tool_allowlists.py::test_robinhood_tools_identical_under_every_server_spelling`
  pins the same set under all three prefixes.
- The existing guard that only `rh-trader` may hold order-power tools covers the new spelling: its
  pattern matches any `mcp__<server>__` prefix.

## Verified
A headless `claude -p` from this branch, with the Reviewer launcher's exact `--allowedTools`, called
`get_accounts`. It returned 3 accounts, 1 of them agentic.
