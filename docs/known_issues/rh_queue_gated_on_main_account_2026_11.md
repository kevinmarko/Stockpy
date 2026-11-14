# Robinhood queue gated on the main account, burn-in counted no days (2026-11)

**Status: fixed on branch `rh-queue-agentic-account-heat` (2026-11-13); pending merge and a stack
restart (the daemon does not hot-reload).** Found 2026-11-13, the day the Robinhood agentic burn-in
(review mode, $50 cap) started. Plan: `.claude/rh_queue_agentic_account_heat_implementation_plan.md`.

## Symptom

The burn-in counted no days. Every BUY in `execution_queue.json` had `gate_allowed=False` with
`portfolio_heat: portfolio heat 34.78% > limit 11.00%`. With nothing actionable, the 15-minute
`execution/agentic_cycle.py` tick skipped every time as `no_actionable_intents`, so no review cycle
ever completed. The Reviewer's READY verdict needs 10 days with a completed review cycle, so the
count stayed at 0.

## Root cause

A cross-account mismatch. `execution/queue_builder.py::_build_risk_context` builds the
`PreTradeRiskGate` context from the daemon's Robinhood snapshot (`data/robinhood_portfolio.py`:
robin_stocks `build_holdings()` / `load_portfolio_profile()` called with no account number, which
reads the default, i.e. main, account). The queue is only ever executed in the separate Agentic
account (`robinhood-execution` skill: "Agentic account only"). The main account's unrealized losses
therefore vetoed orders for a different account.

Of the four account-level checks, portfolio heat was the only one that bit. The others were already
no-ops in the queue: `max_correlation` (`returns_df=None`), `daily_loss_limit`
(`start_of_day_equity == equity`) and `max_position_size` (a $50 order against main-account equity).

## Fix

- `execution/risk_gate.py`: `PreTradeRiskGate.run_all(intent, context, *, skip=())` and the
  `ACCOUNT_LEVEL_CHECKS` set (`max_position_size`, `portfolio_heat`, `max_correlation`,
  `daily_loss_limit`). An unknown name in `skip` raises. The default runs every check, so
  `BrokerExecutionStep` (paper ledger) is unchanged.
- `execution/queue_builder.py`: `gate_intent` skips `ACCOUNT_LEVEL_CHECKS` (`QUEUE_DEFERRED_CHECKS`).
  The payload gains `deferred_account_checks` and `account_checks_owner: "agentic_preflight"`.
  Market and system checks (kill switch, macro, HMM, stress, market hours, validation, order rate)
  still run at queue time. Because `run_all` short-circuits, one of those can now be the first
  failure where heat used to be.
- `execution/agentic_preflight.py`: a BUY-only heat veto against the live Agentic account.
  `portfolio_heat()` = sum of max(0, (avg_cost - price) x qty) over held positions / `total_value`,
  priced off the last trade, else the bid. It fails closed: any missing cost basis, quote,
  positions or account value gives `None` and the veto `portfolio_heat_unavailable`. Heat over
  `MAX_PORTFOLIO_HEAT` gives `portfolio_heat_over_limit`. SELLs are never vetoed on heat. `Holding`
  gained `avg_cost`, parsed from `average_buy_price` (fallback `average_cost`). The report `limits`
  gain `portfolio_heat` and `max_portfolio_heat`.
- `.claude/agents/rh-risk-manager.md`: fetches quotes for queue symbols and held symbols so held
  positions can be priced.

## Not fixed here (follow-up)

- **SELL intents come only from main-account holdings — fixed 2026-11-14.** A position opened in the
  Agentic account never got a daemon exit intent. Rather than a second robin_stocks login path, the
  `rh-risk-manager` (each cycle) and `rh-reviewer` (daily) save the Agentic account's MCP positions to
  `OUTPUT_DIR/agentic/agentic_holdings.json` (`execution/agentic_holdings.py`); the daemon adds those
  symbols to the universe, evaluates them as held, and queues SELLs marked
  `"holding_source": "agentic"`, sized by the preflight from the Agentic account's own shares.
  The snapshot is ignored after 96 h; a position opened since the last refresh waits for the next one.
  `.claude/agentic_exits_speed_partial_fills_walkthrough.md`.
- **SELL quantity for names held in BOTH accounts — fixed 2026-11-14.** A full-exit SELL used the
  main account's held qty, so it either left Agentic shares behind or was vetoed as
  `sell_exceeds_sellable`. The queue now marks SELLs with `exit_kind` (`full` / `fraction` / `trim`)
  and the preflight sizes `full`/`fraction` from the Agentic account's own sellable shares
  (`.claude/agentic_trading_review_fixes_walkthrough.md`). Agentic-only positions still get no
  exit intent (the bullet above).
- **No daily-loss-limit measurement for the Agentic account** anywhere. It was a no-op before this
  change too.
- **`max_correlation` was already a no-op in the queue** (`returns_df=None`) and is still not
  measured for the Agentic account.
- BUY notional at queue time still comes from the main-account snapshot (equity x pct, capped by
  `ROBINHOOD_MAX_NOTIONAL_PER_ORDER`, $50 in practice).

## Verified live (2026-11-13, read-only)

- **Field name.** The Robinhood MCP `get_equity_positions` response uses `average_buy_price`; its own
  guide says the field "may be omitted for positions still reconciling". While a held position is
  reconciling, BUYs veto as `portfolio_heat_unavailable` (fail closed) until the cost basis appears.
- **Queue gate replay.** Gating MFIN, SHEL and WFC against the daemon's cached main-account
  snapshot: the old gate blocks all three at `portfolio heat 34.78% > limit 11.00%`; the new queue
  gate passes all three.
- **Preflight replay.** Run against the real Agentic reads ($250 cash, no positions, live quotes):
  heat is `0.0`, MFIN passes (3 shares, $40.20). SHEL and WFC veto as `qty_below_one_share`,
  because one share costs more than the $50 cap. That veto is pre-existing and unrelated to heat.
- **Still to see:** the first daemon-written queue after the stack restarts.

## How to verify

- `python -m execution.agentic_review status` shows the burn-in day count; the first day that can
  count is the Reviewer run after the next review cycle completes.
- The agentic cycle `.out` log: the tick reason should no longer be `no_actionable_intents`.
- `execution_queue.json` carries `deferred_account_checks` and BUYs show `gate_allowed=True` unless a
  market or system check fails; `preflight.json` limits show `portfolio_heat` (0.0 for an empty
  account).
- Tests: `tests/test_risk_gate.py`, `tests/test_queue_builder.py`, `tests/test_agentic_preflight.py`,
  `tests/test_robinhood_e2e.py`.
