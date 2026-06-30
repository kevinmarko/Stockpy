---
description: Review (and in live mode, place with confirmation) the Robinhood execution queue
---

Run the **robinhood-execution** skill against the platform's gated order queue.

Read `output/execution_queue.json` and act on it strictly per the
`robinhood-execution` skill:

1. Verify the `robinhood-trading` MCP is connected and the queue exists and is
   fresh; honor every hard stop (kill switch, `mode: off`, stale queue,
   no confirmed Agentic account).
2. Confirm the dedicated Agentic account via `get_accounts`.
3. **Preview every intent with `review_equity_order`** (compute share count from
   a live `get_equity_quotes` for BUY intents, respecting
   `max_notional_per_order`).
4. If `mode == "review"`, STOP after previews — this is the paper/dry-run stage.
5. If `mode == "live"`, place ONLY `allow_place: true` intents, one at a time,
   each with an explicit per-order human confirmation, re-checking the kill
   switch before each placement, and append outcomes to
   `output/execution_receipts.jsonl`.

Never place an order in `review` mode, never place an `allow_place: false`
intent, never batch-confirm, and never operate against the main (non-Agentic)
account.

$ARGUMENTS
