"""
gui/engine_status.py
=====================
Sidebar-facing badge for the always-on background refresh loop.

A parallel workstream drives ``main.py --interval N`` as a long-lived
subprocess tied to the desktop shell's lifecycle. The existing GUI has no
visible indicator of whether that loop is alive or how fresh its data is.
``gui.orchestrator_runner.heartbeat_age_seconds()`` already exposes exactly
what's needed (seconds since ``output/heartbeat.txt`` was last written, or
``None`` if the file doesn't exist yet) — this module turns that single
number into a compact ``(emoji, text)`` badge for ``st.sidebar.caption``.

Dead-letter by design (CONSTRAINT #6): a badge helper must never crash the
sidebar render, so any failure degrades to a neutral "unavailable" badge
rather than propagating.
"""

from __future__ import annotations

from gui import orchestrator_runner


def engine_status(fresh_threshold_seconds: float = 600.0) -> tuple[str, str]:
    """Return a ``(emoji, text)`` badge describing the background engine's liveness.

    Parameters
    ----------
    fresh_threshold_seconds:
        Heartbeat age (seconds) at or below which the engine is considered
        "live" rather than "idle". Default 600s (10 minutes).

    Returns
    -------
    tuple[str, str]
        ``('⚪', 'Engine not started')`` — no heartbeat file yet.
        ``('🟢', 'Engine live · refreshed {age}s ago')`` — fresh heartbeat.
        ``('🟠', 'Engine idle · last refresh {age}s ago')`` — stale heartbeat.
        ``('⚪', 'Engine status unavailable')`` — any error reading the heartbeat.
    """
    try:
        age = orchestrator_runner.heartbeat_age_seconds()
    except Exception:
        return ("⚪", "Engine status unavailable")

    if age is None:
        return ("⚪", "Engine not started")

    if age <= fresh_threshold_seconds:
        return ("🟢", f"Engine live · refreshed {int(age)}s ago")

    return ("🟠", f"Engine idle · last refresh {int(age)}s ago")
