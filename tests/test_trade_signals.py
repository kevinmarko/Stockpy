"""
tests/test_trade_signals.py — Trade-signal abilities tests
==========================================================
Covers ``engine/trade_signals.py`` — the two advisory trading abilities:

* Ability A — conviction momentum:
    - history append + lookback trim + universe pruning
    - "building" detection (steady climb below the siren) + debounce
    - "fading" detection (steady decline on a non-BUY name) + debounce
    - trend-reset clears the debounce so a later move re-alerts
* Ability B — stop / target proximity:
    - ATR-scaled stop below cost (and % fallback when ATR missing)
    - forecast-based target (and ATR fallback)
    - approach / breach classification + debounce
    - dust-position and bad-data filtering (CONSTRAINT #4 — no fabrication)
* dispatch helper — no-op on empty, never raises on a broken notify

All tests are fully offline — no network, no filesystem side-effects.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest

from engine.trade_signals import (
    CONFIG,
    TradeAlert,
    detect_conviction_momentum,
    detect_price_triggers,
    dispatch_trade_alerts,
    update_conviction_history,
)


# ---------------------------------------------------------------------------
# Duck-typed fixtures (decouple from the heavy engine.advisory import)
# ---------------------------------------------------------------------------


@dataclass
class _Rec:
    symbol: str
    action: str
    conviction: float
    forecast: Optional[float] = None
    key_indicators: Dict[str, float] = field(default_factory=dict)


@dataclass
class _Pos:
    symbol: str
    quantity: float
    average_cost: float
    current_price: float
    market_value: float
    unrealized_pl_pct: float = 0.0


@dataclass
class _Snap:
    positions: Dict[str, _Pos]


def _hist_from(series: List[float], symbol: str = "AAPL", action: str = "BUY") -> Dict[str, List[float]]:
    """Build a conviction-history map by feeding each value through the updater."""
    hist: Dict[str, List[float]] = {}
    for c in series:
        hist = update_conviction_history(hist, [_Rec(symbol, action, c)])
    return hist


# ===========================================================================
# Ability A — conviction history bookkeeping
# ===========================================================================


class TestConvictionHistory:
    def test_append_accumulates(self):
        hist = _hist_from([0.5, 0.6, 0.7])
        assert hist["AAPL"] == [0.5, 0.6, 0.7]

    def test_lookback_trims_oldest(self):
        lookback = int(CONFIG["momentum_lookback_cycles"])
        hist = _hist_from([0.1 * i for i in range(lookback + 4)])
        assert len(hist["AAPL"]) == lookback

    def test_universe_pruning_drops_absent_symbols(self):
        hist = {"OLD": [0.5, 0.6]}
        hist = update_conviction_history(hist, [_Rec("NEW", "BUY", 0.7)])
        assert "OLD" not in hist
        assert hist["NEW"] == [0.7]

    def test_input_not_mutated(self):
        original = {"AAPL": [0.5]}
        snapshot = dict(original)
        update_conviction_history(original, [_Rec("AAPL", "BUY", 0.6)])
        assert original == snapshot  # original dict untouched

    def test_nan_conviction_skipped(self):
        hist = update_conviction_history({}, [_Rec("AAPL", "BUY", float("nan"))])
        assert "AAPL" not in hist

    def test_blank_symbol_skipped(self):
        hist = update_conviction_history({}, [_Rec("", "BUY", 0.9)])
        assert hist == {}

    def test_empty_recommendations(self):
        assert update_conviction_history({"AAPL": [0.5]}, []) == {}


# ===========================================================================
# Ability A — momentum detection
# ===========================================================================


class TestConvictionMomentum:
    def test_building_fires_once(self):
        hist = _hist_from([0.55, 0.63, 0.72, 0.80])
        alerts, alerted = detect_conviction_momentum(hist, [_Rec("AAPL", "BUY", 0.80)], {})
        assert len(alerts) == 1
        assert alerts[0].kind == "momentum_building"
        assert alerts[0].priority == "default"
        assert alerted["AAPL"] == "building"

    def test_building_debounced_on_repeat(self):
        hist = _hist_from([0.55, 0.63, 0.72, 0.80])
        _, alerted = detect_conviction_momentum(hist, [_Rec("AAPL", "BUY", 0.80)], {})
        alerts2, _ = detect_conviction_momentum(hist, [_Rec("AAPL", "BUY", 0.80)], alerted)
        assert alerts2 == []

    def test_building_suppressed_at_or_above_ceiling(self):
        # last value ≥ ceiling (0.85) belongs to the backlog siren, not "building".
        hist = _hist_from([0.70, 0.80, 0.88])
        alerts, _ = detect_conviction_momentum(hist, [_Rec("AAPL", "BUY", 0.88)], {})
        assert alerts == []

    def test_building_suppressed_below_floor(self):
        hist = _hist_from([0.30, 0.40, 0.50])  # below building_floor 0.60
        alerts, _ = detect_conviction_momentum(hist, [_Rec("AAPL", "BUY", 0.50)], {})
        assert alerts == []

    def test_building_requires_min_rise(self):
        # Climbs but by less than rising_delta (0.10) across the window.
        hist = _hist_from([0.70, 0.72, 0.74])
        alerts, _ = detect_conviction_momentum(hist, [_Rec("AAPL", "BUY", 0.74)], {})
        assert alerts == []

    def test_building_blocked_by_sell_action(self):
        hist = _hist_from([0.60, 0.70, 0.80], action="SELL")
        alerts, _ = detect_conviction_momentum(hist, [_Rec("AAPL", "SELL", 0.80)], {})
        assert alerts == []

    def test_not_enough_history(self):
        hist = _hist_from([0.60, 0.75])  # < momentum_min_cycles (3)
        alerts, _ = detect_conviction_momentum(hist, [_Rec("AAPL", "BUY", 0.75)], {})
        assert alerts == []

    def test_fading_fires_high_priority(self):
        hist = _hist_from([0.80, 0.65, 0.50], action="SELL")
        alerts, alerted = detect_conviction_momentum(hist, [_Rec("AAPL", "SELL", 0.50)], {})
        assert len(alerts) == 1
        assert alerts[0].kind == "momentum_fading"
        assert alerts[0].priority == "high"
        assert alerted["AAPL"] == "fading"

    def test_fading_blocked_when_still_buy(self):
        hist = _hist_from([0.80, 0.65, 0.50], action="BUY")
        alerts, _ = detect_conviction_momentum(hist, [_Rec("AAPL", "BUY", 0.50)], {})
        assert alerts == []

    def test_trend_reset_clears_debounce(self):
        # Build first → alerted. Then conviction reverses → flag cleared so a
        # future build can re-alert.
        hist = _hist_from([0.55, 0.65, 0.75])
        _, alerted = detect_conviction_momentum(hist, [_Rec("AAPL", "BUY", 0.75)], {})
        assert alerted.get("AAPL") == "building"
        # Choppy window (not monotonic) → no direction → flag dropped.
        choppy = {"AAPL": [0.75, 0.60, 0.78]}
        _, alerted2 = detect_conviction_momentum(choppy, [_Rec("AAPL", "BUY", 0.78)], alerted)
        assert "AAPL" not in alerted2

    def test_direction_flip_refires(self):
        # Was "building"; now a clean fade should fire a new (fading) alert.
        hist = _hist_from([0.80, 0.64, 0.48], action="HOLD")
        alerts, alerted = detect_conviction_momentum(
            hist, [_Rec("AAPL", "HOLD", 0.48)], {"AAPL": "building"}
        )
        assert len(alerts) == 1 and alerts[0].kind == "momentum_fading"
        assert alerted["AAPL"] == "fading"

    def test_inputs_not_mutated(self):
        hist = _hist_from([0.55, 0.65, 0.75])
        alerted_in = {"AAPL": "x"}
        snapshot = dict(alerted_in)
        detect_conviction_momentum(hist, [_Rec("AAPL", "BUY", 0.75)], alerted_in)
        assert alerted_in == snapshot


# ===========================================================================
# Ability B — stop / target proximity
# ===========================================================================


class TestPriceTriggers:
    def _rec(self, symbol="NVDA", forecast=130.0, atr=3.0):
        return _Rec(symbol, "HOLD", 0.5, forecast=forecast, key_indicators={"atr": atr})

    def test_stop_approach_atr_scaled(self):
        # cost 100, atr 3, stop = 100 - 2.5*3 = 92.5. price 92.5 → within band.
        snap = _Snap({"NVDA": _Pos("NVDA", 10, 100.0, 92.5, 925.0, -7.5)})
        alerts, alerted = detect_price_triggers(snap, [self._rec()], {})
        assert len(alerts) == 1
        a = alerts[0]
        assert a.kind == "approaching_stop"
        assert a.priority == "high"
        assert math.isclose(a.detail["stop_level"], 92.5)
        assert alerted["NVDA"] == "stop"

    def test_stop_breach_reported(self):
        snap = _Snap({"NVDA": _Pos("NVDA", 10, 100.0, 90.0, 900.0, -10.0)})
        alerts, _ = detect_price_triggers(snap, [self._rec()], {})
        assert alerts and "breached" in alerts[0].title

    def test_stop_fallback_pct_when_atr_missing(self):
        # No ATR → stop = cost*(1-0.08) = 92.0. price 92.0 → within band.
        rec = _Rec("NVDA", "HOLD", 0.5, forecast=130.0, key_indicators={})
        snap = _Snap({"NVDA": _Pos("NVDA", 10, 100.0, 92.0, 920.0, -8.0)})
        alerts, _ = detect_price_triggers(snap, [rec], {})
        assert alerts and math.isclose(alerts[0].detail["stop_level"], 92.0)

    def test_target_approach_uses_forecast(self):
        # price 129 vs forecast 130 → within target band (2%).
        snap = _Snap({"NVDA": _Pos("NVDA", 10, 100.0, 129.0, 1290.0, 29.0)})
        alerts, alerted = detect_price_triggers(snap, [self._rec()], {})
        assert len(alerts) == 1
        assert alerts[0].kind == "approaching_target"
        assert alerts[0].priority == "default"
        assert math.isclose(alerts[0].detail["target_level"], 130.0)
        assert alerted["NVDA"] == "target"

    def test_target_already_exceeded(self):
        # Forecast below price → price has met/passed the model target → fire.
        rec = self._rec(forecast=120.0)
        snap = _Snap({"NVDA": _Pos("NVDA", 10, 100.0, 125.0, 1250.0, 25.0)})
        alerts, _ = detect_price_triggers(snap, [rec], {})
        assert alerts and alerts[0].kind == "approaching_target"

    def test_target_atr_fallback_when_no_forecast(self):
        # No forecast, atr 3 → target = cost + 3*3 = 109. price 108 → within band.
        rec = _Rec("NVDA", "HOLD", 0.5, forecast=None, key_indicators={"atr": 3.0})
        snap = _Snap({"NVDA": _Pos("NVDA", 10, 100.0, 108.0, 1080.0, 8.0)})
        alerts, _ = detect_price_triggers(snap, [rec], {})
        assert alerts and math.isclose(alerts[0].detail["target_level"], 109.0)

    def test_no_trigger_in_midrange(self):
        # price 110, stop 92.5, target 130 → neither.
        snap = _Snap({"NVDA": _Pos("NVDA", 10, 100.0, 110.0, 1100.0, 10.0)})
        alerts, alerted = detect_price_triggers(snap, [self._rec()], {})
        assert alerts == []
        assert alerted == {}

    def test_stop_debounced(self):
        snap = _Snap({"NVDA": _Pos("NVDA", 10, 100.0, 92.0, 920.0, -8.0)})
        _, alerted = detect_price_triggers(snap, [self._rec()], {})
        alerts2, _ = detect_price_triggers(snap, [self._rec()], alerted)
        assert alerts2 == []

    def test_dust_position_ignored(self):
        # market_value below min_position_value_usd (100).
        snap = _Snap({"NVDA": _Pos("NVDA", 0.5, 100.0, 92.0, 46.0, -8.0)})
        alerts, _ = detect_price_triggers(snap, [self._rec()], {})
        assert alerts == []

    def test_zero_quantity_ignored(self):
        snap = _Snap({"NVDA": _Pos("NVDA", 0.0, 100.0, 92.0, 0.0, 0.0)})
        alerts, _ = detect_price_triggers(snap, [self._rec()], {})
        assert alerts == []

    def test_no_recommendation_uses_pct_stop(self):
        # Position with no matching rec → no ATR/forecast → % stop still works.
        snap = _Snap({"NVDA": _Pos("NVDA", 10, 100.0, 92.0, 920.0, -8.0)})
        alerts, _ = detect_price_triggers(snap, [], {})
        assert alerts and alerts[0].kind == "approaching_stop"

    def test_empty_positions(self):
        assert detect_price_triggers(_Snap({}), [self._rec()], {}) == ([], {})

    def test_missing_positions_attr(self):
        class _Bare:
            pass
        assert detect_price_triggers(_Bare(), [self._rec()], {}) == ([], {})

    def test_inputs_not_mutated(self):
        snap = _Snap({"NVDA": _Pos("NVDA", 10, 100.0, 92.0, 920.0, -8.0)})
        alerted_in = {"X": "y"}
        snapshot = dict(alerted_in)
        detect_price_triggers(snap, [self._rec()], alerted_in)
        assert alerted_in == snapshot


# ===========================================================================
# Dispatch helper
# ===========================================================================


class TestDispatch:
    def test_empty_is_noop(self):
        # Must not import alerting at all when there's nothing to send.
        with mock.patch.dict("sys.modules", {"alerting": mock.MagicMock()}) as mods:
            dispatch_trade_alerts([])
            # alerting.notify should never have been touched
            assert not mods["alerting"].notify.called

    def test_one_notify_per_alert(self):
        fake = mock.MagicMock()
        alerts = [
            TradeAlert("AAPL", "momentum_building", "default", "t1", "m1"),
            TradeAlert("NVDA", "approaching_stop", "high", "t2", "m2"),
        ]
        with mock.patch.dict("sys.modules", {"alerting": fake}):
            dispatch_trade_alerts(alerts)
        assert fake.notify.call_count == 2

    def test_dashboard_url_appended(self):
        fake = mock.MagicMock()
        alerts = [TradeAlert("AAPL", "momentum_building", "default", "t", "body")]
        with mock.patch.dict("sys.modules", {"alerting": fake}):
            dispatch_trade_alerts(alerts, dashboard_url="http://localhost:8501")
        _, kwargs = fake.notify.call_args
        assert "localhost:8501" in kwargs["message"]

    def test_broken_notify_does_not_raise(self):
        fake = mock.MagicMock()
        fake.notify.side_effect = RuntimeError("ntfy down")
        alerts = [TradeAlert("AAPL", "momentum_building", "default", "t", "m")]
        with mock.patch.dict("sys.modules", {"alerting": fake}):
            dispatch_trade_alerts(alerts)  # must swallow

    def test_priority_forwarded(self):
        fake = mock.MagicMock()
        alerts = [TradeAlert("AAPL", "momentum_fading", "high", "t", "m")]
        with mock.patch.dict("sys.modules", {"alerting": fake}):
            dispatch_trade_alerts(alerts)
        _, kwargs = fake.notify.call_args
        assert kwargs["priority"] == "high"


# ===========================================================================
# Module surface
# ===========================================================================


class TestModuleSurface:
    def test_config_keys_present(self):
        required = {
            "momentum_lookback_cycles", "momentum_min_cycles",
            "momentum_rising_delta", "momentum_building_floor",
            "momentum_building_ceiling", "momentum_falling_delta",
            "stop_atr_multiple", "stop_fallback_pct", "stop_proximity_pct",
            "target_atr_multiple", "target_proximity_pct", "min_position_value_usd",
        }
        assert required.issubset(CONFIG.keys())

    def test_trade_alert_frozen(self):
        a = TradeAlert("AAPL", "momentum_building", "default", "t", "m")
        with pytest.raises(Exception):
            a.symbol = "MSFT"  # type: ignore[misc]

    def test_no_order_keywords_in_source(self):
        import engine.trade_signals as mod
        src = open(mod.__file__).read().lower()
        for kw in ("submit_order", "place_order", "buy_order", "sell_order",
                   "place_equity_order", "place_option_order"):
            assert kw not in src, f"forbidden order keyword present: {kw}"
