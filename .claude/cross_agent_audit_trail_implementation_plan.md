# Cross-Agent Audit Trail — Implementation Plan

## Status: SCOPED, NOT YET APPROVED FOR BUILD

Written from a dedicated research pass against live code (2026-09-07). No code has been
written. This is the artifact to review/edit before any implementation begins.

## 0. Problem statement, precisely stated

Handover-doc framing: "several per-surface durable audit stores exist, but they lack a
unified schema to track which agent took what action." Research confirmed this is real, but
narrower and more specific than that one sentence implies — see §1.

**Three senses of "agent" exist in this codebase and must not be conflated:**
1. **Coding agent** (Claude Code, Antigravity, Jules) — what this plan is actually about.
2. **Trading strategy/pilot** (`strategy_id`, `pilot_id`, `experiment_arm`) — an unrelated,
   already-established concept. Several stores' columns are literally named `strategy_id`
   but happen to be populated with a hardcoded coding-agent marker today (see §2) — this is
   the exact conflation to stop extending, not build further on.
3. **The platform's own internal advisory/recommendation LLM** (`llm/research_copilot.py`,
   `rlhf_calibration_store.py`) — an "AI" but neither a coding agent nor a trading strategy.

## 1. Real inventory (from the 2026-09-07 research pass — verify freshness before building,
   this list can drift)

Full per-store detail (schema, existing actor-ish fields, exact write call sites, migration
complexity) is preserved in this plan's own research notes below (§7). Headline findings:

- **Most durable stores are Tier C** (pure orchestrator/pipeline telemetry — no coding-agent
  invocation path exists at all): `validation_history_store.py`, `run_history_store.py`,
  `cap_audit_store.py`, `sector_correlation_store.py`, `broker_fills_store.py`,
  `paper_account_store.py` (mostly), `trends_store.py`, `symbol_rating_store.py`,
  `forecast_tracker.py`, `historical_store.py`, `cache_store.py`. Adding `agent_name` to
  these would be schema noise — there is no agent to attribute.
- **Genuinely coding-agent-triggerable (Tier A) stores — the real scope of this plan**:
  - `transactions_store.py`'s `trades` table — written directly by
    `investyo_mcp_server.py::execute_paper_trade`.
  - `execution/live_trade_proposals_store.py`'s `live_trade_proposals` — written by
    `broker_live_execution_mcp.py::execute_live_trade`/`confirm_live_trade`. **Already has a
    `strategy_id` column hardcoded to the literal `"mcp-agent"` for every caller** — the
    closest existing precedent, and the clearest evidence of the conflation to fix, not
    extend.
  - `data/execution_audit_store.py`'s `execution_audit_records` — **mixed**: reachable both
    from an agent-triggered live-trade confirmation and from the daemon's own automated
    order flow, with no way today to tell which produced a given row.
  - `output/jules_dispatched.jsonl` — already a coding-agent-dispatch record, just missing
    which agent (Claude Code vs. Antigravity vs. a future one) dispatched it.
  - `output/execution_receipts.jsonl` / `execution_placed.jsonl` — the `robinhood-execution`
    skill's own shell-appended lines. Adding `agent_name` here is a **skill-prompt change**,
    not a schema change (no Python writer function even exists for the receipts file).
  - `pilots/scan_config_store.py` (`scan_configs.json`), `prompt_registry/store.py`
    (registry.json) — agent-written config state, not really "audit logs," but worth a
    passing note in scope discussion.
- **No shared base class exists.** Every SQL store independently declares its own
  `Base = declarative_base()` — this is a copy-paste convention (own `Base`/table/
  `session_scope`/`readonly=True`), not an inherited mixin. A schema addition must be made
  once per store, not once globally.
- **MCP protocol reality**: no in-repo FastMCP tool (`investyo_mcp_server.py`,
  `broker_live_execution_mcp.py`, `skills/discovery_skill.py`) accepts a `ctx: Context`
  parameter today. The SDK supports one (`mcp.server.fastmcp.Context`, auto-injected,
  excluded from the tool's exposed JSON schema — adding it is a body-only change, not a
  calling-convention break) and can expose `clientInfo.name` from the MCP `initialize`
  handshake via `ctx.session._client_params`. This is the one real, low-cost lever available
  for "does the MCP layer know which client is calling" — but it is unverified whether
  Claude Code's/Antigravity's stdio clients populate `clientInfo.name` distinctly and
  reliably (see §3, open question).
- **Migration pattern to reuse**: `data/execution_audit_store.py::_migrate_add_nbbo_available_column`
  and `data/paper_account_store.py`'s migration helpers — a tolerant
  `ALTER TABLE ... ADD COLUMN ... DEFAULT` wrapped in try/except swallowing the
  duplicate-column error, portable across SQLite/Postgres. This is the template; it must be
  hand-copied per store (no shared migration helper exists), ~5 stores in scope (§1's Tier A
  list minus the two flat-file ledgers).
- **Test isolation**: any mechanism that stamps `agent_name` automatically inside a shared,
  widely-called write helper needs a new/extended `conftest.py` autouse DB-isolation fixture
  (mirroring `_isolate_execution_audit_db_in_tests`) — a mechanism that only takes effect at
  explicit agent-specific call sites (the MCP tool bodies themselves) likely does not, since
  those aren't the implicit/widely-called paths that fixture pattern guards against.

## 2. Explicit scope boundary

**In scope**: an additive, nullable `agent_name` (or similarly named) field on the
handful of genuinely coding-agent-triggerable stores identified in §1, populated at the
specific MCP tool / skill call sites that already exist — not a platform-wide schema
migration, not a new cross-store join table, not a change to any Tier C store.

**Out of scope, deliberately**:
- Any Tier C (pure pipeline/orchestrator) store — there is no agent to attribute there.
- Reusing or overloading `strategy_id`/`pilot_id` for this purpose (the opposite of what
  this plan should do — see the `live_trade_proposals_store.py` conflation above).
- A genuine per-caller ACCESS-CONTROL model — that's a separate, larger effort (see the
  companion `per_agent_identity_implementation_plan.md`); this plan is attribution/telemetry
  only, and should say so explicitly rather than imply it closes any security gap.
- A unified cross-store schema/join — given no shared base class exists and each store has
  its own migration risk profile (`paper_account_store.py` especially, see §1), forcing one
  schema now is higher-risk than it's worth. Recommend per-store additive columns instead.

## 3. Open questions for the operator — resolve before implementation

- **Where does `agent_name` actually come from at each call site?** Candidates: (a) a
  request-body parameter each MCP tool call must now pass explicitly (a real, if small,
  calling-convention change for every agent invoking these tools); (b) `ctx.session`'s MCP
  `clientInfo.name` via the SDK's `Context` injection (zero calling-convention change, but
  unverified whether Claude Code's/Antigravity's stdio MCP clients populate this reliably —
  **needs a live smoke test with real Claude Code + Antigravity sessions before committing
  to this as the source**, not assumed to work from SDK docs alone); (c) an environment
  variable the invoking session sets (would need to be introduced, and hooks confirm no
  such variable currently exists — see the identity plan's §1 for the parallel finding).
  Recommend piloting (b) first since it's zero-calling-convention-change, with (a) as the
  documented fallback if the live smoke test shows `clientInfo.name` is empty/unreliable.
- **Full list of stores to actually touch**: confirm the 5-store list in §1 (`trades`,
  `live_trade_proposals`, `execution_audit_records`, plus the two flat-file ledgers) is
  complete and current — re-verify against live code, not this plan's snapshot, since new
  agent-triggerable write paths may have been added since 2026-09-07.
- **Fix the `strategy_id="mcp-agent"` conflation now, or leave it and add a parallel
  `agent_name` column?** Recommend: add `agent_name` as a new column, leave `strategy_id`
  alone (changing its semantics now is a separate, riskier migration this plan doesn't need).

## 4. Phases

**Phase 1** — Add `agent_name: Optional[str]` (nullable, additive `ALTER TABLE`) to
`transactions_store.py`, `live_trade_proposals_store.py`, `execution_audit_store.py`,
following each store's own existing migration-helper pattern (copy
`_migrate_add_nbbo_available_column`'s shape). Add `agent_name` to
`output/jules_dispatched.jsonl`'s `_record_dispatch()` (this one's a pure JSON-line addition,
no migration needed). Wire the chosen §3(a/b) source into each of the ~4 real call sites
(`execute_paper_trade`, `execute_live_trade`/`confirm_live_trade`, `dispatch_jules_task`).
Default/backfill: `NULL`/`"unknown"` for pre-existing rows — never fabricate a historical
value (CONSTRAINT #4).

**Phase 2** — Update the `robinhood-execution` skill's prose (`.claude/skills/`+`.agents/`
mirrors) to append `agent_name` to `execution_receipts.jsonl`/`execution_placed.jsonl` lines.
Skill-prompt change, not code — verify via the skill's own pinned-invariants test pattern if
one exists for this file.

**Phase 3 (optional)** — A small read-only cross-store query/report tool (e.g. a new MCP tool
or `pilots/*.py` read helper) that surfaces "what has agent X done across these stores
recently" for operator visibility. Only build this if Phase 1/2 land and the operator finds
manually querying the 4 stores individually genuinely inconvenient — don't build a dashboard
speculatively.

## 5. Testing

- New/extended `conftest.py` autouse fixture if Phase 1's write path is implicit/widely-called
  (per §1's test-isolation finding) — confirm which stores actually need this before writing
  a fixture nobody needs.
- Unit test per store: writing a row with `agent_name` set, and a pre-existing-row read still
  returning `agent_name=None` (never a fabricated backfill value).
- If (b) (`ctx.session.clientInfo.name`) is chosen: an integration test is not fully possible
  without a real MCP client round-trip; document this honestly as a live-smoke-test
  requirement, not something offline pytest can fully cover.

## 6. Documentation sync required

- `CLAUDE.md`/`AGENTS.md` — new bullet following the existing per-feature changelog
  convention, explicitly stating this is attribution/telemetry, not access control (cross-
  reference the per-agent-identity plan's honest non-containment finding so the two don't
  get conflated by a future reader).
- `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md` — update its "No cross-agent audit trail" open-
  gap entry to reflect what actually shipped.
- `docs/architecture/execution.md` (or wherever `execution_audit_store.py`/
  `live_trade_proposals_store.py` are documented) — note the new column.

## 7. Full research notes (preserved verbatim from the 2026-09-07 scoping pass)

See this plan's originating research — the complete per-store inventory table, the MCP
`Context`/`clientInfo` SDK finding, and the `.claude/hooks/`/`.agents/hooks/` no-existing-
signal finding are summarized in §1 above; ask the scoping session's transcript or re-run the
same research pass if line-level detail is needed and this summary is insufficient.
