# Strategy & Options Backfill, Webapp Integration, and Dual-Agent Audit

- [x] Initial backtesting and validation of `sector_quality_rank`, `lgbm_ranker`, `vrp_premium_selling`, `put_credit_spread`, `call_credit_spread`, `call_debit_spread`, `put_debit_spread`
- [x] Write and benchmark compiled sequential execution core (`numba_backtest_loop.py`)
- [x] Rebuild CLI command manifest and shell completions for Commands tab
- [x] Declare `meta_label_features` for `vrp_premium_selling`, `options_flow_sentiment`, `sector_quality_rank` in `signals/`
- [x] Add unit test file for `numba_backtest_loop.py` (`tests/test_numba_backtest_loop.py`)
- [x] Run full 2005-present walk-forward backfill across strategies and options
- [x] Implement institutional quantitative metrics (`profit_factor`, `ulcer_index`, `ulcer_performance_index`, `walk_forward_efficiency_ratio`) in `validation/metrics.py`
- [x] Add dynamic margin and volatility slippage model to `numba_backtest_loop.py` (`run_numba_backtest_with_margin`)
- [x] Add `tests/test_institutional_metrics.py` (4/4 passed)
- [x] Construct `_build_options_flow_sentiment_adapter` in `scripts/refresh_validations.py` and register in `STRATEGY_REGISTRY` & `pilots/catalog.py`
- [x] Update `docs/VALIDATION_STRATEGY_FIX_LOG.md` and `docs/signals/options_flow_sentiment.md`
- [x] Create scoped `.claude/` PR artifacts (`.claude/evaluate_four_trading_options_*.md`)
- [x] Dispatch Agent 1: `Institutional Quantitative Auditor` (PASS)
- [x] Dispatch Agent 2: `Systems & Catalog Auditor` (PASS)
- [x] Stage and commit changes to worktree branch
