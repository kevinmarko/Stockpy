"""
tests/test_orchestrator_runner.py
=================================
Unit tests for the new launcher helpers in ``gui/orchestrator_runner.py``
introduced as part of the Launcher / Telemetry remediation task.

Coverage:
*   :func:`validate_required_env` reports missing AND present env vars
    correctly (this is the pre-launch readiness check that surfaces
    ``FRED_API_KEY`` absence before the subprocess silently degrades).
*   :func:`read_log_tail` honours the ``handle.log_path`` of the active run
    handle (so a non-orchestrator launcher's own log is shown when it was
    launched, and the orchestrator log otherwise).
*   :func:`read_telemetry_tail` reads ``logs/investyo.log`` and returns a
    polite idle hint when the file does not yet exist.
*   :func:`launch_orchestrator` routes to the orchestrator log, tags the handle
    with the correct mode, and never forwards ``--refresh-account`` (which
    ``main_orchestrator.py`` does not accept).
*   :func:`launch_train_lgbm` and :func:`launch_train_meta_labelers` (the
    Models tab "Retrain Now" launchers) route to their own log paths, tag the
    handle with the correct mode, build the expected argv (including the
    optional ``--signal`` flag), and construct ``RunHandle`` with keyword
    arguments only.

All subprocess calls are monkeypatched so no real ``main_orchestrator.py``
child process is spawned (CONSTRAINT #6).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture()
def runner(monkeypatch, tmp_path):
    """Import ``shared.orchestrator_runner`` with all FS paths sandboxed to ``tmp_path``."""
    from shared import orchestrator_runner as runner

    # Redirect every FS sink the module writes to.
    monkeypatch.setattr(runner, "RUN_LOG_PATH", tmp_path / "gui_run.log")
    monkeypatch.setattr(runner, "PYTEST_LOG_PATH", tmp_path / "gui_pytest.log")
    monkeypatch.setattr(runner, "VERIFY_LOG_PATH", tmp_path / "gui_verify.log")
    monkeypatch.setattr(runner, "TELEMETRY_LOG_PATH", tmp_path / "investyo.log")
    monkeypatch.setattr(runner, "TRAIN_LGBM_LOG_PATH", tmp_path / "gui_train_lgbm.log")
    monkeypatch.setattr(runner, "TRAIN_META_LOG_PATH", tmp_path / "gui_train_meta.log")
    monkeypatch.setattr(runner.settings, "OUTPUT_DIR", tmp_path)
    return runner


# ---------------------------------------------------------------------------
# validate_required_env
# ---------------------------------------------------------------------------

def test_validate_required_env_detects_missing(monkeypatch, runner):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    out = runner.validate_required_env(["FRED_API_KEY"])
    assert out == {"FRED_API_KEY": False}


def test_validate_required_env_detects_present(monkeypatch, runner):
    monkeypatch.setenv("FRED_API_KEY", "abc123")
    out = runner.validate_required_env(["FRED_API_KEY"])
    assert out == {"FRED_API_KEY": True}


def test_validate_required_env_treats_whitespace_as_missing(monkeypatch, runner):
    monkeypatch.setenv("FRED_API_KEY", "   ")
    out = runner.validate_required_env(["FRED_API_KEY"])
    assert out == {"FRED_API_KEY": False}


# ---------------------------------------------------------------------------
# read_log_tail / read_telemetry_tail
# ---------------------------------------------------------------------------

def test_read_log_tail_follows_handle_log_path(runner, tmp_path):
    # Write distinct contents to each log so we can tell which one was read.
    runner.RUN_LOG_PATH.write_text("orch line\n", encoding="utf-8")
    runner.PYTEST_LOG_PATH.write_text("pytest line\n", encoding="utf-8")

    orch_handle = runner.RunHandle(
        pid=1, started_at=time.time(), dry_run=False,
        refresh_account=False, log_path=runner.RUN_LOG_PATH, mode="orchestrator",
    )
    pytest_handle = runner.RunHandle(
        pid=2, started_at=time.time(), dry_run=False,
        refresh_account=False, log_path=runner.PYTEST_LOG_PATH, mode="pytest",
    )
    assert "orch line" in runner.read_log_tail(handle=orch_handle)
    assert "pytest line" in runner.read_log_tail(handle=pytest_handle)


def test_read_log_tail_returns_hint_when_missing(runner):
    txt = runner.read_log_tail()
    assert "no run log yet" in txt.lower()


# ---------------------------------------------------------------------------
# heartbeat_age_seconds / state_snapshot_age_seconds
# ---------------------------------------------------------------------------
# heartbeat.txt is orchestrator-only (main_orchestrator.py); state_snapshot.json
# is rewritten by EVERY run_once() cycle in BOTH main.py (interval/agent mode
# included) and main_orchestrator.py -- gui/engine_status.py depends on this
# distinction to show a correct badge regardless of which entry point is
# actually driving the refresh loop.

def test_heartbeat_age_seconds_none_when_missing(runner):
    assert runner.heartbeat_age_seconds() is None


def test_heartbeat_age_seconds_reflects_file_mtime(runner):
    hb = runner.settings.OUTPUT_DIR / "heartbeat.txt"
    hb.write_text("2026-01-01T00:00:00Z", encoding="utf-8")
    age = runner.heartbeat_age_seconds()
    assert age is not None
    assert 0.0 <= age < 5.0


def test_state_snapshot_age_seconds_none_when_missing(runner):
    assert runner.state_snapshot_age_seconds() is None


def test_state_snapshot_age_seconds_reflects_file_mtime(runner):
    snap = runner.settings.OUTPUT_DIR / "state_snapshot.json"
    snap.write_text("{}", encoding="utf-8")
    age = runner.state_snapshot_age_seconds()
    assert age is not None
    assert 0.0 <= age < 5.0


def test_state_snapshot_age_seconds_independent_of_heartbeat(runner):
    """main.py --interval mode: only state_snapshot.json is ever written --
    state_snapshot_age_seconds() must report freshness with no heartbeat.txt
    present at all."""
    snap = runner.settings.OUTPUT_DIR / "state_snapshot.json"
    snap.write_text("{}", encoding="utf-8")
    assert runner.heartbeat_age_seconds() is None
    assert runner.state_snapshot_age_seconds() is not None


def test_read_telemetry_tail_missing_returns_hint(runner):
    txt = runner.read_telemetry_tail()
    assert "no telemetry yet" in txt.lower()


def test_read_telemetry_tail_returns_last_lines(runner):
    runner.TELEMETRY_LOG_PATH.write_text(
        "\n".join(f"line {i}" for i in range(500)), encoding="utf-8",
    )
    out = runner.read_telemetry_tail(max_lines=3)
    assert out.splitlines() == ["line 497", "line 498", "line 499"]


# ---------------------------------------------------------------------------
# launch_orchestrator — routing, mode tagging & argv
# ---------------------------------------------------------------------------

class _FakePopen:
    """Stand-in for subprocess.Popen that records the command and pretends to run."""

    def __init__(self, cmd, **kwargs):
        self.args = cmd
        self.pid = 4242
        self.kwargs = kwargs
        self._polled = None

    def poll(self):
        return self._polled


def test_launch_orchestrator_routes_to_orchestrator_log(monkeypatch, runner):
    def fake_popen(cmd, **kwargs):
        return _FakePopen(cmd, **kwargs)

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    handle = runner.launch_orchestrator(dry_run=True)

    assert handle.mode == "orchestrator"
    assert handle.log_path == runner.RUN_LOG_PATH
    assert handle.dry_run is True
    assert runner.RUN_LOG_PATH.exists()


@pytest.mark.parametrize(
    "dry_run,expected_tail",
    [(False, []), (True, ["--dry-run"])],
)
def test_launch_orchestrator_argv_only_carries_flags_its_argparse_accepts(
    monkeypatch, runner, dry_run, expected_tail,
):
    """main_orchestrator.py's argparse accepts only --dry-run/--strict; a
    forwarded --refresh-account made the subprocess fall back exit 2."""
    captured: dict = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakePopen(cmd, **kwargs)

    # Non-dry runs try the daemon first; make it unreachable so the
    # subprocess command is what gets built.
    import shared.daemon_client as daemon_client

    monkeypatch.setattr(daemon_client, "daemon_available", lambda timeout=0.5: False)
    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    handle = runner.launch_orchestrator(dry_run=dry_run)

    assert captured["cmd"] == [sys.executable, "main_orchestrator.py", *expected_tail]
    assert "--refresh-account" not in captured["cmd"]
    assert handle.refresh_account is False
    header = runner.RUN_LOG_PATH.read_text(encoding="utf-8").splitlines()[0]
    assert "refresh_account" not in header


def test_launch_orchestrator_no_longer_accepts_refresh_account(runner):
    """The param was removed outright (callers get a TypeError, not a silent
    drop); api/_jobs.py rejects the job param with HTTP 400 instead."""
    with pytest.raises(TypeError):
        runner.launch_orchestrator(dry_run=True, refresh_account=True)  # type: ignore[call-arg]


def test_retired_main_py_launchers_are_gone(runner):
    for name in (
        "launch_advisory_main",
        "launch_scheduled_advisory",
        "launch_symbol_retry",
        "ADVISORY_LOG_PATH",
        "SCHEDULED_LOG_PATH",
        "RETRY_LOG_PATH",
    ):
        assert not hasattr(runner, name), name
    assert "main.py" not in runner.HIGH_STAKES_COMMANDS


def test_launch_pytest_routes_to_pytest_log(monkeypatch, runner):
    captured: dict = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["stdout"] = kwargs.get("stdout")
        return _FakePopen(cmd, **kwargs)

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    handle = runner.launch_pytest()

    assert handle.mode == "pytest"
    assert handle.log_path == runner.PYTEST_LOG_PATH
    assert handle.dry_run is False
    assert handle.refresh_account is False
    # Command must run pytest quietly via the current interpreter.
    assert captured["cmd"] == [sys.executable, "-m", "pytest", "-q"]
    # Log file must exist (truncated header line written).
    assert runner.PYTEST_LOG_PATH.exists()


def test_launch_verify_routes_to_verify_log(monkeypatch, runner):
    captured: dict = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["stdout"] = kwargs.get("stdout")
        return _FakePopen(cmd, **kwargs)

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    handle = runner.launch_verify()

    assert handle.mode == "verify"
    assert handle.log_path == runner.VERIFY_LOG_PATH
    assert handle.dry_run is False
    assert handle.refresh_account is False
    # Command must be the make target, NOT sys.executable — make is the exe.
    assert captured["cmd"] == ["make", "verify"]
    # Log file must exist (truncated header line written).
    assert runner.VERIFY_LOG_PATH.exists()


# ---------------------------------------------------------------------------
# launch_train_lgbm / launch_train_meta_labelers — Models tab "Retrain Now"
# ---------------------------------------------------------------------------

def test_launch_train_lgbm_returns_well_formed_handle(monkeypatch, runner):
    captured: dict = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["stdout"] = kwargs.get("stdout")
        return _FakePopen(cmd, **kwargs)

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    handle = runner.launch_train_lgbm()

    # Pins RunHandle being constructed with keyword args only (a positional
    # mistake would silently scramble which field gets which value, since
    # RunHandle's field order does not match this call's argument order).
    assert handle.log_path == runner.TRAIN_LGBM_LOG_PATH
    assert handle.mode == "train_lgbm"
    assert isinstance(handle.pid, int)
    assert isinstance(handle.started_at, float)
    assert handle.dry_run is False
    assert handle.refresh_account is False
    assert isinstance(handle._popen, _FakePopen)

    # Command must invoke scripts.train_lgbm via the current interpreter with
    # no extra CLI args.
    assert captured["cmd"] == [sys.executable, "-m", "scripts.train_lgbm"]
    assert runner.TRAIN_LGBM_LOG_PATH.exists()


def test_launch_train_meta_labelers_with_signal_builds_flag(monkeypatch, runner):
    captured: dict = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakePopen(cmd, **kwargs)

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    handle = runner.launch_train_meta_labelers(signal="timeseries_momentum")

    assert handle.log_path == runner.TRAIN_META_LOG_PATH
    assert handle.mode == "train_meta"
    assert isinstance(handle.pid, int)
    assert isinstance(handle.started_at, float)
    assert handle.dry_run is False
    assert handle.refresh_account is False
    assert isinstance(handle._popen, _FakePopen)

    cmd = captured["cmd"]
    assert cmd[:3] == [sys.executable, "-m", "scripts.train_meta_labelers"]
    assert "--signal" in cmd
    assert cmd[cmd.index("--signal") + 1] == "timeseries_momentum"
    assert runner.TRAIN_META_LOG_PATH.exists()


def test_launch_train_meta_labelers_without_signal_omits_flag(monkeypatch, runner):
    captured: dict = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakePopen(cmd, **kwargs)

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    handle = runner.launch_train_meta_labelers()

    assert handle.mode == "train_meta"
    cmd = captured["cmd"]
    assert cmd == [sys.executable, "-m", "scripts.train_meta_labelers"]
    assert "--signal" not in cmd


def test_compute_stage_status_follows_the_handles_own_log(monkeypatch, runner):
    """Stage marker scan must follow the handle's own log, not the orchestrator default."""
    # Seed a non-orchestrator log with no orchestrator markers; stages should all be idle/pending.
    runner.VERIFY_LOG_PATH.write_text("InvestYo Quant Platform starting.\n", encoding="utf-8")
    handle = runner.RunHandle(
        pid=1, started_at=time.time(), dry_run=False, refresh_account=False,
        log_path=runner.VERIFY_LOG_PATH, mode="verify",
    )
    status = runner.compute_stage_status(handle)
    # No orchestrator markers present → first stage is "pending" / others too.
    for v in status.values():
        assert v in {"idle", "pending"}
