"""
tests/test_command_execution.py
================================
Tests for the "command" job type -- the webapp Commands screen's "Run"
button, which executes a manifest-listed CLI target via the existing
background-job infrastructure (``api/_jobs.py`` + ``api/control_api.py``'s
``POST /jobs``).

Written against the exact spec below (a parallel agent implements this spec
verbatim), not against the in-progress implementation:

*   ``settings.COMMAND_EXECUTION_ENABLED`` (default ``False``) gates the new
    job type on top of the existing ``JOBS_API_ENABLED`` +
    ``ORCHESTRATOR_DAEMON_TOKEN`` guard already on ``POST /jobs``.
*   ``gui.orchestrator_runner.launch_manifest_command(job_id, command_name,
    subcommand_name, args, *, confirm=False) -> RunHandle`` resolves the
    target via ``pilots.commands.resolve_command`` (lazy-imported), refuses
    ``DISALLOWED_EXECUTE_COMMANDS`` (``app_shell.py``) and unresolvable
    commands with ``ValueError``, requires ``confirm=True`` for any
    ``HIGH_STAKES_COMMANDS`` flag-set match (``execution.kill_switch
    --activate``/``--deactivate``, ``main.py --refresh-account``), and
    otherwise spawns ``[sys.executable] + shlex.split(invocation)[1:] +
    args`` via ``subprocess.Popen``, logging to
    ``settings.OUTPUT_DIR / "gui_commands" / f"{job_id}.log"``.

Conventions mirrored from ``tests/test_control_api.py`` (loopback
``TestClient`` host, ``mock.patch.object(settings, ...)`` for live-read
settings, an autouse job-manager-clearing fixture) and from
``tests/test_orchestrator_runner.py`` (``monkeypatch.setattr(runner.subprocess,
"Popen", fake)`` so no real subprocess is ever spawned).

Every request-body key beyond the ones the spec text itself shows
(``{"command": ..., "args": [...]}``) -- specifically ``subcommand`` and
``confirm`` -- is this test file's own reasonable inference of the REST
mapping onto ``launch_manifest_command``'s positional parameters; if the
real implementation names them differently, the affected HTTP-level tests
will need reconciling (the direct unit tests against
``launch_manifest_command`` itself are unaffected by that ambiguity).
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from settings import settings
import api.control_api as control_api
import api._jobs as jobs_module
import gui.orchestrator_runner as orchestrator_runner

# Starlette's TestClient defaults request.client.host to the literal string
# "testclient" -- NOT loopback -- which would trip the command-token guard's
# fail-closed-when-non-loopback branch. Loopback host matches every other
# control_api test file's convention.
client = TestClient(control_api.app, client=("127.0.0.1", 54123))

_HEADERS = {"Authorization": "Bearer test-token"}

# A small, fully-controlled manifest fixture -- NOT the real committed
# cli_introspect/command_manifest.json -- covering every target the spec's
# behaviors reference: an ordinary target, a second (distinct) ordinary
# target for the concurrency tests, the disallowed app_shell.py entry (to
# prove the disallow-check fires even for a name the manifest DOES list),
# and both HIGH_STAKES_COMMANDS entries.
FIXTURE_MANIFEST = {
    "generated_at": "2026-07-30T00:00:00+00:00",
    "command_count": 4,
    "dead_letters": [],
    "commands": [
        {
            "name": "scripts/preflight_check.py",
            "invocation": "python scripts/preflight_check.py",
            "aliases": ["preflight"],
            "description": None,
            "options": [],
            "positionals": [],
            "subcommands": [],
        },
        {
            "name": "scripts/build_command_manifest.py",
            "invocation": "python scripts/build_command_manifest.py",
            "aliases": ["build-manifest"],
            "description": None,
            "options": [],
            "positionals": [],
            "subcommands": [],
        },
        {
            "name": "execution.kill_switch",
            "invocation": "python -m execution.kill_switch",
            "aliases": ["kill-switch"],
            "description": None,
            "options": [],
            "positionals": [],
            "subcommands": [],
        },
        {
            "name": "main.py",
            "invocation": "python3 main.py",
            "aliases": [],
            "description": None,
            "options": [],
            "positionals": [],
            "subcommands": [],
        },
        {
            "name": "app_shell.py",
            "invocation": "python3 app_shell.py",
            "aliases": [],
            "description": None,
            "options": [],
            "positionals": [],
            "subcommands": [],
        },
        {
            "name": "database_setup.py",
            "invocation": "python3 database_setup.py",
            "aliases": [],
            "description": None,
            "options": [],
            "positionals": [],
            "subcommands": [],
        },
    ],
    "reason": None,
}


def _manifest_patch():
    return mock.patch("pilots.commands.command_manifest", return_value=FIXTURE_MANIFEST)


@contextmanager
def _enabled(command_execution_enabled: bool = True):
    """Combined context manager for JOBS_API_ENABLED + ORCHESTRATOR_DAEMON_TOKEN
    + COMMAND_EXECUTION_ENABLED, matching test_control_api.py's TestJobsApi
    convention (a single ``with`` item so call sites can compose it with
    ``_manifest_patch()`` without needing starred with-item unpacking, which
    is not valid Python syntax)."""
    with mock.patch.object(settings, "JOBS_API_ENABLED", True), \
         mock.patch.object(settings, "ORCHESTRATOR_DAEMON_TOKEN", "test-token"), \
         mock.patch.object(settings, "COMMAND_EXECUTION_ENABLED", command_execution_enabled):
        yield


class _FakePopen:
    """Stand-in for subprocess.Popen: records argv/kwargs, never actually
    spawns a process, and reports "still running" (poll() -> None) until a
    test flips .returncode -- exactly mirroring
    tests/test_orchestrator_runner.py's _FakePopen."""

    def __init__(self, cmd, **kwargs):
        self.args = cmd
        self.pid = 4242
        self.kwargs = kwargs
        self.returncode = None

    def poll(self):
        return self.returncode


@pytest.fixture(autouse=True)
def _reset_job_manager():
    """No job leaks between tests in this file (job_manager is a module-level
    singleton shared with tests/test_control_api.py's own suite)."""
    jobs_module.job_manager._jobs.clear()
    yield
    jobs_module.job_manager._jobs.clear()


@pytest.fixture(autouse=True)
def _sandbox_output_dir(monkeypatch, tmp_path):
    """Redirect the module-level COMMAND_LOG_DIR constant so a real
    (unmocked) log-file write by launch_manifest_command never touches the
    real repo's output/gui_commands/ directory.

    Note: COMMAND_LOG_DIR = settings.OUTPUT_DIR / "gui_commands" is computed
    ONCE at module-import time (same pattern as every other *_LOG_PATH
    constant in this module -- see tests/test_orchestrator_runner.py's
    `runner` fixture, which likewise patches the constants directly rather
    than settings.OUTPUT_DIR after the fact, since that would have no effect
    on an already-computed derived Path).
    """
    monkeypatch.setattr(orchestrator_runner, "COMMAND_LOG_DIR", tmp_path / "gui_commands")
    monkeypatch.setattr(orchestrator_runner.settings, "OUTPUT_DIR", tmp_path)
    yield


def _recording_popen(created: list):
    def _factory(cmd, **kwargs):
        p = _FakePopen(cmd, **kwargs)
        created.append(p)
        return p

    return _factory


# --------------------------------------------------------------------------- #
# Sanity: the new enum member exists at all
# --------------------------------------------------------------------------- #


def test_job_type_command_is_registered():
    from api._jobs import JobType

    assert JobType("command") == JobType.COMMAND


# --------------------------------------------------------------------------- #
# POST /jobs {"job_type": "command", ...} -- gating, validation, execution
# --------------------------------------------------------------------------- #


def test_command_execution_disabled_returns_403(monkeypatch):
    mock_popen = MagicMock()
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", mock_popen)
    with _manifest_patch(), _enabled(command_execution_enabled=False):
        resp = client.post(
            "/jobs",
            json={"job_type": "command", "params": {"command": "scripts/preflight_check.py", "args": []}},
            headers=_HEADERS,
        )
    assert resp.status_code == 403
    mock_popen.assert_not_called()


def test_disallowed_app_shell_command_returns_400(monkeypatch):
    # Defense in depth: mock Popen so nothing spawns even if the disallow
    # check somehow failed to short-circuit resolution/execution.
    mock_popen = MagicMock()
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", mock_popen)
    with _manifest_patch(), _enabled():
        resp = client.post(
            "/jobs",
            json={"job_type": "command", "params": {"command": "app_shell.py", "args": []}},
            headers=_HEADERS,
        )
    assert resp.status_code == 400
    mock_popen.assert_not_called()


def test_unknown_command_returns_400(monkeypatch):
    mock_popen = MagicMock()
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", mock_popen)
    with _manifest_patch(), _enabled():
        resp = client.post(
            "/jobs",
            json={"job_type": "command", "params": {"command": "not_a_real_target", "args": []}},
            headers=_HEADERS,
        )
    assert resp.status_code == 400
    assert "unknown command" in resp.json()["detail"].lower()
    mock_popen.assert_not_called()


def test_normal_command_succeeds_and_builds_expected_argv(monkeypatch):
    created: list = []
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", _recording_popen(created))
    with _manifest_patch(), _enabled():
        resp = client.post(
            "/jobs",
            json={"job_type": "command", "params": {"command": "scripts/preflight_check.py", "args": []}},
            headers=_HEADERS,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_type"] == "command"
    assert body["status"] == "running"
    assert len(created) == 1
    # invocation "python scripts/preflight_check.py" -> interpreter substituted
    # for "python", rest of the invocation tail preserved, no extra args.
    assert created[0].args == [sys.executable, "scripts/preflight_check.py"]


def test_command_job_response_echoes_command_name_and_created_at(monkeypatch):
    created: list = []
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", _recording_popen(created))
    # GET /jobs/{id} is gated by require_read_token (STATE_API_TOKEN), a
    # DIFFERENT token than the ORCHESTRATOR_DAEMON_TOKEN _enabled() patches
    # for the command-token-gated POST above -- _HEADERS's "Bearer
    # test-token" only satisfies the GET on a machine where STATE_API_TOKEN
    # is unset (the fail-open-on-loopback path this TestClient's loopback
    # host relies on). Pinned explicitly so this doesn't depend on the
    # machine's real .env leaving STATE_API_TOKEN unset.
    monkeypatch.setattr(settings, "STATE_API_TOKEN", None)
    with _manifest_patch(), _enabled():
        resp = client.post(
            "/jobs",
            json={"job_type": "command", "params": {"command": "scripts/preflight_check.py", "args": []}},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["command_name"] == "scripts/preflight_check.py"
        assert body["created_at"]
        datetime.fromisoformat(body["created_at"])  # must be real ISO 8601, not a placeholder

        status = client.get(f"/jobs/{body['job_id']}", headers=_HEADERS)
    assert status.status_code == 200
    status_body = status.json()
    assert status_body["command_name"] == "scripts/preflight_check.py"
    assert status_body["created_at"] == body["created_at"]


def test_non_command_job_has_no_command_name(monkeypatch):
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", _recording_popen([]))
    with _enabled():
        resp = client.post("/jobs", json={"job_type": "preflight"}, headers=_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["command_name"] is None
    assert body["created_at"]


def test_normal_command_with_args_appends_them_after_invocation_tail(monkeypatch):
    created: list = []
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", _recording_popen(created))
    with _manifest_patch(), _enabled():
        resp = client.post(
            "/jobs",
            json={
                "job_type": "command",
                "params": {"command": "execution.kill_switch", "args": ["--status"]},
            },
            headers=_HEADERS,
        )
    assert resp.status_code == 200
    assert created[0].args == [sys.executable, "-m", "execution.kill_switch", "--status"]


def test_kill_switch_activate_requires_confirmation_then_succeeds(monkeypatch):
    created: list = []
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", _recording_popen(created))
    with _manifest_patch(), _enabled():
        no_confirm = client.post(
            "/jobs",
            json={
                "job_type": "command",
                "params": {"command": "execution.kill_switch", "args": ["--activate"]},
            },
            headers=_HEADERS,
        )
        assert no_confirm.status_code == 400
        assert "confirmation required" in no_confirm.json()["detail"].lower()
        assert created == []  # nothing spawned on the rejected attempt

        confirmed = client.post(
            "/jobs",
            json={
                "job_type": "command",
                "params": {
                    "command": "execution.kill_switch",
                    "args": ["--activate"],
                    "confirm": True,
                },
            },
            headers=_HEADERS,
        )
    assert confirmed.status_code == 200
    assert len(created) == 1
    assert created[0].args == [sys.executable, "-m", "execution.kill_switch", "--activate"]


def test_kill_switch_deactivate_also_requires_confirmation(monkeypatch):
    """Both HIGH_STAKES_COMMANDS flag-sets for execution.kill_switch --
    --activate AND --deactivate -- must gate on confirm, not just one."""
    mock_popen = MagicMock()
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", mock_popen)
    with _manifest_patch(), _enabled():
        resp = client.post(
            "/jobs",
            json={
                "job_type": "command",
                "params": {"command": "execution.kill_switch", "args": ["--deactivate"]},
            },
            headers=_HEADERS,
        )
    assert resp.status_code == 400
    assert "confirmation required" in resp.json()["detail"].lower()
    mock_popen.assert_not_called()


def test_main_refresh_account_requires_confirmation_then_succeeds(monkeypatch):
    created: list = []
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", _recording_popen(created))
    with _manifest_patch(), _enabled():
        no_confirm = client.post(
            "/jobs",
            json={"job_type": "command", "params": {"command": "main.py", "args": ["--refresh-account"]}},
            headers=_HEADERS,
        )
        assert no_confirm.status_code == 400
        assert "confirmation required" in no_confirm.json()["detail"].lower()

        confirmed = client.post(
            "/jobs",
            json={
                "job_type": "command",
                "params": {"command": "main.py", "args": ["--refresh-account"], "confirm": True},
            },
            headers=_HEADERS,
        )
    assert confirmed.status_code == 200
    assert len(created) == 1
    assert created[0].args == [sys.executable, "main.py", "--refresh-account"]


def test_database_setup_requires_confirmation_then_succeeds(monkeypatch):
    """database_setup.py takes NO argv -- its HIGH_STAKES_COMMANDS entry keys
    on frozenset() (the empty set is a subset of any arg_set, including the
    always-empty one this command actually gets), so every invocation is
    gated on confirm regardless of `args`."""
    created: list = []
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", _recording_popen(created))
    with _manifest_patch(), _enabled():
        no_confirm = client.post(
            "/jobs",
            json={"job_type": "command", "params": {"command": "database_setup.py", "args": []}},
            headers=_HEADERS,
        )
        assert no_confirm.status_code == 400
        assert "confirmation required" in no_confirm.json()["detail"].lower()
        assert created == []  # nothing spawned on the rejected attempt

        confirmed = client.post(
            "/jobs",
            json={
                "job_type": "command",
                "params": {"command": "database_setup.py", "args": [], "confirm": True},
            },
            headers=_HEADERS,
        )
    assert confirmed.status_code == 200
    assert len(created) == 1
    assert created[0].args == [sys.executable, "database_setup.py"]


def test_two_different_resolved_commands_can_run_concurrently(monkeypatch):
    """Single-flight must key on the resolved command, not the bare job_type
    'command' -- two different targets running at once must both succeed."""
    monkeypatch.setattr(
        orchestrator_runner.subprocess, "Popen", lambda cmd, **kw: _FakePopen(cmd, **kw)
    )
    with _manifest_patch(), _enabled():
        first = client.post(
            "/jobs",
            json={"job_type": "command", "params": {"command": "scripts/preflight_check.py", "args": []}},
            headers=_HEADERS,
        )
        second = client.post(
            "/jobs",
            json={
                "job_type": "command",
                "params": {"command": "scripts/build_command_manifest.py", "args": []},
            },
            headers=_HEADERS,
        )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["job_id"] != second.json()["job_id"]


def test_same_resolved_command_run_twice_while_running_is_409(monkeypatch):
    monkeypatch.setattr(
        orchestrator_runner.subprocess, "Popen", lambda cmd, **kw: _FakePopen(cmd, **kw)
    )
    with _manifest_patch(), _enabled():
        first = client.post(
            "/jobs",
            json={"job_type": "command", "params": {"command": "scripts/preflight_check.py", "args": []}},
            headers=_HEADERS,
        )
        second = client.post(
            "/jobs",
            json={"job_type": "command", "params": {"command": "scripts/preflight_check.py", "args": []}},
            headers=_HEADERS,
        )
    assert first.status_code == 200
    assert second.status_code == 409


# --------------------------------------------------------------------------- #
# Direct unit tests against gui.orchestrator_runner.launch_manifest_command
# --------------------------------------------------------------------------- #
# Cleaner than going through the HTTP layer for precise argv-construction and
# raise-message assertions -- mirrors tests/test_orchestrator_runner.py's
# style of calling launchers directly with subprocess.Popen monkeypatched.


def test_launch_manifest_command_disallowed_raises(monkeypatch):
    mock_popen = MagicMock()
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", mock_popen)
    with _manifest_patch():
        with pytest.raises(ValueError):
            orchestrator_runner.launch_manifest_command("job-1", "app_shell.py", None, [])
    mock_popen.assert_not_called()


def test_launch_manifest_command_unknown_command_raises_with_message(monkeypatch):
    mock_popen = MagicMock()
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", mock_popen)
    with _manifest_patch():
        with pytest.raises(ValueError, match="(?i)unknown command"):
            orchestrator_runner.launch_manifest_command("job-2", "not_a_real_target", None, [])
    mock_popen.assert_not_called()


def test_launch_manifest_command_kill_switch_activate_requires_confirmation(monkeypatch):
    mock_popen = MagicMock()
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", mock_popen)
    with _manifest_patch():
        with pytest.raises(ValueError, match="(?i)confirmation required"):
            orchestrator_runner.launch_manifest_command(
                "job-3", "execution.kill_switch", None, ["--activate"]
            )
    mock_popen.assert_not_called()


def test_launch_manifest_command_kill_switch_deactivate_requires_confirmation(monkeypatch):
    mock_popen = MagicMock()
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", mock_popen)
    with _manifest_patch():
        with pytest.raises(ValueError, match="(?i)confirmation required"):
            orchestrator_runner.launch_manifest_command(
                "job-4", "execution.kill_switch", None, ["--deactivate"]
            )
    mock_popen.assert_not_called()


def test_launch_manifest_command_kill_switch_status_does_not_require_confirmation(monkeypatch):
    """Only the exact HIGH_STAKES_COMMANDS flag-sets gate on confirm -- an
    unrelated flag to the same command must launch normally."""
    created: list = []
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", _recording_popen(created))
    with _manifest_patch():
        handle = orchestrator_runner.launch_manifest_command(
            "job-5", "execution.kill_switch", None, ["--status"]
        )
    assert handle is not None
    assert handle.mode == "command"
    assert len(created) == 1
    assert created[0].args[-1] == "--status"


def test_launch_manifest_command_high_stakes_with_confirm_succeeds(monkeypatch, tmp_path):
    created: list = []
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", _recording_popen(created))
    with _manifest_patch():
        handle = orchestrator_runner.launch_manifest_command(
            "job-6", "execution.kill_switch", None, ["--activate"], confirm=True
        )
    assert handle.mode == "command"
    assert handle.dry_run is False
    assert handle.refresh_account is False
    assert handle.pid == created[0].pid
    assert handle.log_path == tmp_path / "gui_commands" / "job-6.log"
    assert created[0].args == [sys.executable, "-m", "execution.kill_switch", "--activate"]


def test_launch_manifest_command_database_setup_requires_confirmation(monkeypatch):
    """database_setup.py's HIGH_STAKES_COMMANDS entry keys on frozenset()
    (the empty set), so an empty args list still triggers the gate -- unlike
    the kill-switch/main.py entries, there is no flag whose ABSENCE would
    exempt it."""
    mock_popen = MagicMock()
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", mock_popen)
    with _manifest_patch():
        with pytest.raises(ValueError, match="(?i)confirmation required"):
            orchestrator_runner.launch_manifest_command(
                "job-db1", "database_setup.py", None, []
            )
    mock_popen.assert_not_called()


def test_launch_manifest_command_database_setup_with_confirm_succeeds(monkeypatch, tmp_path):
    created: list = []
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", _recording_popen(created))
    with _manifest_patch():
        handle = orchestrator_runner.launch_manifest_command(
            "job-db2", "database_setup.py", None, [], confirm=True
        )
    assert handle.mode == "command"
    assert handle.pid == created[0].pid
    assert created[0].args == [sys.executable, "database_setup.py"]


def test_launch_manifest_command_main_refresh_account_requires_confirmation(monkeypatch):
    mock_popen = MagicMock()
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", mock_popen)
    with _manifest_patch():
        with pytest.raises(ValueError, match="(?i)confirmation required"):
            orchestrator_runner.launch_manifest_command(
                "job-7", "main.py", None, ["--refresh-account"]
            )
    mock_popen.assert_not_called()


def test_launch_manifest_command_refresh_account_confirm_sets_handle_field(monkeypatch):
    created: list = []
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", _recording_popen(created))
    with _manifest_patch():
        handle = orchestrator_runner.launch_manifest_command(
            "job-8", "main.py", None, ["--refresh-account"], confirm=True
        )
    assert handle.mode == "command"
    assert handle.refresh_account is True
    assert created[0].args == [sys.executable, "main.py", "--refresh-account"]


def test_launch_manifest_command_refresh_account_false_when_flag_absent(monkeypatch):
    created: list = []
    monkeypatch.setattr(orchestrator_runner.subprocess, "Popen", _recording_popen(created))
    with _manifest_patch():
        handle = orchestrator_runner.launch_manifest_command(
            "job-9", "scripts/preflight_check.py", None, []
        )
    assert handle.refresh_account is False
    assert created[0].args == [sys.executable, "scripts/preflight_check.py"]
