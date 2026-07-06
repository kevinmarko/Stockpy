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
    handle (so the advisory log is shown when ``main.py`` was launched, and
    the orchestrator log otherwise).
*   :func:`read_telemetry_tail` reads ``logs/investyo.log`` and returns a
    polite idle hint when the file does not yet exist.
*   :func:`launch_advisory_main` and :func:`launch_orchestrator` route to
    distinct log paths and tag the handle with the correct mode.

All subprocess calls are monkeypatched so no real ``main.py`` /
``main_orchestrator.py`` child process is spawned (CONSTRAINT #6).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture()
def runner(monkeypatch, tmp_path):
    """Import ``gui.orchestrator_runner`` with all FS paths sandboxed to ``tmp_path``."""
    from gui import orchestrator_runner as runner

    # Redirect every FS sink the module writes to.
    monkeypatch.setattr(runner, "RUN_LOG_PATH", tmp_path / "gui_run.log")
    monkeypatch.setattr(runner, "ADVISORY_LOG_PATH", tmp_path / "gui_advisory.log")
    monkeypatch.setattr(runner, "PYTEST_LOG_PATH", tmp_path / "gui_pytest.log")
    monkeypatch.setattr(runner, "VERIFY_LOG_PATH", tmp_path / "gui_verify.log")
    monkeypatch.setattr(runner, "TELEMETRY_LOG_PATH", tmp_path / "investyo.log")
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
    runner.ADVISORY_LOG_PATH.write_text("adv line\n", encoding="utf-8")

    orch_handle = runner.RunHandle(
        pid=1, started_at=time.time(), dry_run=False,
        refresh_account=False, log_path=runner.RUN_LOG_PATH, mode="orchestrator",
    )
    adv_handle = runner.RunHandle(
        pid=2, started_at=time.time(), dry_run=False,
        refresh_account=False, log_path=runner.ADVISORY_LOG_PATH, mode="advisory",
    )
    assert "orch line" in runner.read_log_tail(handle=orch_handle)
    assert "adv line" in runner.read_log_tail(handle=adv_handle)


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
# launch_advisory_main / launch_orchestrator — routing & mode tagging
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


def test_launch_advisory_main_routes_to_advisory_log(monkeypatch, runner):
    captured: dict = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["stdout"] = kwargs.get("stdout")
        return _FakePopen(cmd, **kwargs)

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    handle = runner.launch_advisory_main(refresh_account=True)

    assert handle.mode == "advisory"
    assert handle.log_path == runner.ADVISORY_LOG_PATH
    assert handle.refresh_account is True
    assert handle.dry_run is False  # advisory mode never submits orders
    # Command must invoke main.py with the refresh-account flag.
    assert captured["cmd"][0] == sys.executable
    assert captured["cmd"][1] == "main.py"
    assert "--refresh-account" in captured["cmd"]
    # Log file must exist (truncated header line written).
    assert runner.ADVISORY_LOG_PATH.exists()


def test_launch_orchestrator_routes_to_orchestrator_log(monkeypatch, runner):
    def fake_popen(cmd, **kwargs):
        return _FakePopen(cmd, **kwargs)

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    handle = runner.launch_orchestrator(dry_run=True, refresh_account=False)

    assert handle.mode == "orchestrator"
    assert handle.log_path == runner.RUN_LOG_PATH
    assert handle.dry_run is True
    assert runner.RUN_LOG_PATH.exists()


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


def test_compute_stage_status_handles_advisory_log(monkeypatch, runner):
    """Stage marker scan must follow the handle's own log, not the orchestrator default."""
    # Seed the advisory log with no orchestrator markers; stages should all be idle/pending.
    runner.ADVISORY_LOG_PATH.write_text("InvestYo Quant Platform starting.\n", encoding="utf-8")
    handle = runner.RunHandle(
        pid=1, started_at=time.time(), dry_run=False, refresh_account=False,
        log_path=runner.ADVISORY_LOG_PATH, mode="advisory",
    )
    status = runner.compute_stage_status(handle)
    # No orchestrator markers present → first stage is "pending" / others too.
    for v in status.values():
        assert v in {"idle", "pending"}
