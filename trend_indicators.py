"""Aroon oscillator, Coppock curve and Chandelier exit — core equity-path module.

Moved verbatim out of ``technical_options_engine.TechnicalOptionsEngine.
calculate_indicators`` (2026-09, step 3d). ``StrategyEvalStep`` uses these
for its signal and stop logic, and they fill the dashboard's
``Aroon Oscillator`` / ``Coppock Curve`` / ``Chandelier Exit`` columns.

Note these are NOT the same parameterisation as ``processing_engine``'s own
Aroon/Coppock/Chandelier columns (Aroon length 14 here vs 25 there, a
different Coppock window, long AND short Chandelier exits here), so the two
must not be swapped for each other without a deliberate, measured change.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import pandas as pd
import pandas_ta_classic as ta

from volatility.garch import GarchVolatilityEstimator

logger = logging.getLogger(__name__)


# Monkey patch chandelier_exit if not present in pandas_ta_classic
if not hasattr(ta, "chandelier_exit"):
    def chandelier_exit_patch(self, length=22, multiplier=3.0, **kwargs):
        """
        Custom chandelier_exit implementation registered to pandas_ta.
        Uses a lookback period and ATR multiplier to compute long and short exits.
        """
        df = self._df
        atr_val = self.atr(length=length)
        highest_high = df['High'].rolling(window=length).max()
        lowest_low = df['Low'].rolling(window=length).min()
        long = highest_high - (multiplier * atr_val)
        short = lowest_low + (multiplier * atr_val)
        return pd.DataFrame({
            "CHANDELIER_EXIT_LONG": long,
            "CHANDELIER_EXIT_SHORT": short
        }, index=df.index)
        
    ta.chandelier_exit = chandelier_exit_patch
    try:
        from pandas_ta_classic.core import AnalysisIndicators
        setattr(AnalysisIndicators, "chandelier_exit", chandelier_exit_patch)
    except Exception as e:
        logger.warning(f"Failed to bind chandelier_exit to AnalysisIndicators: {e}")


def calculate_trend_exit_indicators(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Calculates Aroon Oscillator, Coppock Curve, and Chandelier Exit.
    Returns the latest values as a dictionary.
    """
    df_clean = GarchVolatilityEstimator.sanitize_ohlcv(df)
    if len(df_clean) < 22:
        logger.warning("Insufficient data points (< 22) for technical indicator calculations.")
        return {
            "Aroon_Oscillator": 0.0,
            "Coppock_Curve": 0.0,
            "Chandelier_Long": 0.0,
            "Chandelier_Short": 0.0
        }

    # 1. Aroon Oscillator using pandas-ta
    aroon_df = df_clean.ta.aroon(length=14)
    if aroon_df is not None and not aroon_df.empty:
        # Column name is typically AROONOSC_14
        osc_col = [col for col in aroon_df.columns if "AROONOSC" in col]
        aroon_osc = float(aroon_df[osc_col[0]].iloc[-1]) if osc_col else 0.0
    else:
        aroon_osc = 0.0

    # 2. Coppock Curve using pandas-ta with length=10, fast=11, slow=14
    coppock_series = df_clean.ta.coppock(length=10, fast=11, slow=14)
    coppock_val = float(coppock_series.iloc[-1]) if (coppock_series is not None and not coppock_series.empty) else 0.0

    # 3. Chandelier Exit (ta.chandelier_exit using a 22-day lookback and 3.0 ATR multiplier)
    chandelier_df = df_clean.ta.chandelier_exit(length=22, multiplier=3.0)
    if chandelier_df is not None and not chandelier_df.empty:
        long_col = [col for col in chandelier_df.columns if "LONG" in col]
        short_col = [col for col in chandelier_df.columns if "SHORT" in col]
        chandelier_long = float(chandelier_df[long_col[0]].iloc[-1]) if long_col else 0.0
        chandelier_short = float(chandelier_df[short_col[0]].iloc[-1]) if short_col else 0.0
    else:
        # Fallback manual calculation of Chandelier Exit
        atr_series = df_clean.ta.atr(length=22)
        if atr_series is not None and not atr_series.empty:
            atr_val = atr_series.iloc[-1]
            highest_high = df_clean['High'].rolling(window=22).max().iloc[-1]
            lowest_low = df_clean['Low'].rolling(window=22).min().iloc[-1]
            chandelier_long = highest_high - (3.0 * atr_val)
            chandelier_short = lowest_low + (3.0 * atr_val)
        else:
            chandelier_long = 0.0
            chandelier_short = 0.0

    return {
        "Aroon_Oscillator": aroon_osc,
        "Coppock_Curve": coppock_val,
        "Chandelier_Long": float(chandelier_long),
        "Chandelier_Short": float(chandelier_short)
    }
