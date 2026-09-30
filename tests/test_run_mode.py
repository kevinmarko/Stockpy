"""
tests/test_run_mode.py
========================
Unit tests for :mod:`shared.run_mode`.

All tests are Streamlit-free and fully offline.

Verified invariants
-------------------
*   ``shared.run_mode`` is importable.
*   ``RunModeState`` is a frozen dataclass.
*   ``read_active_run_mode({})`` returns ``process="idle"``.
*   Active run handle → ``process="running"``.
*   Finished run handle → ``process="finished"``.
*   ``(DRY_RUN=True, PAPER_TRADING=True)`` → ``mode="Simulation"``.
*   ``(DRY_RUN=False, PAPER_TRADING=True)`` → ``mode="Paper"``.
*   ``(DRY_RUN=False, PAPER_TRADING=False)`` → ``mode="Live"``.
*   ``(DRY_RUN=True, PAPER_TRADING=False)`` → ``mode="Simulation"``
    (DRY_RUN wins over PAPER_TRADING=False).
*   ``icon`` and ``color`` fields are non-empty strings.
*   ``run_mode_label`` is non-empty and contains the mode.
*   ``gui/app.py`` imports / references ``run_mode``.
"""

from __future__ import annotations

from unittest import mock

import pytest


# ===========================================================================
# Import
# ===========================================================================

def test_module_importable():
    from shared import run_mode  # noqa: F401


def test_run_mode_state_importable():
    from shared.run_mode import RunModeState  # noqa: F401


def test_read_active_run_mode_importable():
    from shared.run_mode import read_active_run_mode  # noqa: F401


# ===========================================================================
# RunModeState frozen
# ===========================================================================

def test_run_mode_state_frozen():
    from shared.run_mode import RunModeState

    state = RunModeState(
        mode="Simulation",
        process="idle",
        dry_run=True,
        paper_trading=True,
        icon="⚪",
        color="blue",
        pid=None,
        run_mode_label="test",
    )
    with pytest.raises((AttributeError, TypeError)):
        state.mode = "Live"  # type: ignore[misc]


# ===========================================================================
# process derivation
# ===========================================================================

def _mock_settings(dry_run: bool, paper_trading: bool):
    s = mock.MagicMock()
    s.DRY_RUN = dry_run
    s.PAPER_TRADING = paper_trading
    return s


def test_empty_session_state_is_idle():
    from shared.run_mode import read_active_run_mode

    with mock.patch("shared.run_mode._s", _mock_settings(True, True), create=True), \
         mock.patch("settings.settings", _mock_settings(True, True)):
        state = read_active_run_mode(session_state={})

    assert state.process == "idle"
    assert state.pid is None


def test_running_handle_process():
    from shared.run_mode import read_active_run_mode

    handle = mock.MagicMock()
    handle.is_running.return_value = True
    handle.pid = 12345
    handle.mode = "orchestrator"

    with mock.patch("settings.settings", _mock_settings(False, True)):
        state = read_active_run_mode(session_state={"run_handle": handle})

    assert state.process == "running"
    assert state.pid == 12345


def test_finished_handle_process():
    from shared.run_mode import read_active_run_mode

    handle = mock.MagicMock()
    handle.is_running.return_value = False
    handle.pid = 99
    handle.mode = "advisory"

    with mock.patch("settings.settings", _mock_settings(False, True)):
        state = read_active_run_mode(session_state={"run_handle": handle})

    assert state.process == "finished"


# ===========================================================================
# mode derivation — truth table
# ===========================================================================

@pytest.mark.parametrize("dry_run,paper_trading,expected_mode", [
    (True,  True,  "Simulation"),
    (True,  False, "Simulation"),  # DRY_RUN wins
    (False, True,  "Paper"),
    (False, False, "Live"),
])
def test_mode_derivation(dry_run, paper_trading, expected_mode):
    from shared.run_mode import read_active_run_mode

    with mock.patch("settings.settings", _mock_settings(dry_run, paper_trading)):
        state = read_active_run_mode(session_state={})

    assert state.mode == expected_mode, (
        f"DRY_RUN={dry_run}, PAPER_TRADING={paper_trading} → expected {expected_mode}, "
        f"got {state.mode}"
    )
    assert state.dry_run is dry_run
    assert state.paper_trading is paper_trading


# ===========================================================================
# icon / color / label
# ===========================================================================

def test_icon_non_empty():
    from shared.run_mode import read_active_run_mode

    with mock.patch("settings.settings", _mock_settings(True, True)):
        state = read_active_run_mode({})

    assert isinstance(state.icon, str) and len(state.icon) > 0


def test_color_non_empty():
    from shared.run_mode import read_active_run_mode

    with mock.patch("settings.settings", _mock_settings(False, True)):
        state = read_active_run_mode({})

    assert isinstance(state.color, str) and len(state.color) > 0


def test_label_contains_mode():
    from shared.run_mode import read_active_run_mode

    with mock.patch("settings.settings", _mock_settings(False, False)):
        state = read_active_run_mode({})

    assert state.mode in state.run_mode_label
    assert len(state.run_mode_label) > 5


# ===========================================================================
# gui/app.py references run_mode
# ===========================================================================
