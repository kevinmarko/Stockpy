"""
tests/test_ui_server.py
========================
Unit tests for ``desktop/ui_server.py`` — the Streamlit Command Center UI
supervisor used by WS4's native shell (``app_shell.py``) to start/stop the
Streamlit UI as a background subprocess instead of a browser tab.

Coverage:
*   :func:`start_ui_server` builds the correct Streamlit argv (module
    invocation, ``gui/app.py``, ``--server.port``/``--server.address``/
    ``--server.headless`` flags) and passes the correct ``cwd`` (repo root).
    ``subprocess.Popen`` is monkeypatched so no real Streamlit process is
    spawned for this assertion.
*   :func:`stop_ui_server` against a REAL short-lived child process (a
    ``python -c "time.sleep(30)"`` stand-in) confirms it is actually
    terminated within the timeout.
*   :func:`stop_ui_server` returns ``True`` immediately for an already-exited
    process without erroring.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from desktop import ui_server


# ---------------------------------------------------------------------------
# start_ui_server — argv / cwd construction
# ---------------------------------------------------------------------------

class _FakePopen:
    """Stand-in for subprocess.Popen capturing the call args."""

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.kwargs = kwargs
        self.pid = 12345

    def poll(self):
        return None


def test_start_ui_server_builds_correct_argv(monkeypatch, tmp_path):
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakePopen(cmd, **kwargs)

    monkeypatch.setattr(ui_server.settings, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(ui_server, "UI_LOG_PATH", tmp_path / "gui_ui.log")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    popen = ui_server.start_ui_server(port=8599)

    expected_cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "gui/app.py",
        "--server.headless",
        "true",
        "--server.port",
        "8599",
        "--server.address",
        "127.0.0.1",
    ]
    assert captured["cmd"] == expected_cmd
    assert captured["kwargs"]["cwd"] == str(ui_server._REPO_ROOT)
    assert popen.pid == 12345


def test_start_ui_server_headless_false(monkeypatch, tmp_path):
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakePopen(cmd, **kwargs)

    monkeypatch.setattr(ui_server.settings, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(ui_server, "UI_LOG_PATH", tmp_path / "gui_ui.log")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    ui_server.start_ui_server(port=9001, headless=False)

    idx = captured["cmd"].index("--server.headless")
    assert captured["cmd"][idx + 1] == "false"
    port_idx = captured["cmd"].index("--server.port")
    assert captured["cmd"][port_idx + 1] == "9001"


def test_start_ui_server_inherits_environment(monkeypatch, tmp_path):
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["kwargs"] = kwargs
        return _FakePopen(cmd, **kwargs)

    monkeypatch.setattr(ui_server.settings, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(ui_server, "UI_LOG_PATH", tmp_path / "gui_ui.log")
    monkeypatch.setenv("SOME_TEST_VAR", "some_value")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    ui_server.start_ui_server(port=8600)

    assert captured["kwargs"]["env"].get("SOME_TEST_VAR") == "some_value"


def test_start_ui_server_truncates_log_file(monkeypatch, tmp_path):
    log_path = tmp_path / "gui_ui.log"
    log_path.write_text("stale content from a previous run\n", encoding="utf-8")

    monkeypatch.setattr(ui_server.settings, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(ui_server, "UI_LOG_PATH", log_path)
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: _FakePopen(cmd, **kw))

    ui_server.start_ui_server(port=8601)

    content = log_path.read_text(encoding="utf-8")
    assert "stale content" not in content
    assert "InvestYo Streamlit UI server launch" in content


# ---------------------------------------------------------------------------
# stop_ui_server — real child process
# ---------------------------------------------------------------------------

def test_stop_ui_server_terminates_real_process():
    popen = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    try:
        assert popen.poll() is None  # confirm it's actually running

        result = ui_server.stop_ui_server(popen, timeout=5.0)

        assert result is True
        assert popen.poll() is not None  # confirmed stopped
    finally:
        if popen.poll() is None:
            popen.kill()
            popen.wait(timeout=5)


def test_stop_ui_server_already_exited_process_returns_true():
    popen = subprocess.Popen([sys.executable, "-c", "pass"])
    popen.wait(timeout=5)  # let it exit naturally
    assert popen.poll() is not None

    result = ui_server.stop_ui_server(popen, timeout=5.0)

    assert result is True


def test_stop_ui_server_none_returns_true():
    assert ui_server.stop_ui_server(None) is True


def test_stop_ui_server_never_raises_on_error(monkeypatch):
    class _ExplodingPopen:
        pid = 999

        def poll(self):
            raise RuntimeError("boom")

    result = ui_server.stop_ui_server(_ExplodingPopen())
    assert result is False
