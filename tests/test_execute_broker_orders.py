"""
tests/test_execute_broker_orders.py
=====================================
Branch-coverage tests for ``main_orchestrator._execute_broker_orders`` — the
pipeline's order-submission path. Since Alpaca was removed (2026-09-30) the
only automated broker is the local FMP paper ledger (``FMPPaperBroker``);
going live (``ADVISORY_ONLY=False`` and ``PAPER_TRADING=False``) places no
pipeline orders at all.

Everything here is FULLY OFFLINE. No real ``FMPPaperBroker`` is ever
constructed, no network I/O, no real SQLite writes. The tests patch the *source* modules that
``_execute_broker_orders`` re-imports locally (it does its broker imports inside
the function body, after the ADVISORY_ONLY guard) so the real ``OrderManager``,
``PreTradeRiskGate`` seam, ``RiskContext``, ``OrderIntent`` and the real
``KillSwitchActiveError`` are exercised against an in-memory MockBroker.

SAFETY: the platform ships ``ADVISORY_ONLY=True`` (broker quarantined). These
tests flip ``settings.ADVISORY_ONLY`` to ``False`` *only* via monkeypatch (auto
-restored) and *only* against a MockBroker — a real order can never be placed
because ``execution.fmp_paper_broker.FMPPaperBroker`` is replaced by a factory
that returns the MockBroker (and US market hours are patched open). The ADVISORY_ONLY quarantine guard itself is covered by
``test_advisory_only_guard_is_a_noop``.

Coverage map
------------
- test_advisory_only_guard_is_a_noop        — (a) guard returns early, broker never built
- test_normal_cycle_records_buy_sell_intents— (b) BUY+SELL reach broker; BUY qty from _kelly_target_qty (NOT 1.0)
- test_buy_qty_is_kelly_sized_not_one_share — (b) explicit regression guard for the hardcoded-1.0 bug
- test_dry_run_never_reaches_broker          — (b) DRY_RUN semantics: manager-level guard, zero broker submits
- test_kill_switch_aborts_order_loop         — (c) KillSwitchActiveError aborts loop, no broker submit
- test_going_live_constructs_no_broker_and_submits_nothing — going live → no pipeline orders
- test_broker_error_on_one_symbol_is_non_fatal — (e) one symbol raises → logged, cycle continues
- test_unsizable_buy_is_skipped              — BUY with no account equity is skipped, not fabricated to 1 share
- test_buy_target_qty_reflects_post_regime_derate — (g) Kelly_Target_Post_Regime > Kelly Target -> target_qty > qty
- test_buy_target_qty_falls_back_when_column_absent — (g) no Kelly_Target_Post_Regime column -> target_qty == qty
- test_buy_target_qty_equals_qty_when_no_derate — (g) Kelly_Target_Post_Regime == Kelly Target -> target_qty == qty
- test_sell_target_qty_always_equals_qty      — (g) SELL/TRIM: target_qty == qty, unchanged by this feature
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import AsyncIterator, Optional
from unittest.mock import MagicMock

import pandas as pd
import pytest

import main_orchestrator
from execution.broker_base import (
    AccountSnapshot,
    BrokerBase,
    OrderIntent,
    OrderResult,
    OrderSide,
    OrderStatus,
    PositionSnapshot,
)


# ---------------------------------------------------------------------------
# Local MockBroker (deliberately NOT a shared conftest fixture — this test file
# must stand alone on the base branch). Modeled on the mocks in
# tests/test_order_manager_idempotency.py and tests/test_reconciliation.py.
# ---------------------------------------------------------------------------

class MockBroker(BrokerBase):
    """In-memory BrokerBase stub with configurable positions/equity and optional
    per-symbol submit-error injection."""

    def __init__(
        self,
        *,
        positions: Optional[list[PositionSnapshot]] = None,
        equity: float = 100_000.0,
        raise_on_symbols: Optional[set[str]] = None,
    ) -> None:
        self._positions = positions or []
        self._equity = equity
        self._raise_on_symbols = raise_on_symbols or set()
        self.submitted: list[OrderIntent] = []
        self.get_positions_calls = 0
        self.get_account_calls = 0

    async def submit_order(self, intent: OrderIntent) -> OrderResult:
        if intent.symbol in self._raise_on_symbols:
            raise RuntimeError(f"Simulated broker submit failure for {intent.symbol}")
        self.submitted.append(intent)
        return OrderResult(
            client_order_id=intent.client_order_id or "",
            broker_order_id=f"mock-{len(self.submitted)}",
            status=OrderStatus.ACCEPTED,
        )

    async def cancel_order(self, broker_order_id: str) -> bool:
        return True

    async def get_open_positions(self) -> list[PositionSnapshot]:
        self.get_positions_calls += 1
        return list(self._positions)

    async def get_account(self) -> AccountSnapshot:
        self.get_account_calls += 1
        return AccountSnapshot(
            equity=self._equity, cash=self._equity, buying_power=self._equity * 2
        )

    async def get_orders(self, status=None, limit=100) -> list[OrderResult]:
        return []

    async def stream_trade_updates(self) -> AsyncIterator:
        return
        yield  # make it an async generator


class _PassThroughRiskGate:
    """Stand-in for PreTradeRiskGate that always passes — keeps the tests free of
    wall-clock market-hours flakiness. The real gate is exercised in
    tests/test_risk_gate.py; here we only care about the orchestrator branches."""

    def run_all(self, intent, context):
        return True, []


class _FakeKillSwitch:
    """Stand-in for GlobalKillSwitch with a controllable ``is_active``."""

    def __init__(self, active: bool = False, reason: str = "test-reason") -> None:
        # MagicMock so the number of is_active() checks is observable.
        self.is_active = MagicMock(return_value=active)
        self._reason = reason

    def reason(self) -> str:
        return self._reason


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pos(symbol: str, qty: float) -> PositionSnapshot:
    """A pipeline-owned paper position (the paper path only trades its own)."""
    return PositionSnapshot(
        symbol=symbol,
        qty=qty,
        avg_entry_price=100.0,
        market_value=qty * 100.0,
        unrealized_pl=0.0,
        strategy_id=main_orchestrator.PIPELINE_STRATEGY_ID,
    )


def _make_ts_store(positions: Optional[dict[str, float]] = None) -> MagicMock:
    """MagicMock TransactionsStore whose open_trades_df() reports ``positions``."""
    ts = MagicMock()
    positions = positions or {}
    if positions:
        records = [
            {"symbol": sym, "shares": qty, "exit_ts": None}
            for sym, qty in positions.items()
        ]
        ts.open_trades_df.return_value = pd.DataFrame(records)
    else:
        ts.open_trades_df.return_value = pd.DataFrame()
    return ts


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _install_enabled_broker_stack(
    monkeypatch,
    *,
    broker: MockBroker,
    ts_store: MagicMock,
    kill_switch: _FakeKillSwitch,
    market_open: bool = True,
) -> MagicMock:
    """Wire up the enabled (ADVISORY_ONLY=False, PAPER_TRADING=True) paper
    broker path against mocks and return the patched telemetry mock so callers
    can assert on log calls.

    Patches the SOURCE modules that ``_execute_broker_orders`` imports locally:
      * execution.fmp_paper_broker.FMPPaperBroker -> factory returning ``broker``
      * engine.advisory_agent.is_us_market_open -> ``market_open``
      * transactions_store.TransactionsStore  -> factory returning ``ts_store``
      * execution.risk_gate.PreTradeRiskGate  -> pass-through gate
      * execution.order_manager.GlobalKillSwitch -> factory returning ``kill_switch``
    The REAL OrderManager / RiskContext / OrderIntent / KillSwitchActiveError are
    used unchanged.
    """
    import engine.advisory_agent as agent_mod
    import execution.fmp_paper_broker as fmp_mod
    import execution.risk_gate as risk_mod
    import execution.order_manager as om_mod
    import transactions_store as ts_mod

    monkeypatch.setattr(main_orchestrator.settings, "ADVISORY_ONLY", False, raising=False)
    monkeypatch.setattr(main_orchestrator.settings, "PAPER_TRADING", True, raising=False)
    monkeypatch.setattr(main_orchestrator.settings, "BROKER_BACKEND", "fmp_paper", raising=False)
    monkeypatch.setattr(fmp_mod, "FMPPaperBroker", lambda *a, **k: broker)
    monkeypatch.setattr(agent_mod, "is_us_market_open", lambda now: market_open)
    monkeypatch.setattr(ts_mod, "TransactionsStore", lambda *a, **k: ts_store)
    monkeypatch.setattr(risk_mod, "PreTradeRiskGate", lambda *a, **k: _PassThroughRiskGate())
    monkeypatch.setattr(om_mod, "GlobalKillSwitch", lambda *a, **k: kill_switch)

    telemetry_mock = MagicMock()
    monkeypatch.setattr(main_orchestrator, "telemetry", telemetry_mock)
    return telemetry_mock


# ---------------------------------------------------------------------------
# (a) ADVISORY_ONLY quarantine guard
# ---------------------------------------------------------------------------

def test_advisory_only_guard_is_a_noop(monkeypatch):
    """ADVISORY_ONLY=True → function returns immediately, no broker constructed."""
    import execution.fmp_paper_broker as fmp_mod

    monkeypatch.setattr(main_orchestrator.settings, "ADVISORY_ONLY", True, raising=False)

    broker_ctor = MagicMock(name="FMPPaperBroker")
    monkeypatch.setattr(fmp_mod, "FMPPaperBroker", broker_ctor)

    telemetry_mock = MagicMock()
    monkeypatch.setattr(main_orchestrator, "telemetry", telemetry_mock)

    df = _df([{"Symbol": "AAPL", "Action Signal": "BUY", "Kelly Target": 0.1, "Price": 100.0}])

    result = asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    assert result is None
    # The broker class is imported *after* the guard, so with the guard active it
    # must never even be constructed.
    broker_ctor.assert_not_called()
    # And the quarantine notice is logged so the operator sees it in the run log.
    assert telemetry_mock.info.called
    logged = " ".join(str(c.args[0]) for c in telemetry_mock.info.call_args_list if c.args)
    assert "ADVISORY_ONLY" in logged


# ---------------------------------------------------------------------------
# (b) Normal cycle: BUY + SELL intents reach the broker, Kelly-sized
# ---------------------------------------------------------------------------

def test_normal_cycle_records_buy_sell_intents(monkeypatch):
    """A normal enabled cycle submits a Kelly-sized BUY and a full-close SELL."""
    broker = MockBroker(positions=[_pos("MSFT", 5.0)], equity=100_000.0)
    # Internal store matches the broker (MSFT 5) so there is no drift noise here.
    ts_store = _make_ts_store({"MSFT": 5.0})
    kill_switch = _FakeKillSwitch(active=False)
    _install_enabled_broker_stack(
        monkeypatch, broker=broker, ts_store=ts_store, kill_switch=kill_switch
    )

    df = _df([
        {"Symbol": "AAPL", "Action Signal": "STRONG BUY", "Kelly Target": 0.1, "Price": 100.0},
        {"Symbol": "MSFT", "Action Signal": "SELL", "Kelly Target": 0.0, "Price": 200.0},
    ])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    by_symbol = {i.symbol: i for i in broker.submitted}
    assert set(by_symbol) == {"AAPL", "MSFT"}, f"unexpected submits: {broker.submitted}"

    buy = by_symbol["AAPL"]
    assert buy.side is OrderSide.BUY
    expected_qty = main_orchestrator._kelly_target_qty(0.1, 100_000.0, 100.0)
    assert expected_qty == pytest.approx(100.0)  # 0.1 * 100000 / 100
    assert buy.qty == pytest.approx(expected_qty)

    sell = by_symbol["MSFT"]
    assert sell.side is OrderSide.SELL
    assert sell.qty == pytest.approx(5.0)  # abs(open position qty)


def test_buy_qty_is_kelly_sized_not_one_share(monkeypatch):
    """Regression guard for the real past bug where BUY submitted a hardcoded
    qty=1.0 regardless of conviction (which neutered the position-size risk
    check). The BUY qty MUST come from _kelly_target_qty(weight, equity, price)."""
    broker = MockBroker(positions=[], equity=50_000.0)
    ts_store = _make_ts_store({})
    kill_switch = _FakeKillSwitch(active=False)
    _install_enabled_broker_stack(
        monkeypatch, broker=broker, ts_store=ts_store, kill_switch=kill_switch
    )

    # weight 0.2, equity 50k, price 25 -> 0.2*50000/25 = 400 shares (far from 1.0)
    df = _df([{"Symbol": "NVDA", "Action Signal": "BUY", "Kelly Target": 0.2, "Price": 25.0}])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    assert len(broker.submitted) == 1
    buy = broker.submitted[0]
    assert buy.qty == pytest.approx(400.0)
    assert buy.qty != pytest.approx(1.0), "BUY qty must not be the hardcoded 1-share default"


def test_dry_run_never_reaches_broker(monkeypatch):
    """DRY_RUN semantics: OrderManager intercepts at the manager level, so a
    dry-run cycle logs intent but the broker's submit_order is never called."""
    broker = MockBroker(positions=[], equity=100_000.0)
    ts_store = _make_ts_store({})
    kill_switch = _FakeKillSwitch(active=False)
    _install_enabled_broker_stack(
        monkeypatch, broker=broker, ts_store=ts_store, kill_switch=kill_switch
    )

    df = _df([{"Symbol": "AAPL", "Action Signal": "BUY", "Kelly Target": 0.1, "Price": 100.0}])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=True))

    assert broker.submitted == [], "dry_run must not reach broker.submit_order"


# ---------------------------------------------------------------------------
# (c) Active kill switch aborts the whole order loop
# ---------------------------------------------------------------------------

def test_kill_switch_aborts_order_loop(monkeypatch):
    """An active kill switch → KillSwitchActiveError on the first submit → the
    loop aborts (returns) before submitting anything, and does NOT continue to
    the next symbol."""
    broker = MockBroker(positions=[], equity=100_000.0)
    ts_store = _make_ts_store({})
    kill_switch = _FakeKillSwitch(active=True, reason="operator halt")
    telemetry_mock = _install_enabled_broker_stack(
        monkeypatch, broker=broker, ts_store=ts_store, kill_switch=kill_switch
    )

    # Two eligible BUYs: if the loop *continued* past the raise, is_active would
    # be checked twice. Abort-on-first-raise means exactly one check.
    df = _df([
        {"Symbol": "AAPL", "Action Signal": "BUY", "Kelly Target": 0.1, "Price": 100.0},
        {"Symbol": "MSFT", "Action Signal": "BUY", "Kelly Target": 0.1, "Price": 200.0},
    ])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    assert broker.submitted == [], "no order may reach the broker when kill switch is active"
    assert kill_switch.is_active.call_count == 1, "loop must abort after the first raise, not continue"
    # CRITICAL banner naming the kill switch is emitted.
    crit = " ".join(str(c.args[0]) for c in telemetry_mock.critical.call_args_list if c.args)
    assert "Kill switch" in crit


# ---------------------------------------------------------------------------
# (e) A broker error on one symbol is non-fatal — the cycle continues
# ---------------------------------------------------------------------------

def test_broker_error_on_one_symbol_is_non_fatal(monkeypatch):
    """submit_order raising for one symbol is caught and logged; the loop
    proceeds to the next symbol, which submits successfully."""
    broker = MockBroker(positions=[], equity=100_000.0, raise_on_symbols={"AAPL"})
    ts_store = _make_ts_store({})
    kill_switch = _FakeKillSwitch(active=False)
    telemetry_mock = _install_enabled_broker_stack(
        monkeypatch, broker=broker, ts_store=ts_store, kill_switch=kill_switch
    )

    df = _df([
        {"Symbol": "AAPL", "Action Signal": "BUY", "Kelly Target": 0.1, "Price": 100.0},
        {"Symbol": "MSFT", "Action Signal": "BUY", "Kelly Target": 0.1, "Price": 200.0},
    ])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    # AAPL raised (not recorded); MSFT still submitted → loop did not abort.
    submitted_symbols = {i.symbol for i in broker.submitted}
    assert submitted_symbols == {"MSFT"}, f"expected only MSFT recorded, got {submitted_symbols}"
    assert telemetry_mock.error.called, "the per-symbol failure must be logged as ERROR"
    err = " ".join(str(c.args[0]) for c in telemetry_mock.error.call_args_list if c.args)
    assert "AAPL" in err or "Order submission failed" in err


# ---------------------------------------------------------------------------
# Extra: an unsizable BUY (no account equity) is skipped, never fabricated
# ---------------------------------------------------------------------------

def test_unsizable_buy_is_skipped(monkeypatch):
    """When the account has zero equity, the BUY cannot be sized (Kelly Target is
    a weight) and is SKIPPED rather than submitted at a fabricated 1-share size
    (CONSTRAINT #4)."""
    broker = MockBroker(positions=[], equity=0.0)
    ts_store = _make_ts_store({})
    kill_switch = _FakeKillSwitch(active=False)
    telemetry_mock = _install_enabled_broker_stack(
        monkeypatch, broker=broker, ts_store=ts_store, kill_switch=kill_switch
    )

    df = _df([{"Symbol": "AAPL", "Action Signal": "BUY", "Kelly Target": 0.1, "Price": 100.0}])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    assert broker.submitted == [], "an unsizable BUY must not be submitted"
    assert telemetry_mock.warning.called, "the skip must be logged as a WARNING"


# ---------------------------------------------------------------------------
# (g) OrderIntent.target_qty -- the pre-portfolio-cap, pre-Dual-Momentum
#     sizing target implied by Kelly_Target_Post_Regime (BUY only; SELL/TRIM
#     always equals qty since a full close has no distinct target).
# ---------------------------------------------------------------------------

def test_buy_target_qty_reflects_post_regime_derate(monkeypatch):
    """Kelly_Target_Post_Regime > 'Kelly Target' simulates a portfolio-gross-
    cap or Dual-Momentum derate having shrunk the submitted 'Kelly Target'
    weight after strategy_engine.py originally computed a larger per-name
    sizing decision. target_qty must reflect the larger, pre-derate weight,
    strictly greater than the actually-submitted qty."""
    broker = MockBroker(positions=[], equity=100_000.0)
    ts_store = _make_ts_store({})
    kill_switch = _FakeKillSwitch(active=False)
    _install_enabled_broker_stack(
        monkeypatch, broker=broker, ts_store=ts_store, kill_switch=kill_switch
    )

    df = _df([{
        "Symbol": "AAPL", "Action Signal": "BUY",
        "Kelly Target": 0.1, "Kelly_Target_Post_Regime": 0.2,
        "Price": 100.0,
    }])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    assert len(broker.submitted) == 1
    buy = broker.submitted[0]
    expected_qty = main_orchestrator._kelly_target_qty(0.1, 100_000.0, 100.0)
    expected_target_qty = main_orchestrator._kelly_target_qty(0.2, 100_000.0, 100.0)
    assert buy.qty == pytest.approx(expected_qty)  # unaffected -- still Kelly-Target-sized
    assert buy.target_qty == pytest.approx(expected_target_qty)
    assert buy.target_qty > buy.qty


def test_buy_target_qty_falls_back_when_column_absent(monkeypatch):
    """No 'Kelly_Target_Post_Regime' column at all (an older/incompatible
    cached DataFrame) -> graceful fallback to 'Kelly Target' itself, so
    target_qty == qty (today's exact prior behavior)."""
    broker = MockBroker(positions=[], equity=100_000.0)
    ts_store = _make_ts_store({})
    kill_switch = _FakeKillSwitch(active=False)
    _install_enabled_broker_stack(
        monkeypatch, broker=broker, ts_store=ts_store, kill_switch=kill_switch
    )

    df = _df([{"Symbol": "AAPL", "Action Signal": "BUY", "Kelly Target": 0.1, "Price": 100.0}])
    assert "Kelly_Target_Post_Regime" not in df.columns

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    assert len(broker.submitted) == 1
    buy = broker.submitted[0]
    expected_qty = main_orchestrator._kelly_target_qty(0.1, 100_000.0, 100.0)
    assert buy.qty == pytest.approx(expected_qty)
    assert buy.target_qty == pytest.approx(buy.qty)


def test_buy_target_qty_equals_qty_when_no_derate(monkeypatch):
    """Kelly_Target_Post_Regime present but equal to 'Kelly Target' (no
    portfolio-gross-cap / Dual-Momentum derating happened this cycle) ->
    target_qty == qty."""
    broker = MockBroker(positions=[], equity=100_000.0)
    ts_store = _make_ts_store({})
    kill_switch = _FakeKillSwitch(active=False)
    _install_enabled_broker_stack(
        monkeypatch, broker=broker, ts_store=ts_store, kill_switch=kill_switch
    )

    df = _df([{
        "Symbol": "AAPL", "Action Signal": "BUY",
        "Kelly Target": 0.1, "Kelly_Target_Post_Regime": 0.1,
        "Price": 100.0,
    }])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    assert len(broker.submitted) == 1
    buy = broker.submitted[0]
    expected_qty = main_orchestrator._kelly_target_qty(0.1, 100_000.0, 100.0)
    assert buy.qty == pytest.approx(expected_qty)
    assert buy.target_qty == pytest.approx(buy.qty)


def test_sell_target_qty_always_equals_qty(monkeypatch):
    """SELL/TRIM: target_qty == qty, unchanged by this feature -- a full
    position close has no distinct 'target' size to diverge from what's
    actually held."""
    broker = MockBroker(positions=[_pos("MSFT", 5.0)], equity=100_000.0)
    ts_store = _make_ts_store({"MSFT": 5.0})
    kill_switch = _FakeKillSwitch(active=False)
    _install_enabled_broker_stack(
        monkeypatch, broker=broker, ts_store=ts_store, kill_switch=kill_switch
    )

    df = _df([{
        "Symbol": "MSFT", "Action Signal": "SELL",
        "Kelly Target": 0.0, "Kelly_Target_Post_Regime": 0.0,
        "Price": 200.0,
    }])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    assert len(broker.submitted) == 1
    sell = broker.submitted[0]
    assert sell.side is OrderSide.SELL
    assert sell.qty == pytest.approx(5.0)
    assert sell.target_qty == pytest.approx(sell.qty)


# ---------------------------------------------------------------------------
# (f) Opt-in priority queue (settings.EXECUTION_PRIORITY_QUEUE_ENABLED) --
#     Phase-2 WebSocket-ingestion-priority-queue item 1b
# ---------------------------------------------------------------------------

def test_priority_queue_disabled_by_default_preserves_row_order(monkeypatch):
    """Flag unset (default False): submission order must be exactly final_df's
    row order, byte-identical to the pre-priority-queue behavior -- BUY (row 0)
    submitted before SELL (row 1) even though SELL is normally URGENT."""
    broker = MockBroker(positions=[_pos("MSFT", 5.0)], equity=100_000.0)
    ts_store = _make_ts_store({"MSFT": 5.0})
    kill_switch = _FakeKillSwitch(active=False)
    _install_enabled_broker_stack(
        monkeypatch, broker=broker, ts_store=ts_store, kill_switch=kill_switch
    )
    # EXECUTION_PRIORITY_QUEUE_ENABLED left at its real default (False) --
    # deliberately NOT monkeypatched, to prove the default is itself correct.

    df = _df([
        {"Symbol": "AAPL", "Action Signal": "BUY", "Kelly Target": 0.1, "Price": 100.0},
        {"Symbol": "MSFT", "Action Signal": "SELL", "Kelly Target": 0.0, "Price": 200.0},
    ])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    submitted_symbols = [i.symbol for i in broker.submitted]
    assert submitted_symbols == ["AAPL", "MSFT"], (
        f"flag-off must preserve exact final_df row order, got {submitted_symbols}"
    )


def test_priority_queue_enabled_submits_sell_before_buy(monkeypatch):
    """Flag True: even with BUY appearing first in final_df, SELL/TRIM (URGENT)
    must reach the broker before BUY (NORMAL)."""
    broker = MockBroker(positions=[_pos("MSFT", 5.0)], equity=100_000.0)
    ts_store = _make_ts_store({"MSFT": 5.0})
    kill_switch = _FakeKillSwitch(active=False)
    _install_enabled_broker_stack(
        monkeypatch, broker=broker, ts_store=ts_store, kill_switch=kill_switch
    )
    monkeypatch.setattr(main_orchestrator.settings, "EXECUTION_PRIORITY_QUEUE_ENABLED", True, raising=False)
    monkeypatch.setattr(main_orchestrator.settings, "EXECUTION_QUEUE_LEAK_RATE_PER_SEC", -1, raising=False)

    df = _df([
        {"Symbol": "AAPL", "Action Signal": "BUY", "Kelly Target": 0.1, "Price": 100.0},
        {"Symbol": "MSFT", "Action Signal": "SELL", "Kelly Target": 0.0, "Price": 200.0},
    ])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    submitted_symbols = [i.symbol for i in broker.submitted]
    assert submitted_symbols == ["MSFT", "AAPL"], (
        f"flag-on must submit URGENT (SELL) before NORMAL (BUY), got {submitted_symbols}"
    )
    # Both still reached the broker -- the queue reorders, it never drops.
    assert set(submitted_symbols) == {"AAPL", "MSFT"}


def test_priority_queue_enabled_kill_switch_still_aborts_remaining_drain(monkeypatch):
    """Flag True: an active kill switch must still abort submission -- checked
    at DRAIN time now, but the guarantee (no order reaches the broker) holds."""
    broker = MockBroker(positions=[], equity=100_000.0)
    ts_store = _make_ts_store({})
    kill_switch = _FakeKillSwitch(active=True, reason="operator halt")
    telemetry_mock = _install_enabled_broker_stack(
        monkeypatch, broker=broker, ts_store=ts_store, kill_switch=kill_switch
    )
    monkeypatch.setattr(main_orchestrator.settings, "EXECUTION_PRIORITY_QUEUE_ENABLED", True, raising=False)
    monkeypatch.setattr(main_orchestrator.settings, "EXECUTION_QUEUE_LEAK_RATE_PER_SEC", -1, raising=False)

    df = _df([{"Symbol": "AAPL", "Action Signal": "BUY", "Kelly Target": 0.1, "Price": 100.0}])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    assert broker.submitted == [], "no order may reach the broker when kill switch is active"
    crit = " ".join(str(c.args[0]) for c in telemetry_mock.critical.call_args_list if c.args)
    assert "Kill switch" in crit

def _install_going_live(monkeypatch):
    """ADVISORY_ONLY=False + PAPER_TRADING=False, with every broker seam wired
    to a recorder so any construction or submission is observable."""
    import engine.advisory_agent as agent_mod
    import execution.fmp_paper_broker as fmp_mod

    broker = MockBroker(positions=[_pos("MSFT", 5.0)], equity=100_000.0)
    broker_ctor = MagicMock(name="FMPPaperBroker", return_value=broker)
    monkeypatch.setattr(main_orchestrator.settings, "ADVISORY_ONLY", False, raising=False)
    monkeypatch.setattr(main_orchestrator.settings, "PAPER_TRADING", False, raising=False)
    monkeypatch.setattr(fmp_mod, "FMPPaperBroker", broker_ctor)
    monkeypatch.setattr(agent_mod, "is_us_market_open", lambda now: True)
    telemetry_mock = MagicMock()
    monkeypatch.setattr(main_orchestrator, "telemetry", telemetry_mock)
    alert_mock = MagicMock()
    monkeypatch.setattr("observability.alerts.send_alert", alert_mock)
    monkeypatch.setattr("diagnostics_and_visuals.telemetry.error", MagicMock())
    return broker, broker_ctor, alert_mock


def test_going_live_constructs_no_broker_and_submits_nothing(monkeypatch):
    """Going live (ADVISORY_ONLY=False, PAPER_TRADING=False): the automated
    pipeline has no live broker since Alpaca was removed, so
    _execute_broker_orders constructs NO broker and submits NOTHING -- real
    trades go only through the Robinhood queue. The misconfiguration is made
    visible with a CRITICAL alert rather than silently paper-trading."""
    broker, broker_ctor, alert_mock = _install_going_live(monkeypatch)
    df = _df([
        {"Symbol": "AAPL", "Action Signal": "STRONG BUY", "Kelly Target": 0.1, "Price": 100.0},
        {"Symbol": "MSFT", "Action Signal": "SELL", "Kelly Target": 0.0, "Price": 200.0},
    ])

    result = asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    assert result is None
    broker_ctor.assert_not_called()
    assert broker.submitted == []
    assert broker.get_positions_calls == 0
    alert_mock.assert_called_once()
    assert alert_mock.call_args.kwargs.get("level") == "CRITICAL"


# ---------------------------------------------------------------------------
# (h) fmp_paper local ledger: RTH-only, no reconciliation, own positions only
# ---------------------------------------------------------------------------

def _install_fmp_paper_stack(monkeypatch, *, broker, ts_store, market_open: bool):
    return _install_enabled_broker_stack(
        monkeypatch, broker=broker, ts_store=ts_store,
        kill_switch=_FakeKillSwitch(active=False), market_open=market_open,
    )


def _tagged_pos(symbol: str, qty: float, strategy_id: str) -> PositionSnapshot:
    return PositionSnapshot(
        symbol=symbol, qty=qty, avg_entry_price=100.0,
        market_value=qty * 100.0, unrealized_pl=0.0, strategy_id=strategy_id,
    )


def test_fmp_paper_outside_market_hours_submits_nothing(monkeypatch):
    broker = MockBroker(equity=100_000.0)
    ts_store = _make_ts_store()
    telemetry_mock = _install_fmp_paper_stack(
        monkeypatch, broker=broker, ts_store=ts_store, market_open=False
    )
    df = _df([{"Symbol": "AAPL", "Action Signal": "BUY", "Kelly Target": 0.1, "Price": 100.0}])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    assert broker.submitted == []
    assert broker.get_positions_calls == 0, "broker must not even be queried outside RTH"
    logged = " ".join(str(c.args[0]) for c in telemetry_mock.info.call_args_list if c.args)
    assert "outside regular US market hours" in logged


def test_fmp_paper_skips_reconciliation(monkeypatch):
    # A manual position the trades ledger doesn't know about would read as
    # drift against an external broker; the paper ledger IS the ledger, so
    # reconciliation is skipped.
    broker = MockBroker(positions=[_tagged_pos("ABR", 95.0, "Manual Trade")])
    ts_store = _make_ts_store()
    telemetry_mock = _install_fmp_paper_stack(
        monkeypatch, broker=broker, ts_store=ts_store, market_open=True
    )

    asyncio.run(main_orchestrator._execute_broker_orders(_df([]), dry_run=False))

    ts_store.open_trades_df.assert_not_called()
    assert not telemetry_mock.critical.called


def test_fmp_paper_sell_never_closes_a_manual_position(monkeypatch):
    broker = MockBroker(positions=[
        _tagged_pos("ABR", 95.0, "Manual Trade"),
        _tagged_pos("MSFT", 5.0, main_orchestrator.PIPELINE_STRATEGY_ID),
    ])
    ts_store = _make_ts_store()
    _install_fmp_paper_stack(monkeypatch, broker=broker, ts_store=ts_store, market_open=True)
    df = _df([
        {"Symbol": "ABR", "Action Signal": "SELL", "Kelly Target": 0.0, "Price": 10.0},
        {"Symbol": "MSFT", "Action Signal": "SELL", "Kelly Target": 0.0, "Price": 200.0},
    ])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    assert [(i.symbol, i.side, i.qty) for i in broker.submitted] == [
        ("MSFT", OrderSide.SELL, 5.0)
    ]
    assert broker.submitted[0].strategy_id == main_orchestrator.PIPELINE_STRATEGY_ID


def test_fmp_paper_manual_holding_does_not_block_pipeline_buy(monkeypatch):
    broker = MockBroker(positions=[_tagged_pos("ABR", 95.0, "Manual Trade")], equity=100_000.0)
    ts_store = _make_ts_store()
    _install_fmp_paper_stack(monkeypatch, broker=broker, ts_store=ts_store, market_open=True)
    df = _df([{"Symbol": "ABR", "Action Signal": "BUY", "Kelly Target": 0.05, "Price": 10.0}])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    assert len(broker.submitted) == 1
    buy = broker.submitted[0]
    assert (buy.symbol, buy.side) == ("ABR", OrderSide.BUY)
    assert buy.qty == pytest.approx(main_orchestrator._kelly_target_qty(0.05, 100_000.0, 10.0))


def test_fmp_paper_does_not_rebuy_its_own_open_position(monkeypatch):
    broker = MockBroker(positions=[_tagged_pos("AAPL", 3.0, main_orchestrator.PIPELINE_STRATEGY_ID)])
    ts_store = _make_ts_store()
    _install_fmp_paper_stack(monkeypatch, broker=broker, ts_store=ts_store, market_open=True)
    df = _df([{"Symbol": "AAPL", "Action Signal": "BUY", "Kelly Target": 0.1, "Price": 100.0}])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    assert broker.submitted == []


# ---------------------------------------------------------------------------
# (i) paper cold-start probe sizing + RISK REDUCE exits
# ---------------------------------------------------------------------------

def test_fmp_paper_probe_sizes_a_zero_kelly_buy(monkeypatch):
    broker = MockBroker(equity=100_000.0)
    _install_fmp_paper_stack(monkeypatch, broker=broker, ts_store=_make_ts_store(), market_open=True)
    monkeypatch.setattr(main_orchestrator.settings, "PAPER_PIPELINE_PROBE_WEIGHT", 0.01, raising=False)
    df = _df([
        {"Symbol": "AGNC", "Action Signal": "STRONG BUY", "Kelly Target": 0.0,
         "Kelly_Target_Post_Regime": 0.0, "Price": 10.0},
        {"Symbol": "SPY", "Action Signal": "HOLD", "Kelly Target": 0.0, "Price": 500.0},
    ])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    assert len(broker.submitted) == 1
    buy = broker.submitted[0]
    assert (buy.symbol, buy.side) == ("AGNC", OrderSide.BUY)
    assert buy.qty == pytest.approx(100.0)  # 1% of $100k at $10


def test_fmp_paper_positive_kelly_beats_the_probe(monkeypatch):
    broker = MockBroker(equity=100_000.0)
    _install_fmp_paper_stack(monkeypatch, broker=broker, ts_store=_make_ts_store(), market_open=True)
    monkeypatch.setattr(main_orchestrator.settings, "PAPER_PIPELINE_PROBE_WEIGHT", 0.01, raising=False)
    df = _df([{"Symbol": "AGNC", "Action Signal": "BUY", "Kelly Target": 0.03, "Price": 10.0}])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    assert broker.submitted[0].qty == pytest.approx(300.0)


def test_probe_is_off_by_default(monkeypatch):
    broker = MockBroker(equity=100_000.0)
    _install_fmp_paper_stack(monkeypatch, broker=broker, ts_store=_make_ts_store(), market_open=True)
    monkeypatch.setattr(main_orchestrator.settings, "PAPER_PIPELINE_PROBE_WEIGHT", 0.0, raising=False)
    df = _df([{"Symbol": "AGNC", "Action Signal": "BUY", "Kelly Target": 0.0, "Price": 10.0}])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    assert broker.submitted == []


def test_probe_never_applies_when_going_live(monkeypatch):
    """The paper cold-start probe must never turn into a real-money order:
    going live places no pipeline orders at all, probe or not."""
    broker, broker_ctor, _alert = _install_going_live(monkeypatch)
    monkeypatch.setattr(main_orchestrator.settings, "PAPER_PIPELINE_PROBE_WEIGHT", 0.01, raising=False)
    df = _df([{"Symbol": "AGNC", "Action Signal": "BUY", "Kelly Target": 0.0, "Price": 10.0}])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    broker_ctor.assert_not_called()
    assert broker.submitted == []


def test_risk_reduce_closes_the_pipeline_position(monkeypatch):
    broker = MockBroker(positions=[
        _tagged_pos("AGNC", 100.0, main_orchestrator.PIPELINE_STRATEGY_ID),
        _tagged_pos("ABR", 95.0, "Manual Trade"),
    ])
    _install_fmp_paper_stack(monkeypatch, broker=broker, ts_store=_make_ts_store(), market_open=True)
    df = _df([
        {"Symbol": "AGNC", "Action Signal": "RISK REDUCE", "Kelly Target": 0.0, "Price": 10.0},
        {"Symbol": "ABR", "Action Signal": "RISK REDUCE", "Kelly Target": 0.0, "Price": 10.0},
    ])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    assert [(i.symbol, i.side, i.qty) for i in broker.submitted] == [("AGNC", OrderSide.SELL, 100.0)]


def test_exit_signals_match_strategy_engine_vocabulary():
    # strategy_engine emits RISK REDUCE as its exit instruction; it must close.
    assert "RISK REDUCE" in main_orchestrator.EXIT_SIGNALS
    assert "HOLD" not in main_orchestrator.EXIT_SIGNALS
