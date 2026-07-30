"""
api/_jobs.py
============
Job execution adapter over gui.orchestrator_runner for non-orchestrator
background process execution (preflight, pytest, validation, verify, gravity).
"""

from __future__ import annotations

import enum
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from gui.orchestrator_runner import (
    ADVISORY_LOG_PATH,
    GRAVITY_LOG_PATH,
    PYTEST_LOG_PATH,
    RETRY_LOG_PATH,
    RUN_LOG_PATH,
    VALIDATION_LOG_PATH,
    VERIFY_LOG_PATH,
    RunHandle,
    launch_advisory_run,
    launch_gravity_audit,
    launch_orchestrator,
    launch_pytest,
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


@dataclass
class JobRecord:
    job_id: str
    job_type: JobType
    handle: RunHandle
    cancellable: bool


class JobManager:
    """Manages active and historical background jobs launched via HTTP."""

    def __init__(self) -> None:
        self._jobs: Dict[str, JobRecord] = {}

    def start_job(self, job_type: JobType, params: Optional[dict] = None) -> JobRecord:
        """Launch a new job of the specified type."""
        # Check single-flight for same job_type if already running
        for j in self._jobs.values():
            if j.job_type == job_type and j.handle.is_running():
                raise ValueError(f"Job of type '{job_type.value}' is already running (ID: {j.job_id})")

        job_id = f"job-{uuid.uuid4().hex[:8]}"

        if job_type == JobType.PREFLIGHT:
            handle = launch_verify()  # verify includes preflight checks
            cancellable = True
        elif job_type == JobType.PYTEST:
            handle = launch_pytest()
            cancellable = True
        elif job_type == JobType.VALIDATION:
            handle = launch_validation_run()
            cancellable = True
        elif job_type == JobType.VERIFY:
            handle = launch_verify()
            cancellable = True
        elif job_type == JobType.GRAVITY:
            handle = launch_gravity_audit()
            cancellable = True
        elif job_type == JobType.ADVISORY:
            handle = launch_advisory_run()
            cancellable = True
        elif job_type == JobType.ORCHESTRATOR:
            handle = launch_orchestrator()
            cancellable = False  # Orchestrator is managed via kill switch / daemon
        else:
            raise ValueError(f"Unsupported job type: {job_type}")

        rec = JobRecord(job_id=job_id, job_type=job_type, handle=handle, cancellable=cancellable)
        self._jobs[job_id] = rec
        return rec

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> bool:
        rec = self.get_job(job_id)
        if not rec:
            return False
        if not rec.cancellable:
            raise ValueError(f"Job '{job_id}' of type '{rec.job_type.value}' cannot be cancelled directly")
        stop_run(rec.handle)
        return True


job_manager = JobManager()
