# Agentic-only exits, cycle speed, partial fills — walkthrough

Plan: `.claude/agentic_exits_speed_partial_fills_implementation_plan.md`. Built by three parallel
agents (E: exits, F: Python speed + partial fills, G: prompts), integrated and reviewed by the lead.

## Agentic-only exits
- The daemon still can't log in to the Agentic account. Instead the Risk Manager (every cycle) and the
  Reviewer (daily — so it refreshes even when no queue exists) save the account's MCP positions and
  quotes and run `execution.agentic_holdings save`, which writes
  `OUTPUT_DIR/agentic/agentic_holdings.json` atomically.
- The daemon reads it (`load_agentic_positions`, ignored after 96 h) in three places: the universe
  (Agentic symbols protected from rating auto-drop, reported as `agentic_held`), the advisory overlay
  (an Agentic-only position is evaluated as held, so Case A / RISK REDUCE exits can fire) and the queue
  (SELL intents marked `"holding_source": "agentic"`). The main account wins for a name held in both.
- The preflight already sizes `full`/`fraction` SELLs from the Agentic account's own sellable shares.
- Verified: with no holdings file every golden is byte-identical; an Agentic-only SELL is
  `gate_allowed` during market hours (`TestAgenticSellPassesQueueGate`).
- **Behavior change:** Agentic holdings now appear in the daemon's universe and advisory output
  (state snapshot, summary push) as held positions.

## Speed
- Models (operator: Opus only for complex work): Sonnet for scout, risk manager, reviewer and the
  launched coordinator sessions; Opus for the strategy analyst and the trader.
- MCP responses are captured by a PostToolUse hook during a running cycle; agents build their input
  files with `execution.agentic_inputs assemble` instead of retyping them (manual fallback on exit 2).
- The unattended Trader previews only preflight-passed intents, once, at the preflight's qty/limit.
- New gate skip `all_intents_handled` avoids a full cycle when a refreshed queue only repeats orders
  already placed today or pending approval.
- The Reviewer fetches orders incrementally (`agentic_review since`) and merges with its cache.
- Ledger appends use `O_APPEND` instead of rewriting the file.
- The coordinator passes `$OUTPUT_DIR`, the run folder and the account number into each agent; the
  analyst skips `get_recommendation` in the unattended cycle.

## Partial fills
`parse_orders(include_partial=True)` (Reviewer only) counts a cancelled/partially-filled order with
`cumulative_quantity > 0` as a fill of that quantity; main-account callers are unchanged.

## Known limits
- A position opened since the last holdings refresh gets no exit until the next refresh (≤ one cycle
  or one day).
- A main-snapshot outage (`total_equity` not positive) still writes no queue at all, Agentic exits included.
- The capture hook doesn't match MCP servers registered under a UUID name (desktop sessions); those
  fall back to the manual save. Launched cycles use `claude_ai_Robinhood`, which matches.
- Not in scope (need the operator's OK under the freeze): spread / buy-zone / open-order vetoes,
  outcome feedback, sector and daily-loss checks.
