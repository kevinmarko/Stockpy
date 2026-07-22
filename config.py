"""Single Source of Truth (SSOT) for the platform's tabular schema. COLUMN_SCHEMA defines every dashboard column's Google Sheets header, internal dict key, and display format, and this module derives the Pandera validation schemas plus header/internal-key accessors from it. Any new field must be added here first before use elsewhere."""

# ==============================================================================
# MODULE: CONFIGURATION & SCHEMA REGISTRY
# File: config.py
# Description: The Single Source of Truth (SSOT) for the dashboard structure.
#              Defines the mapping between Python internal keys and 
#              Google Sheets display headers. Also provides Pandera schemas
#              for strict type safety and data quality validation.
# ==============================================================================

import pandas as pd
import numpy as np
import pandera.pandas as pa
from pandera.typing import Series
import logging

# Configure module logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

""" 
COLUMN_SCHEMA defines the strict order and mapping of data.
*  'header': The column name as it appears in Google Sheets.
*  'key': The dictionary key used in internal quant engines.
*  'format': Formatting type for the frontend.
"""
COLUMN_SCHEMA = [
    # --- IDENTITY & CLASSIFICATION ---
    {"header": "Ticker", "key": "Symbol", "format": "string"},
    {"header": "Price", "key": "Price", "format": "currency"},
    {"header": "Sector", "key": "sector", "format": "string"},
    {"header": "Company Name", "key": "shortName", "format": "string"},
    {"header": "Market Cap", "key": "Market Cap", "format": "currency_large"},
    
    # --- TIME-SERIES TARGETS ---
    {"header": "Target Days", "key": "Target_Days", "format": "number"},
    {"header": "ARIMA Target", "key": "ARIMA", "format": "currency"},
    {"header": "Monte Carlo Target", "key": "MC_Target", "format": "currency"},
    {"header": "MC Lower 95%", "key": "MC_Lower", "format": "currency"},
    {"header": "MC Upper 95%", "key": "MC_Upper", "format": "currency"},
    
    # --- SYSTEMIC MACRO & FUNDAMENTAL ---
    {"header": "Macro Status", "key": "Macro Status", "format": "string"},
    {"header": "Quality Score", "key": "Quality Score", "format": "number"},
    {"header": "Graham Number", "key": "Graham Num", "format": "currency"},
    {"header": "Gordon Fair Value", "key": "Gordon Fair Value", "format": "currency"},
    {"header": "Div Yield", "key": "Div Yield", "format": "percent"},
    {"header": "P/E", "key": "P/E", "format": "number"},
    {"header": "Book Value", "key": "Book Value", "format": "currency"},
    {"header": "Dividend Premium Spread", "key": "DPS", "format": "percent"},
    {"header": "Institutional Velocity", "key": "Institutional Velocity", "format": "number"},
    {"header": "Dividend Payback Horizon", "key": "DPH", "format": "number"},
    {"header": "Leverage Distress Factor", "key": "Leverage Distress Factor", "format": "number"},
    
    # --- TECHNICAL & VOLATILITY ---
    {"header": "Volume", "key": "Volume", "format": "number"},
    {"header": "RSI", "key": "RSI", "format": "number"},
    {"header": "RSI(2)", "key": "RSI_2", "format": "number"},
    {"header": "MACD Line", "key": "MACD_Line", "format": "number"},
    {"header": "MACD Signal", "key": "MACD_Signal", "format": "number"},
    {"header": "ATR", "key": "ATR", "format": "number"},
    {"header": "SMA 5", "key": "SMA_5", "format": "currency"},
    {"header": "SMA 50", "key": "SMA_50", "format": "currency"},
    {"header": "SMA 200", "key": "SMA_200", "format": "currency"},
    {"header": "Aroon Up", "key": "Aroon Up", "format": "number"},
    {"header": "Aroon Down", "key": "Aroon Down", "format": "number"},
    {"header": "Aroon Oscillator", "key": "Aroon Oscillator", "format": "number"},
    {"header": "Coppock Curve", "key": "Coppock Curve", "format": "number"},
    {"header": "Chandelier Exit", "key": "Chandelier Exit", "format": "currency"},
    {"header": "Relative Strength", "key": "RS vs SPY", "format": "number"},
    {"header": "RS Momentum Slope", "key": "RS-MACD", "format": "number"},
    {"header": "GARCH Vol", "key": "GARCH_Vol", "format": "number"},
    {"header": "Realized Vol Rank", "key": "Realized_Vol_Rank", "format": "number"},
    {"header": "True IVR", "key": "True_IVR", "format": "number"},
    {"header": "Volatility Risk Premium", "key": "VRP", "format": "percent"},
    {"header": "Options IV Edge", "key": "Options IV Edge", "format": "percent"},
    {"header": "ROC 12M", "key": "ROC_12M", "format": "percent"},
    {"header": "ROC 6M", "key": "ROC_6M", "format": "percent"},
    {"header": "Momentum Vol Scaled", "key": "Momentum_Vol_Scaled", "format": "number"},
    
    # --- RISK & PERFORMANCE METRICS ---
    {"header": "VaR 95", "key": "VaR 95", "format": "percent"},
    {"header": "Sortino Ratio", "key": "Sortino Ratio", "format": "number"},
    {"header": "Max Drawdown", "key": "Max Drawdown", "format": "percent"},
    {"header": "Beta", "key": "Beta", "format": "number"},
    {"header": "Tail Dependency Risk", "key": "CoVaR Proxy", "format": "number"},
    {"header": "Edge Ratio", "key": "Edge Ratio", "format": "number"},
    {"header": "Realized Slippage", "key": "Realized Slippage", "format": "percent"},
    
    # --- INTERVAL FORECASTING ---
    {"header": "Forecast 10 Day", "key": "Forecast_10", "format": "currency"},
    {"header": "Forecast 30 Day", "key": "Forecast_30", "format": "currency"},
    {"header": "Forecast 60 Day", "key": "Forecast_60", "format": "currency"},
    {"header": "Forecast 90 Day", "key": "Forecast_90", "format": "currency"},
    {"header": "Prophet Lower 30D", "key": "Forecast_30_Prophet_Lower", "format": "currency"},
    {"header": "Prophet Upper 30D", "key": "Forecast_30_Prophet_Upper", "format": "currency"},
    
    # --- TACTICAL EXECUTION ---
    {"header": "Action Signal", "key": "Action Signal", "format": "string"},
    {"header": "Advice", "key": "Advice", "format": "string"},
    {"header": "Actionable Advice Signal", "key": "Actionable Advice Signal", "format": "string"},
    {"header": "Kelly Size", "key": "Kelly Target", "format": "percent"},
    {"header": "Option Strategy", "key": "Option Strategy", "format": "string"},
    {"header": "Buy Range", "key": "buyRange", "format": "string"},
    # Dedicated sell-side range produced by strategy_engine.apply_sell_side_range.
    # Always populated (every Action Signal yields a sellRange string) so the
    # dashboard / Google Sheets sink can render a resting take-profit + trailing-stop
    # plan alongside the buy corridor. See strategy_engine.apply_sell_side_range
    # for level construction (1.5σ / 3σ ATR envelope, forecast-aware upper bound,
    # Chandelier-anchored trailing stop).
    {"header": "Sell Range", "key": "sellRange", "format": "string"},
    {"header": "Strategy Notes", "key": "Strategy Explainer Notes", "format": "string"},

    # ==========================================================
    # --- ROBINHOOD INTEGRATION ---
    # ==========================================================
    {"header": "Robinhood Shares", "key": "Robinhood Shares", "format": "number"},
    {"header": "Robinhood Avg Cost", "key": "Robinhood Avg Cost", "format": "currency"},
    {"header": "Robinhood Dividends", "key": "Robinhood Dividends", "format": "currency"},
    {"header": "Robinhood Advice", "key": "Robinhood Advice", "format": "string"},

    # ==========================================================
    # --- NEW POST-TRADE EVALUATION & SYSTEM HEAT METRICS ---
    # ==========================================================
    {"header": "Max Favorable Excursion", "key": "MFE", "format": "percent"},
    {"header": "Max Adverse Excursion", "key": "MAE", "format": "percent"},
    {"header": "BF Allocation Effect", "key": "BF_Allocation", "format": "number"},
    {"header": "BF Selection Effect", "key": "BF_Selection", "format": "number"},
    {"header": "Portfolio Heat", "key": "Portfolio_Heat", "format": "percent"},

    # ==========================================================
    # --- CROSS-SECTIONAL MOMENTUM (Jegadeesh-Titman 1993) ---
    # ==========================================================
    {"header": "XSec 12-1M Return", "key": "XSec_12_1M", "format": "percent"},
    {"header": "XSec Momentum Rank", "key": "XSec_Momentum_Rank", "format": "percent"},

    # ==========================================================
    # --- MULTIFACTOR (Fama-French-style; Hou-Xue-Zhang 2020 priors) ---
    # ==========================================================
    {"header": "Value Z-Score", "key": "Value_Z", "format": "number"},
    {"header": "Quality Z-Score", "key": "Quality_Z", "format": "number"},
    {"header": "Low Vol Z-Score", "key": "LowVol_Z", "format": "number"},
    {"header": "Size Z-Score", "key": "Size_Z", "format": "number"},
    {"header": "Multifactor Composite", "key": "Multifactor_Composite", "format": "number"},

    # ==========================================================
    # --- HMM REGIME SECOND OPINION (Hamilton 1989) ---
    # ==========================================================
    {"header": "HMM Risk-On Probability", "key": "HMM_Risk_On_Probability", "format": "percent"},

    # ==========================================================
    # --- NEWS CATALYST (Tier 2.4, signals/news_catalyst.py) ---
    # Populated by NewsCatalystSignal.pre_compute() via orchestrator
    # writeback; NaN / "" when Finnhub is not configured or the
    # module hasn't run for a symbol this cycle.
    # ==========================================================
    {"header": "News Sentiment", "key": "News_Sentiment", "format": "number"},
    {"header": "Earnings Date", "key": "Earnings_Date", "format": "string"},

    # ==========================================================
    # --- SENTIMENT CREDIBILITY (Sentiment Pipeline Phase 4, signals/credibility.py) ---
    # Populated by NewsCatalystSignal.pre_compute() via orchestrator writeback,
    # aggregating the current trading day's sentiment_ingestion_audit rows
    # (data/sentiment_sources.py, data/historical_store.py). NaN when no
    # multi-source social documents exist for a symbol this cycle -- distinct
    # from News_Sentiment (Finnhub-headline-only), never a fabricated 0.0.
    # ==========================================================
    {"header": "Credibility Weighted Sentiment", "key": "Credibility_Weighted_Sentiment", "format": "number"},
    {"header": "Bot Activity Ratio", "key": "Bot_Activity_Ratio", "format": "percent"},
    {"header": "Aggregated Source Credibility", "key": "Aggregated_Source_Credibility", "format": "number"},

    # ==========================================================
    # --- CORRELATION CLUSTER (Tier 2.5, research_engine.py) ---
    # Populated on-demand by the GUI Reports tab; NaN in the main
    # orchestrator run (no historical batch fetch required).
    # ==========================================================
    {"header": "Cluster", "key": "Correlation_Cluster", "format": "number"},

    # ==========================================================
    # --- ADVISORY METADATA (docs/CONFIG_SCHEMA_PLAN.md Phase C1) ---
    # Five fields that reporting/sheet_publisher.py::rec_to_sheet_row()
    # already computed from engine.advisory.Recommendation but which were
    # silently dropped before reaching the Sheet because they matched
    # neither a COLUMN_SCHEMA key nor header (write_recommendations()'s
    # `df[[h for h in final_headers if h in df.columns]]` filter step).
    # Confirmed genuinely new information, not duplicates of any existing
    # column (see the PR description for the full case-by-case audit of
    # all 8 originally-dropped keys). Advisory-path-only: the orchestrator
    # path (main_orchestrator.py / pipeline/production_steps.py) blank/NaN
    # fills these, matching the established pattern used for every other
    # advisory-vs-orchestrator asymmetric column (e.g. "Macro Status").
    # ==========================================================
    # Raw StrategyEngine weighted-sum score (0-100 scale) — distinct from
    # "Quality Score" (a fundamentals-only metric) and from "Kelly Target"
    # (post-Kelly position sizing); this is the signal-aggregation score
    # that gates BUY/SELL/HOLD before sizing is applied.
    {"header": "Advisory Score", "key": "Score", "format": "number"},
    # 30-day forecast expressed as a fractional % change from current price
    # (Forecast_30 is the dollar price target; this is the derived percent).
    {"header": "Forecast 30D % Change", "key": "Forecast_30_Pct", "format": "percent"},
    # Recommendation.conviction, in [0.0, 1.0] — confidence in the action,
    # already decayed for STALE/PARTIAL data quality (see engine/advisory.py
    # Step 13). Not represented by any existing column.
    {"header": "Advisory Conviction", "key": "Advisory_Conviction", "format": "percent"},
    # Recommendation.suggested_position_pct — the FINAL recommended
    # allocation fraction after Kelly sizing, CONFIG["max_single_position_pct"]
    # clamping, and the holding-aware overlay (0.0 for SELL/HOLD). Distinct
    # from "Kelly Target" (the raw pre-clamp Kelly fraction).
    {"header": "Advisory Position %", "key": "Advisory_Position_Pct", "format": "percent"},
    # Recommendation.data_quality ("OK"/"STALE"/"PARTIAL") — no existing
    # column surfaces per-symbol data-quality state end-to-end.
    {"header": "Advisory Data Quality", "key": "Advisory_Data_Quality", "format": "string"},
]

def get_headers() -> list[str]:
    """Returns list of display headers for gspread/Google Sheets."""
    return [col["header"] for col in COLUMN_SCHEMA]

def get_internal_keys() -> list[str]:
    """Returns list of internal dictionary keys for DataFrame construction."""
    return [col["key"] for col in COLUMN_SCHEMA]

def get_rename_mapping() -> dict[str, str]:
    """Returns dict mapping internal keys to external headers."""
    return {col["key"]: col["header"] for col in COLUMN_SCHEMA}

# --- PANDERA SCHEMA DEFINITIONS ---

class MarketDataSchema(pa.DataFrameModel):
    """Schema for raw stock history (OHLCV) fetched from data providers."""
    Open: Series[float] = pa.Field(nullable=True)
    High: Series[float] = pa.Field(nullable=True)
    Low: Series[float] = pa.Field(nullable=True)
    Close: Series[float] = pa.Field(nullable=True)
    Volume: Series[float] = pa.Field(nullable=True, ge=0)

    class Config:
        coerce = True

    @pa.dataframe_check
    def high_greater_or_equal_low(cls, df: pd.DataFrame) -> Series[bool]:
        valid_mask = df["High"].notna() & df["Low"].notna()
        return ~valid_mask | (df["High"] >= df["Low"])

# Dynamically build the DashboardSchema using COLUMN_SCHEMA to guarantee alignment
_dashboard_columns = {}
for col in COLUMN_SCHEMA:
    if col["key"] == "Symbol":
        _dashboard_columns[col["key"]] = pa.Column(str, checks=pa.Check.str_length(1, 10))
    elif col["format"] in ["currency", "currency_large", "percent", "number"]:
        _dashboard_columns[col["key"]] = pa.Column(float, nullable=True)
    else:
        _dashboard_columns[col["key"]] = pa.Column(str, nullable=True)

DashboardSchema = pa.DataFrameSchema(
    columns=_dashboard_columns,
    coerce=True
)

class Config:
    @staticmethod
    def validate_config() -> None:
        """Validates that keys and headers in COLUMN_SCHEMA are unique and consistent."""
        keys = [c["key"] for c in COLUMN_SCHEMA]
        headers = [c["header"] for c in COLUMN_SCHEMA]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate keys detected in COLUMN_SCHEMA")
        if len(headers) != len(set(headers)):
            raise ValueError("Duplicate headers detected in COLUMN_SCHEMA")
        logging.info("✅ config.py schema definition verified successfully.")

if __name__ == "__main__":
    Config.validate_config()
