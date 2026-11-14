# Agentic-only exits, cycle speed, partial fills — implementation plan

Branch `agentic-exits-speed-partial-fills`. Follow-up to PR #1154. Operator request 2026-11-14: fix the
Agentic-only exit gap, the speed items and the Reviewer's partial-fill bug; Opus only for complex agents.
Freeze: the exit gap is a bug fix (a position the system opened can never be exited); speed and
partial fills are efficiency/measurement. The new trade blocks (spread, buy-zone, open orders,
feedback, sector/daily-loss) are NOT in scope; they await the operator's OK.

## Context
The daemon only reads the MAIN Robinhood account (robin_stocks snapshot, `data/robinhood_portfolio.py`).
The Agentic account is reachable only through the Robinhood MCP tools the `rh-*` agents call. So a
position the agents open is never in the universe, never evaluated as held, and never gets a SELL
intent (`queue_builder._intent_dict` returns None when `snapshot.positions` lacks the symbol).

## Design: an MCP-fed Agentic holdings snapshot (no second robin_stocks login)

**K1 — `execution/agentic_holdings.py` (new).**
- Writer CLI: `.venv/bin/python3 -m execution.agentic_holdings save --positions <file> [--quotes <file>] --source risk|review`.
  Parses with `agentic_preflight.AccountState.from_mcp` / `quotes_from_mcp`; writes
  `OUTPUT_DIR/agentic/agentic_holdings.json` atomically (`reporting/atomic_write.py::atomic_write_json`):
  `{"fetched_at", "source", "positions": {SYM: {"quantity", "sellable", "average_cost", "current_price"}}}`.
  Unreadable positions → write nothing, exit 2 (never a fabricated empty book). Missing price/cost → null.
- Reader: `load_agentic_positions(output_dir=None, *, now=None, max_age_hours=MAX_AGE_HOURS) -> Dict[str, PortfolioPosition]`.
  `MAX_AGE_HOURS = 96` (covers a weekend + holiday). Never raises; missing/stale/unparseable → `{}` + one WARNING.
  Positions with no `average_cost` or no `current_price` are skipped (can't evaluate honestly) and logged.
  `dividends_received=0.0`, unrealized P&L computed from cost and price.
- Refreshed by the Risk Manager every cycle and by the Reviewer daily (bootstrap: no queue → no cycle,
  but the daily review still refreshes it).

**K2 — daemon use (absent file ⇒ byte-identical output; goldens must not change).**
- Universe (`pipeline/advisory_inputs.py::build_universe_detailed`): Agentic-held symbols unioned with
  `held` before `compute_tracked_universe` (so rating auto-drop never removes them); new
  `UniverseBuild.agentic_held` field; `held` stays main-account only.
- Evaluate (`AdvisoryOverlayStep._evaluate`): `position = main.get(sym) or agentic.get(sym)` (main wins
  for a name held in both — the preflight already sizes the Agentic sell from Agentic shares).
- Queue (`queue_builder._intent_dict`, SELL only): `held = main.get(sym) or agentic.get(sym)`. BUY
  sizing, `total_equity` and the risk context stay main-account only. `exit_kind` rules unchanged
  (full / fraction / trim; trim needs a price, else full/fraction).

**K3 — speed.**
- Models (operator: Opus only for complex work): `model: sonnet` in rh-research-scout, rh-risk-manager,
  rh-reviewer; `model: opus` in rh-strategy-analyst (judgment) and rh-trader (money-moving).
  `launch()` in agentic_cycle and agentic_review passes `--model sonnet` for the coordinator.
- MCP capture hook: PostToolUse on Robinhood/investyo MCP tools → `.claude/hooks/capture_agentic_mcp.sh`
  → `python -m execution.agentic_inputs capture` writes `{tool_name, tool_input, tool_response}` to
  `OUTPUT_DIR/agentic/<running run_id>/inputs/raw/<seq>_<tool>.json` only while a cycle is `running`;
  otherwise no-op. Never blocks or fails a tool call (always exit 0, short timeout).
  `python -m execution.agentic_inputs assemble <run_id> --tool <short tool name> --out <name>.json [--where key=value]`
  builds a named input (single response, or a list of pages) from captured responses; exit 2 when
  nothing usable was captured → the agent falls back to writing the file itself.
- Reviewer incremental fetch: `python -m execution.agentic_review since` prints `{"since": <ISO date>|null}`
  (day after the last completed review, else null = fetch all); the Reviewer stops paging past it;
  `run` merges today's fetch with the previous day's cached `orders.json` (dedupe by order id, newest wins).
- Trader (unattended): one `review_equity_order` per preflight-PASSED intent at the preflight qty/limit;
  no preview of vetoed intents, no re-fetch of quotes/positions, account number from the Risk inputs,
  one `get_trade_approvals` at the end.
- Gate: new skip `all_intents_handled` when every actionable intent's (symbol, side) is already in
  today's placed ledger or has a pending approval event.
- Ledger appends: `O_APPEND` + fsync under the existing lock instead of rewriting the file.
- Coordinator passes `$OUTPUT_DIR`, the run folder and the Agentic account number into each agent
  prompt; the analyst skips `get_recommendation` inside the unattended cycle.

**K4 — partial fills.** `data/robinhood_orders.parse_orders(..., include_partial=False)`: when True, a
`cancelled`/`partially_filled` order with `cumulative_quantity > 0` yields a fill of that quantity
(average_price). Only `agentic_review.fills_from_mcp` passes True; main-account callers unchanged.

## Owners
| Agent | Files |
|---|---|
| E (exits) | new `execution/agentic_holdings.py`, `pipeline/advisory_inputs.py`, `pipeline/production_steps.py` (AdvisoryOverlayStep only), `execution/queue_builder.py`, `execution/compose.py` if needed, tests for those |
| F (python speed + fills) | `execution/agentic_cycle.py`, `execution/agentic_review.py`, `data/robinhood_orders.py`, `execution/receipts_store.py`, new `execution/agentic_inputs.py`, new `.claude/hooks/capture_agentic_mcp.sh`, `.claude/settings.json` (hook entry), tests for those |
| G (prompts) | `.claude/agents/rh-*.md`, `.claude/commands/rh-*.md`, `.claude/skills/robinhood-execution/SKILL.md` + `.agents/` mirror, `tests/test_rh_agent_tool_allowlists.py` |

## Docs (lead)
`docs/architecture/execution.md`, `docs/architecture/orchestration-entrypoints.md` or data-layer (universe),
`docs/known_issues/rh_queue_gated_on_main_account_2026_11.md` (mark the gap fixed), walkthrough
`.claude/agentic_exits_speed_partial_fills_walkthrough.md`.

## Verification
Targeted tests per agent; goldens byte-identical with no holdings file; new tests: holdings writer/reader
(stale, unreadable, missing cost), universe includes Agentic symbols, AdvisoryOverlay evaluates an
Agentic-only position as held, queue emits an Agentic-only SELL with `exit_kind`, gate skip, capture/assemble,
partial fills, reviewer merge. Then `make ci`, `make local-ci`, daemon restart.
