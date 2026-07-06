"""
tests/test_options_queue_builder.py — Robinhood OPTIONS execution bridge (Tier 8)
=================================================================================
Covers ``execution/options_queue_builder.py`` — the gated, dry-run multi-leg
option-queue emitter, sibling of ``execution/queue_builder.py``.

Safety-critical properties pinned here:
  * mode==off   → emit writes NOTHING and returns None (zero behaviour change)
  * mode==review → queue built, every intent allow_place=False (paper-first)
  * mode==live  → allow_place True ONLY with a notional cap + clear kill switch
                  + risk-gate pass; structurally False otherwise
  * kill switch active → every intent allow_place=False
  * premium gate drops: Cash/Wait, IVR ≤ 50, VIX ≥ 30, CREDIT EVENT / RECESSION,
    integrity failure
  * multi-leg intent shape is correct (side/strike/delta/option_type/dte)

All offline — no broker, no MCP, no network.  Directives are injected via the
``directives=`` seam so no market provider is touched.  The risk gate + kill
switch are the real platform objects; only the notional cap and (in one test)
the kill switch are monkeypatched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pytest

from execution import options_queue_builder as oqb


# ---------------------------------------------------------------------------
# Duck-typed shapes
# ---------------------------------------------------------------------------


@dataclass
class _Rec:
    symbol: str
    action: str = "HOLD"
    conviction: float = 0.9


@dataclass
class _Pos:
    symbol: str
    quantity: float
    average_cost: float
    current_price: float
    market_value: float
    unrealized_pl: float = 0.0


@dataclass
class _Snap:
    positions: Dict[str, _Pos]
    total_equity: float
    buying_power: float


@dataclass
class _RR:
    snapshot: _Snap
    recommendations: List[_Rec]


class _Macro:
    """Minimal macro DTO stand-in exposing vix + market_regime."""

    def __init__(self, vix: float = 18.0, market_regime: str = "RISK ON"):
        self.vix = vix
        self.market_regime = market_regime


_RTH = datetime(2026, 6, 30, 17, 0, tzinfo=timezone.utc)  # Tue ~1pm ET


def _rr(recs: Optional[List[_Rec]] = None, *, equity: float = 100_000.0,
        held: Optional[Dict[str, _Pos]] = None) -> _RR:
    positions = held if held is not None else {
        "NVDA": _Pos("NVDA", 10, 100.0, 120.0, 1200.0, 200.0),
    }
    return _RR(_Snap(positions, equity, 30_000.0), recs or [_Rec("NVDA")])


def _put_credit_spread(*, ivr: float = 65.0, integrity_ok: bool = True) -> Dict:
    """A passing Put Credit Spread directive (delta targets on-grid, valid)."""
    return {
        "Symbol": "NVDA",
        "Strategy": "Put Credit Spread",
        "Action": "Sell to Open",
        "Trend_Bias": "Bullish",
        "IVR_Proxy": ivr,
        "Sigma_GARCH": 0.35,
        "Net_Premium": 1.25,
        "Realizable_Daily_Theta": 0.03,
        "Legs": [
            {"Side": "Short", "Type": "Put", "Strike": 100.0, "Price": 2.50, "Delta": -0.30},
            {"Side": "Long", "Type": "Put", "Strike": 95.0, "Price": 1.25, "Delta": -0.15},
        ],
        "Integrity_OK": integrity_ok,
        "Integrity_Issues": [] if integrity_ok else ["off-grid strike"],
    }


def _cash_wait() -> Dict:
    return {
        "Symbol": "NVDA",
        "Strategy": "Cash",
        "Action": "Wait",
        "IVR_Proxy": 65.0,
        "Legs": [],
        "Integrity_OK": True,
        "Integrity_Issues": [],
    }


@pytest.fixture
def cap5k(monkeypatch):
    """Configure a $5,000 per-order notional cap."""
    monkeypatch.setattr(oqb, "_max_notional", lambda: 5000.0)


@pytest.fixture
def no_cap(monkeypatch):
    monkeypatch.setattr(oqb, "_max_notional", lambda: 0.0)


# ===========================================================================
# Mode staging
# ===========================================================================


class TestModeStaging:
    def test_off_emits_nothing(self, tmp_path):
        path = oqb.emit_options_execution_queue(
            _rr(), mode="off", output_dir=tmp_path,
            directives={"NVDA": _put_credit_spread()},
        )
        assert path is None
        assert list(tmp_path.iterdir()) == []

    def test_unknown_mode_treated_as_off(self, tmp_path):
        path = oqb.emit_options_execution_queue(
            _rr(), mode="banana", output_dir=tmp_path,
            directives={"NVDA": _put_credit_spread()},
        )
        assert path is None

    def test_review_writes_file(self, tmp_path, no_cap):
        path = oqb.emit_options_execution_queue(
            _rr(), mode="review", output_dir=tmp_path,
            directives={"NVDA": _put_credit_spread()},
        )
        assert path is not None and path.exists()
        assert path.name == "options_execution_queue.json"
        payload = json.loads(path.read_text())
        assert payload["queue_type"] == "options"
        assert payload["mode"] == "review"

    def test_review_never_placeable(self, cap5k):
        payload = oqb.build_options_execution_queue(
            _rr(), mode="review", directives={"NVDA": _put_credit_spread()},
        )
        assert payload["n_intents"] == 1
        assert all(i["allow_place"] is False for i in payload["intents"])
        assert payload["n_placeable"] == 0


# ===========================================================================
# allow_place gating
# ===========================================================================


class TestAllowPlaceGating:
    def test_live_with_cap_and_clear_kill_switch_allows(self, cap5k, monkeypatch):
        # Ensure kill switch reads inactive.
        monkeypatch.setattr(oqb.GlobalKillSwitch, "is_active", lambda self: False)
        payload = oqb.build_options_execution_queue(
            _rr(), mode="live", macro_dto=_Macro(), now=_RTH,
            directives={"NVDA": _put_credit_spread()},
        )
        assert payload["n_intents"] == 1
        intent = payload["intents"][0]
        assert intent["gate_allowed"] is True
        assert intent["allow_place"] is True
        assert payload["n_placeable"] == 1

    def test_live_without_cap_blocks(self, no_cap):
        payload = oqb.build_options_execution_queue(
            _rr(), mode="live", macro_dto=_Macro(),
            directives={"NVDA": _put_credit_spread()},
        )
        intent = payload["intents"][0]
        assert intent["allow_place"] is False
        assert "notional_cap_unset" in intent["gate_reasons"]

    def test_kill_switch_blocks_everything(self, cap5k, monkeypatch, tmp_path):
        from execution.kill_switch import GlobalKillSwitch
        ks = GlobalKillSwitch(sentinel_file=tmp_path / "KILL_SWITCH")
        ks.activate("test")
        monkeypatch.setattr(oqb, "GlobalKillSwitch", lambda: ks)
        payload = oqb.build_options_execution_queue(
            _rr(), mode="live", macro_dto=_Macro(),
            directives={"NVDA": _put_credit_spread()},
        )
        assert payload["kill_switch_active"] is True
        assert all(i["allow_place"] is False for i in payload["intents"])

    def test_gate_failure_fails_closed(self, cap5k, monkeypatch):
        class _BoomGate:
            def run_all(self, *a, **k):
                raise RuntimeError("gate exploded")

        monkeypatch.setattr(oqb, "PreTradeRiskGate", lambda *a, **k: _BoomGate())
        payload = oqb.build_options_execution_queue(
            _rr(), mode="live", macro_dto=_Macro(),
            directives={"NVDA": _put_credit_spread()},
        )
        intent = payload["intents"][0]
        assert intent["gate_allowed"] is False
        assert intent["allow_place"] is False
        assert any("gate_error" in r for r in intent["gate_reasons"])


# ===========================================================================
# Premium-selling gate (directive drop rules)
# ===========================================================================


class TestPremiumGate:
    def test_cash_wait_dropped(self, no_cap):
        payload = oqb.build_options_execution_queue(
            _rr(), mode="review", directives={"NVDA": _cash_wait()},
        )
        assert payload["n_intents"] == 0

    def test_low_ivr_dropped(self, no_cap):
        payload = oqb.build_options_execution_queue(
            _rr(), mode="review",
            directives={"NVDA": _put_credit_spread(ivr=45.0)},
        )
        assert payload["n_intents"] == 0

    def test_high_vix_dropped(self, no_cap):
        payload = oqb.build_options_execution_queue(
            _rr(), mode="review", macro_dto=_Macro(vix=35.0),
            directives={"NVDA": _put_credit_spread()},
        )
        assert payload["n_intents"] == 0

    def test_credit_event_regime_dropped(self, no_cap):
        payload = oqb.build_options_execution_queue(
            _rr(), mode="review", macro_dto=_Macro(market_regime="CREDIT EVENT"),
            directives={"NVDA": _put_credit_spread()},
        )
        assert payload["n_intents"] == 0

    def test_recession_regime_dropped(self, no_cap):
        payload = oqb.build_options_execution_queue(
            _rr(), mode="review", macro_dto=_Macro(market_regime="RECESSION"),
            directives={"NVDA": _put_credit_spread()},
        )
        assert payload["n_intents"] == 0

    def test_integrity_failure_dropped(self, no_cap):
        payload = oqb.build_options_execution_queue(
            _rr(), mode="review",
            directives={"NVDA": _put_credit_spread(integrity_ok=False)},
        )
        assert payload["n_intents"] == 0

    def test_vrp_below_threshold_dropped(self, no_cap):
        payload = oqb.build_options_execution_queue(
            _rr(), mode="review", macro_dto=_Macro(), vrp=0.01,
            directives={"NVDA": _put_credit_spread()},
        )
        assert payload["n_intents"] == 0

    def test_vrp_above_threshold_kept(self, no_cap):
        payload = oqb.build_options_execution_queue(
            _rr(), mode="review", macro_dto=_Macro(), vrp=0.05,
            directives={"NVDA": _put_credit_spread()},
        )
        assert payload["n_intents"] == 1


# ===========================================================================
# Multi-leg intent construction
# ===========================================================================


class TestIntentConstruction:
    def test_multi_leg_shape(self, no_cap):
        payload = oqb.build_options_execution_queue(
            _rr(), mode="review", macro_dto=_Macro(),
            directives={"NVDA": _put_credit_spread()},
        )
        intent = payload["intents"][0]
        assert intent["symbol"] == "NVDA"
        assert intent["strategy"] == "Put Credit Spread"
        assert intent["action"] == "Sell to Open"
        assert intent["integrity_ok"] is True
        legs = intent["legs"]
        assert len(legs) == 2
        # Short leg → sell to open; Long leg → buy to open.
        short_leg = next(l for l in legs if l["side"] == "sell")
        long_leg = next(l for l in legs if l["side"] == "buy")
        assert short_leg["option_type"] == "put"
        assert short_leg["strike"] == 100.0
        assert short_leg["delta"] == -0.30
        assert short_leg["dte"] == 30
        assert short_leg["position_effect"] == "open"
        assert long_leg["strike"] == 95.0
        assert long_leg["delta"] == -0.15
        # Net credit spread → order_type net_credit.
        assert intent["order_type"] == "net_credit"
        assert intent["net_premium"] == 1.25

    def test_target_notional_capped(self, cap5k, monkeypatch):
        monkeypatch.setattr(oqb, "_max_notional", lambda: 300.0)
        payload = oqb.build_options_execution_queue(
            _rr(), mode="review", macro_dto=_Macro(),
            directives={"NVDA": _put_credit_spread()},
        )
        assert payload["intents"][0]["target_notional"] == 300.0

    def test_deterministic_client_order_id(self, no_cap):
        d = {"NVDA": _put_credit_spread()}
        p1 = oqb.build_options_execution_queue(_rr(), mode="review",
                                               macro_dto=_Macro(), directives=d, now=_RTH)
        p2 = oqb.build_options_execution_queue(_rr(), mode="review",
                                               macro_dto=_Macro(), directives=d, now=_RTH)
        assert p1["intents"][0]["client_order_id"] == p2["intents"][0]["client_order_id"]


# ===========================================================================
# Payload schema + resilience
# ===========================================================================


class TestPayloadSchema:
    def test_payload_keys(self, no_cap):
        payload = oqb.build_options_execution_queue(
            _rr(), mode="review", macro_dto=_Macro(),
            directives={"NVDA": _put_credit_spread()},
        )
        for k in ("generated_at", "queue_type", "mode", "kill_switch_active",
                  "max_notional_per_order", "n_intents", "n_placeable", "intents"):
            assert k in payload
        intent = payload["intents"][0]
        for k in ("client_order_id", "symbol", "strategy", "action", "legs",
                  "net_premium", "target_notional", "order_type", "gate_allowed",
                  "gate_reasons", "allow_place", "conviction", "rationale",
                  "integrity_ok"):
            assert k in intent

    def test_atomic_write_valid_json(self, tmp_path, no_cap):
        path = oqb.emit_options_execution_queue(
            _rr(), mode="review", output_dir=tmp_path, macro_dto=_Macro(),
            directives={"NVDA": _put_credit_spread()},
        )
        payload = json.loads(path.read_text())
        assert payload["n_intents"] == 1
        # No stray temp file left behind.
        assert not (tmp_path / "options_execution_queue.tmp").exists()

    def test_emit_failure_is_swallowed(self, monkeypatch, tmp_path):
        def _boom(*a, **k):
            raise RuntimeError("build exploded")

        monkeypatch.setattr(oqb, "build_options_execution_queue", _boom)
        # Should not raise; returns None on failure.
        path = oqb.emit_options_execution_queue(
            _rr(), mode="review", output_dir=tmp_path,
            directives={"NVDA": _put_credit_spread()},
        )
        assert path is None

    def test_symbol_union_from_snapshot_and_recs(self):
        rr = _RR(
            _Snap({"AAPL": _Pos("AAPL", 5, 90.0, 100.0, 500.0)}, 10_000.0, 5_000.0),
            [_Rec("MSFT"), _Rec("AAPL")],
        )
        assert oqb._resolve_symbols(rr) == ["AAPL", "MSFT"]
