# Robinhood Four-Agent System — Phase 2 Walkthrough (Portfolio & Risk preflight)

Plan: `.claude/robinhood_four_agent_phase2_implementation_plan.md` (approved by the
operator, "Build as planned", 2026-11-03). Parent plan:
`.claude/robinhood_four_agent_trading_implementation_plan.md`. Phase 1 merged as #1134.

## Why

After Phase 1 every order goes to the operator's phone, but each one was still sized and
checked alone, against the daemon's snapshot rather than the live account. Two buys that
each fit the cash could both be sent; a full exit could ask to sell shares already sold.
Phase 2 adds one pure, tested function that checks the whole queue against the **live**
Agentic account and can only **remove** orders.

## What was verified live before building (read-only)

- Agentic account ••4848 ("MCP"): cash account, `total_value` 250, `buying_power` 250,
  no positions, no pending approvals.
- Real response shapes for `get_portfolio`, `get_equity_positions`
  (`shares_available_for_sells`), `get_equity_quotes` (`data.results[].quote`, with
  `bid_price`/`ask_price`/`last_trade_price`/`state`/`has_traded`) and
  `get_trade_approvals` (`data.approvals[]`, empty, so the item fields are still unknown).

## What changed

| File | Change |
|---|---|
| `execution/agentic_preflight.py` (new) | Parsers for the four MCP responses, `preflight()` (pure, veto-only), `write_preflight` / `read_preflight_for_queue` (atomic, matched to the queue's `generated_at`), CLI. |
| `.claude/agents/rh-risk-manager.md` (new) | Agent 3: read-only Robinhood tools only; saves raw MCP output, runs the CLI, explains the verdicts; never overrides them. |
| `.claude/skills/robinhood-execution/SKILL.md` + `.agents/` copy | New step 4b: place only `passed: true` intents at the preflight's `qty`/`limit_price`; re-review each at that qty; a veto is final; no usable preflight → in-chat path for every order. New invariant "Preflight vetoes are final." |
| `.claude/commands/rh-execute.md` | Mirrors step 4b. |
| `tests/test_agentic_preflight.py` (new) | 50 tests incl. a hypothesis property test. |
| `tests/test_robinhood_e2e.py` | The driver now calls the real `preflight()` on real-shape mock payloads; `TestPreflightIntegration` (6 tests); 3 existing assertions updated for the re-review and the earlier dedup layer; 6 new pinned SKILL.md phrases. |
| `tests/test_rh_agent_tool_allowlists.py` | rh-risk-manager is pinned to exactly the read tools. |
| `tests/test_pipeline_smoke.py` | New module added to the execution-zone AST guard. |
| Docs | `docs/architecture/execution.md`, `docs/architecture/testing.md`, `docs/RUNBOOK.md` (what each veto means), `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md`, the parent plan. |

## Design decisions worth knowing

1. **Veto-only** (operator's choice). The function resolves the exact share count with
   the existing rules (floor of target ÷ price; trims capped at sellable shares) but
   never shrinks an order to make it fit. An order that doesn't fit is removed with a
   reason, so you can see it.
2. **Highest conviction gets the cash first.** When buys together exceed buying power,
   the preflight keeps them in conviction order, with `client_order_id` breaking ties so
   the result is deterministic.
3. **BUY prices off the ask, SELL off the bid.** That's the side you'd actually trade
   at, so a BUY is never sized off a price that's too low.
4. **Full exits sell exactly the queue quantity**, fractional shares included. Flooring
   would silently leave a remainder. If the live account holds fewer sellable shares,
   it's a veto, not a quiet shrink.
5. **Unknown is a veto.** A field Robinhood didn't return, a halted quote, an
   unparseable earnings date, or an approval the code can't tie to a symbol each fail
   closed. "Positions read fine, holds nothing" (`{}`) is kept distinct from "couldn't
   read positions" (`None`).
6. **Missing preflight = stricter, not looser.** Without one, every order goes back to
   the in-chat question.

## Live dry run (read-only, real account state, hand-made queue, $200 cap)

```
T     buy passed=True  qty=7.0  limit=21.29  est=$149.03 reasons=[]
F     buy passed=False qty=12.0 limit=12.36  est=$148.32 reasons=['buying_power_exhausted']
AAPL  buy passed=False qty=0.0  limit=331.8  est=$0.0    reasons=['qty_below_one_share']
```

With $250 of cash, T (higher conviction) takes most of it, so F is vetoed. AAPL at about
$331 can't be bought as one whole share under a $200 cap. No order tool was called.

## Deferred (stated, not dropped)

- Check 10 (Analyst `data_gaps`) → Phase 3.
- Earnings blackout as an operator setting: today it is a caller-supplied window, off by
  default.
- The Antigravity mirror of the agent definitions (no documented way to name MCP tools
  there).

## Verification

See the PR description for the exact commands and results.
