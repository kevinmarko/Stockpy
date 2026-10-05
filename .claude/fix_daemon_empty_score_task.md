# fix-daemon-empty-score — task tracker

- [x] Root-cause: StrategyEvalStep NaN-filled Score; eval_results never copied it
- [x] Check trading-behavior readers of Score (advisory overlay independent; _record_symbol_ratings would activate)
- [x] Operator decision: keep daemon rating writes off
- [x] `_apply_strategy_score_column` + eval_results Score + guarded call
- [x] `_DAEMON_RECORDS_SYMBOL_RATINGS = False` gate
- [x] Snapshot score → `_safe_float_or_none` (null, never 0.0/NaN)
- [x] Tests: helper, parity, wiring gate, real-pipeline e2e
- [x] Docs: known issue + index, step 5.5 trap note, config comment
- [x] ruff + `make ci` (11630 passed, 24 skipped) + honesty audit
- [ ] Merge, then (with operator OK) restart daemon and confirm live snapshot scores
