"""
gui/run_mode.py
===============
Persistent execution-mode header helpers for :mod:`gui.app`.

The active execution mode is the most safety-critical piece of state an
operator can accidentally ignore.  This module derives mode from the same
``DRY_RUN`` / ``ALPACA_PAPER`` env vars that :mod:`gui.strategy_registry`
writes — single source of truth, no new state.

Public API
----------
``RunModeState``       — frozen dataclass surfaced in the header banner.
``read_active_run_mode`` — derives ``RunModeState`` from session_state +
                           settings + heartbeat age.

CONSTRAINT #5 (on-demand)
--------------------------
No scheduler; mode is re-derived on every Streamlit render pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Map (dry_run, alpaca_paper) → human label.
_MODE_LABELS: Dict[tuple[bool, bool], str] = {
    (True,  True):  "Simulation",
    (True,  False): "Simulation",
    (False, True):  "Paper",
    (False, False): "Live",
}

_MODE_ICONS: Dict[str, str] = {
    "Simulation": "⚪",
    "Paper":      "🟡",
    "Live":       "🔴",
    "idle":       "⏸️",
}

_MODE_COLORS: Dict[str, str] = {
    "Simulation": "blue",
    "Paper":      "orange",
    "Live":       "red",
    "idle":       "gray",
}


@dataclass(frozen=True)
class RunModeState:
    """Snapshot of the active execution environment.

    Attributes
    ----------
    mode        : ``"Simulation"`` / ``"Paper"`` / ``"Live"``.
    process     : ``"idle"`` / ``"running"`` / ``"finished"``.
    dry_run     : Whether ``DRY_RUN=true``.
    alpaca_paper: Whether ``ALPACA_PAPER=true``.
    icon        : Emoji prefix for the banner.
    color       : Streamlit color class (``"blue"``, ``"orange"``, ``"red"``, ``"gray"``).
    pid         : Active subprocess PID (``None`` if idle).
    run_mode_label : Full operator-readable banner string.
    """

    mode: str
    process: str
    dry_run: bool
    alpaca_paper: bool
    icon: str
    color: str
    pid: Optional[int]
    run_mode_label: str


def read_active_run_mode(
    session_state: Optional[Dict[str, Any]] = None,
) -> RunModeState:
    """Derive the current :class:`RunModeState` without touching Streamlit.

    This is kept Streamlit-free so it can be called from tests and other
    modules that need mode information without rendering a widget.

    Parameters
    ----------
    session_state:
        A mapping with an optional ``"run_handle"`` key holding a
        :class:`gui.orchestrator_runner.RunHandle` (or any object with
        ``is_running() -> bool``, ``pid: int``, ``mode: str``).
        When ``None`` (or an empty dict), the function uses a minimal
        defaults-only path — appropriate for headless callers.

    Returns
    -------
    RunModeState
        Derived state.  ``process="idle"`` is the neutral default when no
        run handle is present.
    """
    ss = session_state if session_state is not None else {}

    # Derive dry_run / alpaca_paper from settings (never os.environ directly —
    # settings has already coerced the type).
    try:
        from settings import settings as _s
        dry_run = bool(_s.DRY_RUN)
        alpaca_paper = bool(_s.ALPACA_PAPER)
    except Exception:
        dry_run = False
        alpaca_paper = True  # safe default — never default to "Live"

    mode = _MODE_LABELS.get((dry_run, alpaca_paper), "Simulation")
    icon = _MODE_ICONS.get(mode, "⚪")
    color = _MODE_COLORS.get(mode, "gray")

    # Derive process state from the run handle in session_state.
    handle = ss.get("run_handle")
    if handle is None:
        process = "idle"
        pid: Optional[int] = None
    elif hasattr(handle, "is_running") and handle.is_running():
        process = "running"
        pid = getattr(handle, "pid", None)
    else:
        process = "finished"
        pid = getattr(handle, "pid", None)

    # Compose the banner label.
    mode_emoji = icon
    if process == "running":
        handle_mode = getattr(handle, "mode", "?").title()
        label = f"{mode_emoji} {mode} mode | 🟢 {handle_mode} running (PID {pid})"
    elif process == "finished":
        handle_mode = getattr(handle, "mode", "?").title()
        label = f"{mode_emoji} {mode} mode | ⏹️ Last run: {handle_mode} (PID {pid})"
    else:
        label = f"{mode_emoji} {mode} mode | ⏸️ idle"

    return RunModeState(
        mode=mode,
        process=process,
        dry_run=dry_run,
        alpaca_paper=alpaca_paper,
        icon=icon,
        color=color,
        pid=pid,
        run_mode_label=label,
    )
