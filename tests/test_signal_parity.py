"""
InvestYo Quant Platform - Strategy Engine Parity Tests
======================================================
Verifies 100% score and action signal parity between the refactored
pluggable signal modules and the legacy monolithic strategy phases.
"""

from datetime import datetime
import pandas as pd
from strategy_engine import StrategyEngine
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO
from settings import settings


def test_strategy_engine_buy_range_and_options_overlays_parity(monkeypatch):
    # Pin SIGNAL_WEIGHTS to the declared defaults so this test's hardcoded
    # expected scores/actions are deterministic regardless of whatever the
    # real .env in this checkout has tuned them to (operator-customized
    # weights via the Strategy Matrix tab are a legitimate deployment state,
    # not a violation of this test's assumptions) -- same fix as
    # tests/test_quantitative_models.py's parity counterpart.
    monkeypatch.setattr(
        settings, "SIGNAL_WEIGHTS", type(settings)(_env_file=None).SIGNAL_WEIGHTS
    )
    engine = StrategyEngine()

    # Scenario A: JNJ in Bull Market / Risk-On -> STRONG BUY
    bar_equity = MarketBarDTO(datetime.now(), "JNJ", 155.00, 158.00, 154.50, 157.50, 4500000)
    fund_equity = FundamentalDataDTO(
        ticker="JNJ", company_name="Johnson & Johnson", sector="Healthcare",
        pe_ratio=16.5, pb_ratio=1.45, book_value=110.00, eps_trailing=9.50,
        dividend_yield=0.0310, dividend_growth_rate=0.065, payout_ratio=0.52,
    )
    macro_safe = MacroEconomicDTO(0.45, 2.50, 2.10, 4.0)

    result_equity = engine.evaluate_security(
        bar=bar_equity, fundamentals=fund_equity, macro=macro_safe,
        forecast_price=168.00, trend_strength=72.0, atr=2.50
    )

    assert result_equity["Action Signal"] == "STRONG BUY"
    assert result_equity["Score"] == 80
    assert result_equity["buyRange"] == "Buy Zone: $149.62 - $157.50"
    assert "OTM Covered Call (delta-20)" in result_equity["Option Strategy"]

    # Scenario B: REIT high-yielder (AGNC) in Buy Setup
    bar_reit = MarketBarDTO(datetime.now(), "AGNC", 9.80, 10.05, 9.75, 9.85, 2500000)
    fund_reit = FundamentalDataDTO(
        ticker="AGNC", company_name="AGNC Investment Corp", sector="Real Estate (mREIT)",
        pe_ratio=11.5, pb_ratio=0.88, book_value=11.20, eps_trailing=0.85,
        dividend_yield=0.145, dividend_growth_rate=-0.02, payout_ratio=0.92,
    )
    
    result_reit = engine.evaluate_security(
        bar=bar_reit, fundamentals=fund_reit, macro=macro_safe,
        forecast_price=10.50, trend_strength=60.0, atr=0.15
    )

    assert result_reit["Action Signal"] in ["BUY", "STRONG BUY"]
    assert "OTM Covered Call (delta-15)" in result_reit["Option Strategy"]

    # Scenario C: JNJ in Neutral HOLD setup (Price = 157.50, flat forecast, weakening trend)
    result_hold = engine.evaluate_security(
        bar=bar_equity, fundamentals=fund_equity, macro=macro_safe,
        forecast_price=157.50, trend_strength=40.0, atr=2.50
    )
    assert result_hold["Action Signal"] == "HOLD"
    assert result_hold["Score"] == 45
    assert result_hold["buyRange"] == "Hold Range: $152.50 - $162.50"

    # Scenario D: AGNC in Distressed RISK REDUCE setup (Hostile macro credit event)
    macro_hostile = MacroEconomicDTO(0.05, 6.50, 2.80, 4.0)
    result_reduce = engine.evaluate_security(
        bar=bar_reit, fundamentals=fund_reit, macro=macro_hostile,
        forecast_price=9.00, trend_strength=20.0, atr=0.15
    )
    assert result_reduce["Action Signal"] == "RISK REDUCE"
    assert result_reduce["Score"] == 10
    assert result_reduce["buyRange"] == "Trim @ $9.92 | Stop @ $9.70"
