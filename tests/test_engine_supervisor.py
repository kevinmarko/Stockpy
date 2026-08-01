"""tests/test_engine_supervisor.py
===================================
Pins the exact call contract of ``desktop/engine_supervisor.py`` — a thin
pass-through wrapper over ``gui.orchestrator_runner``'s already-tested
``launch_scheduled_advisory`` / ``stop_run`` functions (WS3 of the
always-on-desktop-app unification), OR, behind
``settings.ORCHESTRATOR_DAEMON_ENABLED``, ``launch_daemon_engine`` (see
tests/test_orchestrator_runner_daemon_cutover.py for that branch's own
dedicated coverage).

These tests monkeypatch the underlying ``gui.orchestrator_runner`` functions
so they exercise only the wrapper's argument-mapping and pass-through
behavior, never the real subprocess machinery.

The default-path tests below explicitly pin
``settings.ORCHESTRATOR_DAEMON_ENABLED = False`` rather than relying on the
field's own default. ``Settings`` reads the real ``.env`` file directly via
pydantic-settings' ``env_file=".env"`` config -- independent of any
``load_dotenv()`` call -- so an operator's real ``.env`` (e.g. one who has
locally enabled the daemon for their own use) would otherwise silently flip
these "default behavior" tests onto the daemon branch instead. Pinning here
is the same pattern already used throughout
tests/test_orchestrator_runner_daemon_cutover.py.
"""

from __future__ import annotations

import gui.orchestrator_runner as orchestrator_runner
from desktop.engine_supervisor import start_engine, stop_engine


class _SentinelHandle:
    """Stand-in for gui.orchestrator_runner.RunHandle."""


def test_start_engine_default_maps_to_launch_scheduled_advisory(monkeypatch):
    monkeypatch.setattr(orchestrator_runner.settings, "ORCHESTRATOR_DAEMON_ENABLED", False)
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
    monkeypatch.setattr(orchestrator_runner.settings, "ORCHESTRATOR_DAEMON_ENABLED", False)
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


# -----------------------------------------------------------------------
# Backend-aware default timeout resolution (2026-07 shutdown-budget fix)
# -----------------------------------------------------------------------
# stop_engine's PREVIOUS default (a flat 5.0s regardless of backend) was
# simultaneously too short for a daemon-backed handle (whose graceful
# teardown genuinely needs up to settings.DAEMON_SHUTDOWN_TIMEOUT_SECONDS)
# and pointless to lengthen for main.py --interval (which can only honor
# SIGTERM between cycles). These tests pin the new per-backend resolution;
# every test above already proves an EXPLICIT timeout still wins outright.


class _ModeHandle:
    """Stand-in RunHandle carrying just the `mode` field stop_engine reads."""

    def __init__(self, mode):
        self.mode = mode


def test_stop_engine_daemon_mode_waits_longer_than_the_daemon_shutdown_budget(monkeypatch):
    from settings import settings

    captured = {}

    def fake_stop_run(h, *, timeout=5.0):
        captured["timeout"] = timeout
        return True

    monkeypatch.setattr(orchestrator_runner, "stop_run", fake_stop_run)
    monkeypatch.setattr(settings, "DAEMON_SHUTDOWN_TIMEOUT_SECONDS", 25.0)

    stop_engine(_ModeHandle("daemon"))

    assert captured["timeout"] > 25.0


def test_stop_engine_scheduled_mode_keeps_the_unchanged_five_second_default(monkeypatch):
    captured = {}

    def fake_stop_run(h, *, timeout=5.0):
        captured["timeout"] = timeout
        return True

    monkeypatch.setattr(orchestrator_runner, "stop_run", fake_stop_run)

    stop_engine(_ModeHandle("scheduled"))

    assert captured["timeout"] == 5.0


def test_stop_engine_missing_mode_falls_back_to_five_seconds(monkeypatch):
    """A handle reconstructed across a Streamlit rerun (or this file's own
    _SentinelHandle, which carries no `mode` at all) must resolve to the
    same 5.0s default as an explicit "scheduled" handle -- getattr(...,
    "mode", None) must not raise or misresolve on a missing attribute."""
    captured = {}
    handle = _SentinelHandle()  # no .mode attribute at all

    def fake_stop_run(h, *, timeout=5.0):
        captured["timeout"] = timeout
        return True

    monkeypatch.setattr(orchestrator_runner, "stop_run", fake_stop_run)

    stop_engine(handle)

    assert captured["timeout"] == 5.0


def test_stop_engine_explicit_timeout_wins_for_both_modes(monkeypatch):
    """Regression guard: the backend-aware default resolution must never
    override a caller-supplied timeout, for EITHER backend."""
    captured = {}

    def fake_stop_run(h, *, timeout=5.0):
        captured["timeout"] = timeout
        return True

    monkeypatch.setattr(orchestrator_runner, "stop_run", fake_stop_run)

    stop_engine(_ModeHandle("daemon"), timeout=3.0)
    assert captured["timeout"] == 3.0

    stop_engine(_ModeHandle("scheduled"), timeout=3.0)
    assert captured["timeout"] == 3.0
