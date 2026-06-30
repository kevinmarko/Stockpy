---
name: robinhood-execution
description: >-
  Execute the Stockpy advisory platform's gated order queue against the Robinhood
  Trading MCP, PAPER-FIRST. Use when the operator asks to review or place the
  pending Robinhood trades, run the execution queue, or act on
  output/execution_queue.json. Always previews via review_equity_order; only
  places real orders in `live` mode with explicit per-trade human confirmation.
---

# Robinhood Execution (paper-first, human-gated)

This skill is the **only** actor permitted to call the Robinhood Trading MCP
write tools. The headless Stockpy pipeline (`main.py`) cannot call MCP tools; it
only writes a gated, dry-run proposed-order queue to
`output/execution_queue.json` (via `execution/queue_builder.py`). You read that
queue and turn eligible intents into MCP calls — **previewing always, placing
only under strict conditions.**

## Prerequisites (verify before doing anything else)

1. The `robinhood-trading` MCP server is connected (tools `review_equity_order`,
   `place_equity_order`, `get_accounts`, `get_portfolio`, `get_equity_positions`,
   `get_equity_quotes`, `get_equity_orders` are available). If not, tell the
   operator to run `claude mcp add robinhood-trading --transport http
   https://agent.robinhood.com/mcp/trading` and authenticate via `/mcp`. Stop.
2. `output/execution_queue.json` exists. If missing, the platform is in
   `ROBINHOOD_EXECUTION_MODE=off` (or hasn't run). Tell the operator to set the
   mode to `review` or `live` in `.env` and run `python3 main.py`. Stop.

## Hard stops (refuse and explain — do not proceed)

- `output/KILL_SWITCH` exists **OR** the queue's `kill_switch_active` is `true`
  → the platform is paused. Refuse all placement. (Deactivate with
  `python -m execution.kill_switch --deactivate` only on operator instruction.)
- The queue's `mode` is `off` → nothing to do.
- The queue's `generated_at` is more than ~30 minutes old → it is STALE. Refuse
  to place; offer to re-run `python3 main.py` first.
- `get_accounts` does not show a dedicated **Agentic** account, or the operator
  has not confirmed which account is the agentic/execution account → refuse to
  place anything. Robinhood only allows agent orders in the separately-funded
  Agentic account; never operate against the main account.

## Procedure

1. **Load state.** Read `output/execution_queue.json`. Note `mode`,
   `kill_switch_active`, `max_notional_per_order`, and the `intents` list.
   Run the hard-stop checks above.
2. **Confirm the account.** Call `get_accounts` / `get_portfolio`. Identify the
   Agentic account and confirm it with the operator. Show buying power.
3. **Preview every intent (ALWAYS).** For each intent, call
   `review_equity_order` with its `symbol`, `side`, `order_type`, and quantity:
   - SELL intents carry a concrete `qty` (the held share count).
   - BUY intents carry `qty: null` and a `target_notional`. Call
     `get_equity_quotes` for a live price, compute
     `qty = floor(target_notional / price)`, and verify
     `qty * price <= max_notional_per_order` (and `> 0`). If
     `max_notional_per_order` is `0`, refuse to place (cap unset) — preview only.
   Present each preview to the operator: symbol, side, qty, est. notional,
   Robinhood's pre-trade warnings, and the queue's `conviction` + `rationale`.
4. **Mode gate.**
   - `mode == "review"` → **STOP after previews.** This is the paper/dry-run
     stage. Never call `place_equity_order`. Summarise the previews and end.
   - `mode == "live"` → continue, but only for intents whose `allow_place` is
     `true`. Treat `allow_place: false` as preview-only and say why
     (`gate_reasons`).
5. **Place (live only, one at a time, human-confirmed).** For each
   `allow_place: true` intent:
   a. Re-read `output/KILL_SWITCH`; if it now exists, abort the whole run.
   b. Show the final order and ask the operator to confirm THIS specific order
      ("place / skip / stop"). Require an explicit affirmative per order — never
      batch-confirm.
   c. On "place", call `place_equity_order`. On "skip", move on. On "stop", end.
   d. Append a one-line JSON record of the outcome to
      `output/execution_receipts.jsonl` (append-only): `{"ts","symbol","side",
      "qty","action":"reviewed|placed|skipped","mcp_order_id","note"}`.
6. **Report.** Summarise what was previewed, placed, and skipped, and point the
   operator to `output/execution_receipts.jsonl` and the Robinhood app.

## Invariants (never violate)

- **Preview before place, always.** `review_equity_order` precedes any
  `place_equity_order` for the same intent.
- **Never place in `review` mode.** Never place an `allow_place: false` intent.
- **One explicit human confirmation per placed order.** No auto/batch placement.
- **Honor the kill switch** at load time and again immediately before each
  placement.
- **Agentic account only.** Never act against the operator's main account.
- **Receipts, not intents.** You append outcomes to
  `execution_receipts.jsonl`; you never edit `execution_queue.json` (the
  platform owns it).
