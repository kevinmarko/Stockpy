# Walkthrough

1. Confirmed via web search that Robinhood's hosted trading MCP
   (`agent.robinhood.com/mcp/trading`) has no separate read-only endpoint —
   `place_equity_order`/`place_option_order`/`cancel_equity_order` sit on the
   same connection as `get_accounts`/`get_portfolio`/etc.; Robinhood's own
   safety boundary is a dedicated funded sub-account, not a tool-level scope.
2. Instead of connecting an external coding-agent CLI to that live endpoint,
   added `get_robinhood_account_snapshot()` to this platform's own
   `investyo_mcp_server.py` (already running locally, already the connected
   `investyo-platform` server in this session) — a strictly read-only tool
   backed by `data/robinhood_portfolio.py`, a module whose own docstring
   states it contains no order code of any kind.
3. The call is hardcoded `force=False, allow_live_fetch=False`, so it can
   never spawn a live Robinhood device-approval login on an agent's behalf —
   only ever reads the platform's existing DB/JSON-cache snapshot.
4. Added 6 tests (5 in `TestGetRobinhoodAccountSnapshot`, 1 in the dedicated
   annotations regression file) — all passing, including a direct assertion
   that `fetch_account_snapshot` is always called with
   `force=False, allow_live_fetch=False`.
5. Ran `ruff check . --select=F821,F822,F823,E9` (clean) and the full
   offline suite (`pytest -m "not network and not slow"`): 11,776 passed,
   24 pre-existing failures confirmed unrelated via `git stash` (same
   failures reproduce on a clean tree with none of this change applied).
6. Documented the new tool in `docs/architecture/observability-and-apis.md`
   and bumped the tool count (75 -> 76).

## How to connect `agy`

Point Antigravity at the same local stdio server this session already uses
(`investyo-platform` in `~/.claude.json`), instead of Robinhood's hosted
trading endpoint:

```bash
agy mcp add --transport stdio investyo-platform -- \
  /Users/kevinlee/Stockpy-live/.venv/bin/python3 \
  /Users/kevinlee/Stockpy-live/investyo_mcp_server.py
```

(Confirm the exact flag for passing a stdio command via `agy mcp add --help`
— the shape above matches the existing `investyo-platform` stdio
registration for Claude Code, but `agy`'s own CLI syntax wasn't verified
directly since `agy` isn't installed in this environment.)

This gives `agy` the full 76-tool platform surface — no live-order code
exists anywhere in it (enforced repo-wide by
`tests/test_pipeline_smoke.py::TestNoOrderFunctions`) — rather than a
narrower read-only-only server, per the operator's explicit choice.
