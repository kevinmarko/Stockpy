"""
tests/test_orchestrator_runner_daemon_cutover.py
==================================================
The daemon paths in shared/orchestrator_runner.py. Shrink step 5.6 retired
settings.ORCHESTRATOR_DAEMON_ENABLED (and launch_daemon_engine, the desktop
shell's always-on loop launcher that only that flag used), leaving two:

1. launch_orchestrator()'s HTTP fast path — a non-dry run triggers a cycle on
   an already-running daemon via shared.daemon_client, producing a
   backend="daemon" RunHandle. A 409 attaches to the in-flight full cycle (or
   refuses for a data/metrics sub-run); other failures fall back to the
   main_orchestrator.py subprocess. RunHandle.is_running()/returncode(),
   stop_run(), read_log_tail(), and compute_stage_status() all branch on
   handle.backend.

2. launch_daemon_cycle() — the Console's "Advisory Pipeline" job
   (api/_jobs.py JobType.ADVISORY). Daemon-ONLY: same backend="daemon" handle
   as (1), never falling back to a subprocess.

All daemon_client calls are monkeypatched -- no real HTTP, no real daemon
process (the root conftest also points ORCHESTRATOR_API_PORT at a closed
port). subprocess.Popen is monkeypatched via the same _FakePopen pattern
tests/test_orchestrator_runner.py already uses.
"""
from __future__ import annotations

import sys
import time

import pytest


@pytest.fixture()
def runner(monkeypatch, tmp_path):
    """Import shared.orchestrator_runner with all FS paths sandboxed to tmp_path."""
    from shared import orchestrator_runner as runner

    monkeypatch.setattr(runner, "RUN_LOG_PATH", tmp_path / "gui_run.log")
    monkeypatch.setattr(runner, "DAEMON_CYCLE_LOG_PATH", tmp_path / "gui_daemon_cycle.log")
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
# RunHandle dual-backend: is_running() / returncode()
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
        """_daemon_run_status must swallow any shared.daemon_client exception
        (CONSTRAINT #6) rather than propagate it into is_running()/returncode()."""
        import shared.daemon_client as daemon_client

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
# launch_orchestrator() — daemon fast path + fallback
# ---------------------------------------------------------------------------

class TestLaunchOrchestratorDaemonFastPath:
    def test_retired_flag_is_gone_from_settings_and_env_allowlist(self):
        """Step 5.6: the daemon is the only runtime, so the cutover flag must
        not come back as a setting or a GUI-writable .env key."""
        from settings import Settings
        from shared import env_io

        assert "ORCHESTRATOR_DAEMON_ENABLED" not in Settings.model_fields
        assert "ORCHESTRATOR_DAEMON_ENABLED" not in env_io.ALLOWED_KEYS

    def test_dry_run_always_uses_subprocess_path(self, monkeypatch, runner):
        """An explicit dry_run=True must never go through the daemon fast
        path -- an already-running daemon has a FIXED dry_run set at process
        launch with no per-trigger override."""
        import shared.daemon_client as daemon_client

        def _should_not_be_called(*a, **k):
            raise AssertionError("daemon_available() must not be called when dry_run=True")

        monkeypatch.setattr(daemon_client, "daemon_available", _should_not_be_called)

        def fake_popen(cmd, **kwargs):
            return _FakePopen(cmd, **kwargs)

        monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
        handle = runner.launch_orchestrator(dry_run=True)

        assert handle.backend == "subprocess"

    def test_daemon_available_and_trigger_succeeds(self, monkeypatch, runner):
        import shared.daemon_client as daemon_client

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

    def test_daemon_unavailable_falls_back_to_subprocess(self, monkeypatch, runner):
        import shared.daemon_client as daemon_client

        monkeypatch.setattr(daemon_client, "daemon_available", lambda timeout=0.5: False)

        def fake_popen(cmd, **kwargs):
            return _FakePopen(cmd, **kwargs)

        monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
        handle = runner.launch_orchestrator()

        assert handle.backend == "subprocess"
        assert handle.mode == "orchestrator"

    @staticmethod
    def _patch_fast_path(monkeypatch, runner, response, *, latest=None, status=None,
                         allow_popen=False):
        """Daemon reachable, trigger_run -> ``response``. ``latest`` is
        GET /run/latest (default: reading it fails the test), ``status`` is
        GET /run/{id}/status. Popen fails the test unless ``allow_popen``."""
        import shared.daemon_client as daemon_client

        def _latest_must_not_be_read(*a, **k):
            raise AssertionError("GET /run/latest must only be read as the 409 fallback")

        monkeypatch.setattr(daemon_client, "daemon_available", lambda timeout=0.5: True)
        monkeypatch.setattr(daemon_client, "trigger_run", lambda timeout=5.0: response)
        monkeypatch.setattr(
            daemon_client, "get_latest_run",
            _latest_must_not_be_read if latest is None else (lambda timeout=2.0: latest),
        )
        monkeypatch.setattr(daemon_client, "get_run_status", lambda run_id, timeout=2.0: status)

        def _popen_must_not_be_called(cmd, **kwargs):
            raise AssertionError(f"subprocess.Popen must not be called: {cmd}")

        monkeypatch.setattr(
            runner.subprocess, "Popen",
            (lambda cmd, **kw: _FakePopen(cmd, **kw)) if allow_popen else _popen_must_not_be_called,
        )

    def test_409_already_running_attaches_to_existing_run(self, monkeypatch, runner):
        """A 409 means a cycle is already in flight on the daemon: attach to it,
        never spawn a second, parallel main_orchestrator.py cycle."""
        self._patch_fast_path(
            monkeypatch, runner,
            _trigger_response(error="already_running", existing_run_id="run-xyz"),
        )

        handle = runner.launch_orchestrator()

        assert handle.backend == "daemon"
        assert handle.daemon_run_id == "run-xyz"
        assert handle.mode == "orchestrator"
        assert handle.pid == -1
        assert handle._popen is None

    def test_202_without_a_run_id_still_returns_a_daemon_handle(
        self, monkeypatch, runner,
    ):
        """The daemon ACCEPTED the run; a missing id must not drop through to
        the subprocess fallback and start a second, parallel cycle."""
        self._patch_fast_path(
            monkeypatch, runner, _trigger_response(ok=True, run_id=None, state="queued"),
        )

        handle = runner.launch_orchestrator()

        assert handle.backend == "daemon"
        assert handle.daemon_run_id is None

    def test_409_attaches_to_a_full_run(self, monkeypatch, runner):
        self._patch_fast_path(
            monkeypatch, runner,
            _trigger_response(error="already_running", existing_run_id="run-full"),
            status={"run_id": "run-full", "state": "running", "mode": "full"},
        )

        handle = runner.launch_orchestrator()

        assert handle.backend == "daemon"
        assert handle.daemon_run_id == "run-full"

    def test_409_without_body_id_attaches_to_verifiably_in_flight_latest_run(
        self, monkeypatch, runner,
    ):
        self._patch_fast_path(
            monkeypatch, runner,
            _trigger_response(error="already_running", existing_run_id=None),
            latest={"run_id": "run-latest", "state": "running"},
        )

        handle = runner.launch_orchestrator()

        assert handle.backend == "daemon"
        assert handle.daemon_run_id == "run-latest"

    def test_409_without_any_verifiable_run_id_falls_back(self, monkeypatch, runner):
        """No id in the 409 body and /run/latest is not queued/running: nothing
        to attach to, so the pre-existing subprocess fallback applies."""
        self._patch_fast_path(
            monkeypatch, runner,
            _trigger_response(error="already_running", existing_run_id=None),
            latest={"run_id": "run-old", "state": "succeeded"},
            allow_popen=True,
        )

        handle = runner.launch_orchestrator()

        assert handle.backend == "subprocess"

    @pytest.mark.parametrize("sub_mode", ["data", "metrics"])
    def test_409_on_a_non_full_run_refuses_without_spawning(
        self, monkeypatch, runner, sub_mode,
    ):
        """A data/metrics sub-run in flight must be neither reported as this
        run nor duplicated by a subprocess -- the refusal must escape the fast
        path's catch-all fallback."""
        self._patch_fast_path(
            monkeypatch, runner,
            _trigger_response(error="already_running", existing_run_id="run-sub"),
            status={"run_id": "run-sub", "state": "running", "mode": sub_mode},
        )

        with pytest.raises(RuntimeError, match=sub_mode):
            runner.launch_orchestrator()

    @pytest.mark.parametrize(
        "error", ["network_error", "unavailable", "kill_switch_active",
                  "command_disabled", "unauthorized", "unexpected_response"],
    )
    def test_non_409_trigger_failure_falls_back_to_subprocess(
        self, monkeypatch, runner, error,
    ):
        self._patch_fast_path(
            monkeypatch, runner, _trigger_response(error=error), allow_popen=True,
        )

        handle = runner.launch_orchestrator()

        assert handle.backend == "subprocess"
        assert handle.mode == "orchestrator"

    def test_daemon_client_import_exception_falls_back(self, monkeypatch, runner):
        """Any unexpected exception in the fast-path block (e.g. a broken
        import) must degrade to the subprocess path, never propagate and
        block a manual run (CONSTRAINT #6)."""
        import shared.daemon_client as daemon_client

        def _boom(timeout=0.5):
            raise RuntimeError("unexpected failure")

        monkeypatch.setattr(daemon_client, "daemon_available", _boom)

        def fake_popen(cmd, **kwargs):
            return _FakePopen(cmd, **kwargs)

        monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
        handle = runner.launch_orchestrator()

        assert handle.backend == "subprocess"


# ---------------------------------------------------------------------------
# 4b. launch_daemon_cycle() — daemon-only, attach on 409, honest errors
# ---------------------------------------------------------------------------

def _trigger_response(**kwargs):
    import shared.daemon_client as daemon_client

    base = dict(ok=False, run_id=None, state=None, error=None)
    base.update(kwargs)
    return daemon_client.TriggerResponse(**base)


@pytest.fixture()
def no_subprocess(monkeypatch, runner):
    """launch_daemon_cycle has NO subprocess fallback: any Popen is a failure."""

    def _popen_must_not_be_called(cmd, **kwargs):
        raise AssertionError(f"subprocess.Popen must not be called: {cmd}")

    monkeypatch.setattr(runner.subprocess, "Popen", _popen_must_not_be_called)


def _patch_trigger(monkeypatch, response, latest=None, status=None):
    """Patch daemon_client.trigger_run to return ``response``; also make
    daemon_available() fail the test (the cycle must not pre-probe, it goes
    straight to POST /run). ``latest`` is what GET /run/latest returns (the
    409 fallback); when it is left unset any read of it fails the test.
    ``status`` is what GET /run/{id}/status returns when attaching to an
    in-flight run (default ``None`` = unreadable). Returns the list of
    trigger calls."""
    import shared.daemon_client as daemon_client

    calls: list = []

    def _fake_trigger(timeout=5.0):
        calls.append(timeout)
        return response

    def _available_must_not_be_called(*a, **k):
        raise AssertionError("launch_daemon_cycle must not pre-probe daemon_available()")

    def _latest_must_not_be_read(*a, **k):
        raise AssertionError("GET /run/latest must only be read as the 409 fallback")

    monkeypatch.setattr(daemon_client, "trigger_run", _fake_trigger)
    monkeypatch.setattr(daemon_client, "daemon_available", _available_must_not_be_called)
    monkeypatch.setattr(
        daemon_client, "get_latest_run",
        _latest_must_not_be_read if latest is None else (lambda timeout=2.0: latest),
    )
    monkeypatch.setattr(daemon_client, "get_run_status", lambda run_id, timeout=2.0: status)
    return calls


class TestLaunchDaemonCycle:
    def test_ok_returns_daemon_backed_handle(
        self, monkeypatch, runner, no_subprocess,
    ):
        """Daemon-only: no subprocess fallback, unlike launch_orchestrator."""
        calls = _patch_trigger(
            monkeypatch, _trigger_response(ok=True, run_id="run-abc", state="queued"),
        )

        handle = runner.launch_daemon_cycle()

        assert len(calls) == 1
        assert handle.backend == "daemon"
        assert handle.daemon_run_id == "run-abc"
        assert handle.mode == "daemon_cycle"
        assert handle.pid == -1
        assert handle.dry_run is False
        assert handle.refresh_account is False
        assert handle._popen is None
        # The daemon never writes a per-run log: the handle must not point at a
        # file (e.g. a stale gui_run.log) that could be replayed as its output.
        assert handle.log_path == runner.DAEMON_CYCLE_LOG_PATH
        assert handle.log_path != runner.RUN_LOG_PATH
        assert not handle.log_path.exists()

    def test_returned_handle_uses_the_existing_daemon_backed_machinery(
        self, monkeypatch, runner, no_subprocess,
    ):
        _patch_trigger(monkeypatch, _trigger_response(ok=True, run_id="run-abc", state="queued"))
        handle = runner.launch_daemon_cycle()

        monkeypatch.setattr(runner, "_daemon_run_status", lambda _rid: {"state": "running"})
        assert handle.is_running() is True
        assert handle.returncode() is None
        assert all(v == runner.StageStatus.ACTIVE for v in runner.compute_stage_status(handle).values())

        monkeypatch.setattr(
            runner, "_daemon_run_status",
            lambda _rid: {"run_id": "run-abc", "state": "succeeded", "started_at": "t0",
                          "finished_at": "t1", "duration_seconds": 1.0, "error": None},
        )
        assert handle.is_running() is False
        assert handle.returncode() == 0
        assert "run-abc" in runner.read_log_tail(handle=handle)
        assert "succeeded" in runner.read_log_tail(handle=handle)
        # Not cancellable: stop_run refuses a daemon handle rather than pretending.
        assert runner.stop_run(handle) is False

    def test_409_already_running_attaches_to_existing_run(
        self, monkeypatch, runner, no_subprocess,
    ):
        _patch_trigger(
            monkeypatch,
            _trigger_response(error="already_running", existing_run_id="run-xyz"),
        )

        handle = runner.launch_daemon_cycle()

        # The body's run id wins; GET /run/latest is not consulted (the helper
        # fails the test if it is read).
        assert handle.backend == "daemon"
        assert handle.daemon_run_id == "run-xyz"
        assert handle.mode == "daemon_cycle"
        assert handle.pid == -1

    def test_409_without_a_body_run_id_attaches_to_the_verifiably_in_flight_latest_run(
        self, monkeypatch, runner, no_subprocess,
    ):
        """When the 409 reply yields no run id (e.g. an unparseable body),
        GET /run/latest (queued/running only) identifies the in-flight cycle."""
        _patch_trigger(
            monkeypatch,
            _trigger_response(error="already_running", existing_run_id=None),
            latest={"run_id": "run-latest", "state": "running"},
        )
        handle = runner.launch_daemon_cycle()
        assert handle.backend == "daemon"
        assert handle.daemon_run_id == "run-latest"

    @pytest.mark.parametrize(
        "latest",
        [
            {"run_id": "run-old", "state": "succeeded"},  # finished: not in flight
            {"run_id": "run-old", "state": "failed"},
            {"state": "running"},                          # no id to attach to
            {},
            None,                                          # GET /run/latest unreadable
        ],
    )
    def test_409_without_any_verifiable_run_id_raises_rather_than_guessing(
        self, monkeypatch, runner, no_subprocess, latest,
    ):
        import shared.daemon_client as daemon_client

        _patch_trigger(monkeypatch, _trigger_response(error="already_running", existing_run_id=None))
        monkeypatch.setattr(daemon_client, "get_latest_run", lambda timeout=2.0: latest)
        with pytest.raises(RuntimeError, match="already running"):
            runner.launch_daemon_cycle()

    @pytest.mark.parametrize("mode", ["data", "metrics"])
    def test_409_on_a_non_full_run_refuses_to_attach(
        self, monkeypatch, runner, no_subprocess, mode,
    ):
        """The daemon is single-flight across modes: a data/metrics sub-run in
        flight never runs the advisory/broker/queue/snapshot steps, so it must
        not be reported as this job's result."""
        _patch_trigger(
            monkeypatch,
            _trigger_response(error="already_running", existing_run_id="run-sub"),
            status={"run_id": "run-sub", "state": "running", "mode": mode},
        )
        with pytest.raises(RuntimeError) as exc:
            runner.launch_daemon_cycle()
        msg = str(exc.value)
        assert f"'{mode}' run" in msg
        assert "run-sub" in msg
        assert "advisory/broker/queue/snapshot" in msg
        assert "not attached" in msg
        assert "retry" in msg

    def test_409_non_full_check_also_applies_to_the_run_latest_fallback(
        self, monkeypatch, runner, no_subprocess,
    ):
        _patch_trigger(
            monkeypatch,
            _trigger_response(error="already_running", existing_run_id=None),
            latest={"run_id": "run-sub", "state": "running", "mode": "data"},
            status={"run_id": "run-sub", "state": "running", "mode": "data"},
        )
        with pytest.raises(RuntimeError, match="'data' run"):
            runner.launch_daemon_cycle()

    def test_409_on_a_full_run_attaches(self, monkeypatch, runner, no_subprocess):
        _patch_trigger(
            monkeypatch,
            _trigger_response(error="already_running", existing_run_id="run-full"),
            status={"run_id": "run-full", "state": "running", "mode": "full"},
        )
        handle = runner.launch_daemon_cycle()
        assert handle.backend == "daemon"
        assert handle.daemon_run_id == "run-full"

    @pytest.mark.parametrize(
        "status",
        [
            None,                                          # status unreadable
            {"run_id": "run-x", "state": "running"},       # no mode field
            {"run_id": "run-x", "state": "running", "mode": None},
        ],
    )
    def test_409_with_an_unverifiable_mode_attaches(
        self, monkeypatch, runner, no_subprocess, status,
    ):
        """We cannot prove it is a sub-run, so attach (documented limitation)."""
        _patch_trigger(
            monkeypatch,
            _trigger_response(error="already_running", existing_run_id="run-x"),
            status=status,
        )
        handle = runner.launch_daemon_cycle()
        assert handle.daemon_run_id == "run-x"

    def test_a_freshly_accepted_run_is_never_mode_checked(
        self, monkeypatch, runner, no_subprocess,
    ):
        """POST /run always starts a full cycle; only the attach branch needs
        the in-flight run's mode."""
        import shared.daemon_client as daemon_client

        _patch_trigger(monkeypatch, _trigger_response(ok=True, run_id="run-new", state="queued"))

        def _status_must_not_be_read(*a, **k):
            raise AssertionError("an accepted run must not be mode-checked")

        monkeypatch.setattr(daemon_client, "get_run_status", _status_must_not_be_read)
        assert runner.launch_daemon_cycle().daemon_run_id == "run-new"

    def test_202_without_a_run_id_raises(self, monkeypatch, runner, no_subprocess):
        _patch_trigger(monkeypatch, _trigger_response(ok=True, run_id=None, state="queued"))
        with pytest.raises(RuntimeError, match="run id"):
            runner.launch_daemon_cycle()

    def test_kill_switch_active_raises_with_the_reason(
        self, monkeypatch, runner, no_subprocess,
    ):
        _patch_trigger(
            monkeypatch,
            _trigger_response(error="kill_switch_active", kill_switch_reason="macro regime breach"),
        )
        with pytest.raises(RuntimeError) as exc:
            runner.launch_daemon_cycle()
        msg = str(exc.value).lower()
        assert "kill switch" in msg
        assert "macro regime breach" in msg

    def test_kill_switch_active_without_a_reason_does_not_invent_one(
        self, monkeypatch, runner, no_subprocess,
    ):
        _patch_trigger(
            monkeypatch, _trigger_response(error="kill_switch_active", kill_switch_reason=""),
        )
        with pytest.raises(RuntimeError) as exc:
            runner.launch_daemon_cycle()
        assert "kill switch" in str(exc.value).lower()
        assert "reason" not in str(exc.value).lower()

    @pytest.mark.parametrize("error", ["command_disabled", "unauthorized"])
    def test_token_problems_name_orchestrator_daemon_token(
        self, monkeypatch, runner, no_subprocess, error,
    ):
        _patch_trigger(monkeypatch, _trigger_response(error=error))
        with pytest.raises(RuntimeError, match="ORCHESTRATOR_DAEMON_TOKEN"):
            runner.launch_daemon_cycle()

    def test_503_unavailable_says_the_daemon_is_not_running_and_never_spawns_a_subprocess(
        self, monkeypatch, runner, no_subprocess,
    ):
        _patch_trigger(monkeypatch, _trigger_response(error="unavailable"))
        with pytest.raises(RuntimeError) as exc:
            runner.launch_daemon_cycle()
        assert "daemon not running (python -m desktop.orchestrator_daemon)" in str(exc.value)

    def test_network_error_does_not_claim_nothing_started(
        self, monkeypatch, runner, no_subprocess,
    ):
        """A client-side timeout can fire after the daemon accepted the run, so
        the message says the trigger could not be confirmed, not that the
        daemon is down."""
        _patch_trigger(monkeypatch, _trigger_response(error="network_error"))
        with pytest.raises(RuntimeError) as exc:
            runner.launch_daemon_cycle()
        msg = str(exc.value)
        assert "could not be confirmed" in msg
        assert "connection refused, timeout or unreadable reply" in msg
        assert "may or may not have started" in msg
        assert "Pipeline Dashboard" in msg and "GET /run/latest" in msg
        assert "before retrying" in msg
        assert "likely cause" in msg and "python -m desktop.orchestrator_daemon" in msg
        # Over-claims the audit removed:
        assert "daemon not running" not in msg
        assert "nothing started" not in msg.lower()

    def test_network_error_names_the_port_it_tried(self, monkeypatch, runner, no_subprocess):
        monkeypatch.setattr(runner.settings, "ORCHESTRATOR_API_PORT", 18601)
        _patch_trigger(monkeypatch, _trigger_response(error="network_error"))
        with pytest.raises(RuntimeError, match="127.0.0.1:18601"):
            runner.launch_daemon_cycle()

    def test_unexpected_response_raises_honestly(self, monkeypatch, runner, no_subprocess):
        _patch_trigger(monkeypatch, _trigger_response(error="unexpected_response"))
        with pytest.raises(RuntimeError, match="unexpected"):
            runner.launch_daemon_cycle()

    @pytest.mark.parametrize(
        "error",
        ["kill_switch_active", "command_disabled", "unauthorized", "unavailable",
         "network_error", "unexpected_response", "already_running"],
    )
    def test_error_messages_never_leak_the_token_value(
        self, monkeypatch, runner, no_subprocess, error,
    ):
        import shared.daemon_client as daemon_client

        monkeypatch.setattr(runner.settings, "ORCHESTRATOR_DAEMON_TOKEN", "sentinel-secret-9f3a")
        _patch_trigger(monkeypatch, _trigger_response(error=error))
        # already_running consults GET /run/latest for an id; make it unreadable.
        monkeypatch.setattr(daemon_client, "get_latest_run", lambda timeout=2.0: None)
        with pytest.raises(RuntimeError) as exc:
            runner.launch_daemon_cycle()
        assert "sentinel-secret-9f3a" not in str(exc.value)


# ---------------------------------------------------------------------------
# stop_run() — daemon backend cannot be cancelled
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
# read_log_tail() — daemon backend synthesizes a summary
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
# compute_stage_status() — daemon backend coarse mapping
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
