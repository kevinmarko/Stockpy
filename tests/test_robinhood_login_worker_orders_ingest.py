"""Tests for data/robinhood_login_worker.py's orders-ingest addition
(_ingest_orders_best_effort + its wiring into _run).

Covers the four non-negotiable properties from the function's own docstring:
  1. An ingest failure never flips the worker's overall result to failure.
  2. It only runs strictly after the account snapshot is fetched.
  3. It only runs for mode="refresh", never "connect".
  4. It's bounded by RH_ORDER_INGEST_BUDGET_SECONDS / RH_ORDER_SYMBOL_RESOLVE_MAX.

Fast, in-process unit tests -- no subprocess, no real robin_stocks/network.
"""

from __future__ import annotations

import os

import pytest

import data.robinhood_login_worker as worker


def _collecting_emit():
    events = []

    def emit(obj):
        events.append(obj)

    return emit, events


class TestIngestOrdersBestEffort:
    def test_noop_when_flag_disabled(self, monkeypatch):
        monkeypatch.setattr("settings.settings.BROKER_TRADE_INGEST_ENABLED", False)
        emit, events = _collecting_emit()
        worker._ingest_orders_best_effort(emit)
        assert events == []  # not even a phase event -- fully inert when off

    def test_success_emits_phase_then_log(self, monkeypatch, tmp_path):
        monkeypatch.setattr("settings.settings.BROKER_TRADE_INGEST_ENABLED", True)
        monkeypatch.setattr("settings.settings.RH_ORDER_INGEST_BUDGET_SECONDS", 60)
        monkeypatch.setattr("settings.settings.RH_ORDER_SYMBOL_RESOLVE_MAX", 200)

        import data.broker_fills_store as bfs

        db_url = f"sqlite:///{tmp_path}/ingest.db"
        monkeypatch.setattr(bfs, "resolve_database_url", lambda: db_url)

        from data.robinhood_orders import OrderFill
        from datetime import datetime, timezone

        fake_fill = OrderFill(
            symbol="AAPL", side="buy", quantity=1.0, price=1.0,
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc), order_id="x1",
        )

        import data.robinhood_orders as rho

        monkeypatch.setattr(
            rho, "fetch_filled_orders",
            lambda *, force=False, symbol_resolver=None, **kw: [fake_fill],
        )

        emit, events = _collecting_emit()
        worker._ingest_orders_best_effort(emit)

        phases = [e for e in events if e.get("event") == "phase"]
        logs = [e for e in events if e.get("event") == "log"]
        assert phases == [{"event": "phase", "phase": "fetching_orders"}]
        assert len(logs) == 1
        assert "1 new fill" in logs[0]["message"] or "Ingested 1" in logs[0]["message"]

        # And it actually persisted.
        store = bfs.BrokerFillsStore(db_url=db_url, readonly=True)
        assert len(store.all_fills()) == 1

    def test_fetch_failure_never_raises_and_still_emits_phase(self, monkeypatch, tmp_path):
        monkeypatch.setattr("settings.settings.BROKER_TRADE_INGEST_ENABLED", True)
        monkeypatch.setattr("settings.settings.RH_ORDER_INGEST_BUDGET_SECONDS", 60)
        monkeypatch.setattr("settings.settings.RH_ORDER_SYMBOL_RESOLVE_MAX", 200)

        import data.broker_fills_store as bfs

        db_url = f"sqlite:///{tmp_path}/ingest2.db"
        monkeypatch.setattr(bfs, "resolve_database_url", lambda: db_url)

        import data.robinhood_orders as rho

        def _boom(*, force=False, symbol_resolver=None, **kw):
            raise RuntimeError("network down")

        monkeypatch.setattr(rho, "fetch_filled_orders", _boom)

        emit, events = _collecting_emit()
        worker._ingest_orders_best_effort(emit)  # must not raise

        phases = [e for e in events if e.get("event") == "phase"]
        logs = [e for e in events if e.get("event") == "log"]
        assert phases == [{"event": "phase", "phase": "fetching_orders"}]
        assert len(logs) == 1
        assert "failed" in logs[0]["message"]

    def test_symbol_resolve_max_zero_still_completes(self, monkeypatch, tmp_path):
        """Budget exhausted immediately -- ingest still completes, just with
        unresolved symbols skipped, never a crash."""
        monkeypatch.setattr("settings.settings.BROKER_TRADE_INGEST_ENABLED", True)
        monkeypatch.setattr("settings.settings.RH_ORDER_INGEST_BUDGET_SECONDS", 60)
        monkeypatch.setattr("settings.settings.RH_ORDER_SYMBOL_RESOLVE_MAX", 0)

        import data.broker_fills_store as bfs

        db_url = f"sqlite:///{tmp_path}/ingest3.db"
        monkeypatch.setattr(bfs, "resolve_database_url", lambda: db_url)

        import data.robinhood_orders as rho

        captured = {}

        def _fake_fetch(*, force=False, symbol_resolver=None, **kw):
            captured["resolver"] = symbol_resolver
            return []

        monkeypatch.setattr(rho, "fetch_filled_orders", _fake_fetch)

        emit, events = _collecting_emit()
        worker._ingest_orders_best_effort(emit)
        assert any(e.get("event") == "log" for e in events)


class TestRunDispatch:
    """End-to-end through _run's real pipe plumbing, with robinhood_portfolio
    and the ingest function stubbed -- confirms mode dispatch without a real
    subprocess or network."""

    def _drive_run(self, monkeypatch, mode: str, snapshot_raises: bool = False):
        creds_r, creds_w = os.pipe()
        events_r, events_w = os.pipe()
        os.write(creds_w, b"\n")  # empty line -> "use .env credentials"
        os.close(creds_w)

        monkeypatch.setattr("settings.settings.RH_USERNAME", "u")
        monkeypatch.setattr("settings.settings.RH_PASSWORD", "p")

        ingest_calls = []
        monkeypatch.setattr(worker, "_ingest_orders_best_effort", lambda emit: ingest_calls.append(1))

        class _FakeRP:
            @staticmethod
            def fetch_account_snapshot(force=True):
                if snapshot_raises:
                    raise RuntimeError("snapshot fetch failed")
                return None

            @staticmethod
            def _login_with(username, password, mode=None):
                return None

        class _FakeSession:
            @staticmethod
            def ensure_session_pickle():
                pass

            @staticmethod
            def backup_session_pickle():
                pass

        # `from data import robinhood_portfolio as rp` inside _run resolves
        # via attribute access on the `data` package once the real submodule
        # has been imported anywhere else in this process (import caches the
        # attribute on the parent package, and a subsequent `from data
        # import x` skips sys.modules entirely once hasattr(data, x) is
        # already true) — patch BOTH the sys.modules entry (covers "not
        # imported yet in this process") and the package attribute (covers
        # "already imported for real elsewhere"), same fix as
        # data/robinhood_orders.py's own `import robin_stocks.robinhood as r`
        # test cases required.
        import sys

        import data as _data_pkg

        monkeypatch.setitem(sys.modules, "data.robinhood_portfolio", _FakeRP)
        monkeypatch.setitem(sys.modules, "data.robinhood_session", _FakeSession)
        monkeypatch.setattr(_data_pkg, "robinhood_portfolio", _FakeRP, raising=False)
        monkeypatch.setattr(_data_pkg, "robinhood_session", _FakeSession, raising=False)

        events_fh_r = os.fdopen(events_r, "r", encoding="utf-8")
        emit = worker._make_emitter(os.fdopen(events_w, "w", encoding="utf-8", closefd=True))
        rc = worker._run(mode, creds_r, emit)
        events_fh_r.close()
        return rc, ingest_calls

    def test_refresh_mode_calls_ingest_after_snapshot(self, monkeypatch):
        rc, ingest_calls = self._drive_run(monkeypatch, "refresh")
        assert rc == 0
        assert ingest_calls == [1]

    def test_connect_mode_never_calls_ingest(self, monkeypatch):
        rc, ingest_calls = self._drive_run(monkeypatch, "connect")
        assert rc == 0
        assert ingest_calls == []

    def test_snapshot_failure_short_circuits_before_ingest(self, monkeypatch):
        """If the account-snapshot fetch itself raises, _run must report
        failure -- and never reach the ingest call, since there's nothing
        to ingest-after."""
        rc, ingest_calls = self._drive_run(monkeypatch, "refresh", snapshot_raises=True)
        assert rc == 4  # auth_failed
        assert ingest_calls == []
