# Walkthrough: Agent 2 Overnight Liquidity Tool

## Changes Made
1. **Tool Added**: Implemented `check_overnight_liquidity` in `investyo_mcp_server.py`.
   - The tool takes a `symbol` and returns a Markdown and JSON representation of the symbol's Top-of-Book spread and an approximation of depth notional based on Average Daily Volume (ADV) from `yfinance`.
   - Explicitly added a disclaimer that it's an approximation and no real Level-2 data is used.
2. **Tests Added**: Added `tests/test_investyo_mcp_overnight_liquidity.py` to ensure valid returns and graceful handling of missing market data.
3. **Docs Updated**: Registered `check_overnight_liquidity` under the Advisory & Market Intelligence section in `docs/architecture/observability-and-apis.md`.

## Independent Audit Verification (AGENTS.md Section 6 Checklist)
1. **Re-run tests**: `pytest tests/test_investyo_mcp_overnight_liquidity.py` passed (`2 passed in 2.40s`).
2. **Grep the diff for duplicate Kelly/win-rate formulas, broker-call paths, or hardcoded numeric literals**: No sizing, broker submission code, or hardcoded math was added outside the simple 1% ADV approximation logic in the tool.
3. **Confirm honest status reporting, PR artifact naming, and documentation updates**:
   - `docs/architecture/observability-and-apis.md` is updated.
   - PR artifacts are using unique names per Branch Workflow rule #5.
   - **Specific check**: `check_overnight_liquidity` explicitly states it is an approximation and no claims of real Level-2 data exist. `execution/risk_gate.py` is entirely untouched.

## Sign-off
Audit sign-off complete. All checklist items are covered, and the changes are ready for PR.
