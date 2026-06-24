import pytest
import pandas as pd
import numpy as np
import math

from signals.base import SignalContext, SignalOutput
from signals.timeseries_momentum import TimeSeriesMomentumSignal
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO
from processing_engine import ProcessingEngine
from tests.lookahead_check import verify_no_lookahead


@pytest.fixture
def base_context():
    bar = MarketBarDTO(
        date=pd.Timestamp("2026-06-24"),
        ticker="AAPL",
        open_price=150.0,
        high_price=155.0,
        low_price=149.0,
        close_price=154.0,
        volume=1000000
    )
    fund = FundamentalDataDTO(
        ticker="AAPL", pe_ratio=15.0, pb_ratio=2.0, dividend_yield=0.01,
        book_value=50.0, eps_trailing=10.0, dividend_growth_rate=0.05,
        payout_ratio=0.3, sector="Technology", company_name="Apple Inc"
    )
    macro = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=3.5, inflation_rate=2.0,
        nominal_10y=4.0, vix_value=15.0
    )
    return SignalContext(bar=bar, fundamentals=fund, macro=macro)


def test_pure_uptrend(base_context):
    # Generates a synthetic upward trend
    # 300 days of Close increasing from 100 to 400
    prices = np.linspace(100.0, 400.0, 300)
    dates = pd.date_range(end="2026-06-24", periods=300)
    df = pd.DataFrame({
        "Open": prices - 0.5,
        "High": prices + 1.0,
        "Low": prices - 1.0,
        "Close": prices,
        "Volume": [1000] * 300
    }, index=dates)

    pe = ProcessingEngine()
    df_calc = pe.calculate_momentum_metrics(df)
    
    last_row = df_calc.iloc[-1]
    assert last_row["ROC_12M"] > 0
    
    # Evaluate signal
    sig = TimeSeriesMomentumSignal()
    row = pd.Series({
        "ROC_12M": last_row["ROC_12M"],
        "GARCH_Vol": 0.05  # Low vol
    })
    
    out = sig.compute(row, base_context)
    assert out.score > 0.8
    assert "Bullish" in out.explanation


def test_pure_downtrend(base_context):
    # Generates a synthetic downward trend
    # 300 days of Close decreasing from 400 to 100
    prices = np.linspace(400.0, 100.0, 300)
    dates = pd.date_range(end="2026-06-24", periods=300)
    df = pd.DataFrame({
        "Open": prices - 0.5,
        "High": prices + 1.0,
        "Low": prices - 1.0,
        "Close": prices,
        "Volume": [1000] * 300
    }, index=dates)

    pe = ProcessingEngine()
    df_calc = pe.calculate_momentum_metrics(df)
    
    last_row = df_calc.iloc[-1]
    assert last_row["ROC_12M"] < 0
    
    # Evaluate signal
    sig = TimeSeriesMomentumSignal()
    row = pd.Series({
        "ROC_12M": last_row["ROC_12M"],
        "GARCH_Vol": 0.05  # Low vol
    })
    
    out = sig.compute(row, base_context)
    assert out.score < -0.8
    assert "Bearish" in out.explanation


def test_sideways(base_context):
    # Flat series: price is 100.0 every day
    prices = np.ones(300) * 100.0
    dates = pd.date_range(end="2026-06-24", periods=300)
    df = pd.DataFrame({
        "Open": prices,
        "High": prices,
        "Low": prices,
        "Close": prices,
        "Volume": [1000] * 300
    }, index=dates)

    pe = ProcessingEngine()
    df_calc = pe.calculate_momentum_metrics(df)
    
    last_row = df_calc.iloc[-1]
    assert math.isclose(last_row["ROC_12M"], 0.0, abs_tol=1e-9)
    
    # Evaluate signal
    sig = TimeSeriesMomentumSignal()
    row = pd.Series({
        "ROC_12M": last_row["ROC_12M"],
        "GARCH_Vol": 0.05
    })
    
    out = sig.compute(row, base_context)
    assert math.isclose(out.score, 0.0, abs_tol=1e-2)


def test_lookahead_tsmom():
    pe = ProcessingEngine()
    
    # Generates random walk
    np.random.seed(42)
    prices = 100.0 + np.cumsum(np.random.normal(0, 1.0, 300))
    dates = pd.date_range(end="2026-06-24", periods=300)
    df = pd.DataFrame({
        "Open": prices - 0.5,
        "High": prices + 1.0,
        "Low": prices - 1.0,
        "Close": prices,
        "Volume": [1000] * 300
    }, index=dates)

    def calc_tsmom_indicator(data, t):
        df_calc = pe.calculate_momentum_metrics(data.copy())
        return float(df_calc["ROC_12M"].iloc[t])

    # Verify at index 280
    assert verify_no_lookahead(calc_tsmom_indicator, df, t=280)
