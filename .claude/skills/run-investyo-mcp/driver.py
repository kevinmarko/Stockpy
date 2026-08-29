#!/usr/bin/env python3
"""Driver for investyo_mcp_server.py -- launches it as a stdio MCP subprocess,
connects a real MCP client, lists its tools, calls one read-only tool, and
prints the real result.

This mirrors the "run-skill-generator" pattern: it is meant to be executed
directly, not imported. It does not fabricate output -- everything printed
came back from an actual initialize/list_tools/call_tool round trip against
a real subprocess.

Usage (from the repo root, matching this repo's own .venv convention):

    .venv/bin/python .claude/skills/run-investyo-mcp/driver.py [TOOL_NAME]

If .venv doesn't exist in the current worktree (common in a git-worktree
checkout that has no venv of its own), point at the main checkout's .venv
interpreter instead, e.g.:

    /path/to/main/checkout/.venv/bin/python .claude/skills/run-investyo-mcp/driver.py

TOOL_NAME defaults to "get_universe_status" -- a read-only tool that reads
local settings/DB state only (no broker, no paid API key, no network).
"""

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Directory containing investyo_mcp_server.py -- this file lives at
# <repo_root>/.claude/skills/run-investyo-mcp/driver.py, so the repo root is
# three levels up.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SERVER_PATH = os.path.join(REPO_ROOT, "investyo_mcp_server.py")

DEFAULT_TOOL = "get_universe_status"
# get_universe_status() takes no arguments.
DEFAULT_ARGS: dict = {}


async def main() -> None:
    tool_name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TOOL
    tool_args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_ARGS

    if not os.path.isfile(SERVER_PATH):
        print(f"ERROR: server not found at {SERVER_PATH}", file=sys.stderr)
        sys.exit(1)

    # Launch investyo_mcp_server.py as a stdio subprocess using the SAME
    # interpreter running this driver, so it resolves the identical venv
    # (mcp SDK, settings.py, pydantic, etc.) rather than whatever "python3"
    # happens to be first on PATH.
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[SERVER_PATH],
        cwd=REPO_ROOT,
        env=os.environ.copy(),
    )

    print(f"Launching: {sys.executable} {SERVER_PATH} (cwd={REPO_ROOT})")

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init_result = await session.initialize()
            print(
                f"Connected. Server: {init_result.serverInfo.name} "
                f"v{init_result.serverInfo.version} "
                f"(protocol {init_result.protocolVersion})"
            )

            tools_result = await session.list_tools()
            tools = tools_result.tools
            print(f"\nDiscovered {len(tools)} tools:")
            for t in tools:
                print(f"  - {t.name}")

            if tool_name not in {t.name for t in tools}:
                print(f"\nERROR: tool '{tool_name}' not found on server.", file=sys.stderr)
                sys.exit(1)

            print(f"\nCalling tool: {tool_name}({tool_args!r})")
            result = await session.call_tool(tool_name, arguments=tool_args)

            print("\n--- Tool result (isError=%s) ---" % result.isError)
            for block in result.content:
                if hasattr(block, "text"):
                    print(block.text)
                else:
                    print(block)


if __name__ == "__main__":
    asyncio.run(main())
