# Robinhood Agentic System — Phase 5 Implementation Plan (Agent 5 "Reviewer" + burn-in)

Parent plan: `.claude/robinhood_four_agent_trading_implementation_plan.md` §7 P5 row.
Phases 1–4 merged (#1134–#1137). Operator ask (2026-11-10): "build out the next phase
with 5 agent". Feature freeze: needs the operator's OK on this plan before code.

## Goal

The parent plan's Phase 5 is a burn-in: about 10 trading days in `review`, then `live`
with a small `ROBINHOOD_MAX_NOTIONAL_PER_ORDER`. Its pass criteria are "receipts
reconcile 1:1 with Robinhood fills; zero placements while approvals were off", plus a
review of every approve/decline/expire against its thesis. Nobody measures any of that
today. Phase 5 adds a fifth agent whose only job is to check the other four.

## Agent 5 — Reviewer (read-only)

`.claude/agents/rh-reviewer.md`. Tools: Read, Grep, Glob, Bash and the read-only
Robinhood tools `get_equity_orders`, `get_equity_quotes`, `get_trade_approvals`,
`get_trade_approval_setting` (both server spellings). **No order, decline or cancel
tools, and no Write.** It saves raw MCP output to the review folder through the code's CLI,
runs the code, and narrates the result. It never changes a mode, a cap or a queue.

## Deterministic part: `execution/agentic_review.py` (new)

Like the preflight and the validator, the judgement is code, not the agent:

1. **Reconciliation** of the placed ledger against real Robinhood orders, by reusing
   `receipts_store.reconcile()` with an `orders_fetcher` fed from a saved
   `get_equity_orders` response. If the MCP order shape differs from what
   `data.robinhood_orders.fetch_filled_orders` expects, add a small adapter, never a
   second FIFO.
2. **Outcome per approved order:** entry fill price, the latest quote, and the
   move since entry. When a thesis was validated, show it next to the outcome. Nothing is
   scored as right or wrong on fewer than the thesis horizon's days. That's labelled
   "too early", never a guessed verdict.
3. **Cycle health** from `cycles.jsonl` and each `cycle.json`: cycles completed, failed (by
   stage and reason), and skipped; veto counts by reason from each `preflight.json`;
   approvals approved, declined, expired or pending, from `execution_approvals.jsonl`.
4. **Safety invariants**, each a hard FAIL:
   - any placement while `get_trade_approval_setting` said approvals were off;
   - any placement in a `review`-mode cycle;
   - any placed record with no `ref_id`;
   - any order larger than its preflight `qty`;
   - any resubmission of a declined `dedup_key`.
5. **Go-live readiness** (`burnin_status`): READY only when every invariant passes, there
   are at least 10 distinct trading days with completed `review` cycles, no unexplained
   failed cycles in the last 3 days, reconciliation `ok`, and a positive per-order cap is
   set. Otherwise NOT READY, with each unmet item listed. It is advice only. Switching to
   `live` stays the operator's decision and their own settings change.

Output: `OUTPUT_DIR/agentic/review/<date>/review.json` (atomic) and `inputs/`. CLI
`python -m execution.agentic_review run|status` exits 0 READY, 1 NOT READY, 2 inputs
unusable (fail closed).

## Wiring

- `/rh-agentic-review` command: run the Reviewer and push one daily summary built from
  `review.json` (e.g. "Burn-in day 4/10: 6 cycles, 0 failures, 11 would-pass, 3 vetoes;
  NOT READY: 6 more review days").
- `scripts/com.investyo.rh-agentic-review.plist`: once a day after the close. **Shipped,
  not installed**, like Phase 4's timer.
- Webapp: one "Burn-in" line on the existing Pending approvals card (`GET
  /agentic/approvals` gains a `burnin` block from the latest `review.json`). It doesn't
  add a new screen.

## Tests

`tests/test_agentic_review.py`:
- every invariant (pass and fail);
- readiness, including each unmet item;
- outcomes ("too early", a missing quote stays null, never 0);
- reconciliation through the injected fetcher, with the MCP order shape;
- cycle-health counts;
- the CLI exit codes.

Also: the Reviewer's exact read-only tool set in `tests/test_rh_agent_tool_allowlists.py`,
the new module in the order-tool AST guard, the approvals endpoint's `burnin` block
(`tests/test_pilots_api.py`, plus `tests/test_agentic_approvals.py`), and webapp
typecheck plus a browser check.

## Documentation

- `docs/RUNBOOK.md`: a "Phase 5 burn-in" checklist (load both timers in `review`,
  read the daily push, the READY criteria, how to go live, how to roll back);
- `docs/HOW_TO_GUIDE.md`;
- `docs/architecture/{execution,testing,observability-and-apis,webapp-and-gui}.md`;
- the safety framework;
- the parent plan's P5 row and a new Agent 5 section;
- the `CLAUDE.md`/`AGENTS.md` Robinhood bullet;
- regenerated census and liveness.

## Not in this phase

Loading the timers and running the 10 days happen on your Mac. Going `live` needs your
explicit OK after a READY report, and stays your own settings change.
