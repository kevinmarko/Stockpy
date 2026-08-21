# Agent 2: Overnight Liquidity Tool Implementation Plan

## Goal
Implement the `check_overnight_liquidity` tool in `investyo_mcp_server.py` in the `Stockpy-live-agent2` worktree. The tool will provide an approximation of a symbol's overnight liquidity profile, explicitly avoiding any claims of real Level-2 data.

## Open Questions
- Should the tool also wrap `execution.overnight_guardrails.OvernightGuardrails.check_overnight_intent(symbol, weight, earnings)`? (If so, it would need weight and earnings as inputs, but the tool name is just `check_overnight_liquidity`). 

## Proposed Changes

### `investyo_mcp_server.py`
#### [MODIFY] `investyo_mcp_server.py`
- Register a new `@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))` named `check_overnight_liquidity(symbol: str) -> str`.
- **Implementation**:
  - Fetch `quote = get_provider().get_latest_quote(symbol)`.
  - Use `yfinance` to fetch the Average Daily Volume (ADV) for the symbol.
  - Calculate approximate bid-ask spread `(quote.ask - quote.bid)` and percentage spread.
  - Compute a rough liquidity approximation (e.g., `approximate_depth_notional = ADV * quote.price * 0.01` or similar heuristic).
  - Explicitly include a disclaimer in the output: *"NOTE: Data source is an approximation based on Top-of-Book spread and Average Daily Volume. No claims of real Level-2 data exist."*
  - Format the response as Markdown with a fenced JSON block.

### `tests/test_investyo_mcp_overnight_liquidity.py`
#### [NEW] `tests/test_investyo_mcp_overnight_liquidity.py`
- Add unit tests to verify:
  - Tool handles valid tickers and missing data (NaN) without fabricating numbers.
  - Tool output explicitly includes the "approximation" and "no claims of real Level-2 data" disclaimer.
  - Tool functions independently of `execution/risk_gate.py`.

### `docs/architecture/observability-and-apis.md`
#### [MODIFY] `docs/architecture/observability-and-apis.md`
- Add `check_overnight_liquidity` to the list of Advisory & Market Intelligence read-only tools.

## Verification Plan
### Automated Tests
- Run `pytest tests/test_investyo_mcp_overnight_liquidity.py`.

### Manual Verification
- Review the diff to confirm `execution/risk_gate.py` is absolutely NOT modified.
- Grep the diff to ensure no Kelly/win-rate math, hardcoded numeric literals (aside from test fixtures), or execution endpoints are bypassed.
