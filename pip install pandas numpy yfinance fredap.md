```bash
# IMPORTANT: The platform explicitly rejects pgsqlite in favor of Python's native sqlite3 module
# alongside SQLAlchemy and psycopg2-binary. 'QuantFAA' and 'arch' are strictly required.
pip install pandas numpy yfinance fredapi gspread gspread-dataframe pytest scikit-learn statsmodels scipy SQLAlchemy psycopg2-binary openai anthropic arch prophet google-cloud-language QuantFAA pandas-datareader pandas-ta
```

#### 2. Verify Your Decoupled Architecture Locally
Run the test suite using `python3 -m pytest` to verify the mathematical convergence of your vectorized indicators:
```bash
python3 -m pytest tests/test_quantitative_models.py
```
This runs the isolated, high-precision mathematical tests without touching external network connections or API end-points.

---

### How to Configure Your Google Anti-Gravity IDE AI Agent
When configuring your AI assistant inside the **Google Anti-Gravity IDE** to manage, maintain, and iterate on this platform, provide it with the following strict context and behavior parameters.

Copy and paste this explicit **System Prompt Configuration** directly into your agent's control panel:

```markdown
# AGENT DIRECTIVE: INVESTYO QUANT PLATFORM MAINTENANCE CONTEXT

You are operating on the "InvestYo Quant Platform", a highly modular, decoupled quantitative trading architecture.

## ARCHITECTURAL CONCEPTS
1. DATA TRANSFER OBJECTS (dto_models.py): All market data, fundamental sheets, and macroeconomic metrics MUST be mapped directly to 'MarketBarDTO', 'FundamentalDataDTO', or 'MacroEconomicDTO'. Raw dictionary lookups or positional list indices are strictly forbidden.
2. SYSTEM DECOUPLING (data_engine.py): Decoupling is enforced via the 'IDataProvider' abstract interface. Any modification to data acquisition or parsing patterns must implement 'IDataProvider'.
3. VECTORIZATION (processing_engine.py): Iterative 'for' loops or '.iterrows()' calculations over price series are strictly banned. All technical indicators, moving averages, and momentum parameters MUST be computed as vector expressions over whole Pandas series.
4. SYSTEM INTEGRITY (tests/test_quantitative_models.py): Any code alteration MUST be accompanied by a corresponding validation suite addition. Algorithmic drift of indicators is bounded strictly below 0.00001.
5. GRAVITY AI VERIFICATION (AI_Verification_Prompts.py): You MUST utilize the 6-step Gravity AI Auditor rules prior to any strategy deployment. The 6 steps must be executed via the `AI_Verification_Prompts.py` script to ensure that strategies are rigorously validated for edge cases, mathematical correctness, architectural integrity, and robust scaling.

When adding features, optimize first for mathematical accuracy, then computational scale, and strictly respect this decoupled design.
```

---

### How to Leverage NotebookLM as a Strategic Analytical Partner
You can connect **NotebookLM** directly to your newly modernized system as an elite quantitative research consultant and system architect. Since NotebookLM functions as a context-driven knowledge engine, follow this step-by-step process to build a local analytical feedback loop:

#### Step 1: Export Your System Corpus
Compile your entire modernized modular architecture into a clean, comprehensive Markdown document:
```bash
cat config.py dto_models.py data_engine.py processing_engine.py research_engine.py strategy_engine.py forecasting_engine.py diagnostics_and_visuals.py database_setup.py AI_Verification_Prompts.py "Gravity AI Review Suite.py"evaluation_engine.py technical_options_engine.py macro_engine.py simulation_engine.py reporting_engine.py main.py main_orchestrator.py data_ingestion.py tests/test_quantitative_models.py > quant_platform_corpus.md
```

#### Step 2: Ingest the Corpus into NotebookLM
1. Open [NotebookLM](https://notebooklm.google.com/).
2. Create a new notebook and name it **"InvestYo Quant Architecture Core"**.
3. Upload `quant_platform_corpus.md` as your primary source document. You can also upload your spreadsheet blueprint and transaction logs to complete the context.

#### Step 3: Run Advanced Strategic Prompts
Once ingested, use NotebookLM to gain deep tactical insights and perform portfolio analysis. Here are three high-impact prompts to run:

* **Prompt 1 (System Optimization Analysis)**:
  > *"Analyze our processing engine's vectorized calculation formulas for RSI and MACD. How do these mathematically interact with potential pricing anomalies (like stock splits or massive overnight gaps)? What edge cases should we add to our pytest suite to catch calculation failures?"*
  
* **Prompt 2 (Portfolio Hedging Generation)**:
  > *"Based on our fundamental DTO quality parameters and the top-down Macro Kill Switch, write an institutional-grade investment thesis for our high-yield positions (e.g. mREITs like AGNC vs. stable dividend growers like SCHD) in the event that credit spreads spike past 5.5%."*

* **Prompt 3 (Interactive Developer Audio Briefing)**:
  > *Click on 'Generate Audio Overview' in NotebookLM. This will produce a highly engaging, professional podcast between two quantitative developers explaining the transition from our legacy procedural script framework to our modernized, type-safe, dependency-injected quantitative engine.*

Using NotebookLM in this fashion bridges raw technical deployment with advanced strategic intelligence, turning your local codebase into an interactive knowledge center.

Are you ready to move forward with the remaining steps of the roadmap (Jinja2 reporting and Plotly visualizations), or would you like to explore any of these structural integrations further?