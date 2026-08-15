# Strategy & Options Backfill, Webapp Integration, and Dual-Agent Audit

- [x] Initial backtesting and validation of `sector_quality_rank`, `lgbm_ranker`, `vrp_premium_selling`, `put_credit_spread`, `call_credit_spread`, `call_debit_spread`, `put_debit_spread`
- [x] Write and benchmark compiled sequential execution core (`numba_backtest_loop.py`)
- [x] Rebuild CLI command manifest and shell completions for Commands tab
- [x] Declare `meta_label_features` for `vrp_premium_selling`, `options_flow_sentiment`, `sector_quality_rank` in `signals/`
- [x] Add unit test file for `numba_backtest_loop.py` (`tests/test_numba_backtest_loop.py`)
- [x] Run full 2005-present walk-forward backfill across strategies and options
- [x] Update `docs/VALIDATION_STRATEGY_FIX_LOG.md` and `docs/signals/options_flow_sentiment.md`
- [x] Create scoped `.claude/` PR artifacts (`.claude/evaluate_four_trading_options_*.md`)
- [ ] Dispatch Agent 1: `honesty-auditor` to audit codebase invariants, constraints, and data paths
- [ ] Dispatch Agent 2: Verification / Test agent to run full Python and Webapp test gates
- [ ] Stage and commit changes to worktree branch
