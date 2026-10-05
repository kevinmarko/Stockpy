# Daemon strategy Score always NaN (2026-10-05)

**Status: Fixed** (branch `fix-daemon-empty-score`). Daemon symbol-rating writes are deliberately left off; see "What was not changed".

## Symptom

Every daemon-written `state_snapshot.json` since at least 2026-08-13 (805 of 805 files in `OUTPUT_DIR/history/`) had `score = NaN` for every symbol, while `score_components` was populated. Downstream:

- Symbol Detail's "Score" row was empty.
- The HTML report's "Advisory Score" column was empty.
- `DailySignals.Score` was NaN, so `investyo_mcp_server.py`'s `ORDER BY "Score"` query ranked nothing.
- A row with no `Score` column at all got a fabricated `0.0` in the snapshot (`float(row.get("Score", 0.0) or 0.0)`), a CONSTRAINT #4 violation.

## Root cause

`StrategyEvalStep.run()` (`pipeline/production_steps.py`) NaN-filled `dashboard_df['Score']` together with the advisory-only metadata columns (`Forecast_30_Pct`, `Advisory_Conviction`, …), on the assumption that `Score` belonged to the advisory path. It doesn't: `StrategyEngine.evaluate_security()` returns it (`strategy_engine.py`, `"Score": final_score`). The value was never copied into `eval_results` or written back, so the NaN fill stood.

Trading decisions were not affected: the engine picks the Action Signal from `final_score` internally, and the advisory overlay calls the engine itself.

## Hidden side effect

`_record_symbol_ratings()` skips rows whose Score is NaN, so the daemon has **never** written a symbol rating. All rows in `symbol_rating_events` come from `main.py`'s advisory path (`pipeline/steps.py`), which runs once a day.

## Fix

- `eval_results` carries `strategy_output.get('Score')`. The new helper `_apply_strategy_score_column()` writes it per symbol: NaN for symbols that were not evaluated, and a genuine 0 stays 0.0.
- The snapshot writer (`main_orchestrator.py::_write_state_snapshot`) uses `_safe_float_or_none`, so a missing score is JSON `null`, never `0.0` and never a bare `NaN` token.
- Tests: `tests/test_strategy_eval_step_score_column.py`, `tests/test_state_snapshot_parity.py::TestOrchestratorStrategyScore`, `tests/test_orchestrator_e2e.py::…::test_strategy_score_populated_via_real_run_pipeline`.

## Rating writes: once per trading day (follow-up, 2026-10-05)

With Score fixed, the daemon would have written ratings every cycle. `SYMBOL_RATING_AUTO_DROP_ENABLED` is on in the live config, with a threshold of 5 consecutive BAD *cycles*. At the daemon's hourly cadence, an unheld watchlist or scan symbol could be dropped within hours instead of after about 5 trading days. The first fix therefore kept daemon writes off.

The operator then chose a once-per-trading-day cadence. `pipeline/production_steps.py::_daemon_should_record_ratings` allows a write only when both hold:
- the US market is open (`engine.advisory_agent.is_us_market_open_now`, holiday-aware);
- no rating cycle exists yet for today's America/New_York date (`SymbolRatingStore.has_cycle_since`, which ignores `manual_reinclude` rows).

Any error means "don't write". `main.py` records its cycle at about 08:47 ET, before the open, so while it runs the daemon skips. If `main.py` didn't run, or once it is archived (step 5.5), the first daemon cycle during market hours records the day's single cycle. Pinned by `tests/test_symbol_rating_wiring.py::TestDaemonDailyRatingCadence` and `tests/test_symbol_rating.py::TestHasCycleSince`.

Open pipeline paper positions are unaffected either way: `pipeline/advisory_inputs.py::_open_pipeline_paper_symbols` keeps them in the universe even after a rating drop.
