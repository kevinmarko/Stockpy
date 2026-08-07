"""Parent-side launcher for the isolated forecast-backfill worker
(:mod:`ml.forecast_backfill_worker`).

Mirrors :mod:`data.robinhood_login`'s killable-subprocess job pattern
exactly (``LoginJobState`` -> ``BackfillJobState``, ``start_login`` ->
``start_job``, ``_drain_events``/``_enforce_deadline``/
``_kill_process_group`` unchanged in shape) rather than
:mod:`api._jobs`'s ``JobManager`` (a separate Control-API-only subsystem on
a different port/token) or an in-process background thread — training is
CPU-bound and holds the GIL, so a subprocess is what keeps the rest of the
Pilots API responsive while a run is in flight.

``AgenticForecastBackfiller`` (``ml/forecast_backfill.py``) runs a 6-step,
CPU-bound pipeline that can take minutes, not the sub-second, safe-to-block
work this codebase's other Pilots API handlers do. The previous
implementation blocked the HTTP request for the entire run, guarded only by
an in-process ``threading.Lock`` (``api/pilots_api.py``'s old
``_forecast_backfill_lock``). This module replaces that lock with the same
module-level single-flight primitive the login job uses
(``_active_job_id``): ``start_job()`` returns ``None`` if a run is already
in progress, and the caller (``api/pilots_api.py``) translates that into a
structured 409 carrying the in-flight job's id so a client can poll it
instead of hitting a dead end.

Run parameters (JSON-serializable: tickers/start_date/end_date/use_fmp/
horizons/strategy_ids/theta_c — the exact shape of
``ForecastBackfillRunRequest.model_dump()``) cross the process boundary over
an anonymous pipe, the same idiom as the login worker's credentials pipe —
not because they are secret (they are not), but because it is the
established, already-tested pattern for handing a child process its inputs
without touching argv (visible via ``ps``) or the environment.

Two ways to use a backfill job:
  - ``start_job()`` / ``get_job_state()`` / ``cancel_job()`` — the
    non-blocking job primitive ``api/pilots_api.py``'s
    ``POST /pilots/forecast_backfill/run``,
    ``GET /pilots/forecast_backfill/status/{job_id}``, and
    ``POST /pilots/forecast_backfill/cancel/{job_id}`` poll against.
  - ``serialize_job()`` — the JSON-safe status payload shared by the run and
    status endpoints (mirrors ``api._rh_login.serialize_job``). Kept in this
    module rather than a separate glue file (unlike ``api._rh_login``, there
    is no Pilots-API-specific side effect to own here — ``export_results()``
    already writes its own output artifacts directly from inside the
    engine).
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from settings import settings

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent

# SIGTERM-to-SIGKILL grace period when a backfill worker is cancelled or hits
# its deadline. A plain module constant (not a dedicated setting) -- this
# codebase reserves a dedicated *_GRACE_SECONDS setting for cases like
# data/robinhood_login.py's RH_LOGIN_GRACE_SECONDS where an operator has a
# real reason to tune it; there is no equivalent reason here.
_KILL_GRACE_SECONDS = 5.0

BackfillPhase = Literal[
    "fetching_data",
    "technical_features",
    "primary_signals",
    "meta_targets",
    "backtraining",
    "backfilling",
    "exporting",
]
BackfillState = Literal["running", "succeeded", "failed", "timeout", "cancelled"]
BackfillErrorType = Literal["value_error", "unexpected", "timeout", "cancelled"]

_TOTAL_STEPS = 7


@dataclass
class BackfillJobState:
    job_id: str
    state: BackfillState = "running"
    phase: Optional[BackfillPhase] = None
    step: int = 0
    total_steps: int = _TOTAL_STEPS
    error: Optional[str] = None
    error_type: Optional[BackfillErrorType] = None
    summary: Optional[dict] = None
    sample_rows: Optional[int] = None
    started_at: float = field(default_factory=time.time)
    deadline_at: float = field(
        default_factory=lambda: time.time() + settings.FORECAST_BACKFILL_DEADLINE_SECONDS
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _process: Optional[subprocess.Popen] = field(default=None, repr=False)

    @property
    def seconds_remaining(self) -> float:
        return max(0.0, self.deadline_at - time.time())


_jobs: dict[str, BackfillJobState] = {}
_jobs_lock = threading.Lock()
# Single-flight marker: the job_id of the currently-running backfill, or
# None. Guarded by _jobs_lock (not job._lock -- this tracks which job is
# active across the whole module, not one job's own state).
_active_job_id: Optional[str] = None


def _clear_active_job(job_id: str) -> None:
    global _active_job_id
    with _jobs_lock:
        if _active_job_id == job_id:
            _active_job_id = None


def _kill_process_group(proc: Optional[subprocess.Popen]) -> None:
    """SIGTERM the worker's process group, wait out _KILL_GRACE_SECONDS, then
    SIGKILL if it's still alive. Never raises -- a process that's already
    exited (ProcessLookupError) is the success case, not an error. Mirrors
    data.robinhood_login._kill_process_group exactly."""
    if proc is None or proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception as exc:  # noqa: BLE001 - best-effort escalation below regardless
        logger.debug("forecast_backfill_job: SIGTERM failed: %s", exc)
    try:
        proc.wait(timeout=_KILL_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception as exc:  # noqa: BLE001 - nothing more we can do
        logger.warning("forecast_backfill_job: SIGKILL failed: %s", exc)
    try:
        proc.wait(timeout=_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning("forecast_backfill_job: worker process did not exit after SIGKILL.")


def _drain_events(events_r: int, job: BackfillJobState) -> None:
    """Background thread: read NDJSON events off the child's events pipe and
    update the job record's phase/step/terminal state. Runs until EOF (the
    child closed its write end, whether by exiting cleanly or being killed).

    EOF with no terminal ``result`` event ever having been observed is
    treated as an honest failure (``"worker exited without reporting a
    result"``) rather than leaving the job stuck ``"running"`` forever --
    the same dead-letter posture as every other never-raise path in this
    codebase (CONSTRAINT #6)."""
    try:
        with os.fdopen(events_r, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                with job._lock:
                    if job.state != "running":
                        continue
                    event = obj.get("event")
                    if event == "phase":
                        job.phase = obj.get("phase", job.phase)
                        step = obj.get("step")
                        if isinstance(step, int) and not isinstance(step, bool):
                            job.step = step
                    elif event == "result":
                        if obj.get("ok"):
                            job.state = "succeeded"
                            job.summary = obj.get("summary")
                            job.sample_rows = obj.get("sample_rows")
                        else:
                            job.state = "failed"
                            job.error = obj.get("error") or "Forecast backfill failed."
                            job.error_type = obj.get("error_type", "unexpected")
    except Exception as exc:  # noqa: BLE001 - the deadline thread is the safety net either way
        logger.debug("forecast_backfill_job: event-drain thread ended: %s", exc)
    finally:
        with job._lock:
            if job.state == "running":
                job.state = "failed"
                job.error = "Forecast backfill worker exited without reporting a result."
                job.error_type = "unexpected"
        _clear_active_job(job.job_id)


def _enforce_deadline(job: BackfillJobState) -> None:
    """Background thread: waits out the job's deadline, then SIGTERM/SIGKILLs
    the process group if it's still running. Mirrors
    data.robinhood_login._enforce_deadline's overall-deadline half (this job
    type has no separate "child hasn't started yet" startup-timeout check --
    a training run's first real progress event can legitimately take a while
    just to fetch data, unlike a login worker's near-instant 'started')."""
    while time.time() < job.deadline_at:
        with job._lock:
            if job.state != "running":
                return
        time.sleep(0.5)

    with job._lock:
        if job.state != "running":
            return
        _kill_process_group(job._process)
        job.state = "timeout"
        job.error = (
            f"Forecast backfill did not complete within "
            f"{settings.FORECAST_BACKFILL_DEADLINE_SECONDS}s."
        )
        job.error_type = "timeout"
    _clear_active_job(job.job_id)


def start_job(params: Dict[str, Any]) -> Optional[BackfillJobState]:
    """Launch one forecast-backfill run as an isolated, killable subprocess
    and return immediately with its (running) job state. Poll via
    ``get_job_state(job.job_id)``.

    Returns ``None`` (never raises for this reason) if a job is already
    running -- single-flight, replacing the in-process ``threading.Lock``
    ``api/pilots_api.py`` previously guarded the (blocking) endpoint with.
    The caller is expected to translate ``None`` into an HTTP 409 carrying
    the in-flight job's id (see ``get_job_state`` / the module docstring).

    ``params`` -- a JSON-serializable dict shaped exactly like
    ``ForecastBackfillRunRequest.model_dump()`` (tickers/start_date/
    end_date/use_fmp/horizons/strategy_ids/theta_c) -- crosses the process
    boundary over an anonymous pipe, never argv or the environment,
    mirroring ``data.robinhood_login.start_login``'s credentials pipe.
    """
    global _active_job_id
    with _jobs_lock:
        if _active_job_id is not None:
            existing = _jobs.get(_active_job_id)
            if existing is not None and existing.state == "running":
                return None
            _active_job_id = None
        job_id = f"backfill-{uuid.uuid4().hex[:8]}"
        job = BackfillJobState(job_id=job_id)
        _active_job_id = job_id

    try:
        params_r, params_w = os.pipe()
        events_r, events_w = os.pipe()
        try:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "ml.forecast_backfill_worker",
                    "--params-fd",
                    str(params_r),
                    "--events-fd",
                    str(events_w),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                pass_fds=(params_r, events_w),
                start_new_session=True,  # own process group -> os.killpg works
                cwd=str(_REPO_ROOT),
            )
        finally:
            # The child has its own copies (post-fork) of every fd it needs;
            # the parent must close ITS copies of the ends it doesn't use
            # itself, or they leak for the life of this process.
            os.close(params_r)
            os.close(events_w)

        job._process = proc

        with os.fdopen(params_w, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(params) + "\n")
        # Writing then closing (the `with` block above) sends EOF to the
        # child's read end after exactly one line.
    except Exception:
        _clear_active_job(job_id)
        raise

    with _jobs_lock:
        _jobs[job_id] = job

    threading.Thread(target=_drain_events, args=(events_r, job), daemon=True).start()
    threading.Thread(target=_enforce_deadline, args=(job,), daemon=True).start()
    return job


def get_job_state(job_id: str) -> Optional[BackfillJobState]:
    with _jobs_lock:
        return _jobs.get(job_id)


def get_active_job_id() -> Optional[str]:
    """The ``job_id`` of the currently in-flight run, or ``None``. Used by
    ``api/pilots_api.py`` to build a structured 409 body (carrying the
    existing job's id, so a client can poll it instead of hitting a dead
    end) when ``start_job()`` returns ``None`` because a run is already in
    progress."""
    with _jobs_lock:
        return _active_job_id


def cancel_job(job_id: str) -> bool:
    """Returns True once the process is confirmed stopped. Raises KeyError
    if the job id is unknown."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise KeyError(job_id)
    with job._lock:
        if job.state != "running":
            return True
        _kill_process_group(job._process)
        job.state = "cancelled"
        job.error = "Forecast backfill run was cancelled."
        job.error_type = "cancelled"
    _clear_active_job(job_id)
    return True


def serialize_job(job: BackfillJobState) -> Dict[str, Any]:
    """JSON-safe status payload for both
    ``POST /pilots/forecast_backfill/run`` (the initial, ``running`` state)
    and ``GET /pilots/forecast_backfill/status/{job_id}`` (every poll after)."""
    with job._lock:
        return {
            "job_id": job.job_id,
            "state": job.state,
            "phase": job.phase,
            "step": job.step,
            "total_steps": job.total_steps,
            "error": job.error,
            "error_type": job.error_type,
            "summary": job.summary,
            "sample_rows": job.sample_rows,
            "seconds_remaining": round(job.seconds_remaining, 1),
        }


__all__ = [
    "BackfillJobState",
    "BackfillPhase",
    "BackfillState",
    "start_job",
    "get_job_state",
    "get_active_job_id",
    "cancel_job",
    "serialize_job",
]
