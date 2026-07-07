"""
tests/test_daemon_runtime.py
=============================
Fully offline unit tests for desktop/daemon_runtime.py -- the signal-agnostic
core run engine that keeps main_orchestrator's heavy engines warm across
cycles. ``main_orchestrator._main_body`` is always monkeypatched here; the
real pipeline is never invoked (far too slow / network-dependent for a unit
test). Signal/process-lifecycle handling lives in a separate module and is
out of scope for these tests.
"""
from __future__ import annotations

import threading
import time

import pytest

import desktop.daemon_runtime as daemon_runtime
import main_orchestrator
from desktop.daemon_runtime import (
    OrchestratorDaemon,
    RunState,
    TriggerOutcome,
)


def _poll_until(predicate, *, timeout: float = 3.0, interval: float = 0.02) -> bool:
    """Poll ``predicate`` until it returns truthy or ``timeout`` elapses.

    Returns the last truthy value (or False on timeout). Bounded -- never an
    unbounded sleep.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    return False


@pytest.fixture(autouse=True)
def _patch_data_engine_construction(monkeypatch):
    """Force the credentials-ABSENT branch so start() always builds a
    MockDataEngine and never touches FRED / real DataEngine construction."""
    monkeypatch.setattr(daemon_runtime.os.path, "exists", lambda p: False)


@pytest.fixture(autouse=True)
def _patch_engine_context_build(monkeypatch):
    """EngineContext.build() constructs real heavy engines (MacroEngine,
    ForecastingEngine, ...). Replace with a cheap stand-in so tests stay fast
    and offline; the daemon only needs *some* object to mark engines_warm."""
    monkeypatch.setattr(
        main_orchestrator.EngineContext, "build",
        classmethod(lambda cls, *, data_engine=None: cls()),
    )


def _fast_ok_main_body(monkeypatch):
    async def _fake(*_a, **_k):
        return None
    monkeypatch.setattr(main_orchestrator, "_main_body", _fake)


class TestStartLifecycle:
    def test_start_builds_warm_engines(self, monkeypatch):
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon()
        d.start()
        try:
            status = d.status()
            assert status["engines_warm"] is True
            assert status["started_at"] is not None
        finally:
            d.shutdown(timeout=2.0)

    def test_start_twice_logs_warning_and_noops(self, monkeypatch, caplog):
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon()
        d.start()
        try:
            engines_after_first = d._engines
            with caplog.at_level("WARNING", logger="OrchestratorDaemon"):
                d.start()
            assert engines_after_first is d._engines  # not rebuilt
            assert any("twice" in rec.message for rec in caplog.records)
        finally:
            d.shutdown(timeout=2.0)


class TestTriggerRunHappyPath:
    def test_accepted_then_succeeds(self, monkeypatch):
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon()
        d.start()
        try:
            result = d.trigger_run(reason="manual")
            assert result.outcome == TriggerOutcome.ACCEPTED
            assert result.run_id

            completed = _poll_until(lambda: not d.is_running, timeout=3.0)
            assert completed, "run never completed within timeout"

            status = d.status()
            assert status["last_run"] is not None
            assert status["last_run"].state == RunState.SUCCEEDED
            assert status["last_run"].run_id == result.run_id
            assert status["is_running"] is False
        finally:
            d.shutdown(timeout=2.0)


class TestSingleFlight:
    def test_second_trigger_while_running_returns_already_running_same_id(self, monkeypatch):
        release_event = threading.Event()
        entered_event = threading.Event()

        async def _blocking(*_a, **_k):
            entered_event.set()
            # Block the async call until the test releases it. Use a thread-
            # friendly wait via asyncio.to_thread so the event loop can still
            # be "running" while the underlying OS thread blocks.
            import asyncio
            await asyncio.to_thread(release_event.wait)

        monkeypatch.setattr(main_orchestrator, "_main_body", _blocking)

        d = OrchestratorDaemon()
        d.start()
        try:
            first = d.trigger_run(reason="manual")
            assert first.outcome == TriggerOutcome.ACCEPTED

            assert entered_event.wait(timeout=3.0), "first run never entered _main_body"
            assert d.is_running is True

            second = d.trigger_run(reason="manual")
            assert second.outcome == TriggerOutcome.ALREADY_RUNNING
            assert second.run_id == first.run_id

            release_event.set()
            completed = _poll_until(lambda: not d.is_running, timeout=3.0)
            assert completed, "first run never completed after release"

            record = d.get_run(first.run_id)
            assert record is not None
            assert record.state == RunState.SUCCEEDED
        finally:
            release_event.set()
            d.shutdown(timeout=2.0)


class TestFatalErrorSurvival:
    def test_pipeline_fatal_error_marks_failed_and_daemon_survives(self, monkeypatch):
        """The core promise of this redesign: a PipelineFatalError from one
        cycle must never leave the daemon stuck 'running' or otherwise dead
        -- a subsequent trigger_run() must succeed normally."""
        async def _boom(*_a, **_k):
            raise main_orchestrator.PipelineFatalError("simulated fatal pipeline failure")

        monkeypatch.setattr(main_orchestrator, "_main_body", _boom)

        d = OrchestratorDaemon()
        d.start()
        try:
            result = d.trigger_run(reason="manual")
            completed = _poll_until(lambda: not d.is_running, timeout=3.0)
            assert completed, "daemon appears stuck 'running' after a PipelineFatalError -- it must survive"

            record = d.get_run(result.run_id)
            assert record is not None
            assert record.state == RunState.FAILED
            assert "simulated fatal pipeline failure" in record.error

            # The daemon must still be usable afterwards.
            _fast_ok_main_body(monkeypatch)
            second = d.trigger_run(reason="manual")
            assert second.outcome == TriggerOutcome.ACCEPTED
            completed2 = _poll_until(lambda: not d.is_running, timeout=3.0)
            assert completed2, "daemon did not survive a PipelineFatalError -- second run never completed"
            second_record = d.get_run(second.run_id)
            assert second_record.state == RunState.SUCCEEDED
        finally:
            d.shutdown(timeout=2.0)

    def test_unexpected_exception_marks_failed_and_daemon_survives(self, monkeypatch):
        """Same guarantee, but for an arbitrary unexpected bug (not
        PipelineFatalError) -- belt-and-suspenders path."""
        async def _boom(*_a, **_k):
            raise ValueError("totally unexpected bug")

        monkeypatch.setattr(main_orchestrator, "_main_body", _boom)

        d = OrchestratorDaemon()
        d.start()
        try:
            result = d.trigger_run(reason="manual")
            completed = _poll_until(lambda: not d.is_running, timeout=3.0)
            assert completed, "daemon appears stuck 'running' after an unexpected exception -- it must survive"

            record = d.get_run(result.run_id)
            assert record is not None
            assert record.state == RunState.FAILED
            assert "totally unexpected bug" in record.error

            _fast_ok_main_body(monkeypatch)
            second = d.trigger_run(reason="manual")
            assert second.outcome == TriggerOutcome.ACCEPTED
            completed2 = _poll_until(lambda: not d.is_running, timeout=3.0)
            assert completed2, "daemon did not survive an unexpected exception -- second run never completed"
            second_record = d.get_run(second.run_id)
            assert second_record.state == RunState.SUCCEEDED
        finally:
            d.shutdown(timeout=2.0)


class TestBoundedRunHistory:
    def test_history_evicts_oldest(self, monkeypatch):
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon(run_history_size=3)
        d.start()
        try:
            run_ids = []
            for _ in range(5):
                result = d.trigger_run(reason="manual")
                run_ids.append(result.run_id)
                completed = _poll_until(lambda: not d.is_running, timeout=3.0)
                assert completed, "run never completed within timeout"

            # Oldest two evicted, most recent three retained.
            for evicted_id in run_ids[:2]:
                assert d.get_run(evicted_id) is None
            for kept_id in run_ids[2:]:
                record = d.get_run(kept_id)
                assert record is not None
                assert record.state == RunState.SUCCEEDED
        finally:
            d.shutdown(timeout=2.0)


class TestGetRunUnknown:
    def test_returns_none_for_unknown_id(self, monkeypatch):
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon()
        d.start()
        try:
            assert d.get_run("does-not-exist") is None
        finally:
            d.shutdown(timeout=2.0)


class TestTimerThread:
    def test_interval_triggers_runs_and_stops_on_shutdown(self, monkeypatch):
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon(interval_seconds=1)
        d.start()
        try:
            saw_interval_run = _poll_until(
                lambda: any(
                    d.get_run(rid) is not None and d.get_run(rid).reason == "interval"
                    for rid in list(d._run_order)
                ),
                timeout=3.0,
            )
            assert saw_interval_run, "no interval-triggered run completed within timeout"
        finally:
            d.shutdown(timeout=2.0)

        # After shutdown, no new runs should appear.
        history_size_after_shutdown = len(d._run_order)
        time.sleep(1.5)
        assert len(d._run_order) == history_size_after_shutdown, (
            "timer thread kept triggering runs after shutdown()"
        )


class TestShutdownWaitsForInFlightRun:
    def test_shutdown_waits_rather_than_killing(self, monkeypatch):
        async def _slow_ok(*_a, **_k):
            import asyncio
            await asyncio.sleep(0.3)

        monkeypatch.setattr(main_orchestrator, "_main_body", _slow_ok)

        d = OrchestratorDaemon()
        d.start()

        result = d.trigger_run(reason="manual")
        assert result.outcome == TriggerOutcome.ACCEPTED

        d.shutdown(timeout=2.0)

        # shutdown() must not return until the in-flight run is done.
        record = d.get_run(result.run_id)
        assert record is not None
        assert record.state == RunState.SUCCEEDED
        assert d.is_running is False
