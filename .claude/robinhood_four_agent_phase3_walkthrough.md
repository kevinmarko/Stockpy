# Robinhood Four-Agent System — Phase 3 Walkthrough (Research Scout + Strategy Analyst)

Plan: `.claude/robinhood_four_agent_phase3_implementation_plan.md` (approved, "Build as
planned", 2026-11-03). Parent plan: `.claude/robinhood_four_agent_trading_implementation_plan.md`.
Phase 1 merged as #1134, Phase 2 as #1135.

## Why

After Phase 2 the operator's phone shows *what* will trade but not *why*, and nothing
had looked at what's coming up for the stock. Phase 3 adds two read-only agents that
supply that context. The main risk with LLM-written reasoning is a confident, invented
number, so the honesty rule is enforced in code, not left to the prompt.

## What changed

| File | Change |
|---|---|
| `execution/agentic_research.py` (new) | Schemas and validators for `research.json` and `theses.json`; cite resolution; a check on numbers in the thesis sentence; the earnings bridge to the preflight; `load_run_artifacts`; `validate` CLI. |
| `execution/agentic_preflight.py` | Plan check 10, opt-in (`critical_gap_fields`, `--critical-gaps`): `thesis_missing` / `thesis_invalid` / `critical_data_gap`. With `--blackout-days` and no `--earnings`, dates come from the Scout's `research.json`. |
| `.claude/agents/rh-research-scout.md` (new) | Agent 1: discovery via the unchanged `agentic-discovery` skill, plus `research.json` for every queue symbol (raw responses saved under `inputs/`). |
| `.claude/agents/rh-strategy-analyst.md` (new) | Agent 2: `theses.json` with a cite on every number, validated until clean. It can flag, but never removes, resizes or ranks an order. |
| Both skill copies + `/rh-execute` | Step 4c: narrate validated theses only; "thesis unavailable" otherwise. No placement change. |
| Tests | `tests/test_agentic_research.py` (new, 64 tests), `TestCheck10` in `tests/test_agentic_preflight.py`, exact read-tool sets for both agents, 4 new pinned skill phrases, the new module added to the AST guard. |
| Docs | `docs/architecture/execution.md`, `docs/architecture/testing.md`, `docs/RUNBOOK.md` ("Reading a thesis"), `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md`, the parent plan; settings census and liveness regenerated. |

## How the anti-fabrication gate works

Each driver in a thesis looks like
`{"name": "mean_price_target", "value": 15.0938, "cite": "file:ratings.json:data.results[symbol=F].ratings.mean_price_target"}`.
The validator:
1. Resolves the cite: `queue:<path>` points into the queue, `file:<name>.json:<path>`
   into a raw MCP response saved verbatim under `inputs/`. Path traversal and unknown
   sources are rejected.
2. Requires the value to match the source. Numbers (including Robinhood's numeric
   strings) must match to 1e-9 relative; everything else must be exactly equal, and
   `True` never equals `1`.
3. Allows NaN or `None` only when the driver is listed in `data_gaps`.
4. Requires **every number in the thesis sentence to be one of the cited values** (a
   % may be the value × 100). The live dry run is what exposed the need for this
   check, below. The invalidation line is exempt because it states a condition.

## Live dry run (read-only, real data)

Inputs: real `get_equity_analyst_ratings` for T and F (T 16/10/1, mean target 27.4773;
F 9/13/1, 15.0938) and the real 31-day earnings calendar (1,487 reports, 2026-11-03 to
2026-12-03), filtered in code for T and F. Neither reports in the window, so both are
recorded as "checked, nothing scheduled" and the preflight's blackout treats them as
clear.

```
CLEAN  research_errors: [] | thesis_errors: {} | valid: ['demo-T', 'demo-F']
TAMPER (F target driver 15.0938 → 16.5):
       "thesis text quotes 15.0938, which is not a cited driver value",
       "driver 'mean_price_target': value 16.5 does not match its source '15.0938'"
PROSE  (T sentence says target 29.5, drivers untouched):
       "thesis text quotes 29.5, which is not a cited driver value"
```

The first version only checked drivers. The dry run showed a thesis could cite the
right driver and still print a different number in the sentence the operator reads.
The sentence check closes that gap. No order tool was called.

## Deferred

- The ntfy push carrying the thesis text, and the `/rh-agentic-cycle` coordinator that
  runs agents 1→4 unattended → Phase 4.
- Antigravity mirrors of the agent definitions (no documented way to name MCP tools).

## Verification

See the PR description for the commands and results.
