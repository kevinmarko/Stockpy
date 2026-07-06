"""
tests/test_robinhood_e2e.py
===========================
End-to-end, fully-OFFLINE integration test for the human-in-the-loop Robinhood
execution loop.

This is the regression net that proves the *whole* execution path is safe
BEFORE any real capital is deployed:

    engine.advisory.Recommendation
        → execution.queue_builder.build_execution_queue / emit_execution_queue
            → output/execution_queue.json  (gated, dry-run, allow_place computed here)
                → the `robinhood-execution` skill
                    → Robinhood Trading MCP write tools

The production side of this loop is:
  * `execution/queue_builder.py` — emits the gated queue (never touches a broker).
  * `.claude/skills/robinhood-execution/SKILL.md` — the ONLY actor allowed to call
    the MCP `review_equity_order` / `place_equity_order` tools.

The skill is an LLM procedure, not importable Python, so its safety invariants
cannot be unit-tested directly. This file closes that gap by supplying:

  1. ``_MockRobinhoodMCP`` — a plain-Python stand-in for the Robinhood Trading
     MCP server. It records every tool call and, for ``place_equity_order``,
     records a placed order and returns a fake order id. No network, ever.

  2. ``run_execution_skill(...)`` — a **skill-equivalent driver**: a pure Python
     function that follows the SKILL.md procedure step-for-step (load queue →
     hard-stop checks → preview EVERY intent via ``review_equity_order`` → mode
     gate → in ``live``, for each ``allow_place: true`` intent ask a (mocked)
     confirmation → ``place_equity_order`` → append a receipt line). This mirrors
     what the LLM does at runtime, turning the skill's prose invariants into
     assertable behaviour.

Every safety invariant from the SKILL.md is asserted end-to-end below.

Nothing outside this file is edited — this is purely the regression net around
the production modules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest

from engine.advisory import Recommendation
from execution.kill_switch import GlobalKillSwitch
from execution.queue_builder import build_execution_queue, emit_execution_queue
from execution.receipts_store import (
    already_placed,
    append_placed,
    make_dedup_key,
)


# ---------------------------------------------------------------------------
# Fixed clock inside NYSE regular trading hours so PreTradeRiskGate.market_hours
# passes deterministically (15:00 UTC == 10:00 EST / 11:00 EDT, both in RTH),
# and so idempotency dedup keys (YYYY-MM-DD) are stable across runs in the test.
# ---------------------------------------------------------------------------
FIXED_NOW = datetime(2026, 7, 6, 15, 0, 0, tzinfo=timezone.utc)  # a Monday, in RTH


# ===========================================================================
# Lightweight advisory-side test doubles (mirror the real dataclass shapes)
# ===========================================================================

@dataclass
class _FakePosition:
    """Mirror of data.robinhood_portfolio.PortfolioPosition (fields used by the
    queue builder / risk context)."""
    symbol: str
    quantity: float
    average_cost: float
    current_price: float
    market_value: float
    unrealized_pl: float = 0.0


@dataclass
class _FakeSnapshot:
    """Mirror of data.robinhood_portfolio.AccountSnapshot (fields the queue
    builder reads: positions dict, buying_power, total_equity)."""
    positions: Dict[str, _FakePosition]
    buying_power: float
    total_equity: float


@dataclass
class _FakeRunResult:
    """Mirror of main.RunResult (only .snapshot + .recommendations are read)."""
    snapshot: Any
    recommendations: List[Recommendation]


def _rec(symbol: str, action: str, *, conviction: float, pct: float = 0.05,
         strategy: str = "test-driver") -> Recommendation:
    """Build a minimal but valid Recommendation for the queue builder."""
    return Recommendation(
        symbol=symbol,
        action=action,  # type: ignore[arg-type]
        strategy=strategy,
        conviction=conviction,
        rationale=f"{action} {symbol} (synthetic e2e fixture)",
        suggested_position_pct=pct,
        forecast=None,
        key_indicators={},
        data_quality="OK",
    )


# ===========================================================================
# Mock Robinhood Trading MCP
# ===========================================================================

class _MockRobinhoodMCP:
    """A plain-Python stand-in for the Robinhood Trading MCP server.

    Exposes exactly the tools the `robinhood-execution` skill uses. Every call
    is recorded in ``self.calls`` (a list of ``(tool_name, kwargs)`` tuples) so
    tests can assert *what* was invoked and *how many times*. ``place_equity_order``
    additionally records a placed order and returns a fake order id.

    No network I/O — this never touches the real MCP.
    """

    #: Which single account is the sanctioned "Agentic" execution account.
    AGENTIC_ACCOUNT_ID = "AGENTIC-TEST-0001"

    def __init__(self, *, quote_prices: Optional[Dict[str, float]] = None,
                 buying_power: float = 100_000.0) -> None:
        self.calls: List[tuple[str, Dict[str, Any]]] = []
        self.placed_orders: List[Dict[str, Any]] = []
        self.reviewed_orders: List[Dict[str, Any]] = []
        self._quote_prices = quote_prices or {}
        self._buying_power = buying_power
        self._order_seq = 0

    # -- read tools ---------------------------------------------------------

    def get_accounts(self) -> Dict[str, Any]:
        self.calls.append(("get_accounts", {}))
        return {
            "accounts": [
                {"account_id": self.AGENTIC_ACCOUNT_ID, "type": "agentic",
                 "buying_power": self._buying_power},
            ]
        }

    def get_portfolio(self) -> Dict[str, Any]:
        self.calls.append(("get_portfolio", {}))
        return {"account_id": self.AGENTIC_ACCOUNT_ID,
                "buying_power": self._buying_power}

    def get_equity_positions(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(("get_equity_positions", kwargs))
        return {"positions": []}

    def get_equity_quotes(self, symbol: str, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(("get_equity_quotes", {"symbol": symbol, **kwargs}))
        price = self._quote_prices.get(symbol.upper())
        return {"symbol": symbol.upper(), "last_trade_price": price}

    def get_equity_orders(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(("get_equity_orders", kwargs))
        return {"orders": list(self.placed_orders)}

    def review_equity_order(self, **kwargs: Any) -> Dict[str, Any]:
        """Pre-trade preview. NEVER changes state — recorded only."""
        self.calls.append(("review_equity_order", kwargs))
        self.reviewed_orders.append(dict(kwargs))
        return {"ok": True, "warnings": [], "preview": dict(kwargs)}

    # -- write tool ---------------------------------------------------------

    def place_equity_order(self, **kwargs: Any) -> Dict[str, Any]:
        """Place an order. Records it and returns a fake order id."""
        self.calls.append(("place_equity_order", kwargs))
        self._order_seq += 1
        order_id = f"mcp-order-{self._order_seq:04d}"
        self.placed_orders.append({"order_id": order_id, **kwargs})
        return {"order_id": order_id, "state": "confirmed", **kwargs}

    # -- test helpers -------------------------------------------------------

    def call_count(self, tool: str) -> int:
        return sum(1 for name, _ in self.calls if name == tool)


# ===========================================================================
# Skill-equivalent driver — a Python port of SKILL.md's procedure
# ===========================================================================

_RECEIPTS_FILENAME = "execution_receipts.jsonl"
_PLACED_LEDGER_FILENAME = "execution_placed.jsonl"


class SkillAbort(RuntimeError):
    """Raised by the driver when a hard-stop refuses the whole run."""


def _append_receipt(output_dir: Path, receipt: Dict[str, Any]) -> None:
    """Append one JSON line with the documented receipt schema."""
    path = output_dir / _RECEIPTS_FILENAME
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt) + "\n")


def run_execution_skill(
    output_dir: Path,
    mcp: _MockRobinhoodMCP,
    *,
    confirm: Callable[[Dict[str, Any]], str] = lambda intent: "place",
    now: datetime = FIXED_NOW,
    kill_switch: Optional[GlobalKillSwitch] = None,
    stale_after_minutes: float = 30.0,
) -> Dict[str, Any]:
    """Pure-Python port of ``.claude/skills/robinhood-execution/SKILL.md``.

    Follows the SKILL procedure so the prose safety invariants become testable:

      1. Load ``output/execution_queue.json``.
      2. Hard-stop checks: kill switch (file OR queue flag), mode==off, staleness,
         agentic-account presence.
      3. Preview EVERY intent via ``review_equity_order`` (always).
      4. Mode gate: ``review`` stops after previews; ``live`` continues.
      5. Live only, per ``allow_place: true`` intent: re-check kill switch, ask
         ``confirm(intent)`` ('place' | 'skip' | 'stop'), enforce the notional
         cap, honour idempotency, then ``place_equity_order``.
      6. Append a receipt line per handled intent.

    Returns a small summary dict for assertions:
        {"previewed", "placed", "skipped", "aborted", "reason"}.
    """
    kill_switch = kill_switch or GlobalKillSwitch()
    queue_path = output_dir / "execution_queue.json"

    summary = {"previewed": 0, "placed": 0, "skipped": 0,
               "aborted": False, "reason": ""}

    # -- Step 1: load state ------------------------------------------------
    if not queue_path.exists():
        raise SkillAbort("execution_queue.json missing (mode=off or pipeline not run)")
    payload = json.loads(queue_path.read_text(encoding="utf-8"))
    mode = payload.get("mode", "off")
    intents = payload.get("intents", []) or []
    max_notional = float(payload.get("max_notional_per_order", 0.0) or 0.0)

    # -- Step 1/hard stops -------------------------------------------------
    # Kill switch: either the sentinel file exists OR the queue snapshot flags it.
    if kill_switch.is_active() or payload.get("kill_switch_active"):
        summary["aborted"] = True
        summary["reason"] = "kill_switch_active"
        raise SkillAbort("kill switch active — refusing all placement")

    if mode == "off":
        summary["reason"] = "mode_off"
        return summary  # nothing to do

    # Staleness hard-stop.
    generated_at = payload.get("generated_at")
    if generated_at:
        try:
            gen = datetime.fromisoformat(generated_at)
            if gen.tzinfo is None:
                gen = gen.replace(tzinfo=timezone.utc)
            age_min = (now - gen).total_seconds() / 60.0
            if age_min > stale_after_minutes:
                summary["aborted"] = True
                summary["reason"] = "stale_queue"
                raise SkillAbort(f"queue is stale ({age_min:.0f} min old)")
        except (ValueError, TypeError):
            pass  # unparseable timestamp — don't crash the driver

    # -- Step 2: confirm the agentic account -------------------------------
    accounts = mcp.get_accounts().get("accounts", [])
    agentic = [a for a in accounts if a.get("type") == "agentic"]
    if not agentic:
        summary["aborted"] = True
        summary["reason"] = "no_agentic_account"
        raise SkillAbort("no dedicated Agentic account — refusing to place")
    mcp.get_portfolio()

    # -- Step 3: preview EVERY intent (always) -----------------------------
    for intent in intents:
        symbol = intent["symbol"]
        side = intent["side"]
        order_type = intent.get("order_type", "market")

        # Resolve quantity for the preview (SELL carries qty; BUY carries a
        # target_notional the agent turns into shares via a live quote).
        qty = intent.get("qty")
        price = None
        if qty is None:  # BUY branch — compute qty from a live MCP quote
            q = mcp.get_equity_quotes(symbol)
            price = q.get("last_trade_price")
            target_notional = float(intent.get("target_notional") or 0.0)
            if price and price > 0:
                qty = int(target_notional // price)  # floor
            else:
                qty = 0

        mcp.review_equity_order(symbol=symbol, side=side,
                                order_type=order_type, quantity=qty)
        summary["previewed"] += 1

        # -- Step 4: mode gate ---------------------------------------------
        if mode == "review":
            # STOP after previews — never place in review mode.
            _append_receipt(output_dir, {
                "ts": now.isoformat(), "symbol": symbol, "side": side,
                "qty": qty, "action": "reviewed", "mcp_order_id": None,
                "note": "review-mode preview only",
            })
            continue

        # -- live mode -----------------------------------------------------
        if not intent.get("allow_place"):
            # allow_place:false → preview-only; never placed.
            _append_receipt(output_dir, {
                "ts": now.isoformat(), "symbol": symbol, "side": side,
                "qty": qty, "action": "reviewed", "mcp_order_id": None,
                "note": "allow_place=false; " + ",".join(intent.get("gate_reasons", [])),
            })
            summary["skipped"] += 1
            continue

        # -- Step 5: place (live, allow_place=true, human-confirmed) --------
        # a. re-check the kill switch immediately before placement.
        if kill_switch.is_active():
            summary["aborted"] = True
            summary["reason"] = "kill_switch_active_mid_run"
            raise SkillAbort("kill switch activated mid-run — aborting")

        # Notional cap enforcement (BUY: qty*price must be <= cap).
        if max_notional <= 0:
            _append_receipt(output_dir, {
                "ts": now.isoformat(), "symbol": symbol, "side": side,
                "qty": qty, "action": "reviewed", "mcp_order_id": None,
                "note": "notional cap unset — preview only",
            })
            summary["skipped"] += 1
            continue
        if price is None and qty is not None:
            # SELL / already-priced path — use target_notional as the estimate.
            est_notional = float(intent.get("target_notional") or 0.0)
        else:
            est_notional = (price or 0.0) * (qty or 0)
        if intent["side"] == "buy" and est_notional > max_notional + 1e-9:
            # Over cap — refuse to place (do NOT silently clamp; the queue owns
            # sizing). Preview stands, no placement.
            _append_receipt(output_dir, {
                "ts": now.isoformat(), "symbol": symbol, "side": side,
                "qty": qty, "action": "skipped", "mcp_order_id": None,
                "note": f"over notional cap (${est_notional:,.0f} > ${max_notional:,.0f})",
            })
            summary["skipped"] += 1
            continue

        # b. idempotency — never double-place the same symbol/side on the same day.
        if already_placed(symbol, side, output_dir, on_date=now):
            _append_receipt(output_dir, {
                "ts": now.isoformat(), "symbol": symbol, "side": side,
                "qty": qty, "action": "skipped", "mcp_order_id": None,
                "note": "already placed today (idempotent skip)",
            })
            summary["skipped"] += 1
            continue

        # c. explicit human confirmation per order.
        decision = confirm(intent)
        if decision == "stop":
            summary["reason"] = "operator_stop"
            break
        if decision != "place":
            _append_receipt(output_dir, {
                "ts": now.isoformat(), "symbol": symbol, "side": side,
                "qty": qty, "action": "skipped", "mcp_order_id": None,
                "note": "operator skipped",
            })
            summary["skipped"] += 1
            continue

        # d. place.
        result = mcp.place_equity_order(symbol=symbol, side=side,
                                        order_type=order_type, quantity=qty)
        order_id = result.get("order_id")
        append_placed(
            {"ts": now.isoformat(), "symbol": symbol, "side": side,
             "qty": qty, "target_notional": intent.get("target_notional"),
             "client_order_id": intent.get("client_order_id"),
             "mcp_order_id": order_id},
            output_dir,
        )
        _append_receipt(output_dir, {
            "ts": now.isoformat(), "symbol": symbol, "side": side,
            "qty": qty, "action": "placed", "mcp_order_id": order_id,
            "note": "placed after human confirmation",
        })
        summary["placed"] += 1

    return summary


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def tmp_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect settings.OUTPUT_DIR and the kill-switch sentinel into tmp_path so
    the test never touches the real output/ directory, and reset provider/kill
    singletons. Also pin the execution mode + notional cap in settings so the
    queue builder's local `from settings import settings` reads the test values.
    """
    out = tmp_path / "output"
    out.mkdir(parents=True, exist_ok=True)

    from settings import settings as _settings
    monkeypatch.setattr(_settings, "OUTPUT_DIR", out, raising=False)
    # The no-arg GlobalKillSwitch() resolves its path from the module constant
    # captured at import time — repoint it at the temp dir.
    import execution.kill_switch as ks_mod
    monkeypatch.setattr(ks_mod, "KILL_SWITCH_FILE", out / "KILL_SWITCH", raising=False)

    # Deterministic gate posture: macro gate off (no macro DTO here) is fine, but
    # ensure market-hours enforcement is on so FIXED_NOW is meaningful.
    # Provide a generous per-order cap and a live-ish default; individual tests
    # override the mode as needed.
    monkeypatch.setattr(_settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 10_000.0, raising=False)
    monkeypatch.setattr(_settings, "ROBINHOOD_EXECUTION_MODE", "live", raising=False)

    return out


@pytest.fixture(autouse=True)
def _clean_kill_switch(tmp_output: Path):
    """Guarantee the kill switch is inactive at the start of every test and
    cleaned up afterwards (defends against cross-test bleed)."""
    ks = GlobalKillSwitch()
    if ks.is_active():
        ks.deactivate()
    yield
    if ks.is_active():
        ks.deactivate()


def _standard_snapshot() -> _FakeSnapshot:
    """An account with one held name (AAPL) and buying power, so BUY and SELL
    intents both have a source of truth."""
    return _FakeSnapshot(
        positions={
            "AAPL": _FakePosition(
                symbol="AAPL", quantity=10.0, average_cost=100.0,
                current_price=150.0, market_value=1500.0, unrealized_pl=500.0,
            ),
        },
        buying_power=50_000.0,
        total_equity=100_000.0,
    )


def _build_and_emit(recs: List[Recommendation], out: Path, *, mode: str) -> Dict[str, Any]:
    """Build the queue payload and write it to disk exactly as main.py does."""
    result = _FakeRunResult(snapshot=_standard_snapshot(), recommendations=recs)
    path = emit_execution_queue(result, mode=mode, output_dir=out, now=FIXED_NOW)
    assert path is not None, "emit_execution_queue returned None in a non-off mode"
    assert path.exists()
    return json.loads(path.read_text(encoding="utf-8"))


# ===========================================================================
# Tests
# ===========================================================================

class TestQueueBuilderShape:
    """Sanity: the queue builder produces the intents the driver consumes."""

    def test_off_mode_writes_nothing(self, tmp_output: Path):
        result = _FakeRunResult(
            snapshot=_standard_snapshot(),
            recommendations=[_rec("AAPL", "SELL", conviction=0.95)],
        )
        path = emit_execution_queue(result, mode="off", output_dir=tmp_output, now=FIXED_NOW)
        assert path is None
        assert not (tmp_output / "execution_queue.json").exists()

    def test_low_conviction_dropped(self, tmp_output: Path):
        # conviction below CONFIG["min_conviction"] (0.85) → not queued.
        payload = _build_and_emit(
            [_rec("MSFT", "BUY", conviction=0.50)], tmp_output, mode="review")
        assert payload["n_intents"] == 0

    def test_hold_not_queued(self, tmp_output: Path):
        payload = _build_and_emit(
            [_rec("AAPL", "HOLD", conviction=0.95)], tmp_output, mode="review")
        assert payload["n_intents"] == 0


class TestInvariant1_ReviewModeNeverPlaces:
    def test_review_mode_previews_all_places_none(self, tmp_output: Path):
        recs = [
            _rec("AAPL", "SELL", conviction=0.95),  # held → sellable
            _rec("MSFT", "BUY", conviction=0.95),   # buyable
        ]
        _build_and_emit(recs, tmp_output, mode="review")

        mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 400.0})
        summary = run_execution_skill(tmp_output, mcp)

        assert summary["placed"] == 0
        assert mcp.call_count("place_equity_order") == 0
        # Every queued intent was previewed.
        assert summary["previewed"] == 2
        assert mcp.call_count("review_equity_order") == 2


class TestInvariant2_AllowPlaceFalseNeverPlaced:
    def test_blocked_intent_previewed_but_not_placed(self, tmp_output: Path, monkeypatch):
        # Force a live-mode intent whose allow_place is structurally false by
        # leaving the per-order notional cap UNSET (0). The builder requires a
        # configured cap for allow_place to be true, so this isolates the
        # "allow_place=false is preview-only" invariant WITHOUT tripping the
        # separate kill-switch flag baked into the payload.
        from settings import settings as _settings
        monkeypatch.setattr(_settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 0.0, raising=False)

        payload = _build_and_emit(
            [_rec("MSFT", "BUY", conviction=0.95)], tmp_output, mode="live")

        assert payload["n_intents"] == 1
        assert payload["kill_switch_active"] is False  # not a kill-switch case
        assert payload["intents"][0]["allow_place"] is False

        mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 400.0})
        summary = run_execution_skill(tmp_output, mcp)

        assert mcp.call_count("review_equity_order") == 1  # previewed
        assert mcp.call_count("place_equity_order") == 0   # never placed
        assert summary["placed"] == 0
        assert summary["skipped"] == 1


class TestInvariant3_KillSwitchAborts:
    def test_active_kill_switch_refuses_all_placement(self, tmp_output: Path):
        # Build a clean, placeable queue in live mode first.
        payload = _build_and_emit(
            [_rec("MSFT", "BUY", conviction=0.95)], tmp_output, mode="live")
        assert payload["intents"][0]["allow_place"] is True

        # Now activate the kill switch and run the driver.
        ks = GlobalKillSwitch()
        ks.activate("e2e test — halt")
        try:
            mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 400.0})
            with pytest.raises(SkillAbort):
                run_execution_skill(tmp_output, mcp)
            assert mcp.call_count("place_equity_order") == 0
        finally:
            ks.deactivate()  # deactivate after, per task spec

        assert ks.is_active() is False

    def test_queue_flag_kill_switch_also_aborts(self, tmp_output: Path):
        # Even without the sentinel file, a queue snapshot that captured
        # kill_switch_active=true must abort (SKILL hard stop).
        payload = _build_and_emit(
            [_rec("MSFT", "BUY", conviction=0.95)], tmp_output, mode="live")
        payload["kill_switch_active"] = True
        (tmp_output / "execution_queue.json").write_text(
            json.dumps(payload), encoding="utf-8")

        mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 400.0})
        with pytest.raises(SkillAbort):
            run_execution_skill(tmp_output, mcp)
        assert mcp.call_count("place_equity_order") == 0


class TestInvariant4_NotionalCapEnforced:
    def test_over_cap_buy_not_placed(self, tmp_output: Path, monkeypatch):
        # target_notional is capped at build time (min(equity*pct, max_notional)),
        # but the DRIVER re-derives qty*price from a LIVE quote and must itself
        # refuse an over-cap placement. Simulate a live price spike between build
        # and execution that pushes qty*price above the cap.
        from settings import settings as _settings
        # Cap = $1,000; equity*pct = 100_000*0.05 = 5_000 → capped to 1_000.
        monkeypatch.setattr(_settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 1_000.0, raising=False)

        payload = _build_and_emit(
            [_rec("MSFT", "BUY", conviction=0.95, pct=0.05)], tmp_output, mode="live")
        assert payload["max_notional_per_order"] == 1_000.0
        intent = payload["intents"][0]
        assert intent["allow_place"] is True
        assert intent["target_notional"] == 1_000.0  # capped at build

        # Live price spikes so that floor(1000/600)=1 share * $600 = $600 (<cap) — OK.
        mcp_ok = _MockRobinhoodMCP(quote_prices={"MSFT": 600.0})
        summary_ok = run_execution_skill(tmp_output, mcp_ok)
        assert summary_ok["placed"] == 1  # under cap, placed
        assert mcp_ok.call_count("place_equity_order") == 1
        placed = mcp_ok.placed_orders[0]
        assert placed["quantity"] * 600.0 <= 1_000.0 + 1e-9  # never over cap

        # Fresh run: a driver that would compute an over-cap notional must refuse.
        # We simulate this by forging a queue whose target_notional exceeds the
        # cap AND a quote that yields exactly 1 share priced above the cap.
        payload["intents"][0]["target_notional"] = 5_000.0  # someone tampered
        (tmp_output / "execution_queue.json").write_text(json.dumps(payload), encoding="utf-8")
        # Clear the placed-ledger so idempotency doesn't mask the result.
        (tmp_output / _PLACED_LEDGER_FILENAME).unlink(missing_ok=True)
        (tmp_output / _RECEIPTS_FILENAME).unlink(missing_ok=True)

        mcp_over = _MockRobinhoodMCP(quote_prices={"MSFT": 1_500.0})  # 1 share = $1,500 > $1,000
        summary_over = run_execution_skill(tmp_output, mcp_over)
        assert summary_over["placed"] == 0
        assert mcp_over.call_count("place_equity_order") == 0
        # It WAS previewed (preview always happens before the cap check).
        assert mcp_over.call_count("review_equity_order") == 1


class TestInvariant5_NoDoublePlace:
    def test_idempotent_across_two_runs_same_day(self, tmp_output: Path):
        payload = _build_and_emit(
            [_rec("MSFT", "BUY", conviction=0.95),
             _rec("AAPL", "SELL", conviction=0.95)],
            tmp_output, mode="live")
        for i in payload["intents"]:
            assert i["allow_place"] is True

        quotes = {"MSFT": 400.0}
        mcp1 = _MockRobinhoodMCP(quote_prices=quotes)
        summary1 = run_execution_skill(tmp_output, mcp1)
        assert summary1["placed"] == 2
        assert mcp1.call_count("place_equity_order") == 2

        # Second run on the SAME queue, SAME UTC day → zero new placements.
        mcp2 = _MockRobinhoodMCP(quote_prices=quotes)
        summary2 = run_execution_skill(tmp_output, mcp2)
        assert summary2["placed"] == 0
        assert mcp2.call_count("place_equity_order") == 0
        # Both intents still previewed on the second pass (preview is always safe).
        assert mcp2.call_count("review_equity_order") == 2

        # The placed-ledger has exactly two unique dedup keys.
        ledger = tmp_output / _PLACED_LEDGER_FILENAME
        keys = {json.loads(l)["dedup_key"]
                for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()}
        assert keys == {
            make_dedup_key("MSFT", "buy", FIXED_NOW),
            make_dedup_key("AAPL", "sell", FIXED_NOW),
        }


class TestInvariant6_ReceiptsWritten:
    def test_live_run_writes_documented_receipt_schema(self, tmp_output: Path):
        _build_and_emit(
            [_rec("MSFT", "BUY", conviction=0.95),
             _rec("AAPL", "SELL", conviction=0.95)],
            tmp_output, mode="live")

        mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 400.0})
        run_execution_skill(tmp_output, mcp)

        receipts_path = tmp_output / _RECEIPTS_FILENAME
        assert receipts_path.exists()
        lines = [l for l in receipts_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 2  # one line per handled intent

        required = {"ts", "symbol", "side", "qty", "action", "mcp_order_id", "note"}
        for line in lines:
            rec = json.loads(line)
            assert required.issubset(rec.keys()), f"missing keys in receipt: {rec}"
            assert rec["action"] in ("reviewed", "placed", "skipped")

        placed = [json.loads(l) for l in lines if json.loads(l)["action"] == "placed"]
        assert len(placed) == 2
        for rec in placed:
            assert rec["mcp_order_id"], "placed receipt must carry an mcp_order_id"

    def test_review_mode_receipts_are_reviewed_only(self, tmp_output: Path):
        _build_and_emit(
            [_rec("MSFT", "BUY", conviction=0.95)], tmp_output, mode="review")
        mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 400.0})
        run_execution_skill(tmp_output, mcp)

        lines = [json.loads(l) for l in
                 (tmp_output / _RECEIPTS_FILENAME).read_text(encoding="utf-8").splitlines()
                 if l.strip()]
        assert len(lines) == 1
        assert lines[0]["action"] == "reviewed"
        assert lines[0]["mcp_order_id"] is None


class TestOperatorConfirmationGate:
    """The per-order human confirmation is load-bearing (SKILL invariant)."""

    def test_operator_skip_prevents_placement(self, tmp_output: Path):
        _build_and_emit(
            [_rec("MSFT", "BUY", conviction=0.95)], tmp_output, mode="live")
        mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 400.0})
        summary = run_execution_skill(tmp_output, mcp, confirm=lambda intent: "skip")
        assert summary["placed"] == 0
        assert mcp.call_count("place_equity_order") == 0
        assert mcp.call_count("review_equity_order") == 1  # still previewed

    def test_operator_stop_halts_remaining_intents(self, tmp_output: Path):
        _build_and_emit(
            [_rec("MSFT", "BUY", conviction=0.95),
             _rec("AAPL", "SELL", conviction=0.95)],
            tmp_output, mode="live")
        mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 400.0})
        summary = run_execution_skill(tmp_output, mcp, confirm=lambda intent: "stop")
        assert summary["placed"] == 0
        assert mcp.call_count("place_equity_order") == 0


class TestStaleQueueHardStop:
    def test_stale_queue_refuses(self, tmp_output: Path):
        _build_and_emit(
            [_rec("MSFT", "BUY", conviction=0.95)], tmp_output, mode="live")
        # Run the driver with a clock 45 minutes after the queue was generated.
        later = FIXED_NOW.replace(minute=45)
        mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 400.0})
        with pytest.raises(SkillAbort):
            run_execution_skill(tmp_output, mcp, now=later)
        assert mcp.call_count("place_equity_order") == 0
        assert mcp.call_count("review_equity_order") == 0  # aborted before previews
