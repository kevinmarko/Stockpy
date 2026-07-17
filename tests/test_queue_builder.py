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


@pytest.fixture
def buffer_off(monkeypatch):
    """Explicitly pin the limit buffer to 0 (MARKET behaviour)."""
    monkeypatch.setattr(qb, "_limit_buffer_bps", lambda: 0)


@pytest.fixture
def buffer_25bps(monkeypatch):
    """Configure a 25 bps limit-order buffer (LIMIT behaviour)."""
    monkeypatch.setattr(qb, "_limit_buffer_bps", lambda: 25)


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
                  "max_notional_per_order", "limit_buffer_bps",
                  "n_intents", "n_placeable", "intents"):
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


class TestRationaleField:
    """Bug D: the queue's `rationale` field must carry the real 'why' a reviewer
    reads before approving an order — NOT the strategy label. Historically
    `rationale` was `rec.strategy or rec.rationale`, and a truthy label
    short-circuited the `or`, discarding the engine's actual reasoning."""

    def test_rationale_carries_the_reasoning_not_the_label(self, no_cap):
        rec = _Rec("NVDA", "SELL", 0.9)
        rec.strategy = "advisory"
        rec.rationale = "Momentum broke down below the 200-day; forecast turned negative."
        p = qb.build_execution_queue(_rr([rec]), mode="review", now=_RTH)
        intent = p["intents"][0]
        # The REGRESSION: rationale must be the paragraph, never the label.
        assert intent["rationale"] == (
            "Momentum broke down below the 200-day; forecast turned negative."
        )
        assert intent["rationale"] != "advisory"

    def test_strategy_label_preserved_on_its_own_key(self, no_cap):
        rec = _Rec("NVDA", "SELL", 0.9)
        rec.strategy = "advisory"
        rec.rationale = "some real reasoning"
        intent = qb.build_execution_queue(_rr([rec]), mode="review", now=_RTH)["intents"][0]
        # The label isn't lost — it moves to `strategy` (strategy_id is NOT in
        # the emitted dict, so `rationale` was its only prior home).
        assert intent["strategy"] == "advisory"

    def test_falls_back_to_label_when_no_rationale(self, no_cap):
        rec = _Rec("NVDA", "SELL", 0.9)
        rec.strategy = "Follow:trend-following"
        rec.rationale = ""  # a follow force-exit or a bare intent
        intent = qb.build_execution_queue(_rr([rec]), mode="review", now=_RTH)["intents"][0]
        # Never empty — a missing rationale falls back to the label rather than
        # rendering a blank "why".
        assert intent["rationale"] == "Follow:trend-following"

    def test_verbose_rationale_truncates_on_a_word_boundary_with_a_marker(self, no_cap):
        # A multi-section RATIONALE_VERBOSITY=verbose rationale can exceed the cap;
        # the cut must be visible (…) and on a word boundary, never a silent
        # mid-word chop (a silently truncated reason is worse than a short one).
        rec = _Rec("NVDA", "SELL", 0.9)
        rec.strategy = "advisory"
        rec.rationale = "alpha bravo charlie delta echo foxtrot " * 60  # ~2280 chars
        intent = qb.build_execution_queue(_rr([rec]), mode="review", now=_RTH)["intents"][0]
        r = intent["rationale"]
        assert len(r) <= 1200
        assert r.endswith("…")
        # Word boundary: the char before the ellipsis is a real word char, and
        # the truncation didn't split a token (every token is a known word).
        body = r[:-1].rstrip()
        assert all(tok in {"alpha", "bravo", "charlie", "delta", "echo", "foxtrot"}
                   for tok in body.split())

    def test_follow_intent_rationale_is_an_honest_ranking(self, no_cap):
        # A follow intent's rationale must read as a RANKING built from real
        # numbers (score/weight/target), never a fabricated thesis. Build a real
        # follow intent through pilots.mirror and confirm it survives to the queue.
        from pilots.mirror import _follow_rationale

        class _P:
            id = "trend-following"
            name = "Trend Follower"

        text = _follow_rationale(
            _P(), rank=2, total=20, score=0.82, weight=0.25, target_notional=2500.0
        )
        assert "ranked #2 of 20" in text
        assert "score 0.82" in text          # a REAL number, not invented
        assert "25.0% target weight" in text
        assert "$2,500 target" in text
        # Never implies discretionary judgment.
        assert "believe" not in text.lower()
        assert "think" not in text.lower()

        # And it flows through to the emitted queue unchanged (below the cap).
        rec = _Rec("NVDA", "BUY", 0.9)
        rec.suggested_position_pct = 0.05
        rec.strategy = "Follow:trend-following"
        rec.rationale = text
        intent = qb.build_execution_queue(_rr([rec]), mode="review", now=_RTH)["intents"][0]
        assert intent["rationale"] == text


# ===========================================================================
# gate_intent unit
# ===========================================================================


class TestProactiveNotify:
    """`emit_execution_queue` pushes an ntfy alert via `alerting.notify` for
    genuinely new intents, and never re-notifies about an unchanged one on
    the next `--interval` cycle.
    """

    def _patch_notify(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "alerting.notify",
            lambda title, message, priority="default": calls.append(
                (title, message, priority)
            ),
        )
        return calls

    def test_new_intent_triggers_notify(self, tmp_path, no_cap, monkeypatch):
        calls = self._patch_notify(monkeypatch)
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        qb.emit_execution_queue(rr, mode="review", output_dir=tmp_path, now=_RTH)
        assert len(calls) == 1
        title, message, priority = calls[0]
        assert "NVDA" in message
        assert priority == "default"  # review mode → never placeable

    def test_unchanged_queue_does_not_renotify(self, tmp_path, no_cap, monkeypatch):
        calls = self._patch_notify(monkeypatch)
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        qb.emit_execution_queue(rr, mode="review", output_dir=tmp_path, now=_RTH)
        qb.emit_execution_queue(rr, mode="review", output_dir=tmp_path, now=_RTH)
        assert len(calls) == 1  # second cycle: identical intent, no repeat push

    def test_newly_placeable_intent_renotifies_with_high_priority(self, tmp_path, monkeypatch):
        calls = self._patch_notify(monkeypatch)
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        monkeypatch.setattr(qb, "_max_notional", lambda: 0.0)
        qb.emit_execution_queue(rr, mode="live", output_dir=tmp_path, now=_RTH)
        monkeypatch.setattr(qb, "_max_notional", lambda: 5000.0)
        qb.emit_execution_queue(rr, mode="live", output_dir=tmp_path, now=_RTH)
        assert len(calls) == 2
        assert calls[0][2] == "default"       # blocked (no cap) → not placeable
        assert calls[1][2] == "high"          # now clears the gate → READY TO PLACE
        assert "READY TO PLACE" in calls[1][1]

    def test_empty_queue_skips_notify(self, tmp_path, no_cap, monkeypatch):
        calls = self._patch_notify(monkeypatch)
        rr = _rr([_Rec("IBM", "HOLD", 0.99)])  # dropped, produces zero intents
        qb.emit_execution_queue(rr, mode="review", output_dir=tmp_path, now=_RTH)
        assert calls == []

    def test_notify_failure_does_not_break_emit(self, tmp_path, no_cap, monkeypatch):
        monkeypatch.setattr(
            "alerting.notify",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ntfy down")),
        )
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        path = qb.emit_execution_queue(rr, mode="review", output_dir=tmp_path, now=_RTH)
        assert path is not None and path.exists()  # queue write still succeeded

    def test_notified_sidecar_is_readable_by_gui_panel(self, tmp_path, no_cap, monkeypatch):
        # Cross-module contract: the GUI's read side must be able to parse
        # what this module actually writes (gui/robinhood_execution_panel.py).
        self._patch_notify(monkeypatch)
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        qb.emit_execution_queue(rr, mode="review", output_dir=tmp_path, now=_RTH)

        from gui.robinhood_execution_panel import read_notification_state
        state = read_notification_state(tmp_path / "execution_queue_notified.json")
        assert state is not None
        assert state.last_notified_at == _RTH.isoformat()
        assert state.last_notified_count == 1
        assert state.last_notified_priority == "default"


class TestLimitOrderBuffer:
    """`ROBINHOOD_LIMIT_BUFFER_BPS` flips MARKET → LIMIT additively, preserving
    every gating/safety invariant.  buffer==0 must be byte-identical to legacy.
    """

    def test_buffer_zero_is_market(self, no_cap, buffer_off):
        rr = _rr([_Rec("NVDA", "SELL", 0.9),
                  _Rec("AAPL", "BUY", 0.9, suggested_position_pct=0.05)])
        p = qb.build_execution_queue(rr, mode="review", now=_RTH)
        assert p["limit_buffer_bps"] == 0
        for i in p["intents"]:
            assert i["order_type"] == "market"
            assert i["limit_price"] is None
            assert "limit_offset_bps" not in i  # absent → byte-identical to legacy

    def test_buffer_positive_is_limit(self, no_cap, buffer_25bps):
        rr = _rr([_Rec("NVDA", "SELL", 0.9),
                  _Rec("AAPL", "BUY", 0.9, suggested_position_pct=0.05)])
        p = qb.build_execution_queue(rr, mode="review", now=_RTH)
        assert p["limit_buffer_bps"] == 25
        for i in p["intents"]:
            assert i["order_type"] == "limit"
            assert i["limit_price"] is None          # resolved downstream at review time
            assert i["limit_offset_bps"] == 25

    def test_buffer_default_via_settings_is_market(self, no_cap):
        # No fixture override → reads real settings default (0) → MARKET.
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        p = qb.build_execution_queue(rr, mode="review", now=_RTH)
        assert p["limit_buffer_bps"] == 0
        assert p["intents"][0]["order_type"] == "market"
        assert "limit_offset_bps" not in p["intents"][0]

    def test_limit_buffer_preserves_gating(self, cap5k, buffer_25bps):
        # LIMIT is additive: allow_place computation unchanged.
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        p = qb.build_execution_queue(rr, mode="live", now=_RTH)
        i = p["intents"][0]
        assert i["order_type"] == "limit"
        assert i["allow_place"] is True   # same gate outcome as MARKET
        assert p["n_placeable"] == 1

    def test_limit_buffer_negative_setting_coerced_to_market(self, monkeypatch, no_cap):
        # A negative/garbage setting must degrade to 0 (MARKET), never negative.
        import settings as settings_mod
        monkeypatch.setattr(settings_mod.settings, "ROBINHOOD_LIMIT_BUFFER_BPS", -5,
                            raising=False)
        assert qb._limit_buffer_bps() == 0
        rr = _rr([_Rec("NVDA", "SELL", 0.9)])
        p = qb.build_execution_queue(rr, mode="review", now=_RTH)
        assert p["intents"][0]["order_type"] == "market"


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
