"""
tests/test_advisory_double_fetch_caching.py
===========================================
Unit tests verifying that engine.advisory.evaluate() avoids redundant
database and provider lookups when bars and fundamentals are pre-cached
in context_extras.
"""
from __future__ import annotations

import unittest.mock as mock
from unittest.mock import MagicMock
import pandas as pd
import pytest

from engine.advisory import evaluate
from dto_models import FundamentalDataDTO, MacroEconomicDTO
from transactions_store import TransactionsStore
from tests.test_advisory import (
    _make_market_provider, _make_bars, _make_account_snapshot, _MOCK_TECH,
)


def test_advisory_evaluate_uses_cached_bars_and_fundamentals():
    """Verify that when bars and fundamentals are passed in context_extras,
    we reuse them and do not call the market provider or HistoricalStore."""
    ts = TransactionsStore(db_url="sqlite:///:memory:")
    snapshot = _make_account_snapshot()
    
    # Create dummy bars and fundamentals DTO
    cached_bars = _make_bars(300, 100.0) # 300 days of history
    cached_fund = FundamentalDataDTO(
        ticker="TEST",
        pe_ratio=15.0,
        pb_ratio=1.5,
        dividend_yield=0.02,
        book_value=10.0,
        eps_trailing=2.0,
        dividend_growth_rate=0.02,
        payout_ratio=0.3,
        sector="Technology",
        company_name="Test Company",
    )
    
    context_extras = {
        "bars": {"TEST": cached_bars},
        "fundamentals": {"TEST": cached_fund},
        "xsec_percentile_ranks": {},
        "multifactor_scores": {},
    }

    # Set up mock provider
    mock_market = MagicMock()
    # Mock quote lookup (quote is still required)
    mock_quote = MagicMock()
    mock_quote.price = 100.0
    mock_quote.is_stale = False
    mock_market.get_latest_quote.return_value = mock_quote

    with mock.patch("engine.advisory.ForecastingEngine") as MockFE, \
         mock.patch("engine.advisory.TechnicalOptionsEngine") as MockTOE, \
         mock.patch("engine.advisory.StrategyEngine") as MockSE, \
         mock.patch("engine.advisory._get_historical_store") as MockHS:

        fe_instance = MagicMock()
        fe_instance.generate_forecast.return_value = {"Forecast_30": 105.0}
        MockFE.return_value = fe_instance

        toe_instance = MagicMock()
        toe_instance.estimate_gjr_garch_volatility.return_value = 0.20
        toe_instance.estimate_gjr_garch_volatility_term_structure.return_value = {h: 0.20 for h in (1, 10, 30, 60, 90)}
        MockTOE.return_value = toe_instance

        se_instance = MagicMock()
        se_instance.evaluate_security.return_value = {
            "Action Signal": "HOLD", "Score": 50, "Kelly Target": 0.0,
        }
        MockSE.return_value = se_instance

        # Mock HistoricalStore to verify it is NOT queried
        hs_instance = MagicMock()
        MockHS.return_value = hs_instance

        rec = evaluate(
            symbol="TEST",
            position=None,
            market=mock_market,
            snapshot=snapshot,
            transactions_store=ts,
            context_extras=context_extras,
        )

        # 1. Market provider should NOT have get_intraday_bars or get_fundamentals called
        assert not mock_market.get_intraday_bars.called
        assert not mock_market.get_fundamentals.called

        # 2. HistoricalStore should NOT have get_bars or get_fundamentals_raw called
        assert not hs_instance.get_bars.called
        assert not hs_instance.get_fundamentals_raw.called

        # 3. Verify that the REAL ProcessingEngine calculated indicators from the sliced 252 bars
        # A live indicator calculation means rsi should be present and valid
        assert "rsi" in rec.key_indicators
        assert not pd.isna(rec.key_indicators["rsi"])

        # 4. Verify we returned a clean Recommendation
        assert rec.symbol == "TEST"
        assert rec.action == "HOLD"
