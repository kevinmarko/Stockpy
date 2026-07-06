"""
gui/engine_status.py
=====================
Sidebar-facing badge for the always-on background refresh loop.

The desktop shell (``app_shell.py``) drives ``main.py --interval N`` as a
long-lived subprocess tied to its own lifecycle. The existing GUI has no
visible indicator of whether that loop is alive or how fresh its data is.

Two liveness signals exist, written by different entry points:
  - ``output/heartbeat.txt`` — ``main_orchestrator.py`` ONLY (its async
    ``_heartbeat()`` task). ``main.py`` never writes this file, including in
    ``--interval``/``--agent`` mode -- so relying on it alone means the badge
    can never go green under the desktop shell's actual engine loop.
  - ``output/state_snapshot.json`` — rewritten by EVERY ``run_once()`` cycle
    in ``main.py`` (interval/agent mode included) AND by
    ``main_orchestrator.py``.

This module takes the freshest (minimum age) of both, so the badge is
correct regardless of which entry point is actually driving the refresh.

Dead-letter by design (CONSTRAINT #6): a badge helper must never crash the
sidebar render, so any failure degrades to a neutral "unavailable" badge
rather than propagating.
"""

from __future__ import annotations

from typing import Optional

from gui import orchestrator_runner


def _freshest_age() -> Optional[float]:
    """Return the smaller of the two liveness signals' ages, or None if
    neither file exists yet. Each lookup is independently guarded so one
    signal being unavailable/erroring never hides the other."""
    ages = []
    for lookup in (orchestrator_runner.heartbeat_age_seconds,
                   orchestrator_runner.state_snapshot_age_seconds):
        try:
            age = lookup()
        except Exception:
            age = None
        if age is not None:
            ages.append(age)
    return min(ages) if ages else None


def engine_status(fresh_threshold_seconds: float = 600.0) -> tuple[str, str]:
    """Return a ``(emoji, text)`` badge describing the background engine's liveness.

    Parameters
    ----------
    fresh_threshold_seconds:
        Age (seconds) at or below which the engine is considered "live"
        rather than "idle". Default 600s (10 minutes).

    Returns
    -------
    tuple[str, str]
        ``('⚪', 'Engine not started')`` — neither signal file exists yet.
        ``('🟢', 'Engine live · refreshed {age}s ago')`` — fresh signal.
        ``('🟠', 'Engine idle · last refresh {age}s ago')`` — stale signal.
        ``('⚪', 'Engine status unavailable')`` — unexpected error computing age.
    """
    try:
        age = _freshest_age()
    except Exception:
        return ("⚪", "Engine status unavailable")

    if age is None:
        return ("⚪", "Engine not started")

    if age <= fresh_threshold_seconds:
        return ("🟢", f"Engine live · refreshed {int(age)}s ago")

    return ("🟠", f"Engine idle · last refresh {int(age)}s ago")
