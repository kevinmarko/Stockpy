"""
gui/orchestrator_runner.py
==========================
Subprocess launcher and stage tracker for ``main_orchestrator.py``, used by the
Command Center's **Launcher & Orchestration** tab.

Why a subprocess (not in-process asyncio)
------------------------------------------
``main_orchestrator.main()`` is ``async`` and drives the Alpaca broker adapters,
which run their own event loop.  Streamlit renders synchronously and reruns the
whole script on every interaction.  Calling ``asyncio.run(main())`` inside a
Streamlit callback would block the UI for the entire pipeline duration and risk
nested-event-loop errors.  Launching the orchestrator as a detached subprocess
keeps the GUI responsive, isolates failures, and lets us stream the child's
stdout to a log file the UI tails — exactly the on-demand, file-backed model the
existing observability dashboard already relies on (CONSTRAINT #5).

Stage tracking
--------------
The orchestrator does not emit a machine-readable progress stream, so we derive
coarse stage status from observable side effects:

*   **log markers** — we scan the child's stdout log for the human-readable
    stage banners the orchestrator already logs (macro/options/processing/
    forecasting/strategy/execution).
*   **heartbeat freshness** — ``output/heartbeat.txt`` is rewritten every 60 s
    while the orchestrator is alive (``main_orchestrator._heartbeat``).
*   **snapshot mtime** — ``output/state_snapshot.json`` is rewritten at the end
    of a successful run.

This is intentionally best-effort: if a marker changes upstream the worst case
is a stage shows "pending" slightly longer; it never blocks or crashes the GUI.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from settings import settings

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_LOG_PATH = settings.OUTPUT_DIR / "gui_run.log"

# Ordered pipeline stages surfaced as indicators in the UI. Each tuple is
# (display_label, list of case-insensitive substrings that, once seen in the
# child log, mark the stage as reached). Markers are matched against the banners
# main_orchestrator already logs; multiple synonyms make matching robust to
# minor wording drift.
STAGES: List[tuple[str, List[str]]] = [
    ("Data Acquisition", ["async data fetch", "fetching", "data acquisition", "macro engine"]),
    ("Processing", ["processing engine", "compile_dashboard", "technical metrics"]),
    ("Forecasting", ["forecasting engine", "forecast", "arima", "monte carlo"]),
    ("Execution", ["broker execution", "_execute_broker_orders", "order manager", "state snapshot"]),
]


@dataclass
class RunHandle:
    """Mutable handle for a launched orchestrator subprocess.

    Stored in Streamlit ``session_state`` so the run survives reruns and the UI
    can poll status without re-launching.
    """

    pid: int
    started_at: float
    dry_run: bool
    refresh_account: bool
    log_path: Path = RUN_LOG_PATH
    _popen: Optional[subprocess.Popen] = field(default=None, repr=False)

    def is_running(self) -> bool:
        """True while the child process has not yet exited."""
        if self._popen is None:
            # Reconstructed across a rerun without the Popen object: fall back to
            # an OS-level liveness probe on the PID.
            return _pid_alive(self.pid)
        return self._popen.poll() is None

    def returncode(self) -> Optional[int]:
        """Exit code if finished, else ``None``."""
        if self._popen is None:
            return None
        return self._popen.poll()


def _pid_alive(pid: int) -> bool:
    """Return True if a process with ``pid`` is currently alive (POSIX)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user — still "alive" for our purposes.
        return True
    except Exception:
        return False
    return True


def launch_orchestrator(dry_run: bool = False, refresh_account: bool = False) -> RunHandle:
    """Spawn ``main_orchestrator.py`` as a non-blocking subprocess.

    Parameters
    ----------
    dry_run:
        When True, pass ``--dry-run`` so the orchestrator logs intended orders
        but never submits them (mirrors ``settings.DRY_RUN`` / the CLI flag).
    refresh_account:
        When True, pass ``--refresh-account`` to force a fresh Robinhood
        account fetch on this launch (the flag is ignored gracefully by the
        orchestrator if unsupported).

    Returns
    -------
    RunHandle
        Handle for polling status and reading the streamed log.

    Notes
    -----
    The child inherits the current environment (so ``.env`` is loaded by the
    orchestrator itself via ``load_dotenv``).  stdout+stderr are redirected to
    :data:`RUN_LOG_PATH`, truncated at launch so the UI tails only the current
    run.  We use ``sys.executable`` to guarantee the ``.venv`` interpreter is
    used rather than a stray system Python.
    """
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd: List[str] = [sys.executable, "main_orchestrator.py"]
    if dry_run:
        cmd.append("--dry-run")
    if refresh_account:
        cmd.append("--refresh-account")

    # Truncate the log for a clean per-run tail.
    log_file = open(RUN_LOG_PATH, "w", encoding="utf-8")  # noqa: SIM115 - kept open for the child
    log_file.write(
        f"# InvestYo orchestrator launch @ {time.strftime('%Y-%m-%d %H:%M:%S')} "
        f"(dry_run={dry_run}, refresh_account={refresh_account})\n"
    )
    log_file.flush()

    popen = subprocess.Popen(
        cmd,
        cwd=str(_REPO_ROOT),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
        bufsize=1,
        text=True,
    )
    logger.info("Launched orchestrator pid=%s cmd=%s", popen.pid, " ".join(cmd))

    return RunHandle(
        pid=popen.pid,
        started_at=time.time(),
        dry_run=dry_run,
        refresh_account=refresh_account,
        _popen=popen,
    )


def read_log_tail(max_lines: int = 200) -> str:
    """Return the last ``max_lines`` lines of the current run log (or a hint)."""
    if not RUN_LOG_PATH.exists():
        return "(no run log yet — launch the orchestrator to populate it)"
    try:
        lines = RUN_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception as exc:  # pragma: no cover - IO edge
        return f"(failed to read run log: {exc})"


def heartbeat_age_seconds() -> Optional[float]:
    """Seconds since ``output/heartbeat.txt`` was last written, or None if absent.

    A fresh heartbeat (< ~90 s) means the orchestrator is alive; a stale or
    missing one means it is idle or crashed.
    """
    hb = settings.OUTPUT_DIR / "heartbeat.txt"
    if not hb.exists():
        return None
    try:
        return max(0.0, time.time() - hb.stat().st_mtime)
    except Exception:
        return None


def compute_stage_status(handle: Optional[RunHandle]) -> Dict[str, str]:
    """Derive per-stage status from the run log + heartbeat + snapshot mtime.

    Returns a mapping ``{stage_label: "done"|"active"|"pending"|"idle"}``.

    *   ``idle``    — no run handle / no log (nothing launched this session).
    *   ``pending`` — launched but this stage's markers not yet seen.
    *   ``active``  — this is the latest stage whose markers have appeared.
    *   ``done``    — a later stage's markers have appeared, or the run finished
                      and a snapshot was written.
    """
    labels = [label for label, _ in STAGES]
    if handle is None or not RUN_LOG_PATH.exists():
        return {label: "idle" for label in labels}

    log_text = read_log_tail(max_lines=2000).lower()
    reached: List[bool] = []
    for _label, markers in STAGES:
        reached.append(any(m in log_text for m in markers))

    # Index of the furthest stage reached (-1 if none).
    last_reached = max((i for i, r in enumerate(reached) if r), default=-1)

    finished = (handle is not None) and (not handle.is_running())
    snapshot = settings.OUTPUT_DIR / "state_snapshot.json"
    snapshot_fresh = snapshot.exists() and (snapshot.stat().st_mtime >= handle.started_at)

    status: Dict[str, str] = {}
    for i, label in enumerate(labels):
        if finished and snapshot_fresh:
            status[label] = "done"
        elif i < last_reached:
            status[label] = "done"
        elif i == last_reached:
            status[label] = "active"
        else:
            status[label] = "pending"
    return status
