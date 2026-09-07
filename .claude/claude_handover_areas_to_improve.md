# Claude Handover: Areas to Look At and Improve

This document summarizes the current state of the Stockpy pipeline after a recent 4-agent remediation pass, and highlights key open gaps and technical debt areas for Claude to tackle next.

## Recent Fixes (2026-09-07)
The following known issues were successfully closed in the latest multi-agent pass:
1. **Forecast Universe Discrepancy**: The forecast and validation universes were decoupled from hardcoded legacy lists and dynamically wired into `compute_tracked_universe`.
2. **Robinhood MCP Safety Violation**: Removed the third-party Robinhood execution skill, enforcing the advisory-only design rule.
3. **Zero DTE 15:45 ET Hard-Exit Gap**: Implemented the `maybe_trigger_0dte_exits` primitive in the `_timer_loop` of `desktop/daemon_runtime.py`, ensuring the hard exit triggers exactly once per day at 15:45 ET.
4. **Exception Masking**: Repaired silent failures in network I/O during universe resolutions, enforcing Constraint #3 (Fail closed) and Constraint #6 (Secrets/errors properly recorded).

## Priority Areas to Improve (Open Gaps Backlog)

### 1. Cross-Agent Audit Trail
- **The Issue**: There are several per-surface durable audit stores (e.g., `decision_log.py`, `execution_audit_store.py`, `run_history_store.py`, `jules_dispatched.jsonl`), but they do not share a schema. There is no unified mechanism correlating which specific agent (Claude Code, Antigravity, or Jules) took which action.
- **Goal**: Implement a unified event schema or inject an `agent_name` / `actor_id` field across all existing persistence stores to make cross-agent operations fully traceable.

### 2. Per-Agent Identity & Least Privilege
- **The Issue**: Currently, the platform token-gates access using single shared API-surface tokens (like `FOLLOW_API_TOKEN` or `ORCHESTRATOR_DAEMON_TOKEN`). There is no per-caller subject claim. An action taken by one compromised agent is indistinguishable from any other caller.
- **Goal**: Transition from shared secrets to a per-agent identity model. Issue distinct identities for Claude, Antigravity, and Jules with granular, least-privilege scoping.

### 3. Jules Dispatch `confirm=True` Prose-Gate
- **The Issue**: As documented in `docs/JULES_INTEGRATION.md`, Jules's `confirm=True` dispatch gate relies on prompt/skill prose. There is no hard code-level interception point to physically assure that a human reviewed the exact prompt prior to dispatch.
- **Goal**: Investigate if a physical wait/confirm intercept can be implemented at the HTTP/SDK layer when interacting with Jules, rather than trusting the LLM to follow the instructions in the prompt.

### 4. Recalculate Strategy Validations
- **The Issue**: Due to a `yfinance` network outage during the recent fix, the `forecast_direction_arima_hw` validation metrics (Sharpe, MaxDD, PBO, DSR) across the newly widened universe could not be recomputed and remain marked as "Unvalidated".
- **Goal**: Re-run the `validation.harness` on the widened universe once the network outage is resolved and formally update `STRATEGY_REGISTRY` and the Validation Log.

## Handoff Instructions
Please review the codebase against these gaps. Start with the **Cross-Agent Audit Trail** and **Per-Agent Identity** to lock down the multi-agent operating environment.
