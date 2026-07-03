import os
import json
import logging
import re
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

# --- CONFIGURATION & LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("GravityAIAuditor")

# --- DATA STRUCTURES ---
@dataclass
class ValidationCriterion:
    id: str
    description: str
    required_keywords: List[str]
    critical: bool

@dataclass
class StepPromptTemplate:
    step_number: int
    step_title: str
    prompt_text: str
    criteria: List[ValidationCriterion]

@dataclass
class AIReviewReport:
    step_number: int
    status: str  # "PASSED" or "FAILED"
    score: float
    findings: List[str]
    missing_elements: List[str]
    timestamp: str

# --- SYSTEM PROMPT DEFINITION ---
SYSTEM_PROMPT = """
You are 'Gravity', an Expert Quantitative Python Auditor and Algorithmic Trading Architect. Your mandate is to perform rigorous static analysis and logical verification of financial codebases based on institutional-grade quantitative finance standards.

MASTER RULES FOR YOUR REVIEW:
1. VECTORIZATION IS MANDATORY: You must enforce strict adherence to vectorized operations (Pandas/NumPy). Iteration via loops is an automatic failure.
2. NO LOOKAHEAD BIAS: You must check for Lookahead Bias in all time-series and machine learning models.
3. MATHEMATICAL INTEGRITY: You must verify that complex quantitative formulas (e.g., Black-Scholes, RSI, MAE, MFE) are implemented correctly with algorithmic drift bounded strictly below 0.00001.
4. HARDCODED RISK MANAGEMENT: You must ensure institutional risk management (Position Sizing via ATR Kelly targets, Portfolio Heat limits, Slippage limits) is hardcoded into execution logic.

Output your evaluation strictly in valid JSON format matching the requested schema. No conversational filler. No markdown outside of the JSON block.
"""

# --- STEP 1 TO 6 AI PROMPT TEMPLATES ---

STEP_1_PROMPT = StepPromptTemplate(
    step_number=1,
    step_title="Establish the Vectorized Python Backend and Relational Database",
    prompt_text="""
    Analyze the provided source code for Step 1. Verify the following:
    1. VECTORIZATION: Ensure all DataFrame operations utilize vectorized Pandas/NumPy functions. Fail the code if iterrows(), itertuples(), or standard for loops are used for data mutation.
    2. DATABASE ARCHITECTURE: Verify the presence of a relational database implementation (SQLite/PostgreSQL) using SQLAlchemy or direct adapters (like psycopg2).
    3. SCHEMA RIGIDITY: Check for explicitly defined schemas, primary keys, and foreign key relationships for storing ticks, daily bars, and fundamental metrics.
    4. CONFIGURATION: Confirm the existence of a Configuration-Driven Architecture (e.g., config.py) decoupling the DB schema from the execution layer.

    Respond in JSON: {"status": "PASSED/FAILED", "score": 0-100, "findings": [], "missing_elements": []}
    """,
    criteria=[
        ValidationCriterion("1.1", "Vectorized Operations", ["pandas", "numpy", "apply", "np.where"], True),
        ValidationCriterion("1.2", "Relational DB Initialization", ["sqlite3", "psycopg2", "SQLAlchemy", "CREATE TABLE"], True)
    ]
)

STEP_2_PROMPT = StepPromptTemplate(
    step_number=2,
    step_title="Implement Advanced Technical Indicators & Strategy Engine",
    prompt_text="""
    Analyze the provided source code for Step 2. Verify the mathematical implementation of the following indicators and strategy logic:
    1. MACD & RSI: Check that Moving Average Convergence Divergence (12, 26, 9) and Relative Strength Index (14) are calculated using Exponential Moving Averages (EMA).
    2. VOLATILITY BANDS: Verify the presence of Average True Range (ATR) and Chandelier Exits or Bollinger Bands.
    3. STRATEGY CHOP-FILTERS: Confirm the Strategy Engine utilizes an Aroon Oscillator Chop-Filter to suppress false MACD whipsaws, GARCH Volatility to penalize tail-risk, and the Edge Ratio (expectancy) as a gate before allowing maximum Kelly sizing.
    4. LOOKAHEAD BIAS: Ensure that indicators only calculate using historical closing prices up to time t, never t+1 (e.g., correct use of .shift(1) where necessary).

    Respond in JSON: {"status": "PASSED/FAILED", "score": 0-100, "findings": [], "missing_elements": []}
    """,
    criteria=[
        ValidationCriterion("2.1", "MACD & RSI Logic", ["ewm", "span", "macd", "rsi"], True),
        ValidationCriterion("2.2", "Strategy Chop-Filters", ["Aroon_Oscillator", "GARCH_Vol", "Edge_Ratio", "Kelly_Size"], True)
    ]
)

STEP_3_PROMPT = StepPromptTemplate(
    step_number=3,
    step_title="Volatility Modeling & Automated Options Strategy Matrix",
    prompt_text="""
    Analyze the provided source code for Step 3. Verify the quantitative derivatives modeling:
    1. BLACK-SCHOLES: Validate the Black-Scholes PDE implementation. It must calculate theoretical pricing and output the Greeks (Delta, Gamma, Theta, Vega).
    2. IVR CALCULATION: Ensure Implied Volatility Rank (IVR) is calculated correctly over a 52-week rolling window: (Current IV - 52W Low IV) / (52W High IV - 52W Low IV) * 100.
    3. STRATEGY MATRIX: Check the automated options routing logic.
       * If IVR > 70: Must deploy Credit Spreads or Iron Condors.
       * If IVR < 30: Must deploy Debit Spreads or Calendar Spreads.
    4. DELTA HEDGING: Verify logic for calculating portfolio beta/delta and sizing protective puts.

    Respond in JSON: {"status": "PASSED/FAILED", "score": 0-100, "findings": [], "missing_elements": []}
    """,
    criteria=[
        ValidationCriterion("3.1", "Black-Scholes Math", ["norm.cdf", "np.exp", "sigma", "sqrt"], True),
        ValidationCriterion("3.2", "IVR & Matrix Logic", ["ivr", "Credit Spread", "Debit Spread"], True)
    ]
)

STEP_4_PROMPT = StepPromptTemplate(
    step_number=4,
    step_title="Machine Learning & Time-Series Forecasting",
    prompt_text="""
    Analyze the provided source code for Step 4. Verify the predictive modeling framework:
    1. PREPROCESSING: Verify the data is scaled (e.g., MinMaxScaler or StandardScaler) BEFORE being fed into models.
    2. TIME-SERIES FORMATTING: Ensure 2D data is successfully reshaped into 3D tensors [samples, time_steps, features] for LSTM models.
    3. STRUCTURAL DRIFT: Verify that ARIMA/Holt-Winters models explicitly enforce trend parameters (e.g., trend='t' or 'add') and Monte Carlo simulations mathematically calculate and inject structural drift (mu - 0.5 * var) to prevent horizontal 0-mean averaging.
    4. STATIONARITY: Verify the presence of an Augmented Dickey-Fuller (ADF) test ensuring time-series data is stationary before prediction.

    Respond in JSON: {"status": "PASSED/FAILED", "score": 0-100, "findings": [], "missing_elements": []}
    """,
    criteria=[
        ValidationCriterion("4.1", "Data Scaling & Reshaping", ["MinMaxScaler", "reshape", "fit_transform"], True),
        ValidationCriterion("4.2", "Drift & Trend Parameters", ["drift", "trend", "add"], True)
    ]
)

STEP_5_PROMPT = StepPromptTemplate(
    step_number=5,
    step_title="Macro-Regime Detection & Fundamental Verification",
    prompt_text="""
    Analyze the provided source code for Step 5. Verify external data ingestion and macro logic:
    1. FRED API INTEGRATION: Code must pull the 10Y-2Y Yield Curve, Sahm Rule Unemployment data, and High Yield Credit Spreads.
    2. VALUATION INDEPENDENCE: Verify that the Graham Number and Gordon Fair Value are calculated using completely distinct mathematical bounds and mapped to independent dictionary keys to prevent collision.
    3. REGIME GOVERNANCE: Verify an override mechanism where if the Yield Curve is inverted AND Credit Spreads are widening, the system translates this to a "CREDIT EVENT" state. The macroeconomic penalty for 'RECESSION' or 'CREDIT EVENT' must apply a reduced -5 score deduction rather than a hard score freeze.

    Respond in JSON: {"status": "PASSED/FAILED", "score": 0-100, "findings": [], "missing_elements": []}
    """,
    criteria=[
        ValidationCriterion("5.1", "Macro Data Ingestion", ["FRED", "Yield Curve", "Sahm"], True),
        ValidationCriterion("5.2", "Fundamental Override & Penalties", ["Graham", "Gordon", "-5"], True)
    ]
)

STEP_6_PROMPT = StepPromptTemplate(
    step_number=6,
    step_title="Post-Trade Analytics & Position Sizing",
    prompt_text="""
    Analyze the provided source code for Step 6. Verify risk management and execution analytics:
    1. EXCURSION METRICS: Ensure formulas exist to track Maximum Favorable Excursion (MFE) and Maximum Adverse Excursion (MAE) against executed Entry Prices for every trade.
    2. SECTOR ATTRIBUTION & SYSTEMIC RISK: Verify mathematical implementation of Brinson-Fachler performance attribution (Allocation/Selection effects), CoVaR proxy (Tail Dependency Risk scaling VaR by Beta), and Realized Slippage (Implementation Shortfall).
    3. POSITION SIZING: The code MUST calculate position size dynamically using the ATR multiplier: Account_Value * Risk_Percent / (ATR * Multiplier).
    4. PORTFOLIO HEAT: Check that the Evaluation Engine calculates total open risk across all positions (Position Size * Stop Loss Penalty) and triggers a hard execution halt if portfolio heat exceeds the institutional 6% threshold.

    Respond in JSON: {"status": "PASSED/FAILED", "score": 0-100, "findings": [], "missing_elements": []}
    """,
    criteria=[
        ValidationCriterion("6.1", "MAE & MFE tracking", ["MAE", "MFE", "Maximum Adverse Excursion"], True),
        ValidationCriterion("6.2", "Brinson-Fachler & CoVaR", ["BF_Allocation", "BF_Selection", "CoVaR", "Slippage"], True),
        ValidationCriterion("6.3", "Portfolio Heat", ["Portfolio_Heat", "open_risk", "0.06"], True)
    ]
)

STEP_7_PROMPT = StepPromptTemplate(
    step_number=7,
    step_title="Pre-Trade Risk Gate & Kill Switch",
    prompt_text="""
    Analyze the provided source code for Step 7. Verify the pre-trade risk control layer:
    1. KILL SWITCH: GlobalKillSwitch uses a file-based sentinel (OUTPUT_DIR/KILL_SWITCH). activate() MUST write atomically via write-then-rename. KillSwitchActiveError MUST be raised BEFORE any idempotency dedup in OrderManager.submit_order_with_idempotency so the sentinel cannot be bypassed.
    2. RISK GATE ORDER: PreTradeRiskGate.run_all() MUST short-circuit on first failure. max_order_rate_check MUST be last so blocked orders never consume rate-limit budget. Verify this ordering in the checks list.
    3. CORRELATION CHECK: max_correlation_check MUST use absolute value (|r|) — both highly-positive and highly-negative correlations must block new positions.
    4. CONSERVATIVE PASS: Every check MUST return passed=True when required context is None or missing, NEVER False. A check must never block due to absent data.
    5. HEARTBEAT: _heartbeat() must be spawned as an asyncio background task in main() and cancelled in a try/finally so it always stops even on pipeline crash. It must write OUTPUT_DIR/heartbeat.txt on every tick.
    6. DRY-RUN NON-BYPASS: dry_run=True must NOT bypass the kill switch — KillSwitchActiveError is still raised even in dry-run mode.

    Respond in JSON: {"status": "PASSED/FAILED", "score": 0-100, "findings": [], "missing_elements": []}
    """,
    criteria=[
        ValidationCriterion("7.1", "Atomic kill switch write", ["write-then-rename", "tmp", "rename"], True),
        ValidationCriterion("7.2", "Kill switch before dedup", ["KillSwitchActiveError", "_submitted", "is_active"], True),
        ValidationCriterion("7.3", "Absolute correlation gate", ["abs(corr)", "|r|", "abs_corr"], True),
        ValidationCriterion("7.4", "Rate check is last", ["max_order_rate_check", "max_order_rate"], True),
        ValidationCriterion("7.5", "Heartbeat try/finally", ["_heartbeat", "_hb_task", "CancelledError", "finally"], True),
    ]
)

STEP_8_PROMPT = StepPromptTemplate(
    step_number=8,
    step_title="Opal Research Agent & Multi-Provider Grounding",
    prompt_text="""
    Analyze the provided source code for Step 8 (Tier 9 Scope 4 — Opal, the OpenAI/GPT
    front-of-pipeline research agent). Verify:
    1. PROVIDER ABSTRACTION: OpenAIProvider implements the LLMProvider ABC, lazy-imports openai, and
       soft-fails to None on every error (network/parse/schema/missing-SDK).
    2. GROUNDING (no hallucinated data): generate_research_brief synthesizes REAL retrieved Finnhub
       news/earnings (via signals.news_catalyst helpers), never invents catalysts/numbers; the
       ResearchBrief schema exposes NO numeric price/score fields (CONSTRAINT #4).
    3. OPT-IN: brief generation is gated on OPAL_RESEARCH_ENABLED (default False) — off means no
       openai import, no network.
    4. THREADING: the brief flows into context["research_brief"] and into the Claude rationale
       user-prompt; it never writes a numeric Recommendation field.
    5. SECRETS: OPENAI_API_KEY is SECRET_KEYS-only (CONSTRAINT #3).
    6. ADVISORY-ONLY: no order-submission verbs in llm/research.py.

    Respond in JSON: {"status": "PASSED/FAILED", "score": 0-100, "findings": [], "missing_elements": []}
    """,
    criteria=[
        ValidationCriterion("8.1", "Provider abstraction + soft-fail", ["LLMProvider", "import openai", "None"], True),
        ValidationCriterion("8.2", "Real grounding, no numeric fields", ["fetch_company_news", "fetch_next_earnings", "ResearchBrief"], True),
        ValidationCriterion("8.3", "Opt-in default-off", ["OPAL_RESEARCH_ENABLED"], True),
        ValidationCriterion("8.4", "Threading into rationale prompt", ["research_brief", "context"], True),
        ValidationCriterion("8.5", "Secrets + advisory-only", ["OPENAI_API_KEY", "SECRET_KEYS"], True),
    ]
)

ALL_PROMPTS = [STEP_1_PROMPT, STEP_2_PROMPT, STEP_3_PROMPT, STEP_4_PROMPT, STEP_5_PROMPT, STEP_6_PROMPT, STEP_7_PROMPT, STEP_8_PROMPT]

# --- AGENT VALIDATOR CLASS ---
class GravityAIAuditor:
    """
    This class handles the compilation of prompts, pre-checks the code via RegEx
    for required terminology, and outputs formatted requests for the LLM API.
    """
    def __init__(self):
        pass

    def generate_prompt_for_step(self, step_template: StepPromptTemplate, target_code: str) -> str:
        return f"""
        {SYSTEM_PROMPT}

        {step_template.prompt_text}

        --- TARGET PYTHON CODE TO ANALYZE ---
        {target_code}
        """

    def run_full_validation_suite(self, code_files_map: Dict[int, str]) -> List[AIReviewReport]:
        """
        Takes a dictionary of codebases mapping to each step and processes them.
        In a live environment, this interacts directly with the Claude/OpenAI APIs.
        """
        final_results = []
        for step_num, code in code_files_map.items():
            template = next((t for t in ALL_PROMPTS if t.step_number == step_num), None)
            if template:
                # Prepare payload
                prompt = self.generate_prompt_for_step(template, code)
                logger.info(f"Generated validation payload for Step {step_num}: {template.step_title}")
                # Mock response generator for local testing
                final_results.append(
                    AIReviewReport(
                        step_number=step_num,
                        status="PENDING_API_CALL",
                        score=0.0,
                        findings=["Awaiting Claude API Execution"],
                        missing_elements=[],
                        timestamp=datetime.now(timezone.utc).isoformat()
                    )
                )
        return final_results

# --- EXECUTION SANDBOX ---
if __name__ == "__main__":
    # Example Dummy Data to trigger the local logic test.
    print("--- 🧠 INITIALIZING GRAVITY AI AUDITOR PROMPT HARNESS ---")
    
    dummy_code_map = {
        # Step 4: Forecasting Drift Check
        4: """
        def _forecast_monte_carlo(self, series: pd.Series, steps: int) -> float:
            returns = np.log(series / series.shift(1)).dropna()
            mu, var = returns.mean(), returns.var()
            drift = mu - (0.5 * var)  # Structural Drift
            return float(series.iloc[-1] * np.exp(drift * steps))
        """,
        # Step 6: Post-Trade Analytics
        6: """
        def calculate_tail_dependency(self, var_95: float, beta: float) -> float:
            covar = abs(var_95) * max(beta, 0.0)
            return round(covar, 4)
            
        def calculate_portfolio_heat(self, df: pd.DataFrame) -> float:
            if df['position_size'].sum() == 0: return 0.0
            return (df['position_size'] * df['stop_loss_pct']).sum() / df['position_size'].sum()
        """
    }

    auditor = GravityAIAuditor()
    reports = auditor.run_full_validation_suite(dummy_code_map)
    for report in reports:
        print(f"\n[Step {report.step_number} Payload Prepared]: {report.timestamp}")