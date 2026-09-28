"""tests/test_options_flow_sentiment.py — Tests for Options Order Flow Sentiment & Regime Bridge.
=============================================================================================

Unit tests verifying:
1. Feature scoring: fast order flow velocity (5d ROC), accumulation/distribution (20d ROC vs 200d trend).
2. News & earnings blackout window filtering (blackout_window_days=3, neutralizing directional bets).
3. Flow regime classifications (ACCUMULATION, DISTRIBUTION, HIGH_VELOCITY_BULLISH, HIGH_VELOCITY_BEARISH, NEUTRAL, BLACKOUT).
4. Position recommendations (BUY, SELL, NEUTRAL).
5. Zero lookahead bias (mathematically verified with perturbation checks and 1-day lagged signals).
6. Direct options flow records (UOARecords, dicts, Series) integration.
7. Signal module (OptionsFlowSentimentSignal) scalar & vectorized parity, blackout suppression, and honest degradation.
"""

from datetime import date, datetime, timedelta
from typing import Dict, Optional

import numpy as np
import pandas as pd
import pytest

from dto_models import FundamentalDataDTO, MacroEconomicDTO, MarketBarDTO
from pilots.unusual_options_flow import UOARecord
from signals.base import SignalContext, SignalOutput
from signals.options_flow_sentiment import (
    DEFAULT_BLACKOUT_WINDOW_DAYS,
    OptionsFlowSentimentSignal,
    calculate_accumulation_distribution,
    calculate_order_flow_velocity,
    compute_flow_regime,
    is_blackout_active,
)
from tests.lookahead_check import verify_no_lookahead


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------


def _dummy_signal_context(sentiment_dict: Optional[Dict[str, float]] = None) -> SignalContext:
    bar = MarketBarDTO(
        date=datetime.now(),
        ticker="AAPL",
        open_price=150.0,
        high_price=155.0,
        low_price=149.0,
        close_price=153.0,
        volume=1000000,
    )
    fund = FundamentalDataDTO(
        ticker="AAPL",
        pe_ratio=25.0,
        pb_ratio=10.0,
        dividend_yield=0.005,
        book_value=20.0,
        eps_trailing=8.0,
        dividend_growth_rate=0.05,
        payout_ratio=0.15,
        sector="Technology",
        company_name="Apple Inc.",
    )
    macro = MacroEconomicDTO(
        yield_curve_10y_2y=1.0,
        high_yield_oas=3.0,
        inflation_rate=2.5,
        vix_value=15.0,
        sahm_rule_indicator=0.0,
    )
    ctx = SignalContext(bar=bar, fundamentals=fund, macro=macro)
    if sentiment_dict:
        ctx.options_flow_sentiment = dict(sentiment_dict)
    return ctx


@pytest.fixture
def sample_dates() -> pd.DatetimeIndex:
    return pd.date_range("2026-01-01", periods=250, freq="B")


@pytest.fixture
def sample_closes(sample_dates: pd.DatetimeIndex) -> pd.Series:
    """Generates a steady upward trending price series with noise."""
    np.random.seed(42)
    noise = np.random.normal(0, 0.5, len(sample_dates))
    base = np.linspace(100, 180, len(sample_dates))
    prices = base + noise
    return pd.Series(prices, index=sample_dates)


# ---------------------------------------------------------------------------
# 1. Feature Scoring & Math Invariant Tests
# ---------------------------------------------------------------------------


class TestFeatureScoringMath:
    def test_order_flow_velocity_5d_roc(self):
        prices = pd.Series([100.0, 102.0, 104.0, 106.0, 108.0, 110.0])
        roc5 = calculate_order_flow_velocity(prices, window=5)
        # At index 5: (110 - 100) / 100 = 0.10
        assert np.isclose(roc5.iloc[5], 0.10)
        assert np.isclose(roc5.iloc[0], 0.0)  # NaNs filled with 0.0

    def test_accumulation_distribution_20d_and_sma200(self, sample_closes: pd.Series):
        roc20, sma200, trend_up = calculate_accumulation_distribution(sample_closes, roc_window=20, sma_window=200)
        assert len(roc20) == len(sample_closes)
        assert len(sma200) == len(sample_closes)
        assert len(trend_up) == len(sample_closes)

        # In an upward trend (100 -> 180), late points should have positive 20d ROC and price > SMA200
        assert roc20.iloc[-1] > 0.0
        assert sample_closes.iloc[-1] > sma200.iloc[-1]
        assert bool(trend_up.iloc[-1]) is True

    def test_flow_score_bounds_and_invariants(self, sample_closes: pd.Series):
        df_regime = compute_flow_regime(sample_closes)
        assert "flow_score" in df_regime.columns
        assert "regime" in df_regime.columns
        assert "blackout_active" in df_regime.columns
        assert "position_recommendation" in df_regime.columns

        # Flow score strictly bounded in [-1.0, 1.0]
        assert (df_regime["flow_score"] >= -1.0).all()
        assert (df_regime["flow_score"] <= 1.0).all()
        assert not df_regime["flow_score"].isna().any()


# ---------------------------------------------------------------------------
# 2. News & Earnings Blackout Window Filtering Tests
# ---------------------------------------------------------------------------


class TestNewsBlackoutFiltering:
    def test_blackout_window_exact_date_range(self):
        dates = pd.date_range("2026-03-01", periods=20, freq="D")
        event_date = "2026-03-10"

        # blackout_window_days=3 -> active from 2026-03-07 to 2026-03-13 inclusive (+/- 3 days)
        blackout = is_blackout_active(dates, news_events=[event_date], blackout_window_days=3)

        assert len(blackout) == 20
        # Check inside window
        assert bool(blackout.loc["2026-03-07"]) is True
        assert bool(blackout.loc["2026-03-10"]) is True
        assert bool(blackout.loc["2026-03-13"]) is True

        # Check outside window
        assert bool(blackout.loc["2026-03-06"]) is False
        assert bool(blackout.loc["2026-03-14"]) is False

    def test_blackout_neutralizes_directional_flow_and_bets(self):
        dates = pd.date_range("2026-03-01", periods=15, freq="D")
        # Massive price spike during blackout window
        prices = pd.Series(
            [100, 100, 100, 100, 100, 100, 100, 150, 160, 170, 100, 100, 100, 100, 100],
            index=dates,
            dtype=float,
        )
        event_date = "2026-03-08"

        df_regime = compute_flow_regime(
            prices,
            news_events=[event_date],
            blackout_window_days=3,
        )

        # On 2026-03-08 (earnings day), blackout must be active, flow_score zeroed, and position neutral
        row_earnings = df_regime.loc["2026-03-08"]
        assert bool(row_earnings["blackout_active"]) is True
        assert row_earnings["regime"] == "BLACKOUT"
        assert row_earnings["flow_score"] == 0.0
        assert row_earnings["position_recommendation"] == "NEUTRAL"

    def test_is_blackout_active_multiple_formats(self):
        dates = pd.date_range("2026-01-01", periods=10, freq="D")

        # String format
        res_str = is_blackout_active(dates, news_events=["2026-01-05"], blackout_window_days=1)
        assert bool(res_str.loc["2026-01-05"]) is True
        assert bool(res_str.loc["2026-01-04"]) is True
        assert bool(res_str.loc["2026-01-07"]) is False

        # Datetime / Timestamp objects
        res_ts = is_blackout_active(dates, news_events=[pd.Timestamp("2026-01-05")], blackout_window_days=1)
        assert bool(res_ts.loc["2026-01-05"]) is True

        # Dict format
        res_dict = is_blackout_active(dates, news_events=[{"date": "2026-01-05", "event": "earnings"}], blackout_window_days=1)
        assert bool(res_dict.loc["2026-01-05"]) is True

    def test_blackout_with_empty_or_none_events(self, sample_closes: pd.Series):
        res_none = is_blackout_active(sample_closes.index, news_events=None)
        assert not res_none.any()

        res_empty = is_blackout_active(sample_closes.index, news_events=[])
        assert not res_empty.any()


# ---------------------------------------------------------------------------
# 3. Flow Regime Classification Tests
# ---------------------------------------------------------------------------


class TestFlowRegimeClassification:
    def test_regime_high_velocity_bullish(self):
        dates = pd.date_range("2026-01-01", periods=30, freq="D")
        # Steady base prices then sudden 5-day surge (> 1.5% ROC5)
        p = [100.0] * 20 + [101.0, 103.0, 106.0, 110.0, 115.0] + [116.0] * 5
        prices = pd.Series(p, index=dates)

        df = compute_flow_regime(prices)
        # At index 24 (day 25, price 115), velocity ROC5 is (115-100)/100 = 15%
        assert df["regime"].iloc[24] == "HIGH_VELOCITY_BULLISH"
        assert df["position_recommendation"].iloc[24] == "BUY"
        assert df["flow_score"].iloc[24] > 0.30

    def test_regime_high_velocity_bearish(self):
        dates = pd.date_range("2026-01-01", periods=30, freq="D")
        # Steady base prices then sudden 5-day plunge (< -1.5% ROC5)
        p = [100.0] * 20 + [99.0, 97.0, 94.0, 90.0, 85.0] + [84.0] * 5
        prices = pd.Series(p, index=dates)

        df = compute_flow_regime(prices)
        # At index 24 (day 25, price 85), velocity ROC5 is (85-100)/100 = -15%
        assert df["regime"].iloc[24] == "HIGH_VELOCITY_BEARISH"
        assert df["position_recommendation"].iloc[24] == "SELL"
        assert df["flow_score"].iloc[24] < -0.30

    def test_regime_accumulation(self, sample_closes: pd.Series):
        # Gradual upward trend across 250 bars
        df = compute_flow_regime(sample_closes)
        # Final point should be ACCUMULATION or HIGH_VELOCITY_BULLISH
        assert df["regime"].iloc[-1] in ("ACCUMULATION", "HIGH_VELOCITY_BULLISH")
        assert df["position_recommendation"].iloc[-1] == "BUY"
        assert df["flow_score"].iloc[-1] > 0.0

    def test_regime_distribution(self, sample_dates: pd.DatetimeIndex):
        # Gradual downward trend across 250 bars (180 -> 100)
        prices = pd.Series(np.linspace(180, 100, len(sample_dates)), index=sample_dates)
        df = compute_flow_regime(prices)
        assert df["regime"].iloc[-1] in ("DISTRIBUTION", "HIGH_VELOCITY_BEARISH")
        assert df["position_recommendation"].iloc[-1] == "SELL"
        assert df["flow_score"].iloc[-1] < 0.0

    def test_regime_neutral(self, sample_dates: pd.DatetimeIndex):
        # Perfectly flat prices
        prices = pd.Series(100.0, index=sample_dates)
        df = compute_flow_regime(prices)
        assert (df["regime"] == "NEUTRAL").all()
        assert (df["position_recommendation"] == "NEUTRAL").all()
        assert (df["flow_score"] == 0.0).all()


# ---------------------------------------------------------------------------
# 4. Zero Lookahead Bias Verification
# ---------------------------------------------------------------------------


class TestZeroLookaheadBias:
    def test_compute_flow_regime_no_lookahead(self, sample_closes: pd.Series):
        """Mathematically prove compute_flow_regime has zero lookahead bias."""
        def func_score(data, t):
            out = compute_flow_regime(data)
            return out["flow_score"].iloc[t]

        def func_pos(data, t):
            out = compute_flow_regime(data)
            return out["position_recommendation"].iloc[t]

        for t in [20, 50, 100, 180]:
            assert verify_no_lookahead(func_score, sample_closes, t)
            assert verify_no_lookahead(func_pos, sample_closes, t)

    def test_lagged_signals_shift_one_bar(self, sample_closes: pd.Series):
        """Verify that lag_signals=True shifts signals by exactly 1 bar."""
        df_standard = compute_flow_regime(sample_closes, lag_signals=False)
        df_lagged = compute_flow_regime(sample_closes, lag_signals=True)

        # Bar 0 in lagged should be default
        assert df_lagged["flow_score"].iloc[0] == 0.0
        assert df_lagged["regime"].iloc[0] == "NEUTRAL"
        assert df_lagged["position_recommendation"].iloc[0] == "NEUTRAL"

        # Bar t in lagged should equal bar t-1 in standard
        for t in range(1, 10):
            assert np.isclose(df_lagged["flow_score"].iloc[t], df_standard["flow_score"].iloc[t - 1])
            assert df_lagged["regime"].iloc[t] == df_standard["regime"].iloc[t - 1]
            assert df_lagged["position_recommendation"].iloc[t] == df_standard["position_recommendation"].iloc[t - 1]


# ---------------------------------------------------------------------------
# 5. Direct Options Flow Records Integration
# ---------------------------------------------------------------------------


class TestOptionsFlowRecordsIntegration:
    def test_uoa_records_list_integration(self):
        dates = pd.date_range("2026-03-01", periods=10, freq="D")
        prices = pd.Series([100.0] * 10, index=dates)

        # Create UOARecord for 2026-03-05
        rec_bullish = UOARecord(
            symbol="AAPL",
            contract_symbol="AAPL260305C00100000",
            expiration="2026-03-05",
            strike=100.0,
            option_type="call",
            trade_price=5.0,
            volume=5000,
            open_interest=500,
            aggressiveness="ask_sweep",
            sentiment="BULLISH",
            timestamp="2026-03-05T14:30:00Z",
        )

        df = compute_flow_regime(prices, options_flow_records=[rec_bullish])
        # On 2026-03-05, direct flow score is integrated and bullish
        assert df.loc["2026-03-05", "flow_score"] > 0.40
        assert df.loc["2026-03-05", "position_recommendation"] == "BUY"

    def test_dict_flow_scores_integration(self):
        dates = pd.date_range("2026-03-01", periods=5, freq="D")
        prices = pd.Series([100.0] * 5, index=dates)

        flow_map = {
            "2026-03-02": 0.85,
            "2026-03-04": -0.80,
        }

        df = compute_flow_regime(prices, options_flow_records=flow_map)
        assert df.loc["2026-03-02", "flow_score"] > 0.50
        assert df.loc["2026-03-02", "position_recommendation"] == "BUY"
        assert df.loc["2026-03-04", "flow_score"] < -0.50
        assert df.loc["2026-03-04", "position_recommendation"] == "SELL"


# ---------------------------------------------------------------------------
# 6. Signal Module (OptionsFlowSentimentSignal) Tests
# ---------------------------------------------------------------------------


class TestSignalModuleIntegration:
    def test_signal_module_blackout_neutralizes_scalar(self):
        signal = OptionsFlowSentimentSignal()
        ctx = _dummy_signal_context()

        # Regular row without blackout
        row_normal = pd.Series({"Symbol": "AAPL", "Options_Flow_Sentiment": 0.75, "Blackout_Active": False})
        out_normal = signal.compute(row_normal, ctx)
        assert out_normal.score == pytest.approx(0.75)
        assert out_normal.confidence >= 0.80

        # Row with blackout active
        row_blackout = pd.Series({"Symbol": "AAPL", "Options_Flow_Sentiment": 0.75, "Blackout_Active": True})
        out_blackout = signal.compute(row_blackout, ctx)
        assert out_blackout.score == 0.0
        assert out_blackout.confidence == 0.0
        assert "blackout active" in out_blackout.explanation

    def test_signal_module_blackout_neutralizes_vectorized(self):
        signal = OptionsFlowSentimentSignal()
        ctx = _dummy_signal_context()

        df = pd.DataFrame({
            "Symbol": ["AAPL", "MSFT", "NVDA"],
            "Options_Flow_Sentiment": [0.80, -0.60, 0.50],
            "Blackout_Active": [False, True, False],
        })

        out = signal.compute_vectorized(df, ctx)
        assert np.isclose(out["score"].iloc[0], 0.80)
        assert out["confidence"].iloc[0] >= 0.80

        # MSFT is in blackout -> score=0, confidence=0
        assert out["score"].iloc[1] == 0.0
        assert out["confidence"].iloc[1] == 0.0
        assert "blackout active" in out["explanation"].iloc[1]

        assert np.isclose(out["score"].iloc[2], 0.50)

    def test_signal_module_flow_velocity_proxy_fallback(self):
        signal = OptionsFlowSentimentSignal()
        ctx = _dummy_signal_context()

        # No direct Options_Flow_Sentiment column, but ROC_5 and ROC_20 are present
        df = pd.DataFrame({
            "Symbol": ["AAPL", "TSLA"],
            "ROC_5": [0.03, -0.03],
            "ROC_20": [0.06, -0.06],
        })

        out = signal.compute_vectorized(df, ctx)
        assert out["score"].iloc[0] > 0.50
        assert "bullish" in out["explanation"].iloc[0]
        assert out["score"].iloc[1] < -0.50
        assert "bearish" in out["explanation"].iloc[1]

    def test_signal_module_vectorized_scalar_parity(self):
        signal = OptionsFlowSentimentSignal()
        ctx = _dummy_signal_context()

        df = pd.DataFrame({
            "Symbol": ["AAPL", "MSFT", "GOOGL", "AMZN"],
            "Options_Flow_Sentiment": [0.65, -0.45, np.nan, 0.0],
            "Blackout_Active": [False, False, False, True],
        })

        vec = signal.compute_vectorized(df, ctx)
        for i, row in df.iterrows():
            scal = signal.compute(row, ctx)
            assert np.isclose(vec["score"].iloc[i], scal.score, atol=1e-5)
            assert np.isclose(vec["confidence"].iloc[i], scal.confidence, atol=1e-5)
            assert vec["explanation"].iloc[i] == scal.explanation


# ---------------------------------------------------------------------------
# 7. Edge Cases & Robustness
# ---------------------------------------------------------------------------


class TestEdgeCasesAndRobustness:
    def test_empty_dataframe_and_series(self):
        df_empty = compute_flow_regime(pd.Series(dtype=float))
        assert isinstance(df_empty, pd.DataFrame)
        assert len(df_empty) == 0
        assert list(df_empty.columns) == ["flow_score", "regime", "blackout_active", "position_recommendation"]

    def test_short_price_series(self):
        # 3 prices only
        prices = pd.Series([100.0, 102.0, 101.0])
        df = compute_flow_regime(prices)
        assert len(df) == 3
        assert not df["flow_score"].isna().any()

    def test_series_with_nans(self):
        prices = pd.Series([100.0, np.nan, 105.0, 104.0, np.nan, 110.0])
        df = compute_flow_regime(prices)
        assert len(df) == 6
        assert not df["flow_score"].isna().any()
