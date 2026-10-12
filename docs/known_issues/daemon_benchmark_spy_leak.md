# Daemon benchmark SPY leaked into the per-symbol pipeline (2026-10)

**Status:** fixed in code on branch `fix-advisory-spy-leak` (2026-10-11). Live effect needs a daemon restart after merge. The one leaked paper trade is left in the ledger, pending the operator's decision (see below).

## Symptom

- Every daemon cycle logged: `Advisory: 1 dashboard symbol(s) not in the cycle universe (evaluated anyway): SPY`. The earliest copy in the logs is 2026-09-30.
- The live shadow advisory source (`OUTPUT_DIR/shadow/queue_sources/advisory.json`) carried a SPY target, although SPY was not held, not on the watchlist and not discovered.
- The 2026-10-05 step-5 shadow review listed it as a cutover blocker: after the primary cutover the daemon could have queued a real Robinhood SPY order.

## Root cause

`main_orchestrator.fetch_all_data_async` adds SPY to the technical fetch. Three consumers legitimately need it there:
- the HMM regime input (`MacroStep`);
- `RS vs SPY` and the rolling beta (`processing_engine.py`);
- the Dual Momentum allocator (`StrategyEvalStep`).

`ProcessingEngine.compile_dashboard` builds one row per `tech_raw` key, so SPY became a full universe row. From there it went everywhere a tracked symbol goes:
- forecasting, with forecast-tracker rows;
- strategy evaluation and Kelly sizing;
- the advisory overlay, which evaluates dashboard rows outside `ctx.symbols` on purpose and warns about them;
- `BrokerExecutionStep` paper orders;
- symbol ratings and the state snapshot.

A second, separate leak: `StrategyEvalStep` computed cross-sectional 12-1m momentum over all of `tech_raw`. SPY therefore shifted every universe symbol's `XSec_Momentum_Rank`. `main.py`'s advisory path (`pipeline/advisory_inputs.py`) ranks universe bars only, so this was also a source of daemon-vs-`main.py` score drift in the shadow compare.

## Fix

- `main_orchestrator.BENCHMARK_SYMBOLS = ("SPY",)` names the injected list.
- `pipeline/production_steps.py::_drop_benchmark_only_rows` runs right after `compile_dashboard`.
  - It drops a benchmark row only when the symbol is not in the cycle universe, compared case-insensitively.
  - A held or watched SPY keeps its row, so it can still be sold.
  - SPY stays in `tech_raw`, so RS, beta, HMM and Dual Momentum are unchanged.
- The xsec momentum pass ranks `{t: tech_raw[t] for t in ctx.symbols}`.
- `AdvisoryOverlayStep`'s "evaluated anyway" warning is kept as a tripwire for any future leak path.
- Tests: `tests/test_benchmark_spy_leak.py`. Both pipeline tests were confirmed to fail against the pre-fix code.

## Leaked paper trade

The live `quant_platform.db` has one SPY round trip tagged `strategy_id = "main_pipeline"`. It is `paper_closed_trades.trade_id` 2: 1.301487 shares, entered 2026-09-30 14:34 UTC, closed 2026-10-01, realized P&L −$4.87. The leak produced it, not a tracked symbol.

- **Feature-freeze count:** excluded. The operator decided this on 2026-10-11. It is listed in `scripts/feature_freeze_status.py::EXCLUDED_TRADES` with its reason, and the script reports it as excluded. The ledger row is unchanged.
- **Kelly ledger: still included (open).** The row has `bridge_status = 'bridged'` (`bridged_trade_id` 1), so it was copied into the `transactions_store` `trades` ledger. Production Kelly reads that ledger unfiltered by strategy. Taking it out of Kelly stats means a live DB edit, which is the operator's call.

## Verify after deploy

After `launchctl kickstart -k gui/$(id -u)/com.investyo.stack`, check the next cycle:
- there is no SPY in `shadow/queue_sources/advisory.json`;
- there is no "evaluated anyway: SPY" log line;
- `universe_funnel.dashboard_rows` equals `tracked_universe_total` in `state_snapshot.json`.
