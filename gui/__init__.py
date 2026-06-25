"""
gui/ — InvestYo Command Center
==============================
Local-first, on-demand Streamlit operational suite for the InvestYo / Stockpy
quant platform.  Launched via::

    streamlit run gui/app.py

or by double-clicking ``launch_gui.command`` on macOS.

Design principles (mirroring the existing ``observability/dashboard.py``):

*   **Read-only / file-backed where possible.**  The GUI never calls async
    broker code directly — it launches ``main_orchestrator.py`` as a subprocess
    and consumes the file-backed state the orchestrator writes
    (``output/state_snapshot.json``, ``output/heartbeat.txt``,
    ``output/KILL_SWITCH``).  This avoids the asyncio/Streamlit event-loop
    conflict and keeps the GUI usable even when the broker API is unreachable.
*   **Secrets stay in ``.env``.**  ``gui/env_io.py`` edits only a hard-coded
    allowlist of NON-secret tunables; secret keys are shown masked and are never
    written or echoed (CONSTRAINT #3).
*   **Source-of-truth separation.**  Robinhood is surfaced for account state
    only; market-data providers for prices/indicators — never crossed
    (CONSTRAINT #4).
*   **Dead-letter resilience.**  Every panel is wrapped so one failing tab never
    aborts the whole app (CONSTRAINT #6).
"""

from __future__ import annotations

__all__ = ["env_io", "orchestrator_runner", "panels"]
