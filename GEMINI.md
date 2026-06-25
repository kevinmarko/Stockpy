Project Context: Stock Dashboard Py (InvestYo Quant Platform)

1. Overview

Purpose: An automated, institutional-grade quantitative analysis pipeline that fetches financial data, calculates technical/fundamental indicators, performs multi-horizon forecasting, simulates trading strategies via backtesting, stores historical signals and runs locally, updates a Google Sheets dashboard, and validates architecture via a 6-step AI Verification Suite.

Target Audience: Kevin Marko Lee (Individual Investor / Quantitative Analyst).

Key Features:

Automated Data Ingestion: Uses Yahoo Finance for equities and FRED for macroeconomic data.

Advanced Processing: Calculates Graham/Gordon valuations, Risk metrics (VaR/Sortino), and Technicals (RSI/MACD/Aroon).

Sector-Specific Forecasting: Employs ARIMA, Monte Carlo, and Holt-Winters statistical models.

Simulation & Backtesting: Matrix-based parameter optimization via vectorbt and event-driven realistic simulation (accounting for slippage and commissions) via backtrader.

SQLite Database Integration (New): Transitions flat-file storage to an institutional SQLite schema initialized via database_setup.py. Tracks DailySignals (dynamically aligned to config keys) and ExecutionLogs. Uses SQLAlchemy and psycopg2-binary for robust database architecture.

Google Sheets Integration: Reads input tickers and writes analyzed output seamlessly with automated conditional formatting.

Advanced Research Metrics (New): Computes realized portfolio slippage from historical transactions, tail dependency risk (CoVaR proxy correlation matrix), Brinson-Fachler Sector Attribution, Maximum Favorable/Adverse Excursion (MFE/MAE), and Global Portfolio Heat.

Fundamental Processing: The ProcessingEngine strictly maps Graham Number and Gordon Fair Value valuations independently to prevent dictionary key collisions.

AI Verification Suite (New): A 6-step static analysis and simulation sandbox managed by ai_verification_prompts.py to ensure rigorous auditing of strategies prior to deployment. Uses openai/anthropic for LLM agent integration.

2. Tech Stack

Language: Python 3.12 (Enforced via setup.sh)

Core Libraries: pandas, numpy, yfinance, fredapi, statsmodels, pandas_ta, vectorbt, backtrader, pandera, pydantic, arch, prophet, google-cloud-language, QuantFAA, scikit-learn, scipy, openai, anthropic.

Database/Storage: SQLite (quant_platform.db), SQLAlchemy, psycopg2-binary, and Google Sheets (via gspread and gspread_dataframe).

Configuration: config.py (Single Source of Truth for schema).

Authentication: Google Service Account (credentials.json).

3. Architecture & Patterns

Folder Structure: Flat, modular "Engine" architecture designed for Dependency Injection:

main.py (Orchestrator: Coordinates engines and database / Google Sheets I/O)

data/robinhood_portfolio.py (READ-ONLY portfolio snapshot — ADVISORY ONLY, NO ORDER CODE. TOTP authentication via pyotp.TOTP(RH_MFA_SECRET).now() + robin_stocks.login(store_session=True, mfa_code=..., by_sms=False). Daily cache at cache/account_snapshot.json: fetch_account_snapshot(max_age_hours=20.0, force=False) returns cached snapshot instantly when fresh (no login), triggers live fetch when stale or absent, falls back to stale cache on live-fetch failure, raises only if live fails AND no cache exists. PortfolioPosition (frozen dataclass): symbol, quantity, average_cost, current_price, market_value, unrealized_pl, unrealized_pl_pct, dividends_received, name — with to_dict()/from_dict() JSON round-trip. AccountSnapshot (frozen dataclass): positions dict[str,PortfolioPosition], buying_power, total_equity, total_dividends, fetched_at (UTC-aware) — plus age_hours()/is_stale(max_age_hours) helpers. Dividend correlation: only "paid" and "reinvested" states counted; UUID extracted from instrument URL path. Per-symbol failures are logged and skipped — never abort the snapshot. Credentials from os.environ: RH_USERNAME, RH_PASSWORD, RH_MFA_SECRET.)

data_engine.py (Fetcher: Ingests external API data, implements IDataProvider abstract interface. IDataProvider now also declares fetch_macro_history() -> pd.DataFrame -- full historical VIX/yield-curve series for regime/hmm_regime.py's expanding-window fit, distinct from fetch_macro_raw()'s single current-snapshot dict. DataEngine.fetch_macro_history() pulls unbounded FRED series via self.fred.get_series('VIXCLS'/'T10Y2Y'); returns an empty DataFrame, never fabricated rows, if FRED is unavailable. MockDataEngine.fetch_macro_history() returns a deterministic seeded 500-row synthetic series for offline tests.)

processing_engine.py (Calculator: Vectorized mathematical indicators and fundamental calculations)

macro_engine.py (Top-down macro risk assessment: rules-based "MACRO FREEZE" regime classification via MacroEngine.run_macro_killswitch(), Fama-French 3-factor alpha isolation, and Google Cloud NL sentiment. MacroEngine.__init__ now constructs a persistent regime.hmm_regime.HMMRegimeDetector(n_states=3, retrain_freq_days=7) instance. compute_hmm_risk_on_probability(spy_price_df) builds the 4-feature matrix via regime.hmm_regime.build_feature_matrix() (using self.data_engine.fetch_macro_history() for VIX/yield-curve history), fits/refits, and returns the HMM's risk_on_probability second opinion -- or None (never fabricated) if SPY history, macro history, or the aligned feature matrix (<HMM_MIN_FIT_ROWS=100 rows) is insufficient, or if fit/predict raises -- logged, never propagated, since a statistical second opinion failing must never crash the primary rules-based pipeline.)

regime/hmm_regime.py (Gaussian HMM regime detector, Hamilton 1989 -- a statistical second opinion to macro_engine.py's rules-based regime, never an independent override. build_feature_matrix(spy_price_df, vix_series, yield_curve_series) builds spy_return/realized_vol_20d/vix_level/yield_curve_spread, normalizing all three inputs' indices to timezone-naive, time-stripped dates before aligning -- yfinance is often tz-aware/intraday-stamped, FRED is naive/midnight, and without this normalization misaligned sources silently produce an all-NaN join. HMMRegimeDetector(n_states=3, retrain_freq_days=7): fit(features_df) refits only if retrain_freq_days have elapsed since the last real fit (expanding window is the caller's responsibility); within that window repeated fit() calls are no-ops -- exactly what tests/test_hmm_no_lookahead.py exercises. identify_states_by_vol() sorts states by fitted diagonal-covariance variance ascending, labels ["bull","sideways","bear"]. predict_proba(features_df) returns FORWARD (filtered) probabilities at the LAST ROW only -- never Viterbi/smoothing -- via the identity that a smoothed posterior's last row in any sequence equals pure forward filtering (the backward pass is seeded with beta_T=1 at the sequence's own terminal step, so there is no "future" to smooth with). Callers MUST slice their feature frame to end exactly at the date they want a probability for.)

research_engine.py (Researcher: Computes realized slippage and CoVaR tail risk metrics)

strategy_engine.py (Decision Maker: Institutional signal generator, calibrated momentum/trend weights, and tactical risk zone sizing. Utilizes an Aroon Oscillator Chop-Filter to suppress false-positive MACD signals. Position sizing ("Kelly Target") is now volatility-targeting + estimated-p fractional Kelly via the sizing/ package, not a score-derived formula. NEW (dedicated sell-side range): apply_sell_side_range produces a first-class sellRange string for EVERY Action Signal alongside the legacy apply_tactical_ranges/buyRange. Active long signals (STRONG BUY/BUY/HOLD) emit "Sell Zone: $LO - $HI | Stop @ $STOP" where LO = price + 1.5*ATR, HI = max(price + 3*ATR, forecast_30) -- forecast wins only when above the ATR ceiling, never fabricated when forecast_price = 0 -- and STOP = chandelier_long (fallback price - 2.5*ATR, clamped >= $0.01). RISK REDUCE / unknown signals fail closed to "Sell Now @ market | Stop @ $STOP" (chandelier_long, fallback price - 1.0*ATR). Lookahead-free: consumes only the already-causal ATR, Chandelier Exit, and Forecast_30 already flowing into evaluate_security. Registered in config.COLUMN_SCHEMA as {"header":"Sell Range","key":"sellRange","format":"string"} immediately after buyRange; propagated by both main.py (Sheets sink) and main_orchestrator.py (dashboard_df, JSON payload, state_snapshot.json's per-signal "buy_range"/"sell_range" fields) and rendered in daily_report_template.html as a summary table column AND a per-ticker advice card row.)

sizing/kelly.py, sizing/vol_target.py (Position Sizing: single source of truth for "Kelly Target", replacing two divergent arbitrary score-derived win-probability formulas previously in strategy_engine._calculate_kelly_sizing and a main_orchestrator.py call into evaluation_engine.calculate_kelly_target. kelly.py: estimate_win_rate_and_payoff(closed_trades_df, lookback_trades=100) -> (p, b, n_trades) from transactions_store.TransactionsStore.closed_trades_df(), NaN if n_trades<30; fractional_kelly(p, b, fraction=0.5, cap=0.20) -> f*=(p*b-(1-p))/b scaled and capped, NaN-safe. vol_target.py: volatility_target_weight(realized_vol, target_vol=0.10, max_leverage=2.0) -> target_vol/realized_vol capped at max_leverage; portfolio_vol_target(positions, cov_matrix, target_vol, max_leverage) -> dict scaling a position vector to sqrt(w^T*Sigma*w)==target_vol, excluding (weight 0.0, logged) symbols missing covariance data.)

forecasting_engine.py (Predictor: Statistical modeling and multi-horizon forecasts. Strictly enforces deterministic trend parameters (trend='t' in ARIMA, trend='add' in Holt-Winters) and structural drift (μ - 0.5 * σ^2) in Monte Carlo to prevent naive flatline projections. The CNN-LSTM model is refactored to perform direct multi-horizon forecasting, train once per ticker, and fit scalers/sequence windows strictly on the training partition to eliminate lookahead bias)

simulation_engine.py (Simulator: VectorBT optimization and Backtrader simulations, integrating survivorship bias warning prints via universe_engine.py)

universe_engine.py (Universe Loader: Scrapes Wikipedia current/changes tables to reconstruct S&P 500 constituents, seeds local delistings from data/delisted_tickers.csv, and outputs estimated survivorship bias reports)

database_setup.py (Schema Builder: SQLite database and dynamic mapping creator)

execution/cost_model.py (Execution: Tiered execution cost model and Backtrader CommissionInfo)

validation/purged_cv.py (CPCV partitioner: generates combinatorics train/test splits, applying purging and embargoes)

validation/metrics.py (Valuator: computes standard Sharpe, Deflated Sharpe Ratio (DSR), Probability of Backtest Overfitting (PBO), and runs CPCV walks)

validation/harness.py (Validation Harness: runs walk-forward splits & CPCV, computes Sharpe, Sortino, Calmar, Max DD, Turnover, Hit Rate, PBO, DSR, and renders reports. StrategyValidationHarness now accepts is_options_selling=False and stress_returns_fn=None; for options-selling strategies it runs validation/stress_scenarios.py across each dated shock window, prints the stress summary at the top of the report, and ValidationReport.deployable additionally requires the stress gate to pass. ValidationReport carries is_options_selling + stress_test_results and exposes stress_gate_passed.)

validation/stress_scenarios.py (Tail-Scenario Stress Testing: replays negatively-skewed options-selling strategies across four dated shock windows -- OCT_2008 Lehman/VIX>80, FEB_2018 Volmageddon/XIV blowup, MAR_2020 COVID crash+rebound, AUG_2024 yen carry unwind -- each a StressScenario(start, end, expected_max_dd_for_short_vol, description). Caller supplies returns_fn(start,end)->daily returns Series; run_stress_tests() -> {name: StressResult}. compute_max_drawdown() returns NaN on empty data (never fabricated 0.0); account_survived() flags blow-ups (any daily return <= -100% or equity hitting 0). passes_stress_gate() True iff every canonical window is present AND each survived with max_drawdown < MAX_STRESS_DRAWDOWN=0.50; fails closed on missing/errored windows. format_stress_summary() renders the top-of-report block.)

signals/base.py, registry.py, aggregator.py, timeseries_momentum.py, cross_sectional_momentum.py, rsi2_mean_reversion.py, multifactor.py, regime_multiplier.py (Pluggable Signals Package: decouples StrategyEngine scoring into individual, weighted SignalModule classes managed by a registry. SignalContext carries an optional xsec_percentile_ranks dict and an optional multifactor_scores dict. SignalModule exposes a default no-op pre_compute(universe_df, context) hook, and a default always-True is_active_in_regime(macro) hook that regime-fragile modules override to opt out entirely for a cycle. SignalRegistry.run_pre_compute() calls all modules' pre_compute hooks once per cycle before the per-ticker loop; SignalAggregator.aggregate() checks is_active_in_regime() before adding a module's weighted contribution. rsi2_mean_reversion.py is a Connors-style long-only RSI(2) mean-reversion module — its score is in [0.0, 1.0] (long-only, unlike every other module's [-1.0, 1.0]), with trend filter Close>SMA_200, entry RSI_2<10, an already-reverted guard Close>SMA_5, and is_active_in_regime() returning False during RECESSION/CREDIT EVENT/VIX>30. multifactor.py is a Fama-French-style Value/Quality/LowVol/Size signal (Hou-Xue-Zhang 2020 priors; momentum is the separate cross_sectional_momentum module) — pre_compute() reads raw factor inputs from dashboard_df (book_to_market, earnings_yield, quality_factor_score, low_vol_score, log_market_cap, Market Cap), excludes tickers below settings.MULTIFACTOR_MICROCAP_THRESHOLD ($300M default) from the cross-sectional z-scoring population entirely, z-scores+winsorizes each input at +/-3, averages into Value_Z/Quality_Z/LowVol_Z/Size_Z (Size_Z negated so smaller=positive) and a Multifactor_Composite, storing per-ticker results in context.multifactor_scores; compute() maps the composite to [-1,+1] via tanh(z/2), returning a neutral 0.0 for microcap-excluded/data-unavailable tickers rather than a fabricated exposure. Default weight 15.0 in settings.SIGNAL_WEIGHTS -- deliberately rescaled from the "0.15" figure in the original task spec to match this codebase's points-scale weight convention (other modules range 10-45; 0.15 would be numerically inert). regime_multiplier.py's RegimeMultiplierSignal deliberately carries NO directional alpha -- compute() always returns score=0.0 regardless of input, contributing nothing to SignalAggregator's weighted-sum final_score even with a nonzero weight (settings.SIGNAL_WEIGHTS["regime_multiplier"] is explicitly 0.0, structurally enforcing this). It carries context.macro.hmm_risk_on_probability (the regime/hmm_regime.py second opinion) through its confidence field instead, defaulting to 1.0 (neutral) when the HMM didn't run. StrategyEngine.evaluate_security() reads outputs['regime_multiplier'].confidence from aggregator.aggregate()'s returned outputs dict and multiplies the final Kelly Target by it, then re-clamps to settings.MAX_POSITION_WEIGHT -- the only signal module wired as a position-sizing scalar rather than a score input.)

reports/cpcv_report.html.j2 (Report Template: HTML template with Plotly charts for DSR, PBO, and OOS Sharpe distributions)

reports/validation_report_template.html.j2 (Report Template: Jinja2 validation report template)

transactions_store.py (Transaction Store: SQLite-backed database using SQLAlchemy for active/closed trades tracking)

schema_registry.py (Gatekeeper: Pandera validation for incoming data)

dto_models.py (Structures: Object-oriented Data Transfer Objects)

config.py (Schema Definition & SSOT config)

ai_verification_prompts.py (Auditor: 6-step static analysis and simulation sandbox for AI agents)

Preferred Patterns (Strictly Enforced):

Separation of Concerns: Fetching logic is strictly separated from calculation logic using the IDataProvider interface.

Single Source of Truth (SSOT): All column headers and internal keys must be defined in config.py before use in other modules.

Fail-Safe Iteration: Loops in the data engine and orchestrator must use try/except blocks so that one bad ticker does not crash the entire portfolio pipeline.

4. AGENT DIRECTIVES (Maintenance Context)

Any AI Agent, Developer, or IDE interacting with this codebase MUST adhere to the following rules:

DATA TRANSFER OBJECTS (dto_models.py): All market data, fundamental sheets, and macroeconomic metrics MUST be mapped directly to MarketBarDTO, FundamentalDataDTO, or MacroEconomicDTO. Raw dictionary lookups or positional list indices are strictly forbidden in calculation layers.

SYSTEM DECOUPLING (data_engine.py): Decoupling is enforced via the IDataProvider abstract interface. Any modification to data acquisition or parsing patterns must implement IDataProvider.

VECTORIZATION (processing_engine.py): Iterative for loops or .iterrows() calculations over price series are strictly banned. All technical indicators, moving averages, and momentum parameters MUST be computed as vector expressions over whole Pandas/NumPy series.

SYSTEM INTEGRITY: Any code alteration MUST be accompanied by a corresponding validation suite addition in pytest. Algorithmic drift of indicators is bounded strictly below 0.00001.

FORECASTING INTEGRITY (forecasting_engine.py): MinMaxScaler fitting and supervised sequence construction in forecasting models must run strictly on training partitions (excluding the reserved inference tail) to prevent lookahead target/feature leakage.

DATABASE SCHEMA INTEGRITY (database_setup.py): The SQLite schema columns in the DailySignals table must be dynamically generated from config.COLUMN_SCHEMA. Avoid hardcoded SQL schemas to prevent column synchronization issues.

CALIBRATED STRATEGY CORRIDORS (strategy_engine.py): Sizing recommendations and advice matrices must utilize calibrated ATR volatility corridors across all signals (Buy Zone, Hold Range, and Trim/Stop levels) to avoid algorithmic pessimism. Option overlay signals must use the lookahead-free strong uptrend filter (ROC_12M > 0 and price > SMA_200) falling back to legacy trend strength when not provided.

POSITION SIZING SINGLE SOURCE OF TRUTH (sizing/, strategy_engine.py): "Kelly Target" MUST be computed by StrategyEngine._calculate_kelly_sizing(realized_vol), which calls sizing.kelly.estimate_win_rate_and_payoff() against transactions_store.TransactionsStore.closed_trades_df() and sizes via sizing.kelly.fractional_kelly() (settings.KELLY_FRACTION=0.5, settings.KELLY_CAP=0.20) when >=30 closed trades exist. With insufficient history it MUST fall back to sizing.vol_target.volatility_target_weight(realized_vol, settings.VOL_TARGET=0.10, settings.MAX_LEVERAGE=2.0) with NO Kelly multiplier, logged explicitly. Either path is then clamped to settings.MAX_POSITION_WEIGHT=1.0 (the middle ground between the old 25% ceiling and an uncapped 2.0x) -- this clamp lives in StrategyEngine._calculate_kelly_sizing itself, not in sizing.kelly or sizing.vol_target, so any other direct caller of those modules must apply its own ceiling. Do not reintroduce a score/sortino/edge_ratio-derived win-probability formula at any call site -- main_orchestrator.py reads strategy_output['Kelly Target'] directly rather than recomputing it. StrategyEngine accepts an optional injected transactions_store for testing.

LIVE TRADE HISTORY NOTE (quant_platform.db): The trades table was seeded with 169 real closed trades reconstructed via FIFO lot-matching from a Robinhood account's filled equity order history, so StrategyEngine() with no injected transactions_store now exercises the real Kelly path by default, not the empty-history fallback. Any new test asserting on Kelly Target MUST inject TransactionsStore(db_url="sqlite:///:memory:") explicitly rather than assuming the production DB is empty (tests/test_quantitative_models.py::test_garch_and_edge_scoring had this exact latent bug, fixed by injecting an empty store).

MULTIFACTOR FUNDAMENTAL INPUTS (processing_engine.py): calculate_fundamental_metrics() now accepts an optional realized_vol_60d_map: dict[str, float] parameter (sourced from calculate_technical_metrics()'s Realized_Vol_60D, itself exposed from calculate_momentum_metrics()'s already-lookahead-free internal computation -- .shift(1) before the rolling window) and computes five raw multifactor inputs per ticker: book_to_market (1/P/B), earnings_yield (1/P/E), quality_factor_score (ROE + operating margin, falling back to -debt_to_equity when yfinance omits ROE/margin), low_vol_score (negative 60-day realized vol), log_market_cap. All five are NaN (never fabricated) when the underlying yfinance field is unavailable. main_orchestrator.py and main.py both build realized_vol_60d_map from tech_metrics before calling calculate_fundamental_metrics().

MULTIFACTOR SCHEMA AND ORCHESTRATOR WIRING: config.COLUMN_SCHEMA now includes Value_Z, Quality_Z, LowVol_Z, Size_Z, Multifactor_Composite (all "format": "number"). Unlike XSec momentum (whose pre_compute INPUTS are written to dashboard_df before run_pre_compute, with no separate outputs write-back needed), MultifactorSignal's pre_compute OUTPUTS (the five Z-score columns) must be explicitly written back into dashboard_df from shared_context.multifactor_scores AFTER global_registry.run_pre_compute() runs -- main_orchestrator.py does this in the loop immediately following the pre_compute call. Any orchestrator refactor that reorders or removes that write-back will silently leave these five columns as NaN in the final output even though pre_compute computed them correctly.

SIMULATION BEFORE DEPLOYMENT (simulation_engine.py): Any new trading rule or strategy must be optimized via vectorbt and run through backtrader with a standard 0.1% commission and 0.05% slippage model to prove viability. Every backtest must display the survivorship bias warning and statistics using universe_engine.py. All simulations must utilize the realistic `TieredCostModel` for commissions, regulatory fees (SEC/TAF), bid-ask spreads, and order-type slippage.

CPCV OVERFITTING AUDIT (validation/metrics.py): Any strategy deployment must be validated against the Combinatorial Purged Cross-Validation (CPCV) framework. The deployment is gated and rejected if the Probability of Backtest Overfitting (PBO) is >= 0.5 or the Deflated Sharpe Ratio (DSR) is <= 0.95.

STRATEGY VALIDATION HARNESS (validation/harness.py): The strategy validation harness evaluates walk-forward (60/40, 70/30, 80/20) and CPCV stability, applying transaction cost drag linearly scaled by average daily turnover. Deployment is strictly gated on: PBO < 0.5, DSR > 0.95, net Net-of-cost Sharpe > 0.5, and Max Drawdown < 30%.

TAIL-SCENARIO STRESS GATE (validation/stress_scenarios.py): Options-selling strategies (negatively-skewed payoff) carry an ADDITIONAL deployability gate beyond the four above: max drawdown < 50% AND account survival (no blow-up) in EVERY dated shock window (OCT_2008, FEB_2018, MAR_2020, AUG_2024). Construct the harness with is_options_selling=True and a stress_returns_fn(start,end)->daily returns; the gate fails closed if an options-selling strategy is never stress-tested. The stress summary is printed at the top of every options-selling validation report. The full-sample MaxDD gate is insufficient for short-vol books because a multi-year average washes out a two-week tail catastrophe.

PLUGGABLE SIGNAL MODULES (signals/): Scoring phases 1-4D must be implemented as modular classes inheriting from `SignalModule`. Weights must reside in settings.SIGNAL_WEIGHTS and be aggregated via `SignalAggregator` to ensure decoupling. Cross-sectional modules (e.g. CrossSectionalMomentumSignal) MUST use the two-phase pre_compute/compute hook pattern: pre_compute runs once per cycle on the full universe DataFrame via global_registry.run_pre_compute(); compute reads pre-computed ranks from context.xsec_percentile_ranks. The 12-1m return (skip_days=22, lookback_days=252) MUST be computed by compute_xsec_momentum_ranks() in main_orchestrator.py without iterrows and without current-month data.

CROSS-SECTIONAL MOMENTUM SCHEMA: config.COLUMN_SCHEMA now includes XSec_12_1M (percent) and XSec_Momentum_Rank (percent). Any Pandera-validated DataFrame must carry these columns or validation will fail. The orchestrator writes both columns before calling run_pre_compute().

REGIME-FRAGILE SIGNAL SUPPRESSION (signals/base.py, signals/aggregator.py): SignalModule exposes is_active_in_regime(macro: MacroEconomicDTO) -> bool, default True. SignalAggregator.aggregate() MUST check this before adding a module's weighted score/explanation contribution — compute() still runs and its raw SignalOutput remains in the returned outputs dict for introspection, but a False module contributes nothing to final_score or score_log. New regime-fragile signal modules (mean reversion, carry strategies, etc.) MUST override this hook rather than self-zeroing inside compute(), so suppression is enforced centrally and cannot be silently skipped per-module. config.COLUMN_SCHEMA now also includes RSI_2 and SMA_5 (RSI(2) and SMA(5), both computed the same causal/vectorized way as the existing RSI(14)/SMA(50)/SMA(200) columns in processing_engine.calculate_technical_metrics()).

GRAVITY AI VERIFICATION (ai_verification_prompts.py, "Gravity AI Review Suite.py"): You MUST utilize the Gravity AI Review Suite's 21-step audit rules (including Step 13 signal registry audit, Step 14 cross-sectional momentum pre_compute audit, Step 15 RSI(2) regime-gate audit, Step 16 Kelly/vol-target sizing audit, Step 17 multifactor audit, Step 18 HMM regime audit, Step 19 IVR/VRP audit, Step 20 pairs trading audit, and Step 21 tail-scenario stress gate audit) prior to any strategy deployment. The AI Verification Suite acts as a static analysis and simulation sandbox to ensure mathematical correctness, edge case handling, and architectural integrity before strategies are permitted to run. Every indicator and forecaster must also be verified using the perturbation lookahead checks in tests/.

IMPLIED VOLATILITY & VRP (volatility/): ATM implied volatility interpolation must target a calendar-30-day horizon using front and second month chains. IVR calculations must be causal and check strictly `< D` where D is the target date. VRP gates for premium selling must restrict trading when `true_ivr > 50` but VRP/VIX/Credit Event rules fail, defaulting directly to `Cash/Wait`.

PAIRS TRADING (pairs/, signals/): Pairs trading must utilize Engle-Granger ADF cointegration checks at p < 0.05 and filter half-life of mean reversion to [5, 60] days. Kalman Filter dynamic hedge ratio estimation must execute lookahead-free forward filtering (kf.filter or tracker updates). Spread z-scores are scaled by a window of 2 * HL. Exit rules must include cointegration breaks (rolling ADF p > 0.10) and standard z-score thresholds.
ALPACA PAPER BROKER INTEGRATION (execution/broker_base.py, execution/alpaca_broker.py, execution/order_manager.py): A full async broker execution layer has been added as Step 6 of the main_orchestrator pipeline. BrokerBase ABC defines the interface: submit_order, cancel_order, get_open_positions, get_account, get_orders, stream_trade_updates (async generator). AlpacaBroker implements BrokerBase using alpaca-py; credentials come from settings.ALPACA_API_KEY / settings.ALPACA_SECRET_KEY / settings.ALPACA_PAPER (default True = paper). Multi-leg options spreads/condors are supported via OrderIntent.legs (list of {"symbol", "ratio_qty", "side"} dicts → OptionLegRequest). OrderManager is the ONLY permitted path for order submission from orchestrators and strategies — never call AlpacaBroker.submit_order() directly.

ORDER IDEMPOTENCY (execution/order_manager.py): make_client_order_id(strategy_id, symbol, side, qty, timestamp) produces a deterministic 48-char SHA-256 hex ID bucketed to 60-second windows. OrderManager._submitted is a process-lifetime set of already-submitted IDs; a duplicate call within the bucket returns immediately with status=ACCEPTED without a second network round-trip. Idempotency is enforced at the MANAGER level (not the broker level) — the dry-run guard is also at manager level so MockBroker tests see it correctly.

DRY-RUN MODE: Setting settings.DRY_RUN=true OR passing --dry-run CLI flag to main_orchestrator.py enables dry-run mode. In this mode, OrderManager._submit_with_retry intercepts before broker.submit_order() and logs the intent without any network call. Returns OrderResult(status=ACCEPTED, broker_order_id=None). Both settings and CLI flag are ORed; either source can enable dry-run.

STATE RECONCILIATION (execution/order_manager.py): reconcile_state(broker, transactions_store) compares TransactionsStore.open_trades_df() (grouped by symbol) against broker.get_open_positions(). Any qty mismatch > 1e-4 creates a DriftItem logged at CRITICAL. If ALERT_WEBHOOK_URL is configured, a JSON POST is sent (Slack/Discord incoming webhook). reconcile_state NEVER raises — broker errors are caught and stored in ReconciliationReport.error. Runs before each order-submission cycle in _execute_broker_orders.

GLOBAL KILL SWITCH (execution/kill_switch.py): GlobalKillSwitch is a stateless file-based sentinel (OUTPUT_DIR/KILL_SWITCH). is_active() checks file presence. activate(reason) writes atomically via write-then-rename to avoid partial-write races. deactivate() removes the file. KillSwitchActiveError(RuntimeError) is raised by OrderManager.submit_order_with_idempotency BEFORE any dedup check so the sentinel cannot be bypassed. CLI: python -m execution.kill_switch --activate/--deactivate/--status. FLATTEN_ON_KILL=true logs a CRITICAL reminder; automatic flattening is a future extension.

PRE-TRADE RISK GATE (execution/risk_gate.py): PreTradeRiskGate wraps 10 synchronous checks; run_all(intent, context) short-circuits at first failure. Check order: (1) max_position_size, (2) portfolio_heat, (3) max_correlation (|r| threshold — blocks both over-correlated longs AND negatively-correlated shorts), (4) daily_loss_limit, (5) macro_kill_switch, (6) hmm_regime (HMM risk-off > HMM_RISK_OFF_BLOCK_THRESHOLD blocks new longs), (7) stress_scenario (VIX > 30 blocks premium-sell orders), (8) market_hours (NYSE RTH 09:30–16:00 ET via zoneinfo), (9) minimum_validation, (10) max_order_rate — ALWAYS LAST so blocked orders never consume rate-limit budget. All checks pass conservatively on missing/None context — the gate never blocks due to absent data. RiskContext dataclass holds all per-call live state; thresholds are injectable at gate construction for testing.

ORDER MANAGER KILL-SWITCH + RISK-GATE WIRING (execution/order_manager.py): submit_order_with_idempotency now accepts risk_context: Optional[RiskContext] = None and checks the kill switch FIRST (before dedup), then risk gate (after dedup, returns ERROR OrderResult if any check fails). OrderManager constructor accepts kill_switch: Optional[GlobalKillSwitch] = None (defaults to GlobalKillSwitch()) and risk_gate: Optional[PreTradeRiskGate] = None (skipped when None for backward compat).

HEARTBEAT + _main_body REFACTOR (main_orchestrator.py): main(dry_run) now spawns an asyncio background task _heartbeat(output_dir, interval=60) that logs "ORCHESTRATOR ALIVE" and writes OUTPUT_DIR/heartbeat.txt (UTC ISO timestamp) every 60s. The try/finally in main() always cancels the heartbeat task regardless of pipeline success/failure. The pipeline body is in _main_body(effective_dry_run) so the try/finally is clean. _execute_broker_orders now takes macro_dto=None and builds a RiskContext from live broker state before each order loop; reconstructs a _broker_macro_dto from macro_raw since run_pipeline() doesn't return the internal macro_dto.

NEW SETTINGS (settings.py): DRY_RUN: bool (default False) — set via env/CLI. ALERT_WEBHOOK_URL: Optional[str] (default None) — Slack/Discord incoming webhook for drift alerts. MAX_CORRELATION: float (0.85). DAILY_LOSS_LIMIT_PCT: float (0.02). MAX_ORDER_RATE_PER_MIN: int (10). HMM_RISK_OFF_BLOCK_THRESHOLD: float (0.80). RISK_GATE_ENFORCE_MARKET_HOURS: bool (True). FLATTEN_ON_KILL: bool (False).

NEW DEPENDENCY: alpaca-py>=0.40.0 added to requirements.txt (official free SDK; no other paid dependencies introduced).

BROKER EXECUTION IS BEST-EFFORT: _execute_broker_orders errors are caught as ERROR and never crash the signal/sizing/reporting pipeline. The pipeline's value must not be contingent on broker connectivity. AlpacaBroker is instantiated only when both ALPACA_API_KEY and ALPACA_SECRET_KEY are present; the orchestrator silently skips Step 6 otherwise. KillSwitchActiveError in the order loop aborts remaining submissions for that cycle (CRITICAL logged) but never crashes the outer try/except.

GRAVITY STEPS 24 (broker/order-manager audit): Step 24 has been added to Gravity AI Review Suite to verify: BrokerBase ABC is uninstantiatable; AlpacaBroker construction raises on missing credentials; make_client_order_id is deterministic for same inputs and differs for different inputs; OrderManager dry-run does not call broker.submit_order; reconcile_state never raises on broker error; DRY_RUN setting defaults to False; kill switch active → KillSwitchActiveError raised before broker contact; risk gate blocked → ERROR OrderResult, broker never called.

TRIPLE-BARRIER LABELING & META-LABELING (ml/triple_barrier.py, ml/meta_labeling.py — Stage 4):
Triple-barrier labeling (Lopez de Prado AFML Ch. 3) generates +1/-1/0 labels by racing three barriers: upper (profit-take = entry + pt_mult * sigma * entry), lower (stop-loss = entry - sl_mult * sigma * entry), and vertical (timeout = entry + N business days). `get_volatility(close, span=100)` uses causal EWMA (`adjust=False`) — vol at t uses ONLY returns ≤ t. `cusum_filter(close, threshold)` samples events when cumulative log-return drift crosses ±threshold (resets after each event). The CUSUM loop is intentionally sequential (not vectorizable). `apply_triple_barrier(events, close, pt_sl_multiples, vertical_barrier_days)` pre-computes the full vol series (causal), indexes at each event time, and finds first-touch barriers looking ONLY at close[t0+1:t1]. Perturbing prices after t0 must NOT change barriers — verified by perturbation tests.

MetaLabeler trains a binary LightGBM classifier to predict P(primary_signal_correct). Meta-label = 1 when primary direction matches barrier outcome (both +1 or both -1); vertical timeout counts as wrong. The `MetaLabelerRegistry` (global_meta_registry in ml/meta_labeling.py) maps signal_id → MetaLabeler. SignalAggregator queries it for each active module; if P < settings.META_LABEL_MIN_CONFIDENCE (default 0.4), meta_label_composite is forced to 0.0 (hard gate, not a gradual suppression). This zeroes the Kelly Target while leaving the directional signal score (BUY/HOLD/etc.) unchanged. Empty registry = pre-Stage-4 behavior (composite = 1.0, no-op).

QLIB-STYLE ML ARCHITECTURE (ml/ — Prompt 4.3, no qlib dependency):
Three-layer structure: ml/data/ (PIT feature store + label construction), ml/models/ (Model ABC + implementations), ml/strategies/ (StrategySpec). ALL ML models must implement ml.models.base.Model: fit(X, y, t1)/predict(X)/save(path)/load(path). Both LGBMCrossSectionalRanker and MetaLabeler satisfy this ABC. ml/registry.yaml lists production models with trained_date, cpcv_dsr, pbo, and deployable flag; parse with PyYAML (added to requirements.txt). PITFeatureStore (ml/data/store.py) caches daily cross-sectional features as Parquet files (ml/data/cache/) for expanding-window retraining. StrategySpec (ml/strategies/__init__.py) links a Model to a signal_id and flags is_meta_labeler.

DISK-PERSISTED CADENCE CACHE (cache/cache_store.py — Stage 5):
A reusable, SQLite-backed cache layer at cache/cache.db that prevents redundant network calls across all data-fetch categories. No new third-party dependencies — uses only stdlib (sqlite3, gzip, json, threading, hashlib) plus pandas.

Core types: Cadence enum (INTRADAY=5 min, DAILY=20 h, WEEKLY=7 d, MONTHLY=30 d, QUARTERLY=90 d, YEARLY=365 d). CADENCE_TTL dict is the single place to retune any TTL. CADENCE_REGISTRY maps logical names ("quotes", "daily_bars", "fundamentals", "financials", "dividends_meta", "analyst_ratings", "earnings_calendar", "company_profile", "macro_regime_inputs") to their Cadence. CacheEntry (frozen dataclass) holds value + fetched_at (tz-aware UTC) + expires_at + cadence; exposes age_seconds and is_fresh.

Cache class: SQLite WAL mode + threading.RLock (one writer at a time, unlimited concurrent readers). isolation_level=None (autocommit) with manual BEGIN IMMEDIATE / COMMIT / ROLLBACK for atomic writes. Two tables: cache_entries (JSON key-value; PRIMARY KEY (namespace, key)) and history_cache (gzip-compressed JSON blobs for OHLCV time-series; PRIMARY KEY (symbol, namespace)). Methods: get / set / invalidate / clear(namespace=None). explicit expires_at override: set() accepts an optional expires_at datetime to pin expiry to a known event (e.g., next-earnings date) rather than the cadence TTL.

Incremental history (get_history_incremental): Three-case logic: (1) cold cache → full fetch via fetch_fn(symbol); (2) warm cache within TTL → return cached DataFrame, no network call; (3) expired → delta fetch via fetch_fn(symbol, start="YYYY-MM-DD") starting the day after the last cached bar, merged and de-duplicated on DatetimeIndex. Falls back to full re-fetch via TypeError catch if fetch_fn doesn't accept start. Logs "Fetching history delta from <date> for <symbol> (namespace=<ns>)" at INFO on every delta fetch. tz-aware DatetimeIndex is normalised to tz-naive on write so yfinance (tz-aware) and FRED (tz-naive) sources concat without collision.

get_default_cache() / _inject_cache(): thread-safe module-level singleton (the production instance). _inject_cache(cache) replaces it for test isolation — preferred over monkeypatching.

@cached(namespace, cadence) decorator: transparent cache lookup + store around any fetch function. Accepts force=True kwarg (consumed before forwarding to wrapped function). Logs cache hits at INFO. Attaches ._cache_namespace and ._cache_cadence for Gravity auditing.

Serialisation: custom JSON encoder/decoder supports pd.DataFrame (orient="split"), pd.Series (orient="split"), and datetime (ISO). OHLCV DataFrames stored as gzip-compressed JSON blobs (no pyarrow). Cache keys > 128 chars are SHA-256-hashed. SECRETS MUST NEVER BE PASSED AS CACHE VALUES — callers are responsible; the cache stores no credentials.

Gravity Step 25 (cache system audit): Checks Cache API completeness, all Cadence enum values present in CADENCE_TTL with non-zero TTLs, CADENCE_REGISTRY has all required keys, @cached second call is a hit (network called once), TTL expiry triggers refresh, force=True bypasses cache, and no known secret-pattern strings appear in cache values.
