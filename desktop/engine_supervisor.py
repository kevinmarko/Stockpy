"""desktop/engine_supervisor.py
================================
Thin delegation wrapper over ``gui.orchestrator_runner``'s already-mature,
already-tested background-refresh-loop functions.

Why this module exists (WS3 of the always-on-desktop-app unification)
-----------------------------------------------------------------------
The always-on background refresh loop lives in ``gui/orchestrator_runner.py``
— either ``launch_scheduled_advisory`` (spawns ``main.py --interval N``) or,
behind the ``settings.ORCHESTRATOR_DAEMON_ENABLED`` cutover flag,
``launch_daemon_engine`` (spawns ``python -m desktop.orchestrator_daemon
--interval N`` — the persistent orchestrator daemon that keeps pipeline
engines warm across cycles instead of re-constructing them every run). Both
are ordinary supervised subprocesses using the SAME ``RunHandle``/
``stop_run`` (SIGTERM->SIGKILL) mechanics — the daemon's warm-engine benefit
is entirely internal to that process, so ``stop_engine`` needs no branching
at all. Those functions are mature and covered by existing tests; this
module does NOT reimplement or modify them.

A parallel workstream (WS4) built the native desktop shell against a small,
stable, desktop-specific import surface rather than reaching into
``gui.orchestrator_runner`` internals directly. This module is that surface:
two functions, ``start_engine`` / ``stop_engine``, whose SIGNATURES are
frozen (``app_shell.py`` depends on them unchanged) but whose BODIES pick the
underlying launcher based on the cutover flag.
"""

from __future__ import annotations

from typing import Any


def start_engine(interval_seconds: int = 300, *, refresh_account: bool = False):
    """Start the always-on advisory refresh loop.

    Delegates to :func:`gui.orchestrator_runner.launch_daemon_engine` (the
    persistent orchestrator daemon) when ``settings.ORCHESTRATOR_DAEMON_ENABLED``
    is True, else to :func:`gui.orchestrator_runner.launch_scheduled_advisory`
    in ``mode='interval'`` (today's default, unchanged behavior). Exactly ONE
    of the two is ever spawned — never both — so there is no double-loop
    risk during the flag's rollout.

    Parameters
    ----------
    interval_seconds:
        Refresh cadence in seconds (forwarded as-is; clamping/validation is
        the chosen launcher's responsibility).
    refresh_account:
        Forwarded as-is — forces a fresh Robinhood account fetch on this
        launch when True. Silently ignored by the daemon path (see
        :func:`gui.orchestrator_runner.launch_daemon_engine`'s docstring —
        the daemon entrypoint has no ``--refresh-account`` equivalent).

    Returns
    -------
    gui.orchestrator_runner.RunHandle
        The handle returned by whichever launcher was used, unmodified.
    """
    from settings import settings

    if settings.ORCHESTRATOR_DAEMON_ENABLED:
        from gui.orchestrator_runner import launch_daemon_engine

        return launch_daemon_engine(
            interval_seconds=interval_seconds,
            refresh_account=refresh_account,
        )

    from gui.orchestrator_runner import launch_scheduled_advisory

    return launch_scheduled_advisory(
        mode="interval",
        interval_seconds=interval_seconds,
        refresh_account=refresh_account,
    )


def stop_engine(handle: Any, *, timeout: float = 5.0) -> bool:
    """Stop a previously-started advisory refresh loop.

    Pure pass-through to :func:`gui.orchestrator_runner.stop_run`. No
    branching needed here: both ``launch_scheduled_advisory`` and
    ``launch_daemon_engine`` produce an ordinary subprocess-backed
    ``RunHandle`` (``backend="subprocess"``), so the SAME SIGTERM->SIGKILL
    teardown mechanics apply regardless of which one started this handle.

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
