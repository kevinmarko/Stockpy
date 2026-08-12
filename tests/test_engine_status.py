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


def _patch_signals(monkeypatch, *, heartbeat=None, snapshot=None, paused_for_market_hours=False):
    """Patch both liveness signals. Each arg is either a fixed value/None or
    a zero-arg callable (for raising side effects).

    ``paused_for_market_hours`` pins ``_paused_for_market_hours()`` (real wall-clock
    time otherwise) so every idle/threshold test stays deterministic regardless of
    when it's actually run -- see ``TestMarketHoursPausedBadge`` below for tests
    of that branch itself."""
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
    monkeypatch.setattr(
        engine_status_module, "_paused_for_market_hours", lambda: paused_for_market_hours
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


class TestMarketHoursPausedBadge:
    """Regression coverage for the ORCHESTRATOR_EXTENDED_HOURS_ONLY gate (see
    main.py/desktop/daemon_runtime.py): a stale signal explained by the
    market-hours gate legitimately skipping automatic cycles must render as
    an honest '🌙 paused' badge, not a falsely-alarming '🟠 idle' one."""

    def test_stale_and_paused_returns_moon_badge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_signals(monkeypatch, heartbeat=None, snapshot=6000, paused_for_market_hours=True)
        badge, text = engine_status()
        assert badge == "🌙"
        assert text == "Automatic runs paused (outside market hours) · last refresh 6000s ago"

    def test_stale_and_not_paused_still_returns_idle_badge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_signals(monkeypatch, heartbeat=None, snapshot=6000, paused_for_market_hours=False)
        badge, text = engine_status()
        assert badge == "🟠"
        assert text == "Engine idle · last refresh 6000s ago"

    def test_fresh_signal_ignores_paused_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The paused check must only apply once staleness has already been
        established -- a fresh signal always wins regardless of market hours."""
        _patch_signals(monkeypatch, heartbeat=None, snapshot=12, paused_for_market_hours=True)
        badge, text = engine_status()
        assert badge == "🟢"
        assert text == "Engine live · refreshed 12s ago"

    def test_paused_for_market_hours_true_when_gate_on_and_outside_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("settings.settings.ORCHESTRATOR_EXTENDED_HOURS_ONLY", True)
        monkeypatch.setattr("engine.advisory_agent.is_extended_hours", lambda now: False)
        assert engine_status_module._paused_for_market_hours() is True

    def test_paused_for_market_hours_false_when_gate_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("settings.settings.ORCHESTRATOR_EXTENDED_HOURS_ONLY", False)
        monkeypatch.setattr("engine.advisory_agent.is_extended_hours", lambda now: False)
        assert engine_status_module._paused_for_market_hours() is False

    def test_paused_for_market_hours_false_when_inside_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("settings.settings.ORCHESTRATOR_EXTENDED_HOURS_ONLY", True)
        monkeypatch.setattr("engine.advisory_agent.is_extended_hours", lambda now: True)
        assert engine_status_module._paused_for_market_hours() is False

    def test_paused_helper_never_raises_on_a_broken_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Dead-letter by design (CONSTRAINT #6): a broken market-hours check must
        degrade to False (ordinary '🟠 idle' badge), never raise or mask a real
        idle badge with a falsely-reassuring 'paused' one."""
        def _raise(now):
            raise RuntimeError("boom")

        monkeypatch.setattr("settings.settings.ORCHESTRATOR_EXTENDED_HOURS_ONLY", True)
        monkeypatch.setattr("engine.advisory_agent.is_extended_hours", _raise)
        assert engine_status_module._paused_for_market_hours() is False
