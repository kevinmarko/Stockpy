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
| Advisory-only adaptive-cadence agent loop policy | **Partial** — wired only into `main.py`'s legacy `--agent` subprocess loop; the daemon backend (`desktop/daemon_runtime.py`) imports only the narrower `is_automatic_run_gated` helper, not the backlog-reminder/cadence policy itself | `engine/advisory_agent.py` |
| Gated dry-run order queue → human execution | **Production-ready, gated** | `execution/queue_builder.py`, `.claude/skills/robinhood-execution/SKILL.md` (or `.agents/skills/...`) |
| Decision journal | **Production-ready** — despite the `gui/` path, this module has no Streamlit dependency and is the live backing store read by `api/pilots_api.py`/`pilots/calibration.py` and surfaced in `webapp/src/screens/Calibration.tsx`, not part of the decommissioned Streamlit Command Center | `gui/decision_log.py` |
| Paper trading book | **Production-ready** | `data/paper_account_store.py` (virtual cash/positions/order history for the default `settings.BROKER_BACKEND="fmp_paper"` engine); `transactions_store.py` is a separate closed-trade ledger feeding Kelly sizing/evaluation, not the paper book itself |
| Opportunity discovery via broker scans | **Production-ready** | `.claude/skills/agentic-discovery/SKILL.md` (or `.agents/skills/...`), `pilots/discovery.py`, `pilots/scan_config_store.py` |
| Consolidated agent command-center UI | **Production-ready** | `webapp/src/screens/AgenticTrading.tsx` |
| Autonomous placement w/o per-trade human gate | **Intentionally absent** | Blocked by AST guard `tests/test_pipeline_smoke.py::TestNoOrderFunctions` |
| Robinhood device-approval login | **Production-ready** | `api/_rh_login.py`, `data/robinhood_login.py` |
| Third-party autonomous coding agent (Jules) | **Production-ready, opt-in** (`JULES_ENABLED` default `False`) — built and verified entirely offline per `docs/JULES_INTEGRATION.md`; no live Jules API call has ever been made or is possible in this sandboxed environment | `docs/JULES_INTEGRATION.md`, `data/jules_client.py`, `settings.py::JULES_ENABLED` |
| Options Desk Automation (Phases 19-36) | **Partial** — 0DTE lifecycle management (`manage_0dte_exits`) is wired into the daemon's `_timer_loop`; `PAPER_OPTIONS_AUTO_EXECUTE_ENABLED`'s exit-management, new-position auto-execution, and delta-hedging behaviors run only via the legacy `main.py` subprocess loop, with **no** `main_orchestrator.py`/`desktop/daemon_runtime.py` equivalent | `settings.py` (`PAPER_OPTIONS_AUTO_EXECUTE_ENABLED`, `OPTIONS_0DTE_ENABLED`), `docs/known_issues/options_lifecycle_daemon_gate_gap_2026_08_22.md` |
| Runtime settings read/write store | **Production-ready** | `runtime_flags.py` (read path), `runtime_flags_writer.py` (write path), `settings.py` (schema) |

## Guardrail-by-guardrail status

1. **Input/Output filtering (Prompt injection defense + DLP)**
   - **Implemented, bounded.** Untrusted external news headlines in `llm/research.py` are explicitly fenced in `<headline>` tags; `llm/commentary.py`'s downstream research-context block (itself derived from the same untrusted headlines) is separately fenced in `<research_context>` tags, with a matching system-prompt instruction to ignore any embedded directives (`docs/known_issues/llm_prompt_injection_undelimited_headlines.md`). This is defense-in-depth, not a hard technical guarantee against a sufficiently capable adversarial model — the doc itself says so — and is bounded risk mainly because none of the affected LLM output schemas carries a numeric/action field a manipulated headline could corrupt. That same known-issues doc separately discloses that no LLM-asserted numeric/factual claim in the surrounding free text is technically validated against real data either, so "bounded" should not be read as "verified."
   - **DLP**: `api/_redact.py::install_redacting_exception_handler` scrubs credentials/tokens from tracebacks, logs, and SSE streams across all four FastAPI services (`api/data_api.py`, `api/control_api.py`, `api/pilots_api.py`, `api/metrics_api.py`).
   - **A second, separate input-integrity gate** protects remotely-fetched LLM system-prompt content specifically: `prompt_registry/registry.py`'s HMAC-SHA256 signature verification, plus `prompt_registry/guardrails.py::validate_prompt()`'s deny-list (rejects phrases instructing an AI to disable a platform safety gate) and required-markers check (every `master_preprompt` version must name `ADVISORY_ONLY`). `docs/known_issues/prompt_registry_unsigned_remote_adoption.md`'s unsigned-store adoption gap was fixed 2026-08-24, but that same doc's own exploit narrative shows the deny-list is an intentionally narrow literal-phrase list that a rephrased instruction can still slip past — the fix closed the signing gap, not the deny-list's bypassability.

2. **Tool use / API boundaries (Least privilege)**
   - **Partially implemented**. The platform token-gates access by surface using API tokens (e.g., `require_read_token`, `require_write_token`, `make_command_token_guard` in `api/auth.py`, and `settings.py::FOLLOW_API_TOKEN` / `ORCHESTRATOR_DAEMON_TOKEN`). Additionally, `tests/test_pipeline_smoke.py::TestNoOrderFunctions` is the AST guard that structurally prevents any order-placement logic outside of `execution/`; `tests/test_pilots_api.py`'s `test_pilots_api_never_imports_heavy_engines` and `tests/test_pilots_strategy_matrix.py`'s `test_pilots_read_helpers_stay_dependency_light` are separate AST guards enforcing an unrelated invariant — a heavy-engine/dependency import boundary for `api/pilots_api.py`/`pilots/*.py` — not an order-placement check. However, there is no per-agent identity or granular least-privilege model across Claude, Antigravity, and Jules — these tokens are single shared secrets with no per-caller subject claim, so a compromised or misbehaving single agent is indistinguishable, at the token layer, from any other caller holding the same token.

3. **Deterministic limits**
   - **Implemented**. The repository eschews fixed-dollar caps (e.g., "$25/trade") in favor of dynamic, percentage-based, and correlation-driven limits. Seven of `execution/risk_gate.py::PreTradeRiskGate.run_all()`'s 11 sequential checks are enumerated below (not the full list — `dynamic_circuit_breaker_check`, `stress_scenario_check`, `market_hours_check`, and `minimum_validation_check` also run as part of the same gate but aren't classic "limit" checks; see `docs/architecture/execution.md` for the complete, authoritative ordered list of all 11). The real execution order begins with `dynamic_circuit_breaker_check` (Check #0, evaluated before every check below) and continues Position Size → Portfolio Heat → Correlation → Daily Loss Limit → Macro Kill Switch → HMM Regime → ... → Order Rate (deliberately always run **last**, so a blocked order never consumes rate budget):
     - **Position Size Cap:** `max_position_size_check` uses `settings.py::MAX_POSITION_WEIGHT` (Default: 1.0 or 100%).
     - **Portfolio Heat Cap:** `portfolio_heat_check` uses `settings.py::MAX_PORTFOLIO_HEAT` (Default: 0.06 or 6%).
     - **Concentration/Correlation Cap:** `max_correlation_check` uses `settings.py::MAX_CORRELATION` (Default: 0.85).
     - **Daily Loss Limit:** `daily_loss_limit_check` uses `settings.py::DAILY_LOSS_LIMIT_PCT` (Default: 0.02 or 2%).
     - **Macro Kill Switch:** `macro_kill_switch_check` gates on `settings.py::MACRO_REGIME_GATE_ENABLED` (Default: True) and consumes the already-computed `MacroEconomicDTO.killSwitch` flag. The fail-closed-on-missing-or-fabricated-FRED-data behavior is implemented one layer down — in `dto_models.py::MacroEconomicDTO.killSwitch`/`_rules_based_regime`, fed by `macro_engine.py::macro_killswitch_data_unavailable()` — not inside `risk_gate.py` itself. It fails closed both when FRED data is missing outright and when it's silently populated with fabricated placeholder defaults (`docs/known_issues/macro_killswitch_fail_open_on_missing_fred_data.md`, including its 2026-08 follow-up fix for the fabricated-defaults case).
     - **HMM Regime Check:** `hmm_regime_check` uses `settings.py::HMM_RISK_OFF_BLOCK_THRESHOLD` (Default: 0.80 probability).
     - **Order Rate Circuit Breaker:** `max_order_rate_check` blocks once `settings.py::MAX_ORDER_RATE_PER_MIN` (Default: 10) orders have been submitted within the trailing **60-second rolling window** (not a fixed per-minute clock bucket).
   - **A separate, earlier-stage portfolio-level limit** is enforced at sizing time, before an order intent even exists: `sizing/position_sizer.py::apply_portfolio_gross_cap()`, driven by `settings.py::MAX_PORTFOLIO_GROSS` (Default: 2.0 / 200% gross exposure), called from `pipeline/production_steps.py::StrategyEvalStep`. It is not itself one of `PreTradeRiskGate`'s checks.

4. **State validation**
   - **Implemented, but not a distinct mechanism from item 3.** `execution/queue_builder.py::gate_intent()` (and its caller `build_execution_queue()`) construct each candidate `OrderIntent` and run it through the same `execution/risk_gate.py::PreTradeRiskGate.run_all()` gate described above, in dry-run, before an intent is ever marked placeable. There is no additional outcome-verification logic in `queue_builder.py` itself — this platform does not currently have a "state validation" guardrail meaningfully separate from "deterministic limits"; the two categories describe the same underlying mechanism viewed from two different call sites.

5. **Model alignment**
   - **Partially implemented**. Alignment is governed almost entirely by skill-level prose (`.claude/skills/robinhood-execution/SKILL.md`, `.claude/skills/jules-delegation/SKILL.md`); there is no custom model fine-tuning anywhere in this platform. `prompt_registry/guardrails.py::validate_prompt()` (deny-lists phrases instructing a model to disable a platform safety gate; requires every `master_preprompt` version to name `ADVISORY_ONLY`) is a related but distinct, non-alignment mechanism — its own docstring states its premise explicitly: *"A fetched prompt can change what an AI is told. It cannot change what the platform is permitted to do."* It is a code-level content boundary that exists precisely because model alignment cannot be relied on, not an instance of alignment itself.

6. **Observability**
   - **Partially implemented**. Per-surface durable audit trails exist — `gui/decision_log.py` (human decision journal), `data/execution_audit_store.py` (SEC Rule 606 execution audit), `desktop/run_history_store.py` (durable pipeline run history), `validation/validation_history_store.py` (backtest/validation run history), `output/jules_dispatched.jsonl` (Jules dispatch ledger) — but none share a common schema or are joined. There is no single query answering "which agent (Claude Code, Antigravity, or Jules) took this specific action" across all of them.

7. **Human override (Kill switches)**
   - **Implemented**. `execution/kill_switch.py::GlobalKillSwitch` enforces a file-sentinel kill switch offering a `KILL_SWITCH` (hard halt blocking all orders) and `SOFT_HALT` (blocks new BUY orders but allows risk-reducing SELLs).
   - **The Guardrail Imperative (Prose gates vs. Code gates)**: As explicitly documented in `docs/known_issues/robinhood_confirmation_gate_is_prose_only.md`, the "one-confirmation-per-order" gate for Robinhood execution is enforced by prose in the `.claude/skills/robinhood-execution/SKILL.md` skill, not by a code-level hook, as the Robinhood MCP is a third-party server with no interception point — unlike the Alpaca/FMP live-execution path, which has a real server-side `pending_approval` durable state (`execution/live_trade_proposals_store.py`). A pinned test (`tests/test_robinhood_e2e.py::TestSkillMdInvariantsPinned`) guards against silent drift in the prose itself, but that is not a substitute for code-level enforcement. Similarly, `docs/JULES_INTEGRATION.md §4` explicitly discloses that Jules's `confirm=True` dispatch gate is a one-shot parameter without strong code-level assurance that a human reviewed the exact prompt prior to dispatch — its append-only dispatch ledger (`output/jules_dispatched.jsonl`) provides same-day dedup, not prompt-level review assurance.

## Open-gaps backlog

- **No cross-agent audit trail**: several per-surface durable audit stores exist (see item 6 above), but none share a schema or are joined — there is no logging mechanism that correlates which specific agent (Claude Code, Antigravity, or Jules) took which action. A first step toward closing this without a schema migration: add an agent-name field to the existing per-surface stores.
- **No per-agent identity/least-privilege model** (see item 2 above): single shared API-surface tokens, no per-caller subject claim.
- **Prose-only confirmation gates** (see item 7 above): Robinhood execution confirmation and the Jules `confirm=True` gate rely on prompt/skill prose, not code-level enforcement.
