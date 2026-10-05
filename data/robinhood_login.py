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

Single-flight (2026-09)
-----------------------
Every login attempt sends the operator a device-approval push. Two callers
in one process -- e.g. the daemon's scheduled/auto refresh and a webapp
``POST /brokerage/refresh`` -- must never launch two workers (two prompts,
two competing sessions). ``start_login`` therefore holds a module lock
across "is a job already running?" + ``Popen`` + registration, and allows
at most ONE non-terminal job per process:

  - ``refresh`` requested while a ``refresh`` is running: the RUNNING job is
    returned (the caller joins it; no new worker, no second prompt).
    ``login_blocking("refresh")`` then simply waits on that existing job.
  - Anything else while a job is running (``connect`` while anything runs,
    or ``refresh`` while a ``connect`` runs): raises
    :class:`RobinhoodLoginInProgress` carrying the running job. A
    ``connect`` carries candidate credentials that a running job was not
    started with, and a ``connect`` job does not fetch an account snapshot,
    so joining across modes would silently answer a different question.
    The Pilots API maps this to HTTP 409.

Cross-process single-flight (2026-09 follow-up)
-----------------------------------------------
Logins can also start from OTHER processes: any process whose
``fetch_account_snapshot`` tier-3 auto-refresh fires (the standalone Data /
Metrics APIs, ``investyo_mcp_server``, ``main.py``) when
``ROBINHOOD_AUTO_REFRESH_ENABLED=true``. So, after the in-process check and
before launching a worker, ``start_login`` also takes an OS advisory lock:
``fcntl.flock(LOCK_EX | LOCK_NB)`` on ``<OUTPUT_DIR>/robinhood_login.lock``.
The launching process holds it until its job reaches a terminal state
(released by the job's deadline thread, the one thread that always runs
until the job is terminal). If another process holds it, ``start_login``
raises :class:`RobinhoodLoginInProgress` with ``.job=None`` (there is no
in-process job to join) and ``.owner`` read from the diagnostic sidecar
``<OUTPUT_DIR>/robinhood_login_owner.json`` (pid, job_id, mode, started_at
-- never credentials).

No stale locks: an ``flock`` lock belongs to the open file description, so
the kernel drops it when the holding process exits or is killed (even by
SIGKILL). The lock fd is not inherited by the worker (Python fds are
non-inheritable and ``Popen`` passes only the two pipe fds), so a worker
orphaned by its parent's death does NOT keep the lock -- that orphan can
still finish its own login (at most one extra prompt), a disclosed edge.
The sidecar is removed on a clean release; one left behind by a killed
process is harmless (it is only read while the lock is held, and the next
holder overwrites it).

Without ``fcntl`` (non-POSIX), or when the lock file cannot be opened, the
guard falls back to per-process only, with a one-time warning.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from settings import settings

try:  # POSIX only; see the module docstring's cross-process section.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    _fcntl = None  # type: ignore[assignment]

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
    #: fd holding the cross-process flock for this job, or None (released,
    #: or never taken because the fallback is per-process only).
    _xlock_fd: Optional[int] = field(default=None, repr=False)

    @property
    def seconds_remaining(self) -> float:
        return max(0.0, self.deadline_at - time.time())


_jobs: dict[str, LoginJobState] = {}
_jobs_lock = threading.Lock()
#: Serializes start_login's check-running + Popen + register sequence so two
#: near-simultaneous callers can never both observe "nothing running" and
#: both launch a worker. Separate from _jobs_lock so status polls never wait
#: on a Popen.
_start_lock = threading.Lock()
#: The most recently started job -- the only one that can still be running,
#: because start_login refuses to start another while it is.
_active_job: Optional[LoginJobState] = None


class RobinhoodLoginTimeout(RuntimeError):
    """Raised by login_blocking() when the attempt hit its deadline without
    a human approving in time."""


class RobinhoodLoginFailed(RuntimeError):
    """Raised by login_blocking() for any other non-success terminal state
    (bad credentials, an unsupported SMS/email challenge, cancellation, or
    the child failing to start)."""


class RobinhoodLoginInProgress(RobinhoodLoginFailed):
    """Raised by start_login() (and so login_blocking()) when a login job
    is already running and the request cannot join it -- see the module
    docstring's single-flight sections. ``.job`` is the running job in THIS
    process, or ``None`` when the running login belongs to another process;
    ``.owner`` is then that process's diagnostic sidecar (``pid``,
    ``job_id``, ``mode``, ``started_at``; ``{}`` if unreadable)."""

    def __init__(
        self,
        job: Optional[LoginJobState],
        requested_mode: str,
        owner: Optional[dict] = None,
    ) -> None:
        self.job = job
        self.requested_mode = requested_mode
        self.owner = dict(owner or {})
        if job is not None:
            running = f"({job.mode}, job {job.job_id})"
        else:
            running = f"in another process ({describe_owner(self.owner)})"
        super().__init__(
            f"A Robinhood login {running} is already in progress; not "
            f"starting a '{requested_mode}' login. Approve or cancel it first."
        )


def describe_owner(owner: dict) -> str:
    """Human-readable summary of a lock-owner sidecar. Only the four
    diagnostic fields are ever read -- never anything else."""
    parts = [
        f"{key} {owner[key]}"
        for key in ("pid", "job_id", "mode", "started_at")
        if owner.get(key) is not None
    ]
    return ", ".join(parts) or "owner unknown"


# ---------------------------------------------------------------------------
# Cross-process advisory lock (see the module docstring)
# ---------------------------------------------------------------------------

LOCK_FILENAME = "robinhood_login.lock"
OWNER_FILENAME = "robinhood_login_owner.json"
#: Test seam: when set, the lock + sidecar live here instead of OUTPUT_DIR
#: (the root conftest points it at a per-test temp dir so xdist workers never
#: contend on one real lock file).
_lock_dir_override: Optional[Path] = None
_xlock_mutex = threading.Lock()
#: The job in THIS process currently holding the cross-process lock (a
#: second flock on a new fd would conflict with our own), or None.
_xlock_holder: Optional[LoginJobState] = None
_warned_no_xlock = False


class _CrossProcessLockHeld(Exception):
    def __init__(self, owner: dict) -> None:
        super().__init__("robinhood login lock held by another process")
        self.owner = owner


def _lock_dir() -> Path:
    if _lock_dir_override is not None:
        return Path(_lock_dir_override)
    return Path(settings.OUTPUT_DIR)


def _warn_no_xlock_once(reason: str) -> None:
    global _warned_no_xlock
    if not _warned_no_xlock:
        _warned_no_xlock = True
        logger.warning(
            "robinhood_login: cross-process login lock unavailable (%s); "
            "single-flight is per process only.", reason,
        )


def read_lock_owner() -> dict:
    """The lock owner's diagnostic sidecar, or {} if missing/unreadable.
    Only the four diagnostic fields are kept."""
    try:
        raw = json.loads((_lock_dir() / OWNER_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: raw[k] for k in ("pid", "job_id", "mode", "started_at") if k in raw}


def _write_owner(job: LoginJobState) -> None:
    """Atomic (temp + os.replace) best-effort write of the sidecar."""
    directory = _lock_dir()
    target = directory / OWNER_FILENAME
    tmp = directory / f".{OWNER_FILENAME}.{os.getpid()}.tmp"
    payload = {
        "pid": os.getpid(),
        "job_id": job.job_id,
        "mode": job.mode,
        "started_at": datetime.fromtimestamp(job.started_at, timezone.utc).isoformat(),
    }
    try:
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, target)
    except OSError as exc:
        logger.debug("robinhood_login: owner sidecar write failed: %s", exc)
        try:
            tmp.unlink()
        except OSError:
            pass


def _acquire_cross_process_lock() -> Optional[int]:
    """Take the cross-process flock without blocking. Returns the held fd,
    or None when the lock is unavailable here (no fcntl, or the lock file
    cannot be opened/locked) -- per-process single-flight only, warned
    once. Raises _CrossProcessLockHeld when ANOTHER process holds it."""
    if _fcntl is None:
        _warn_no_xlock_once("fcntl is not available on this platform")
        return None
    directory = _lock_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(directory / LOCK_FILENAME), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        _warn_no_xlock_once(f"cannot open lock file in {directory}: {exc}")
        return None
    try:
        _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise _CrossProcessLockHeld(read_lock_owner()) from None
    except OSError as exc:
        os.close(fd)
        _warn_no_xlock_once(f"flock failed: {exc}")
        return None
    return fd


def _release_cross_process_lock(job: Optional[LoginJobState]) -> None:
    """Release ``job``'s cross-process lock, if it still holds one.
    Idempotent, never raises. Removes the sidecar (only if it is still
    ours) BEFORE unlocking, so it can never delete the next holder's."""
    global _xlock_holder
    if job is None:
        return
    with _xlock_mutex:
        fd = job._xlock_fd
        job._xlock_fd = None
        if _xlock_holder is job:
            _xlock_holder = None
    if fd is None:
        return
    try:
        if read_lock_owner().get("job_id") == job.job_id:
            (_lock_dir() / OWNER_FILENAME).unlink()
    except OSError:
        pass
    try:
        if _fcntl is not None:
            _fcntl.flock(fd, _fcntl.LOCK_UN)
    except OSError as exc:  # closing the fd releases it anyway
        logger.debug("robinhood_login: flock unlock failed: %s", exc)
    try:
        os.close(fd)
    except OSError:
        pass


def _running_job() -> Optional[LoginJobState]:
    """The in-flight job, or None. Caller must hold _start_lock."""
    job = _active_job
    if job is None:
        return None
    with job._lock:
        return job if job.state == "running" else None


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
    deadline.

    This thread runs until the job is terminal (whoever made it terminal:
    the event drain, cancel_login, or this thread), so it is the job's
    finalisation point: the cross-process lock is released here."""
    try:
        _enforce_deadline_until_terminal(job)
    finally:
        _release_cross_process_lock(job)


def _enforce_deadline_until_terminal(job: LoginJobState) -> None:
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

    Single-flight: while a job is running, a ``refresh`` request returns
    that running ``refresh`` job instead of launching a second worker, and
    any other request raises :class:`RobinhoodLoginInProgress` -- see the
    module docstring. A login running in ANOTHER process (cross-process
    lock held) raises :class:`RobinhoodLoginInProgress` with ``job=None``
    for any mode: there is nothing in this process to join.
    """
    global _active_job, _xlock_holder
    with _start_lock:
        running = _running_job()
        if running is not None:
            if mode == "refresh" and running.mode == "refresh":
                logger.info(
                    "robinhood_login: refresh login %s already running; "
                    "joining it instead of starting a second worker.",
                    running.job_id,
                )
                return running
            raise RobinhoodLoginInProgress(running, mode)
        # No job is running here, but the last lock-holding job's deadline
        # thread (which polls) may not have released the cross-process lock
        # yet. Release it now so this process never refuses itself.
        holder = _xlock_holder
        if holder is not None:
            with holder._lock:
                holder_terminal = holder.state != "running"
            if holder_terminal:
                _release_cross_process_lock(holder)
        try:
            xlock_fd = _acquire_cross_process_lock()
        except _CrossProcessLockHeld as held:
            logger.info(
                "robinhood_login: a login is running in another process (%s); "
                "not starting a '%s' login.", describe_owner(held.owner), mode,
            )
            raise RobinhoodLoginInProgress(None, mode, owner=held.owner) from None
        job = LoginJobState(job_id=f"rhlogin-{uuid.uuid4().hex[:8]}", mode=mode)
        with _xlock_mutex:
            job._xlock_fd = xlock_fd
            if xlock_fd is not None:
                _xlock_holder = job
        try:
            _launch(job, username=username, password=password)
        except BaseException:
            _release_cross_process_lock(job)
            raise
        if xlock_fd is not None:
            _write_owner(job)
        _active_job = job
        return job


def _launch(job: LoginJobState, *, username: str, password: str) -> LoginJobState:
    """Spawn the worker and register the job. Caller holds _start_lock (and,
    when available, the cross-process lock)."""
    job_id = job.job_id
    mode = job.mode

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


def login_blocking(mode: LoginMode, *, username: str = "", password: str = "", poll_interval: float = 0.5) -> None:
    """Start a login attempt and block the calling thread until it reaches a
    terminal state. Raises on anything but success — callers that need a
    result object for an HTTP response should use ``start_login`` +
    ``get_login_state`` instead; this is for the synchronous internal
    callers (``data.robinhood_portfolio._fetch_live_snapshot``,
    ``main.py --refresh-account``) that already expect a plain blocking
    call and just need it to no longer be able to hang forever.

    If a ``refresh`` job is already running, a ``refresh`` call waits on
    that job rather than starting another (single-flight). A running job of
    the other mode raises :class:`RobinhoodLoginInProgress` (a
    :class:`RobinhoodLoginFailed`) immediately.
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
