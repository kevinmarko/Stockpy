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
    stop_run,
)
from settings import settings

logger = logging.getLogger(__name__)


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
    if handle.is_running():
        return "running"
    if cancelled:
        return "cancelled"
    rc = handle.returncode()
    if rc is None:
        return "unknown"
    return "success" if rc == 0 else "failed"


@dataclass
class JobRecord:
    job_id: str
    job_type: JobType
    handle: RunHandle
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
        return getattr(self.handle, "backend", "subprocess") == "subprocess"

    def status(self) -> str:
        return job_status(self.handle, cancelled=self.cancelled)

    def exit_code(self) -> Optional[int]:
        return self.handle.returncode()


class JobManager:
    """Manages active and historical background jobs launched via HTTP."""

    def __init__(self) -> None:
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = threading.Lock()

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

        # TRAIN_LGBM and TRAIN_META both write to the same ML registry, so
        # they share one single-flight group ("train") rather than each
        # having its own independent slot -- two different job TYPES must
        # still conflict with each other. Every other job type keeps its
        # existing type-scoped single-flight behavior (key=None).
        single_flight_key = "train" if job_type in (JobType.TRAIN_META, JobType.TRAIN_LGBM) else None

        with self._lock:
            job_id = f"job-{uuid.uuid4().hex[:8]}"
            command_name = params.get("command") if job_type == JobType.COMMAND else None

            for rec in self._jobs.values():
                if not rec.handle.is_running():
                    continue
                if job_type == JobType.COMMAND:
                    if rec.job_type == JobType.COMMAND and rec.command_name == command_name:
                        raise RuntimeError(
                            f"Command '{command_name}' is already running (ID: {rec.job_id})"
                        )
                elif (rec.single_flight_key or rec.job_type.value) == (single_flight_key or job_type.value):
                    raise RuntimeError(
                        f"Job of type '{job_type.value}' conflicts with already-running "
                        f"job '{rec.job_type.value}' (ID: {rec.job_id})"
                    )

            if job_type == JobType.PREFLIGHT:
                handle = launch_preflight()
            elif job_type == JobType.PYTEST:
                handle = launch_pytest()
            elif job_type == JobType.VALIDATION:
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
                if not settings.COMMAND_EXECUTION_ENABLED:
                    raise PermissionError("COMMAND_EXECUTION_ENABLED is False.")
                subcommand_name = params.get("subcommand")
                args = params.get("args") or []
                confirm = bool(params.get("confirm", False))
                if not command_name or not isinstance(command_name, str):
                    raise ValueError("COMMAND job requires params: command (str)")
                if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                    raise ValueError("COMMAND job requires params: args (list[str])")
                handle = launch_manifest_command(job_id, command_name, subcommand_name, args, confirm=confirm)
            elif job_type == JobType.TRAIN_LGBM:
                handle = launch_train_lgbm()
            elif job_type == JobType.TRAIN_META:
                signal = params.get("signal")
                handle = launch_train_meta_labelers(signal=signal)
            else:
                raise ValueError(f"Unsupported job type: {job_type}")

            rec = JobRecord(
                job_id=job_id,
                job_type=job_type,
                handle=handle,
                command_name=command_name,
                single_flight_key=single_flight_key,
            )
            self._jobs[job_id] = rec
            return rec

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> bool:
        """Returns True once the process is confirmed stopped. Raises
        KeyError if the job id is unknown (-> 404) and ValueError if the job
        type isn't cancellable (-> 400). A confirmed-stop failure (rare —
        stop_run() escalates SIGTERM->SIGKILL) surfaces as a plain False
        rather than either exception, so the caller can report it honestly
        instead of claiming success it didn't achieve."""
        rec = self.get_job(job_id)
        if rec is None:
            raise KeyError(job_id)
        if not rec.cancellable:
            raise ValueError(
                f"Job '{job_id}' of type '{rec.job_type.value}' has no local "
                "process to cancel (daemon-hosted run — it will run to completion)"
            )
        with rec._lock:
            if not rec.handle.is_running():
                return False
            stopped = stop_run(rec.handle)
            if stopped:
                rec.cancelled = True
            return stopped


job_manager = JobManager()
