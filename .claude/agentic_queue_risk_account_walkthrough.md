# Walkthrough: agentic queue gate account scope (portfolio heat)

## Problem
The burn-in ran zero cycles on 2026-11-13. Every BUY in `execution_queue.json` was `gate_allowed: false`
for `portfolio_heat 34.78% > 11%`. The daemon's gate builds its `RiskContext` from its account snapshot.
That is the robin_stocks default (main) account. Agentic orders go to the separate Agentic account, which
held nothing (0% heat). Full write-up: `docs/known_issues/agentic_queue_gate_wrong_account_heat_2026_11.md`.

## What changed
1. **`settings.AGENTIC_QUEUE_RISK_ACCOUNT`** (`main` default | `agentic`). It is in DANGEROUS_KEYS and
   ALLOWED_KEYS, and unknown values collapse to `main`.
2. **`execution/risk_gate.py`.** `PreTradeRiskGate.deferred_checks` is a frozenset, empty by default.
   `run_all` reports a deferred check as passed ("deferred to the agentic preflight") without calling it.
   The default is empty, so the paper broker and `OrderManager` paths are unchanged.
3. **`execution/queue_builder.py`.**
   - With `agentic`, it defers `portfolio_heat` and `max_position_size`.
   - The payload has a new top-level `deferred_checks` list, absent in `main`, so the default queue stays
     byte-identical (the golden-queue tests pin it).
   - A settings error defers nothing.
4. **`execution/agentic_preflight.py`, check 9.**
   - BUYs are vetoed when the Agentic account's adverse-only heat
     (`Σ max(0,(average_buy_price−last)×qty)/total_value`) exceeds `MAX_PORTFOLIO_HEAT`
     (`over_portfolio_heat`).
   - Unknown heat also vetoes (`heat_unavailable`): a held position with no average cost or quote,
     unreadable positions, no total value, or no limit configured.
   - The check is always on. It is veto-only, so it can only make the preflight stricter.
   - `Holding.avg_cost` parses `average_buy_price`, and 0 or missing becomes `None`.
   - `limits` now carries `portfolio_heat`, `max_portfolio_heat` and `heat_unpriced_symbols`.
5. **`.claude/agents/rh-risk-manager.md`.** The agent now quotes held symbols too, and explains the two
   new reasons and `deferred_checks`.

## Verification
- Targeted and neighbouring tests: queue builder, risk gate, preflight, agentic cycle and review, robinhood
  e2e, census, liveness, runtime flags, keysets, pipeline smoke, help content and agent-docs sync. All pass;
  see the PR for counts.
- **Offline replay of the real 2026-11-13 queue** against the cached main-account snapshot:
  - `main`: MFIN, SHEL and WFC are blocked by `portfolio_heat 34.78%`.
  - `agentic`: all three are `gate_allowed=True`, with `deferred_checks=[portfolio_heat, max_position_size]`.
  - Preflight on an empty Agentic account: heat 0.0. MFIN and SHEL pass. WFC hit `buying_power_exhausted`
    only because the replay used a made-up $100 balance for the stand-in account; that is not a finding.
- `make local-ci`: see the PR.

## Not verified
The live loop (daemon queue → 15-min cycle → preflight on real MCP output) runs only after the
operator sets `AGENTIC_QUEUE_RISK_ACCOUNT=agentic`.
