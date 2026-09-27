"""Aroon oscillator, Coppock curve and Chandelier exit — core equity-path module.

Moved out of ``technical_options_engine.TechnicalOptionsEngine.
calculate_indicators`` (2026-09, step 3d) with the same formulas, now calling
``pandas_ta_classic`` directly instead of the shared ``DataFrame.ta`` accessor
(see the function docstring). ``StrategyEvalStep`` uses these
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


def calculate_trend_exit_indicators(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Calculates Aroon Oscillator, Coppock Curve, and Chandelier Exit.
    Returns the latest values as a dictionary.

    Calls ``pandas_ta_classic``'s functions directly instead of going through
    the ``DataFrame.ta`` accessor. Both ``pandas_ta`` and ``pandas_ta_classic``
    are installed and register that same accessor, so ``df.ta`` resolves to
    whichever library was imported last; under ``pandas_ta`` the old
    ``df.ta.chandelier_exit`` call returned differently named columns and the
    Chandelier values silently fell back to 0.0. The formulas are unchanged:
    Aroon(14), Coppock(10, 11, 14), and a 22-day, 3xATR Chandelier exit
    (classic has no ``chandelier_exit``, so the old code always used this
    rolling-extreme formula via its own registered patch).
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

    high, low, close = df_clean['High'], df_clean['Low'], df_clean['Close']

    # 1. Aroon Oscillator
    aroon_df = ta.aroon(high, low, length=14)
    if aroon_df is not None and not aroon_df.empty:
        osc_col = [col for col in aroon_df.columns if "AROONOSC" in col]
        aroon_osc = float(aroon_df[osc_col[0]].iloc[-1]) if osc_col else 0.0
    else:
        aroon_osc = 0.0

    # 2. Coppock Curve (length=10, fast=11, slow=14)
    coppock_series = ta.coppock(close, length=10, fast=11, slow=14)
    coppock_val = float(coppock_series.iloc[-1]) if (coppock_series is not None and not coppock_series.empty) else 0.0

    # 3. Chandelier Exit (22-day lookback, 3.0 ATR multiplier)
    atr_series = ta.atr(high, low, close, length=22)
    if atr_series is not None and not atr_series.empty:
        chandelier_long = high.rolling(window=22).max() - (3.0 * atr_series)
        chandelier_short = low.rolling(window=22).min() + (3.0 * atr_series)
        chandelier_long = float(chandelier_long.iloc[-1])
        chandelier_short = float(chandelier_short.iloc[-1])
    else:
        chandelier_long = 0.0
        chandelier_short = 0.0

    return {
        "Aroon_Oscillator": aroon_osc,
        "Coppock_Curve": coppock_val,
        "Chandelier_Long": float(chandelier_long),
        "Chandelier_Short": float(chandelier_short)
    }
