# Agentic Trading Safety Framework

## General framework
> **Note**: This section summarizes general industry background on agentic trading safety from a third-party research briefing, not a claim about this repository's current state.

A robust agentic trading system architecture comprises perception, memory, reasoning, and action layers. The seven enterprise AI guardrail categories typically include:
1. **Input/Output filtering**: Defending against prompt injections and data loss prevention (DLP).
2. **Tool use / API boundaries**: Enforcing least privilege and human-in-the-loop checkpoints.
3. **Deterministic limits**: Placing rigid, hard-coded boundaries (e.g., volume and price caps) that AI cannot override.
4. **State validation**: Mathematically or logically verifying the outcomes of proposed actions.
5. **Model alignment**: System instructions that ensure refusal of out-of-bounds or unsafe requests.
6. **Observability**: Creating rich audit trails, logging, and performance metrics.
7. **Human override**: Providing manual kill switches and pause mechanisms.

An effective system heavily relies on "deterministic limits" which place rigid bounds on trading operations without AI intervention. It also leverages kill-switch layering to halt trading at different severity levels (e.g. halting new positions vs. flatting existing positions). The RTO (Return to Operations) prompting playbook specifies how agents should handle recovery and context-reset after an intervention. Research-reproducibility caveats warn against the difficulty of reproducing exact AI actions, emphasizing the need for deterministic fallbacks.

## Refreshed Stockpy capability map

This map supersedes the previous 2026-07-18 synthesis, reflecting updates up through Jules integration, options-desk automation, and settings revisions.

| Capability | Status | Key files / settings |
|---|---|---|
| Copy-a-strategy Pilots + follow/mirror rebalance | **Production-ready** | `pilots/catalog.py`, `pilots/mirror.py`, `pilots/follows_store.py` |
| Holding-aware recommendations | **Production-ready** | `engine/advisory.py` |
| Advisory-only adaptive-cadence agent loop policy | **Partial** | `engine/advisory_agent.py` |
| Gated dry-run order queue → human execution | **Production-ready, gated** | `execution/queue_builder.py`, `.claude/skills/robinhood-execution/SKILL.md` (or `.agents/skills/...`) |
| Decision journal | **Production-ready** | `gui/decision_log.py` |
| Paper trading book | **Production-ready** | `transactions_store.py` |
| Opportunity discovery via broker scans | **Production-ready** | `.agents/skills/agentic-discovery/SKILL.md`, `pilots/discovery.py`, `pilots/scan_config_store.py` |
| Consolidated agent command-center UI | **Production-ready** | `webapp/src/screens/AgenticTrading.tsx` |
| Autonomous placement w/o per-trade human gate | **Intentionally absent** | Blocked by AST guard `tests/test_pipeline_smoke.py::TestNoOrderFunctions` |
| Robinhood device-approval login | **Production-ready** | `api/_rh_login.py`, `data/robinhood_login.py` |
| Third-party autonomous coding agent (Jules) | **Production-ready** | `docs/JULES_INTEGRATION.md`, `data/jules_client.py`, `settings.py::JULES_ENABLED` |
| Options Desk Automation (Phases 19-36) | **Production-ready** | `settings.py` (`PAPER_OPTIONS_AUTO_EXECUTE_ENABLED`, `OPTIONS_0DTE_ENABLED`, etc.) |
| Runtime settings read/write store | **Production-ready** | `settings.py` |

## Guardrail-by-guardrail status

1. **Input/Output filtering (Prompt injection defense)**
   - **Implemented**. Untrusted external news headlines in `llm/research.py` and `llm/commentary.py` are explicitly fenced in `<headline>` and `<research_context>` tags. The system prompts explicitly instruct the model to ignore any instructions embedded within these tags (`docs/known_issues/llm_prompt_injection_undelimited_headlines.md`).

2. **Tool use / API boundaries (Least privilege)**
   - **Partially implemented**. The platform token-gates access by surface using API tokens (e.g., `require_read_token`, `require_write_token`, `make_command_token_guard` in `api/auth.py`, and `settings.py::FOLLOW_API_TOKEN` / `ORCHESTRATOR_DAEMON_TOKEN`). Additionally, AST Guards (`tests/test_pipeline_smoke.py::TestNoOrderFunctions`, `tests/test_pilots_api.py`, `tests/test_pilots_strategy_matrix.py`) structurally prevent any order placement logic outside of `execution/`. However, there is no per-agent identity or granular least-privilege model across Claude, Antigravity, and Jules.

3. **Deterministic limits**
   - **Implemented**. The repository eschews fixed-dollar caps (e.g., "$25/trade") in favor of dynamic, percentage-based, and correlation-driven limits checked synchronously in `execution/risk_gate.py::PreTradeRiskGate`:
     - **Position Size Cap:** `max_position_size_check` uses `settings.py::MAX_POSITION_WEIGHT` (Default: 1.0 or 100%).
     - **Portfolio Heat Cap:** `portfolio_heat_check` uses `settings.py::MAX_PORTFOLIO_HEAT` (Default: 0.06 or 6%).
     - **Concentration/Correlation Cap:** `max_correlation_check` uses `settings.py::MAX_CORRELATION` (Default: 0.85).
     - **Daily Loss Limit:** `daily_loss_limit_check` uses `settings.py::DAILY_LOSS_LIMIT_PCT` (Default: 0.02 or 2%).
     - **Order Rate Circuit Breaker:** `max_order_rate_check` uses `settings.py::MAX_ORDER_RATE_PER_MIN` (Default: 10 orders per 60 seconds).
     - **HMM Regime Check:** `hmm_regime_check` uses `settings.py::HMM_RISK_OFF_BLOCK_THRESHOLD` (Default: 0.80 probability).
     - **Macro Kill Switch:** `macro_kill_switch_check` uses `settings.py::MACRO_REGIME_GATE_ENABLED` (Default: True). This properly fails closed during missing FRED data (`docs/known_issues/macro_killswitch_fail_open_on_missing_fred_data.md`).

4. **State validation**
   - **Implemented**. The queue evaluates intents dynamically and limits operations before passing to execution via `execution/queue_builder.py`.

5. **Model alignment**
   - **Partially implemented**. Guardrails rely heavily on specific skill instructions and robust prompt construction, though alignment is mostly governed through system-prompt rules rather than custom model tuning.

6. **Observability**
   - **Partially implemented**. Output directories are flushed with decision logs and executions, but there is no cross-agent audit trail correlating which agent (e.g., Claude Code, Antigravity, Jules) triggered which action.

7. **Human override (Kill switches)**
   - **Implemented**. `execution/kill_switch.py::GlobalKillSwitch` enforces a file-sentinel kill switch offering a `KILL_SWITCH` (hard halt blocking all orders) and `SOFT_HALT` (blocks new BUY orders but allows risk-reducing SELLs).
   - **The Guardrail Imperative (Prose gates vs. Code gates)**: As explicitly documented in `docs/known_issues/robinhood_confirmation_gate_is_prose_only.md`, the "one-confirmation-per-order" gate for Robinhood execution is enforced by prose in the `.claude/skills/robinhood-execution/SKILL.md` skill, not by a code-level hook, as the Robinhood MCP is a third-party server. Similarly, `docs/JULES_INTEGRATION.md` §4 explicitly discloses that Jules's `confirm=True` dispatch gate is a one-shot parameter without strong code-level assurance that a human reviewed the exact prompt prior to dispatch.

## Open-gaps backlog

- **No cross-agent audit trail**: There is no logging mechanism that correlates which specific agent (Claude Code, Antigravity, or Jules) took which action.
- **No per-agent identity/least-privilege model**: The platform token-gates by API surface (`api/auth.py`), leaving no distinction in identity or permission tiers among the different agents.
- **Prose-only confirmation gates**: The Robinhood execution per-order confirmation and the Jules `confirm=True` gate are enforced solely by LLM prompt instructions and skill prose, lacking robust code-level runtime guarantees.
