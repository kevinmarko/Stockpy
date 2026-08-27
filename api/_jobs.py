"""
api/_jobs.py
============
Job execution adapter over gui.orchestrator_runner for non-orchestrator
background process execution (preflight, pytest, validation, verify, gravity,
advisory, orchestrator).

``gui.orchestrator_runner.RunHandle`` exposes only ``is_running()`` and
``returncode()`` — it has no ``status``/``exit_code()`` of its own. Job status
strings (``running``/``success``/``failed``/``cancelled``/``unknown``) are
derived here rather than invented on the handle, so this module is the single
place that has to stay in sync with ``RunHandle``'s real surface.
"""

from __future__ import annotations

import enum
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from gui.orchestrator_runner import (
    RunHandle,
    launch_advisory_main,
    launch_gravity_audit,
    launch_manifest_command,
    launch_orchestrator,
    launch_preflight,
    launch_pytest,
    launch_train_lgbm,
    launch_train_meta_labelers,
    launch_validation_run,
    launch_verify,
    stop_run_detailed,
)
from settings import settings

logger = logging.getLogger(__name__)


class JobConflictError(RuntimeError):
    def __init__(self, message: str, existing_job_id: str, existing_job_type: str, existing_command_name: str | None = None):
        super().__init__(message)
        self.existing_job_id = existing_job_id
        self.existing_job_type = existing_job_type
        self.existing_command_name = existing_command_name


class JobType(str, enum.Enum):
    PREFLIGHT = "preflight"
    PYTEST = "pytest"
    VALIDATION = "validation"
    VERIFY = "verify"
    GRAVITY = "gravity"
    ADVISORY = "advisory"
    ORCHESTRATOR = "orchestrator"
    COMMAND = "command"
    TRAIN_META = "train_meta"
    TRAIN_LGBM = "train_lgbm"


def job_status(handle: RunHandle, *, cancelled: bool) -> str:
    """Derive a status string from RunHandle's real surface (is_running/returncode).

    Never reads a ``.status``/``.exit_code()`` attribute — RunHandle has neither.
    """
    if cancelled:
        return "cancelled"
    if handle.is_running():
        return "running"
    rc = handle.returncode()
    if rc is None:
        return "unknown"
    return "success" if rc == 0 else "failed"


@dataclass
class JobRecord:
    job_id: str
    job_type: JobType
    handle: Optional[RunHandle] = None
    cancelled: bool = False
    command_name: Optional[str] = None
    single_flight_key: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def cancellable(self) -> bool:
        # Only a genuine local subprocess can be signaled. A daemon-hosted
        # cycle (launch_orchestrator's ORCHESTRATOR_DAEMON_ENABLED fast path)
        # has no local PID — stop_run() itself refuses to touch it (returns
        # False rather than raising), so don't offer Cancel for it at all.
        if self.handle is None:
            return True
        return getattr(self.handle, "backend", "subprocess") == "subprocess"

    def status(self) -> str:
        if self.handle is None:
            return "cancelled" if self.cancelled else "starting"
        return job_status(self.handle, cancelled=self.cancelled)

    def exit_code(self) -> Optional[int]:
        if self.handle is None:
            return None
        return self.handle.returncode()

    @property
    def is_running(self) -> bool:
        """Null-safe replacement for ``rec.handle.is_running()`` -- a job in the
        "starting" window (handle not yet assigned by start_job's post-lock launch
        step) is not running yet, not an error condition. Every read endpoint in
        api/control_api.py (GET /jobs, GET /jobs/{id}, GET /jobs/{id}/stream) MUST
        go through this property instead of touching ``.handle`` directly -- a
        direct ``rec.handle.is_running()`` call raises AttributeError on None and
        is not caught by install_redacting_exception_handler (HTTPException-only),
        surfacing as a raw 500 to a client polling mid-launch."""
        if self.handle is None:
            return False
        return self.handle.is_running()


class JobManager:
    """Manages active and historical background jobs launched via HTTP."""

    def __init__(self) -> None:
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        self._max_jobs = 100

    def _cleanup_jobs(self) -> None:
        if len(self._jobs) <= self._max_jobs:
            return
            
        sorted_jobs = sorted(self._jobs.values(), key=lambda r: r.created_at)
        to_remove = []
        for r in sorted_jobs:
            if len(self._jobs) - len(to_remove) <= self._max_jobs:
                break
            if r.handle is not None and not r.handle.is_running():
                to_remove.append(r.job_id)
                
        for job_id in to_remove:
            del self._jobs[job_id]

    def start_job(self, job_type: JobType, params: Optional[Dict[str, Any]] = None) -> JobRecord:
        """Launch a new job of the specified type. Raises ValueError on bad
        params or an unsupported type; the caller maps that to an HTTP 400.
        Raises RuntimeError when a job of the same type (or, for the training
        job types, the same single-flight group) is already running; the
        caller maps that to an HTTP 409 (single-flight per job type, widened
        to per-``single_flight_key`` group for TRAIN_LGBM/TRAIN_META so the
        two training job types can't run concurrently against the same
        model-registry write path)."""
        params = params or {}
        single_flight_key = "train" if job_type in (JobType.TRAIN_META, JobType.TRAIN_LGBM) else None
        command_name = params.get("command") if job_type == JobType.COMMAND else None
        
        # Validation
        if job_type == JobType.VALIDATION:
            strategies = params.get("strategies")
            start = params.get("start")
            end = params.get("end")
            if isinstance(strategies, str):
                strategies = [s.strip() for s in strategies.split(",") if s.strip()]
            if not strategies or not isinstance(strategies, list) or not start or not end:
                raise ValueError(
                    "VALIDATION job requires params: strategies (list[str] or "
                    "comma-separated string), start (YYYY-MM-DD), end (YYYY-MM-DD)"
                )
            if not isinstance(start, str) or not isinstance(end, str):
                raise ValueError("VALIDATION job start and end must be YYYY-MM-DD strings")
            try:
                date.fromisoformat(start.strip())
                date.fromisoformat(end.strip())
            except (ValueError, TypeError) as err:
                raise ValueError(f"Invalid date format (expected YYYY-MM-DD): {err}") from err
        elif job_type == JobType.COMMAND:
            if not settings.COMMAND_EXECUTION_ENABLED:
                raise PermissionError("COMMAND_EXECUTION_ENABLED is False.")
            subcommand_name = params.get("subcommand")
            args = params.get("args") or []
            confirm = bool(params.get("confirm", False))
            if not command_name or not isinstance(command_name, str):
                raise ValueError("COMMAND job requires params: command (str)")
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                raise ValueError("COMMAND job requires params: args (list[str])")
        elif job_type not in (JobType.PREFLIGHT, JobType.PYTEST, JobType.VERIFY, JobType.GRAVITY, 
                              JobType.ADVISORY, JobType.ORCHESTRATOR, JobType.TRAIN_LGBM, JobType.TRAIN_META):
            raise ValueError(f"Unsupported job type: {job_type}")

        with self._lock:
            job_id = f"job-{uuid.uuid4().hex[:8]}"

            for rec in self._jobs.values():
                is_active = (rec.handle is None) or rec.handle.is_running()
                if not is_active:
                    continue
                if job_type == JobType.COMMAND:
                    if rec.job_type == JobType.COMMAND and rec.command_name == command_name:
                        raise JobConflictError(
                            f"Command '{command_name}' is already running (ID: {rec.job_id})",
                            existing_job_id=rec.job_id,
                            existing_job_type=rec.job_type.value,
                            existing_command_name=rec.command_name,
                        )
                elif (rec.single_flight_key or rec.job_type.value) == (single_flight_key or job_type.value):
                    raise JobConflictError(
                        f"Job of type '{job_type.value}' conflicts with already-running "
                        f"job '{rec.job_type.value}' (ID: {rec.job_id})",
                        existing_job_id=rec.job_id,
                        existing_job_type=rec.job_type.value,
                        existing_command_name=rec.command_name,
                    )

            rec = JobRecord(
                job_id=job_id,
                job_type=job_type,
                handle=None,
                command_name=command_name,
                single_flight_key=single_flight_key,
            )
            self._jobs[job_id] = rec
            self._cleanup_jobs()

        try:
            if job_type == JobType.PREFLIGHT:
                handle = launch_preflight()
            elif job_type == JobType.PYTEST:
                handle = launch_pytest()
            elif job_type == JobType.VALIDATION:
                handle = launch_validation_run(strategies, start, end)
            elif job_type == JobType.VERIFY:
                handle = launch_verify()
            elif job_type == JobType.GRAVITY:
                handle = launch_gravity_audit()
            elif job_type == JobType.ADVISORY:
                handle = launch_advisory_main(
                    refresh_account=bool(params.get("refresh_account", False))
                )
            elif job_type == JobType.ORCHESTRATOR:
                handle = launch_orchestrator(
                    dry_run=bool(params.get("dry_run", False)),
                    refresh_account=bool(params.get("refresh_account", False)),
                )
            elif job_type == JobType.COMMAND:
                handle = launch_manifest_command(job_id, command_name, subcommand_name, args, confirm=confirm)
            elif job_type == JobType.TRAIN_LGBM:
                handle = launch_train_lgbm()
            elif job_type == JobType.TRAIN_META:
                handle = launch_train_meta_labelers(signal=params.get("signal"))
        except Exception:
            with self._lock:
                self._jobs.pop(job_id, None)
            raise

        with rec._lock:
            rec.handle = handle
            if rec.cancelled and getattr(handle, "backend", "subprocess") == "subprocess":
                stop_run_detailed(handle)
                
        return rec

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        return self._jobs.get(job_id)

    def list_jobs(self) -> List[JobRecord]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda r: r.created_at, reverse=True)

    def cancel_job(self, job_id: str) -> bool:
        """Returns True once the process is confirmed stopped -- including
        when it was already stopped by an earlier cancel_job call on this
        same job (an honest "yes, it's cancelled", not a fresh kill). Raises
        KeyError if the job id is unknown (-> 404) and ValueError if the job
        type isn't cancellable (-> 400). Returns False, never raises, in two
        distinct cases the caller can't tell apart from this bool alone but
        neither of which should ever claim a success it didn't achieve: the
        job had already finished on its own (never cancelled), or a
        confirmed-stop failed (rare — stop_run escalates SIGTERM->SIGKILL).

        Uses stop_run_detailed() (not the plain stop_run()) as the SOLE
        liveness check -- no separate is_running() pre-check, and no
        post-hoc ``returncode() == 0`` guess -- so there is only one point
        where "is it alive, and did WE kill it" gets decided, atomically
        with the kill attempt itself. stop_run_detailed()'s already_stopped
        flag is what distinguishes "it finished on its own" (any exit code,
        not just 0) from "it was already cancelled by an earlier call",
        which neither a bare stop_run() bool nor a returncode heuristic can
        do reliably -- a process racing to a nonzero ("failed") exit right
        as cancel_job examines it is exactly as much "not a cancellation" as
        one racing to a zero ("success") exit.
        """
        rec = self.get_job(job_id)
        if rec is None:
            raise KeyError(job_id)
        if not rec.cancellable:
            raise ValueError(
                f"Job '{job_id}' of type '{rec.job_type.value}' has no local "
                "process to cancel (daemon-hosted run — it will run to completion)"
            )
        with rec._lock:
            if rec.handle is None:
                rec.cancelled = True
                return True
                
            outcome = stop_run_detailed(rec.handle)
            if outcome.already_stopped:
                # Nothing was signalled by this call -- either the job
                # finished on its own (rec.cancelled is still False: report
                # False, honestly nothing to cancel) or an earlier call
                # already cancelled it (rec.cancelled is already True:
                # report True, it IS cancelled, just not by this call).
                # Never flip rec.cancelled here -- that would be exactly
                # the terminal-status-clobbering bug this guard exists to
                # prevent.
                return rec.cancelled
            if outcome.stopped:
                rec.cancelled = True
            return outcome.stopped


job_manager = JobManager()
