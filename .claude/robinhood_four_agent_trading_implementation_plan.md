# Robinhood Four-Agent Trading System — Implementation Plan

**Status:** Written 2026-11-02 during the step-7 feature freeze. **Phase 1 approved by
the operator and built 2026-11-02** (walkthrough:
`.claude/robinhood_four_agent_phase1_walkthrough.md`; merged as #1134). **Phase 2
approved and built 2026-11-03** (plan `.claude/robinhood_four_agent_phase2_implementation_plan.md`,
walkthrough `.claude/robinhood_four_agent_phase2_walkthrough.md`; merged as #1135).
**Phase 3 approved and built 2026-11-03** (plan
`.claude/robinhood_four_agent_phase3_implementation_plan.md`, walkthrough
`.claude/robinhood_four_agent_phase3_walkthrough.md`). Phases 4–5 still need the
operator's explicit OK each (CLAUDE.md "Feature freeze").

**Operator decisions (2026-11-02):**

| Decision | Choice |
|---|---|
| Where it lives | Inside Stockpy (Claude Code agents/skills on the existing daemon queue + Robinhood MCP). Not DataRobot-hosted. |
| Freeze handling | Plan now, build after the freeze lifts (or on explicit OK). |
| Approval channel | **Robinhood native trade approvals** — the operator taps approve/decline on their phone. |
| Agent roles | Merge of "Scout/Analyst/Risk/Executor" and "Research/Strategy/Portfolio/Trader" → the four roles below. |

---

## 1. Goal

A system that trades stocks in the Robinhood **Agentic** account on its own — finding
candidates, building a thesis, sizing and risk-checking, and submitting orders — where
the **only** human step is approving (or declining) each order in the Robinhood app.

"On its own" means no operator is needed to drive the chain. "With my approval"
means no order can execute without a tap the agents cannot perform.

## 2. The key fact this design rests on

The Robinhood MCP exposes an approval flow for agentic accounts (verified from the tool
schemas, 2026-11-02):

- `get_trade_approval_setting(account_number)` — reads whether trades on the account
  require the customer's manual approval. **Only the customer can change it, in the
  Robinhood app; no tool can turn it off.**
- `place_equity_order(...)` — when `review_equity_order` returns
  `customer_approval_required: true` (and no `order_checks` alert), this call **does
  not execute the order**; it sends it to the customer's app to approve or decline.
- `get_trade_approvals(account_number, approval_ids?)` — lists pending approvals, or the
  state (pending / approved / declined / expired / auto-approved) of specific ids.
  Pending approvals **expire on their own**.
- `decline_trade_approval(account_number, approval_id)` — the agent can decline. **It
  can never approve.**

Why this matters (the teaching point): `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md`
records that every "hard" gate built inside this repo (the `confirm` param, typed-echo
`DANGEROUS_KEYS`, the in-chat "place / skip / stop" step in `robinhood-execution`)
reduces to something a filesystem-privileged agent could reproduce itself. The doc names
exactly one genuine exception: a tap on the operator's own phone, a channel this code
cannot read or write. Robinhood trade approvals are that kind of gate for **order
execution**. The design keeps every existing code gate as defence-in-depth, but the
phone tap is what makes "trades on its own" safe to contemplate.

**Verified 2026-11-02:** `get_trade_approval_setting` on the operator's Agentic account
(the only account this agent can act on) returned `human_must_approve_trades: true`,
last changed 2026-10-13. It can change at any time, so the skill re-derives the path per
order from `review_equity_order` rather than trusting this.

## 3. Architecture

```
 orchestrator daemon (unchanged, headless, no MCP access)
   AdvisoryOverlayStep → AgenticQueueStep → $OUTPUT_DIR/execution_queue.json
                                                 │  (+ ntfy push on new intents)
                                                 ▼
 /rh-agentic-cycle (Claude Code command, coordinates 4 subagents; hand-offs are files)
   ┌─────────────────┐   ┌──────────────────┐   ┌──────────────────────┐   ┌───────────────┐
   │ 1 Research Scout│──▶│ 2 Strategy       │──▶│ 3 Portfolio & Risk   │──▶│ 4 Trader      │
   │ scans, research │   │   Analyst: thesis│   │   Manager: preflight │   │ review+place  │
   │ (read-only)     │   │   (read-only)    │   │   veto, never upsize │   │ → phone       │
   └─────────────────┘   └──────────────────┘   └──────────────────────┘   └──────┬────────┘
     scan_candidates.json   agentic/theses.json    agentic/preflight.json         │
                                                                                  ▼
                                                     Robinhood app: operator taps APPROVE / DECLINE
                                                                                  │
                         execution_receipts.jsonl ◀── Trader polls get_trade_approvals, logs outcome
                         execution_placed.jsonl        receipts_store.reconcile() vs real fills
```

Design principles:

1. **The daemon stays the single source of quant truth.** Agents never compute, change or
   override a score, conviction, Kelly weight or gate verdict. They explain it, check it
   against live broker state, and can veto. They never upsize.
2. **Deterministic checks are code, not agent judgement.** Whatever can be a Python
   function with a test (staleness, caps, concentration, dedup) is one; the agent calls it
   and narrates the result. An LLM's "looks fine" is never a gate.
3. **Hand-offs are files under `$OUTPUT_DIR/agentic/<run_id>/`.** Each agent writes one
   JSON artifact the next one reads. That gives a replayable audit trail per cycle and
   lets any agent be re-run alone.
4. **Least privilege by tool allowlist.** Each subagent's `.claude/agents/*.md` frontmatter
   lists only the tools it needs. Only the Trader has `place_equity_order` /
   `decline_trade_approval`. Honest limit: tool allowlists contain a misbehaving agent by
   accident, not by design. A same-session agent with shell access could still get around
   them, which is exactly why the phone tap is the real gate.
5. **Fail closed (CONSTRAINT #6), never fabricate (CONSTRAINT #4).** Missing data → the
   intent is skipped with a reason, never filled with a default.

## 4. The four agents

### Agent 1 — Research Scout  (Scout + Research)

- **Job:** widen and enrich the funnel. Runs the operator's configured Robinhood scans,
  cross-references hits against the advisory engine, and gathers research context
  (earnings dates, analyst ratings, fundamentals, recent filings and news) for **both**
  new candidates and the symbols already in today's queue.
- **Builds on:** the existing `agentic-discovery` skill and `pilots/discovery.py` (it
  becomes this agent's procedure; it is not rewritten).
- **Tools:** Robinhood read and scan tools (`run_scan`, `get_scans`, `get_equity_quotes`,
  `get_equity_fundamentals`, `get_earnings_calendar`, `get_equity_analyst_ratings`,
  `get_sec_filing_index`), FMP read tools. **No order tools at all.**
- **Writes:** `scan_candidates.json` (existing schema) and
  `agentic/<run_id>/research.json` (per symbol: next earnings date, ratings summary,
  material news with source URL and timestamp; missing → `null` + reason).
- **May not:** add a symbol to `watchlist.txt` without the operator naming it. The
  existing skill rule stays; discovery never silently expands the traded universe
  (`docs/architecture/execution-boundary.md`).

### Agent 2 — Strategy Analyst  (Analyst + Strategy)

- **Job:** turn each queue intent into a short, falsifiable thesis a human can judge in
  about 10 seconds on a phone: why this trade, why now, what would make it wrong.
- **Inputs:** `execution_queue.json` (`conviction`, `rationale`, `strategy`, `sources`,
  `overridden`, `gate_reasons`), signal breakdown and regime status (existing investyo
  MCP / Pilots read endpoints), the forecast bands, and `research.json`.
- **Writes:** `agentic/<run_id>/theses.json`, keyed by `client_order_id`:
  `{thesis, drivers:[{signal, value, weight}], regime, invalidation, horizon, data_gaps:[...]}`.
- **Rules:** every number cited comes from an artifact and is quoted, never recomputed.
  A NaN or missing value shows up in `data_gaps`, not as a guess. The Analyst can flag
  ("thesis contradicts research: earnings in 2 days") but **cannot remove or resize an
  intent**; flags go to Agent 3.
- **Tools:** read-only (files plus read MCP tools). No order tools.

### Agent 3 — Portfolio & Risk Manager  (Risk officer + Portfolio construction)

- **Job:** the last line of code-enforced defence before anything reaches the broker.
  Checks each intent against the **live** Agentic account (not the daemon's snapshot)
  and either passes it with a resolved order or vetoes it with a reason.
- **New code:** `execution/agentic_preflight.py::preflight(queue, account_state, quotes,
  theses, now) -> list[PreflightResult]`, a pure function with no MCP calls and no I/O,
  so it is fully unit-testable. The agent gathers live state via MCP, calls it, and
  narrates the result.
- **Checks** (each produces an explicit reason; any failure = veto):
  1. Kill switch sentinel is absent and the queue's `kill_switch_active` is false.
  2. Queue `generated_at` is at most 30 min old (the existing stale rule).
  3. Intent `allow_place` is true (the daemon's `PreTradeRiskGate` verdict is final;
     never re-litigated upward).
  4. `ROBINHOOD_MAX_NOTIONAL_PER_ORDER` > 0 and `qty × price` ≤ the cap. Quantity is
     resolved from the **live** quote (limit price for limit intents), exactly as the
     current skill does.
  5. Post-trade concentration on the **real** Agentic positions is at most
     `MAX_POSITION_WEIGHT`; the cycle's total new gross is within `MAX_PORTFOLIO_GROSS`.
  6. Buying power is enough for the sum of all passed buys (not just each one alone).
  7. Earnings blackout: no new BUY within N trading days of earnings (N is a new
     setting, default = today's behaviour = no blackout, per the default-value policy).
  8. Dedup: `receipts_store.already_placed()` for today's `dedup_key`, **and** no
     still-pending approval for the same symbol/side (from `get_trade_approvals`).
  9. SELL quantity is no more than the held quantity (no accidental short).
  10. Analyst `data_gaps` include a field the operator has marked critical → veto.
     **(Built in Phase 3 as opt-in**, `critical_gap_fields` / `--critical-gaps`, off by
     default; it also vetoes intents with no valid thesis. Check 7 takes its dates from
     the Scout's `research.json` when no explicit map is passed.)
- **Can (as built in Phase 2):** veto only — the operator chose veto-only over shrink-to-fit
  (plan Q5). It does resolve the exact share count (floor of target ÷ price, trims capped at
  sellable shares), which is sizing per the existing rules, not shrinking.
- **Originally drafted:** veto, or shrink (e.g. round down for buying power). **Cannot:** upsize, add
  an intent, or flip a side.
- **Writes:** `agentic/<run_id>/preflight.json`.

### Agent 4 — Trader  (Executor)

- **Job:** the **only** agent allowed to submit orders. It turns passed preflight results
  into Robinhood approval requests, then tracks them to a final state.
- **Builds on:** `robinhood-execution` (its prerequisites, hard stops, ledgers and
  receipts stay); its confirmation step changes as described below.
- **Procedure per cycle:**
  1. `get_accounts` → the operator-confirmed Agentic account (persisted once in a
     non-secret setting, e.g. `ROBINHOOD_AGENTIC_ACCOUNT_NUMBER`; masked in logs).
  2. **Approval-mode gate:** `get_trade_approval_setting`. If approvals are **not**
     required → **hard stop**: no placement in agentic mode. Tell the operator to enable
     trade approvals in the Robinhood app, or to run the legacy in-chat
     `robinhood-execution` flow by hand. (Fail closed: without the phone gate, nothing
     here is allowed to place autonomously.)
  3. Re-check the kill switch.
  4. For each passed intent: `review_equity_order`. If the response shows
     `customer_approval_required: true` and `order_checks` is empty → `place_equity_order`
     with `ref_id = uuid5(STOCKPY_NS, client_order_id)`. A deterministic ref_id makes a
     retry or re-run against the same queue file idempotent at Robinhood's gateway;
     `client_order_id` is time-bucketed, so across queue refreshes the `dedup_key`
     ledger remains the day-wide guard. If
     `order_checks` is non-empty or `customer_approval_required` is false → do not
     place; log `skipped` with the reason.
  5. Append to `execution_placed.jsonl` and `execution_receipts.jsonl` with new fields
     `approval_id`, `approval_state: "pending"`, `ref_id`, `run_id`.
  6. Send one ntfy push: "3 trades awaiting your approval in Robinhood", with each
     one-line thesis from `theses.json`, so the operator sees the *why* next to the tap.
  7. **Track:** poll `get_trade_approvals(approval_ids=[...])` until each id is
     approved, declined, expired or auto-approved, or until a deadline, then write the
     final state to receipts. `receipts_store.reconcile()` later matches approved ones to
     real fills via `get_equity_orders`.
  8. **Expired or declined approvals are never re-submitted automatically** the same
     day: the `dedup_key` already blocks them, and a decline is an operator decision,
     not a transient error.
  9. **Stale approvals:** if the next cycle's queue no longer contains an intent that is
     still pending (the signal flipped), the Trader tells the operator and offers to
     decline it via `decline_trade_approval`, calling it only after their explicit OK.
     (Corrected in Phase 1: the original draft let the Trader decline on its own, but the
     tool's own contract says "Confirm with the user before calling".)
- **Order type:** recommend `ROBINHOOD_LIMIT_BUFFER_BPS > 0` (limit orders) for this
  mode. Teaching point: with an approval step, minutes can pass between preview and
  execution. A market order executes at whatever the price is when you tap; a limit
  order caps the slippage at the buffer no matter how late you approve.

## 5. How it runs ("on its own")

- **Trigger:** the daemon already pushes an ntfy notification when the queue gains a new
  placeable intent. Phase 4 adds a scheduled Claude Code run (a scheduled task, or a
  local `launchd` job running `claude -p "/rh-agentic-cycle"`) shortly after each
  daemon cycle in regular hours. The chain then runs unattended; the operator only sees
  approval requests arrive on their phone.
- **Coordinator:** `.claude/commands/rh-agentic-cycle.md` runs Agents 1→4 in order,
  passing `run_id`. Any agent failing → the cycle stops at that point (fail closed),
  writes a `cycle_failed` receipt, and pushes one alert.
- **Modes** reuse `ROBINHOOD_EXECUTION_MODE`:
  - `review`: all four agents run; the Trader calls `review_equity_order` only. This is
    the burn-in stage.
  - `live`: the Trader submits approval requests.
  - `off`: the command exits immediately.

## 6. Invariants (pinned by tests)

1. No order executes without a phone approval: the Trader refuses when
   `get_trade_approval_setting` does not report approvals required.
2. Only Agent 4's definition lists `place_equity_order` / `decline_trade_approval`
   (AST/frontmatter test over `.claude/agents/*.md`).
3. Agents never raise size or add intents: `preflight()` output notional ≤ input
   notional, per intent (property test).
4. Every placement has a deterministic `ref_id` derived from `client_order_id`.
5. Kill switch is checked at cycle start and immediately before each `place_equity_order`.
6. Agentic account only, never the main account.
7. Expired or declined approvals are never auto-resubmitted the same day.
8. Every artifact field that is unmeasurable is `null` + reason (CONSTRAINT #4).

## 7. Phases

| Phase | Scope | Verification |
|---|---|---|
| **P0** (now) | This plan. | Operator review. |
| **P1 — Trader approval-aware** ✅ built 2026-11-02 | Update `robinhood-execution` with the approval-mode gate, deterministic `ref_id`, `approval_id`/`approval_state` receipt fields, approval tracking and stale-approval decline. Extend `execution/receipts_store.py` (new fields optional; old rows still parse). Create `.claude/agents/rh-trader.md`. | `tests/test_receipts_store.py`, `tests/test_robinhood_e2e.py::TestSkillMdInvariantsPinned` updated to pin the new invariants; a `review`-mode dry run against the real Agentic account. |
| **P2 — Portfolio & Risk preflight** ✅ built 2026-11-03 (`.claude/robinhood_four_agent_phase2_walkthrough.md`) | `execution/agentic_preflight.py` + `.claude/agents/rh-risk-manager.md`. | New `tests/test_agentic_preflight.py`: each check pass/fail, never-upsize property, missing-data → veto. |
| **P3 — Analyst + Scout** ✅ built 2026-11-03 | `.claude/agents/rh-strategy-analyst.md`, `rh-research-scout.md` (wrapping `agentic-discovery`); theses/research JSON schemas. | Schema tests; a `review` cycle producing all four artifacts. |
| **P4 — Orchestration** | `/rh-agentic-cycle` command, scheduled run, ntfy summary. Webapp "Pending approvals" card on `AgenticTrading.tsx` is a webapp screen change and **needs a separate explicit OK**. | `/verify`; for the webapp part, `npm run --prefix webapp typecheck` + `/verify-webapp`. |
| **P5 — Burn-in** | 10 trading days in `review`, then `live` with a small `ROBINHOOD_MAX_NOTIONAL_PER_ORDER`; review every approve/decline/expire against the theses. | Receipts reconcile 1:1 with Robinhood fills; zero placements while approvals were off. |

## 8. Documentation updates (part of the deliverable)

- `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md`: the prose-only Robinhood confirmation gap
  is closed for agentic mode by an out-of-band gate. State it honestly: it holds only
  while the operator keeps trade approvals on in the app.
- `docs/known_issues/robinhood_confirmation_gate_is_prose_only.md`: status update.
- `docs/architecture/execution.md`: the four-agent flow, artifacts, preflight module.
- `docs/RUNBOOK.md`: enabling/disabling agentic mode, clearing stuck approvals, rollback.
- `docs/HOW_TO_GUIDE.md`: the operator's view (what the phone push means, how to decline).
- `CLAUDE.md` / `AGENTS.md` "Robinhood trading" bullet: queue-driven → agent chain with
  phone approval (mirror kept in sync by the hook).

## 9. Open questions for the operator

1. ~~Is trade approval currently ON for your Robinhood Agentic account?~~ **Answered:
   yes** (operator, and verified via `get_trade_approval_setting`, 2026-11-02).
2. How long do Robinhood pending approvals live before expiring? The tool docs say they
   expire but give no duration. P1 measures it. It decides whether a limit order or the
   approval expiry is the binding staleness control.
3. Earnings-blackout window N for new buys (P2): keep it off (today's behaviour) or pick a
   value?
4. Polling: should the Trader wait in-session for your taps (simple, but holds a session
   open), or should the next cycle pick up final states (cheaper)? Recommended: the
   latter, with a 30-min tracking window.
5. Should Agent 3 be able to *shrink* an order to fit buying power, or only veto?
   Recommended: veto only in v1, because it's simpler to reason about.
