"""
tests/test_execution_queue_gating.py
====================================
Regression tests pinning the CORE SAFETY INVARIANT of the Tier 8 Robinhood
execution bridge (``execution/queue_builder.py``).

The bridge emits ``output/execution_queue.json`` — a gated, dry-run list of
proposed order intents that the ``robinhood-execution`` skill consumes. The one
load-bearing field is ``allow_place``: the downstream agent treats it as the
sole "this order may be placed live" flag. It MUST be structurally ``False`` in
every posture except a fully-cleared live one. These tests lock that down so a
future refactor of the ``allow_place`` computation (queue_builder.py ~L258)
cannot silently loosen it.

Invariants pinned here
----------------------
1. ``allow_place`` is ``True`` ONLY when ALL of:
   ``mode == "live"`` AND ``gate_allowed`` AND ``not kill_switch_active`` AND a
   positive notional cap is set. Each of the four conditions, flipped
   individually, forces ``allow_place=False``.
2. ``review`` and ``off`` modes NEVER yield ``allow_place=True``.
   (``off`` also writes nothing via ``emit_execution_queue``.)
3. ``gate_intent()`` fails CLOSED on an internal gate exception — a raising gate
   yields ``(False, [...])`` so the intent is never placeable.
4. Kill switch active (sentinel file present OR queue ``kill_switch_active``
   True) forces no placement.
5. Stale-queue semantics: the payload carries a machine-parseable
   ``generated_at`` ISO timestamp so the skill's >30-min staleness hard-stop is
   computable from the file alone.

Everything is fully offline: no broker/MCP calls. The ``PreTradeRiskGate`` and
``GlobalKillSwitch`` are monkeypatched where needed, following the fixture
patterns in ``tests/test_risk_gate.py`` and ``tests/test_kill_switch.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from execution import queue_builder
from execution.broker_base import OrderIntent, OrderSide, OrderType
from execution.queue_builder import (
    build_execution_queue,
    emit_execution_queue,
    gate_intent,
)
from execution.risk_gate import RiskContext


# ---------------------------------------------------------------------------
# Lightweight fakes mirroring the RunResult / AccountSnapshot / Recommendation
# shapes that queue_builder duck-types on (getattr-based, so simple objects work).
# ---------------------------------------------------------------------------

@dataclass
class _FakePosition:
    symbol: str
    quantity: float = 0.0
    average_cost: float = 0.0
    current_price: float = 0.0
    market_value: float = 0.0
    unrealized_pl: float = 0.0


@dataclass
class _FakeSnapshot:
    total_equity: float = 100_000.0
    buying_power: float = 50_000.0
    positions: Dict[str, _FakePosition] = field(default_factory=dict)


@dataclass
class _FakeRecommendation:
    symbol: str
    action: str
    conviction: float = 0.95
    suggested_position_pct: float = 0.05  # fraction of equity
    strategy: str = "test-strategy"
    rationale: str = "test rationale"


@dataclass
class _FakeRunResult:
    snapshot: _FakeSnapshot
    recommendations: List[_FakeRecommendation] = field(default_factory=list)


# A deterministic weekday timestamp inside NYSE regular trading hours
# (Tue 2026-07-07 14:00 UTC == 10:00 ET during EDT) so the real
# PreTradeRiskGate.market_hours_check passes on the happy-path tests that
# exercise the full gate rather than a monkeypatched one.
_RTH_NOW = datetime(2026, 7, 7, 14, 0, 0, tzinfo=timezone.utc)


def _one_buy_run(conviction: float = 0.95, pct: float = 0.05) -> _FakeRunResult:
    """A RunResult with a single high-conviction BUY that clears the min gate."""
    return _FakeRunResult(
        snapshot=_FakeSnapshot(total_equity=100_000.0, buying_power=50_000.0, positions={}),
        recommendations=[_FakeRecommendation(symbol="AAPL", action="BUY",
                                             conviction=conviction, suggested_position_pct=pct)],
    )


@pytest.fixture(autouse=True)
def _isolate_bridge(monkeypatch, tmp_path):
    """Neutralise external state for every test.

    * Kill switch OFF by default (patch GlobalKillSwitch used *inside*
      build_execution_queue so no on-disk sentinel leaks in).
    * A positive notional cap by default (so cap is not the reason a test fails
      unless it is explicitly the variable under test).
    * ntfy notify is a no-op sidecar-free path (output_dir is a tmp dir).
    """
    monkeypatch.setattr(queue_builder, "_max_notional", lambda: 1_000.0)

    class _KSOff:
        def is_active(self) -> bool:
            return False

    monkeypatch.setattr(queue_builder, "GlobalKillSwitch", _KSOff)
    return tmp_path


# ---------------------------------------------------------------------------
# Invariant 1 — the four-condition AND for allow_place
# ---------------------------------------------------------------------------

def _placeable(payload: dict) -> List[dict]:
    return [i for i in payload["intents"] if i["allow_place"]]


def test_all_conditions_met_live_yields_allow_place():
    """The happy path: live + gate-pass + KS clear + cap set → allow_place True."""
    payload = build_execution_queue(_one_buy_run(), mode="live", now=_RTH_NOW)
    assert payload["n_intents"] == 1
    assert payload["intents"][0]["gate_allowed"] is True
    assert payload["intents"][0]["allow_place"] is True
    assert payload["n_placeable"] == 1


def test_mode_not_live_flips_allow_place_false():
    """Condition 1: mode != live → allow_place False even if everything else clears."""
    payload = build_execution_queue(_one_buy_run(), mode="review", now=_RTH_NOW)
    assert payload["intents"][0]["gate_allowed"] is True  # gate still passes
    assert payload["intents"][0]["allow_place"] is False
    assert _placeable(payload) == []


def test_gate_blocked_flips_allow_place_false(monkeypatch):
    """Condition 2: gate blocks → allow_place False in live mode."""

    class _BlockingGate:
        def run_all(self, intent, context):
            from execution.risk_gate import RiskCheckResult
            return False, [RiskCheckResult("max_position_size", False, "too big")]

    monkeypatch.setattr(queue_builder, "PreTradeRiskGate", _BlockingGate)
    payload = build_execution_queue(_one_buy_run(), mode="live")
    intent = payload["intents"][0]
    assert intent["gate_allowed"] is False
    assert intent["allow_place"] is False
    assert any("max_position_size" in r for r in intent["gate_reasons"])


def test_kill_switch_active_flips_allow_place_false(monkeypatch):
    """Condition 3: kill switch active → allow_place False in live mode."""

    class _KSOn:
        def is_active(self) -> bool:
            return True

    monkeypatch.setattr(queue_builder, "GlobalKillSwitch", _KSOn)
    payload = build_execution_queue(_one_buy_run(), mode="live", now=_RTH_NOW)
    assert payload["kill_switch_active"] is True
    assert payload["intents"][0]["allow_place"] is False
    assert _placeable(payload) == []


def test_zero_notional_cap_flips_allow_place_false(monkeypatch):
    """Condition 4: no positive notional cap → allow_place False in live mode.

    Also asserts the ``notional_cap_unset`` reason is surfaced for the operator.
    """
    monkeypatch.setattr(queue_builder, "_max_notional", lambda: 0.0)
    payload = build_execution_queue(_one_buy_run(), mode="live", now=_RTH_NOW)
    intent = payload["intents"][0]
    assert intent["allow_place"] is False
    assert "notional_cap_unset" in intent["gate_reasons"]
    assert payload["max_notional_per_order"] == 0.0


# ---------------------------------------------------------------------------
# Invariant 2 — review / off never placeable
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["review", "off", "REVIEW", "  off  ", "garbage"])
def test_non_live_modes_never_placeable(mode):
    """review, off, and any invalid/garbage mode → never allow_place True.

    Invalid modes resolve to ``off`` (VALID_MODES fallback), so this also pins
    that an unrecognised mode string can never accidentally place.
    """
    payload = build_execution_queue(_one_buy_run(), mode=mode)
    assert _placeable(payload) == []
    assert payload["n_placeable"] == 0


def test_off_mode_emits_nothing(tmp_path):
    """emit_execution_queue writes NO file when mode resolves to off."""
    path = emit_execution_queue(_one_buy_run(), mode="off", output_dir=tmp_path)
    assert path is None
    assert not (tmp_path / "execution_queue.json").exists()


def test_review_mode_emits_file_with_no_placeable(tmp_path):
    """review mode writes the queue file, but every intent is preview-only."""
    path = emit_execution_queue(_one_buy_run(), mode="review", output_dir=tmp_path)
    assert path is not None and path.exists()
    payload = json.loads(path.read_text())
    assert payload["mode"] == "review"
    assert payload["n_intents"] == 1
    assert payload["n_placeable"] == 0
    assert all(i["allow_place"] is False for i in payload["intents"])


# ---------------------------------------------------------------------------
# Invariant 3 — gate_intent fails CLOSED on internal exception
# ---------------------------------------------------------------------------

def test_gate_intent_fails_closed_on_exception():
    """A gate whose run_all raises → gate_intent returns (False, [gate_error...])."""

    class _RaisingGate:
        def run_all(self, intent, context):
            raise RuntimeError("boom")

    intent = OrderIntent(
        strategy_id="advisory", symbol="AAPL", side=OrderSide.BUY, qty=1.0,
        order_type=OrderType.MARKET, dry_run=True,
    )
    ctx = RiskContext(timestamp=datetime.now(timezone.utc))
    allowed, reasons = gate_intent(intent, ctx, gate=_RaisingGate())
    assert allowed is False
    assert any("gate_error" in r for r in reasons)


def test_raising_gate_in_build_yields_no_placeable(monkeypatch):
    """A raising gate wired through build_execution_queue → no placeable intent."""

    class _RaisingGate:
        def run_all(self, intent, context):
            raise RuntimeError("boom")

    monkeypatch.setattr(queue_builder, "PreTradeRiskGate", _RaisingGate)
    payload = build_execution_queue(_one_buy_run(), mode="live")
    assert payload["intents"][0]["gate_allowed"] is False
    assert payload["intents"][0]["allow_place"] is False
    assert _placeable(payload) == []


# ---------------------------------------------------------------------------
# Invariant 4 — kill switch active forces no placement (file present path)
# ---------------------------------------------------------------------------

def test_kill_switch_sentinel_file_forces_no_placement(monkeypatch, tmp_path):
    """A real GlobalKillSwitch pointed at an existing sentinel file → no placement.

    Exercises the actual GlobalKillSwitch.is_active() file check (not just a
    stub), pinning that a present sentinel structurally blocks placement.
    """
    from execution.kill_switch import GlobalKillSwitch

    sentinel = tmp_path / "KILL_SWITCH"
    ks = GlobalKillSwitch(sentinel_file=sentinel)
    ks.activate(reason="test halt")
    assert sentinel.exists()

    # Route build_execution_queue's internal GlobalKillSwitch() to our sentinel.
    monkeypatch.setattr(
        queue_builder, "GlobalKillSwitch", lambda: GlobalKillSwitch(sentinel_file=sentinel)
    )
    payload = build_execution_queue(_one_buy_run(), mode="live", now=_RTH_NOW)
    assert payload["kill_switch_active"] is True
    assert _placeable(payload) == []


# ---------------------------------------------------------------------------
# Invariant 5 — stale-queue semantics are computable from the payload
# ---------------------------------------------------------------------------

def test_generated_at_is_parseable_iso_timestamp():
    """generated_at round-trips through datetime.fromisoformat for staleness math."""
    now = datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc)
    payload = build_execution_queue(_one_buy_run(), mode="review", now=now)
    parsed = datetime.fromisoformat(payload["generated_at"])
    assert parsed == now


def test_stale_queue_age_is_derivable():
    """A queue generated >30 min ago is detectable purely from generated_at.

    The robinhood-execution skill's stale hard-stop (>~30 min) is a pure
    function of ``generated_at``; this pins that the field supports that math.
    """
    old = datetime.now(timezone.utc) - timedelta(minutes=45)
    payload = build_execution_queue(_one_buy_run(), mode="review", now=old)
    age = datetime.now(timezone.utc) - datetime.fromisoformat(payload["generated_at"])
    assert age > timedelta(minutes=30)


# ---------------------------------------------------------------------------
# Deterministic non-empty review-mode fixture (Deliverable 1)
# ---------------------------------------------------------------------------

def test_deterministic_nonempty_review_queue_shape(tmp_path):
    """Reproducible non-empty review queue with a fully asserted intent shape.

    This is the canonical fixture future work can build a non-empty
    ``execution_queue.json`` from: one BUY that clears gating, emitted in
    review mode, with every payload/intent key pinned.
    """
    now = datetime(2026, 7, 5, 9, 45, 0, tzinfo=timezone.utc)
    path = emit_execution_queue(_one_buy_run(), mode="review", output_dir=tmp_path, now=now)
    assert path is not None
    payload = json.loads(path.read_text())

    # Payload-level keys.
    for key in ("generated_at", "mode", "kill_switch_active", "max_notional_per_order",
                "n_intents", "n_placeable", "intents"):
        assert key in payload, f"missing payload key {key}"
    assert payload["mode"] == "review"
    assert payload["kill_switch_active"] is False
    assert payload["n_intents"] == 1
    assert payload["n_placeable"] == 0

    # Intent-level keys.
    intent = payload["intents"][0]
    for key in ("client_order_id", "symbol", "action", "side", "qty", "target_notional",
                "order_type", "conviction", "gate_allowed", "gate_reasons", "allow_place",
                "rationale"):
        assert key in intent, f"missing intent key {key}"
    assert intent["symbol"] == "AAPL"
    assert intent["action"] == "BUY"
    assert intent["side"] == "buy"
    assert intent["qty"] is None                      # BUY: qty resolved by agent at review
    # 100_000 * 0.05 = 5_000 desired, capped by the 1_000 fixture ceiling.
    assert intent["target_notional"] == 1_000.0
    assert intent["allow_place"] is False             # review mode


def test_buy_notional_capped_by_max_notional():
    """target_notional is capped by the configured per-order notional ceiling."""
    # equity 100k * pct 0.05 = 5_000 desired, but cap is 1_000 (from fixture).
    payload = build_execution_queue(_one_buy_run(pct=0.05), mode="review")
    assert payload["intents"][0]["target_notional"] == 1_000.0


# ---------------------------------------------------------------------------
# Below-threshold recommendations do not enter the queue
# ---------------------------------------------------------------------------

def test_low_conviction_recommendation_excluded():
    """A recommendation below CONFIG['min_conviction'] never becomes an intent."""
    run = _one_buy_run(conviction=0.50)  # below default 0.85
    payload = build_execution_queue(run, mode="live")
    assert payload["n_intents"] == 0
    assert payload["intents"] == []
