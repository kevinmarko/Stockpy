"""Glue between data.robinhood_login's killable-subprocess login-job
primitive and the Pilots API's POST /brokerage/connect and
POST /brokerage/refresh endpoints.

Owns exactly one Pilots-API-specific responsibility data.robinhood_login
itself has no business knowing about: persisting RH_USERNAME/RH_PASSWORD to
``.env`` ONLY once a "connect" job's login attempt actually succeeds — never
before, never on failure/timeout/cancellation. This is done by a background
watcher thread rather than the status-poll endpoint, so persistence doesn't
depend on a client continuing to poll after it has already seen "succeeded"
once. Never logs credential values (CONSTRAINT #3).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

from data import brokerage_credentials
from data.historical_store import HistoricalStore
from data.robinhood_login import (
    LoginJobState,
    RobinhoodLoginInProgress,
    cancel_login,
    describe_owner,
    get_login_state,
    start_login,
)

logger = logging.getLogger(__name__)


def start_connect_job(username: str, password: str) -> LoginJobState:
    """Starts a 'connect' login job for CANDIDATE credentials and arranges
    for RH_USERNAME/RH_PASSWORD to be persisted to ``.env`` the moment (and
    ONLY if) the login actually succeeds.

    Raises :class:`data.robinhood_login.RobinhoodLoginInProgress` (before
    any watcher is started) when another login job is already running --
    single-flight, one approval prompt at a time.
    """
    job = start_login("connect", username=username, password=password)

    def _watch() -> None:
        state: Optional[LoginJobState] = job
        while True:
            state = get_login_state(job.job_id)
            if state is None or state.state != "running":
                break
            time.sleep(0.5)
        if state is not None and state.state == "succeeded":
            try:
                brokerage_credentials.write_rh_credentials(username, password)
            except Exception as exc:  # noqa: BLE001 - never crash the watcher thread
                logger.error(
                    "api/_rh_login: failed to persist credentials after a "
                    "successful connect job: %s",
                    exc,
                )

    threading.Thread(target=_watch, daemon=True).start()
    return job


def start_refresh_job() -> LoginJobState:
    """Starts a 'refresh' login job. No credential persistence needed here —
    RH_USERNAME/RH_PASSWORD are already in ``.env``; the worker reads them
    itself and, on success, writes the account snapshot to cache + DB.

    Single-flight: joins an already-running refresh job (same job returned),
    and raises :class:`data.robinhood_login.RobinhoodLoginInProgress` if a
    connect job is running.
    """
    return start_login("refresh")


def login_in_progress_detail(exc: RobinhoodLoginInProgress) -> str:
    """Plain-string HTTP 409 detail for a refused start (a string, not a
    dict, so the webapp's generic error surfacing shows it verbatim). Never
    includes credential values -- only the running job's id and mode (or,
    for a login running in ANOTHER process, the lock owner's pid/job/mode/
    start time from its diagnostic sidecar)."""
    if exc.job is None:
        return (
            f"A Robinhood login is already in progress in another process "
            f"({describe_owner(exc.owner)}). Approve it in the Robinhood app or "
            f"wait for it to finish before starting a new {exc.requested_mode} login."
        )
    return (
        f"A Robinhood login is already in progress (job {exc.job.job_id}, "
        f"mode {exc.job.mode}). Approve it in the Robinhood app or cancel it "
        f"before starting a new {exc.requested_mode} login."
    )


def serialize_job(job: LoginJobState) -> Dict[str, Any]:
    """JSON-safe status payload for GET /brokerage/login/status/{job_id}."""
    with job._lock:
        state = job.state
        phase = job.phase
        error_code = job.error_code
        seconds_remaining = job.seconds_remaining

    connected = brokerage_credentials.rh_credentials_present()
    has_snapshot = False
    try:
        has_snapshot = HistoricalStore(readonly=True).latest_account_snapshot() is not None
    except Exception as exc:  # noqa: BLE001 - dead-letter: cold DB -> honest False
        logger.warning("api/_rh_login: snapshot presence check failed: %s", exc)

    return {
        "job_id": job.job_id,
        "mode": job.mode,
        "state": state,
        "phase": phase,
        "error_code": error_code,
        "seconds_remaining": round(seconds_remaining, 1),
        "connected": connected,
        "has_account_snapshot": has_snapshot,
    }


__all__ = [
    "start_connect_job",
    "start_refresh_job",
    "serialize_job",
    "get_login_state",
    "cancel_login",
    "RobinhoodLoginInProgress",
    "login_in_progress_detail",
]
