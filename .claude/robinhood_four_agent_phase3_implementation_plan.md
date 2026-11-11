# Phase 3 — Research Scout (Agent 1) + Strategy Analyst (Agent 2)

Parent plan: `.claude/robinhood_four_agent_trading_implementation_plan.md`. Phases 1 (#1134)
and 2 (#1135) are merged.

## Context

After Phase 2 the Trader only sends orders that pass a code-enforced check against the
live account. What the operator still doesn't get is the *why*. The phone shows "BUY 7 T
@ ≤ $21.29" with no reason, and nothing has checked what's coming up for the stock
(earnings, filings, news). Phase 3 adds the two read-only agents that supply that
context: the **Research Scout** (what's happening with these names) and the **Strategy
Analyst** (a short, falsifiable thesis per order).

The risk with LLM-written theses is a confident, made-up number. So the honesty rule
("every number is quoted from an artifact, never recomputed") is **enforced in code**:
each number in a thesis must cite where it came from, and a validator checks that the
cited value really is there.

## Design

### New module `execution/agentic_research.py`

Two JSON documents per queue, in the same folder as the preflight
(`OUTPUT_DIR/agentic/<run_id>/`, `run_id` from the queue's `generated_at`, reusing
`agentic_preflight.run_id_for`):

**`research.json`** (written by the Scout), keyed by symbol:
```
{"queue_generated_at": ..., "symbols": {"T": {
   "next_earnings": {"date": "2026-11-20"|null, "source": "get_earnings_calendar", "reason": null|"..."},
   "analyst_ratings": {"buy": 12, "hold": 5, "sell": 1, "source": ...} | null + reason,
   "recent_filings": [{"form": "8-K", "filed": "...", "url": "..."}],
   "news": [{"headline": "...", "url": "...", "published": "..."}],
   "data_gaps": ["analyst_ratings"]}}}
```

**`theses.json`** (written by the Analyst), keyed by `client_order_id`:
```
{"queue_generated_at": ..., "theses": {"<coid>": {
   "symbol": "T", "side": "buy",
   "thesis": "≤ 280 chars", "invalidation": "≤ 160 chars", "horizon": "e.g. 30d",
   "drivers": [{"name": "conviction", "value": 0.95,
                "cite": "queue:intents[<coid>].conviction"}],
   "flags": ["earnings_in_2_days"], "data_gaps": ["forecast_30d"]}}}
```

Functions (pure; nothing raises; every problem is reported):
- `validate_research(doc, queue) -> list[str]`: the right queue, known symbols, a date or
  `null` with a reason for every missing field, and URLs on news and filings.
- `validate_theses(doc, queue, cite_sources) -> list[str]`:
  - one thesis per queue intent at most; symbol and side match the intent
  - length limits respected
  - **every driver's `cite` resolves to the same value**. A cite is `queue:<path>`
    (into the queue) or `file:<name>:<path>` (into a raw MCP response the Analyst saved
    under `agentic/<run_id>/inputs/`). A number that doesn't match its source within
    1e-9 relative, or a cite that doesn't resolve, is an error.
  - a NaN or `None` value is an error unless that driver's name is also in `data_gaps`
- `earnings_dates_from_research(doc) -> {SYM: date|None}`: feeds the preflight's
  existing earnings blackout, so the Scout's data drives that check. A symbol whose
  earnings field is missing is left out, which is how the preflight already marks
  "unknown" and vetoes it.
- `load_run_artifacts(queue, output_dir) -> (research, theses, errors)`: reads both files
  for this queue only.
- CLI: `python -m execution.agentic_research validate` prints the errors and exits 0
  (clean) or 1 (errors).

### Plan check 10 in the preflight (opt-in)

`preflight(...)` gains `theses=None, critical_gap_fields=()`. With
`critical_gap_fields` empty (the default, today's behaviour), nothing changes. When the
caller passes fields, an intent is vetoed:
- `critical_data_gap` if its thesis lists one of those fields in `data_gaps`
- `thesis_missing` if it has no valid thesis
- `thesis_invalid` if the validator rejected the thesis

The preflight CLI gains `--theses` and `--critical-gaps`. Still veto-only.

### Agents

- `.claude/agents/rh-research-scout.md`: read-only Robinhood research tools
  (`run_scan`, `get_scans`, `get_scanner_filter_specs`, `get_equity_quotes`,
  `get_equity_fundamentals`, `get_earnings_calendar`, `get_earnings_results`,
  `get_equity_analyst_ratings`, `get_sec_filing_index`) under both server spellings,
  plus Read/Grep/Glob/Bash. Its procedure is the existing `agentic-discovery` skill
  (unchanged), then `research.json` for every queue symbol. It never edits
  `watchlist.txt` unless the operator names the symbol (the existing rule).
- `.claude/agents/rh-strategy-analyst.md`: read-only. Reads the queue,
  `research.json`, and `investyo` MCP `get_signal_breakdown` / `get_regime_status` /
  `get_recommendation` output, which it saves raw under `inputs/`. It writes
  `theses.json` with cites and runs the validator until it is clean. It can flag, but
  never removes, resizes or ranks an intent.
- `tests/test_rh_agent_tool_allowlists.py`: both are covered by the existing
  "no order-power tool" guard. New positive tests pin each one's exact read set.

### Trader (skill, both copies) — narration only

- When a valid `theses.json` exists for this queue, the Trader shows each order's
  one-line thesis and invalidation before sending it to your phone, and puts them in
  the ntfy text. Without one, it says the thesis is unavailable.
- **No placement rule changes**, except through the opt-in check 10 above.

## Files

- New: `execution/agentic_research.py`, `tests/test_agentic_research.py`,
  `.claude/agents/rh-research-scout.md`, `.claude/agents/rh-strategy-analyst.md`,
  `.claude/robinhood_four_agent_phase3_walkthrough.md`.
- Edit: `execution/agentic_preflight.py` (check 10 + CLI flags),
  `tests/test_agentic_preflight.py`, both skill copies + `/rh-execute`,
  `tests/test_robinhood_e2e.py` (pinned phrases), `tests/test_rh_agent_tool_allowlists.py`,
  `tests/test_pipeline_smoke.py` (guard list), settings census/liveness regen if needed.
- Reuse: `agentic_preflight.run_id_for` / `_parse_dt` / `write`-style atomic writer,
  the `agentic-discovery` skill as-is.

## Documentation step

`docs/architecture/execution.md`, `docs/architecture/testing.md`, `docs/RUNBOOK.md`
(reading a thesis; check 10), the parent plan (Phase 3 row, Agents 1–2 as built),
`docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md` (thesis numbers are citation-verified).

## Verification

1. `pytest tests/test_agentic_research.py tests/test_agentic_preflight.py
   tests/test_robinhood_e2e.py tests/test_rh_agent_tool_allowlists.py
   tests/test_pipeline_smoke.py` green, covering:
   - a correct cite passes
   - a tampered number fails
   - a dangling cite fails
   - NaN without a matching `data_gaps` entry fails
   - one thesis per intent
   - check 10 off by default, and each of its veto paths
   - earnings flow into the blackout
2. ruff clean; full suite on CI.
3. Read-only live run: real earnings calendar, analyst ratings and quotes for a hand-made
   queue → `research.json` + a hand-written thesis → validator clean, then tamper with
   one number → validator rejects it. No order tool called.
