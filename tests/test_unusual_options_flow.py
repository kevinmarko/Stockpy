"""
tests/test_unusual_options_flow.py — Tests for Unusual Options Activity (UOA) Engine.
=====================================================================================

Tests the pure, dependency-light UOA anomaly detection and options flow analysis module
(pilots/unusual_options_flow.py).

Covers:
1. Anomaly detection: V/OI ratio (>= 3.0), volume (>= 500), notional (>= $100,000).
2. Multi-criteria filtering: rejection of contracts failing any individual gate.
3. 0 OI edge case: infinite ratio treated as new flow anomaly.
4. Trade aggressiveness categorization: ask_sweep, bid_sweep, mid_block.
5. Inferred directional sentiment for calls vs puts.
6. IV burst score calculation vs 30-day Historical Realized Volatility.
7. Support for varied chain input structures: DataFrames, Dicts, Objects, Lists.
8. Net flow sentiment score normalization and top strikes aggregation.
9. Graceful degradation & honesty invariants (never raises on empty/corrupt chains).
10. JSON Persistence roundtrip (save_uoa_records / load_uoa_records / get_symbol_flow_sentiment).
11. AST safety & dependency-light import allowlist guard.
"""

from __future__ import annotations

import ast
from datetime import datetime
import math
import pathlib
from typing import Any, Dict, List

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from dto_models import FundamentalDataDTO, MacroEconomicDTO, MarketBarDTO
from pilots.unusual_options_flow import (
    DEFAULT_MIN_NOTIONAL,
    DEFAULT_MIN_VOL_OI_RATIO,
    DEFAULT_MIN_VOLUME,
    IV_BURST_THRESHOLD,
    UOARecord,
    calculate_historical_volatility,
    calculate_iv_burst_score,
    calculate_net_flow_sentiment,
    categorize_trade_aggressiveness,
    get_symbol_flow_sentiment,
    load_uoa_records,
    save_uoa_records,
    scan_unusual_options_activity,
)
from signals.base import SignalContext, SignalOutput
from signals.options_flow_sentiment import OptionsFlowSentimentSignal
from tests.lookahead_check import verify_no_lookahead


# ---------------------------------------------------------------------------
# Test Fixtures & Sample Chains
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_option_chain_dict() -> Dict[str, Any]:
    """Provides a realistic multi-expiration option chain dictionary."""
    return {
        "2026-09-18": {
            "symbol": "AAPL",
            "calls": [
                # Anomaly 1: Bullish Ask Sweep (Vol 2000, OI 400 -> V/OI 5.0x, Price $5.00 >= Ask $5.00 -> Notional $1,000,000)
                {
                    "contractSymbol": "AAPL260918C00150000",
                    "strike": 150.0,
                    "lastPrice": 5.00,
                    "bid": 4.80,
                    "ask": 5.00,
                    "volume": 2000,
                    "openInterest": 400,
                    "impliedVolatility": 0.40,
                },
                # Normal contract (Vol 200 < 500, V/OI 0.2 < 3.0) -> Not an anomaly
                {
                    "contractSymbol": "AAPL260918C00155000",
                    "strike": 155.0,
                    "lastPrice": 3.00,
                    "bid": 2.90,
                    "ask": 3.10,
                    "volume": 200,
                    "openInterest": 1000,
                    "impliedVolatility": 0.28,
                },
                # Anomaly 2: Bearish Call Bid Sweep / Floor Hit (Vol 1500, OI 300 -> V/OI 5.0x, Price $4.00 <= Bid $4.00 -> Notional $600,000)
                {
                    "contractSymbol": "AAPL260918C00160000",
                    "strike": 160.0,
                    "lastPrice": 4.00,
                    "bid": 4.00,
                    "ask": 4.20,
                    "volume": 1500,
                    "openInterest": 300,
                    "impliedVolatility": 0.32,
                },
            ],
            "puts": [
                # Anomaly 3: Bearish Put Ask Sweep (Vol 1000, OI 200 -> V/OI 5.0x, Price $6.00 >= Ask $6.00 -> Notional $600,000)
                {
                    "contractSymbol": "AAPL260918P00140000",
                    "strike": 140.0,
                    "lastPrice": 6.00,
                    "bid": 5.80,
                    "ask": 6.00,
                    "volume": 1000,
                    "openInterest": 200,
                    "impliedVolatility": 0.45,
                },
                # Anomaly 4: Bullish Put Bid Sweep (Vol 800, OI 150 -> V/OI 5.33x, Price $3.00 <= Bid $3.00 -> Notional $240,000)
                {
                    "contractSymbol": "AAPL260918P00135000",
                    "strike": 135.0,
                    "lastPrice": 3.00,
                    "bid": 3.00,
                    "ask": 3.20,
                    "volume": 800,
                    "openInterest": 150,
                    "impliedVolatility": 0.25,
                },
            ],
        }
    }


# ---------------------------------------------------------------------------
# 1. Anomaly Detection Filtering Tests
# ---------------------------------------------------------------------------


class TestAnomalyDetectionFiltering:
    def test_detects_qualified_anomalies_and_sorts_by_notional(self, sample_option_chain_dict):
        results = scan_unusual_options_activity(
            chain_data=sample_option_chain_dict,
            spot_price=150.0,
            min_vol_oi_ratio=3.0,
            min_notional=100000.0,
            min_volume=500,
        )
        assert len(results) == 4
        # Should be sorted descending by premium notional
        notionals = [r.notional for r in results]
        assert notionals == sorted(notionals, reverse=True)
        assert results[0].notional == 1000000.0
        assert results[0].contract_symbol == "AAPL260918C00150000"
        assert results[0]["notional"] == 1000000.0

    def test_filter_fails_when_volume_below_threshold(self):
        chain = {
            "calls": [
                {
                    "contractSymbol": "TEST_C",
                    "strike": 100.0,
                    "lastPrice": 10.0,
                    "bid": 9.9,
                    "ask": 10.0,
                    "volume": 400,  # Below default min_volume 500
                    "openInterest": 50,  # V/OI = 8.0x (passes ratio)
                }
            ]
        }
        res = scan_unusual_options_activity(chain, spot_price=100.0)
        assert len(res) == 0

    def test_filter_fails_when_ratio_below_threshold(self):
        chain = {
            "calls": [
                {
                    "contractSymbol": "TEST_C",
                    "strike": 100.0,
                    "lastPrice": 10.0,
                    "bid": 9.9,
                    "ask": 10.0,
                    "volume": 1000,  # Passes volume >= 500
                    "openInterest": 500,  # V/OI = 2.0x (fails ratio < 3.0)
                }
            ]
        }
        res = scan_unusual_options_activity(chain, spot_price=100.0)
        assert len(res) == 0

    def test_filter_fails_when_notional_below_threshold(self):
        chain = {
            "calls": [
                {
                    "contractSymbol": "TEST_C",
                    "strike": 100.0,
                    "lastPrice": 0.50,  # Cheap contract
                    "bid": 0.45,
                    "ask": 0.50,
                    "volume": 1000,  # Passes volume >= 500
                    "openInterest": 100,  # V/OI = 10.0x (passes ratio)
                    # Notional = 1000 * 0.50 * 100 = $50,000 (fails notional < $100,000)
                }
            ]
        }
        res = scan_unusual_options_activity(chain, spot_price=100.0)
        assert len(res) == 0

    def test_zero_open_interest_treated_as_infinite_ratio_anomaly(self):
        """0 Open Interest with heavy volume represents newly initiated institutional position."""
        chain = {
            "calls": [
                {
                    "contractSymbol": "NEW_C",
                    "strike": 100.0,
                    "lastPrice": 5.0,
                    "bid": 4.9,
                    "ask": 5.0,
                    "volume": 800,
                    "openInterest": 0,  # Zero OI
                }
            ]
        }
        res = scan_unusual_options_activity(chain, spot_price=100.0)
        assert len(res) == 1
        assert res[0].open_interest == 0
        assert res[0].vol_oi_ratio >= 999.0
        assert res[0].notional == 400000.0


# ---------------------------------------------------------------------------
# 2. Sweep vs Block & Directional Sentiment Categorization Tests
# ---------------------------------------------------------------------------


class TestSweepCategorization:
    def test_call_ask_sweep_is_bullish(self):
        aggressiveness, sentiment = categorize_trade_aggressiveness(
            trade_price=5.10,
            bid=4.90,
            ask=5.00,
            option_type="call",
        )
        assert aggressiveness == "ask_sweep"
        assert sentiment == "BULLISH"

    def test_call_bid_sweep_is_bearish(self):
        aggressiveness, sentiment = categorize_trade_aggressiveness(
            trade_price=4.90,
            bid=4.90,
            ask=5.10,
            option_type="call",
        )
        assert aggressiveness == "bid_sweep"
        assert sentiment == "BEARISH"

    def test_put_ask_sweep_is_bearish(self):
        aggressiveness, sentiment = categorize_trade_aggressiveness(
            trade_price=3.50,
            bid=3.40,
            ask=3.50,
            option_type="put",
        )
        assert aggressiveness == "ask_sweep"
        assert sentiment == "BEARISH"

    def test_put_bid_sweep_is_bullish(self):
        aggressiveness, sentiment = categorize_trade_aggressiveness(
            trade_price=3.40,
            bid=3.40,
            ask=3.60,
            option_type="put",
        )
        assert aggressiveness == "bid_sweep"
        assert sentiment == "BULLISH"

    def test_mid_block_trade(self):
        aggressiveness, sentiment = categorize_trade_aggressiveness(
            trade_price=5.00,
            bid=4.90,
            ask=5.10,
            option_type="call",
        )
        assert aggressiveness == "mid_block"
        assert sentiment == "NEUTRAL"

    def test_multi_trade_aggressor_classification(self):
        chain_data = [
            # Call Ask Sweep: Bullish
            {
                "symbol": "NVDA",
                "option_type": "call",
                "strike": 130.0,
                "volume": 2000,
                "open_interest": 400,
                "bid": 5.00,
                "ask": 5.20,
                "price": 5.20,
            },
            # Put Ask Sweep: Bearish
            {
                "symbol": "NVDA",
                "option_type": "put",
                "strike": 110.0,
                "volume": 2000,
                "open_interest": 400,
                "bid": 3.00,
                "ask": 3.20,
                "price": 3.20,
            },
            # Call Bid Sweep: Bearish
            {
                "symbol": "NVDA",
                "option_type": "call",
                "strike": 140.0,
                "volume": 2000,
                "open_interest": 400,
                "bid": 2.00,
                "ask": 2.10,
                "price": 2.00,
            },
            # Put Bid Sweep: Bullish
            {
                "symbol": "NVDA",
                "option_type": "put",
                "strike": 100.0,
                "volume": 2000,
                "open_interest": 400,
                "bid": 1.50,
                "ask": 1.60,
                "price": 1.50,
            },
            # Mid-Market Block: Neutral
            {
                "symbol": "NVDA",
                "option_type": "call",
                "strike": 125.0,
                "volume": 2000,
                "open_interest": 400,
                "bid": 6.00,
                "ask": 6.40,
                "price": 6.20,
            },
        ]
        records = scan_unusual_options_activity(chain_data, min_vol_oi_ratio=3.0, min_notional=100000.0)
        assert len(records) == 5
        by_strike = {r.strike: r for r in records}
        assert by_strike[130.0].trade_type == "ask_sweep" and by_strike[130.0].sentiment == "BULLISH"
        assert by_strike[110.0].trade_type == "ask_sweep" and by_strike[110.0].sentiment == "BEARISH"
        assert by_strike[140.0].trade_type == "bid_sweep" and by_strike[140.0].sentiment == "BEARISH"
        assert by_strike[100.0].trade_type == "bid_sweep" and by_strike[100.0].sentiment == "BULLISH"
        assert by_strike[125.0].trade_type in ("block", "mid_block") and by_strike[125.0].sentiment == "NEUTRAL"


# ---------------------------------------------------------------------------
# 3. IV Burst & Volatility Expansion Anomaly Tests
# ---------------------------------------------------------------------------


class TestIVBurstScore:
    def test_iv_burst_detected_when_iv_exceeds_hv30_by_25_percent(self):
        burst_score, detected = calculate_iv_burst_score(iv=0.45, hv_30=0.30)
        assert burst_score == 1.50
        assert detected is True

    def test_iv_burst_not_detected_when_iv_normal(self):
        burst_score, detected = calculate_iv_burst_score(iv=0.32, hv_30=0.30)
        assert burst_score == 1.0667
        assert detected is False

    def test_iv_burst_handles_missing_data_gracefully(self):
        burst_score, detected = calculate_iv_burst_score(iv=None, hv_30=0.30)
        assert burst_score is None
        assert detected is False

        burst_score, detected = calculate_iv_burst_score(iv=0.45, hv_30=None)
        assert burst_score is None
        assert detected is False

    def test_iv_expansion_flag(self):
        chain_data = [
            # IV = 0.60 > 1.25 * 0.40 (0.50) -> iv_burst_detected / iv_expansion_flag = True
            {
                "symbol": "TSLA",
                "strike": 220.0,
                "volume": 1000,
                "open_interest": 200,
                "bid": 5.0,
                "ask": 5.2,
                "price": 5.2,
                "implied_volatility": 0.60,
                "historical_volatility": 0.40,
                "hv30": 0.40,
            }
        ]
        records = scan_unusual_options_activity(chain_data, historical_volatility=0.40)
        assert len(records) == 1
        r0 = records[0]
        flag = getattr(r0, "iv_burst_detected", getattr(r0, "iv_expansion_flag", False))
        if isinstance(r0, dict):
            flag = r0.get("iv_burst_detected") or r0.get("iv_expansion_flag")
        assert bool(flag) is True
        assert records[0].iv_burst_detected is True
        assert records[0].iv_burst_score == 1.50

    def test_historical_realized_volatility_calculation(self):
        np.random.seed(42)
        returns = np.random.normal(0, 0.015, 35)
        prices = [100.0]
        for r in returns:
            prices.append(prices[-1] * math.exp(r))

        hv = calculate_historical_volatility(prices, window=30)
        assert hv is not None
        assert 0.15 <= hv <= 0.35

    def test_scan_with_historical_prices_computes_iv_burst(self, sample_option_chain_dict):
        prices = [100.0 + (i * 0.05) for i in range(40)]
        results = scan_unusual_options_activity(
            chain_data=sample_option_chain_dict,
            spot_price=150.0,
            historical_prices=prices,
        )
        assert len(results) > 0
        for r in results:
            assert r.hv_30 is not None
            if r.iv and r.iv > r.hv_30 * 1.25:
                assert r.iv_burst_detected is True
                assert r.iv_burst_score is not None


# ---------------------------------------------------------------------------
# 4. Chain Input Container Flexibility Tests
# ---------------------------------------------------------------------------


class TestChainDataStructures:
    def test_pandas_dataframe_input(self):
        df = pd.DataFrame([
            {
                "contractSymbol": "NVDA260918C00120000",
                "ticker": "NVDA",
                "expiration": "2026-09-18",
                "strike": 120.0,
                "type": "call",
                "lastPrice": 8.00,
                "bid": 7.90,
                "ask": 8.00,
                "volume": 3000,
                "openInterest": 500,
                "impliedVolatility": 0.55,
            }
        ])
        results = scan_unusual_options_activity(df, spot_price=120.0)
        assert len(results) == 1
        assert results[0].symbol == "NVDA"
        assert results[0].notional == 2400000.0
        assert results[0].aggressiveness == "ask_sweep"
        assert results[0].sentiment == "BULLISH"

    def test_object_with_calls_and_puts_attributes(self):
        mock_chain = MagicMock()
        mock_chain.symbol = "MSFT"
        mock_chain.expiration = "2026-09-18"
        mock_chain.calls = pd.DataFrame([
            {
                "contractSymbol": "MSFT260918C00400000",
                "strike": 400.0,
                "lastPrice": 12.0,
                "bid": 11.8,
                "ask": 12.0,
                "volume": 1200,
                "openInterest": 200,
                "impliedVolatility": 0.28,
            }
        ])
        mock_chain.puts = pd.DataFrame()

        results = scan_unusual_options_activity(mock_chain, spot_price=400.0)
        assert len(results) == 1
        assert results[0].symbol == "MSFT"
        assert results[0].notional == 1440000.0

    def test_list_of_contract_dicts(self):
        contracts = [
            {
                "symbol": "TSLA",
                "contract_symbol": "TSLA260918P00200000",
                "expiration": "2026-09-18",
                "strike": 200.0,
                "option_type": "put",
                "price": 10.0,
                "bid": 9.9,
                "ask": 10.0,
                "volume": 2500,
                "open_interest": 400,
                "iv": 0.65,
            }
        ]
        results = scan_unusual_options_activity(contracts, spot_price=200.0)
        assert len(results) == 1
        assert results[0].symbol == "TSLA"
        assert results[0].sentiment == "BEARISH"
        assert results[0].aggressiveness == "ask_sweep"

    def test_empty_and_none_chain_data_never_raises(self):
        assert scan_unusual_options_activity(None) == []
        assert scan_unusual_options_activity({}) == []
        assert scan_unusual_options_activity([]) == []
        assert scan_unusual_options_activity(pd.DataFrame()) == []
        assert scan_unusual_options_activity([{"invalid": 123}]) == []


# ---------------------------------------------------------------------------
# 5. Net Flow Sentiment Aggregator Tests
# ---------------------------------------------------------------------------


class TestNetFlowSentiment:
    def test_strongly_bullish_net_flow(self):
        records = [
            UOARecord(
                symbol="MSFT",
                option_type="call",
                strike=420.0,
                volume=1000,
                price=5.0,
                trade_price=5.0,
                bid=4.9,
                ask=5.0,
                notional=500_000.0,
                trade_type="ask_sweep",
                aggressiveness="ask_sweep",
                sentiment="BULLISH",
            ),
            UOARecord(
                symbol="MSFT",
                option_type="put",
                strike=400.0,
                volume=500,
                price=4.0,
                trade_price=4.0,
                bid=4.0,
                ask=4.1,
                notional=200_000.0,
                trade_type="bid_sweep",
                aggressiveness="bid_sweep",
                sentiment="BULLISH",
            ),
            UOARecord(
                symbol="MSFT",
                option_type="put",
                strike=390.0,
                volume=200,
                price=5.0,
                trade_price=5.0,
                bid=4.9,
                ask=5.0,
                notional=100_000.0,
                trade_type="ask_sweep",
                aggressiveness="ask_sweep",
                sentiment="BEARISH",
            ),
        ]
        res = calculate_net_flow_sentiment("MSFT", records)
        assert res["symbol"] == "MSFT"
        # (700k - 100k) / 800k = 0.75
        assert abs(res["sentiment_score"] - 0.75) < 1e-4
        assert res["sentiment_label"] == "VERY_BULLISH"
        assert res["bullish_notional"] == 700000.0
        assert res["bearish_notional"] == 100000.0
        assert res["call_volume"] == 1000
        assert res["put_volume"] == 700
        assert len(res["top_active_strikes"]) == 3

    def test_strongly_bearish_net_flow(self):
        records = [
            UOARecord(
                symbol="AMZN",
                option_type="put",
                strike=180.0,
                volume=2000,
                price=4.0,
                trade_price=4.0,
                bid=3.9,
                ask=4.0,
                notional=800_000.0,
                trade_type="ask_sweep",
                aggressiveness="ask_sweep",
                sentiment="BEARISH",
            ),
            UOARecord(
                symbol="AMZN",
                option_type="call",
                strike=190.0,
                volume=1000,
                price=2.0,
                trade_price=2.0,
                bid=2.0,
                ask=2.1,
                notional=200_000.0,
                trade_type="bid_sweep",
                aggressiveness="bid_sweep",
                sentiment="BEARISH",
            ),
        ]
        res = calculate_net_flow_sentiment("AMZN", records)
        assert res["sentiment_score"] == -1.0
        assert res["sentiment_label"] == "VERY_BEARISH"
        assert res["bearish_notional"] == 1000000.0
        assert res["bullish_notional"] == 0.0

    def test_balanced_flow_sentiment(self):
        records = [
            {
                "symbol": "GOOGL",
                "notional": 500000.0,
                "sentiment": "BULLISH",
                "option_type": "call",
                "volume": 1000,
                "strike": 170.0,
            },
            {
                "symbol": "GOOGL",
                "notional": 500000.0,
                "sentiment": "BEARISH",
                "option_type": "put",
                "volume": 1000,
                "strike": 160.0,
            },
        ]
        res = calculate_net_flow_sentiment("GOOGL", records)
        assert res["sentiment_score"] == 0.0
        assert res["sentiment_label"] == "NEUTRAL"

    def test_empty_records_sentiment_never_raises(self):
        res = calculate_net_flow_sentiment("AAPL", [])
        assert res["sentiment_score"] == 0.0
        assert res["sentiment_label"] == "NEUTRAL"
        assert res["total_notional"] == 0.0
        assert res["record_count"] == 0


# ---------------------------------------------------------------------------
# 6. Persistence Roundtrip Tests
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_save_and_load_uoa_records_roundtrip(self, tmp_path):
        test_file = tmp_path / "test_uoa.json"
        records = [
            UOARecord(
                symbol="NFLX",
                contract_symbol="NFLX260821C00700000",
                option_type="call",
                strike=700.0,
                volume=1500,
                price=10.0,
                trade_price=10.0,
                bid=9.8,
                ask=10.0,
                notional=1_500_000.0,
                trade_type="ask_sweep",
                aggressiveness="ask_sweep",
                sentiment="BULLISH",
            )
        ]
        saved_path = save_uoa_records(records, test_file)
        assert saved_path == str(test_file)

        loaded = load_uoa_records(test_file)
        assert len(loaded) == 1
        assert loaded[0].symbol == "NFLX"
        assert loaded[0].strike == 700.0
        assert loaded[0].notional == 1500000.0
        assert loaded[0].sentiment == "BULLISH"

        sentiment_res = get_symbol_flow_sentiment("NFLX", test_file)
        assert sentiment_res["symbol"] == "NFLX"
        assert sentiment_res["sentiment_score"] == 1.0


# ---------------------------------------------------------------------------
# 7. AST Safety & Dependency-Light Allowlist Guard Tests
# ---------------------------------------------------------------------------


class TestASTSafety:
    def test_unusual_options_flow_stays_dependency_light_and_ast_safe(self):
        """
        Guards that pilots/unusual_options_flow.py never imports heavy orchestrators,
        engines, or circular dependencies.
        """
        module_path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "pilots"
            / "unusual_options_flow.py"
        )
        tree = ast.parse(module_path.read_text(encoding="utf-8"))

        imported_roots = set()
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_roots.add(alias.name.split(".")[0])
                    imported_modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
                imported_modules.add(node.module)

        forbidden = {
            "processing_engine",
            "strategy_engine",
            "forecasting_engine",
            "macro_engine",
            "technical_options_engine",
            "universe_engine",
            "simulation_engine",
            "main_orchestrator",
            "desktop",
            "gui",
        }

        overlap = imported_roots & forbidden
        assert not overlap, f"pilots/unusual_options_flow.py must not import {overlap}"

        # Assert all imports are within clean allowlist. "data" is permitted ONLY as the
        # lazy, function-scoped `data.market_data` provider import (the same live-chain
        # fetch pattern already used by pilots/options_gex.py, pilots/vol_mispricing.py,
        # and pilots/har_volatility.py) — never a heavier `data.*` submodule.
        allowed_roots = {
            "__future__",
            "dataclasses",
            "datetime",
            "json",
            "logging",
            "math",
            "pathlib",
            "re",
            "typing",
            "numpy",
            "pandas",
            "settings",
            "data",
            "pilots",
        }
        unrecognized = imported_roots - allowed_roots
        assert not unrecognized, f"Unrecognized import roots in unusual_options_flow.py: {unrecognized}"

        data_modules = {m for m in imported_modules if m == "data" or m.startswith("data.")}
        assert data_modules <= {"data.market_data"}, (
            f"pilots/unusual_options_flow.py may only import data.market_data, found: {data_modules}"
        )

        pilots_modules = {m for m in imported_modules if m == "pilots" or m.startswith("pilots.")}
        assert pilots_modules <= {"pilots.options_alerts"}, (
            f"pilots/unusual_options_flow.py may only import pilots.options_alerts, found: {pilots_modules}"
        )


# ---------------------------------------------------------------------------
# 8. OptionsFlowSentimentSignal Tests
# ---------------------------------------------------------------------------


def _dummy_signal_context(sentiment_dict: Dict[str, float] | None = None) -> SignalContext:
    bar = MarketBarDTO(
        date=datetime.now(),
        ticker="AAPL",
        open_price=200.0,
        high_price=205.0,
        low_price=198.0,
        close_price=202.0,
        volume=5_000_000,
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
        yield_curve_10y_2y=0.4,
        high_yield_oas=3.0,
        inflation_rate=0.025,
        vix_value=15.0,
        sahm_rule_indicator=0.0,
    )
    ctx = SignalContext(bar=bar, fundamentals=fund, macro=macro)
    if sentiment_dict:
        ctx.options_flow_sentiment = dict(sentiment_dict)
    return ctx


class TestOptionsFlowSentimentSignal:
    def test_signal_score_from_row_column(self):
        signal = OptionsFlowSentimentSignal()
        ctx = _dummy_signal_context()

        # Bullish row
        row_bull = pd.Series({"Symbol": "AAPL", "Options_Flow_Sentiment": 0.80})
        out_bull = signal.compute(row_bull, ctx)
        assert np.isclose(out_bull.score, 0.80)
        assert out_bull.confidence >= 0.80
        assert "bullish (+0.80)" in out_bull.explanation

        # Bearish row
        row_bear = pd.Series({"Symbol": "AAPL", "Options_Flow_Sentiment": -0.65})
        out_bear = signal.compute(row_bear, ctx)
        assert np.isclose(out_bear.score, -0.65)
        assert out_bear.confidence >= 0.80
        assert "bearish (-0.65)" in out_bear.explanation

        # Neutral row
        row_neut = pd.Series({"Symbol": "AAPL", "Options_Flow_Sentiment": 0.05})
        out_neut = signal.compute(row_neut, ctx)
        assert np.isclose(out_neut.score, 0.05)
        assert "neutral (0.05)" in out_neut.explanation

    def test_signal_score_from_context(self):
        signal = OptionsFlowSentimentSignal()
        ctx = _dummy_signal_context({"MSFT": 0.72, "NVDA": -0.45})

        row_msft = pd.Series({"Symbol": "MSFT"})
        out_msft = signal.compute(row_msft, ctx)
        assert np.isclose(out_msft.score, 0.72)

        row_nvda = pd.Series({"Symbol": "NVDA"})
        out_nvda = signal.compute(row_nvda, ctx)
        assert np.isclose(out_nvda.score, -0.45)

    def test_signal_missing_data_degrades_neutrally(self):
        signal = OptionsFlowSentimentSignal()
        ctx = _dummy_signal_context()

        # Missing symbol
        row_empty = pd.Series({"Symbol": "UNKNOWN"})
        out_empty = signal.compute(row_empty, ctx)
        assert out_empty.score == 0.0
        assert out_empty.confidence == 0.0
        assert "neutral/no flow data" in out_empty.explanation

        # NaN row value
        row_nan = pd.Series({"Symbol": "AAPL", "Options_Flow_Sentiment": float("nan")})
        out_nan = signal.compute(row_nan, ctx)
        assert out_nan.score == 0.0
        assert out_nan.confidence == 0.0

    def test_vectorized_parity_with_scalar(self):
        signal = OptionsFlowSentimentSignal()
        ctx = _dummy_signal_context({"AAPL": 0.60, "MSFT": -0.50})

        df = pd.DataFrame({
            "Symbol": ["AAPL", "MSFT", "GOOGL", "AMZN"],
            "Options_Flow_Sentiment": [0.60, -0.50, float("nan"), 0.10],
        })

        vec_out = signal.compute_vectorized(df, ctx)

        for i, row in df.iterrows():
            scalar_out = signal.compute(row, ctx)
            assert np.isclose(vec_out["score"].iloc[i], scalar_out.score, atol=1e-5)
            assert np.isclose(vec_out["confidence"].iloc[i], scalar_out.confidence, atol=1e-5)
            assert vec_out["explanation"].iloc[i] == scalar_out.explanation
            assert vec_out["meta_label_proba"].iloc[i] == scalar_out.meta_label_proba

    def test_no_lookahead_bias(self):
        """Verify that OptionsFlowSentimentSignal introduces zero lookahead bias."""
        df = pd.DataFrame(
            {
                "Symbol": ["AAPL"] * 100,
                "Options_Flow_Sentiment": np.sin(np.linspace(0, 10, 100)),
            },
            index=pd.date_range("2026-01-01", periods=100),
        )
        signal = OptionsFlowSentimentSignal()

        def func(data, t):
            out = signal.compute_vectorized(data, None)
            return out["score"].iloc[t]

        assert verify_no_lookahead(func, df, 50)
