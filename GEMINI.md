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

data_engine.py (Fetcher: Ingests external API data, implements IDataProvider abstract interface)

processing_engine.py (Calculator: Vectorized mathematical indicators and fundamental calculations)

research_engine.py (Researcher: Computes realized slippage and CoVaR tail risk metrics)

strategy_engine.py (Decision Maker: Institutional signal generator, calibrated momentum/trend weights, and tactical risk zone sizing. Utilizes an Aroon Oscillator Chop-Filter to suppress false-positive MACD signals)

forecasting_engine.py (Predictor: Statistical modeling and multi-horizon forecasts. Strictly enforces deterministic trend parameters (trend='t' in ARIMA, trend='add' in Holt-Winters) and structural drift (μ - 0.5 * σ^2) in Monte Carlo to prevent naive flatline projections)

simulation_engine.py (Simulator: VectorBT optimization and Backtrader simulations)

database_setup.py (Schema Builder: SQLite database and dynamic mapping creator)

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

DATABASE SCHEMA INTEGRITY (database_setup.py): The SQLite schema columns in the DailySignals table must be dynamically generated from config.COLUMN_SCHEMA. Avoid hardcoded SQL schemas to prevent column synchronization issues.

CALIBRATED STRATEGY CORRIDORS (strategy_engine.py): Sizing recommendations and advice matrices must utilize calibrated ATR volatility corridors across all signals (Buy Zone, Hold Range, and Trim/Stop levels) to avoid algorithmic pessimism.

SIMULATION BEFORE DEPLOYMENT (simulation_engine.py): Any new trading rule or strategy must be optimized via vectorbt and run through backtrader with a standard 0.1% commission and 0.05% slippage model to prove viability.

GRAVITY AI VERIFICATION (ai_verification_prompts.py): You MUST utilize the ai_verification_prompts.py script and its 6-step Gravity AI Auditor rules prior to any strategy deployment. The AI Verification Suite acts as a static analysis and simulation sandbox to ensure mathematical correctness, edge case handling, and architectural integrity before strategies are permitted to run.