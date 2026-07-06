"""tests/test_engine_supervisor.py
===================================
Pins the exact call contract of ``desktop/engine_supervisor.py`` — a thin
pass-through wrapper over ``gui.orchestrator_runner``'s already-tested
``launch_scheduled_advisory`` / ``stop_run`` functions (WS3 of the
always-on-desktop-app unification).

These tests monkeypatch the underlying ``gui.orchestrator_runner`` functions
so they exercise only the wrapper's argument-mapping and pass-through
behavior, never the real subprocess machinery.
"""

from __future__ import annotations

import gui.orchestrator_runner as orchestrator_runner
from desktop.engine_supervisor import start_engine, stop_engine


class _SentinelHandle:
    """Stand-in for gui.orchestrator_runner.RunHandle."""


def test_start_engine_default_maps_to_launch_scheduled_advisory(monkeypatch):
    captured = {}
    sentinel = _SentinelHandle()

    def fake_launch_scheduled_advisory(mode, interval_seconds, *, refresh_account=False):
        captured["mode"] = mode
        captured["interval_seconds"] = interval_seconds
        captured["refresh_account"] = refresh_account
        return sentinel

    monkeypatch.setattr(
        orchestrator_runner, "launch_scheduled_advisory", fake_launch_scheduled_advisory
    )

    result = start_engine(300)

    assert captured == {
        "mode": "interval",
        "interval_seconds": 300,
        "refresh_account": False,
    }
    assert result is sentinel


def test_start_engine_maps_custom_interval_and_refresh_account(monkeypatch):
    captured = {}
    sentinel = _SentinelHandle()

    def fake_launch_scheduled_advisory(mode, interval_seconds, *, refresh_account=False):
        captured["mode"] = mode
        captured["interval_seconds"] = interval_seconds
        captured["refresh_account"] = refresh_account
        return sentinel

    monkeypatch.setattr(
        orchestrator_runner, "launch_scheduled_advisory", fake_launch_scheduled_advisory
    )

    result = start_engine(60, refresh_account=True)

    assert captured == {
        "mode": "interval",
        "interval_seconds": 60,
        "refresh_account": True,
    }
    assert result is sentinel


def test_stop_engine_maps_handle_and_timeout(monkeypatch):
    captured = {}
    handle = _SentinelHandle()

    def fake_stop_run(h, *, timeout=5.0):
        captured["handle"] = h
        captured["timeout"] = timeout
        return True

    monkeypatch.setattr(orchestrator_runner, "stop_run", fake_stop_run)

    result = stop_engine(handle, timeout=7.0)

    assert captured["handle"] is handle
    assert captured["timeout"] == 7.0
    assert result is True


def test_stop_engine_passes_through_false_return(monkeypatch):
    handle = _SentinelHandle()

    def fake_stop_run(h, *, timeout=5.0):
        return False

    monkeypatch.setattr(orchestrator_runner, "stop_run", fake_stop_run)

    result = stop_engine(handle, timeout=1.0)

    assert result is False
