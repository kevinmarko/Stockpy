# Robinhood Four-Agent System — Phase 1 Walkthrough (Trader approval-aware)

Plan: `.claude/robinhood_four_agent_trading_implementation_plan.md` (Phase 1 row).
Operator OK: "approvals are on in my app, start phase 1" (2026-11-02), given during
the step-7 feature freeze.

## What was verified before building

- `get_accounts`: exactly one account is agentic-accessible (nickname "MCP").
- `get_trade_approval_setting` on it: `human_must_approve_trades: true`, last changed
  2026-10-13. No order tool was called while building or testing this.

## What changed

| File | Change |
|---|---|
| `execution/receipts_store.py` | `make_ref_id(client_order_id)` (uuid5, fixed namespace, `None` for blank — never random); ledger rows gain optional `approval_id`/`ref_id`/`run_id`; new append-only `execution_approvals.jsonl` with `append_approval_event`, `read_approval_events`, `latest_approval_states`, `pending_approval_ids`, `normalise_approval_state`; `reconcile()` adds `not_approved` and `awaiting_approval` buckets. |
| `.claude/skills/robinhood-execution/SKILL.md` + `.agents/` copy | New "Robinhood trade-approval mode" section; per-order path choice from `review_equity_order` (`customer_approval_required: true` + empty `order_checks` → phone tap is the confirmation, else the unchanged in-chat question); approval-setting read and pending-approval refresh in step 2; deterministic `ref_id` on every placement; ledger writes through the Python helpers; new step 6 "Track approvals"; stale-approval decline only with the operator's OK; four new invariants. Also fixed a pre-existing wrong step reference (5b → 5c). |
| `.claude/commands/rh-execute.md` | Mirrors the approval path and tracking step. |
| `.claude/agents/rh-trader.md` | New Agent 4 definition: equity-only Robinhood tools (both server-name spellings), no Write/Edit, defers to the skill. |
| `tests/test_receipts_store.py` | +23 tests (ref_id determinism and pinned namespace, state normalisation, approvals log, reconcile buckets, declined blocks same-day resubmit). |
| `tests/test_robinhood_e2e.py` | Mock MCP gains approval mode (ref_id dedup, `get_trade_approvals`, `customer_acts`); driver follows the new per-order path and tracking step; `TestTradeApprovalMode` (7 tests); 11 new pinned SKILL.md phrases. |
| `tests/test_rh_agent_tool_allowlists.py` | New: only rh-trader may list `place_*`/`cancel_*`/`exercise_*`/`decline_*` tools. |
| Docs | `docs/architecture/execution.md`, `docs/architecture/testing.md`, `docs/RUNBOOK.md`, `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md`, `docs/known_issues/robinhood_confirmation_gate_is_prose_only.md`, `CLAUDE.md`/`AGENTS.md`, the plan. |

## Design decisions worth knowing

1. **Path per order, not per session.** The approval setting can change at any time, so
   the skill trusts only the current order's `review_equity_order` response. Any doubt →
   the in-chat question (the stricter path).
2. **A ledger line now means "submitted for approval".** Keeping it in the existing
   ledger makes the `dedup_key` idempotency check cover approval requests too, so a
   declined order can't be resubmitted the same day.
3. **Approval outcomes are a separate append-only log.** The ledger stays one line per
   submission; state changes (pending → approved/declined/expired) are events. The last
   event per id is current.
4. **Unknown is unknown.** Robinhood's exact state strings aren't documented beyond the
   five names, so unmapped values become `"unknown"` with `state_raw` kept (CONSTRAINT #4).
5. **Two idempotency layers.** `dedup_key` in our ledger (day-wide), plus Robinhood's
   own `ref_id` dedup — covered by a test that deletes the ledger and re-runs. The
   `ref_id` layer only spans one queue file: `client_order_id` is time-bucketed, so a
   refreshed queue gives the same trade a new `ref_id`. The ledger is the real guard.

## Not done in Phase 1 (deliberately)

- No `ROBINHOOD_AGENTIC_ACCOUNT_NUMBER` setting (the skill still confirms the account
  per session).
- No Antigravity mirror of `rh-trader` (its agent format has no documented way to name
  MCP tools).
- Agents 1–3, the `/rh-agentic-cycle` coordinator, scheduling and the webapp card are
  Phases 2–4.

## Verification

See the PR description for the exact commands and results.

## Next live step for the operator

Run `/rh-execute` with `ROBINHOOD_EXECUTION_MODE=review` first and confirm the narration
says approvals are on. Then, in `live` with a small `ROBINHOOD_MAX_NOTIONAL_PER_ORDER`
and limit orders, check that one order arrives on the phone and that approve, decline
and expire each land in `execution_approvals.jsonl`. Also record which exact field names
and state strings Robinhood returns (`approval_id`, the states), since those aren't
documented and the code tolerates but does not yet pin them.
