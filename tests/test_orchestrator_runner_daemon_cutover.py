"""
tests/test_orchestrator_runner_daemon_cutover.py
==================================================
PR5 — persistent-daemon GUI cutover, behind settings.ORCHESTRATOR_DAEMON_ENABLED
(default False). Two distinct cutover points, both in gui/orchestrator_runner.py:

1. launch_daemon_engine() — the always-on desktop-shell refresh loop, spawned
   by desktop/engine_supervisor.py's start_engine() when the flag is True.
   This reuses the EXACT SAME Popen/RunHandle/stop_run mechanics as
   launch_scheduled_advisory (backend="subprocess") -- just a different
   command. No dual-backend logic needed for this path.

2. launch_orchestrator()'s flag-gated HTTP fast path — the Launcher tab's
   manual "Run Pipeline" button triggers a cycle against an already-running
   daemon via gui.daemon_client instead of spawning a subprocess, producing
   a backend="daemon" RunHandle. This DOES need dual-backend logic:
   RunHandle.is_running()/returncode(), stop_run(), read_log_tail(), and
   compute_stage_status() all branch on handle.backend.

All daemon_client calls are monkeypatched -- no real HTTP, no real daemon
process. subprocess.Popen is monkeypatched via the same _FakePopen pattern
tests/test_orchestrator_runner.py already uses.
"""
from __future__ import annotations

import sys
import time
from unittest import mock

import pytest


@pytest.fixture()
def runner(monkeypatch, tmp_path):
    """Import gui.orchestrator_runner with all FS paths sandboxed to tmp_path."""
    from gui import orchestrator_runner as runner

    monkeypatch.setattr(runner, "RUN_LOG_PATH", tmp_path / "gui_run.log")
    monkeypatch.setattr(runner, "DAEMON_LOG_PATH", tmp_path / "gui_daemon.log")
    monkeypatch.setattr(runner, "SCHEDULED_LOG_PATH", tmp_path / "gui_scheduled.log")
    monkeypatch.setattr(runner.settings, "OUTPUT_DIR", tmp_path)
    return runner


class _FakePopen:
    def __init__(self, cmd, **kwargs):
        self.args = cmd
        self.pid = 4242
        self.kwargs = kwargs
        self._polled = None

    def poll(self):
        return self._polled


# ---------------------------------------------------------------------------
# 1. launch_daemon_engine — always-on loop, subprocess backend (no dual-logic)
# ---------------------------------------------------------------------------

class TestLaunchDaemonEngine:
    def test_spawns_daemon_module_with_interval(self, monkeypatch, runner):
        captured = {}

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            return _FakePopen(cmd, **kwargs)

        monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
        handle = runner.launch_daemon_engine(interval_seconds=60)

        assert captured["cmd"] == [
            sys.executable, "-m", "desktop.orchestrator_daemon", "--interval", "60",
        ]
        assert handle.mode == "daemon"
        assert handle.backend == "subprocess"
        assert handle.log_path == runner.DAEMON_LOG_PATH
        assert runner.DAEMON_LOG_PATH.exists()

    def test_interval_clamped_to_minimum_30(self, monkeypatch, runner):
        captured = {}

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            return _FakePopen(cmd, **kwargs)

        monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
        runner.launch_daemon_engine(interval_seconds=5)

        assert "--interval" in captured["cmd"]
        idx = captured["cmd"].index("--interval")
        assert captured["cmd"][idx + 1] == "30"

    def test_refresh_account_is_silently_ignored(self, monkeypatch, runner):
        """The daemon entrypoint has no --refresh-account flag; the kwarg
        must not appear in the spawned command, and must not raise."""
        captured = {}

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            return _FakePopen(cmd, **kwargs)

        monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
        handle = runner.launch_daemon_engine(interval_seconds=60, refresh_account=True)

        assert "--refresh-account" not in captured["cmd"]
        assert handle.refresh_account is False

    def test_returned_handle_uses_ordinary_subprocess_mechanics(self, monkeypatch, runner):
        """is_running()/returncode() for a daemon-engine handle must use the
        plain Popen path -- no dual-backend branching applies here, since
        backend defaults to "subprocess"."""
        fake = _FakePopen(["x"])

        def fake_popen(cmd, **kwargs):
            return fake

        monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
        handle = runner.launch_daemon_engine(interval_seconds=60)

        assert handle.is_running() is True
        fake._polled = 0
        assert handle.is_running() is False
        assert handle.returncode() == 0


# ---------------------------------------------------------------------------
# 2. desktop/engine_supervisor.start_engine — flag branching
# ---------------------------------------------------------------------------

class TestEngineSupervisorFlagBranching:
    def test_flag_off_routes_to_launch_scheduled_advisory(self, monkeypatch):
        import gui.orchestrator_runner as orchestrator_runner
        from desktop.engine_supervisor import start_engine

        monkeypatch.setattr(orchestrator_runner.settings, "ORCHESTRATOR_DAEMON_ENABLED", False)
        sentinel = object()
        captured = {}

        def fake_launch_scheduled_advisory(mode, interval_seconds, *, refresh_account=False):
            captured["called"] = True
            return sentinel

        def fake_launch_daemon_engine(*a, **k):
            captured["daemon_called"] = True
            return sentinel

        monkeypatch.setattr(orchestrator_runner, "launch_scheduled_advisory", fake_launch_scheduled_advisory)
        monkeypatch.setattr(orchestrator_runner, "launch_daemon_engine", fake_launch_daemon_engine)

        result = start_engine(120)

        assert captured.get("called") is True
        assert "daemon_called" not in captured
        assert result is sentinel

    def test_flag_on_routes_to_launch_daemon_engine(self, monkeypatch):
        import gui.orchestrator_runner as orchestrator_runner
        from desktop.engine_supervisor import start_engine

        monkeypatch.setattr(orchestrator_runner.settings, "ORCHESTRATOR_DAEMON_ENABLED", True)
        sentinel = object()
        captured = {}

        def fake_launch_daemon_engine(interval_seconds, *, refresh_account=False):
            captured["interval_seconds"] = interval_seconds
            captured["refresh_account"] = refresh_account
            return sentinel

        def fake_launch_scheduled_advisory(*a, **k):
            captured["scheduled_called"] = True
            return sentinel

        monkeypatch.setattr(orchestrator_runner, "launch_daemon_engine", fake_launch_daemon_engine)
        monkeypatch.setattr(orchestrator_runner, "launch_scheduled_advisory", fake_launch_scheduled_advisory)

        result = start_engine(90, refresh_account=True)

        assert captured == {"interval_seconds": 90, "refresh_account": True}
        assert "scheduled_called" not in captured
        assert result is sentinel

    def test_stop_engine_unchanged_regardless_of_flag(self, monkeypatch):
        """stop_engine needs no flag-awareness -- it's a pure pass-through
        to stop_run() for both backends."""
        import gui.orchestrator_runner as orchestrator_runner
        from desktop.engine_supervisor import stop_engine

        captured = {}

        def fake_stop_run(handle, *, timeout=5.0):
            captured["handle"] = handle
            captured["timeout"] = timeout
            return True

        monkeypatch.setattr(orchestrator_runner, "stop_run", fake_stop_run)
        handle = object()
        result = stop_engine(handle, timeout=3.0)

        assert captured == {"handle": handle, "timeout": 3.0}
        assert result is True


# ---------------------------------------------------------------------------
# 3. RunHandle dual-backend: is_running() / returncode()
# ---------------------------------------------------------------------------

class TestRunHandleDaemonBackend:
    def test_is_running_true_for_queued_and_running_states(self, runner, monkeypatch):
        handle = runner.RunHandle(
            pid=-1, started_at=time.time(), dry_run=False, refresh_account=False,
            backend="daemon", daemon_run_id="run-123",
        )
        for state in ("queued", "running"):
            monkeypatch.setattr(
                runner, "_daemon_run_status", lambda _rid, state=state: {"state": state}
            )
            assert handle.is_running() is True

    def test_is_running_false_for_terminal_states(self, runner, monkeypatch):
        handle = runner.RunHandle(
            pid=-1, started_at=time.time(), dry_run=False, refresh_account=False,
            backend="daemon", daemon_run_id="run-123",
        )
        for state in ("succeeded", "failed"):
            monkeypatch.setattr(
                runner, "_daemon_run_status", lambda _rid, state=state: {"state": state}
            )
            assert handle.is_running() is False

    def test_is_running_false_when_status_lookup_fails(self, runner, monkeypatch):
        """A daemon that's gone unreachable must not make the UI believe a
        run is stuck running forever."""
        handle = runner.RunHandle(
            pid=-1, started_at=time.time(), dry_run=False, refresh_account=False,
            backend="daemon", daemon_run_id="run-123",
        )
        monkeypatch.setattr(runner, "_daemon_run_status", lambda _rid: None)
        assert handle.is_running() is False

    def test_returncode_maps_states_correctly(self, runner, monkeypatch):
        handle = runner.RunHandle(
            pid=-1, started_at=time.time(), dry_run=False, refresh_account=False,
            backend="daemon", daemon_run_id="run-123",
        )
        cases = {"succeeded": 0, "failed": 1, "running": None, "queued": None}
        for state, expected in cases.items():
            monkeypatch.setattr(
                runner, "_daemon_run_status", lambda _rid, state=state: {"state": state}
            )
            assert handle.returncode() == expected

    def test_returncode_none_when_status_lookup_fails(self, runner, monkeypatch):
        handle = runner.RunHandle(
            pid=-1, started_at=time.time(), dry_run=False, refresh_account=False,
            backend="daemon", daemon_run_id="run-123",
        )
        monkeypatch.setattr(runner, "_daemon_run_status", lambda _rid: None)
        assert handle.returncode() is None

    def test_daemon_run_status_never_raises_on_client_exception(self, runner, monkeypatch):
        """_daemon_run_status must swallow any gui.daemon_client exception
        (CONSTRAINT #6) rather than propagate it into is_running()/returncode()."""
        import gui.daemon_client as daemon_client

        def _boom(_run_id, timeout=2.0):
            raise RuntimeError("network exploded")

        monkeypatch.setattr(daemon_client, "get_run_status", _boom)
        result = runner._daemon_run_status("run-123")
        assert result is None

    def test_daemon_run_status_none_when_no_run_id(self, runner):
        assert runner._daemon_run_status(None) is None
        assert runner._daemon_run_status("") is None

    def test_subprocess_backend_handle_unaffected(self, runner):
        """A default (backend='subprocess') handle must be completely
        unaffected by the new daemon branch -- exercises the existing
        _popen/_pid_alive path exactly as before."""
        fake = _FakePopen(["x"])
        handle = runner.RunHandle(
            pid=999, started_at=time.time(), dry_run=False, refresh_account=False,
            _popen=fake,
        )
        assert handle.backend == "subprocess"
        assert handle.is_running() is True
        fake._polled = 1
        assert handle.is_running() is False
        assert handle.returncode() == 1


# ---------------------------------------------------------------------------
# 4. launch_orchestrator() — daemon fast path + fallback
# ---------------------------------------------------------------------------

class TestLaunchOrchestratorDaemonFastPath:
    def test_flag_off_uses_subprocess_path_unchanged(self, monkeypatch, runner):
        monkeypatch.setattr(runner.settings, "ORCHESTRATOR_DAEMON_ENABLED", False)

        def fake_popen(cmd, **kwargs):
            return _FakePopen(cmd, **kwargs)

        monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
        handle = runner.launch_orchestrator()

        assert handle.backend == "subprocess"
        assert handle.mode == "orchestrator"

    def test_dry_run_always_uses_subprocess_path_even_with_flag_on(self, monkeypatch, runner):
        """An explicit dry_run=True must never go through the daemon fast
        path -- an already-running daemon has a FIXED dry_run set at process
        launch with no per-trigger override."""
        monkeypatch.setattr(runner.settings, "ORCHESTRATOR_DAEMON_ENABLED", True)

        import gui.daemon_client as daemon_client

        def _should_not_be_called(*a, **k):
            raise AssertionError("daemon_available() must not be called when dry_run=True")

        monkeypatch.setattr(daemon_client, "daemon_available", _should_not_be_called)

        def fake_popen(cmd, **kwargs):
            return _FakePopen(cmd, **kwargs)

        monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
        handle = runner.launch_orchestrator(dry_run=True)

        assert handle.backend == "subprocess"

    def test_flag_on_daemon_available_and_trigger_succeeds(self, monkeypatch, runner):
        monkeypatch.setattr(runner.settings, "ORCHESTRATOR_DAEMON_ENABLED", True)

        import gui.daemon_client as daemon_client

        monkeypatch.setattr(daemon_client, "daemon_available", lambda timeout=0.5: True)
        monkeypatch.setattr(
            daemon_client, "trigger_run",
            lambda timeout=5.0: daemon_client.TriggerResponse(
                ok=True, run_id="run-abc", state="queued", error=None,
            ),
        )

        def _popen_should_not_be_called(cmd, **kwargs):
            raise AssertionError("subprocess.Popen must not be called on the daemon fast path")

        monkeypatch.setattr(runner.subprocess, "Popen", _popen_should_not_be_called)

        handle = runner.launch_orchestrator()

        assert handle.backend == "daemon"
        assert handle.daemon_run_id == "run-abc"
        assert handle.mode == "orchestrator"
        assert handle.pid == -1

    def test_flag_on_daemon_unavailable_falls_back_to_subprocess(self, monkeypatch, runner):
        monkeypatch.setattr(runner.settings, "ORCHESTRATOR_DAEMON_ENABLED", True)

        import gui.daemon_client as daemon_client

        monkeypatch.setattr(daemon_client, "daemon_available", lambda timeout=0.5: False)

        def fake_popen(cmd, **kwargs):
            return _FakePopen(cmd, **kwargs)

        monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
        handle = runner.launch_orchestrator()

        assert handle.backend == "subprocess"
        assert handle.mode == "orchestrator"

    def test_flag_on_trigger_fails_falls_back_to_subprocess(self, monkeypatch, runner):
        monkeypatch.setattr(runner.settings, "ORCHESTRATOR_DAEMON_ENABLED", True)

        import gui.daemon_client as daemon_client

        monkeypatch.setattr(daemon_client, "daemon_available", lambda timeout=0.5: True)
        monkeypatch.setattr(
            daemon_client, "trigger_run",
            lambda timeout=5.0: daemon_client.TriggerResponse(
                ok=False, run_id=None, state=None, error="already_running",
                existing_run_id="run-xyz",
            ),
        )

        def fake_popen(cmd, **kwargs):
            return _FakePopen(cmd, **kwargs)

        monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
        handle = runner.launch_orchestrator()

        assert handle.backend == "subprocess"

    def test_flag_on_daemon_client_import_exception_falls_back(self, monkeypatch, runner):
        """Any unexpected exception in the fast-path block (e.g. a broken
        import) must degrade to the subprocess path, never propagate and
        block a manual run (CONSTRAINT #6)."""
        monkeypatch.setattr(runner.settings, "ORCHESTRATOR_DAEMON_ENABLED", True)

        import gui.daemon_client as daemon_client

        def _boom(timeout=0.5):
            raise RuntimeError("unexpected failure")

        monkeypatch.setattr(daemon_client, "daemon_available", _boom)

        def fake_popen(cmd, **kwargs):
            return _FakePopen(cmd, **kwargs)

        monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
        handle = runner.launch_orchestrator()

        assert handle.backend == "subprocess"


# ---------------------------------------------------------------------------
# 5. stop_run() — daemon backend cannot be cancelled
# ---------------------------------------------------------------------------

class TestStopRunDaemonBackend:
    def test_daemon_backed_handle_returns_false_never_raises(self, runner):
        handle = runner.RunHandle(
            pid=-1, started_at=time.time(), dry_run=False, refresh_account=False,
            backend="daemon", daemon_run_id="run-123",
        )
        assert runner.stop_run(handle) is False

    def test_subprocess_backed_handle_unaffected(self, runner):
        fake = _FakePopen(["x"])
        fake._polled = 0  # already exited
        handle = runner.RunHandle(
            pid=999, started_at=time.time(), dry_run=False, refresh_account=False,
            _popen=fake,
        )
        assert runner.stop_run(handle) is True


# ---------------------------------------------------------------------------
# 6. read_log_tail() — daemon backend synthesizes a summary
# ---------------------------------------------------------------------------

class TestReadLogTailDaemonBackend:
    def test_daemon_backend_returns_synthesized_summary_not_file_read(self, runner, monkeypatch):
        handle = runner.RunHandle(
            pid=-1, started_at=time.time(), dry_run=False, refresh_account=False,
            backend="daemon", daemon_run_id="run-123", log_path=runner.RUN_LOG_PATH,
        )
        monkeypatch.setattr(
            runner, "_daemon_run_status",
            lambda _rid: {
                "run_id": "run-123", "state": "succeeded", "started_at": "t0",
                "finished_at": "t1", "duration_seconds": 4.2, "error": None,
            },
        )
        out = runner.read_log_tail(handle=handle)
        assert "run-123" in out
        assert "succeeded" in out

    def test_daemon_backend_unavailable_status_returns_hint(self, runner, monkeypatch):
        handle = runner.RunHandle(
            pid=-1, started_at=time.time(), dry_run=False, refresh_account=False,
            backend="daemon", daemon_run_id="run-123",
        )
        monkeypatch.setattr(runner, "_daemon_run_status", lambda _rid: None)
        out = runner.read_log_tail(handle=handle)
        assert "unavailable" in out.lower()

    def test_subprocess_backend_still_reads_file(self, runner):
        runner.RUN_LOG_PATH.write_text("hello from file\n", encoding="utf-8")
        handle = runner.RunHandle(
            pid=1, started_at=time.time(), dry_run=False, refresh_account=False,
            log_path=runner.RUN_LOG_PATH,
        )
        out = runner.read_log_tail(handle=handle)
        assert "hello from file" in out


# ---------------------------------------------------------------------------
# 7. compute_stage_status() — daemon backend coarse mapping
# ---------------------------------------------------------------------------

class TestComputeStageStatusDaemonBackend:
    def test_succeeded_maps_all_stages_success(self, runner, monkeypatch):
        handle = runner.RunHandle(
            pid=-1, started_at=time.time(), dry_run=False, refresh_account=False,
            backend="daemon", daemon_run_id="run-123",
        )
        monkeypatch.setattr(runner, "_daemon_run_status", lambda _rid: {"state": "succeeded"})
        status = runner.compute_stage_status(handle)
        assert all(v == runner.StageStatus.SUCCESS for v in status.values())

    def test_failed_maps_all_stages_error(self, runner, monkeypatch):
        handle = runner.RunHandle(
            pid=-1, started_at=time.time(), dry_run=False, refresh_account=False,
            backend="daemon", daemon_run_id="run-123",
        )
        monkeypatch.setattr(runner, "_daemon_run_status", lambda _rid: {"state": "failed"})
        status = runner.compute_stage_status(handle)
        assert all(v == runner.StageStatus.ERROR for v in status.values())

    def test_running_maps_all_stages_active(self, runner, monkeypatch):
        handle = runner.RunHandle(
            pid=-1, started_at=time.time(), dry_run=False, refresh_account=False,
            backend="daemon", daemon_run_id="run-123",
        )
        monkeypatch.setattr(runner, "_daemon_run_status", lambda _rid: {"state": "running"})
        status = runner.compute_stage_status(handle)
        assert all(v == runner.StageStatus.ACTIVE for v in status.values())

    def test_unknown_status_maps_all_stages_pending_never_raises(self, runner, monkeypatch):
        handle = runner.RunHandle(
            pid=-1, started_at=time.time(), dry_run=False, refresh_account=False,
            backend="daemon", daemon_run_id="run-123",
        )
        monkeypatch.setattr(runner, "_daemon_run_status", lambda _rid: None)
        status = runner.compute_stage_status(handle)
        assert all(v == runner.StageStatus.PENDING for v in status.values())
