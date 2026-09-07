# Cross-Agent Audit Trail — Task Tracker

> Companion to `cross_agent_audit_trail_implementation_plan.md`.

## §0 — Confirm before building
- [ ] Re-verify the Tier A store list (§1 of the plan) against live code — it may have
      changed since 2026-09-07
- [ ] Live smoke test: does `ctx.session._client_params.clientInfo.name` actually populate
      reliably for a real Claude Code stdio session and a real Antigravity session? Decide
      §3's (a) vs (b) based on this, not assumption
- [ ] Confirm the 3 SQL stores + 1 JSONL ledger in scope for Phase 1

## Phase 1 — Schema + call-site wiring
- [ ] `transactions_store.py` — add `agent_name` column + migration helper
- [ ] `execution/live_trade_proposals_store.py` — add `agent_name` column + migration helper
      (leave `strategy_id="mcp-agent"` as-is, do not repurpose it)
- [ ] `data/execution_audit_store.py` — add `agent_name` column + migration helper
- [ ] `output/jules_dispatched.jsonl`'s `_record_dispatch()` — add `agent_name` field
- [ ] Wire chosen identity source into `execute_paper_trade`, `execute_live_trade`/
      `confirm_live_trade`, `dispatch_jules_task`
- [ ] Pre-existing rows: `NULL`/`"unknown"`, never a fabricated backfill

## Phase 2 — robinhood-execution skill
- [ ] Update `.claude/skills/robinhood-execution/SKILL.md` (+ `.agents/` mirror if it exists)
      to append `agent_name` to `execution_receipts.jsonl`/`execution_placed.jsonl` lines

## Phase 3 — optional, only if Phase 1/2 prove genuinely inconvenient to query manually
- [ ] Cross-store "what has agent X done recently" read helper/MCP tool

## Testing
- [ ] Per-store unit test: write with `agent_name` set, pre-existing row reads back `None`
- [ ] New/extended `conftest.py` autouse fixture IF the write path is implicit/widely-called
- [ ] Document the MCP-client-identity live-smoke-test requirement honestly (not fully
      pytest-coverable)

## Doc sync
- [ ] `CLAUDE.md`/`AGENTS.md` bullet — explicitly state this is attribution, not access
      control
- [ ] `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md` — update the open-gap entry
- [ ] `docs/architecture/execution.md` (or equivalent) — note new columns

## Explicitly NOT in this task list
- Any Tier C (pure pipeline/orchestrator) store
- Repurposing `strategy_id`/`pilot_id` for coding-agent identity
- A genuine per-caller access-control model (see `per_agent_identity_implementation_plan.md`)
- A unified cross-store schema/join table
