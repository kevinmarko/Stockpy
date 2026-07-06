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

You may arrive here two ways: the operator asks you to run this skill, or
`execution/queue_builder.py` pushed them an ntfy notification (if
`NTFY_TOPIC` is configured) because the queue gained a new or newly-placeable
intent since the last cycle. Either way, treat this as a **conversation, not a
script**: narrate what you're seeing and why in plain English, and actively
invite the operator to ask questions, request more detail (their existing
position, recent earnings, why the gate blocked or cleared something), or
redirect before you move on. The procedure and hard stops below are the
non-negotiable safety rails; how you talk through them with the operator is
not — read them a checklist only if they ask for one.

## Prerequisites (verify before doing anything else)

1. The `robinhood-trading` MCP server is connected (tools `review_equity_order`,
   `place_equity_order`, `get_accounts`, `get_portfolio`, `get_equity_positions`,
   `get_equity_quotes`, `get_equity_orders` are available). If not, tell the
   operator to run `claude mcp add robinhood-trading --transport http
   https://agent.robinhood.com/mcp/trading` and authenticate via `/mcp`. Stop.
2. `output/execution_queue.json` exists. If missing, the platform is in
   `ROBINHOOD_EXECUTION_MODE=off` (or hasn't run). Tell the operator to set the
   mode to `review` or `live` in `.env` and run `python3 main.py`. Stop.
3. `output/execution_placed.jsonl` is the append-only **placed-intent ledger**
   (may not exist yet — that just means nothing has been placed). Each line is
   one JSON record:
   `{"ts","dedup_key","symbol","side","qty","target_notional","client_order_id","mcp_order_id"}`,
   where `dedup_key = "YYYY-MM-DD:SYMBOL:SIDE"` in **UTC**. You consult it for
   the idempotency check (step 5) and append to it after every successful
   placement. It is distinct from `output/execution_receipts.jsonl` (the broader
   reviewed/placed/skipped audit trail) — write BOTH on a placement.

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

1. **Load state and orient the operator.** Read `output/execution_queue.json`.
   Note `mode`, `kill_switch_active`, `max_notional_per_order`, and the
   `intents` list. Run the hard-stop checks above. Then, before touching any
   MCP tool, give the operator a short spoken overview — mode, how many
   intents, how many are `allow_place: true`, and the highest-conviction 1-2 —
   and pause for questions. Don't front-load every detail; let the operator
   pull on whatever they want to know more about.
2. **Confirm the account.** Call `get_accounts` / `get_portfolio`. Identify the
   Agentic account and confirm it with the operator. Show buying power. Once the
   operator confirms which account is the Agentic/execution account, **remember
   it for the rest of this session** — you don't need to re-run the discovery
   dance every intent. You still re-confirm ("placing into your Agentic account
   ‹…›, correct?") as part of the per-order confirm gate in step 5b before any
   placement; persistence saves the lookup, not the confirmation.
3. **Preview every intent (ALWAYS), one at a time, narrated.** For each
   intent, call `review_equity_order` with its `symbol`, `side`, `order_type`,
   and quantity:
   - SELL intents carry a concrete `qty` (the held share count).
   - BUY intents carry `qty: null` and a `target_notional`. Call
     `get_equity_quotes` for a live price, compute
     `qty = floor(target_notional / price)`, and verify
     `qty * price <= max_notional_per_order` (and `> 0`). If
     `max_notional_per_order` is `0`, refuse to place (cap unset) — preview only.
   - **Order type.** An intent may carry `order_type` and `limit_offset_bps`.
     - `order_type: "market"` (or `order_type` absent, or `limit_offset_bps`
       `0`/absent) → a **market** order, exactly as today. Pass no limit price.
     - `order_type: "limit"` with a positive `limit_offset_bps` → a **limit**
       order. Resolve the limit price from the **live MCP quote at review time**
       (`get_equity_quotes`), never from the queue's snapshot price:
       - BUY:  `limit_price = quote * (1 + limit_offset_bps / 10000)` — you pay
         at most this; the offset is the max slippage you'll tolerate above the
         quote.
       - SELL: `limit_price = quote * (1 - limit_offset_bps / 10000)` — you
         receive at least this.
       Round the limit price to the venue's tick (2 decimals for equities), pass
       it to BOTH `review_equity_order` and `place_equity_order`, and state the
       resolved limit price aloud when you narrate the intent. For a BUY limit,
       size `qty` off the resolved `limit_price` (not the raw quote) so the
       `qty * limit_price <= max_notional_per_order` cap check stays honest.
   Walk the operator through it in your own words — symbol, side, qty, est.
   notional, Robinhood's pre-trade warnings, the queue's `conviction` and
   `rationale`, and (for a blocked intent) *why* `gate_reasons` says what it
   says. Then explicitly invite questions about this specific intent — e.g.
   their current position/cost basis (`get_equity_positions`), upcoming
   earnings (`get_earnings_calendar`), or a different size — and answer them
   using whatever read-only MCP tools help, before moving to the confirm gate
   in step 5. Don't rush past this into the next intent.
4. **Mode gate.**
   - `mode == "review"` → **STOP after previews.** This is the paper/dry-run
     stage. Never call `place_equity_order`. Summarise the previews and end.
   - `mode == "live"` → continue, but only for intents whose `allow_place` is
     `true`. Treat `allow_place: false` as preview-only and say why
     (`gate_reasons`).
5. **Place (live only, one at a time, human-confirmed).** For each
   `allow_place: true` intent:
   a. Re-read `output/KILL_SWITCH`; if it now exists, abort the whole run.
   b. **Idempotency check.** Compute this intent's
      `dedup_key = "YYYY-MM-DD:SYMBOL:SIDE"` using **today's UTC date**, then
      read `output/execution_placed.jsonl` and check whether that `dedup_key`
      already appears for today. If it does, treat the intent as **ALREADY
      PLACED**: skip it, tell the operator plainly ("MSFT BUY was already placed
      today — mcp_order_id ‹…› — skipping to avoid a double-fill"), record a
      `skipped` receipt with a note, and move on. Do **not** re-place. (If the
      ledger file is absent, no intent has been placed today — proceed.)
   c. Show the final order and ask the operator to confirm THIS specific order
      ("place / skip / stop"). Require an explicit affirmative per order — never
      batch-confirm, and never treat silence or a topic change as consent.
   d. On "place", call `place_equity_order` (with the resolved limit price for
      limit intents). On "skip", move on. On "stop", end.
   e. **On a successful placement, append to BOTH ledgers (append-only):**
      - `output/execution_placed.jsonl` — the placed-intent ledger:
        `{"ts","dedup_key","symbol","side","qty","target_notional",
        "client_order_id","mcp_order_id"}` (use the same `dedup_key` you computed
        in step 5b; this is what makes the next run's idempotency check work).
      - `output/execution_receipts.jsonl` — the outcome audit trail:
        `{"ts","symbol","side","qty","action":"reviewed|placed|skipped",
        "mcp_order_id","note"}`.
      For reviewed-only or skipped intents, append only the receipts record (no
      ledger line — nothing was placed).
6. **Report, and stay open.** Summarise what was previewed, placed, and
   skipped, point the operator to `output/execution_receipts.jsonl`,
   `output/execution_placed.jsonl`, and the Robinhood app, and invite any
   follow-up questions rather than treating the run as over the moment the last
   intent is handled. Note that after the run, `execution/receipts_store.py`
   reconciles the receipts/ledger against the account's **actual** Robinhood
   fills (via `get_equity_orders`), and the **Robinhood panel in the GUI Command
   Center surfaces that reconciliation** — direct the operator there to confirm
   every intent the ledger records as placed shows a matching real fill (and to
   catch any drift).

## Invariants (never violate)

- **Preview before place, always.** `review_equity_order` precedes any
  `place_equity_order` for the same intent.
- **Never place in `review` mode.** Never place an `allow_place: false` intent.
- **One explicit human confirmation per placed order.** No auto/batch placement.
- **Honor the kill switch** at load time and again immediately before each
  placement.
- **Agentic account only.** Never act against the operator's main account.
- **Idempotent placement.** Before placing, check the placed-intent ledger
  (`execution_placed.jsonl`) for today's `dedup_key`; if present, the intent is
  already placed — skip it, never double-place. Append a ledger line after every
  successful placement so the next run sees it.
- **Limit price from the live quote.** For a `limit` intent, always derive the
  limit price from the review-time MCP quote (BUY ≤ quote·(1+bps/10000), SELL ≥
  quote·(1−bps/10000)) — never from the queue's stale snapshot price — and pass
  the same price to both `review_equity_order` and `place_equity_order`.
- **Receipts, not intents.** You append outcomes to `execution_receipts.jsonl`
  (and placements to `execution_placed.jsonl`); you never edit
  `execution_queue.json` (the platform owns it).
- **Conversation, not consent.** Narrating, answering questions, and
  discussing an intent is encouraged and never itself counts as the operator's
  explicit per-order confirmation — that confirmation still has to be asked
  for and given plainly, per step 5b.
