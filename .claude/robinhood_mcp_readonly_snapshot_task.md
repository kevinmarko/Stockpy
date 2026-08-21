- [x] Investigate whether Robinhood's hosted MCP endpoint offers a narrower
      read-only scope (it doesn't — order tools live on the same connection
      as read tools, gated only at the account level).
- [x] Implement `get_robinhood_account_snapshot` tool in `investyo_mcp_server.py`.
- [x] Add tool documentation to `docs/architecture/observability-and-apis.md`.
- [x] Write unit tests in `tests/test_investyo_mcp_server.py` and
      `tests/test_investyo_mcp_tool_annotations.py`.
- [x] Run the genuine-bug ruff gate (`F821,F822,F823,E9`) and the full
      offline pytest suite; confirm pre-existing failures are unrelated
      (verified via `git stash`).
