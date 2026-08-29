---
name: run-investyo-mcp
description: Run, launch, and drive this repo's own MCP server, investyo_mcp_server.py -- start it as a stdio subprocess, connect a real MCP client, list its tools, and call a tool to get a real response. Use when asked to run/test/drive/exercise the InvestYo MCP server or verify a change to investyo_mcp_server.py actually works end-to-end (not just imports cleanly).
---

# Run InvestYo MCP Server

`investyo_mcp_server.py` (repo root) is this repo's own MCP server: a `FastMCP`
app (from the `mcp` SDK's `mcp.server.fastmcp`) exposing ~76 read/analytics
tools over the InvestYo Quant Platform (portfolio, signals, backtests, pilots,
options, prompt registry, etc.) to an MCP client such as Claude Desktop or
Claude Code. It is advisory-only -- no live order-submission tool exists.

**Transport**: the default invocation (`python investyo_mcp_server.py`, no
flags) runs **stdio** -- the `if __name__ == "__main__":` block at the bottom
of the file falls through to `mcp.run(transport="stdio")` when no
`--transport` argument is passed. SSE and streamable-HTTP (with bearer-token
or OAuth auth) are also supported via CLI flags but are not what this skill
drives.

This skill is driven by `.claude/skills/run-investyo-mcp/driver.py`: a
self-contained script that launches `investyo_mcp_server.py` as a stdio
subprocess, connects a real `mcp.ClientSession`, lists every registered tool,
calls one read-only tool, and prints the actual result.

## Prerequisites

- The `mcp` Python SDK, pinned `mcp<2.0.0` in `requirements.txt` (2.0.0 was a
  breaking rewrite that removed `FastMCP`; this server has not been migrated).
  In this environment it was **already installed** in the repo's `.venv`
  (Python 3.12) at version 1.28.1 -- confirmed via:
  ```
  /Users/kevinlee/Stockpy-live/.venv/bin/python -m pip show mcp
  ```
  No `pip install` was actually needed in this session. If your environment's
  `.venv` doesn't have it, install with `.venv/bin/pip install "mcp<2.0.0"`.
- No `.env` file and no environment variables are required to start the
  server or to call `get_universe_status` -- `settings.py`'s Pydantic
  defaults are enough for a fresh clone. It falls back to `AAPL, MSFT, JNJ,
  AGNC` as the active universe when `DEFAULT_TICKERS` is unset, and to the
  local SQLite DB at `settings.LOCAL_DATA_ROOT` (default `~/.stockpy_local`)
  for its DB metrics, creating it read-only-safe with all-zero counts if it
  doesn't exist yet.
- **A `.venv` for THIS specific worktree may not exist** (git worktrees don't
  each get their own `.venv` automatically). If `ls .venv` in your worktree
  comes up empty, point the driver at another checkout's `.venv/bin/python`
  instead (see Run below) -- any `.venv` built from this repo's
  `requirements.txt` works, since the server and its dependencies aren't
  worktree-specific.

## Run (agent path)

From the repo root (adjust the interpreter path if your own worktree has its
own `.venv`):

```
/Users/kevinlee/Stockpy-live/.venv/bin/python .claude/skills/run-investyo-mcp/driver.py
```

This calls `get_universe_status` (no arguments) by default -- a read-only
tool with the fewest external dependencies of the 76 registered tools: it
reads `settings.DEFAULT_TICKERS`, an optional local `watch_rules.yaml`, and
three `SELECT COUNT(*)` queries against the local SQLite DB, each wrapped in
its own `try/except` so a missing table or file degrades to a message instead
of crashing. No broker, no paid API key, no network egress.

To call a different tool, pass its name (and optionally a JSON args object)
as CLI arguments, e.g. `driver.py get_regime_status`.

**Healthy output looks like this** (real excerpt from this session -- your
`Daily Signals`/`Trades`/`Execution Logs` counts will differ once a live
pipeline has actually run):

```
Connected. Server: InvestyoPlatform v1.28.1 (protocol 2025-11-25)

Discovered 76 tools:
  - list_registry_prompts
  - get_doc
  ...
  - get_universe_status
  ...

Calling tool: get_universe_status({})

--- Tool result (isError=False) ---
# InvestYo Universe Status Dashboard

## Active Trading Universe
`AAPL`, `MSFT`, `JNJ`, `AGNC`

## Active Watch Rules
Symbol | Alert Trigger | Threshold | Priority | Label
---|---|---|---|---
`*` | conviction_above | 0.85 | high | High Conviction Alert
`*` | action_change | N/A | default | Action Signal Change

## Database Metrics
- **Daily Signals Table Rows**: 0
- **Trades Table Rows**: 0
- **Execution Logs Table Rows**: 0
```

A `0`-row `Database Metrics` section is expected and correct on a fresh
clone/sandbox with no pipeline runs yet -- it is not a failure.

## Gotchas / Troubleshooting

- **This worktree has no `.venv` of its own.** `ls .venv` in the
  `skill-generator-gemini-mcp-15dff6` worktree returns nothing (only the main
  checkout at `/Users/kevinlee/Stockpy-live/.venv` has one, Python 3.12 with
  `mcp==1.28.1` already installed). The fix used in this session was simply
  to invoke the driver with the main checkout's interpreter directly
  (`/Users/kevinlee/Stockpy-live/.venv/bin/python .../driver.py`) rather than
  assuming a local `.venv/bin/python` exists -- the server code and its
  dependencies aren't worktree-specific, so any `.venv` built from this
  repo's `requirements.txt` works. If you hit `ModuleNotFoundError: No module
  named 'mcp'` (or `settings`, `pandas`, etc.) running from a bare system
  Python, this is almost always the cause -- find or build a real `.venv`
  first (`ls .venv` at the repo root you're actually in), don't just `pip
  install` into whatever interpreter happens to be first on `PATH`.
- **No timeout command on macOS by default.** `timeout 60 ...` fails with
  `command not found: timeout` in a plain zsh shell (no coreutils `timeout`
  installed). Just run the command directly and rely on the driver's own
  clean shutdown (the `async with` blocks close the subprocess and streams
  automatically once `main()` returns) -- it does not hang.
- **`bash: mkdir: Operation not permitted` when creating
  `.claude/skills/<name>/`.** The default Bash sandbox denies writes under
  `.claude/skills` even though it allows `.` broadly. This is expected --
  creating or editing files under `.claude/skills/` needs the sandbox
  override for that one write, not a real permissions problem with the repo.
- Everything else worked on the first real run in this session: the module
  imported cleanly with zero env vars set (`import investyo_mcp_server` at a
  Python prompt returns immediately, no `MCP_OAUTH_ENABLED` or DB setup
  needed for the default bearer-token-less stdio path), `list_tools()`
  returned all 76 tools, and `get_universe_status` returned real (if empty)
  data on the first call -- no missing-credential or network-egress errors
  were hit for this particular tool. A tool that touches a broker
  (`get_robinhood_account_snapshot`), a paid data API (`get_quote`,
  `run_backtest`), or the webapp (`inspect_webapp_screen`) would very likely
  need real credentials/network access this sandbox does not have; those
  were deliberately not the tool exercised here.
