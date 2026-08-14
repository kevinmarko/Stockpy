# Task Tracker: Backtest Missing Strategies (Phases 1-3)

- [x] Phase 1: 5 Options Strategies Backtesting <!-- id: 1 -->
    - [x] Generalize options simulator in `validation/options_selling_backtest.py` for Credit & Debit Spreads <!-- id: 1.1 -->
    - [x] Implement backtest adapters in `scripts/refresh_validations.py` (`put_credit_spread`, `call_credit_spread`, `call_debit_spread`, `put_debit_spread`, `covered_call`) <!-- id: 1.2 -->
    - [x] Wire stress test scenarios for options selling strategies <!-- id: 1.3 -->
    - [x] Add unit tests in `tests/test_options_selling_backtest_stress.py` & registry tests <!-- id: 1.4 -->
    - [x] Run validation harness & document results in `docs/signals/` and `docs/VALIDATION_STRATEGY_FIX_LOG.md` <!-- id: 1.5 -->
- [x] Phase 2: Standalone Signal & Analytic Engines Backtesting <!-- id: 2 -->
    - [x] Implement `pairs_trading` backtest adapter (`_build_pairs_trading_adapter`) on cointegrated liquid pairs <!-- id: 2.1 -->
    - [x] Implement `aroon_trend` standalone breakout & trend adapter (`_build_aroon_trend_adapter`) <!-- id: 2.2 -->
    - [x] Implement `sentiment_index` / `news_catalyst` historical proxy adapter <!-- id: 2.3 -->
    - [x] Add unit tests & registry tests for Phase 2 strategies <!-- id: 2.4 -->
    - [x] Run validation harness & document results in `docs/signals/` and `docs/VALIDATION_STRATEGY_FIX_LOG.md` <!-- id: 2.5 -->
- [x] Phase 3: Optimize & Re-Validate 4 Non-Deployable Strategies <!-- id: 3 -->
    - [x] Optimize `vrp_premium_selling` (Iron Condor stop-loss & dynamic delta adjustment) <!-- id: 3.1 -->
    - [x] Optimize `rsi2_mean_reversion` (empirical turnover & trend-aligned pullbacks) <!-- id: 3.2 -->
    - [x] Optimize `rsi14_extremes` (trend-filtered mean reversion) <!-- id: 3.3 -->
    - [x] Optimize `forecast_direction_arima_hw` (forecast magnitude threshold & vol parity) <!-- id: 3.4 -->
    - [x] Run full 19+ strategy validation suite sweep & update documentation <!-- id: 3.5 -->
