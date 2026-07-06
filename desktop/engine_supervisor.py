"""desktop/engine_supervisor.py
================================
Thin delegation wrapper over ``gui.orchestrator_runner``'s already-mature,
already-tested background-refresh-loop functions.

Why this module exists (WS3 of the always-on-desktop-app unification)
-----------------------------------------------------------------------
The always-on background refresh loop already lives in
``gui/orchestrator_runner.py`` — ``launch_scheduled_advisory`` spawns
``main.py --interval N`` as a subprocess, and ``stop_run`` performs a clean
SIGTERM->SIGKILL teardown. Those functions are mature and covered by existing
tests; this module does NOT reimplement or modify them.

A parallel workstream (WS4) is building the native desktop shell against a
small, stable, desktop-specific import surface rather than reaching into
``gui.orchestrator_runner`` internals directly. This module is that surface:
two functions, ``start_engine`` / ``stop_engine``, that call straight through
to the existing implementation and pass results back unchanged.
"""

from __future__ import annotations

from typing import Any


def start_engine(interval_seconds: int = 300, *, refresh_account: bool = False):
    """Start the always-on advisory refresh loop.

    Pure pass-through to
    :func:`gui.orchestrator_runner.launch_scheduled_advisory` in
    ``mode='interval'``.

    Parameters
    ----------
    interval_seconds:
        Refresh cadence in seconds (forwarded as-is; clamping/validation is
        ``launch_scheduled_advisory``'s responsibility).
    refresh_account:
        Forwarded as-is — forces a fresh Robinhood account fetch on this
        launch when True.

    Returns
    -------
    gui.orchestrator_runner.RunHandle
        The handle returned by ``launch_scheduled_advisory``, unmodified.
    """
    from gui.orchestrator_runner import launch_scheduled_advisory

    return launch_scheduled_advisory(
        mode="interval",
        interval_seconds=interval_seconds,
        refresh_account=refresh_account,
    )


def stop_engine(handle: Any, *, timeout: float = 5.0) -> bool:
    """Stop a previously-started advisory refresh loop.

    Pure pass-through to :func:`gui.orchestrator_runner.stop_run`.

    Parameters
    ----------
    handle:
        The ``RunHandle`` returned by :func:`start_engine`.
    timeout:
        Seconds to wait for graceful SIGTERM shutdown before escalating to
        SIGKILL (forwarded as-is).

    Returns
    -------
    bool
        ``True`` when the process is confirmed stopped (or was never
        running), ``False`` otherwise — passed through unchanged from
        ``stop_run``.
    """
    from gui.orchestrator_runner import stop_run

    return stop_run(handle, timeout=timeout)
