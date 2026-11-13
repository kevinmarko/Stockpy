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
from execution import agentic_preflight as ap
from execution.kill_switch import GlobalKillSwitch
from execution.queue_builder import build_execution_queue, emit_execution_queue
from execution.receipts_store import (
    already_placed,
    append_approval_event,
    append_placed,
    latest_approval_states,
    make_dedup_key,
    make_ref_id,
    read_placed_ledger,
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
                 buying_power: float = 100_000.0,
                 approvals_required: bool = False,
                 order_checks: Optional[Dict[str, List[str]]] = None) -> None:
        self.calls: List[tuple[str, Dict[str, Any]]] = []
        self.placed_orders: List[Dict[str, Any]] = []
        self.reviewed_orders: List[Dict[str, Any]] = []
        # Robinhood trade-approval mode: place_equity_order only CREATES an
        # approval request the customer approves in the app; nothing executes.
        self.approvals_required = approvals_required
        self._order_checks = order_checks or {}
        self.approval_requests: Dict[str, Dict[str, Any]] = {}  # approval_id -> request
        self._by_ref_id: Dict[str, str] = {}                     # ref_id -> approval_id
        # AAPL is the held name in _standard_snapshot (10 sh @ $150): give it a
        # live quote by default so the Portfolio & Risk preflight can size it.
        self._quote_prices = {"AAPL": 150.0, **(quote_prices or {})}
        self.held: Dict[str, float] = {"AAPL": 10.0}
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

    # -- real-shape payloads for the Portfolio & Risk preflight --------------
    # (what rh-risk-manager saves verbatim from get_portfolio /
    # get_equity_positions / get_equity_quotes / get_trade_approvals)

    def portfolio_payload(self) -> Dict[str, Any]:
        self.calls.append(("get_portfolio", {"shape": "real"}))
        equity = sum(q * self._quote_prices.get(s, 0.0) for s, q in self.held.items())
        return {"data": {"total_value": str(self._buying_power + equity), "equity_value": str(equity),
                         "cash": str(self._buying_power),
                         "buying_power": {"buying_power": str(self._buying_power)}}}

    def positions_payload(self) -> Dict[str, Any]:
        self.calls.append(("get_equity_positions", {"shape": "real"}))
        return {"data": {"positions": [
            {"symbol": s, "quantity": str(q), "shares_available_for_sells": str(q),
             # cost basis at the live quote: held flat, so portfolio heat is measurable and 0
             "average_buy_price": str(self._quote_prices.get(s, 1.0))} for s, q in self.held.items()]}}

    def quotes_payload(self, symbols: List[str]) -> Dict[str, Any]:
        self.calls.append(("get_equity_quotes", {"symbols": symbols}))
        return {"data": {"results": [
            {"quote": {"symbol": s, "last_trade_price": str(self._quote_prices[s]), "state": "active",
                       "has_traded": True}} for s in symbols if s in self._quote_prices]}}

    def approvals_payload(self) -> Dict[str, Any]:
        self.calls.append(("get_trade_approvals", {"shape": "real"}))
        return {"data": {"approvals": [
            {"approval_id": a, "symbol": r["order"]["symbol"], "side": r["order"]["side"]}
            for a, r in self.approval_requests.items() if r["state"] == "pending"]}}

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
        checks = list(self._order_checks.get(str(kwargs.get("symbol", "")).upper(), []))
        return {"ok": True, "warnings": [], "preview": dict(kwargs),
                "customer_approval_required": self.approvals_required,
                "order_checks": checks}

    def get_trade_approval_setting(self, account_number: str) -> Dict[str, Any]:
        self.calls.append(("get_trade_approval_setting", {"account_number": account_number}))
        return {"setting": {"account_number": account_number,
                            "human_must_approve_trades": self.approvals_required}}

    def get_trade_approvals(self, account_number: str,
                            approval_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        self.calls.append(("get_trade_approvals",
                           {"account_number": account_number, "approval_ids": approval_ids}))
        ids = approval_ids if approval_ids is not None else [
            a for a, r in self.approval_requests.items() if r["state"] == "pending"]
        return {"approvals": [
            {"approval_id": a, "state": self.approval_requests[a]["state"]}
            for a in ids if a in self.approval_requests]}

    def customer_acts(self, approval_id: str, state: str) -> None:
        """Test helper: the operator approves/declines in the app (or it expires)."""
        req = self.approval_requests[approval_id]
        req["state"] = state
        if state == "approved":
            self.placed_orders.append({"order_id": f"filled-{approval_id}", **req["order"]})

    # -- write tool ---------------------------------------------------------

    def place_equity_order(self, **kwargs: Any) -> Dict[str, Any]:
        """Place an order. Records it and returns a fake order id."""
        self.calls.append(("place_equity_order", kwargs))
        if self.approvals_required:
            ref_id = kwargs.get("ref_id")
            if ref_id and ref_id in self._by_ref_id:   # Robinhood dedups on ref_id
                aid = self._by_ref_id[ref_id]
                return {"approval_id": aid, "state": self.approval_requests[aid]["state"]}
            self._order_seq += 1
            aid = f"approval-{self._order_seq:04d}"
            self.approval_requests[aid] = {"state": "pending", "order": dict(kwargs)}
            if ref_id:
                self._by_ref_id[ref_id] = aid
            return {"approval_id": aid, "state": "pending"}
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
    use_preflight: bool = True,
) -> Dict[str, Any]:
    """Pure-Python port of ``.claude/skills/robinhood-execution/SKILL.md``.

    ``use_preflight=False`` simulates step 4b finding no usable preflight:
    every order then takes the in-chat path.

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
               "aborted": False, "reason": "",
               "submitted_for_approval": 0, "confirm_asked": 0}
    submitted_approvals: List[Dict[str, Any]] = []

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

    # -- Step 4b: Portfolio & Risk preflight (live only), the REAL code ------
    verdicts: Dict[str, Any] = {}
    preflight_ok = False
    if mode == "live" and use_preflight:
        # queue ∪ held symbols, as rh-risk-manager fetches (heat needs held quotes)
        symbols = sorted({i["symbol"] for i in intents} | set(mcp.held))
        approvals, unidentified = ap.approvals_from_mcp(mcp.approvals_payload())
        report = ap.preflight(
            payload,
            ap.AccountState.from_mcp(mcp.portfolio_payload(), mcp.positions_payload()),
            ap.quotes_from_mcp(mcp.quotes_payload(symbols)),
            now=now,
            pending_approvals=approvals,
            unidentified_pending_approvals=unidentified,
            placed_dedup_keys=[r["dedup_key"] for r in read_placed_ledger(output_dir)],
            kill_switch_active=kill_switch.is_active(),
            max_notional=max_notional,
        )
        ap.write_preflight(report, output_dir=output_dir)
        loaded = ap.read_preflight_for_queue(payload, output_dir=output_dir)
        preflight_ok = loaded is not None
        verdicts = {v["client_order_id"]: v for v in (loaded or {}).get("verdicts", [])}
    summary["preflight_used"] = preflight_ok

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

        review = mcp.review_equity_order(symbol=symbol, side=side,
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

        # 4b/5a. a preflight veto is final; a pass re-reviews at ITS qty.
        if preflight_ok:
            verdict = verdicts.get(intent.get("client_order_id"))
            if verdict is None or not verdict.get("passed"):
                reasons = ",".join((verdict or {}).get("reasons", []) or ["no_verdict"])
                _append_receipt(output_dir, {
                    "ts": now.isoformat(), "symbol": symbol, "side": side,
                    "qty": qty, "action": "skipped", "mcp_order_id": None,
                    "note": f"preflight veto: {reasons}",
                })
                summary["skipped"] += 1
                summary.setdefault("preflight_vetoed", []).append(symbol)
                continue
            qty = verdict["qty"]
            review = mcp.review_equity_order(symbol=symbol, side=side, order_type=order_type,
                                             quantity=qty, limit_price=verdict.get("limit_price"))

        # c. confirmation path, decided by THIS order's review response:
        #    approval path → the operator's tap in the app is the confirmation;
        #    anything else (incl. no usable preflight) → in-chat "place / skip / stop".
        approval_path = (preflight_ok
                         and review.get("customer_approval_required") is True
                         and not review.get("order_checks"))
        if approval_path:
            decision = "place"
        else:
            summary["confirm_asked"] += 1
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

        # d. place, with a deterministic ref_id (never random).
        ref_id = make_ref_id(intent.get("client_order_id"))
        if ref_id is None:
            _append_receipt(output_dir, {
                "ts": now.isoformat(), "symbol": symbol, "side": side,
                "qty": qty, "action": "skipped", "mcp_order_id": None,
                "note": "blank client_order_id — no deterministic ref_id",
            })
            summary["skipped"] += 1
            continue
        result = mcp.place_equity_order(symbol=symbol, side=side,
                                        order_type=order_type, quantity=qty,
                                        ref_id=ref_id)
        order_id = result.get("order_id")
        approval_id = result.get("approval_id")
        append_placed(
            {"ts": now.isoformat(), "symbol": symbol, "side": side,
             "qty": qty, "target_notional": intent.get("target_notional"),
             "client_order_id": intent.get("client_order_id"),
             "mcp_order_id": order_id, "approval_id": approval_id,
             "ref_id": ref_id},
            output_dir,
        )
        if approval_id:
            append_approval_event({"ts": now.isoformat(), "approval_id": approval_id,
                                   "state": result.get("state", "pending"),
                                   "symbol": symbol, "side": side, "ref_id": ref_id},
                                  output_dir)
            submitted_approvals.append({"approval_id": approval_id,
                                        "symbol": symbol, "side": side})
            summary["submitted_for_approval"] += 1
        _append_receipt(output_dir, {
            "ts": now.isoformat(), "symbol": symbol, "side": side,
            "qty": qty, "action": "placed", "mcp_order_id": order_id,
            "note": "placed after human confirmation",
        })
        summary["placed"] += 1

    # -- Step 6: track approvals (fresh call; record every observed state) ---
    if submitted_approvals:
        ids = [a["approval_id"] for a in submitted_approvals]
        meta = {a["approval_id"]: a for a in submitted_approvals}
        resp = mcp.get_trade_approvals(account_number=_MockRobinhoodMCP.AGENTIC_ACCOUNT_ID,
                                       approval_ids=ids)
        for appr in resp.get("approvals", []):
            m = meta.get(appr["approval_id"], {})
            append_approval_event({"ts": now.isoformat(), "approval_id": appr["approval_id"],
                                   "state": appr.get("state"), "symbol": m.get("symbol"),
                                   "side": m.get("side")}, output_dir)

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
        # previewed in step 3, then re-reviewed at the preflight's qty (step 5a)
        assert mcp.call_count("review_equity_order") == 2

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

class TestTradeApprovalMode:
    """Robinhood trade-approval mode (four-agent plan, Phase 1): with approvals
    on, place_equity_order only creates a request the operator approves on
    their phone, so the in-chat confirmation is skipped — but only per order,
    and only when Robinhood's own review response says so."""

    def _two_buys(self, out: Path) -> None:
        _build_and_emit([_rec("MSFT", "BUY", conviction=0.95),
                         _rec("NVDA", "BUY", conviction=0.95)], out, mode="live")

    def test_approval_path_submits_without_in_chat_confirm(self, tmp_output: Path):
        self._two_buys(tmp_output)
        asked: List[str] = []
        mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 400.0, "NVDA": 100.0},
                                approvals_required=True)
        summary = run_execution_skill(
            tmp_output, mcp, confirm=lambda i: asked.append(i["symbol"]) or "skip")
        assert asked == []                                  # phone tap replaces chat
        assert summary["submitted_for_approval"] == 2
        assert mcp.placed_orders == []                      # nothing EXECUTED
        assert len(mcp.approval_requests) == 2
        ledger = read_placed_ledger(tmp_output)
        assert {r["approval_id"] for r in ledger} == set(mcp.approval_requests)
        states = latest_approval_states(tmp_output)
        assert {r["state"] for r in states.values()} == {"pending"}
        tracking = [kw for name, kw in mcp.calls
                    if name == "get_trade_approvals" and kw.get("approval_ids")]
        assert len(tracking) == 1                           # tracked with a fresh call

    def test_every_submission_carries_deterministic_ref_id(self, tmp_output: Path):
        payload = _build_and_emit([_rec("MSFT", "BUY", conviction=0.95)], tmp_output, mode="live")
        mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 400.0}, approvals_required=True)
        run_execution_skill(tmp_output, mcp)
        (_, kwargs), = [c for c in mcp.calls if c[0] == "place_equity_order"]
        expected = make_ref_id(payload["intents"][0]["client_order_id"])
        assert expected is not None and kwargs["ref_id"] == expected

    def test_order_checks_fall_back_to_in_chat_confirm(self, tmp_output: Path):
        self._two_buys(tmp_output)
        asked: List[str] = []
        mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 400.0, "NVDA": 100.0},
                                approvals_required=True,
                                order_checks={"NVDA": ["pattern_day_trader_warning"]})
        summary = run_execution_skill(
            tmp_output, mcp, confirm=lambda i: asked.append(i["symbol"]) or "skip")
        assert asked == ["NVDA"]                 # only the flagged order asks in chat
        assert summary["submitted_for_approval"] == 1
        assert summary["skipped"] == 1

    def test_approvals_off_keeps_legacy_in_chat_gate(self, tmp_output: Path):
        self._two_buys(tmp_output)
        asked: List[str] = []
        mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 400.0, "NVDA": 100.0},
                                approvals_required=False)
        summary = run_execution_skill(
            tmp_output, mcp, confirm=lambda i: asked.append(i["symbol"]) or "skip")
        assert sorted(asked) == ["MSFT", "NVDA"]
        assert mcp.call_count("place_equity_order") == 0
        assert summary["skipped"] == 2

    def test_review_mode_never_submits_even_with_approvals_on(self, tmp_output: Path):
        _build_and_emit([_rec("MSFT", "BUY", conviction=0.95)], tmp_output, mode="review")
        mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 400.0}, approvals_required=True)
        run_execution_skill(tmp_output, mcp)
        assert mcp.call_count("place_equity_order") == 0
        assert mcp.approval_requests == {}

    def test_declined_order_not_resubmitted_same_day(self, tmp_output: Path):
        _build_and_emit([_rec("MSFT", "BUY", conviction=0.95)], tmp_output, mode="live")
        mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 400.0}, approvals_required=True)
        run_execution_skill(tmp_output, mcp)
        (aid,) = mcp.approval_requests
        mcp.customer_acts(aid, "declined")

        summary2 = run_execution_skill(tmp_output, mcp)
        assert summary2["submitted_for_approval"] == 0
        assert len(mcp.approval_requests) == 1
        assert mcp.placed_orders == []

    def test_lost_ledger_write_caught_by_preflight_then_by_ref_id(self, tmp_output: Path):
        """Belt and braces for a lost ledger line: the preflight sees the still-
        pending approval and vetoes the duplicate; and even with no preflight,
        the same intent re-sends the same ref_id and Robinhood returns the SAME
        approval instead of creating a second one."""
        _build_and_emit([_rec("MSFT", "BUY", conviction=0.95)], tmp_output, mode="live")
        mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 400.0}, approvals_required=True)
        run_execution_skill(tmp_output, mcp)
        (tmp_output / _PLACED_LEDGER_FILENAME).unlink()

        summary = run_execution_skill(tmp_output, mcp)
        assert summary["preflight_vetoed"] == ["MSFT"]
        assert mcp.call_count("place_equity_order") == 1

        run_execution_skill(tmp_output, mcp, use_preflight=False)   # in-chat path, confirm=place
        assert mcp.call_count("place_equity_order") == 2
        assert len(mcp.approval_requests) == 1


class TestPreflightIntegration:
    """Phase 2: the Trader honors the Portfolio & Risk preflight (step 4b)."""

    def test_cumulative_buying_power_veto_is_final_even_if_operator_says_place(self, tmp_output: Path):
        # Each BUY alone fits the cash; together they don't. The preflight keeps
        # the higher-conviction one and vetoes the other — no confirm can undo it.
        _build_and_emit([_rec("MSFT", "BUY", conviction=0.99, pct=0.01),
                         _rec("NVDA", "BUY", conviction=0.90, pct=0.01)], tmp_output, mode="live")
        mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 100.0, "NVDA": 100.0}, buying_power=1_000.0)
        mcp.held = {}
        summary = run_execution_skill(tmp_output, mcp, confirm=lambda i: "place")
        assert summary["preflight_used"] is True
        assert summary["preflight_vetoed"] == ["NVDA"]
        assert [o["symbol"] for o in mcp.placed_orders] == ["MSFT"]
        receipts = [json.loads(l) for l in (tmp_output / _RECEIPTS_FILENAME).read_text().splitlines()]
        assert any("buying_power_exhausted" in (r.get("note") or "") for r in receipts)

    def test_places_at_the_preflight_quantity(self, tmp_output: Path):
        _build_and_emit([_rec("MSFT", "BUY", conviction=0.95)], tmp_output, mode="live")
        mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 400.0})
        run_execution_skill(tmp_output, mcp)
        verdict = json.loads(next((tmp_output / "agentic").glob("*/preflight.json")).read_text())["verdicts"][0]
        (order,) = mcp.placed_orders
        assert order["quantity"] == verdict["qty"]

    def test_no_preflight_means_in_chat_for_every_order_even_with_approvals_on(self, tmp_output: Path):
        _build_and_emit([_rec("MSFT", "BUY", conviction=0.95)], tmp_output, mode="live")
        asked: List[str] = []
        mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 400.0}, approvals_required=True)
        summary = run_execution_skill(tmp_output, mcp, use_preflight=False,
                                      confirm=lambda i: asked.append(i["symbol"]) or "skip")
        assert summary["preflight_used"] is False
        assert asked == ["MSFT"] and mcp.approval_requests == {}

    def test_full_exit_of_shares_already_sold_is_vetoed(self, tmp_output: Path):
        payload = _build_and_emit([_rec("AAPL", "SELL", conviction=0.95, pct=0.0)], tmp_output, mode="live")
        assert payload["intents"][0]["qty"] == 10.0          # full exit of the snapshot's 10 shares
        mcp = _MockRobinhoodMCP()
        mcp.held = {"AAPL": 4.0}                              # live account has only 4
        summary = run_execution_skill(tmp_output, mcp)
        assert summary["preflight_vetoed"] == ["AAPL"]
        assert mcp.placed_orders == []

    def test_trim_is_capped_at_live_sellable_shares(self, tmp_output: Path):
        payload = _build_and_emit([_rec("AAPL", "SELL", conviction=0.95)], tmp_output, mode="live")
        assert payload["intents"][0]["qty"] is None           # a trim: sized from target_notional
        mcp = _MockRobinhoodMCP()
        mcp.held = {"AAPL": 4.0}
        run_execution_skill(tmp_output, mcp)
        (order,) = mcp.placed_orders
        assert order["quantity"] == 4.0

    def test_review_mode_runs_no_preflight_and_places_nothing(self, tmp_output: Path):
        _build_and_emit([_rec("MSFT", "BUY", conviction=0.95)], tmp_output, mode="review")
        mcp = _MockRobinhoodMCP(quote_prices={"MSFT": 400.0})
        summary = run_execution_skill(tmp_output, mcp)
        assert summary["preflight_used"] is False and mcp.placed_orders == []


class TestSkillMdInvariantsPinned:
    """The Robinhood one-confirmation-per-order gate is prose-only in the skill instructions.
    This test ensures the critical safety invariants are pinned verbatim in the SKILL.md
    so they cannot be accidentally removed or hallucinated away."""

    def test_skill_md_contains_required_safety_invariants(self):
        # Check every copy of the skill that actually exists -- not just
        # .claude's with an .agents fallback used only when .claude's is
        # missing. .claude/skills/robinhood-execution/SKILL.md always
        # exists in this repo, so a fallback-only check would never
        # actually read the .agents/ copy and could miss a safety
        # invariant silently dropped from it while .claude's stayed intact.
        candidate_paths = [
            Path(".claude/skills/robinhood-execution/SKILL.md"),
            Path(".agents/skills/robinhood-execution/SKILL.md"),
        ]
        skill_md_paths = [p for p in candidate_paths if p.exists()]

        assert skill_md_paths, f"Could not find any of {candidate_paths}"

        required_phrases = [
            "Preview before place, always.",
            "Never place in `review` mode.",
            "One explicit human confirmation per placed order",
            "No auto/batch placement.",
            "Honor the kill switch",
            "immediately before each",
            "Agentic account only.",
            "Idempotent placement.",
            "execution_placed.jsonl",
            "Limit price from the live quote.",
            # Robinhood trade-approval mode (four-agent plan, Phase 1)
            "## Robinhood trade-approval mode",
            "customer_approval_required: true",
            "When in doubt, use this path",
            "You never approve a trade.",
            "Deterministic ref_id.",
            "make_ref_id(",
            "get_trade_approval_setting",
            "Approval states come from a fresh call.",
            "append_approval_event",
            "never resubmit a declined or expired order",
            "execution_approvals.jsonl",
            # Portfolio & Risk preflight (four-agent plan, Phase 2)
            "Portfolio & Risk preflight",
            "A preflight veto is final.",
            "read_preflight_for_queue",
            "No usable preflight",
            "A missing check makes the process stricter, never",
            "Preflight vetoes are final.",
            # Thesis narration (four-agent plan, Phase 3)
            "Thesis per order (narration only).",
            "load_run_artifacts",
            "thesis unavailable",
            "This step changes nothing about which orders are placed.",
        ]

        for skill_md_path in skill_md_paths:
            content = skill_md_path.read_text(encoding="utf-8")

            assert "## Invariants (never violate)" in content, (
                f"Missing 'Invariants (never violate)' section header in {skill_md_path}."
            )

            for phrase in required_phrases:
                assert phrase in content, (
                    f"Missing critical safety invariant phrase in {skill_md_path}: '{phrase}'"
                )

    def test_skill_md_never_references_bare_repo_relative_output_path(self):
        """Regression test for the bug documented in
        docs/known_issues/robinhood_execution_skill_repo_relative_path.md:
        the skill instructions used to tell an agent to read/write the queue,
        kill switch, and both ledgers via a LITERAL relative `output/...`
        path. Since the skill is followed by an agent using Read/Write/Bash
        tools (not a Python import), a bare `output/...` resolves against
        the agent's CWD -- the repo/worktree root -- not
        `settings.OUTPUT_DIR` (default `~/.stockpy_local/output`, and this
        repo runs many concurrent git worktrees, so the two can easily
        diverge). Both SKILL.md copies and the /rh-execute command must
        route every reference to these four artifacts through a resolved
        `$OUTPUT_DIR` instead.
        """
        candidate_paths = [
            Path(".claude/skills/robinhood-execution/SKILL.md"),
            Path(".agents/skills/robinhood-execution/SKILL.md"),
            Path(".claude/commands/rh-execute.md"),
        ]
        paths = [p for p in candidate_paths if p.exists()]
        assert paths, f"Could not find any of {candidate_paths}"

        forbidden_literal_paths = [
            "output/execution_queue.json",
            "output/execution_placed.jsonl",
            "output/execution_receipts.jsonl",
            "output/KILL_SWITCH",
        ]

        for path in paths:
            content = path.read_text(encoding="utf-8")
            for forbidden in forbidden_literal_paths:
                assert forbidden not in content, (
                    f"{path} still references the bare, repo-relative "
                    f"'{forbidden}' -- this must be resolved through "
                    f"$OUTPUT_DIR (settings.OUTPUT_DIR) instead, since an "
                    f"agent following these instructions with Read/Write/"
                    f"Bash tools resolves a literal 'output/...' path "
                    f"against its own CWD, not settings.OUTPUT_DIR."
                )

    def test_skill_md_resolves_output_dir_via_settings(self):
        """The two SKILL.md copies must actually teach the agent HOW to
        resolve the real output directory, not just avoid the bad literal
        path -- pins the concrete recipe (settings.OUTPUT_DIR via a one-shot
        `python3 -c` call, reused as $OUTPUT_DIR) so it can't be quietly
        dropped in a future edit."""
        candidate_paths = [
            Path(".claude/skills/robinhood-execution/SKILL.md"),
            Path(".agents/skills/robinhood-execution/SKILL.md"),
        ]
        skill_md_paths = [p for p in candidate_paths if p.exists()]
        assert skill_md_paths, f"Could not find any of {candidate_paths}"

        required_phrases = [
            "Resolve the output directory once.",
            "settings.OUTPUT_DIR",
            "from settings import settings",
            "$OUTPUT_DIR",
        ]

        for skill_md_path in skill_md_paths:
            content = skill_md_path.read_text(encoding="utf-8")
            for phrase in required_phrases:
                assert phrase in content, (
                    f"Missing output-directory-resolution phrase in "
                    f"{skill_md_path}: '{phrase}'"
                )

    def test_skill_md_refreshes_the_queue_through_the_daemon_in_primary(self):
        """Step 5.3: with DAEMON_AGENTIC_QUEUE_MODE=primary the daemon writes
        the queue, so both SKILL.md copies must teach the agent to trigger a
        daemon cycle (POST /run via shared.daemon_client) and poll it to a
        terminal state -- while keeping the unchanged freshness rule and the
        guidance for when the daemon is not primary. (The ``python3 main.py``
        fallback phrase was dropped from the required set when main.py was
        archived in shrink step 5.5.)"""
        candidate_paths = [
            Path(".claude/skills/robinhood-execution/SKILL.md"),
            Path(".agents/skills/robinhood-execution/SKILL.md"),
        ]
        skill_md_paths = [p for p in candidate_paths if p.exists()]
        assert skill_md_paths, f"Could not find any of {candidate_paths}"
        required_phrases = [
            "DAEMON_AGENTIC_QUEUE_MODE",
            "`primary`",
            "from shared import daemon_client",
            "trigger_run(",
            "get_run_status(",
            "already_running",
            '("succeeded", "failed")',
            "kill_switch_active",
            "**`off` or `shadow`**",
            "more than ~30 minutes old",
            "This rule is unchanged in `primary`",
        ]
        for skill_md_path in skill_md_paths:
            content = skill_md_path.read_text(encoding="utf-8")
            for phrase in required_phrases:
                assert phrase in content, (
                    f"Missing daemon-refresh phrase in {skill_md_path}: '{phrase}'"
                )
