# Walkthrough: Multi-Leg Options Paper Trading, Strategy Auto-Execution, Portfolio Risk Greeks & Stage 4 ML Pipeline (All 6 Phases Complete)

## Summary of All Completed Phases

We have completed the full end-to-end design, implementation, testing, and verification of all **6 phases** of the master plan:
1. **Phase 1: Multi-Leg Option Paper Trading Primitives**
2. **Phase 2: Automated Strategy Paper Execution Engine**
3. **Phase 3: Options Portfolio Risk & Aggregate Greeks**
4. **Phase 4: Options Backtest Harness Integration**
5. **Phase 5: Stage 4 ML Meta-Labeling & Model Feed**
6. **Phase 6: Interactive Options Strategy Backtester, ML Model Management & Expiration Settlement**

---

## What Was Built

### Phase 1: Multi-Leg Option Paper Trading Primitives
- **Order Sizing (`pilots/order_sizing.py`)**: `calculate_multi_leg_option_sizing` computing integer contracts based on max spread loss and target sizing.
- **Atomic SQLite Ledger (`data/paper_account_store.py`)**:
  - Multi-leg atomic fills via `apply_multi_leg_fill` across all legs.
  - Full support for short option positions (`qty < 0`).
  - Mark-to-market Black-Scholes valuation of option positions in portfolio equity calculation.
- **FMP Paper Broker Adapter (`execution/fmp_paper_broker.py`)**:
  - `submit_order` handles multi-leg `OptionLegRequest` with debit/credit pricing and limit marketability checks.
- **REST Endpoints (`api/pilots_api.py`)**: `POST /pilots/paper-broker/options/order`.
- **Pilots PWA (`webapp/`)**: UI option badges, short badges, and parity between `liveApi` and `mockApi`.

### Phase 2: Automated Strategy Paper Execution Engine
- **Engine (`execution/options_paper_executor.py`)**:
  - Scans quantitative options directives from `technical_options_engine.py` passing VRP and regime gates (`passes_premium_gate=True` and `Integrity_OK=True`).
  - Enforces single-position deduplication per underlying and max portfolio option cap (`MAX_CONCURRENT_OPTION_POSITIONS`).
  - Automatically calculates contracts via `calculate_multi_leg_option_sizing` capped by `MAX_OPTION_NOTIONAL_PER_TRADE`.
  - Executes atomic fills directly into `PaperAccountStore`.
- **Orchestration Hook (`main.py`)**:
  - Wired automated strategy options execution at the end of each advisory run cycle when `PAPER_OPTIONS_AUTO_EXECUTE_ENABLED=True`.
- **Pilots API (`api/pilots_api.py`)**:
  - `GET /pilots/paper-broker/strategy-options/candidates`
  - `POST /pilots/paper-broker/strategy-options/execute`
- **PWA Command Surface (`webapp/src/screens/PaperBroker.tsx`)**:
  - Automated Strategy Options candidates table and execution trigger button.

### Phase 3: Options Portfolio Risk & Aggregate Greeks Engine
- **Greeks & Risk Engine (`pilots/options_risk.py`)**:
  - `parse_option_symbol`: Parses OCC/format option symbols into ticker, expiration, strike, type.
  - `calculate_black_scholes_greeks`: Computes $\Delta$, $\Gamma$, $\Theta_{\text{daily}}$, and $\mathcal{V}_{\text{1\%}}$ for European/American approximation.
  - `calculate_position_greeks`: Computes per-position net Greeks factoring in short position quantities ($\text{qty} < 0$) and standard 100-share multipliers.
  - `calculate_portfolio_greeks`: Computes aggregate net Delta ($\Delta_{\text{net}}$), Dollar Delta ($\Delta_{\$}$), Gamma ($\Gamma_{\text{net}}$), Daily Theta ($\Theta_{\text{daily}}$), Vega ($\mathcal{V}_{\text{1\%}}$), and $\beta$-weighted SPY Delta ($\Delta_{\text{SPY}}$) across all equities and options.
- **REST Endpoint (`api/pilots_api.py`)**:
  - `GET /pilots/paper-broker/greeks`
- **PWA UI Cards & Table (`webapp/src/screens/PaperBroker.tsx`)**:
  - Greeks Summary Cards: Net Delta (shares & notional), Net Gamma, Daily Theta decay income/cost, Net Vega per 1% IV, and SPY $\beta$-Weighted Delta.
  - Position Table Columns: Displays Delta ($\Delta$), Daily Theta ($\Theta/\text{d}$), and Vega ($\mathcal{V}/1\%$) per row.

### Phase 4: Options Backtest Harness Integration
- **Options Harness (`validation/options_harness.py`)**:
  - Multi-leg strategy backtester supporting Put Credit Spreads, Call Credit Spreads, Iron Condors, Bull Call Spreads, Bear Put Spreads, and Straddles.
  - Daily Black-Scholes mark-to-market pricing with rolling realized volatility / VRP modeling.
  - Profit target exits (e.g. 50% max profit), stop-loss exits (e.g. 200% max credit loss), and intrinsic expiration cash settlement.
  - Full execution cost modeling via `TieredCostModel`.
  - Computes Sharpe, Sortino, Max Drawdown, Win Rate, Profit Factor, DSR, PBO, downsampled base-100 equity curves, and runs tail-scenario stress tests (`validation/stress_scenarios.py`).
- **Validation Harness Integration (`validation/harness.py`)**:
  - `StrategyValidationHarness.run_options_validation` helper.
  - CLI execution: `python -m validation.options_harness --strategy "Put Credit Spread" --ticker SPY`.

### Phase 5: Stage 4 ML Meta-Labeling & Dynamic Sizing
- **Meta-Labeling Classifier (`ml/options_meta_labeler.py`)**:
  - `OptionsTradeFeatureRow`: Extracts features (IVR, VRP, VIX, trend bias, target DTE, credit-to-width ratio, delta).
  - `OptionsMetaLabeler`: Fits a calibrated binary classifier predicting $P(\text{Win})$.
  - `get_sizing_multiplier`: Computes dynamic sizing scaling factor $\in [0.30, 1.50]$ (or $0.0$ to gate/reject low-confidence trades).
  - Model serialization: saves and loads weights to/from disk.
- **Integration with Execution Engine (`execution/options_paper_executor.py`)**:
  - `OptionsPaperExecutor` scores candidates using `global_options_meta_labeler`, filters out trades where $P(\text{Win}) < \text{min\_confidence}$, and dynamically scales contract size by the ML sizing multiplier.

### Phase 6: Interactive Options Strategy Backtester, ML Model Management & Expiration Settlement
- **Automatic Expiration & Cash Settlement (`data/paper_account_store.py`)**:
  - `settle_expired_options(market_provider=None, current_date=None)` scans open positions, finds expired options ($DTE \le 0$), computes intrinsic value, credits/debits cash, deletes closed positions, and records settlement ledger orders.
- **REST Endpoints (`api/pilots_api.py`)**:
  - `POST /pilots/options/backtest`: Runs on-demand options backtests and returns Sharpe, Sortino, MaxDD, Win Rate, Profit Factor, PBO, DSR, Stress results, and equity curves.
  - `GET /pilots/options/meta-model/status`: Returns sample size, training accuracy, and ROC-AUC of the Stage 4 ML Meta-Labeler.
  - `POST /pilots/options/meta-model/retrain`: Retrains the Stage 4 ML model across backtest and paper trade history.
  - `POST /pilots/paper-broker/settle-expired`: Triggers expiration settlement across open paper positions.
- **PWA Command Surface (`webapp/src/screens/PaperBroker.tsx`)**:
  - Header Button: "⏱ Settle Expired Options".
  - Stage 4 ML Meta-Labeler status card with live accuracy, ROC-AUC, sample size, and "⚡ Retrain Meta-Model" trigger.
  - Interactive Options Strategy Backtesting Harness panel with strategy dropdown, ticker, dates, and live performance metrics display.

---

## Verification Results

### Backend Python Pytest Suite
```bash
pytest tests/test_options_meta_labeler.py tests/test_options_harness.py tests/test_options_risk.py \
       tests/test_options_paper_executor.py tests/test_pilots_paper_broker.py \
       tests/test_paper_broker_options_order.py tests/test_paper_account_store.py \
       tests/test_fmp_paper_broker.py tests/test_order_sizing.py -v
```
**Result**: **84 passed**, 0 failed.

### Webapp TypeScript & Vitest Suite
```bash
npm run --prefix webapp typecheck
npm test src/screens/PaperBroker.test.tsx src/screens/OptionsChain.test.tsx src/components/options/OptionsOrderTicket.test.tsx
```
**Result**:
- `tsc --noEmit`: 0 errors.
- Vitest: **3 test files passed, 12 tests passed**.
