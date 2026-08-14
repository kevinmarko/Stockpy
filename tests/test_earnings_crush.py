"""
tests/test_earnings_crush.py
============================
Comprehensive test suite for pilots/earnings_crush.py (Workstream 1):
- Expected move mathematical precision and ATM straddle proxy rules
- Historical earnings gap calculations (|Open - PrevClose| / PrevClose)
- Sparse history fallback bounds and honesty flags
- Crush Edge Ratio (Implied / Realized) evaluation and candidate scanning
- Delta-neutral Iron Condor strike geometry and wing multiplier rules
- AST import boundary safety
"""

import ast
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from pilots.earnings_crush import (
    FALLBACK_MAX_MOVE_PCT,
    FALLBACK_MEAN_MOVE_PCT,
    FALLBACK_MEDIAN_MOVE_PCT,
    FALLBACK_MIN_MOVE_PCT,
    calculate_expected_earnings_move,
    evaluate_earnings_crush_candidates,
    get_historical_earnings_moves,
    snap_strike_to_grid_or_chain,
)


# ---------------------------------------------------------------------------
# 1. Expected Move Math & Boundary Tests
# ---------------------------------------------------------------------------

class TestExpectedEarningsMove:
    def test_expected_move_exact_math(self):
        """Test expected move formula: 0.80 * S * IV * sqrt(DTE / 365)."""
        spot = 100.0
        atm_iv = 0.50
        dte = 365.0  # sqrt(1.0) = 1.0

        res = calculate_expected_earnings_move(spot, atm_iv, dte)
        expected_usd = 0.80 * 100.0 * 0.50 * 1.0  # $40.00
        expected_pct = 0.40  # 40.0%

        assert pytest.approx(res["expected_move_usd"], 1e-4) == expected_usd
        assert pytest.approx(res["expected_move_pct"], 1e-4) == expected_pct
        assert pytest.approx(res["upper_expected_price"], 1e-4) == 140.0
        assert pytest.approx(res["lower_expected_price"], 1e-4) == 60.0
        assert pytest.approx(res["straddle_price_proxy"], 1e-4) == expected_usd

    def test_expected_move_short_dte(self):
        """Test expected move with typical 7 DTE front-week option."""
        spot = 200.0
        atm_iv = 0.40
        dte = 7.0

        res = calculate_expected_earnings_move(spot, atm_iv, dte)
        t_years = 7.0 / 365.0
        expected_usd = 0.80 * 200.0 * 0.40 * np.sqrt(t_years)
        expected_pct = expected_usd / 200.0

        assert pytest.approx(res["expected_move_usd"], abs=1e-4) == expected_usd
        assert pytest.approx(res["expected_move_pct"], abs=1e-4) == expected_pct
        assert res["upper_expected_price"] > spot
        assert res["lower_expected_price"] < spot

    def test_percentage_iv_auto_normalization(self):
        """Test that IV passed as 50.0 (50%) is normalized identically to 0.50."""
        res_decimal = calculate_expected_earnings_move(150.0, 0.60, 14.0)
        res_percent = calculate_expected_earnings_move(150.0, 60.0, 14.0)

        assert res_decimal["expected_move_usd"] == res_percent["expected_move_usd"]
        assert res_decimal["expected_move_pct"] == res_percent["expected_move_pct"]
        assert pytest.approx(res_percent["atm_iv"], 1e-4) == 0.60

    def test_degenerate_and_none_inputs(self):
        """Test that degenerate inputs return safe zero bounds without raising (CONSTRAINT #6)."""
        res_zero_spot = calculate_expected_earnings_move(0.0, 0.40, 7.0)
        assert res_zero_spot["expected_move_usd"] == 0.0
        assert res_zero_spot["expected_move_pct"] == 0.0

        res_neg_spot = calculate_expected_earnings_move(-100.0, 0.40, 7.0)
        assert res_neg_spot["expected_move_usd"] == 0.0

        res_zero_iv = calculate_expected_earnings_move(100.0, 0.0, 7.0)
        assert res_zero_iv["expected_move_usd"] == 0.0

        res_neg_dte = calculate_expected_earnings_move(100.0, 0.40, -5.0)
        assert res_neg_dte["expected_move_usd"] == 0.0

        res_none = calculate_expected_earnings_move(None, None, None)
        assert res_none["expected_move_usd"] == 0.0

        res_nan = calculate_expected_earnings_move(float("nan"), 0.50, 7.0)
        assert res_nan["expected_move_usd"] == 0.0


# ---------------------------------------------------------------------------
# 2. Historical Realized Moves & Fallback Tests
# ---------------------------------------------------------------------------

class MockHistoricalStore:
    def __init__(self, events: List[Dict[str, Any]], bars_df: Optional[pd.DataFrame]):
        self._events = events
        self._bars_df = bars_df

    def get_earnings_events(self, symbol: str, **kwargs: Any) -> List[Dict[str, Any]]:
        limit = kwargs.get("limit", len(self._events))
        actuals_only = kwargs.get("actuals_only", False)
        after = kwargs.get("after")

        res = self._events
        if actuals_only:
            res = [e for e in res if e.get("eps_actual") is not None]
        if after:
            res = [e for e in res if str(e.get("event_date", "")) > str(after)]
        return res[:limit]

    def get_bars(self, symbol: str, lookback_days: int = 756) -> Optional[pd.DataFrame]:
        return self._bars_df


class TestHistoricalEarningsMoves:
    def _create_synthetic_bars_and_events(self):
        """Creates 8 quarters of synthetic earnings and daily bars with known price gaps."""
        dates = pd.date_range(start="2024-01-01", end="2025-12-31", freq="B")
        df = pd.DataFrame(
            {
                "Open": 100.0,
                "High": 102.0,
                "Low": 99.0,
                "Close": 100.0,
                "Volume": 1000000,
            },
            index=dates,
        )

        # 8 quarters of earnings dates with specific known gaps:
        # e.g. Quarter 1: PrevClose=100, Open=105 -> 5.0%
        #      Quarter 2: PrevClose=100, Open=96  -> 4.0%
        #      Quarter 3: PrevClose=100, Open=106 -> 6.0%
        #      Quarter 4: PrevClose=100, Open=97  -> 3.0%
        #      Quarter 5: PrevClose=100, Open=108 -> 8.0%
        #      Quarter 6: PrevClose=100, Open=95  -> 5.0%
        #      Quarter 7: PrevClose=100, Open=104 -> 4.0%
        #      Quarter 8: PrevClose=100, Open=93  -> 7.0%
        quarters_data = [
            ("2024-02-15", 105.0),
            ("2024-05-15", 96.0),
            ("2024-08-15", 106.0),
            ("2024-11-15", 97.0),
            ("2025-02-14", 108.0),
            ("2025-05-15", 95.0),
            ("2025-08-15", 104.0),
            ("2025-11-14", 93.0),
        ]

        events = []
        for d_str, open_p in quarters_data:
            dt = pd.to_datetime(d_str)
            if dt in df.index:
                df.loc[dt, "Open"] = open_p
            events.append({
                "symbol": "AAPL",
                "event_date": d_str,
                "eps_actual": 1.50,
                "eps_estimated": 1.45,
            })

        return df, events

    def test_full_8_quarters_history(self):
        """Test accurate calculation of 8-quarter median, mean, min, and max moves."""
        bars_df, events = self._create_synthetic_bars_and_events()
        store = MockHistoricalStore(events, bars_df)

        res = get_historical_earnings_moves("AAPL", store, lookback_quarters=8)

        assert res["symbol"] == "AAPL"
        assert res["quarters_count"] == 8
        assert res["sparse_history"] is False
        assert res["fallback"] is False
        assert len(res["moves"]) == 8

        # Expected gaps: [0.05, 0.04, 0.06, 0.03, 0.08, 0.05, 0.04, 0.07]
        # Sorted gaps: [0.03, 0.04, 0.04, 0.05, 0.05, 0.06, 0.07, 0.08]
        # Median: 0.05
        # Mean: 0.0525
        assert pytest.approx(res["median_move_pct"], 1e-3) == 0.05
        assert pytest.approx(res["mean_move_pct"], 1e-3) == 0.0525
        assert pytest.approx(res["min_move_pct"], 1e-3) == 0.03
        assert pytest.approx(res["max_move_pct"], 1e-3) == 0.08

    def test_sparse_history_flagging(self):
        """Test that < 3 quarters sets sparse_history=True while still calculating empirical metrics."""
        bars_df, all_events = self._create_synthetic_bars_and_events()
        sparse_events = all_events[:2]  # only 2 quarters
        store = MockHistoricalStore(sparse_events, bars_df)

        res = get_historical_earnings_moves("AAPL", store, lookback_quarters=8)

        assert res["quarters_count"] == 2
        assert res["sparse_history"] is True
        assert res["fallback"] is False
        assert "Sparse history" in str(res["reason"])
        assert len(res["moves"]) == 2

    def test_empty_store_realistic_fallback(self):
        """Test that missing data or empty store returns realistic fallback empirical bounds."""
        store = MockHistoricalStore([], pd.DataFrame())

        res = get_historical_earnings_moves("XYZ", store)

        assert res["quarters_count"] == 0
        assert res["sparse_history"] is True
        assert res["fallback"] is True
        assert res["median_move_pct"] == FALLBACK_MEDIAN_MOVE_PCT
        assert res["mean_move_pct"] == FALLBACK_MEAN_MOVE_PCT
        assert res["min_move_pct"] == FALLBACK_MIN_MOVE_PCT
        assert res["max_move_pct"] == FALLBACK_MAX_MOVE_PCT

    def test_store_none_dynamic_init(self):
        """Test calling get_historical_earnings_moves without store argument handles failure gracefully."""
        with mock.patch("data.historical_store.HistoricalStore", side_effect=Exception("DB boom")):
            res = get_historical_earnings_moves("AAPL", None)
            assert res["sparse_history"] is True
            assert res["fallback"] is True


# ---------------------------------------------------------------------------
# 3. Strike Snapping & Grid Helpers
# ---------------------------------------------------------------------------

class TestStrikeSnapping:
    def test_snap_to_available_chain_strikes(self):
        """Test strike snapping prefers available chain strikes with directional preference."""
        available = [90.0, 95.0, 100.0, 105.0, 110.0]

        # Call above: target 106.2 -> should snap to 110.0 (or nearest >= target)
        call_strike = snap_strike_to_grid_or_chain(106.2, available, preference="above")
        assert call_strike == 110.0

        # Put below: target 93.8 -> should snap to 90.0 (or nearest <= target)
        put_strike = snap_strike_to_grid_or_chain(93.8, available, preference="below")
        assert put_strike == 90.0

        # Nearest
        nearest_strike = snap_strike_to_grid_or_chain(98.8, available, preference="nearest")
        assert nearest_strike == 100.0

    def test_snap_to_grid_when_chain_absent(self):
        """Test standard grid rounding ($0.50) when options chain is unavailable."""
        strike = snap_strike_to_grid_or_chain(105.32, available_strikes=None, grid=0.50)
        assert strike == 105.50

        strike_custom_grid = snap_strike_to_grid_or_chain(106.18, available_strikes=None, grid=1.00)
        assert strike_custom_grid == 106.00


# ---------------------------------------------------------------------------
# 4. Evaluate Earnings Crush Candidates
# ---------------------------------------------------------------------------

class MockOptionsChain:
    def __init__(self, strikes: List[float], atm_iv: float = 0.60):
        self.calls = pd.DataFrame([
            {"strike": s, "impliedVolatility": atm_iv, "bid": 2.0, "ask": 2.20, "lastPrice": 2.10}
            for s in strikes
        ])
        self.puts = pd.DataFrame([
            {"strike": s, "impliedVolatility": atm_iv, "bid": 2.0, "ask": 2.20, "lastPrice": 2.10}
            for s in strikes
        ])


class MockOptionsProvider:
    def __init__(self, expirations_map: Dict[str, List[str]], chain_map: Dict[str, Any]):
        self._expirations_map = expirations_map
        self._chain_map = chain_map

    def fetch_options_chain(self, symbol: str, expiration: Optional[str] = None) -> Any:
        if expiration is None:
            return self._expirations_map.get(symbol, [])
        key = f"{symbol}_{expiration}"
        return self._chain_map.get(key)


class TestEvaluateEarningsCrushCandidates:
    def test_high_edge_candidate_qualifies_for_iron_condor(self):
        """Test candidate with Implied Move 8.0% vs Realized Move 5.0% (Edge 1.60x) qualifies."""
        today = date(2026-8, 14, 1) if False else date(2026, 8, 14)
        earnings_date = date(2026, 8, 17)  # 3 days away (within 1-5 window)
        exp_date = "2026-08-21"  # front-week Friday expiration (7 DTE)

        # Setup historical store
        dates = pd.date_range(start="2025-01-01", end="2026-08-14", freq="B")
        bars_df = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 100000}, index=dates)

        events = [
            {"symbol": "NVDA", "event_date": "2025-05-15", "eps_actual": 1.0},
            {"symbol": "NVDA", "event_date": "2025-08-15", "eps_actual": 1.0},
            {"symbol": "NVDA", "event_date": "2025-11-15", "eps_actual": 1.0},
            {"symbol": "NVDA", "event_date": "2026-02-15", "eps_actual": 1.0},
        ]
        store = MockHistoricalStore(events, bars_df)

        # Options provider with ATM IV = 0.70 (high pre-earnings IV)
        strikes = [80.0, 85.0, 90.0, 95.0, 100.0, 105.0, 110.0, 115.0, 120.0]
        chain = MockOptionsChain(strikes, atm_iv=0.70)
        options_provider = MockOptionsProvider(
            expirations_map={"NVDA": [exp_date]},
            chain_map={f"NVDA_{exp_date}": chain},
        )

        candidates = evaluate_earnings_crush_candidates(
            universe=["NVDA"],
            store=store,
            options_provider=options_provider,
            min_edge=1.25,
            wing_multiplier=1.20,
            as_of=today,
            upcoming_earnings={"NVDA": earnings_date.isoformat()},
            spot_prices={"NVDA": 100.0},
        )

        assert len(candidates) == 1
        c = candidates[0]
        assert c["symbol"] == "NVDA"
        assert c["spot"] == 100.0
        assert c["days_to_earnings"] == 3
        assert c["dte"] == 7
        assert c["is_recommended"] is True
        assert c["crush_edge_ratio"] >= 1.25
        assert c["strategy"] == "Iron Condor"

        # Check strike geometry: long_put < short_put < spot < short_call < long_call
        strikes_dict = c["strikes"]
        lp = strikes_dict["long_put"]
        sp = strikes_dict["short_put"]
        sc = strikes_dict["short_call"]
        lc = strikes_dict["long_call"]

        assert lp < sp < 100.0 < sc < lc
        assert len(c["legs"]) == 4

        # Verify 4 Iron Condor legs
        leg_lp, leg_sp, leg_sc, leg_lc = c["legs"]
        assert leg_lp["side"] == "buy" and leg_lp["type"] == "put" and leg_lp["strike"] == lp
        assert leg_sp["side"] == "sell" and leg_sp["type"] == "put" and leg_sp["strike"] == sp
        assert leg_sc["side"] == "sell" and leg_sc["type"] == "call" and leg_sc["strike"] == sc
        assert leg_lc["side"] == "buy" and leg_lc["type"] == "call" and leg_lc["strike"] == lc

    def test_low_edge_candidate_not_recommended(self):
        """Test candidate with low IV edge (< 1.25x) is marked is_recommended=False."""
        today = date(2026, 8, 14)
        earnings_date = date(2026, 8, 18)

        dates = pd.date_range(start="2025-01-01", end="2026-08-14", freq="B")
        bars_df = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 100000}, index=dates)

        # High historical move (8%)
        events = [
            {"symbol": "LOW_EDGE", "event_date": "2025-05-15", "eps_actual": 1.0},
        ]
        store = MockHistoricalStore(events, bars_df)

        # Low ATM IV (0.20) -> Expected Move ~ 2.2%
        strikes = [90.0, 95.0, 100.0, 105.0, 110.0]
        chain = MockOptionsChain(strikes, atm_iv=0.20)
        options_provider = MockOptionsProvider(
            expirations_map={"LOW_EDGE": ["2026-08-21"]},
            chain_map={"LOW_EDGE_2026-08-21": chain},
        )

        candidates = evaluate_earnings_crush_candidates(
            universe=["LOW_EDGE"],
            store=store,
            options_provider=options_provider,
            min_edge=1.25,
            as_of=today,
            upcoming_earnings={"LOW_EDGE": earnings_date.isoformat()},
            spot_prices={"LOW_EDGE": 100.0},
        )

        assert len(candidates) == 1
        c = candidates[0]
        assert c["is_recommended"] is False
        assert c["crush_edge_ratio"] < 1.25

    def test_filter_outside_1_to_5_days_window(self):
        """Test announcements 0 days away (today) or > 5 days away (e.g. 10 days) are skipped."""
        today = date(2026, 8, 14)
        store = MockHistoricalStore([], None)
        options_provider = MockOptionsProvider({}, {})

        candidates = evaluate_earnings_crush_candidates(
            universe=["SYM_TODAY", "SYM_FAR"],
            store=store,
            options_provider=options_provider,
            as_of=today,
            upcoming_earnings={
                "SYM_TODAY": "2026-08-14",  # 0 days away
                "SYM_FAR": "2026-08-28",    # 14 days away
            },
        )

        assert len(candidates) == 0

    def test_candidate_sorting_by_edge(self):
        """Test multiple candidates are returned sorted by crush_edge_ratio descending."""
        today = date(2026, 8, 14)
        store = MockHistoricalStore([], None)

        chain_high = MockOptionsChain([90.0, 100.0, 110.0], atm_iv=0.80)
        chain_med = MockOptionsChain([90.0, 100.0, 110.0], atm_iv=0.50)

        options_provider = MockOptionsProvider(
            expirations_map={"HIGH": ["2026-08-21"], "MED": ["2026-08-21"]},
            chain_map={"HIGH_2026-08-21": chain_high, "MED_2026-08-21": chain_med},
        )

        candidates = evaluate_earnings_crush_candidates(
            universe=["MED", "HIGH"],
            store=store,
            options_provider=options_provider,
            as_of=today,
            upcoming_earnings={
                "HIGH": "2026-08-17",
                "MED": "2026-08-17",
            },
            spot_prices={"HIGH": 100.0, "MED": 100.0},
        )

        assert len(candidates) == 2
        assert candidates[0]["symbol"] == "HIGH"
        assert candidates[1]["symbol"] == "MED"
        assert candidates[0]["crush_edge_ratio"] > candidates[1]["crush_edge_ratio"]


# ---------------------------------------------------------------------------
# 5. AST Import Safety Test
# ---------------------------------------------------------------------------

def test_earnings_crush_ast_import_safety():
    """Verifies that pilots/earnings_crush.py never imports heavy forbidden engines."""
    file_path = Path(__file__).resolve().parent.parent / "pilots" / "earnings_crush.py"
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename="earnings_crush.py")

    forbidden_modules = {
        "processing_engine",
        "technical_options_engine",
        "forecasting_engine",
        "strategy_engine",
        "macro_engine",
        "main",
        "main_orchestrator",
        "desktop",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for forbidden in forbidden_modules:
                    assert forbidden not in alias.name, f"Forbidden import found: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for forbidden in forbidden_modules:
                    assert forbidden not in node.module, f"Forbidden import from found: {node.module}"
