"""
tests/test_engine_status.py
============================
Unit tests for ``gui/engine_status.py``'s sidebar liveness badge.

All tests monkeypatch ``gui.orchestrator_runner.heartbeat_age_seconds`` so no
filesystem or process state is touched — the badge logic is pure function of
whatever that lookup returns.
"""

from __future__ import annotations

import pytest

from gui import engine_status as engine_status_module
from gui.engine_status import engine_status


def test_no_heartbeat_file_returns_not_started(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine_status_module.orchestrator_runner, "heartbeat_age_seconds", lambda: None
    )
    badge, text = engine_status()
    assert badge == "⚪"
    assert text == "Engine not started"


def test_fresh_heartbeat_returns_live_badge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine_status_module.orchestrator_runner, "heartbeat_age_seconds", lambda: 12
    )
    badge, text = engine_status()
    assert badge == "🟢"
    assert text == "Engine live · refreshed 12s ago"


def test_stale_heartbeat_returns_idle_badge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine_status_module.orchestrator_runner, "heartbeat_age_seconds", lambda: 5000
    )
    badge, text = engine_status()
    assert badge == "🟠"
    assert text == "Engine idle · last refresh 5000s ago"


def test_age_exactly_at_threshold_is_still_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine_status_module.orchestrator_runner, "heartbeat_age_seconds", lambda: 600.0
    )
    badge, text = engine_status(fresh_threshold_seconds=600.0)
    assert badge == "🟢"
    assert "600s" in text


def test_age_just_over_threshold_is_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine_status_module.orchestrator_runner, "heartbeat_age_seconds", lambda: 600.1
    )
    badge, text = engine_status(fresh_threshold_seconds=600.0)
    assert badge == "🟠"


def test_heartbeat_lookup_raising_degrades_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise() -> float:
        raise RuntimeError("boom")

    monkeypatch.setattr(
        engine_status_module.orchestrator_runner, "heartbeat_age_seconds", _raise
    )
    badge, text = engine_status()
    assert badge == "⚪"
    assert text == "Engine status unavailable"


def test_custom_threshold_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        engine_status_module.orchestrator_runner, "heartbeat_age_seconds", lambda: 100
    )
    badge, _ = engine_status(fresh_threshold_seconds=50.0)
    assert badge == "🟠"

    badge, _ = engine_status(fresh_threshold_seconds=150.0)
    assert badge == "🟢"
