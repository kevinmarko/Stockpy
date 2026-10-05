# Pipeline paper trades recorded no entry context and no real exit reason

**Status:** Fixed (2026-10-05), forward-only. Trades opened or closed before the fix keep no context.

## What was wrong

The feature freeze (step 7) ends on the evidence of the pipeline's own closed paper trades
(`paper_closed_trades.strategy_id = 'main_pipeline'`). Those trades could not explain themselves:

- **Entry context was never recorded.** `execution/fmp_paper_broker.py::submit_order` called
  `PaperAccountStore.apply_fill` with only symbol/side/qty/price/strategy_id. `_create_entry_snapshot`
  writes a snapshot only when context is supplied, the strategy is `"Manual Trade"`, or a `pilot_id` is set.
  `main_pipeline` met none of these, so it wrote nothing. That is the anti-fabrication rule working as
  designed. Live DB on 2026-10-05: 4 snapshots, all Manual Trade stubs; 0 for `main_pipeline`; 8 open pipeline
  positions with `entry_snapshot_id = NULL`.
- **Every close said `flatten`.** `apply_fill` hard-coded `"flatten"` at both equity close sites, so the one
  closed pipeline trade (SPY, 0.96 days, -$4.87) cannot say which exit signal fired. Dual Momentum's
  safe-asset switch only zeroes `Kelly Target` and never changes `Action Signal`. So the exit was an
  `Action Signal` in `{SELL, TRIM, RISK REDUCE, AVOID}`, but which one is lost.

## Fix

- New `execution/trade_context.py` builds an entry or exit context from the same `final_df` row the
  decision used. `main_orchestrator._execute_broker_orders` attaches it as `OrderIntent.decision_context`
  **after** the trading fields are final.
- `FMPPaperBroker` forwards it to `apply_fill`, which writes a `signal_driven` snapshot on open. On close it
  records `close_reason = signal_sell | signal_trim | signal_risk_reduce | signal_avoid` plus
  `paper_closed_trades.exit_context_json`.
- The retrospective composer and narrative surface both.
- The context is inert for trading. Sizing, the probe, the risk gate, the kill switch and
  `make_client_order_id` never read it. A test runs the order loop with the builders working and with
  them forced to raise, and gets identical orders and client_order_ids, with the priority queue on and off.
- Failures fail open for the order: the order goes out with `decision_context=None`.
- The snapshot `conviction` column stays `None` for pipeline trades. The strategy engine has no 0-1
  conviction. The advisory engine's same-cycle number is kept only as
  `key_indicators_json.advisory_conviction_same_cycle`, so retrospective calibration never silently
  measures a different engine.

## Not done (deliberately)

- **No backfill.** Rebuilding the 8 open positions' or SPY's entry context from `DailySignals` would be
  reconstruction, which the snapshot design forbids. Up to 8 of the first 30 freeze trades will show entry
  context `not captured`. They will still get a real `close_reason`, because that is written at close time.
- The webapp shows the improved `close_reason` in the Paper Broker table. Rendering `exit_context` in the
  retrospective modal is a follow-up.

Plan and walkthrough: `.claude/paper_trade_entry_exit_context_{implementation_plan,walkthrough}.md`.
