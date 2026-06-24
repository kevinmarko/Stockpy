# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

InvestYo Quant Platform ("Stock Dashboard Py") — an automated quantitative analysis pipeline: fetches market/macro data, computes technical & fundamental indicators, runs multi-horizon forecasts, backtests strategies, persists signals to SQLite, and publishes results to Google Sheets / an HTML report.

## Commands

```bash
./setup.sh                       # creates .venv (Python 3.12), installs requirements.txt
source .venv/bin/activate
python3 main.py                  # legacy sync orchestrator (Data -> Processing -> Forecasting -> Strategy -> Google Sheets)
python3 main_orchestrator.py     # newer async master orchestrator (data acquisition, schema validation, HTML report compilation)
pytest                           # run test suite
pytest tests/test_quantitative_models.py            # run a single test file
pytest tests/test_quantitative_models.py::test_graham_number_imaginary_bounds  # run a single test
python3 database_setup.py        # (re)build the SQLite schema in quant_platform.db from config.COLUMN_SCHEMA
```

`main.py` and `main_orchestrator.py` both auto-re-exec themselves under `.venv`'s interpreter if not already running inside it — no need to manually activate the venv before running them.

## Architecture

Flat, modular "Engine" architecture using dependency injection — no package directories, every engine is a top-level module imported directly by the orchestrators.

- **config.py** — Single Source of Truth (SSOT). `COLUMN_SCHEMA` defines every column's Google Sheets header, internal dict key, and display format, plus Pandera schemas for validation. Any new field must be added here first before use elsewhere.
- **dto_models.py** — `MarketBarDTO`, `FundamentalDataDTO`, `MacroEconomicDTO`. All market/fundamental/macro data flowing through calculation layers must be coerced into these DTOs — raw dict lookups or positional indexing in calc code is not allowed. DTO parsers also handle messy upstream strings (currency symbols, `%`, `"N/A"`, padding).
- **data_engine.py** — `DataEngine` (live Yahoo Finance/FRED via `fredapi`) and `MockDataEngine` (test fixture), both implementing an `IDataProvider` interface. Fetching is strictly decoupled from calculation.
- **processing_engine.py** — vectorized fundamental valuations (Graham Number, Gordon Fair Value) and technical indicators (RSI, MACD, Aroon, ATR, SMA). Iterative loops/`.iterrows()` over price series are disallowed; everything must be expressed as pandas/numpy vector operations.
- **macro_engine.py** — macroeconomic regime detection/gating.
- **technical_options_engine.py** — options-specific technical metrics (IV rank, GARCH vol, options IV edge).
- **forecasting_engine.py** — ARIMA, Monte Carlo, Holt-Winters forecasts. Deterministic trend params are enforced (`trend='t'` for ARIMA, `trend='add'` for Holt-Winters) and Monte Carlo uses structural drift (μ − 0.5σ²) — these guard against naive flatline projections, don't drop them when refactoring.
- **strategy_engine.py** — signal generation with calibrated momentum/trend weights and ATR-based volatility corridors (buy zone / hold range / trim-stop). Uses an Aroon Oscillator chop-filter to suppress false-positive MACD signals.
- **simulation_engine.py** — `vectorbt` parameter-sweep optimization plus `backtrader` event-driven backtests (0.1% commission / 0.05% slippage standard). New strategies should be proven here before being wired into `strategy_engine.py`.
- **research_engine.py** — realized slippage, CoVaR tail-dependency proxy, Brinson-Fachler attribution, MFE/MAE, portfolio heat.
- **evaluation_engine.py** — strategy performance evaluation.
- **database_setup.py** — builds the SQLite schema (`DailySignals`, `ExecutionLogs`) dynamically from `config.COLUMN_SCHEMA`; never hardcode SQL column lists.
- **reporting_engine.py** / **diagnostics_and_visuals.py** / **daily_report_template.html** — HTML report generation/rendering.
- **AI_Verification_Prompts.py** ("Gravity AI Auditor") — 6-step static-analysis + simulation sandbox (via OpenAI/Anthropic) that strategies must pass before deployment.
- **main.py** — legacy synchronous orchestrator: Data → Processing → Forecasting → Strategy → Google Sheets (via `gspread`/`gspread_dataframe`).
- **main_orchestrator.py** — newer async master orchestrator: routes data through the math engines, validates schemas, compiles the HTML report.

## Conventions enforced in this codebase

- All data crossing into calculation code goes through the DTOs in `dto_models.py`, not raw dicts/lists.
- Data fetching always goes through `IDataProvider` implementations in `data_engine.py`.
- Technical/fundamental math is vectorized — no per-row Python loops.
- Loops over tickers (in `data_engine.py`, orchestrators) wrap each ticker in try/except so one bad symbol doesn't abort the whole run.
- New/changed indicators need a corresponding test in `tests/`; numeric drift on existing indicators must stay below 1e-5.
- New trading rules must be optimized in `vectorbt` and validated in `backtrader` before being added to `strategy_engine.py`.
- Google Sheets auth uses a service account via `credentials.json`.
