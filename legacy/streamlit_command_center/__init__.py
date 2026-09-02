"""
legacy/streamlit_command_center/ — InvestYo Command Center (FROZEN)
=====================================================================
**Frozen — no new development.** Kept runnable for existing local setups
only; a severe bug blocking use of the platform entirely may still be
patched. See CLAUDE.md's "Frontend strategy" section for the full policy.
The Pilots PWA (``webapp/``) is the platform's one actively-developed
frontend — new features go there, not here.

Local-first, on-demand Streamlit operational suite for the InvestYo / Stockpy
quant platform. Launched via::

    streamlit run legacy/streamlit_command_center/app.py

or by double-clicking ``launch_gui.command`` on macOS (unchanged — the
launcher's own path stays at the repo root; only its internal reference to
this module's new location changed).

Design principles (the platform's single observability surface — the former
standalone ``streamlit run observability/dashboard.py`` app has been retired;
its panels now live in the Observability tab,
``legacy/streamlit_command_center/panels/observability.py``):

*   **Read-only / file-backed where possible.**  The GUI never calls async
    broker code directly — it launches ``main_orchestrator.py`` as a subprocess
    and consumes the file-backed state the orchestrator writes
    (``output/state_snapshot.json``, ``output/heartbeat.txt``,
    ``output/KILL_SWITCH``).  This avoids the asyncio/Streamlit event-loop
    conflict and keeps the GUI usable even when the broker API is unreachable.
*   **Secrets stay in ``.env``.**  ``shared/env_io.py`` edits only a hard-coded
    allowlist of NON-secret tunables; secret keys are shown masked and are never
    written or echoed (CONSTRAINT #3).
*   **Source-of-truth separation.**  Robinhood is surfaced for account state
    only; market-data providers for prices/indicators — never crossed
    (CONSTRAINT #4).
*   **Dead-letter resilience.**  Every panel is wrapped so one failing tab never
    aborts the whole app (CONSTRAINT #6).

Note: the live shared logic this package's panels depend on (``env_io``,
``orchestrator_runner``, ``daemon_client``, and 28 other modules) no longer
lives here — it moved to ``shared/`` because production code entirely
outside this frozen UI (``api/pilots_api.py``, ``api/data_api.py``,
``api/_jobs.py``, ``pilots/*``, ``main.py``, and others) depends on it
directly. ``shared/`` is NOT part of the frozen surface.
"""

from __future__ import annotations

__all__ = ["panels"]
