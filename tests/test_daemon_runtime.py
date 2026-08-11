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

import json
import pathlib
import signal
import threading
import time
from unittest import mock

import pytest

import desktop.daemon_runtime as daemon_runtime
import main_orchestrator
from desktop.daemon_runtime import (
    OrchestratorDaemon,
    RunState,
    TriggerOutcome,
)
from desktop.run_history_store import RunHistoryStore
from tests._db_isolation import redirect_class_to_memory_db


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
    MockDataEngine and never touches FRED / real DataEngine construction.

    Scoped to the literal "credentials.json" path daemon_runtime.py:255
    actually checks (``os.path.exists("credentials.json")``) -- NOT a blanket
    ``lambda p: False``. ``daemon_runtime.os`` is the real, process-wide
    ``os`` module (not a copy), so an unscoped patch here replaces
    ``os.path.exists`` for the whole process, not just this one call site.
    Under Python 3.13+ (this repo is pinned to 3.12 via .venv, but a stray
    unscoped patch would silently be wrong on any newer interpreter),
    ``pathlib.Path.exists()`` was rewritten to delegate straight to
    ``os.path.exists`` -- so a blanket ``False`` also makes every
    ``Path(...).exists()`` check in the SAME process report "missing" during
    these tests, including ``runtime_flags.load_store()``'s real
    ``resolved.exists()`` check against a genuinely-written test fixture
    file. That silently emptied ApplyReport in
    TestMaybeRefreshSettingsAppliesChanges/IntervalHook/NeverRaises.
    """
    real_exists = daemon_runtime.os.path.exists
    monkeypatch.setattr(
        daemon_runtime.os.path,
        "exists",
        lambda p: False if p == "credentials.json" else real_exists(p),
    )


@pytest.fixture(autouse=True)
def _patch_engine_context_build(monkeypatch):
    """EngineContext.build() constructs real heavy engines (MacroEngine,
    ForecastingEngine, ...). Replace with a cheap stand-in so tests stay fast
    and offline; the daemon only needs *some* object to mark engines_warm."""
    monkeypatch.setattr(
        main_orchestrator.EngineContext, "build",
        classmethod(lambda cls, *, data_engine=None: cls()),
    )


@pytest.fixture(autouse=True)
def _isolate_run_history_db():
    """_run_one_cycle persists every completed run to RunHistoryStore (see
    desktop/run_history_store.py), which defaults to the real, git-untracked
    quant_platform.db when constructed with no db_url. Every test in this
    file drives a real _run_one_cycle to completion, so without this
    redirect they'd all write test run records into that on-disk file."""
    with redirect_class_to_memory_db(RunHistoryStore):
        yield


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


class TestProgressSnapshotStamping:
    """RunRecord.progress -- the plain-dict snapshot of output/progress.json
    taken at cycle completion (see _run_one_cycle). Covers the run_id
    correlation fix: main_orchestrator's internally-constructed
    ProgressReporter has no notion of the daemon's run_id, so progress.json
    on disk always carries "run_id": null -- _run_one_cycle must overwrite
    that with the daemon's own run_id so RunRecord.progress["run_id"] always
    agrees with RunRecord.run_id (the two describe the same cycle)."""

    def test_progress_run_id_overwritten_with_daemon_run_id(self, monkeypatch):
        from datetime import datetime, timezone
        from reporting.progress import ProgressState

        _fast_ok_main_body(monkeypatch)

        # Simulate the real on-disk contract: progress.json's own "run_id" is
        # always None (main_orchestrator never threads one into its internal
        # ProgressReporter) -- this is what read_progress() actually returns
        # in production, not a test artifact.
        fake_state = ProgressState(
            run_id=None,
            state="succeeded",
            stage="execution",
            stage_index=5,
            stage_total=6,
            symbols_done=10,
            symbols_total=10,
            percent=100.0,
            message="succeeded",
            started_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        monkeypatch.setattr(daemon_runtime, "read_progress", lambda: fake_state)

        d = OrchestratorDaemon()
        d.start()
        try:
            result = d.trigger_run(reason="manual")
            completed = _poll_until(lambda: not d.is_running, timeout=3.0)
            assert completed, "run never completed within timeout"

            record = d.status()["last_run"]
            assert record.progress is not None
            # The fix: RunRecord.progress["run_id"] must agree with the
            # daemon's real run_id, not the (always-null) value on disk.
            assert record.progress["run_id"] == result.run_id == record.run_id
            # Every other field passes through from ProgressState unchanged.
            assert record.progress["percent"] == 100.0
            assert record.progress["stage"] == "execution"
            assert record.progress["symbols_done"] == 10
            assert record.progress["symbols_total"] == 10
        finally:
            d.shutdown(timeout=2.0)

    def test_progress_none_when_unavailable(self, monkeypatch):
        """No progress.json yet (or the read failed) -- read_progress()
        returns None -- RunRecord.progress must be None, never a fabricated
        snapshot (CONSTRAINT #4)."""
        _fast_ok_main_body(monkeypatch)
        monkeypatch.setattr(daemon_runtime, "read_progress", lambda: None)

        d = OrchestratorDaemon()
        d.start()
        try:
            d.trigger_run(reason="manual")
            completed = _poll_until(lambda: not d.is_running, timeout=3.0)
            assert completed, "run never completed within timeout"

            record = d.status()["last_run"]
            assert record.progress is None
        finally:
            d.shutdown(timeout=2.0)

    def test_progress_read_failure_degrades_to_none_not_raise(self, monkeypatch):
        """A read_progress() exception must never abort the run or leave the
        record un-built (CONSTRAINT #6) -- the run still completes and
        RunRecord.progress degrades to None."""
        def _boom():
            raise OSError("disk full")

        _fast_ok_main_body(monkeypatch)
        monkeypatch.setattr(daemon_runtime, "read_progress", _boom)

        d = OrchestratorDaemon()
        d.start()
        try:
            d.trigger_run(reason="manual")
            completed = _poll_until(lambda: not d.is_running, timeout=3.0)
            assert completed, "run never completed within timeout"

            record = d.status()["last_run"]
            assert record.state == RunState.SUCCEEDED  # unaffected by the read failure
            assert record.progress is None
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


class TestGetRunVisibleWhileInFlight:
    """A caller polling get_run(run_id) immediately after trigger_run()
    returns must find the run in RunState.RUNNING, not a false "unknown
    run_id" -- this is the seam the control API's GET /run/{id}/status
    depends on."""

    def test_get_run_returns_running_placeholder_before_completion(self, monkeypatch):
        release_event = threading.Event()
        entered_event = threading.Event()

        async def _blocking(*_a, **_k):
            entered_event.set()
            import asyncio
            await asyncio.to_thread(release_event.wait)

        monkeypatch.setattr(main_orchestrator, "_main_body", _blocking)

        d = OrchestratorDaemon()
        d.start()
        try:
            result = d.trigger_run(reason="manual")
            assert entered_event.wait(timeout=3.0), "run never entered _main_body"

            record = d.get_run(result.run_id)
            assert record is not None, "get_run() must find an in-flight run, not just a finished one"
            assert record.state == RunState.RUNNING
            assert record.finished_at is None
            assert record.duration_seconds is None

            release_event.set()
            completed = _poll_until(lambda: not d.is_running, timeout=3.0)
            assert completed

            final_record = d.get_run(result.run_id)
            assert final_record.state == RunState.SUCCEEDED
            assert final_record.finished_at is not None
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


class TestRunHistoryPersistence:
    """_run_one_cycle persists every completed run to the durable
    pipeline_runs DB table (desktop/run_history_store.py) on top of the
    in-memory ring -- see the docstring on that call site."""

    def test_completed_run_is_persisted(self, monkeypatch):
        _fast_ok_main_body(monkeypatch)
        recorded = []
        monkeypatch.setattr(
            RunHistoryStore, "record_run",
            lambda self, record: recorded.append(record),
        )
        d = OrchestratorDaemon()
        d.start()
        try:
            result = d.trigger_run(reason="manual")
            completed = _poll_until(lambda: not d.is_running, timeout=3.0)
            assert completed, "run never completed within timeout"

            assert len(recorded) == 1
            assert recorded[0].run_id == result.run_id
            assert recorded[0].state == RunState.SUCCEEDED
        finally:
            d.shutdown(timeout=2.0)

    def test_persistence_failure_does_not_crash_daemon(self, monkeypatch, caplog):
        """A DB hiccup in RunHistoryStore must never affect the run's own
        SUCCEEDED/FAILED verdict -- only the durable table lags."""
        _fast_ok_main_body(monkeypatch)
        monkeypatch.setattr(
            RunHistoryStore, "record_run",
            lambda self, record: (_ for _ in ()).throw(RuntimeError("db is down")),
        )
        d = OrchestratorDaemon()
        d.start()
        try:
            with caplog.at_level("WARNING"):
                result = d.trigger_run(reason="manual")
                completed = _poll_until(lambda: not d.is_running, timeout=3.0)
                assert completed, "run never completed within timeout"

            record = d.get_run(result.run_id)
            assert record is not None
            assert record.state == RunState.SUCCEEDED
            assert "failed to persist run history" in caplog.text
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

    def test_shutdown_poll_loop_does_not_overshoot_a_small_timeout(self, monkeypatch):
        """Regression guard (Copilot review, PR #536): the in-flight-run poll
        loop used to sleep a FIXED 0.1s per iteration regardless of how much
        of `timeout` actually remained -- so a small timeout (e.g. 0.01s)
        could overshoot by up to 10x while still correctly reporting the run
        as in-flight. Each sleep slice must be clamped to what's left on the
        deadline."""
        import time as _time

        async def _never_finishes(*_a, **_k):
            import asyncio
            # Comfortably longer than the 0.01s shutdown timeout under test,
            # but short enough not to leave a slow background worker thread
            # lingering for the rest of the test session.
            await asyncio.sleep(0.5)

        monkeypatch.setattr(main_orchestrator, "_main_body", _never_finishes)

        d = OrchestratorDaemon()
        d.start()
        result = d.trigger_run(reason="manual")
        assert result.outcome == TriggerOutcome.ACCEPTED

        started = _time.monotonic()
        d.shutdown(timeout=0.01)
        elapsed = _time.monotonic() - started

        # Generous margin over the 0.01s budget -- this is a regression
        # guard against a full extra 0.1s sleep slice, not a tight timing
        # assertion. Before the fix this reliably took ~0.1s; after, it
        # should return in a few milliseconds.
        assert elapsed < 0.08
        assert d.is_running is True  # the 0.5s run hasn't finished yet


class TestShutdownTimerJoinBudget:
    """Regression tests for the 2026-07 shutdown-budget fix: the timer
    -thread join must be BUDGETED WITHIN shutdown()'s own `timeout`, not a
    flat extra 5.0s tacked on top. Before the fix, shutdown(timeout=T) could
    actually take up to T+5.0s -- the exact "emergent, unreconciled sum"
    defect settings.DAEMON_SHUTDOWN_TIMEOUT_SECONDS's single-published
    -budget design exists to eliminate."""

    def test_join_timeout_is_bounded_by_the_passed_timeout(self):
        d = OrchestratorDaemon()
        d.start()  # interval_seconds=0 (default) -> no real timer thread created

        recorded = {}

        class _FakeThread:
            def join(self, timeout=None):
                recorded["timeout"] = timeout

        with d._lock:
            d._timer_thread = _FakeThread()

        d.shutdown(timeout=2.0)

        assert recorded.get("timeout") is not None
        assert recorded["timeout"] <= 2.0

    def test_shutdown_returns_within_the_passed_timeout_even_with_a_slow_join(self):
        """A timer thread whose join() blocks for its full allotted slice
        must not make the OVERALL shutdown() call exceed `timeout`."""
        d = OrchestratorDaemon()
        d.start()

        class _SlowJoinThread:
            def join(self, timeout=None):
                time.sleep(timeout or 0)

        with d._lock:
            d._timer_thread = _SlowJoinThread()

        started = time.monotonic()
        d.shutdown(timeout=1.0)
        elapsed = time.monotonic() - started

        # Generous margin over the 1.0s budget -- this is a regression guard
        # against an ADDITIONAL flat 5.0s, not a tight timing assertion.
        assert elapsed <= 2.0


# =============================================================================
# Live interval setter (Piece 2)
# =============================================================================


class TestSetIntervalStateMutation:
    def test_state_and_wake_event_updated_without_wall_clock(self, monkeypatch):
        """set_interval() updates _interval_seconds and signals _wake_event
        synchronously -- proven without any wall-clock wait, by pre-seeding a
        dummy (never-started) _timer_thread so set_interval() sees a thread
        already "exists" and doesn't spawn a real one that would immediately
        race-consume the event via its own clear()."""
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon()
        d._timer_thread = threading.Thread(target=lambda: None)  # pre-seeded stand-in

        d.set_interval(300)

        assert d._interval_seconds == 300
        assert d._wake_event.is_set()
        assert d.status()["interval_seconds"] == 300


class TestSetIntervalValidation:
    @pytest.mark.parametrize("bad_value", [-1, 1, 59, 86401])
    def test_invalid_values_raise_and_do_not_mutate_state(self, bad_value):
        d = OrchestratorDaemon(interval_seconds=42)
        with pytest.raises(ValueError):
            d.set_interval(bad_value)
        assert d._interval_seconds == 42  # unchanged -- rejected before mutation
        assert d._timer_thread is None  # no thread spun up for a rejected value

    @pytest.mark.parametrize("good_value", [0, 60, 86400])
    def test_boundary_values_are_accepted(self, monkeypatch, good_value):
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon()
        try:
            d.set_interval(good_value)
            assert d._interval_seconds == good_value
        finally:
            d.shutdown(timeout=2.0)


class TestSetIntervalCreatesThreadWhenNoneExists:
    def test_zero_to_nonzero_creates_the_thread(self, monkeypatch):
        """start() only creates the timer thread when interval_seconds > 0
        at startup -- a daemon started at the default (0, on-demand only)
        has no thread. set_interval() must create one on demand so a later
        cadence change actually has something to signal."""
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon()  # interval_seconds=0 by default
        d.start()
        try:
            assert d._timer_thread is None

            d.set_interval(60)

            assert d._timer_thread is not None
            assert d._timer_thread.is_alive()
            assert d.status()["interval_seconds"] == 60
        finally:
            d.shutdown(timeout=2.0)


class TestNoSpinAtIntervalZero:
    def test_transitioning_to_zero_parks_instead_of_spinning(self, monkeypatch):
        """The bug this design exists to prevent: the OLD timer loop used
        ``self._stop_event.wait(self._interval_seconds)``, which for
        interval_seconds == 0 becomes Event.wait(0) -- returning almost
        instantly and busy-looping trigger_run() thousands of times a
        second. Starting a daemon AT interval=0 never hit this (start() does
        not create a thread for interval_seconds <= 0), but transitioning a
        LIVE thread from a positive interval down to 0 via set_interval()
        does exercise the exact code path that must now park instead."""
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon(interval_seconds=60)
        d.start()
        try:
            assert d._timer_thread is not None

            d.set_interval(0)
            time.sleep(0.3)  # bounded window -- see module docstring re: :391-413

            interval_runs = [
                rid for rid in list(d._run_order)
                if (rec := d.get_run(rid)) is not None and rec.reason == "interval"
            ]
            assert len(interval_runs) == 0, (
                f"expected zero interval-triggered runs while parked at "
                f"interval=0, got {len(interval_runs)} -- the timer loop is "
                f"spinning instead of parking"
            )
        finally:
            d.shutdown(timeout=2.0)


class TestTimerLoopRaceOrdering:
    """Pins the clear-BEFORE-read ordering _timer_loop's comment depends on,
    deterministically -- via the same Event-handshake pattern as
    TestSingleFlight, not a sleep-and-hope race."""

    def test_set_interval_during_clear_to_read_window_is_observed(self, monkeypatch):
        _fast_ok_main_body(monkeypatch)

        entered_clear = threading.Event()
        release_clear = threading.Event()
        observed_timeouts: list = []

        d = OrchestratorDaemon(interval_seconds=100_000)  # effectively "never fires"
        original_clear = d._wake_event.clear
        original_wait = d._wake_event.wait
        call_count = {"clear": 0}
        # clear() is called 3 times before the window we want to land in:
        # (1) start()'s own defensive clear, on the MAIN/test thread, before
        #     the timer thread even exists; (2) the timer thread's first
        #     loop iteration, right before its first (real, long) wait();
        #     (3) the timer thread's SECOND iteration -- reached only after
        #     we wake iteration 1's wait() below -- which is the exact
        #     "clear() has run, read hasn't yet" window the ordering
        #     invariant is about.
        INTERCEPT_AT = 3

        def _clear_wrapper():
            call_count["clear"] += 1
            original_clear()
            if call_count["clear"] == INTERCEPT_AT:
                entered_clear.set()
                assert release_clear.wait(timeout=3.0), "test never released clear()"

        def _wait_wrapper(timeout=None):
            observed_timeouts.append(timeout)
            return original_wait(timeout)

        monkeypatch.setattr(d._wake_event, "clear", _clear_wrapper)
        monkeypatch.setattr(d._wake_event, "wait", _wait_wrapper)

        d.start()
        try:
            # Let iteration 1 reach its (real) first wait(timeout=100_000)
            # call, then wake it directly so the loop proceeds to
            # iteration 2's clear() call -- call #3, where we've arranged
            # to intercept.
            reached_first_wait = _poll_until(lambda: len(observed_timeouts) >= 1, timeout=3.0)
            assert reached_first_wait, "timer loop never reached its first wait() call"
            d._wake_event.set()

            assert entered_clear.wait(timeout=3.0), (
                "timer loop never reached the targeted (second-iteration) clear() call"
            )

            # Fire set_interval() from here -- exactly between the loop's
            # clear() and its read of self._interval_seconds.
            d.set_interval(300)
            release_clear.set()

            # The loop must read interval=300 (not the stale 100_000), see
            # wake_event already set (by set_interval), and take one
            # harmless spurious pass before parking again on the NEW value.
            saw_second_wait = _poll_until(lambda: len(observed_timeouts) >= 2, timeout=3.0)
            assert saw_second_wait, "timer loop never reached its second wait() call"
            assert observed_timeouts[1] == 300, (
                f"expected the loop's second wait() to use the NEW interval "
                f"(300), got {observed_timeouts[1]} -- it read a stale "
                f"value, proving clear() ran AFTER the interval read "
                f"instead of before it"
            )
        finally:
            d.shutdown(timeout=2.0)


class TestShutdownWhileParked:
    def test_shutdown_exits_promptly_from_parked_state(self, monkeypatch):
        """A PARKED loop (interval <= 0) blocks on an UNTIMED
        wake_event.wait() -- only _wake_event, not _stop_event, can reach
        it. shutdown() must set both, or this test would hang until its own
        internal deadline/poll loop gives up."""
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon()
        d.start()  # interval_seconds=0 -> no thread yet

        wait_calls: list = []
        original_wait = d._wake_event.wait

        def _wait_wrapper(timeout=None):
            wait_calls.append(timeout)
            return original_wait(timeout)

        monkeypatch.setattr(d._wake_event, "wait", _wait_wrapper)

        d.set_interval(0)  # creates + starts the thread; it will park

        reached_park = _poll_until(lambda: len(wait_calls) >= 1, timeout=2.0)
        assert reached_park, "timer thread never reached its parked wait() call"
        assert all(c is None for c in wait_calls), (
            "a parked (interval<=0) loop must call wake_event.wait() with no "
            "timeout, never a timed wait"
        )

        start = time.monotonic()
        d.shutdown(timeout=2.0)
        elapsed = time.monotonic() - start

        assert elapsed < 1.5, (
            f"shutdown() took {elapsed:.2f}s to return a PARKED timer thread -- "
            f"shutdown() must set _wake_event (not just _stop_event) so an "
            f"untimed wake_event.wait() actually wakes"
        )


@pytest.mark.skipif(
    not hasattr(signal, "pthread_sigmask"), reason="pthread_sigmask is POSIX-only"
)
class TestTimerThreadSignalMaskInheritance:
    """desktop/orchestrator_daemon.py blocks SIGTERM/SIGINT on the main
    thread via signal.pthread_sigmask(SIG_BLOCK, ...) BEFORE any thread is
    created, specifically so every thread spawned afterward inherits the
    blocked mask (POSIX: a new thread inherits the CALLING thread's mask at
    the moment it is created, not the process's mask). A timer thread
    created LATE by set_interval() -- e.g. from a live uvicorn
    request-handler thread, long after startup -- must inherit that mask
    too, transitively, through whichever thread happens to call
    set_interval(). This test proves that inheritance actually holds for a
    thread created well after daemon startup, not just for one created at
    daemon.start() time (see tests/test_orchestrator_daemon.py's
    TestSignalHandling for the *ordering* half of this property; neither
    test alone covers both halves)."""

    def test_thread_created_by_set_interval_inherits_blocked_sigterm(self, monkeypatch):
        _fast_ok_main_body(monkeypatch)
        prior_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        try:
            d = OrchestratorDaemon()
            d.start()  # interval_seconds=0 -> no thread yet
            try:
                assert d._timer_thread is None

                observed_mask: dict = {}
                captured = threading.Event()
                original_timer_loop = d._timer_loop

                def _instrumented_timer_loop():
                    observed_mask["mask"] = signal.pthread_sigmask(signal.SIG_BLOCK, set())
                    captured.set()
                    original_timer_loop()

                monkeypatch.setattr(d, "_timer_loop", _instrumented_timer_loop)

                # Simulates a request-handler thread calling set_interval()
                # well after the process-startup SIGTERM block -- the mask
                # is already blocked on THIS (the calling/test) thread, so
                # the new timer thread it spawns must inherit it.
                d.set_interval(60)

                assert captured.wait(timeout=3.0), "timer thread never started"
                assert signal.SIGTERM in observed_mask["mask"], (
                    "timer thread created by set_interval() did not inherit "
                    "the SIGTERM-blocked mask from the thread that created "
                    "it -- this is the property desktop/orchestrator_daemon.py's "
                    "startup ordering depends on"
                )
            finally:
                d.shutdown(timeout=2.0)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, prior_mask)


def _write_store(tmp_path, flags: dict, *, version: int = 1):
    """A runtime_flags.json-shaped fixture file, matching the real
    {"version": 1, "flags": {KEY: {"value": ..., "updated_at": ...,
    "updated_by": ...}}} shape runtime_flags.load_store() expects. Each
    value in `flags` here is the RAW value; this wraps it in the envelope."""
    store = tmp_path / "runtime_flags.json"
    store.write_text(
        json.dumps({
            "version": version,
            "flags": {
                k: {"value": v, "updated_at": "2026-01-01T00:00:00+00:00", "updated_by": "test"}
                for k, v in flags.items()
            },
        }),
        encoding="utf-8",
    )
    return store


@pytest.fixture(autouse=True)
def _no_env_pins(monkeypatch):
    """Env-pinning is a per-moment fact about the real shell environment
    (see runtime_flags.real_environment_keys()) -- pin it empty so whether a
    field counts as pinned in these tests depends only on what each test
    itself sets up, never on the CI/developer machine's ambient environment.
    Same rationale as tests/test_runtime_flags.py's own no_dotenv-style
    fixtures."""
    monkeypatch.setattr(daemon_runtime.runtime_flags, "real_environment_keys", lambda: frozenset())


class TestMaybeRefreshSettingsDeferral:
    def test_returns_none_and_does_nothing_while_a_run_is_in_flight(self, monkeypatch, tmp_path):
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon()
        d.start()
        try:
            store = _write_store(tmp_path, {"KELLY_FRACTION": 0.6})
            with mock.patch.object(
                OrchestratorDaemon, "is_running", new_callable=mock.PropertyMock, return_value=True
            ):
                with mock.patch.object(daemon_runtime.runtime_flags, "apply_overrides") as spy:
                    result = d.maybe_refresh_settings(path=store)
            assert result is None
            spy.assert_not_called()
        finally:
            d.shutdown(timeout=2.0)


class TestMaybeRefreshSettingsNoStore:
    def test_no_file_is_a_quiet_noop(self, monkeypatch, tmp_path):
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon()
        d.start()
        try:
            missing = tmp_path / "does-not-exist.json"
            assert d.maybe_refresh_settings(path=missing) is None
            # And it remembers having checked -- a second call against the
            # same still-missing path is ALSO a fast no-op (covered by
            # TestMaybeRefreshSettingsChangeDetection below more directly,
            # but worth a light touch here too).
            with mock.patch.object(daemon_runtime.runtime_flags, "apply_overrides") as spy:
                assert d.maybe_refresh_settings(path=missing) is None
            spy.assert_not_called()
        finally:
            d.shutdown(timeout=2.0)


class TestMaybeRefreshSettingsAppliesChanges:
    def test_first_check_with_a_real_store_applies_it(self, monkeypatch, tmp_path):
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon()
        d.start()
        try:
            store = _write_store(tmp_path, {"KELLY_FRACTION": 0.6})
            report = d.maybe_refresh_settings(path=store)
            assert report is not None
            assert report.applied == {"KELLY_FRACTION": 0.6}

            from settings import settings
            assert settings.KELLY_FRACTION == 0.6
        finally:
            d.shutdown(timeout=2.0)

    def test_unchanged_file_on_the_next_check_is_a_noop(self, monkeypatch, tmp_path):
        """The whole point of the mtime/size check: a poll tick against an
        UNCHANGED store must not pay the cost of re-validating and
        re-applying the same content."""
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon()
        d.start()
        try:
            store = _write_store(tmp_path, {"KELLY_FRACTION": 0.6})
            first = d.maybe_refresh_settings(path=store)
            assert first is not None

            with mock.patch.object(daemon_runtime.runtime_flags, "apply_overrides") as spy:
                second = d.maybe_refresh_settings(path=store)
            assert second is None
            spy.assert_not_called()
        finally:
            d.shutdown(timeout=2.0)

    def test_a_real_content_change_is_detected_and_reapplied(self, monkeypatch, tmp_path):
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon()
        d.start()
        try:
            store = _write_store(tmp_path, {"KELLY_FRACTION": 0.6})
            d.maybe_refresh_settings(path=store)

            # Overwrite with different content. Real filesystems can have
            # coarse mtime resolution; rewriting with a different SIZE (a
            # differently-formatted float) is what actually guarantees the
            # (mtime_ns, size) tuple changes here, independent of clock
            # granularity.
            _write_store(tmp_path, {"KELLY_FRACTION": 0.75})

            report = d.maybe_refresh_settings(path=store)
            assert report is not None
            assert report.applied == {"KELLY_FRACTION": 0.75}

            from settings import settings
            assert settings.KELLY_FRACTION == 0.75
        finally:
            d.shutdown(timeout=2.0)

    def test_env_pinned_key_is_skipped_not_applied(self, monkeypatch, tmp_path):
        """Thin end-to-end confirmation that this method is wired to the
        SAME env-pin precedence apply_overrides() already enforces
        everywhere else -- a real shell export always wins over the store."""
        _fast_ok_main_body(monkeypatch)
        monkeypatch.setattr(
            daemon_runtime.runtime_flags, "real_environment_keys",
            lambda: frozenset({"KELLY_FRACTION"}),
        )
        d = OrchestratorDaemon()
        d.start()
        try:
            from settings import settings
            before = settings.KELLY_FRACTION

            store = _write_store(tmp_path, {"KELLY_FRACTION": 0.6})
            report = d.maybe_refresh_settings(path=store)

            assert report is not None
            assert "KELLY_FRACTION" in report.skipped_env_pinned
            assert "KELLY_FRACTION" not in report.applied
            assert settings.KELLY_FRACTION == before
        finally:
            d.shutdown(timeout=2.0)


class TestMaybeRefreshSettingsIntervalHook:
    def test_a_valid_interval_change_reaches_the_live_timer(self, monkeypatch, tmp_path):
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon()  # interval_seconds=0 by default
        d.start()
        try:
            assert d.status()["interval_seconds"] == 0

            store = _write_store(tmp_path, {"ORCHESTRATOR_INTERVAL_SECONDS": 300})
            report = d.maybe_refresh_settings(path=store)

            assert report.applied == {"ORCHESTRATOR_INTERVAL_SECONDS": 300}
            assert d.status()["interval_seconds"] == 300
            assert d._timer_thread is not None  # set_interval() created it on demand
        finally:
            d.shutdown(timeout=2.0)

    def test_an_out_of_bounds_interval_applies_the_setting_but_not_the_timer(
        self, monkeypatch, tmp_path, caplog
    ):
        """ORCHESTRATOR_INTERVAL_SECONDS carries no @field_validator of its
        own -- only set_interval()'s stricter business rule
        (validate_interval_seconds: 0 or [60, 86400]) enforces the bound.
        apply_overrides() therefore accepts a value like 45 that
        set_interval() then rejects. This is a genuinely reachable gap, not
        a defensive-only branch -- proven by this test actually hitting it
        -- and matches the SAME degrade-and-log behavior the established
        write path (api/pilots_api.py's set_automation_interval) already
        has for a live-apply rejection: the .env/settings write stands, the
        live timer does not move, and both facts are surfaced rather than
        one silently winning."""
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon()
        d.start()
        try:
            assert d.status()["interval_seconds"] == 0

            store = _write_store(tmp_path, {"ORCHESTRATOR_INTERVAL_SECONDS": 45})
            with caplog.at_level("WARNING", logger="OrchestratorDaemon"):
                report = d.maybe_refresh_settings(path=store)

            assert report.applied == {"ORCHESTRATOR_INTERVAL_SECONDS": 45}
            # The setting itself moved...
            from settings import settings
            assert settings.ORCHESTRATOR_INTERVAL_SECONDS == 45
            # ...but the running timer's cadence did not, and no thread was
            # spun up for a value that was never actually accepted live.
            assert d.status()["interval_seconds"] == 0
            assert d._timer_thread is None
            assert any(
                "ORCHESTRATOR_INTERVAL_SECONDS" in rec.message and "rejected" in rec.message
                for rec in caplog.records
            )
        finally:
            d.shutdown(timeout=2.0)

    def test_other_keys_changing_do_not_touch_the_timer(self, monkeypatch, tmp_path):
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon(interval_seconds=120)
        d.start()
        try:
            store = _write_store(tmp_path, {"KELLY_FRACTION": 0.6})
            with mock.patch.object(d, "set_interval") as spy:
                d.maybe_refresh_settings(path=store)
            spy.assert_not_called()
            assert d.status()["interval_seconds"] == 120
        finally:
            d.shutdown(timeout=2.0)


class TestMaybeRefreshSettingsNeverRaises:
    """CONSTRAINT #6: every failure mode here degrades to 'try again next
    tick,' never an exception escaping into the caller's polling loop."""

    def test_corrupt_json_applies_nothing_and_does_not_raise(self, monkeypatch, tmp_path):
        """The file exists (so the os.stat() half of this method succeeds
        and treats it as "possibly changed"), but its content doesn't parse
        -- runtime_flags.apply_overrides() degrades this to an ApplyReport
        carrying .error and an empty .applied, per its own CONSTRAINT #6
        contract, rather than raising. That report -- not None -- is what
        this method correctly passes back; the only thing under test here
        is that nothing raises and nothing gets applied."""
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon()
        d.start()
        try:
            store = tmp_path / "runtime_flags.json"
            store.write_text("{not valid json", encoding="utf-8")
            report = d.maybe_refresh_settings(path=store)
            assert report is not None
            assert report.applied == {}
            assert report.error is not None
        finally:
            d.shutdown(timeout=2.0)

    def test_unexpected_stat_failure_degrades_to_none(self, monkeypatch, tmp_path):
        """FileNotFoundError is the one EXPECTED stat() outcome (handled as
        "no store"); anything else -- e.g. a permissions error -- must still
        degrade rather than propagate."""
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon()
        d.start()
        try:
            store = _write_store(tmp_path, {"KELLY_FRACTION": 0.6})
            with mock.patch.object(
                pathlib.Path, "stat", side_effect=PermissionError("denied")
            ):
                assert d.maybe_refresh_settings(path=store) is None
        finally:
            d.shutdown(timeout=2.0)

    def test_apply_overrides_raising_unexpectedly_still_degrades(self, monkeypatch, tmp_path):
        """apply_overrides() is itself documented to never raise -- this
        proves the method doesn't SILENTLY DEPEND on that holding forever."""
        _fast_ok_main_body(monkeypatch)
        d = OrchestratorDaemon()
        d.start()
        try:
            store = _write_store(tmp_path, {"KELLY_FRACTION": 0.6})
            with mock.patch.object(
                daemon_runtime.runtime_flags, "apply_overrides",
                side_effect=RuntimeError("boom"),
            ):
                assert d.maybe_refresh_settings(path=store) is None
        finally:
            d.shutdown(timeout=2.0)
