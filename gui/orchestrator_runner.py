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

import enum
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from settings import settings


class StageStatus(str, enum.Enum):
    """Pipeline-stage status values.

    Inherits from ``str`` so legacy callers that compare with plain string
    literals (e.g. ``if status == "active"``) continue to work without
    modification.

    Values
    ------
    SUCCESS : Finished cleanly — a later stage's markers appeared or run exited 0.
    ACTIVE  : This is the furthest stage whose log markers have been seen.
    ERROR   : Run exited non-zero and this stage was the last active one.
    PENDING : Launched but this stage's markers not yet seen.
    SKIPPED : Execution was intentionally skipped (e.g. ``DRY_RUN=true``).
    """

    SUCCESS = "success"
    ACTIVE = "active"
    ERROR = "error"
    PENDING = "pending"
    SKIPPED = "skipped"

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Orchestrator (async) launch log — written by ``main_orchestrator.py``.
RUN_LOG_PATH = settings.OUTPUT_DIR / "gui_run.log"

# Advisory (synchronous main.py) launch log — written when the operator picks
# the "Refresh Data (Advisory main.py)" button on the Launcher tab. Kept in a
# distinct file so the orchestrator's stage-marker scan in ``compute_stage_status``
# is not confused by main.py's lighter-weight progress lines.
ADVISORY_LOG_PATH = settings.OUTPUT_DIR / "gui_advisory.log"

# Log written by ``launch_symbol_retry`` for per-symbol dead-letter retries.
RETRY_LOG_PATH: Path = settings.OUTPUT_DIR / "gui_retry.log"

# Telemetry log written by ``alerting.setup_logging()`` and shared by both
# entry points. Surfaced in the Launcher tab so the operator sees structured
# diagnostics from across the platform (CONSTRAINT #2 — observable feedback).
TELEMETRY_LOG_PATH = _REPO_ROOT / "logs" / "investyo.log"

# Env vars the pipeline NEEDS to produce non-trivial output. Missing values are
# surfaced as a pre-launch warning in the UI rather than discovered after a
# silent degraded run. Only the *minimum* set required for a useful refresh —
# optional integrations (Robinhood, alerts, broker) are NOT listed here.
REQUIRED_ENV_VARS: tuple[str, ...] = ("FRED_API_KEY",)

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
    """Mutable handle for a launched pipeline subprocess.

    Stored in Streamlit ``session_state`` so the run survives reruns and the UI
    can poll status without re-launching.

    The ``mode`` field distinguishes the two supported entry points:

    *   ``"orchestrator"`` — async ``main_orchestrator.py`` (full pipeline,
        broker execution, schema validation, HTML report).
    *   ``"advisory"``     — synchronous ``main.py`` (advisory-only, no broker,
        primary entry point for environment loading + global constraint
        validation per the project's ``.env`` convention).
    """

    pid: int
    started_at: float
    dry_run: bool
    refresh_account: bool
    log_path: Path = RUN_LOG_PATH
    mode: str = "orchestrator"
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
        log_path=RUN_LOG_PATH,
        mode="orchestrator",
        _popen=popen,
    )


def launch_advisory_main(refresh_account: bool = False) -> RunHandle:
    """Spawn ``main.py`` (the advisory, synchronous entry point) as a subprocess.

    This is the path the project's ``.env`` convention says is canonical for
    environment loading and global-constraint validation (the entry-point-level
    ``load_dotenv()`` call lives in ``main.py``).  Surfacing it from the
    Launcher tab gives the operator a fast, one-shot refresh that:

    * loads ``.env`` into ``os.environ`` (modules using ``os.environ.get``
      directly — notably ``data.robinhood_portfolio`` — depend on this);
    * runs the structured-logging + push-notification ``alerting.setup_logging``
      flow, so the ``logs/investyo.log`` telemetry tail in the UI populates;
    * writes ``output/state_snapshot.json`` so all observability panels
      refresh their data without restarting the async orchestrator.

    Parameters
    ----------
    refresh_account:
        Pass ``--refresh-account`` to bypass the daily Robinhood cache for
        this launch.

    Returns
    -------
    RunHandle
        Handle for polling status and tailing :data:`ADVISORY_LOG_PATH`.
    """
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd: List[str] = [sys.executable, "main.py"]
    if refresh_account:
        cmd.append("--refresh-account")

    log_file = open(ADVISORY_LOG_PATH, "w", encoding="utf-8")  # noqa: SIM115 - kept open for child
    log_file.write(
        f"# InvestYo advisory (main.py) launch @ {time.strftime('%Y-%m-%d %H:%M:%S')} "
        f"(refresh_account={refresh_account})\n"
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
    logger.info("Launched advisory main.py pid=%s cmd=%s", popen.pid, " ".join(cmd))

    return RunHandle(
        pid=popen.pid,
        started_at=time.time(),
        dry_run=False,  # main.py is advisory-only — no orders submitted
        refresh_account=refresh_account,
        log_path=ADVISORY_LOG_PATH,
        mode="advisory",
        _popen=popen,
    )


def _read_tail(path: Path, max_lines: int, idle_hint: str) -> str:
    """Return the last ``max_lines`` lines of ``path`` (or ``idle_hint``)."""
    if not path.exists():
        return idle_hint
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception as exc:  # pragma: no cover - IO edge
        return f"(failed to read {path.name}: {exc})"


def read_log_tail(max_lines: int = 200, handle: Optional[RunHandle] = None) -> str:
    """Return the last ``max_lines`` lines of the active run log.

    If ``handle`` is supplied, its ``log_path`` is preferred so the tail follows
    whichever entry point (orchestrator vs. advisory) the operator launched
    most recently. With no handle, the orchestrator's log is the default to
    preserve backwards-compatible behaviour for older callers.
    """
    target = handle.log_path if handle is not None else RUN_LOG_PATH
    return _read_tail(
        target,
        max_lines,
        idle_hint="(no run log yet — launch a pipeline to populate it)",
    )


def read_telemetry_tail(max_lines: int = 120) -> str:
    """Return the last ``max_lines`` lines of ``logs/investyo.log``.

    ``alerting.setup_logging()`` rotates that file at 10 MB × 5 backups and is
    invoked by both ``main.py`` and (indirectly) the orchestrator path; tailing
    it gives the UI a single, entry-point-agnostic stream of structured
    diagnostics that survives across runs.
    """
    return _read_tail(
        TELEMETRY_LOG_PATH,
        max_lines,
        idle_hint="(no telemetry yet — alerting.setup_logging() runs on first launch)",
    )


def validate_required_env(
    required: Optional[Iterable[str]] = None,
) -> Dict[str, bool]:
    """Return a mapping ``{env_var: present}`` for each required variable.

    A variable is considered *present* when it appears in ``os.environ`` with a
    non-empty stripped value. This is a pre-launch readiness check surfaced in
    the Launcher tab so a missing key (most commonly ``FRED_API_KEY``) is
    diagnosed *before* the subprocess silently degrades to neutral defaults
    rather than after — eliminating the failure mode the user reported as
    "Refresh Data does not produce observable results".

    Parameters
    ----------
    required:
        Iterable of env-var names to check. Defaults to
        :data:`REQUIRED_ENV_VARS`.
    """
    names = tuple(required) if required is not None else REQUIRED_ENV_VARS
    out: Dict[str, bool] = {}
    for n in names:
        val = os.environ.get(n, "")
        out[n] = bool(val and val.strip())
    return out


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


def launch_symbol_retry(
    symbol: str,
    refresh_account: bool = False,
) -> "RunHandle":
    """Spawn ``main.py`` targeting a single symbol for a dead-letter retry.

    The symbol is injected via the ``WATCHLIST`` environment variable so
    the advisory run contains only that ticker.  Held positions are always
    included by ``main.run_once()``'s ``_build_universe`` — the operator
    can confirm the single-symbol result on the Paper Monitor tab.

    The retry is a best-effort diagnosis run, not a production execution:
    ``main.py`` is advisory-only and submits no orders.

    Parameters
    ----------
    symbol:
        Ticker to retry (case-insensitive; forced to upper-case for the env).
    refresh_account:
        Pass ``--refresh-account`` to bypass the daily Robinhood cache.

    Returns
    -------
    RunHandle
        Handle with ``mode="retry"`` and ``log_path=RETRY_LOG_PATH``.
    """
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd: List[str] = [sys.executable, "main.py"]
    if refresh_account:
        cmd.append("--refresh-account")

    # Inject the single target symbol via WATCHLIST so no code in main.py
    # needs to change — it already reads WATCHLIST from os.environ.
    env = os.environ.copy()
    env["WATCHLIST"] = symbol.upper()

    log_file = open(RETRY_LOG_PATH, "w", encoding="utf-8")  # noqa: SIM115
    log_file.write(
        f"# InvestYo dead-letter retry: {symbol.upper()} "
        f"@ {time.strftime('%Y-%m-%d %H:%M:%S')} "
        f"(refresh_account={refresh_account})\n"
    )
    log_file.flush()

    popen = subprocess.Popen(
        cmd,
        cwd=str(_REPO_ROOT),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        bufsize=1,
        text=True,
    )
    logger.info(
        "Launched dead-letter retry pid=%s symbol=%s", popen.pid, symbol.upper()
    )

    return RunHandle(
        pid=popen.pid,
        started_at=time.time(),
        dry_run=False,
        refresh_account=refresh_account,
        log_path=RETRY_LOG_PATH,
        mode="retry",
        _popen=popen,
    )


def compute_stage_status(
    handle: Optional[RunHandle],
) -> Dict[str, StageStatus]:
    """Derive per-stage status from the run log + heartbeat + snapshot mtime.

    Returns a mapping ``{stage_label: StageStatus}``.

    *   ``PENDING`` — launched but this stage's markers not yet seen.
    *   ``ACTIVE``  — this is the latest stage whose markers have appeared.
    *   ``SUCCESS`` — a later stage's markers appeared or run finished cleanly.
    *   ``ERROR``   — run exited non-zero and this was the last active stage.
    *   ``SKIPPED`` — Execution stage when the run handle was ``dry_run=True``
                      (orchestrator logs intent but never calls the broker).

    Note
    ----
    The legacy string values (``"pending"``, ``"active"``, etc.) are still
    valid because :class:`StageStatus` subclasses ``str``.  Callers that
    previously compared against bare string literals continue to work.
    """
    labels = [label for label, _ in STAGES]
    if handle is None:
        return {label: StageStatus.PENDING for label in labels}
    if not handle.log_path.exists():
        return {label: StageStatus.PENDING for label in labels}

    log_text = read_log_tail(max_lines=2000, handle=handle).lower()
    reached: List[bool] = []
    for _label, markers in STAGES:
        reached.append(any(m in log_text for m in markers))

    # Index of the furthest stage reached (-1 if none).
    last_reached = max((i for i, r in enumerate(reached) if r), default=-1)

    finished = not handle.is_running()
    rc = handle.returncode()
    run_errored = finished and rc is not None and rc != 0
    snapshot = settings.OUTPUT_DIR / "state_snapshot.json"
    snapshot_fresh = snapshot.exists() and (snapshot.stat().st_mtime >= handle.started_at)

    status: Dict[str, StageStatus] = {}
    for i, label in enumerate(labels):
        # "Execution" stage is SKIPPED when dry_run=True on an orchestrator run.
        if label == "Execution" and handle.dry_run and handle.mode == "orchestrator":
            status[label] = StageStatus.SKIPPED
            continue

        if finished and snapshot_fresh and not run_errored:
            status[label] = StageStatus.SUCCESS
        elif run_errored and i == last_reached:
            status[label] = StageStatus.ERROR
        elif run_errored and i < last_reached:
            status[label] = StageStatus.SUCCESS
        elif i < last_reached:
            status[label] = StageStatus.SUCCESS
        elif i == last_reached:
            status[label] = StageStatus.ACTIVE
        else:
            status[label] = StageStatus.PENDING
    return status
