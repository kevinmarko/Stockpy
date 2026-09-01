---
name: agentic-discovery
description: >-
  Discover new trading candidates for the Stockpy Agentic Trading tab by running
  the operator's configured Robinhood broker scans, cross-referencing hits
  against this platform's own advisory engine, and writing
  output/scan_candidates.json. Can also add a specific, operator-named
  candidate to watchlist.txt so the platform's advisory pipeline picks it up
  going forward. Use when the operator asks to run a scan, find new
  candidates, refresh the Agentic Trading tab's Discovery section, acts on
  output/scan_configs.json, or asks to track/watch a discovered symbol.
  Read-only with respect to orders — never calls any Robinhood
  order-placement tool; that stays the robinhood-execution skill's job alone.
---

<!--
  Ported from this repo's Claude Code sibling skill (`.claude/skills/agentic-discovery/SKILL.md`)
  to Antigravity's skill format. Frontmatter and body content are carried over verbatim --
  Antigravity's own `google-antigravity-sdk` skill and this repo's existing `.agents/skills/supabase`
  skill both use the same minimal `name` + `description` frontmatter shape Claude's SKILL.md already
  used here, so no restructuring was required for this port beyond this note.
-->

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
2. The `investyo-platform` MCP server is connected (tools `get_signal_breakdown`,
   `generate_daily_signals`, `update_universe_tickers` are available) — this is
   how you cross-reference a scan hit against the platform's own advisory
   output. If not connected, you can still run scans and write candidates with
   `action: null` / `conviction: null` (honest — never fabricate a score), but
   tell the operator the cross-reference step was skipped and why.
   **Confirmed 2026-08-02: this MCP connection can be a completely separate
   process from this checkout — in this operator's setup it's reached over SSH
   to a deployed instance with its own `.env`/`watchlist.txt`/DB, not this
   repo's working directory.** Whatever it's pointed at, treat it as a
   possibly-remote system with its own state, distinct from local files you
   edit directly — see step 7.5.
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
   call `get_signal_breakdown(symbol)` on the investyo MCP and parse `action`
   from its `Action Signal`/`Advice` field and `conviction` from whatever
   composite score field it returns. If the call fails (including a generic
   backend error unrelated to any specific symbol — that's a tool bug, not a
   per-symbol gap; say so explicitly) or the symbol isn't in the platform's
   tracked universe, leave `action`/`conviction` as `null` — **never** invent a
   plausible-looking score. Say out loud when this happens for a candidate so
   the operator knows it's an honest gap, not a scan error.
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
   only discovers and scores; adding a symbol to that universe is the separate,
   operator-confirmed step below (7), never an automatic side effect of a scan.
7. **Track a candidate (only when the operator names it).** A high score or
   `BUY` action is information, not consent — this step only runs when the
   operator explicitly names which symbol(s) to start tracking (e.g. "track
   YMM"), never automatically for every high-conviction hit.
   1. `main._load_watchlist()` unions BOTH the `WATCHLIST` env var and
      `watchlist.txt`, deduped — its own docstring states plainly that
      "neither one takes precedence over the other." So appending to
      `watchlist.txt` is always effective regardless of whether `WATCHLIST`
      is also set; there's no precedence conflict to check or ask the
      operator to resolve here.
   2. Read `watchlist.txt` (create it if missing). Append the named
      ticker(s), one per line, uppercase, skipping any already present
      (case-insensitive match against existing non-comment lines) — never
      duplicate. Add a `# added via agentic-discovery on <UTC date>` comment
      above the new line(s) for auditability, matching the file's existing
      `#`-comment convention.
   3. Report exactly which lines were added vs. already present. Note this
      takes effect on the *next* `main.py`/`main_orchestrator.py` universe
      build — it's not retroactive and places no order on its own.
   4. `DEFAULT_TICKERS` (`data/portfolio_sync.py`'s separate coverage-tracking
      universe, distinct from the advisory pipeline's `WATCHLIST`/
      `watchlist.txt`) is a DIFFERENT mechanism — don't conflate the two. See
      7.5 for when to also update it; otherwise leave it untouched.
   5. **Also sync the connected `investyo-platform` MCP's universe, by
      default — this is what closes the "two places to update" gap.** That
      MCP connection may be a separate, possibly-remote deployment with its
      own `.env` (see prerequisite 2) — editing local `watchlist.txt` alone
      does not reach it, and its own `get_universe_status` reports
      `DEFAULT_TICKERS`, not `watchlist.txt`. If `update_universe_tickers` is
      available, call it once per newly-tracked symbol (`action: "add"`) right
      alongside the local watchlist.txt edit in 7.2 — don't make the operator
      ask for this separately each time. Tell them plainly that this updates a
      DIFFERENT field (the connected deployment's `DEFAULT_TICKERS`) than the
      local `watchlist.txt` edit, in case the two ever need to diverge (e.g.
      operator wants a symbol advisory-scored locally but not yet on the live
      deployment) — if they say so, skip this call for that symbol and say
      which one you skipped and why. If `update_universe_tickers` isn't
      available (MCP not connected, or a write error), say so and fall back to
      local-only, same as today.

## Invariants (never violate)

- **Never call an order tool.** No `place_equity_order`, `review_equity_order`,
  `place_option_order`, `review_option_order`, ever, from this skill.
- **Never fabricate a score.** A candidate the advisory cross-reference
  couldn't score gets `action: null`, `conviction: null` — not a guess, not a
  0.0, not a copied score from a similar symbol.
- **Overwrite, don't merge, `scan_candidates.json`.** Each run is a fresh
  snapshot; stale candidates from a prior run should not linger silently.
- **Only add symbols to the tracked universe when the operator names them.**
  A high score or `BUY` action is never sufficient justification on its own —
  discovering and scoring a candidate is not the same as watching it. Step 7
  only acts on symbols the operator explicitly named in this conversation.
- **Respect `AGENTIC_MAX_CANDIDATES`.** Don't write an unbounded candidate list.
