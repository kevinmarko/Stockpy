# fix-daemon-empty-score — walkthrough

**Problem.** The daemon's `state_snapshot.json` had `score = NaN` for every symbol in all 805 history snapshots since 2026-08-13. `StrategyEvalStep.run()` NaN-filled `dashboard_df['Score']` as advisory-only metadata, but the score is StrategyEngine's own (`strategy_engine.py:513`), and it was never copied back.

**Fix.**
- `eval_results` now carries the engine's Score.
- `_apply_strategy_score_column()` writes it per symbol: NaN when the symbol wasn't evaluated, and a genuine 0 stays 0.
- The call is wrapped in try/except, so a failure degrades to NaN and never aborts the cycle.
- The snapshot writer emits `null` for a missing score instead of NaN or a fabricated 0.0.

**Deliberately unchanged.** `_record_symbol_ratings` skipped NaN scores, so the daemon has never written ratings. Auto-drop is on live with a threshold of 5 cycles, so hourly daemon writes would drop unheld symbols within hours. It is gated off by `_DAEMON_RECORDS_SYMBOL_RATINGS = False` (operator decision). Before step 5.5 it needs a once-per-trading-day cadence.

**Verification.**
- ruff (genuine-bug rules): clean.
- `make ci`: 11630 passed, 24 skipped.
- Targeted suites: 127 passed, including a real `run_pipeline()` e2e test asserting a numeric 0–100 score in the snapshot.
- Honesty audit: no CONSTRAINT #4/#6 violations. Its hardening suggestion (try/except around the helper) was applied.

**Not yet verified.** The live daemon. It picks up the change only after a restart.
