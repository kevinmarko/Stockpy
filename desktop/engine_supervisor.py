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
``stop_run`` (SIGTERM->SIGKILL) mechanics, but ``stop_engine`` (2026-07 fix)
DOES branch on which one it's stopping: the daemon has a genuinely bounded,
meaningful graceful teardown (draining two uvicorn servers, joining a timer
thread, polling for an in-flight run — see ``desktop/orchestrator_daemon.py``
and ``settings.DAEMON_SHUTDOWN_TIMEOUT_SECONDS``), while ``main.py
--interval``'s loop has none beyond falling out of its sleep loop between
cycles — giving both the SAME flat timeout was simultaneously too short for
the daemon (routinely SIGKILLing it mid-teardown) and pointless to lengthen
for the interval loop (a cycle takes minutes; nothing waits that long to
close a desktop window). Those functions are mature and covered by existing
tests; this module does NOT reimplement or modify them.

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


# Grace margin added on top of the daemon's own published shutdown budget
# (settings.DAEMON_SHUTDOWN_TIMEOUT_SECONDS) when resolving stop_engine's
# default timeout for a daemon-backed handle -- so the PARENT always waits
# strictly longer than the child's own bounded teardown needs, rather than
# racing it. A module constant, not a second setting: it is a fixed derived
# margin, not an independently meaningful operator knob (see settings.py's
# DAEMON_SHUTDOWN_TIMEOUT_SECONDS docstring for the full ladder).
_DAEMON_STOP_GRACE_SECONDS = 5.0

# Today's exact value for the non-daemon (main.py --interval) backend --
# unchanged by this fix. See this function's docstring for why a longer
# timeout would not help that backend: it can only honor SIGTERM between
# cycles, and a cycle takes minutes, so there is no useful value between "a
# few seconds" and "wait out the whole cycle".
_INTERVAL_STOP_TIMEOUT_SECONDS = 5.0


def stop_engine(handle: Any, *, timeout: float | None = None) -> bool:
    """Stop a previously-started advisory refresh loop.

    Delegates to :func:`gui.orchestrator_runner.stop_run`. When ``timeout``
    is omitted (the default), it is resolved PER BACKEND rather than a flat
    literal (2026-07 fix):

    * ``handle.mode == "daemon"`` — the persistent orchestrator daemon has a
      genuinely bounded graceful teardown (see
      ``settings.DAEMON_SHUTDOWN_TIMEOUT_SECONDS``), so this waits
      ``settings.DAEMON_SHUTDOWN_TIMEOUT_SECONDS + _DAEMON_STOP_GRACE_SECONDS``
      — strictly longer than the child's own budget, so the parent never
      races the child's teardown.
    * anything else (``"scheduled"``, or a handle missing ``mode`` entirely
      — e.g. one reconstructed across a Streamlit rerun) —
      ``_INTERVAL_STOP_TIMEOUT_SECONDS`` (5.0s, today's unchanged value).
      ``main.py --interval`` can only honor SIGTERM between cycles; a
      longer timeout here would not make shutdown more graceful, only
      slower to give up on a cycle that's actually mid-flight (see this
      module's docstring's "Why this module exists" section).

    An EXPLICITLY passed ``timeout`` always wins over this resolution,
    preserving the frozen call signature every existing caller already
    relies on.

    Parameters
    ----------
    handle:
        The ``RunHandle`` returned by :func:`start_engine`.
    timeout:
        Seconds to wait for graceful SIGTERM shutdown before escalating to
        SIGKILL. ``None`` (the default) resolves per-backend as above.

    Returns
    -------
    bool
        ``True`` when the process is confirmed stopped (or was never
        running), ``False`` otherwise — passed through unchanged from
        ``stop_run``.
    """
    from gui.orchestrator_runner import stop_run

    if timeout is None:
        if getattr(handle, "mode", None) == "daemon":
            from settings import settings

            timeout = settings.DAEMON_SHUTDOWN_TIMEOUT_SECONDS + _DAEMON_STOP_GRACE_SECONDS
        else:
            timeout = _INTERVAL_STOP_TIMEOUT_SECONDS

    return stop_run(handle, timeout=timeout)
