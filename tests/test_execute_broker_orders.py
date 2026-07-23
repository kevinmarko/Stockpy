"""
tests/test_execute_broker_orders.py
=====================================
Branch-coverage tests for ``main_orchestrator._execute_broker_orders`` — the
Alpaca order-submission / reconciliation path that ``docs/test_coverage_analysis.md``
flags as the largest remaining uncovered slice of ``main_orchestrator.py``
("Remaining 49% is mostly the live-broker/reconciliation branches").

Everything here is FULLY OFFLINE. No real ``AlpacaBroker`` is ever constructed,
no network I/O, no real SQLite writes. The tests patch the *source* modules that
``_execute_broker_orders`` re-imports locally (it does its broker imports inside
the function body, after the ADVISORY_ONLY guard) so the real ``OrderManager``,
``PreTradeRiskGate`` seam, ``RiskContext``, ``OrderIntent`` and the real
``KillSwitchActiveError`` are exercised against an in-memory MockBroker.

SAFETY: the platform ships ``ADVISORY_ONLY=True`` (broker quarantined). These
tests flip ``settings.ADVISORY_ONLY`` to ``False`` *only* via monkeypatch (auto
-restored) and *only* against a MockBroker — a real order can never be placed
because ``execution.alpaca_broker.AlpacaBroker`` is replaced by a factory that
returns the MockBroker. The ADVISORY_ONLY quarantine guard itself is covered by
``test_advisory_only_guard_is_a_noop``.

Coverage map
------------
- test_advisory_only_guard_is_a_noop        — (a) guard returns early, broker never built
- test_normal_cycle_records_buy_sell_intents— (b) BUY+SELL reach broker; BUY qty from _kelly_target_qty (NOT 1.0)
- test_buy_qty_is_kelly_sized_not_one_share — (b) explicit regression guard for the hardcoded-1.0 bug
- test_dry_run_never_reaches_broker          — (b) DRY_RUN semantics: manager-level guard, zero broker submits
- test_kill_switch_aborts_order_loop         — (c) KillSwitchActiveError aborts loop, no broker submit
- test_reconciliation_drift_is_surfaced      — (d) drift detected → telemetry.critical
- test_broker_error_on_one_symbol_is_non_fatal — (e) one symbol raises → logged, cycle continues
- test_unsizable_buy_is_skipped              — BUY with no account equity is skipped, not fabricated to 1 share
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
    return PositionSnapshot(
        symbol=symbol,
        qty=qty,
        avg_entry_price=100.0,
        market_value=qty * 100.0,
        unrealized_pl=0.0,
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
) -> MagicMock:
    """Wire up the enabled (ADVISORY_ONLY=False) broker path against mocks and
    return the patched telemetry mock so callers can assert on log calls.

    Patches the SOURCE modules that ``_execute_broker_orders`` imports locally:
      * execution.alpaca_broker.AlpacaBroker  -> factory returning ``broker``
      * transactions_store.TransactionsStore  -> factory returning ``ts_store``
      * execution.risk_gate.PreTradeRiskGate  -> pass-through gate
      * execution.order_manager.GlobalKillSwitch -> factory returning ``kill_switch``
    The REAL OrderManager / RiskContext / OrderIntent / KillSwitchActiveError are
    used unchanged.
    """
    import execution.alpaca_broker as alpaca_mod
    import execution.risk_gate as risk_mod
    import execution.order_manager as om_mod
    import transactions_store as ts_mod

    monkeypatch.setattr(main_orchestrator.settings, "ADVISORY_ONLY", False, raising=False)
    monkeypatch.setattr(alpaca_mod, "AlpacaBroker", lambda *a, **k: broker)
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
    import execution.alpaca_broker as alpaca_mod

    monkeypatch.setattr(main_orchestrator.settings, "ADVISORY_ONLY", True, raising=False)

    broker_ctor = MagicMock(name="AlpacaBroker")
    monkeypatch.setattr(alpaca_mod, "AlpacaBroker", broker_ctor)

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
# (d) Reconciliation drift is detected and surfaced
# ---------------------------------------------------------------------------

def test_reconciliation_drift_is_surfaced(monkeypatch):
    """Broker holds a position the internal store does not → drift is detected by
    reconcile_state and surfaced via telemetry.critical before submission."""
    broker = MockBroker(positions=[_pos("TSLA", 12.0)], equity=100_000.0)
    ts_store = _make_ts_store({})  # internal store is flat → drift on TSLA
    kill_switch = _FakeKillSwitch(active=False)
    telemetry_mock = _install_enabled_broker_stack(
        monkeypatch, broker=broker, ts_store=ts_store, kill_switch=kill_switch
    )

    df = _df([{"Symbol": "TSLA", "Action Signal": "HOLD", "Kelly Target": 0.0, "Price": 250.0}])

    asyncio.run(main_orchestrator._execute_broker_orders(df, dry_run=False))

    assert telemetry_mock.critical.called, "reconciliation drift must be surfaced"
    crit = " ".join(str(c.args[0]) for c in telemetry_mock.critical.call_args_list if c.args)
    assert "drift" in crit.lower()


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
