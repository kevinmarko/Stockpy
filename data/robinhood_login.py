"""Parent-side launcher for the isolated Robinhood device-approval login
worker (:mod:`data.robinhood_login_worker`).

Why a fresh subprocess per attempt rather than a persistent worker pool
(contrast :mod:`cnn_lstm_process_pool`, which *does* now kill and replace a
timed-out worker -- see that module's docstring): even a pool that kills on
timeout is still the wrong shape here. A login attempt is a one-shot,
side-effecting operation (it may leave a real Robinhood session
authenticated) that needs its own dedicated, immediately-killable process
and a SIGTERM-then-SIGKILL grace period around that one specific attempt --
not a warm process reused across many calls. This follows
``shared.orchestrator_runner``'s detached-``Popen`` + SIGTERM-then-SIGKILL
pattern instead, one fresh process per attempt.

Credentials cross the process boundary over an anonymous pipe only — never
argv (visible via ``ps``) or the environment (visible via ``/proc``/``ps -E``).
Never logs credential values; job records store phase/state/error-code only.

Two ways to use a login attempt:
  - ``start_login()`` / ``get_login_state()`` / ``cancel_login()`` — the
    non-blocking job primitive the Pilots API's ``/brokerage/connect`` and
    ``/brokerage/refresh`` poll against.
  - ``login_blocking()`` — starts a job and blocks the calling thread until
    it reaches a terminal state, raising on anything but success. Used by
    :func:`data.robinhood_portfolio._fetch_live_snapshot` to delegate an
    unattended-context login to the isolated worker while still presenting
    a simple synchronous call to every existing caller of
    ``fetch_account_snapshot()``.
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
from typing import Literal, Optional

from settings import settings

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent

LoginPhase = Literal[
    "starting", "authenticating", "awaiting_approval", "verifying", "fetching_snapshot",
    "fetching_orders", "done"
]
LoginState = Literal["running", "succeeded", "failed", "timeout", "cancelled"]
LoginMode = Literal["connect", "refresh"]


@dataclass
class LoginJobState:
    job_id: str
    mode: LoginMode
    phase: LoginPhase = "starting"
    state: LoginState = "running"
    error_code: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    deadline_at: float = field(default_factory=lambda: time.time() + settings.RH_LOGIN_DEADLINE_SECONDS)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _process: Optional[subprocess.Popen] = field(default=None, repr=False)

    @property
    def seconds_remaining(self) -> float:
        return max(0.0, self.deadline_at - time.time())


_jobs: dict[str, LoginJobState] = {}
_jobs_lock = threading.Lock()


def _drain_events(events_r: int, job: LoginJobState) -> None:
    """Background thread: read NDJSON events off the child's events pipe and
    update the job record's phase/terminal state. Runs until EOF (the child
    closed its write end, whether by exiting cleanly or being killed)."""
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
                    if obj.get("event") == "phase":
                        job.phase = obj.get("phase", job.phase)
                    elif obj.get("event") == "result":
                        job.phase = "done"
                        if obj.get("ok"):
                            job.state = "succeeded"
                        else:
                            job.state = "failed"
                            job.error_code = obj.get("code", "auth_failed")
    except Exception as exc:  # noqa: BLE001 - the deadline thread is the safety net either way
        logger.debug("robinhood_login: event-drain thread ended: %s", exc)


def _enforce_deadline(job: LoginJobState) -> None:
    """Background thread: waits out the job's deadline, then kills the
    process group if it's still running. A confirmed-not-yet-started child
    (no 'started' event within RH_LOGIN_STARTUP_SECONDS) is killed early and
    reported as a distinct error code, rather than waited out for the full
    deadline."""
    startup_deadline = job.started_at + settings.RH_LOGIN_STARTUP_SECONDS
    while time.time() < startup_deadline:
        with job._lock:
            if job.state != "running":
                return
            if job.phase != "starting":
                break  # got a 'started' (or later) event -- normal path below
        time.sleep(0.2)
    else:
        with job._lock:
            if job.state == "running" and job.phase == "starting":
                _kill_process_group(job._process)
                job.state = "failed"
                job.error_code = "child_start_failed"
                return

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
        job.error_code = "timeout"


def _kill_process_group(proc: Optional[subprocess.Popen]) -> None:
    """SIGTERM the worker's process group, wait out RH_LOGIN_GRACE_SECONDS,
    then SIGKILL if it's still alive. Never raises -- a process that's
    already exited (ProcessLookupError) is the success case, not an error."""
    if proc is None or proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception as exc:  # noqa: BLE001 - best-effort escalation below regardless
        logger.debug("robinhood_login: SIGTERM failed: %s", exc)
    try:
        proc.wait(timeout=settings.RH_LOGIN_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception as exc:  # noqa: BLE001 - nothing more we can do
        logger.warning("robinhood_login: SIGKILL failed: %s", exc)
    try:
        proc.wait(timeout=settings.RH_LOGIN_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning("robinhood_login: worker process did not exit after SIGKILL.")


def start_login(mode: LoginMode, *, username: str = "", password: str = "") -> LoginJobState:
    """Launch one login attempt as an isolated, killable subprocess and
    return immediately with its (running) job state. Poll via
    ``get_login_state(job.job_id)``.

    ``username``/``password`` are the CANDIDATE credentials for
    ``mode="connect"`` (a brokerage-connect verification, never yet
    persisted to ``.env``) — passed to the child over an anonymous pipe,
    never argv or the environment. Leave both empty for ``mode="refresh"``,
    where the worker reads the already-configured ``RH_USERNAME``/
    ``RH_PASSWORD`` off the settings singleton itself.
    """
    job_id = f"rhlogin-{uuid.uuid4().hex[:8]}"
    job = LoginJobState(job_id=job_id, mode=mode)

    creds_r, creds_w = os.pipe()
    events_r, events_w = os.pipe()
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "data.robinhood_login_worker",
                "--mode",
                mode,
                "--creds-fd",
                str(creds_r),
                "--events-fd",
                str(events_w),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            pass_fds=(creds_r, events_w),
            start_new_session=True,  # own process group -> os.killpg works
            cwd=str(_REPO_ROOT),
        )
    finally:
        # The child has its own copies (post-fork) of every fd it needs;
        # the parent must close ITS copies of the ends it doesn't use
        # itself, or they leak for the life of this process.
        os.close(creds_r)
        os.close(events_w)

    job._process = proc

    with os.fdopen(creds_w, "w", encoding="utf-8") as fh:
        if username and password:
            fh.write(json.dumps({"username": username, "password": password}) + "\n")
        else:
            fh.write("\n")
    # Writing then closing (the `with` block above) sends EOF to the child's
    # read end after exactly one line, whether or not credentials were sent.

    with _jobs_lock:
        _jobs[job_id] = job

    threading.Thread(target=_drain_events, args=(events_r, job), daemon=True).start()
    threading.Thread(target=_enforce_deadline, args=(job,), daemon=True).start()
    return job


def get_login_state(job_id: str) -> Optional[LoginJobState]:
    with _jobs_lock:
        return _jobs.get(job_id)


def cancel_login(job_id: str) -> bool:
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
        job.error_code = "cancelled"
        return True


class RobinhoodLoginTimeout(RuntimeError):
    """Raised by login_blocking() when the attempt hit its deadline without
    a human approving in time."""


class RobinhoodLoginFailed(RuntimeError):
    """Raised by login_blocking() for any other non-success terminal state
    (bad credentials, an unsupported SMS/email challenge, cancellation, or
    the child failing to start)."""


def login_blocking(mode: LoginMode, *, username: str = "", password: str = "", poll_interval: float = 0.5) -> None:
    """Start a login attempt and block the calling thread until it reaches a
    terminal state. Raises on anything but success — callers that need a
    result object for an HTTP response should use ``start_login`` +
    ``get_login_state`` instead; this is for the synchronous internal
    callers (``data.robinhood_portfolio._fetch_live_snapshot``,
    ``main.py --refresh-account``) that already expect a plain blocking
    call and just need it to no longer be able to hang forever.
    """
    job = start_login(mode, username=username, password=password)
    while True:
        with job._lock:
            state = job.state
            error_code = job.error_code
        if state == "succeeded":
            return
        if state == "timeout":
            raise RobinhoodLoginTimeout(
                f"Robinhood login did not receive approval within "
                f"{settings.RH_LOGIN_DEADLINE_SECONDS}s."
            )
        if state != "running":
            raise RobinhoodLoginFailed(f"Robinhood login failed: {error_code or state}")
        time.sleep(poll_interval)
