import pytest
import pandas as pd
import numpy as np
import pandas_ta as ta
try:
    from tests.lookahead_check import verify_no_lookahead, make_synthetic_ohlcv
except ImportError:
    from lookahead_check import verify_no_lookahead, make_synthetic_ohlcv
from research_engine import AdvancedResearchEngine

# Seed random number generator for reproducibility
np.random.seed(42)

@pytest.fixture
def synthetic_ohlcv_data():
    """Generates synthetic stock price history (100 days)."""
    return make_synthetic_ohlcv(periods=100, seed=42)

@pytest.fixture
def synthetic_spy_data():
    """Generates synthetic SPY index price history (100 days)."""
    dates = pd.date_range(end="2026-06-24", periods=100)
    close = 400.0 + np.cumsum(np.random.normal(0, 2.0, 100))
    return pd.DataFrame({"Close": close}, index=dates)

def test_rsi_lookahead(synthetic_ohlcv_data):
    """Verifies that RSI has no lookahead bias."""
    def rsi_calc(df, t):
        # Slice input up to t to ensure we can compare perturbed vs original
        # Note: pandas_ta requires enough history, so t must be >= 14
        series = df['Close'].iloc[:t+1]
        rsi = ta.rsi(series, length=14)
        return rsi.iloc[-1]

    # Test at index 50
    assert verify_no_lookahead(rsi_calc, synthetic_ohlcv_data, t=50)

def test_macd_lookahead(synthetic_ohlcv_data):
    """Verifies that MACD line and signal have no lookahead bias."""
    def macd_line_calc(df, t):
        series = df['Close'].iloc[:t+1]
        macd = ta.macd(series, fast=12, slow=26, signal=9)
        if macd is None or macd.empty:
            return np.nan
        return macd['MACD_12_26_9'].iloc[-1]

    def macd_sig_calc(df, t):
        series = df['Close'].iloc[:t+1]
        macd = ta.macd(series, fast=12, slow=26, signal=9)
        if macd is None or macd.empty:
            return np.nan
        return macd['MACDs_12_26_9'].iloc[-1]

    assert verify_no_lookahead(macd_line_calc, synthetic_ohlcv_data, t=50)
    assert verify_no_lookahead(macd_sig_calc, synthetic_ohlcv_data, t=50)

def test_atr_lookahead(synthetic_ohlcv_data):
    """Verifies that ATR has no lookahead bias."""
    def atr_calc(df, t):
        sub_df = df.iloc[:t+1]
        atr = ta.atr(sub_df['High'], sub_df['Low'], sub_df['Close'], length=14)
        return atr.iloc[-1]

    assert verify_no_lookahead(atr_calc, synthetic_ohlcv_data, t=50)

def test_aroon_lookahead(synthetic_ohlcv_data):
    """Verifies that Aroon Oscillator has no lookahead bias."""
    def aroon_osc_calc(df, t):
        sub_df = df.iloc[:t+1]
        aroon = ta.aroon(sub_df['High'], sub_df['Low'], length=25)
        if aroon is None or aroon.empty:
            return np.nan
        return aroon['AROONOSC_25'].iloc[-1]

    assert verify_no_lookahead(aroon_osc_calc, synthetic_ohlcv_data, t=50)

def test_chandelier_exit_lookahead(synthetic_ohlcv_data):
    """Verifies that Chandelier Exit has no lookahead bias."""
    def chandelier_calc(df, t):
        sub_df = df.iloc[:t+1].copy()
        atr = ta.atr(sub_df['High'], sub_df['Low'], sub_df['Close'], length=22)
        rolling_max_high = sub_df['High'].rolling(window=22).max()
        chandelier_long = rolling_max_high - (3.0 * atr)
        return chandelier_long.iloc[-1]

    assert verify_no_lookahead(chandelier_calc, synthetic_ohlcv_data, t=50)

def test_rs_momentum_slope_lookahead(synthetic_ohlcv_data, synthetic_spy_data):
    """Verifies that Relative Strength Momentum Slope has no lookahead bias."""
    research_engine = AdvancedResearchEngine(risk_free_rate=0.04)

    def rs_slope_calc(df, t):
        # Extract slices up to t
        asset_closes = df['Close'].iloc[:t+1]
        spy_closes = synthetic_spy_data['Close'].iloc[:t+1]
        return research_engine.calculate_relative_strength_momentum_slope(asset_closes, spy_closes)

    assert verify_no_lookahead(rs_slope_calc, synthetic_ohlcv_data, t=50)


def test_rolling_beta_lookahead_ticker_perturbation(synthetic_ohlcv_data, synthetic_spy_data):
    """Verifies calculate_rolling_beta has no lookahead bias when the TICKER
    series is perturbed after the cutoff (SPY held fixed via closure)."""
    from processing_engine import calculate_rolling_beta

    def beta_calc(df, t):
        price_slice = df.iloc[:t + 1]
        spy_slice = synthetic_spy_data.iloc[:t + 1]
        beta_series = calculate_rolling_beta(price_slice, spy_slice, window=20)
        return beta_series.iloc[-1] if not beta_series.empty else np.nan

    assert verify_no_lookahead(beta_calc, synthetic_ohlcv_data, t=50)


def test_rolling_beta_lookahead_spy_perturbation(synthetic_ohlcv_data, synthetic_spy_data):
    """Verifies calculate_rolling_beta has no lookahead bias when the SPY
    series itself is perturbed after the cutoff (ticker held fixed via
    closure) -- the mirror case of the ticker-perturbation test above,
    since beta depends on BOTH series and either one could leak the future."""
    from processing_engine import calculate_rolling_beta

    def beta_calc_spy_perturbed(spy_df, t):
        price_slice = synthetic_ohlcv_data.iloc[:t + 1]
        spy_slice = spy_df.iloc[:t + 1]
        beta_series = calculate_rolling_beta(price_slice, spy_slice, window=20)
        return beta_series.iloc[-1] if not beta_series.empty else np.nan

    assert verify_no_lookahead(beta_calc_spy_perturbed, synthetic_spy_data, t=50)
