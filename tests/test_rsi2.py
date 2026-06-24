"""
InvestYo Quant Platform - RSI(2) Mean Reversion Signal Tests
==============================================================
Unit tests for signals/rsi2_mean_reversion.py: trend filter, entry scoring,
and the already-reverted guard.
"""

import pandas as pd
from datetime import datetime

from signals.rsi2_mean_reversion import RSI2MeanReversionSignal
from signals.base import SignalContext
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO


def _context(vix_value: float = 15.0) -> SignalContext:
    bar = MarketBarDTO(datetime.now(), "TEST", 100.0, 100.0, 100.0, 100.0, 1000)
    fundamentals = FundamentalDataDTO(
        ticker="TEST", company_name="Test Corp", sector="Technology",
        pe_ratio=15.0, pb_ratio=1.5, book_value=50.0, eps_trailing=5.0,
        dividend_yield=0.02, dividend_growth_rate=0.05, payout_ratio=0.30,
    )
    # Benign macro inputs -> RISK ON / NEUTRAL, not RECESSION/CREDIT EVENT.
    macro = MacroEconomicDTO(
        yield_curve_10y_2y=0.5, high_yield_oas=2.0, inflation_rate=2.0,
        nominal_10y=4.0, vix_value=vix_value,
    )
    return SignalContext(bar=bar, fundamentals=fundamentals, macro=macro)


def test_oversold_in_uptrend_scores_above_half():
    """Engineered oversold bar (RSI(2) deeply oversold) in an uptrend -> score > 0.5."""
    sig = RSI2MeanReversionSignal()
    context = _context()
    row = pd.Series({
        "Close": 100.0,
        "RSI_2": 3.0,       # deeply oversold (< 10 threshold)
        "SMA_5": 102.0,      # Close < SMA5 -> not yet reverted
        "SMA_200": 90.0,      # Close > SMA200 -> uptrend
        "sector": "Technology",
    })
    output = sig.compute(row, context)
    assert output.score > 0.5


def test_same_bar_in_downtrend_scores_zero():
    """Identical RSI(2) reading, but Close < SMA(200) (downtrend) -> score forced to 0."""
    sig = RSI2MeanReversionSignal()
    context = _context()
    row = pd.Series({
        "Close": 80.0,
        "RSI_2": 3.0,        # same oversold reading as the uptrend case
        "SMA_5": 82.0,
        "SMA_200": 90.0,      # Close < SMA200 -> downtrend
        "sector": "Technology",
    })
    output = sig.compute(row, context)
    assert output.score == 0.0


def test_not_oversold_scores_zero():
    """RSI(2) above the oversold threshold -> no entry conviction."""
    sig = RSI2MeanReversionSignal()
    context = _context()
    row = pd.Series({
        "Close": 100.0,
        "RSI_2": 45.0,        # not oversold
        "SMA_5": 99.0,
        "SMA_200": 90.0,
        "sector": "Technology",
    })
    output = sig.compute(row, context)
    assert output.score == 0.0


def test_already_reverted_guard_scores_zero():
    """Close already back above SMA(5): the bounce already happened, no fresh entry."""
    sig = RSI2MeanReversionSignal()
    context = _context()
    row = pd.Series({
        "Close": 100.0,
        "RSI_2": 3.0,         # still technically oversold...
        "SMA_5": 98.0,        # ...but Close > SMA5 already -> reverted
        "SMA_200": 90.0,
        "sector": "Technology",
    })
    output = sig.compute(row, context)
    assert output.score == 0.0


def test_required_features_declared():
    sig = RSI2MeanReversionSignal()
    assert sig.required_features == ["Close", "RSI_2", "SMA_5", "SMA_200"]


def test_score_bounded_in_zero_one_range():
    """Long-only signal: score must always stay within [0.0, 1.0]."""
    sig = RSI2MeanReversionSignal()
    context = _context()
    row = pd.Series({
        "Close": 100.0,
        "RSI_2": 0.0,          # extreme oversold edge case
        "SMA_5": 105.0,
        "SMA_200": 90.0,
        "sector": "Technology",
    })
    output = sig.compute(row, context)
    assert 0.0 <= output.score <= 1.0
