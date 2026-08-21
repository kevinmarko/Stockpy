# Goal Description
Give a coding-agent CLI (Antigravity's `agy`) a read-only view of the operator's
real Robinhood account without ever connecting it to Robinhood's own hosted
trading MCP endpoint (`agent.robinhood.com/mcp/trading`), which bundles
order-placement tools (`place_equity_order`, `place_option_order`,
`cancel_equity_order`) on the same connection as its read tools, with no
narrower read-only scope offered by Robinhood itself.

## Proposed Changes
### `investyo_mcp_server.py`
[MODIFY] Add `get_robinhood_account_snapshot()` — a new `readOnlyHint=True`
`@mcp.tool()` sourced from `data.robinhood_portfolio.fetch_account_snapshot(
force=False, allow_live_fetch=False)`. Never triggers a live Robinhood login;
degrades to an honest "no cached snapshot" message rather than fabricating
figures.

### `docs/architecture/observability-and-apis.md`
[MODIFY] Add the new tool to the "Advisory & Market Intelligence (read-only)"
inventory and bump the documented tool count (75 -> 76).

### `tests/test_investyo_mcp_server.py`
[MODIFY] Add `TestGetRobinhoodAccountSnapshot` — covers the never-forces-a-
live-login invariant, honest degradation with no cache, empty-positions
rendering, positions-table rendering, and staleness flagging.

### `tests/test_investyo_mcp_tool_annotations.py`
[MODIFY] Add the `readOnlyHint=True` regression check for the new tool,
matching this file's existing per-tool convention.
