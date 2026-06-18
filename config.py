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
import pandera as pa
from pandera.typing import Series
import logging

"""
COLUMN_SCHEMA defines the strict order and mapping of data.
- 'header': The column name as it appears in Google Sheets.
- 'key': The dictionary key used in processing_engine.py or forecasting_engine.py.
- 'format': Formatting type for the frontend (currency, percent, number, string).
"""

COLUMN_SCHEMA = [
    # --- IDENTITY & CLASSIFICATION ---
    {"header": "Ticker",            "key": "Symbol",            "format": "string"},
    {"header": "Price",             "key": "Price",             "format": "currency"},
    {"header": "Sector",            "key": "sector",            "format": "string"},
    {"header": "Company Name",      "key": "shortName",         "format": "string"},
    {"header": "Market Cap",        "key": "Market Cap",        "format": "currency_large"},

    # --- TARGETS & FORECAST MODELS ---
    {"header": "Target Days",       "key": "Target_Days",       "format": "number"},
    {"header": "ARIMA Target",      "key": "ARIMA",             "format": "currency"},
    {"header": "Monte Carlo Target","key": "MC_Target",         "format": "currency"},
    {"header": "MC Lower 95%",      "key": "MC_Lower",          "format": "currency"},
    {"header": "MC Upper 95%",      "key": "MC_Upper",          "format": "currency"},

    # --- MACRO & REGIME ---
    {"header": "Macro Status",      "key": "Macro Status",      "format": "string"},
    {"header": "Quality Score",     "key": "Quality Score",     "format": "number"},

    # --- TECHNICAL INDICATORS ---
    {"header": "Volume",            "key": "Volume",            "format": "number"},
    {"header": "RSI",               "key": "RSI",               "format": "number"},
    {"header": "MACD Line",         "key": "MACD_Line",         "format": "number"},
    {"header": "MACD Signal",       "key": "MACD_Signal",       "format": "number"},
    {"header": "ATR",               "key": "ATR",               "format": "number"},
    {"header": "SMA 50",            "key": "SMA_50",            "format": "currency"},
    {"header": "SMA 200",           "key": "SMA_200",           "format": "currency"},

    # NEW: AROON & RELATIVE STRENGTH
    {"header": "Aroon Up",          "key": "Aroon Up",          "format": "number"},
    {"header": "Aroon Down",        "key": "Aroon Down",        "format": "number"},
    {"header": "Relative Strength", "key": "RS vs SPY",         "format": "number"},

    # --- FUNDAMENTAL VALUATION ---
    {"header": "Graham Number",     "key": "Graham Num",        "format": "currency"},
    {"header": "Gordon Fair Value", "key": "Gordon Fair Value", "format": "currency"},
    {"header": "Div Yield",         "key": "Div Yield",         "format": "percent"},
    {"header": "P/E",               "key": "P/E",               "format": "number"},
    {"header": "Book Value",        "key": "Book Value",        "format": "currency"},

    # --- RISK METRICS ---
    {"header": "VaR 95",            "key": "VaR 95",            "format": "percent"},
    {"header": "Sortino Ratio",     "key": "Sortino Ratio",     "format": "number"},
    {"header": "Max Drawdown",      "key": "Max Drawdown",      "format": "percent"},
    {"header": "Beta",              "key": "Beta",              "format": "number"},

    # --- MULTI-HORIZON FORECASTS ---
    {"header": "Forecast 10 Day",   "key": "Forecast_10",       "format": "currency"},
    {"header": "Forecast 30 Day",   "key": "Forecast_30",       "format": "currency"},
    {"header": "Forecast 60 Day",   "key": "Forecast_60",       "format": "currency"},
    {"header": "Forecast 90 Day",   "key": "Forecast_90",       "format": "currency"},
]

def get_headers():
    """Returns list of display headers for gspread/Google Sheets."""
    return [col["header"] for col in COLUMN_SCHEMA]

def get_internal_keys():
    """Returns list of internal dictionary keys for DataFrame construction."""
    return [col["key"] for col in COLUMN_SCHEMA]

def get_rename_mapping():
    """
    Returns dict mapping internal keys to external headers.
    Useful for: df.rename(columns=get_rename_mapping())
    """
    return {col["key"]: col["header"] for col in COLUMN_SCHEMA}

# ==============================================================================
# PANDERA SCHEMA DEFINITIONS FOR GATEWAY SECURITY
# ==============================================================================

class MarketDataSchema(pa.DataFrameModel):
    """
    Schema for raw stock history (OHLCV) fetched from data providers.
    Enforces basic pricing rules and volume constraints.
    """
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


# Schema for consolidated Dashboard DataFrame containing all metrics
# before writing to the target output sheet.
DashboardSchema = pa.DataFrameSchema(
    columns={
        "Symbol": pa.Column(str, checks=pa.Check.str_length(1, 10)),
        "Price": pa.Column(float, checks=pa.Check.greater_than_or_equal_to(0), nullable=True),
        "sector": pa.Column(str, nullable=True),
        "shortName": pa.Column(str, nullable=True),
        "Market Cap": pa.Column(float, nullable=True),
        "Volume": pa.Column(float, nullable=True),
        "RSI": pa.Column(float, nullable=True),
        "Beta": pa.Column(float, nullable=True),
    },
    coerce=True,
    strict=False  # Allow other intermediate columns
)



class Config:
    @staticmethod
    def validate_config():
        """Validates that keys and headers in COLUMN_SCHEMA are unique and consistent."""
        keys = [c["key"] for c in COLUMN_SCHEMA]
        headers = [c["header"] for c in COLUMN_SCHEMA]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate keys detected in COLUMN_SCHEMA")
        if len(headers) != len(set(headers)):
            raise ValueError("Duplicate headers detected in COLUMN_SCHEMA")
        logging.info("✅ config.py schema definition verified successfully.")


if __name__ == "__main__":
    # Run a quick self-test if this file is executed directly
    Config.validate_config()
