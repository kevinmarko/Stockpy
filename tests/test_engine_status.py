"""
tests/test_engine_status.py
============================
Unit tests for ``gui/engine_status.py``'s sidebar liveness badge.

All tests monkeypatch both ``gui.orchestrator_runner.heartbeat_age_seconds``
(main_orchestrator.py-only signal) and
``gui.orchestrator_runner.state_snapshot_age_seconds`` (written by every
main.py run_once() cycle AND main_orchestrator.py) so no filesystem or
process state is touched -- the badge logic is a pure function of whatever
those two lookups return, taking whichever signal is freshest.
"""

from __future__ import annotations

import pytest

from gui import engine_status as engine_status_module
from gui.engine_status import engine_status


def _patch_signals(monkeypatch, *, heartbeat=None, snapshot=None):
    """Patch both liveness signals. Each arg is either a fixed value/None or
    a zero-arg callable (for raising side effects)."""
    def _wrap(value):
        if callable(value):
            return value
        return lambda: value

    monkeypatch.setattr(
        engine_status_module.orchestrator_runner, "heartbeat_age_seconds", _wrap(heartbeat)
    )
    monkeypatch.setattr(
        engine_status_module.orchestrator_runner, "state_snapshot_age_seconds", _wrap(snapshot)
    )


def test_no_signals_returns_not_started(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_signals(monkeypatch, heartbeat=None, snapshot=None)
    badge, text = engine_status()
    assert badge == "⚪"
    assert text == "Engine not started"


def test_fresh_heartbeat_only_returns_live_badge(monkeypatch: pytest.MonkeyPatch) -> None:
    """main_orchestrator.py mode: only heartbeat.txt is fresh."""
    _patch_signals(monkeypatch, heartbeat=12, snapshot=None)
    badge, text = engine_status()
    assert badge == "🟢"
    assert text == "Engine live · refreshed 12s ago"


def test_fresh_snapshot_only_returns_live_badge(monkeypatch: pytest.MonkeyPatch) -> None:
    """main.py --interval mode: only state_snapshot.json is fresh (the bug
    this test guards against: the badge must NOT stay stuck on 'not started'
    or 'idle' just because heartbeat.txt -- an orchestrator-only file --
    was never written).
    """
    _patch_signals(monkeypatch, heartbeat=None, snapshot=12)
    badge, text = engine_status()
    assert badge == "🟢"
    assert text == "Engine live · refreshed 12s ago"


def test_freshest_signal_wins_when_both_present(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_signals(monkeypatch, heartbeat=5000, snapshot=12)
    badge, text = engine_status()
    assert badge == "🟢"
    assert text == "Engine live · refreshed 12s ago"

    _patch_signals(monkeypatch, heartbeat=12, snapshot=5000)
    badge, text = engine_status()
    assert badge == "🟢"
    assert text == "Engine live · refreshed 12s ago"


def test_both_stale_returns_idle_badge(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_signals(monkeypatch, heartbeat=5000, snapshot=6000)
    badge, text = engine_status()
    assert badge == "🟠"
    assert text == "Engine idle · last refresh 5000s ago"


def test_age_exactly_at_threshold_is_still_live(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_signals(monkeypatch, heartbeat=None, snapshot=600.0)
    badge, text = engine_status(fresh_threshold_seconds=600.0)
    assert badge == "🟢"
    assert "600s" in text


def test_age_just_over_threshold_is_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_signals(monkeypatch, heartbeat=None, snapshot=600.1)
    badge, text = engine_status(fresh_threshold_seconds=600.0)
    assert badge == "🟠"


def test_one_signal_raising_still_uses_the_other(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single signal erroring must degrade to just ignoring that signal,
    not the whole badge -- each lookup is independently guarded."""
    def _raise():
        raise RuntimeError("boom")

    _patch_signals(monkeypatch, heartbeat=_raise, snapshot=30)
    badge, text = engine_status()
    assert badge == "🟢"
    assert text == "Engine live · refreshed 30s ago"


def test_both_signals_raising_degrades_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise():
        raise RuntimeError("boom")

    _patch_signals(monkeypatch, heartbeat=_raise, snapshot=_raise)
    badge, text = engine_status()
    assert badge == "⚪"
    assert text == "Engine not started"


def test_custom_threshold_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_signals(monkeypatch, heartbeat=None, snapshot=100)
    badge, _ = engine_status(fresh_threshold_seconds=50.0)
    assert badge == "🟠"

    badge, _ = engine_status(fresh_threshold_seconds=150.0)
    assert badge == "🟢"
