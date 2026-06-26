"""
tests/test_preflight_runner.py
================================
Unit tests for :mod:`gui.preflight_runner`.

All network / subprocess calls are monkeypatched.

Verified invariants
-------------------
*   Module imports cleanly.
*   :class:`PreflightReport` is a frozen dataclass with ``all_passed: bool``.
*   :func:`run_preflight` returns ``PreflightReport`` with typed
    :class:`PreflightCheck` entries on a good JSON response.
*   **CONSTRAINT #4**: Timeout → ``all_passed=False`` (never fabricated success).
*   Missing script → ``all_passed=False``.
*   Corrupt JSON → ``all_passed=False``.
*   Empty stdout → ``all_passed=False``.
*   Non-zero exit code → ``all_passed=False`` (even with valid JSON).
*   Zero exit code + all passing → ``all_passed=True``.
*   :func:`_render_preflight_panel` exists in ``gui.panels``.
*   ``render_launcher`` references ``_render_preflight_panel`` (wiring check).
"""

from __future__ import annotations

import ast
import inspect
import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest


# ===========================================================================
# Module imports
# ===========================================================================

def test_preflight_runner_importable():
    from gui import preflight_runner  # noqa: F401


def test_preflight_check_frozen():
    from gui.preflight_runner import PreflightCheck

    pc = PreflightCheck(name="x", passed=True, reason="ok")
    with pytest.raises((AttributeError, TypeError)):
        pc.name = "y"  # type: ignore[misc]


def test_preflight_report_frozen():
    from gui.preflight_runner import PreflightReport

    pr = PreflightReport(all_passed=True)
    with pytest.raises((AttributeError, TypeError)):
        pr.all_passed = False  # type: ignore[misc]


# ===========================================================================
# run_preflight — good path
# ===========================================================================

def _good_result(returncode: int = 0, checks_json: str | None = None):
    if checks_json is None:
        checks_json = json.dumps([
            {"name": "fred_key_configured", "passed": True, "reason": "key present", "warning": False},
            {"name": "kill_switch_inactive", "passed": True, "reason": "no sentinel", "warning": False},
        ])
    result = mock.MagicMock()
    result.returncode = returncode
    result.stdout = checks_json
    result.stderr = ""
    return result


def test_run_preflight_returns_typed_report():
    from gui.preflight_runner import PreflightReport, run_preflight

    with mock.patch("subprocess.run", return_value=_good_result(0)):
        report = run_preflight(timeout=5.0)

    assert isinstance(report, PreflightReport)
    assert isinstance(report.all_passed, bool)
    assert report.all_passed is True
    assert len(report.checks) == 2


def test_run_preflight_checks_are_typed():
    from gui.preflight_runner import PreflightCheck, run_preflight

    with mock.patch("subprocess.run", return_value=_good_result(0)):
        report = run_preflight()

    for c in report.checks:
        assert isinstance(c, PreflightCheck)
        assert isinstance(c.passed, bool)
        assert isinstance(c.name, str)
        assert isinstance(c.reason, str)


def test_run_preflight_non_zero_exit_sets_all_passed_false():
    """Non-zero exit → all_passed=False even with valid JSON."""
    with mock.patch("subprocess.run", return_value=_good_result(returncode=1)):
        from gui.preflight_runner import run_preflight
        report = run_preflight()
    assert report.all_passed is False
    assert report.returncode == 1


# ===========================================================================
# CONSTRAINT #4 — timeout path never fabricates success
# ===========================================================================

def test_run_preflight_timeout_returns_all_passed_false():
    """Timeout → all_passed=False (CONSTRAINT #4 — no fabricated success)."""
    from gui.preflight_runner import run_preflight

    with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5.0)):
        report = run_preflight(timeout=5.0)

    assert report.all_passed is False
    assert report.returncode is None
    assert "timeout" in (report.error or "").lower()


def test_run_preflight_missing_script_returns_false(tmp_path, monkeypatch):
    """Non-existent script → all_passed=False (CONSTRAINT #4)."""
    from gui import preflight_runner
    monkeypatch.setattr(preflight_runner, "_PREFLIGHT_SCRIPT", tmp_path / "no_such.py")
    from gui.preflight_runner import run_preflight
    report = run_preflight()
    assert report.all_passed is False


def test_run_preflight_corrupt_json_returns_false():
    """Corrupt JSON output → all_passed=False."""
    bad = mock.MagicMock()
    bad.returncode = 0
    bad.stdout = "{NOT valid json!!!"
    bad.stderr = ""

    with mock.patch("subprocess.run", return_value=bad):
        from gui.preflight_runner import run_preflight
        report = run_preflight()

    assert report.all_passed is False
    assert report.error is not None


def test_run_preflight_empty_stdout_returns_false():
    """Empty stdout → all_passed=False."""
    empty = mock.MagicMock()
    empty.returncode = 0
    empty.stdout = ""
    empty.stderr = "something went wrong"

    with mock.patch("subprocess.run", return_value=empty):
        from gui.preflight_runner import run_preflight
        report = run_preflight()

    assert report.all_passed is False


def test_run_preflight_subprocess_exception_returns_false():
    """Subprocess launch failure → all_passed=False."""
    with mock.patch("subprocess.run", side_effect=OSError("binary not found")):
        from gui.preflight_runner import run_preflight
        report = run_preflight()

    assert report.all_passed is False
    assert "binary not found" in (report.error or "")


# ===========================================================================
# Wiring checks — gui.panels
# ===========================================================================

def test_render_preflight_panel_exists_in_panels():
    import gui.panels as panels
    assert hasattr(panels, "_render_preflight_panel"), (
        "_render_preflight_panel must be defined in gui.panels"
    )


def test_render_preflight_panel_callable():
    import gui.panels as panels
    assert callable(panels._render_preflight_panel)


def test_render_launcher_calls_preflight_panel():
    """render_launcher must call _render_preflight_panel."""
    import gui.panels as panels

    src = inspect.getsource(panels.render_launcher)
    assert "_render_preflight_panel" in src, (
        "render_launcher must call _render_preflight_panel"
    )
