---
name: run-investyo-mcp
description: Run, launch, and test the InvestYo MCP server locally using a Python driver script to list tools and call a read-only tool.
---

# `run-investyo-mcp`

This skill provides a driver script to launch the InvestYo MCP server (`investyo_mcp_server.py`) and verify that it can successfully initialize and execute a tool call over the `stdio` transport.

## Prerequisites
- The platform's virtual environment must be active or you must use the same python interpreter that has the platform's dependencies installed (`uv run` or `.venv/bin/python`).
- No special API tokens are strictly required just to boot the server and call read-only endpoints like `get_regime_status`. If using HTTP transport (not used by default here), `MCP_HTTP_BEARER_TOKEN` must be set in `.env`.

## Driver Script

The driver script is located at `.claude/skills/run-investyo-mcp/driver.py`. It uses the `mcp` SDK to:
1. Launch `investyo_mcp_server.py --transport stdio` as a subprocess.
2. Initialize an MCP `ClientSession`.
3. List the available tools.
4. Call `get_regime_status` and print the output.

## Verified Invocation

Run the driver script from the repository root:

```bash
python3 .claude/skills/run-investyo-mcp/driver.py
```

## Healthy vs. Failing Run

### Healthy Run
A healthy run will output connection success, a tool count, and the output from the `get_regime_status` tool (a markdown document containing regime metrics). Example output:

```
Connecting to MCP server using command: /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 investyo_mcp_server.py --transport stdio
Successfully initialized session with MCP server.
Found 76 tools.
Found target tool: get_regime_status
Calling tool 'get_regime_status'...
Tool call result:
# Macro Regime & Risk Status
...
```

### Failing Run
A failing run might hang if the server fails to start, or return an error during `session.initialize()`. If a tool is missing or dependencies are broken, you may see:
- Exceptions related to `mcp.client` connection errors.
- `Tool 'get_regime_status' not found!` (if the tool was removed or failed to register).
- The server crashing and exiting before the session can be initialized.
