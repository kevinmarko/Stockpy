Project Context: Stock Dashboard Py (InvestYo Quant Platform)

1. Overview

Purpose: An automated, institutional-grade quantitative analysis pipeline that fetches financial data, calculates technical/fundamental indicators, performs multi-horizon forecasting, simulates trading strategies via backtesting, and updates a Google Sheets dashboard.

Target Audience: Kevin Marko Lee (Individual Investor / Quantitative Analyst).

Key Features:

Automated Data Ingestion: Uses Yahoo Finance for equities and FRED for macroeconomic data.

Advanced Processing: Calculates Graham/Gordon valuations, Risk metrics (VaR/Sortino), and Technicals (RSI/MACD/Aroon).

Sector-Specific Forecasting: Employs ARIMA, Monte Carlo, and Holt-Winters statistical models.

Simulation & Backtesting (New): Matrix-based parameter optimization via vectorbt and event-driven realistic simulation (accounting for slippage and commissions) via backtrader.

Google Sheets Integration: Reads input tickers and writes analyzed output seamlessly.

2. Tech Stack

Language: Python 3.12 (Enforced via setup.sh)

Core Libraries: pandas, numpy, yfinance, fredapi, statsmodels, pandas_ta, vectorbt, backtrader, pandera, pydantic.

Database/Storage: Google Sheets (via gspread and gspread_dataframe).

Configuration: config.py (Single Source of Truth for schema).

Authentication: Google Service Account (credentials.json).

3. Architecture & Patterns

Folder Structure: Flat, modular "Engine" architecture designed for Dependency Injection:

main.py (Orchestrator: Coordinates engines and Google Sheets I/O)

data_engine.py (Fetcher: Handles external API calls, abstract interfaces, and mock data)

processing_engine.py (Calculator: Vectorized mathematical indicators and fundamental logic)

forecasting_engine.py (Predictor: Statistical modeling and projections)

simulation_engine.py (Simulator: VectorBT optimization and Backtrader simulations)

schema_registry.py (Gatekeeper: Pandera validation for incoming data)

dto_models.py (Structures: Object-oriented Data Transfer Objects)

config.py (Schema Definition)

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

SIMULATION BEFORE DEPLOYMENT (simulation_engine.py): Any new trading rule or strategy must be optimized via vectorbt and run through backtrader with a standard 0.1% commission and 0.05% slippage model to prove viability.