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

# Log written by ``launch_daemon_engine`` — the always-on desktop-shell
# refresh loop when settings.ORCHESTRATOR_DAEMON_ENABLED is True (spawns
# `python -m desktop.orchestrator_daemon --interval N` instead of
# `main.py --interval N`). Kept distinct for the same reason as the other
# per-launcher log paths in this module.
DAEMON_LOG_PATH: Path = settings.OUTPUT_DIR / "gui_daemon.log"

# Log written by ``launch_scheduled_advisory`` — the AI Control Center's
# operator-started recurring run (main.py --interval N / --agent).  Kept
# distinct so the Control Center tails only its own scheduled run.
SCHEDULED_LOG_PATH: Path = settings.OUTPUT_DIR / "gui_scheduled.log"

# Log written by ``launch_pytest`` — the Maintenance tab's "Run test suite"
# button.  Kept distinct so the long-running pytest tail never collides with a
# concurrent orchestrator/advisory refresh log.
PYTEST_LOG_PATH: Path = settings.OUTPUT_DIR / "gui_pytest.log"

# Log written by ``launch_verify`` — the Maintenance tab's "Run make verify"
# button (env-var check → pytest → one live run_once + summary).  Distinct file
# so the operator can tail the full readiness gate independently of a refresh.
VERIFY_LOG_PATH: Path = settings.OUTPUT_DIR / "gui_verify.log"

# Log written by ``launch_gravity_audit`` — the Gravity Audit tab's "Run Gravity
# AI Review Suite" button.  Distinct file so the operator can tail the audit run
# and the parsed JSON report is read back from this log independently of any
# concurrent orchestrator/advisory/pytest refresh log.
GRAVITY_LOG_PATH: Path = settings.OUTPUT_DIR / "gravity_run.log"

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
    """Mutable handle for a launched pipeline run.

    Stored in Streamlit ``session_state`` so the run survives reruns and the UI
    can poll status without re-launching.

    The ``mode`` field distinguishes the supported entry points:

    *   ``"orchestrator"`` — async ``main_orchestrator.py`` (full pipeline,
        broker execution, schema validation, HTML report).
    *   ``"advisory"``     — synchronous ``main.py`` (advisory-only, no broker,
        primary entry point for environment loading + global constraint
        validation per the project's ``.env`` convention).
    *   ``"scheduled"``    — the AI Control Center's recurring ``main.py
        --interval``/``--agent`` loop.
    *   ``"daemon"``       — the always-on desktop-shell refresh loop backed
        by ``python -m desktop.orchestrator_daemon`` (still a supervised
        subprocess — see :func:`launch_daemon_engine`).

    The ``backend`` field distinguishes HOW a run is tracked/controlled:

    *   ``"subprocess"`` (default) — a local ``Popen`` child; ``is_running``/
        ``returncode``/``stop_run`` use the process-level mechanics below.
        This is what every launcher in this module produces except one case.
    *   ``"daemon"`` — a cycle TRIGGERED over HTTP against an
        already-running persistent orchestrator daemon
        (:func:`launch_orchestrator`'s flag-gated fast path). There is no
        local process for the cycle itself — ``daemon_run_id`` identifies it
        to the daemon's Control API, and ``is_running``/``returncode`` poll
        :mod:`gui.daemon_client` instead of a ``Popen`` handle. Do not
        confuse this with ``mode="daemon"`` above, which is the (ordinary
        subprocess-backed) always-on refresh loop itself.
    """

    pid: int
    started_at: float
    dry_run: bool
    refresh_account: bool
    log_path: Path = RUN_LOG_PATH
    mode: str = "orchestrator"
    backend: str = "subprocess"
    daemon_run_id: Optional[str] = None
    _popen: Optional[subprocess.Popen] = field(default=None, repr=False)

    def is_running(self) -> bool:
        """True while the run has not yet finished."""
        if self.backend == "daemon":
            status = _daemon_run_status(self.daemon_run_id)
            if status is None:
                # Daemon unreachable, or the run id is unknown to it — fail
                # safe rather than reporting "running" forever and hanging
                # the UI's poll loop.
                return False
            return status.get("state") in ("queued", "running")
        if self._popen is None:
            # Reconstructed across a rerun without the Popen object: fall back to
            # an OS-level liveness probe on the PID.
            return _pid_alive(self.pid)
        return self._popen.poll() is None

    def returncode(self) -> Optional[int]:
        """Exit code if finished, else ``None``. For a daemon-backed run this
        is synthesized from the Control API's run state (0 = succeeded,
        1 = failed) since there is no real process exit code."""
        if self.backend == "daemon":
            status = _daemon_run_status(self.daemon_run_id)
            if status is None:
                return None
            state = status.get("state")
            if state == "succeeded":
                return 0
            if state == "failed":
                return 1
            return None  # queued/running, or an unrecognized state
        if self._popen is None:
            return None
        return self._popen.poll()


def _daemon_run_status(daemon_run_id: Optional[str]) -> Optional[dict]:
    """Look up a daemon-hosted run's status via gui.daemon_client, never
    raising. Returns None if daemon_run_id is unset or the lookup fails for
    any reason (daemon unreachable, unknown run id, network error) —
    CONSTRAINT #6, dead-letter resilience."""
    if not daemon_run_id:
        return None
    try:
        from gui.daemon_client import get_run_status
        return get_run_status(daemon_run_id)
    except Exception as exc:  # noqa: BLE001 - a status poll must never crash the GUI
        logger.warning("Failed to poll daemon run status for %s: %s", daemon_run_id, exc)
        return None


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
    """Trigger a pipeline cycle — via the persistent orchestrator daemon's
    Control API when available, else by spawning ``main_orchestrator.py`` as
    a non-blocking subprocess (today's behavior).

    Parameters
    ----------
    dry_run:
        When True, pass ``--dry-run`` so the orchestrator logs intended orders
        but never submits them (mirrors ``settings.DRY_RUN`` / the CLI flag).
        The daemon fast path below is skipped whenever ``dry_run=True`` --
        an already-running daemon was started with a FIXED ``dry_run`` value
        at process launch and has no per-trigger override, so honoring an
        explicit dry-run request safely means falling back to a real
        subprocess rather than silently ignoring it.
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
    Daemon fast path (``settings.ORCHESTRATOR_DAEMON_ENABLED=True``, an
    already-running daemon reachable, and ``dry_run=False``): triggers a
    cycle over HTTP via :mod:`gui.daemon_client` against the running daemon
    instead of spawning a new process, returning a ``backend="daemon"``
    ``RunHandle``. Falls back to the subprocess path below (unchanged) when
    the flag is off, the daemon is unreachable, ``trigger_run()`` itself
    fails, or ``dry_run=True`` — CONSTRAINT #6, this fast path degrades
    silently rather than ever blocking a manual run.

    Subprocess path (default / fallback): the child inherits the current
    environment (so ``.env`` is loaded by the orchestrator itself via
    ``load_dotenv``).  stdout+stderr are redirected to :data:`RUN_LOG_PATH`,
    truncated at launch so the UI tails only the current run.  We use
    ``sys.executable`` to guarantee the ``.venv`` interpreter is used rather
    than a stray system Python.
    """
    if settings.ORCHESTRATOR_DAEMON_ENABLED and not dry_run:
        try:
            from gui.daemon_client import daemon_available, trigger_run

            if daemon_available():
                result = trigger_run()
                if result.ok:
                    logger.info(
                        "Triggered orchestrator cycle via daemon Control API: run_id=%s",
                        result.run_id,
                    )
                    return RunHandle(
                        pid=-1,  # no local process — the daemon owns this run
                        started_at=time.time(),
                        dry_run=dry_run,
                        refresh_account=refresh_account,
                        log_path=RUN_LOG_PATH,
                        mode="orchestrator",
                        backend="daemon",
                        daemon_run_id=result.run_id,
                    )
                logger.warning(
                    "Daemon reachable but trigger_run failed (%s); falling back "
                    "to subprocess launch.", result.error,
                )
        except Exception as exc:  # noqa: BLE001 - the fast path must never block a manual run
            logger.warning("Daemon fast path failed (%s); falling back to subprocess launch.", exc)

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


def launch_scheduled_advisory(
    mode: str = "interval",
    interval_seconds: int = 300,
    *,
    refresh_account: bool = False,
) -> RunHandle:
    """Spawn ``main.py`` as an OPERATOR-STARTED recurring advisory run.

    This backs the AI Control Center's "Start scheduled run" / "Start agent
    loop" buttons.  It is the ONLY scheduling mechanism the platform exposes
    from the AI Control Center's tab and it is strictly operator-initiated
    and operator-stoppable — there is no cron and nothing autonomous.  The
    operator presses Start; the child keeps refreshing on the requested
    cadence until the operator presses Stop (:func:`stop_run`) or the GUI
    process ends.  (This module also has a SEPARATE, deliberately-scoped
    long-lived process now: :func:`launch_daemon_engine`, which backs the
    desktop shell's always-on refresh loop when
    ``settings.ORCHESTRATOR_DAEMON_ENABLED=True`` — still strictly
    operator-started via the desktop shell opening, still advisory-only, and
    still fully stoppable via :func:`stop_run`/closing the window; it is not
    autonomous or self-scheduling beyond the interval the operator
    configured, so the "no cron, nothing autonomous" guarantee here is
    unaffected.)

    Parameters
    ----------
    mode:
        ``"interval"`` → ``python main.py --interval N`` (fixed-cadence refresh
        loop).  ``"agent"`` → ``python main.py --agent`` (the autonomous
        *advisory* agent loop from Tier 6 — still advisory-only, no orders,
        still operator-started/stoppable).  Any other value is treated as
        ``"interval"``.
    interval_seconds:
        Cadence in seconds for ``--interval`` mode.  Ignored for ``--agent``
        (the agent sets its own adaptive cadence).  Clamped to ``>= 30`` so the
        operator cannot hot-loop the market-data API.
    refresh_account:
        Pass ``--refresh-account`` on the first launch to bypass the daily
        Robinhood cache.

    Returns
    -------
    RunHandle
        Handle (``mode="scheduled"``, ``log_path=SCHEDULED_LOG_PATH``) for
        polling status, tailing the log, and stopping via :func:`stop_run`.

    Notes
    -----
    During a scheduled run, only the ALREADY-WIRED automatic AI path fires —
    Gemini alert-commentary appended to watch/trade alerts when
    ``LLM_COMMENTARY_ENABLED`` is set.  The per-symbol Claude / Gemini-vision /
    Opal actions remain on-demand GUI buttons; they are NOT invoked by the
    scheduled loop.
    """
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd: List[str] = [sys.executable, "main.py"]
    if str(mode).lower() == "agent":
        cmd.append("--agent")
        cadence_desc = "agent-adaptive"
    else:
        safe_interval = max(30, int(interval_seconds or 0))
        cmd.extend(["--interval", str(safe_interval)])
        cadence_desc = f"{safe_interval}s"
    if refresh_account:
        cmd.append("--refresh-account")

    log_file = open(SCHEDULED_LOG_PATH, "w", encoding="utf-8")  # noqa: SIM115 - kept open for child
    log_file.write(
        f"# InvestYo scheduled advisory (main.py) launch @ "
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} (mode={mode}, cadence={cadence_desc}, "
        f"refresh_account={refresh_account})\n"
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
    logger.info("Launched scheduled advisory pid=%s cmd=%s", popen.pid, " ".join(cmd))

    return RunHandle(
        pid=popen.pid,
        started_at=time.time(),
        dry_run=False,  # advisory-only — no orders submitted
        refresh_account=refresh_account,
        log_path=SCHEDULED_LOG_PATH,
        mode="scheduled",
        _popen=popen,
    )


def launch_daemon_engine(interval_seconds: int = 300, *, refresh_account: bool = False) -> RunHandle:
    """Spawn the persistent orchestrator daemon as the always-on background
    refresh loop, replacing ``main.py --interval N`` when
    ``settings.ORCHESTRATOR_DAEMON_ENABLED`` is True.

    This is ``desktop/engine_supervisor.py``'s cutover target for
    ``start_engine``. It reuses the EXACT SAME ``Popen``/``RunHandle``/
    ``stop_run`` mechanics as :func:`launch_scheduled_advisory` — the daemon
    is just a different command being supervised as a child process, not a
    different handle shape or control mechanism. The daemon's warm-engine
    benefit (engines constructed once, reused across cycles) is entirely
    internal to that process; the GUI still just spawns-and-supervises one
    subprocess exactly as before, so ``stop_run`` (SIGTERM→SIGKILL) works
    completely unchanged.

    Parameters
    ----------
    interval_seconds:
        Cadence in seconds for the daemon's internal timer thread. Clamped
        to ``>= 30`` (mirrors :func:`launch_scheduled_advisory`) so the
        operator cannot hot-loop the market-data API.
    refresh_account:
        Accepted for call-site symmetry with :func:`launch_scheduled_advisory`
        but has no daemon-entrypoint equivalent — ``desktop/
        orchestrator_daemon.py`` drives ``main_orchestrator._main_body``,
        which has no ``--refresh-account`` concept (that flag only exists on
        ``main.py``'s advisory path). Silently ignored.

    Returns
    -------
    RunHandle
        Handle (``mode="daemon"``, ``backend="subprocess"``,
        ``log_path=DAEMON_LOG_PATH``) for polling status, tailing the log,
        and stopping via :func:`stop_run` — identical mechanics to every
        other subprocess-backed launcher in this module.
    """
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    safe_interval = max(30, int(interval_seconds or 0))
    cmd: List[str] = [
        sys.executable, "-m", "desktop.orchestrator_daemon",
        "--interval", str(safe_interval),
    ]

    log_file = open(DAEMON_LOG_PATH, "w", encoding="utf-8")  # noqa: SIM115 - kept open for the child
    log_file.write(
        f"# InvestYo persistent orchestrator daemon launch @ "
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} (interval={safe_interval}s)\n"
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
    logger.info("Launched persistent orchestrator daemon pid=%s cmd=%s", popen.pid, " ".join(cmd))

    return RunHandle(
        pid=popen.pid,
        started_at=time.time(),
        dry_run=False,
        refresh_account=False,
        log_path=DAEMON_LOG_PATH,
        mode="daemon",
        _popen=popen,
    )


def launch_pytest() -> RunHandle:
    """Spawn the full ``pytest`` suite as a non-blocking subprocess.

    This backs the Maintenance tab's "Run test suite" button.  ``pytest`` is a
    long-running maintenance command (the full suite takes minutes), so — like
    every other launcher in this module — it is spawned via a detached
    :class:`subprocess.Popen` rather than run inline, keeping the Streamlit UI
    responsive while the operator tails the streamed log.

    The child runs ``[sys.executable, "-m", "pytest", "-q"]`` from the repo
    root, inheriting the current environment (so ``.env`` is available to any
    test that reads it).  stdout+stderr are redirected to :data:`PYTEST_LOG_PATH`,
    truncated at launch so the UI tails only the current run.  Using
    ``sys.executable`` guarantees the ``.venv`` interpreter is used rather than a
    stray system Python.

    Returns
    -------
    RunHandle
        Handle (``mode="pytest"``, ``log_path=PYTEST_LOG_PATH``) for polling
        status, tailing the log via :func:`read_log_tail`, and stopping via
        :func:`stop_run`.
    """
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd: List[str] = [sys.executable, "-m", "pytest", "-q"]

    log_file = open(PYTEST_LOG_PATH, "w", encoding="utf-8")  # noqa: SIM115 - kept open for child
    log_file.write(
        f"# InvestYo pytest launch @ {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
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
    logger.info("Launched pytest pid=%s cmd=%s", popen.pid, " ".join(cmd))

    return RunHandle(
        pid=popen.pid,
        started_at=time.time(),
        dry_run=False,
        refresh_account=False,
        log_path=PYTEST_LOG_PATH,
        mode="pytest",
        _popen=popen,
    )


def launch_gravity_audit() -> RunHandle:
    """Spawn the Gravity AI Review Suite as a non-blocking subprocess.

    This backs the Gravity Audit tab's non-blocking run.  The Gravity AI Review
    Suite is a long-running static-analysis + simulation audit, so — like every
    other launcher in this module — it is spawned via a detached
    :class:`subprocess.Popen` rather than run inline, keeping the Streamlit UI
    responsive while the operator tails the streamed log.

    The child runs ``[sys.executable, "Gravity AI Review Suite.py"]`` from the
    repo root, inheriting the current environment (so ``.env`` is available).
    stdout+stderr are redirected to :data:`GRAVITY_LOG_PATH`, truncated at launch
    so the UI tails only the current run; the parsed JSON report is read back from
    the trailing lines of this log after the run completes.  Using
    ``sys.executable`` guarantees the ``.venv`` interpreter is used rather than a
    stray system Python.

    Returns
    -------
    RunHandle
        Handle (``mode="gravity"``, ``log_path=GRAVITY_LOG_PATH``) for polling
        status, tailing the log via :func:`read_log_tail`, and stopping via
        :func:`stop_run`.
    """
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd: List[str] = [sys.executable, "Gravity AI Review Suite.py"]

    log_file = open(GRAVITY_LOG_PATH, "w", encoding="utf-8")  # noqa: SIM115 - kept open for child
    log_file.write(
        f"# InvestYo Gravity AI Review Suite launch @ {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
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
    logger.info("Launched Gravity AI Review Suite pid=%s cmd=%s", popen.pid, " ".join(cmd))

    return RunHandle(
        pid=popen.pid,
        started_at=time.time(),
        dry_run=False,
        refresh_account=False,
        log_path=GRAVITY_LOG_PATH,
        mode="gravity",
        _popen=popen,
    )


def launch_verify() -> RunHandle:
    """Spawn the ``make verify`` readiness gate as a non-blocking subprocess.

    This backs the Maintenance tab's "Run make verify" button.  ``make verify``
    runs the full readiness gate — env-var check → pytest → one live
    ``run_once()`` cycle → print summary — and is therefore long-running, so it
    is spawned via a detached :class:`subprocess.Popen` (never inline) with a
    tailed log, exactly like the other launchers in this module.

    Note the command is ``["make", "verify"]``, NOT ``sys.executable`` — ``make``
    is the executable here, and the Makefile's ``verify`` target itself invokes
    ``.venv/bin/python3`` for the pytest + ``run_once()`` steps.  The child runs
    from the repo root inheriting the current environment; stdout+stderr are
    redirected to :data:`VERIFY_LOG_PATH`, truncated at launch so the UI tails
    only the current run.

    Returns
    -------
    RunHandle
        Handle (``mode="verify"``, ``log_path=VERIFY_LOG_PATH``) for polling
        status, tailing the log via :func:`read_log_tail`, and stopping via
        :func:`stop_run`.
    """
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd: List[str] = ["make", "verify"]

    log_file = open(VERIFY_LOG_PATH, "w", encoding="utf-8")  # noqa: SIM115 - kept open for child
    log_file.write(
        f"# InvestYo make-verify launch @ {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
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
    logger.info("Launched make verify pid=%s cmd=%s", popen.pid, " ".join(cmd))

    return RunHandle(
        pid=popen.pid,
        started_at=time.time(),
        dry_run=False,
        refresh_account=False,
        log_path=VERIFY_LOG_PATH,
        mode="verify",
        _popen=popen,
    )


def stop_run(handle: Optional[RunHandle], *, timeout: float = 5.0) -> bool:
    """Terminate a launched subprocess (operator "Stop" button).

    Sends SIGTERM (via ``Popen.terminate`` when the object survived, else an
    OS-level ``os.kill``), waits up to ``timeout`` seconds, then escalates to
    SIGKILL if still alive.  Returns ``True`` when the process is confirmed
    stopped (or was never running), ``False`` if it could not be confirmed.
    Never raises — a Stop button must not crash the GUI (CONSTRAINT #6).

    A ``backend="daemon"`` handle (a cycle triggered over HTTP against an
    already-running daemon) cannot be stopped — the Control API exposes no
    cancel/abort endpoint, so an in-flight daemon-hosted cycle always runs to
    completion. This is logged clearly and returns ``False`` (not stopped)
    rather than pretending to cancel something it can't.
    """
    if handle is None:
        return True
    if getattr(handle, "backend", "subprocess") == "daemon":
        logger.warning(
            "stop_run: cannot cancel a daemon-hosted run (run_id=%s) — the "
            "Control API has no cancel/abort endpoint; the cycle will run to "
            "completion.",
            getattr(handle, "daemon_run_id", None),
        )
        return False
    try:
        popen = getattr(handle, "_popen", None)
        if popen is not None:
            if popen.poll() is not None:
                return True  # already exited
            popen.terminate()
            try:
                popen.wait(timeout=timeout)
            except Exception:
                popen.kill()
            return popen.poll() is not None
        # Reconstructed across a Streamlit rerun without the Popen object:
        # fall back to a PID-level signal.
        pid = getattr(handle, "pid", None)
        if not pid or not _pid_alive(int(pid)):
            return True
        import signal as _signal  # noqa: PLC0415

        os.kill(int(pid), _signal.SIGTERM)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not _pid_alive(int(pid)):
                return True
            time.sleep(0.2)
        try:
            os.kill(int(pid), _signal.SIGKILL)
        except Exception:
            pass
        return not _pid_alive(int(pid))
    except Exception as exc:
        logger.warning("stop_run failed for handle pid=%s: %s",
                       getattr(handle, "pid", "?"), exc)
        return False


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

    A ``backend="daemon"`` handle has no local per-run log file (the cycle
    runs inside the daemon's own process) — a short status summary is
    synthesized from :mod:`gui.daemon_client` instead.
    """
    if handle is not None and getattr(handle, "backend", "subprocess") == "daemon":
        return _daemon_run_summary(handle.daemon_run_id)
    target = handle.log_path if handle is not None else RUN_LOG_PATH
    return _read_tail(
        target,
        max_lines,
        idle_hint="(no run log yet — launch a pipeline to populate it)",
    )


def _daemon_run_summary(daemon_run_id: Optional[str]) -> str:
    """Render a short, human-readable status summary for a daemon-hosted run
    in place of a log tail — there is no local log file to read."""
    status = _daemon_run_status(daemon_run_id)
    if status is None:
        return (
            "(daemon-hosted run: status unavailable — the daemon may be "
            "unreachable; see the Observability tab for daemon liveness)"
        )
    lines = [
        f"# Daemon-hosted run {status.get('run_id')}",
        f"state: {status.get('state')}",
        f"started_at: {status.get('started_at')}",
        f"finished_at: {status.get('finished_at')}",
        f"duration_seconds: {status.get('duration_seconds')}",
    ]
    if status.get("error"):
        lines.append(f"error: {status.get('error')}")
    lines.append("")
    lines.append(
        "(This cycle ran inside the persistent orchestrator daemon's own "
        "process, not a per-run subprocess — see logs/investyo.log for "
        "shared structured telemetry, or the daemon's own stdout log if it "
        "was launched as the always-on refresh loop.)"
    )
    return "\n".join(lines)


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
    missing one means it is idle or crashed. ``output/heartbeat.txt`` is
    written ONLY by ``main_orchestrator.py``'s async ``_heartbeat()`` task --
    ``main.py`` (including its ``--interval``/``--agent`` scheduled-run modes)
    never writes it. Callers wanting a liveness signal that covers BOTH
    entry points should use :func:`state_snapshot_age_seconds` instead, or
    combine both (see ``gui/engine_status.py``).
    """
    hb = settings.OUTPUT_DIR / "heartbeat.txt"
    if not hb.exists():
        return None
    try:
        return max(0.0, time.time() - hb.stat().st_mtime)
    except Exception:
        return None


def state_snapshot_age_seconds() -> Optional[float]:
    """Seconds since ``output/state_snapshot.json`` was last written, or None
    if absent.

    Unlike :func:`heartbeat_age_seconds` (orchestrator-only), BOTH ``main.py``
    (every ``run_once()`` cycle, including ``--interval``/``--agent`` mode)
    and ``main_orchestrator.py`` rewrite this file, so it is the one liveness
    signal common to every scheduled-run entry point this platform supports.
    """
    snap = settings.OUTPUT_DIR / "state_snapshot.json"
    if not snap.exists():
        return None
    try:
        return max(0.0, time.time() - snap.stat().st_mtime)
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
    if getattr(handle, "backend", "subprocess") == "daemon":
        # A daemon-hosted run has no local log to scan for stage markers
        # (see read_log_tail's daemon branch). Map the coarse daemon run
        # state onto a uniform per-stage status instead of fine-grained
        # per-stage tracking -- degraded but honest and non-crashing
        # (CONSTRAINT #6), rather than showing a misleading "all pending"
        # for a run that actually already succeeded.
        status = _daemon_run_status(handle.daemon_run_id)
        state = status.get("state") if status else None
        if state == "succeeded":
            return {label: StageStatus.SUCCESS for label in labels}
        if state == "failed":
            return {label: StageStatus.ERROR for label in labels}
        if state in ("running", "queued"):
            return {label: StageStatus.ACTIVE for label in labels}
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
