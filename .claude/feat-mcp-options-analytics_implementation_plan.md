# InvestYo MCP — Agent 1 (Options Analytics Desk)

This plan covers Phase 1 for Agent 1 in the `feat-mcp-options-analytics` branch (`../Stockpy-live-agent1`).

## Goal Description
Implement two new MCP tools in `investyo_mcp_server.py`:
1. `analyze_options_chain`: Fuses live options-chain Greeks, volatility surface/VRP cone, and rich/cheap strike scans into one call.
2. `simulate_0dte_payoff`: Simulates a same-session 0DTE contract's payoff/theta-decay using existing logic. Ships in simulation-only mode (as decided in the master plan) with explicit status fields for safety.

## Proposed Changes

### `investyo_mcp_server.py`
We will add two new functions decorated with `@mcp.tool(annotations={"readOnlyHint": True, ...})`:

#### [MODIFY] [investyo_mcp_server.py](file:///Users/kevinlee/Stockpy-live-agent1/investyo_mcp_server.py)
- Add `analyze_options_chain(ticker: str, target_dte: int = 30) -> dict`
  - Wrap `pilots.options_risk.calculate_position_greeks`
  - Wrap `pilots.volatility_surface.calculate_volatility_surface`
  - Wrap `pilots.vol_mispricing.evaluate_strike_mispricing`
  - Combine and format results safely (degrade to `NaN`/`None` on failure per CONSTRAINT #4/#6).
- Add `simulate_0dte_payoff(ticker: str, contracts: int = 1) -> dict`
  - Delegate to `pilots.zero_dte_engine` (e.g. `simulate_trade` or similar analysis logic).
  - Include explicitly: `"live_exit_gate_wired": False` and `"strategy_registry_status": "unregistered"` per the safety prerequisites in the master plan.

### Tests
#### [NEW] [test_investyo_mcp_options_analytics.py](file:///Users/kevinlee/Stockpy-live-agent1/tests/test_investyo_mcp_options_analytics.py)
- Add targeted tests verifying:
  - Both tools return gracefully on missing data (mocked).
  - `simulate_0dte_payoff` honestly reports the hardcoded fallback statuses (since the gate is not wired).
  - Neither tool calls `execute_0dte_trade` or `execute_0dte_exits`.

### Documentation & PR Artifacts
- **[NEW]** `.claude/mcp_options_analytics_implementation_plan.md`
- **[NEW]** `.claude/mcp_options_analytics_task.md`
- **[NEW]** `.claude/mcp_options_analytics_walkthrough.md`
- **[MODIFY]** `docs/architecture/observability-and-apis.md` (Add bullets for both tools)
- (Note: `docs/architecture/execution.md` was originally planned to be updated but was dropped because the tools are read-only analytics passes and do not change the execution path.)

## User Review Required

> [!IMPORTANT]
> Since you want me to act as the builder agents here in the IDE:
> I will be executing this work strictly within the `/Users/kevinlee/Stockpy-live-agent1` worktree to keep the PRs cleanly separated as originally planned. 

Please approve this plan so I can begin execution for Agent 1. Once Agent 1 is complete and verified, we will move on to Agent 2.
