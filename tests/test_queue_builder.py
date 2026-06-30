"""
tests/test_queue_builder.py — Robinhood execution bridge tests (Tier 8)
=======================================================================
Covers ``execution/queue_builder.py`` — the gated, dry-run order-queue emitter.

Safety-critical properties pinned here:
  * mode==off  → emit writes NOTHING and returns None (zero behaviour change)
  * mode==review → queue built, every intent allow_place=False (paper-first)
  * mode==live  → allow_place True ONLY with a notional cap + clear kill switch
                  + gate pass; structurally False otherwise
  * kill switch active → every intent allow_place=False
  * drop rules: HOLD / below-conviction / not-held-SELL are excluded
  * gate failure fails CLOSED (never marks an intent allowed)

All offline — no broker, no MCP, no network.  The risk gate + kill switch are
the real platform objects; only the notional cap and (in one test) the kill
switch are monkeypatched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pytest

from execution import queue_builder as qb
from execution.broker_base import OrderIntent, OrderSide
from execution.risk_gate import RiskContext


# ---------------------------------------------------------------------------
# Duck-typed advisory shapes
# ---------------------------------------------------------------------------


@dataclass
class _Rec:
    symbol: str
    action: str
    conviction: float
    suggested_position_pct: float = 0.0
    strategy: str = "Momentum"
    rationale: str = "because"


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


_RTH = datetime(2026, 6, 30, 17, 0, tzinfo=timezone.utc)  # Tue ~1pm ET


def _rr(recs: List[_Rec], *, equity: float = 10_000.0,
        held: Optional[Dict[str, _Pos]] = None) -> _RR:
    positions = held if held is not None else {
        "NVDA": _Pos("NVDA", 10, 100.0, 120.0, 1200.0, 200.0),
    }
    return _RR(_Snap(positions, equity, 3000.0), recs)


@pytest.fixture
def cap5k(monkeypatch):
    """Configure a $5,000 per-order notional cap."""
    monkeypatch.setattr(qb, "_max_notional", lambda: 5000.0)


@pytest.fixture
def no_cap(monkeypatch):
    monkeypatch.setattr(qb, "_max_notional", lambda: 0.0)


# ===========================================================================
# Mode staging
# ===========================================================================


class TestModeStaging:
    def test_off_emits_nothing(self, tmp_path):
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        ret = qb.emit_execution_queue(rr, mode="off", output_dir=tmp_path, now=_RTH)
        assert ret is None
        assert not (tmp_path / "execution_queue.json").exists()

    def test_unknown_mode_treated_as_off(self, tmp_path):
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        assert qb.emit_execution_queue(rr, mode="garbage", output_dir=tmp_path, now=_RTH) is None

    def test_review_writes_file(self, tmp_path, no_cap):
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        path = qb.emit_execution_queue(rr, mode="review", output_dir=tmp_path, now=_RTH)
        assert path is not None and path.exists()
        payload = json.loads(path.read_text())
        assert payload["mode"] == "review"
        assert payload["n_intents"] == 1
        assert all(not i["allow_place"] for i in payload["intents"])

    def test_review_never_placeable(self, cap5k):
        # Even with a cap configured, review mode can never place.
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        p = qb.build_execution_queue(rr, mode="review", now=_RTH)
        assert p["n_placeable"] == 0


# ===========================================================================
# allow_place gating (the safety core)
# ===========================================================================


class TestAllowPlaceGating:
    def test_live_with_cap_and_clear_kill_switch_allows(self, cap5k):
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        p = qb.build_execution_queue(rr, mode="live", now=_RTH)
        assert p["n_placeable"] == 1
        assert p["intents"][0]["allow_place"] is True

    def test_live_without_cap_blocks(self, no_cap):
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        p = qb.build_execution_queue(rr, mode="live", now=_RTH)
        i = p["intents"][0]
        assert i["allow_place"] is False
        assert "notional_cap_unset" in i["gate_reasons"]

    def test_kill_switch_blocks_everything(self, cap5k, monkeypatch, tmp_path):
        from execution.kill_switch import GlobalKillSwitch
        ks = GlobalKillSwitch(sentinel_file=tmp_path / "KILL_SWITCH")
        ks.activate("test")
        monkeypatch.setattr(qb, "GlobalKillSwitch", lambda: ks)
        try:
            rr = _rr([_Rec("NVDA", "SELL", 0.9)])
            p = qb.build_execution_queue(rr, mode="live", now=_RTH)
            assert p["kill_switch_active"] is True
            assert all(not i["allow_place"] for i in p["intents"])
        finally:
            ks.deactivate()

    def test_gate_failure_fails_closed(self, cap5k, monkeypatch):
        # A gate that raises must never produce an allowed intent.
        class _BoomGate:
            def run_all(self, intent, ctx):
                raise RuntimeError("gate exploded")
        monkeypatch.setattr(qb, "PreTradeRiskGate", lambda *a, **k: _BoomGate())
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        p = qb.build_execution_queue(rr, mode="live", now=_RTH)
        i = p["intents"][0]
        assert i["allow_place"] is False
        assert i["gate_allowed"] is False
        assert any("gate_error" in r for r in i["gate_reasons"])


# ===========================================================================
# Intent construction / drop rules
# ===========================================================================


class TestIntentConstruction:
    def test_drop_rules(self, no_cap):
        rr = _rr([
            _Rec("AAPL", "BUY", 0.90, suggested_position_pct=0.05),
            _Rec("NVDA", "SELL", 0.90),
            _Rec("MSFT", "BUY", 0.50, suggested_position_pct=0.05),  # below conviction
            _Rec("TSLA", "SELL", 0.99),                              # not held
            _Rec("IBM", "HOLD", 0.99),                               # HOLD
        ])
        p = qb.build_execution_queue(rr, mode="review", now=_RTH)
        assert {i["symbol"] for i in p["intents"]} == {"AAPL", "NVDA"}

    def test_buy_intent_notional_no_qty(self, no_cap):
        rr = _rr([_Rec("AAPL", "BUY", 0.9, suggested_position_pct=0.05)], equity=10_000.0)
        p = qb.build_execution_queue(rr, mode="review", now=_RTH)
        i = next(x for x in p["intents"] if x["symbol"] == "AAPL")
        assert i["qty"] is None                      # agent computes from live quote
        assert i["target_notional"] == 500.0         # 10000 * 0.05
        assert i["side"] == "buy"

    def test_buy_notional_capped(self, monkeypatch):
        monkeypatch.setattr(qb, "_max_notional", lambda: 300.0)
        rr = _rr([_Rec("AAPL", "BUY", 0.9, suggested_position_pct=0.05)], equity=10_000.0)
        p = qb.build_execution_queue(rr, mode="review", now=_RTH)
        i = next(x for x in p["intents"] if x["symbol"] == "AAPL")
        assert i["target_notional"] == 300.0         # min(500, cap 300)

    def test_sell_uses_held_quantity(self, no_cap):
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        p = qb.build_execution_queue(rr, mode="review", now=_RTH)
        i = next(x for x in p["intents"] if x["symbol"] == "NVDA")
        assert i["qty"] == 10.0
        assert i["target_notional"] == 1200.0
        assert i["side"] == "sell"

    def test_zero_notional_buy_dropped(self, no_cap):
        # Zero suggested pct → zero notional → no intent (no fabricated order).
        rr = _rr([_Rec("AAPL", "BUY", 0.9, suggested_position_pct=0.0)])
        p = qb.build_execution_queue(rr, mode="review", now=_RTH)
        assert all(i["symbol"] != "AAPL" for i in p["intents"])

    def test_deterministic_client_order_id(self, no_cap):
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        a = qb.build_execution_queue(rr, mode="review", now=_RTH)["intents"][0]["client_order_id"]
        b = qb.build_execution_queue(rr, mode="review", now=_RTH)["intents"][0]["client_order_id"]
        assert a == b and a


# ===========================================================================
# Payload schema + persistence
# ===========================================================================


class TestPayloadSchema:
    def test_payload_keys(self, no_cap):
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        p = qb.build_execution_queue(rr, mode="review", now=_RTH)
        for k in ("generated_at", "mode", "kill_switch_active",
                  "max_notional_per_order", "n_intents", "n_placeable", "intents"):
            assert k in p, k
        intent = p["intents"][0]
        for k in ("client_order_id", "symbol", "action", "side", "qty",
                  "target_notional", "order_type", "limit_price", "conviction",
                  "gate_allowed", "gate_reasons", "allow_place", "rationale"):
            assert k in intent, k

    def test_atomic_write_valid_json(self, tmp_path, no_cap):
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        path = qb.emit_execution_queue(rr, mode="review", output_dir=tmp_path, now=_RTH)
        # No leftover temp file; file is valid JSON.
        assert not (tmp_path / "execution_queue.tmp").exists()
        json.loads(path.read_text())

    def test_emit_failure_is_swallowed(self, monkeypatch, tmp_path):
        # A build failure must never raise out of emit (best-effort caller).
        monkeypatch.setattr(qb, "build_execution_queue",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        assert qb.emit_execution_queue(rr, mode="review", output_dir=tmp_path, now=_RTH) is None


# ===========================================================================
# gate_intent unit
# ===========================================================================


class TestGateIntent:
    def test_gate_intent_returns_reasons_on_block(self):
        # Force a block: tiny equity so a large notional trips max_position_size.
        intent = OrderIntent(strategy_id="advisory", symbol="AAPL",
                             side=OrderSide.BUY, qty=1000.0)
        ctx = RiskContext(
            account=type("A", (), {"equity": 100.0, "cash": 0.0, "buying_power": 0.0})(),
            current_prices={"AAPL": 100.0},
        )
        allowed, reasons = qb.gate_intent(intent, ctx)
        assert allowed is False and reasons

    def test_gate_intent_fails_closed_on_exception(self):
        class _BoomGate:
            def run_all(self, intent, ctx):
                raise ValueError("nope")
        intent = OrderIntent(strategy_id="advisory", symbol="AAPL",
                             side=OrderSide.SELL, qty=1.0)
        allowed, reasons = qb.gate_intent(intent, RiskContext(), gate=_BoomGate())
        assert allowed is False
        assert any("gate_error" in r for r in reasons)
