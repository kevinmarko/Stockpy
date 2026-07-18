---
name: agentic-discovery
description: >-
  Discover new trading candidates for the Stockpy Agentic Trading tab by running
  the operator's configured Robinhood broker scans, cross-referencing hits
  against this platform's own advisory engine, and writing
  output/scan_candidates.json. Use when the operator asks to run a scan, find
  new candidates, refresh the Agentic Trading tab's Discovery section, or acts
  on output/scan_configs.json. Read-only with respect to orders — never calls
  any Robinhood order-placement tool; that stays the robinhood-execution skill's
  job alone.
---

# Agentic Discovery (scan-based candidate discovery, read-only on orders)

This skill is the **only** actor permitted to call the Robinhood Trading MCP's
scan tools (`create_scan`, `run_scan`, `update_scan_filters`,
`update_scan_config`, `get_scans`, `get_scanner_filter_specs`). Like
`robinhood-execution`, it exists because the headless Stockpy pipeline
(`main.py`) cannot call MCP tools at all — the platform's fixed universe
(held positions ∪ `WATCHLIST` ∪ `watchlist.txt`) has no path to *discover* new
names, only to analyze the ones already on it. This skill closes that gap
without touching order placement: it finds candidates, scores them with the
platform's own advisory engine, and writes a file the webapp reads. Placing an
order is always a separate, later step through `robinhood-execution` — this
skill **never** calls `place_equity_order`, `review_equity_order`, or any
option-order tool.

Treat this as a conversation, not a script: tell the operator what scans you
ran, what you found, and why a candidate did or didn't get an advisory score,
and invite questions before writing the file.

## Prerequisites (verify before doing anything else)

1. The `robinhood-trading` MCP server is connected (tools `create_scan`,
   `run_scan`, `get_scans`, `get_scanner_filter_specs`, `update_scan_filters`
   are available). If not, tell the operator to run `claude mcp add
   robinhood-trading --transport http https://agent.robinhood.com/mcp/trading`
   and authenticate via `/mcp`. Stop.
2. The `investyo-platform` MCP server is connected (tools `get_recommendation`,
   `generate_daily_signals` are available) — this is how you cross-reference a
   scan hit against the platform's own advisory output. If not connected, you
   can still run scans and write candidates with `action: null` / `conviction:
   null` (honest — never fabricate a score), but tell the operator the
   cross-reference step was skipped and why.
3. `output/scan_configs.json` (read via `pilots.scan_config_store.ScanConfigStore`
   if you want to inspect it directly, or just read the file — schema is
   `{"version": 1, "scan_configs": [{"name", "filters", "enabled", ...}]}`).
   The operator edits this from the Agentic Trading tab's Discovery section
   (`PUT /agentic/scan-config` on the Pilots API, gated behind
   `AGENTIC_DISCOVERY_ENABLED`). If the file is missing or has no `enabled:
   true` rows, ask the operator what they want scanned (symbol universe,
   price/volume/RSI/etc. filters) rather than guessing — call
   `get_scanner_filter_specs` first so you propose only filter keys the
   scanner actually supports, then confirm the resulting config with the
   operator before running anything.

## Hard stops (refuse and explain — do not proceed)

- No `enabled: true` scan configs exist and the operator hasn't given you an
  ad-hoc scan definition in this conversation → nothing to run. Ask, don't guess.
- `output/KILL_SWITCH` exists → the platform is paused. You may still run
  read-only scans and cross-reference them (this never touches orders), but
  say so plainly and note the kill switch is active in your summary — the
  operator should know new candidates are being surfaced while the platform
  itself won't act on anything.
- Never call `place_equity_order`, `review_equity_order`, `place_option_order`,
  or `review_option_order` from this skill under any circumstance. If the
  operator asks you to place an order on a candidate you just found, tell them
  that's a separate step — hand off to the `robinhood-execution` skill (which
  reads the platform's own gated queue, not this skill's output directly).

## Procedure

1. **Load scan configs.** Read `output/scan_configs.json`. For each row with
   `enabled: true`, note its `name` and `filters`. If empty, follow the
   prerequisite-3 fallback above.
2. **Run each enabled scan.** For each config: call `create_scan` (or
   `update_scan_filters` if a scan with that name already exists on the
   account — check `get_scans` first) with the stored `filters`, then
   `run_scan` to get the matching symbols. Narrate what each scan found
   (symbol count, a few names) before moving on.
3. **De-duplicate and cap.** Merge results across scans into one candidate
   list, deduplicating by symbol (keep the first scan's `name`/reason a symbol
   matched under). Cap the list at `settings.AGENTIC_MAX_CANDIDATES` (ask the
   operator or check `.env` if you need the current value; default is 25) —
   don't write an unbounded list.
4. **Cross-reference against the advisory engine.** For each candidate symbol,
   call `get_recommendation(symbol)` on the investyo MCP. Record its `action`
   and `conviction` on the candidate. If the call fails or the symbol isn't in
   the platform's tracked universe, leave `action`/`conviction` as `null` —
   **never** invent a plausible-looking score. Say out loud when this happens
   for a candidate so the operator knows it's an honest gap, not a scan error.
5. **Write `output/scan_candidates.json`.** Shape:
   ```json
   {
     "generated_at": "<UTC ISO-8601 timestamp, now>",
     "candidates": [
       {
         "symbol": "NVDA",
         "scan_name": "high_momentum_breakout",
         "scan_reason": "Price > 20SMA, volume > 2x avg, RSI(14) 55-70",
         "action": "BUY",
         "conviction": 0.72,
         "discovered_at": "<UTC ISO-8601 timestamp>"
       }
     ]
   }
   ```
   Write the whole file in one shot (overwrite, don't append — this is a
   point-in-time snapshot, and `pilots.discovery.discovery()` reads it as
   such). `scan_reason` should be a short, human-readable description of why
   the symbol matched (the filters that triggered), not the raw filter dict.
6. **Report, and stay open.** Summarize scans run, candidates found, how many
   got an advisory cross-reference vs. `null`, and point the operator to the
   Agentic Trading tab's Discovery section to review them. If any candidate
   scored a high-conviction BUY/SELL, mention that the platform's *existing*
   gated pipeline (not this skill) is what would eventually surface it on the
   real execution queue once it's part of the tracked universe — this skill
   only discovers and scores, it doesn't add symbols to `WATCHLIST` or
   `watchlist.txt` on its own. If the operator wants a candidate tracked going
   forward, that's a separate, explicit edit to `watchlist.txt` they should
   confirm — don't do it silently as a side effect of a scan.

## Invariants (never violate)

- **Never call an order tool.** No `place_equity_order`, `review_equity_order`,
  `place_option_order`, `review_option_order`, ever, from this skill.
- **Never fabricate a score.** A candidate the advisory cross-reference
  couldn't score gets `action: null`, `conviction: null` — not a guess, not a
  0.0, not a copied score from a similar symbol.
- **Overwrite, don't merge, `scan_candidates.json`.** Each run is a fresh
  snapshot; stale candidates from a prior run should not linger silently.
- **Never silently add symbols to the tracked universe.** Discovering a
  candidate is not the same as watching it — `WATCHLIST`/`watchlist.txt`
  changes are a separate, operator-confirmed action.
- **Respect `AGENTIC_MAX_CANDIDATES`.** Don't write an unbounded candidate list.
