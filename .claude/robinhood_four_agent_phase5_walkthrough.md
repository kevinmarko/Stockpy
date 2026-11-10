# Robinhood Agentic System — Phase 5 Walkthrough (Agent 5 "Reviewer" + burn-in tooling)

Plan: `.claude/robinhood_four_agent_phase5_implementation_plan.md` (operator: "Build as
planned", 2026-11-10, after asking for "the next phase with 5 agent"). Parent plan:
`.claude/robinhood_four_agent_trading_implementation_plan.md`. Phases 1–4 merged as
#1134–#1137.

## Why

Phase 5 is the burn-in: about 10 trading days in `review`, then `live` with a small cap.
Its pass criteria ("receipts reconcile 1:1 with fills; zero placements while approvals
were off"), and the review of each decision against its thesis, had nothing measuring
them. The fifth agent does that measuring, and the verdict is code.

## What changed

| File | Change |
|---|---|
| `execution/agentic_review.py` (new) | Saves raw MCP inputs; turns MCP orders into fills through the existing pure parser; reconciliation; five safety invariants; outcomes vs. theses; cycle health; `burnin_status`; `review.json`; the daily push text; `launch` for the timer; CLI `save/run/status/launch`. |
| `execution/receipts_store.py` | `reconcile()` takes an optional `fills=` so the Agentic account's fills never go through `fetch_filled_orders`. |
| `.claude/agents/rh-reviewer.md` (new) | Agent 5, read-only: five Robinhood read tools (both server spellings) plus Read/Grep/Glob/Bash. |
| `.claude/commands/rh-agentic-review.md` (new) | Runs the Reviewer and sends the daily push from `review.json`. |
| `scripts/com.investyo.rh-agentic-review.plist` (new) | Weekdays 16:40 local; runs `python -m execution.agentic_review launch`. Not auto-installed. |
| `shared/agentic_approvals.py`, `webapp/` | `GET /agentic/approvals` gains `burnin`; the card shows "Burn-in day n/10" with a Ready/Not ready chip and the unmet items. |
| Tests | `tests/test_agentic_review.py` (52), a `burnin` test in `tests/test_agentic_approvals.py`, guard list. |
| Docs | RUNBOOK "Phase 5 burn-in", HOW_TO_GUIDE, architecture (execution, testing, APIs, webapp), safety framework, parent plan (P5 row + Agent 5 section), `CLAUDE.md`/`AGENTS.md`; census and liveness regenerated. |

## Things found while building

- **The existing fill fetcher would have corrupted the main account's cache.**
  `data.robinhood_orders.fetch_filled_orders` reads `cache/robinhood_orders.json` when
  it's fresh, and writes it after every fetch. That cache holds the main account's order
  history. Feeding it Agentic-account orders would have reconciled against the wrong
  account and overwritten the cache. The Reviewer uses the pure `parse_orders` instead,
  and `reconcile(fills=...)` skips the fetch (pinned by
  `test_never_touches_the_shared_order_cache`).
- **Robinhood gives direct evidence for "nothing executed without a tap."** Its order
  records carry `placed_agent`: an order an agent submitted itself reads `agentic`,
  while one the operator approved in the app reads `user`. The invariant
  `order_without_phone_approval` uses that field, not an inference from our own files.
- **The live order shape is unconfirmed.** The Agentic account has no orders yet
  (read-only `get_equity_orders` returned `[]`), so the parser follows Robinhood's
  documented fields. A filled row without a symbol is counted as unresolved, never
  guessed. The first real review will confirm the shape.

## Verification

- `pytest tests/test_agentic_review.py tests/test_agentic_approvals.py
  tests/test_receipts_store.py` plus the Phase 1–4 files, the pilots API, pipeline
  smoke, census/liveness and docs-sync gates: see the PR for counts.
- `npm run --prefix webapp typecheck` clean. Playwright checked `/agentic` on mock data
  at 1280 and 390 px: the burn-in line renders, with no console errors. A real
  `uvicorn api.pilots_api:app` served the `burnin` block, which matched the
  `AgenticBurnin` type.

## Honest limits

- The Reviewer detects problems after the fact; it doesn't prevent them. Prevention is
  still the phone tap and the preflight.
- "Trade approvals were on" is known for the day of each review, not continuously.
  The `placed_agent` check covers the gap for executed orders.
- A real burn-in can't run in this cloud session. It needs the operator's Mac.
