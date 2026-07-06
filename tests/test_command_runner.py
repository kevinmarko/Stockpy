"""
tests/test_command_runner.py
============================
Unit tests for ``gui/command_runner.py``.

Testing strategy
----------------
``subprocess.run`` is monkeypatched in every test — no real process is ever
spawned, so these tests are pure and offline. Each test either substitutes a
fake ``subprocess.run`` that returns a ``CompletedProcess``-shaped stub, or one
that raises (``TimeoutExpired`` / ``OSError``) to exercise the dead-letter path.

Coverage
--------
* ``run_command`` builds the correct argv and ``cwd`` (repo root).
* Success path: exit 0 → ``ok=True``, stdout/stderr captured.
* Failure path: exit 1 → ``ok=False``, ``returncode=1``.
* Timeout: ``TimeoutExpired`` → ``ok=False``, ``error`` contains "timeout",
  ``returncode is None``.
* Generic exception: ``OSError`` → ``ok=False``, ``error`` set, never raises.
* ``run_daily_briefing`` / ``run_database_setup`` build the exact argv.
* ``CommandResult`` is frozen (assignment raises).
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from gui import command_runner
from gui.command_runner import (
    CommandResult,
    run_command,
    run_daily_briefing,
    run_database_setup,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    """A stub with the attributes command_runner reads off subprocess.run()."""
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# run_command — argv / cwd wiring
# ---------------------------------------------------------------------------

def test_run_command_builds_argv_and_cwd(monkeypatch):
    captured = {}

    def fake_run(cmd, *, cwd, capture_output, text, timeout):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        return _fake_completed(0, "out", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    run_command(["echo", "hi"], timeout=42.0, label="unit")

    assert captured["cmd"] == ["echo", "hi"]
    assert captured["cwd"] == str(command_runner._REPO_ROOT)
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["timeout"] == 42.0


# ---------------------------------------------------------------------------
# run_command — success path
# ---------------------------------------------------------------------------

def test_run_command_success(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _fake_completed(0, "hello", "warn")
    )

    result = run_command(["anything"])

    assert isinstance(result, CommandResult)
    assert result.ok is True
    assert result.stdout == "hello"
    assert result.stderr == "warn"
    assert result.returncode == 0
    assert result.error is None


def test_run_command_none_streams_coerced_to_empty(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _fake_completed(0, None, None)
    )

    result = run_command(["anything"])

    assert result.ok is True
    assert result.stdout == ""
    assert result.stderr == ""


# ---------------------------------------------------------------------------
# run_command — failure path
# ---------------------------------------------------------------------------

def test_run_command_failure(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _fake_completed(1, "", "boom")
    )

    result = run_command(["anything"])

    assert result.ok is False
    assert result.returncode == 1
    assert result.stderr == "boom"
    assert result.error is None


# ---------------------------------------------------------------------------
# run_command — timeout path
# ---------------------------------------------------------------------------

def test_run_command_timeout(monkeypatch):
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="anything", timeout=5.0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_command(["anything"], timeout=5.0)

    assert result.ok is False
    assert result.returncode is None
    assert result.error is not None
    assert "timeout" in result.error
    assert result.stdout == ""
    assert result.stderr == ""


# ---------------------------------------------------------------------------
# run_command — generic exception path (never raises)
# ---------------------------------------------------------------------------

def test_run_command_generic_exception(monkeypatch):
    def fake_run(*a, **k):
        raise OSError("interpreter vanished")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_command(["anything"])  # must not raise

    assert result.ok is False
    assert result.returncode is None
    assert result.error == "interpreter vanished"


# ---------------------------------------------------------------------------
# Convenience wrappers — exact argv
# ---------------------------------------------------------------------------

def test_run_daily_briefing_argv(monkeypatch):
    captured = {}

    def fake_run(cmd, **k):
        captured["cmd"] = cmd
        return _fake_completed(0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    run_daily_briefing()

    assert captured["cmd"] == [
        sys.executable,
        "-m",
        "scripts.daily_briefing",
        "--print",
    ]


def test_run_database_setup_argv(monkeypatch):
    captured = {}

    def fake_run(cmd, **k):
        captured["cmd"] = cmd
        return _fake_completed(0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    run_database_setup()

    assert captured["cmd"] == [sys.executable, "database_setup.py"]


# ---------------------------------------------------------------------------
# CommandResult — frozen
# ---------------------------------------------------------------------------

def test_command_result_is_frozen():
    result = CommandResult(ok=True, stdout="a", stderr="b", returncode=0)
    with pytest.raises(FrozenInstanceError):
        result.ok = False  # type: ignore[misc]
