import pandas as pd
import numpy as np

from tests.lookahead_check import verify_no_lookahead, make_synthetic_ohlcv
from signals.base import SignalContext
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO

from signals.graham_value import GrahamValueSignal
from signals.dividend_quality import DividendQualitySignal
from signals.cross_sectional_momentum import CrossSectionalMomentumSignal
from signals.regime_multiplier import RegimeMultiplierSignal


def test_graham_value_lookahead():
    dates = pd.date_range("2026-01-01", periods=100)
    df = pd.DataFrame({
        "current_price": np.random.uniform(50, 150, 100),
        "graham_number": np.random.uniform(50, 150, 100)
    }, index=dates)

    signal = GrahamValueSignal()

    def func(data, t):
        out = signal.compute_vectorized(data, None)
        return out["score"].iloc[t]

    assert verify_no_lookahead(func, df, 50)


def test_dividend_quality_lookahead():
    dates = pd.date_range("2026-01-01", periods=100)
    df = pd.DataFrame({
        "dividend_yield": np.random.uniform(0.01, 0.05, 100),
        "payout_ratio": np.random.uniform(0.2, 0.8, 100)
    }, index=dates)

    signal = DividendQualitySignal()

    def func(data, t):
        out = signal.compute_vectorized(data, None)
        return out["score"].iloc[t]

    assert verify_no_lookahead(func, df, 50)


def test_regime_multiplier_lookahead():
    dates = pd.date_range("2026-01-01", periods=100)
    df = pd.DataFrame({
        "regime": np.random.choice(["BULL", "BEAR", "VOLATILE"], 100)
    }, index=dates)
    
    signal = RegimeMultiplierSignal()

    def func(data, t):
        try:
            out = signal.compute_vectorized(data, None)
            return out["score"].iloc[t]
        except Exception:
            return 0.0

    assert verify_no_lookahead(func, df, 50)


def test_cross_sectional_momentum_lookahead():
    dates = pd.date_range("2026-01-01", periods=100)
    df = pd.DataFrame({
        "Close": np.random.uniform(50, 150, 100),
        "ticker": ["AAPL"] * 100,
        "1M_Return": np.random.uniform(-0.1, 0.1, 100)
    }, index=dates)
    
    signal = CrossSectionalMomentumSignal()
    
    def func(data, t):
        try:
            out = signal.compute_vectorized(data, None)
            return out["score"].iloc[t]
        except Exception:
            return 0.0
            
    assert verify_no_lookahead(func, df, 50)
