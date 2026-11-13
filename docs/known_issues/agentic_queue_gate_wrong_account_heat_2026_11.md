# Agentic queue gate judged portfolio heat on the wrong account (2026-11)

**Status:** Fixed in code (2026-11-13). Opt-in: the operator sets
`AGENTIC_QUEUE_RISK_ACCOUNT=agentic`; the default `main` keeps the old behaviour.

## Symptom

The Phase 5 burn-in started 2026-11-13 in review mode with a $50 cap. By the close it had run
**zero** agent cycles. `output/agentic_cycle.out` showed `queue_stale`, then
`no_actionable_intents` on every market-hours check. Every BUY in `execution_queue.json` was
`gate_allowed: false` with one reason:

```
portfolio_heat: portfolio heat 34.78% > limit 11.00% — halting new long exposure
```

(That day's queue: MFIN, SHEL and WFC, $50 each.)

## Root cause

`execution/queue_builder.py::_build_risk_context` builds the gate's `RiskContext` from
`ctx.snapshot`. That snapshot comes from `main_orchestrator.fetch_account_snapshot()`, which uses
robin_stocks with no account number, so it reads the **default (main) margin account**. Agentic
orders go to a different account: the Agentic cash account, the only one the Robinhood MCP lets an
agent trade. On 2026-11-13 that account held nothing, so its real heat was 0%. The main account's
unrealized losses were blocking trades on an account they have nothing to do with.

`PreTradeRiskGate` has two account-dependent checks that matter here:
- `portfolio_heat` was the one actually firing.
- `max_position_size` was wrong in principle (main-account equity) but passed.

`daily_loss_limit` is a no-op in this context (start-of-day equity is set equal to equity), and
`max_correlation` is skipped (`returns_df=None`). The agentic preflight
(`execution/agentic_preflight.py`) already read the real Agentic account and enforced position weight,
gross and buying power, but it had no heat check.

## Fix

- **`execution/agentic_preflight.py`, check 9 (always on, every BUY).** Heat on the Agentic account:
  `Σ max(0, (average_buy_price − last) × qty) / total_value`, the same adverse-only definition as the
  gate, against `MAX_PORTFOLIO_HEAT`.
  - Over the limit: `over_portfolio_heat`.
  - A held position with no average cost or no quote, unreadable positions, or no total value:
    `heat_unavailable` (fails closed).
  - An empty account has 0% heat.
  - The `rh-risk-manager` agent now fetches quotes for held symbols as well as queued ones.
- **`AGENTIC_QUEUE_RISK_ACCOUNT` (`main` | `agentic`, default `main`, a `DANGEROUS_KEYS` member).**
  - With `agentic`, `build_execution_queue` sets
    `PreTradeRiskGate.deferred_checks = {portfolio_heat, max_position_size}`. Those two report "passed,
    deferred" without being evaluated, and the queue payload lists them under `deferred_checks`.
  - Every other gate check still runs: kill switch, macro, HMM, stress, market hours, validation and rate.
  - Unknown values collapse to `main`.
- `deferred_checks` defaults to empty, so the paper `BrokerExecutionStep` and `OrderManager` paths
  are unchanged.

## Rejected

- **Reading the Agentic account inside the daemon** (robin_stocks with an `account_number`). It would
  add a new login/data path to a component that logs in by device approval only. The preflight
  already reads the right account over the MCP.
- **Raising `MAX_PORTFOLIO_HEAT`.** That would hide the wrong-account bug, not fix it.
- **Making `agentic` the default.** The default-value policy keeps trading-behaviour changes opt-in.

## Not verified

- The live end-to-end path (daemon queue, then the 15-min cycle, then the preflight on real MCP output)
  is verified only after the operator flips the setting. The first market-hours queue after that
  should show `deferred_checks: ["portfolio_heat", "max_position_size"]` and `gate_allowed: true` for
  BUYs that pass the other checks.
