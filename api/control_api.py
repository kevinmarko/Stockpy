"""
api/control_api.py
===================
STANDALONE FastAPI service that fronts the persistent orchestrator daemon
(``desktop/daemon_runtime.OrchestratorDaemon``) with HTTP.

Why this is a SEPARATE app/module from ``api/state_api.py``
-------------------------------------------------------------
``api/state_api.py`` is a deliberately pure, read-only view over already
-persisted files (state_snapshot.json, TransactionsStore). Its whole value
proposition — proven by a test-enforced AST guard (see
``tests/test_state_api.py::test_state_api_never_imports_engine_or_broker_code``)
— is that it NEVER imports engine/calculation or broker/execution modules.
That purity must never regress.

This module's entire purpose is the opposite: it needs to reach into the
live ``OrchestratorDaemon`` instance (to report run status and trigger new
cycles) and into ``execution.kill_switch.GlobalKillSwitch`` (to gate/report
on the kill switch). Importing either of those in ``api/state_api.py`` would
violate its guard and blur a load-bearing architectural boundary. So this
capability gets its own file, importing only what it needs
(``desktop.daemon_runtime`` and ``execution.kill_switch`` are explicitly
ALLOWED here — see this module's own AST guard test, which forbids direct
imports of the heavy pipeline engines themselves — e.g. ``main_orchestrator``,
``processing_engine``, ``strategy_engine`` — since this module must only ever
reach the pipeline THROUGH the daemon object, never call pipeline code
directly), and its own (stricter) auth posture: a second, FAIL-CLOSED bearer
-token guard specifically for the command endpoint (``POST /run``), on top of
the same fail-open read-token guard state_api.py already uses for its GET
endpoints.

Run standalone (for local testing only — production hosting is inside
``desktop/orchestrator_daemon.py``, see that module's wiring):
    uvicorn api.control_api:app --port 8601

Endpoints
---------
  GET  /health              -> always open, no auth. Liveness of this API
                                process (and whether a daemon has been
                                attached via ``set_daemon``), not the
                                trading engine itself.
  GET  /status               -> read-token guarded (fail-open when
                                STATE_API_TOKEN is unset). Full daemon +
                                kill-switch status snapshot.
  POST /run                  -> command-token guarded (FAIL-CLOSED when
                                ORCHESTRATOR_DAEMON_TOKEN is unset — the
                                endpoint is disabled entirely, 403). Triggers
                                a new orchestrator cycle, gated by the
                                kill switch.
  GET  /run/{run_id}/status  -> read-token guarded. Status of a specific run.
  GET  /run/latest           -> read-token guarded. Status of the most
                                recent run (may still be RUNNING).
  GET  /runs/history         -> read-token guarded. Durable run history read
                                from the pipeline_runs DB table (desktop/
                                run_history_store.py), independent of the
                                daemon's in-memory run_history ring on
                                GET /status -- survives a daemon restart.
                                Degrades to [] (never a 500) on a DB read
                                failure.
  PUT  /interval              -> command-token guarded (same posture as
                                POST /run — no separate master-switch flag;
                                see the docstring on the endpoint itself for
                                why this is the right posture even though
                                api/pilots_api.py's equivalent write also
                                requires AUTOMATION_WRITES_ENABLED). Changes
                                the daemon's internal timer cadence LIVE, no
                                restart required.
  POST /daemon/restart        -> command-token guarded. 409 while
                                daemon.is_running (a @property on
                                OrchestratorDaemon -- read WITHOUT parens;
                                confusable with gui.orchestrator_runner.
                                RunHandle.is_running(), which IS a method
                                and is called correctly elsewhere in this
                                file). On success arms
                                threading.Timer(0.5, os._exit, (0,)) --
                                os._exit, not sys.exit(), since uvicorn is
                                hosted on a background thread inside
                                desktop/orchestrator_daemon.py and
                                SystemExit would only kill that thread.
                                Deliberately does NOT 503 when no daemon is
                                attached, unlike every other endpoint here --
                                this API lives inside the daemon process, so
                                "no daemon attached" still means this
                                process can be exited. Promises only a
                                clean exit, never a respawn: whether one
                                follows depends entirely on the external
                                process supervisor (systemd Restart=always,
                                launchd KeepAlive, or none).

Auth
----
Two independent bearer-token guards, both via ``HTTPBearer(auto_error=False)``
+ ``hmac.compare_digest`` (constant-time; the token is NEVER logged —
CONSTRAINT #3):

  * ``require_read_token`` — reads ``settings.STATE_API_TOKEN`` live per
    request. FAIL-OPEN when unset (mirrors ``api/state_api.py`` exactly —
    same token, same semantics, so a deployment that already configured
    STATE_API_TOKEN for the read-only API gets read-auth here for free).
  * ``require_command_token`` — reads ``settings.ORCHESTRATOR_DAEMON_TOKEN``
    live per request. FAIL-CLOSED when unset: triggering a real pipeline
    run is a materially different risk than reading already-persisted
    state, so silence must never mean "open" here. When set, a
    missing/mismatched token is rejected before any daemon or kill-switch
    state is touched or reflected in the response — an unauthenticated
    caller must not be able to probe daemon state via response differences
    on the command endpoint.

CORS mirrors ``api/state_api.py`` (``settings.CORS_ALLOWED_ORIGINS``) but
additionally allows POST and PUT (state_api.py is GET-only; this module
needs POST for ``/run`` and PUT for ``/interval``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from dotenv import load_dotenv as _load_dotenv

from settings import ENV_PATH, INTERVAL_MAX_SECONDS, settings, validate_interval_seconds

# Load .env before any subsequent project import. Standalone
# `uvicorn api.control_api:app` has no main()-style entry point to hook this
# into the way main.py/main_orchestrator.py/app_shell.py do, so it runs
# here, at true module top, anchored to ENV_PATH (settings.py) — a bare
# load_dotenv() walks UP from this file's directory via find_dotenv() and,
# in a git worktree with no .env of its own, silently finds a PARENT
# checkout's .env instead.
_load_dotenv(ENV_PATH, override=False)

from api.auth import (
    require_orchestrator_command_token as require_command_token,
    require_read_token,
    require_stream_token,
)
from api.cors import LAN_TAILSCALE_ORIGIN_REGEX
from desktop.daemon_runtime import OrchestratorDaemon, RunRecord, TriggerOutcome
from desktop.run_history_store import RunHistoryStore
from execution.kill_switch import GlobalKillSwitch
from pilots.run_status import parse_crontab_status

logger = logging.getLogger(__name__)

app = FastAPI(
    title="InvestYo Orchestrator Control API",
    description=(
        "Control-plane API fronting the persistent orchestrator daemon "
        "(desktop/daemon_runtime.OrchestratorDaemon). Complements the "
        "read-only api/state_api.py with run-status introspection and a "
        "gated POST /run trigger. Never calls pipeline engines directly — "
        "only reaches them through the daemon object."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOWED_ORIGINS,
    allow_origin_regex=LAN_TAILSCALE_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Authorization", "Content-Type"],
)

# Mount the training-status WebSocket router (/ws/training/status) only --
# NOT tick_router. This is the process that actually runs POST /jobs and the
# train_lgbm/train_meta job types, so it's the only process with anything
# real to broadcast; tick_router (/ws/ticks/{symbol}, live market-tick
# streaming) is data_api.py's own capability and mounting it here too would
# make the daemon process unintentionally also serve it, an unrelated
# surface with no test coverage in this context -- see api/ws_api.py's
# module docstring. Broad `except Exception`, not `except ImportError` --
# api/ws_api.py imports data.websocket_streamer at its own module top, and a
# narrower catch would let ANY non-ImportError failure in that import chain
# crash this entire file's import (killing every Control API route, not
# just the WS ones).
try:
    from api.ws_api import training_router
    app.include_router(training_router)
except Exception as _ws_e:  # noqa: BLE001 - a WS mount must never break the rest of this API
    logger.warning("training_router mount skipped in control_api: %s", _ws_e)

# Guarded import of the training-status broadcast helpers -- mirrors the
# try/except above so a broken api.ws_api import degrades this module to
# "no training-status broadcasts", never a crash on import.
try:
    from api.ws_api import broadcast_training_status_threadsafe as _broadcast_training_status
    from api.ws_api import training_status_manager as _training_status_manager
except Exception:  # noqa: BLE001 - see the training_router mount comment above
    _broadcast_training_status = None
    _training_status_manager = None


@app.on_event("startup")
async def _capture_main_loop() -> None:
    """Capture the real running event loop so that SYNCHRONOUS routes (which
    FastAPI runs in a threadpool with no event loop of their own -- e.g.
    ``create_job`` below) can still schedule a training-status broadcast
    coroutine onto it via ``broadcast_training_status_threadsafe``."""
    from api import ws_api
    ws_api.set_main_loop(asyncio.get_running_loop())


# ---------------------------------------------------------------------------
# Daemon registry — set once by the process entrypoint after daemon.start()
# ---------------------------------------------------------------------------

_daemon: Optional[OrchestratorDaemon] = None


def set_daemon(daemon: Optional[OrchestratorDaemon]) -> None:
    """Register the live daemon instance this API should front.

    Called once by ``desktop/orchestrator_daemon.py`` after
    ``daemon.start()`` succeeds. Also used by tests to inject a fake daemon
    (or ``None``, to simulate "no daemon attached yet")."""
    global _daemon
    _daemon = daemon


def get_daemon() -> Optional[OrchestratorDaemon]:
    """Return the currently-registered daemon instance, or None if
    ``set_daemon`` has never been called (or was reset to None)."""
    return _daemon


# Auth guards (require_read_token / require_command_token) are imported from
# api/auth.py at module top — see there for the shared implementation.

if not settings.STATE_API_TOKEN:
    logger.warning(
        "STATE_API_TOKEN not set — /status, /run/{run_id}/status, /run/latest "
        "are UNAUTHENTICATED. Set STATE_API_TOKEN to require a bearer token."
    )
if not settings.ORCHESTRATOR_DAEMON_TOKEN:
    logger.warning(
        "ORCHESTRATOR_DAEMON_TOKEN not set — POST /run and PUT /interval are "
        "DISABLED (fail-closed, 403 on every call). Set ORCHESTRATOR_DAEMON_TOKEN "
        "to enable them."
    )


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class IntervalUpdateRequest(BaseModel):
    """Body for ``PUT /interval``. ``0`` disables the daemon's internal timer
    (on-demand only); otherwise MUST be in
    ``[settings.INTERVAL_MIN_SECONDS, settings.INTERVAL_MAX_SECONDS]``
    seconds. Validated via the SAME ``settings.validate_interval_seconds``
    used by ``desktop.daemon_runtime.OrchestratorDaemon.set_interval`` and by
    ``api/pilots_api.py``'s equivalent body — the shared policy function is
    what keeps all three from drifting apart (see ``settings.py``'s
    docstring on it)."""

    interval_seconds: int = Field(..., ge=0, le=INTERVAL_MAX_SECONDS)

    @field_validator("interval_seconds")
    @classmethod
    def _validate(cls, v: int) -> int:
        return validate_interval_seconds(v)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _serialize_run(record: Optional[RunRecord]) -> Optional[Dict[str, Any]]:
    """Serialize a RunRecord into a JSON-safe dict, or None passthrough.

    ``progress`` (reporting/progress.py telemetry, added alongside the other
    RunRecord fields -- see desktop/daemon_runtime.py::RunRecord) is already a
    plain, JSON-safe dict (or None) as constructed by
    ``OrchestratorDaemon._run_one_cycle`` -- no further serialization needed,
    it is passed through verbatim.
    """
    if record is None:
        return None
    return {
        "run_id": record.run_id,
        "state": record.state.value,
        # "full" | "data" | "metrics" -- getattr-guarded so a RunRecord from a
        # pre-mode daemon build (should never happen post-deploy, but defensive)
        # still serializes without KeyError, defaulting to the historical "full".
        "mode": getattr(record, "mode", "full"),
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
        "duration_seconds": record.duration_seconds,
        "error": record.error,
        "reason": record.reason,
        "progress": record.progress,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> Dict[str, Any]:
    """Liveness check for this API process. Always open, no auth."""
    return {"status": "ok", "daemon_alive": get_daemon() is not None}


@app.get("/status", dependencies=[Depends(require_read_token)])
def get_status() -> Dict[str, Any]:
    """Full daemon + kill-switch status snapshot."""
    daemon = get_daemon()
    if daemon is None:
        return {"daemon_alive": False}

    daemon_status = daemon.status()
    ks = GlobalKillSwitch()
    ks_active = ks.is_active()
    started_at = daemon_status.get("started_at")

    return {
        "daemon_alive": True,
        "is_running": daemon_status.get("is_running"),
        "current_run_id": daemon_status.get("current_run_id"),
        "interval_seconds": daemon_status.get("interval_seconds"),
        "engines_warm": daemon_status.get("engines_warm"),
        "started_at": started_at.isoformat() if started_at else None,
        "last_run": _serialize_run(daemon_status.get("last_run")),
        # Bounded run history, most-recent-first (see the frozen GET /status
        # contract). daemon.status() supplies the RunRecord list; a fake/legacy
        # daemon status dict without the key degrades to [] (never fabricated).
        "run_history": [
            _serialize_run(r) for r in (daemon_status.get("run_history") or [])
        ],
        "kill_switch_active": ks_active,
        "kill_switch_reason": ks.reason() if ks_active else None,
        "advisory_only": settings.ADVISORY_ONLY,
        "dry_run": settings.DRY_RUN,
    }


@app.post("/run", dependencies=[Depends(require_command_token)])
def trigger_run() -> JSONResponse:
    """Trigger a new orchestrator cycle. Gated by the kill switch.

    Auth is checked FIRST (via the dependency) so an unauthenticated caller
    can never distinguish daemon/kill-switch state through this endpoint's
    response — the 401/403 always fires before any daemon or kill-switch
    check runs.
    """
    daemon = get_daemon()
    if daemon is None:
        raise HTTPException(status_code=503, detail="Daemon not available.")

    ks = GlobalKillSwitch()
    if ks.is_active():
        raise HTTPException(
            status_code=423,
            detail={
                "detail": "Kill switch active — pipeline triggering is paused.",
                "kill_switch_reason": ks.reason() or "",
            },
        )

    result = daemon.trigger_run(reason="manual")

    if result.outcome == TriggerOutcome.ALREADY_RUNNING:
        raise HTTPException(
            status_code=409,
            detail={
                "detail": "A run is already in flight.",
                "run_id": result.run_id,
            },
        )

    return JSONResponse(
        status_code=202,
        content={"run_id": result.run_id, "state": "queued"},
    )


def _trigger_pipeline_mode(mode: str) -> JSONResponse:
    """Shared body for the mode-scoped pipeline triggers.

    Mirrors ``POST /run``'s posture exactly: auth is enforced by the endpoint
    dependency FIRST, then the daemon/kill-switch checks. 423 when the kill
    switch is active, 409 when a run is already in flight, 202 + run_id + mode
    otherwise.
    """
    daemon = get_daemon()
    if daemon is None:
        raise HTTPException(status_code=503, detail="Daemon not available.")

    ks = GlobalKillSwitch()
    if ks.is_active():
        raise HTTPException(
            status_code=423,
            detail={
                "detail": "Kill switch active — pipeline triggering is paused.",
                "kill_switch_reason": ks.reason() or "",
            },
        )

    result = daemon.trigger_run(reason="manual", mode=mode)

    if result.outcome == TriggerOutcome.ALREADY_RUNNING:
        raise HTTPException(
            status_code=409,
            detail={
                "detail": "A run is already in flight.",
                "run_id": result.run_id,
            },
        )

    return JSONResponse(
        status_code=202,
        content={"run_id": result.run_id, "state": "queued", "mode": mode},
    )


@app.post("/pipeline/data", dependencies=[Depends(require_command_token)])
def trigger_pipeline_data() -> JSONResponse:
    """Trigger a data-fetch-only pipeline sub-run (``mode="data"``)."""
    return _trigger_pipeline_mode("data")


@app.post("/pipeline/metrics", dependencies=[Depends(require_command_token)])
def trigger_pipeline_metrics() -> JSONResponse:
    """Trigger a data-fetch + indicator/forecast/signal sub-run (``mode="metrics"``)."""
    return _trigger_pipeline_mode("metrics")


@app.get("/run/{run_id}/status", dependencies=[Depends(require_read_token)])
def get_run_status(run_id: str) -> Dict[str, Any]:
    """Status of a specific run (including one still RUNNING)."""
    daemon = get_daemon()
    if daemon is None:
        raise HTTPException(status_code=503, detail="Daemon not available.")

    record = daemon.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="No such run.")

    return _serialize_run(record)


@app.get("/run/latest", dependencies=[Depends(require_read_token)])
def get_latest_run() -> Dict[str, Any]:
    """Status of the most recent run (may still be RUNNING)."""
    daemon = get_daemon()
    if daemon is None:
        raise HTTPException(status_code=503, detail="Daemon not available.")

    record = daemon.last_result
    if record is None:
        raise HTTPException(
            status_code=404, detail="No completed run yet — trigger one via POST /run."
        )

    return _serialize_run(record)


@app.get("/runs/history", dependencies=[Depends(require_read_token)])
def get_runs_history(limit: int = 50) -> List[Dict[str, Any]]:
    """Durable run history read straight from the ``pipeline_runs`` DB table
    (``desktop/run_history_store.py``).

    Deliberately independent of ``get_daemon()`` -- unlike every other
    endpoint here, this one has no daemon-not-attached branch, since the
    whole point is to keep working (and keep showing history) across a
    daemon restart, which is exactly when ``GET /status``'s in-memory
    ``run_history`` ring is empty. ``limit`` is clamped to ``[1, 200]``.
    Degrades to ``[]`` (never a 500) on a DB read failure -- CONSTRAINT #6.
    """
    limit = max(1, min(limit, 200))
    try:
        store = RunHistoryStore(readonly=True)
        return store.get_recent(limit=limit)
    except Exception as exc:  # noqa: BLE001 - dead-letter: DB errors degrade to []
        logger.warning("control_api: failed to read run history from DB: %s", exc)
        return []


@app.put("/interval", dependencies=[Depends(require_command_token)])
def set_interval(body: IntervalUpdateRequest) -> Dict[str, Any]:
    """Change the daemon's internal timer cadence LIVE, without a restart.

    Guarded by ``require_command_token`` ALONE — unlike
    ``api/pilots_api.py``'s ``PUT /automation/schedule/interval`` (which adds
    ``AUTOMATION_WRITES_ENABLED`` on top of its own command token because
    that write persists to ``.env``), a live ``set_interval`` call has NO
    persistence — it dies with the process, exactly like ``POST /run``'s
    "run now" trigger, which sits behind the command token alone. Gating a
    "run more often" cadence change more strictly than "run right now" would
    invert that risk ordering. The operator-facing write path is already
    gated at ``pilots_api``; this endpoint is loopback-bound and
    token-gated, one layer further from the browser.

    A rejected (out-of-range) ``interval_seconds`` never reaches the daemon
    at all — pydantic's ``field_validator`` (via the same
    ``settings.validate_interval_seconds`` the daemon itself uses) rejects
    it with 422 before this function body runs.
    """
    daemon = get_daemon()
    if daemon is None:
        raise HTTPException(status_code=503, detail="Daemon not available.")

    daemon.set_interval(body.interval_seconds)
    return {"interval_seconds": body.interval_seconds}


@app.post("/daemon/restart", dependencies=[Depends(require_command_token)])
def restart_daemon() -> Dict[str, Any]:
    """Terminate this process so its process supervisor (systemd
    ``Restart=always``, launchd ``KeepAlive``) respawns it with freshly
    -written ``.env`` values picked up.

    Honesty note: whether anything actually respawns this process depends
    entirely on how it's being run. ``deploy/investyo-daemon.service``
    (``Restart=always``) and ``scripts/com.investyo.stack.plist``
    (``KeepAlive``) both respawn on exit. The plain desktop-shell path
    (``app_shell.py`` spawning ``desktop/orchestrator_daemon.py`` via
    ``gui.orchestrator_runner.launch_daemon_engine`` — a bare
    ``subprocess.Popen`` with no restart-on-death watchdog) does NOT: this
    call simply stops the daemon until the operator relaunches the app. This
    endpoint has no way to know which case it's in, so it cannot promise a
    respawn — only an honest, clean exit.
    """
    daemon = get_daemon()
    # ``OrchestratorDaemon.is_running`` is a @property (desktop/daemon_runtime.py),
    # NOT a method: calling it as ``daemon.is_running()`` evaluates the property to
    # a bool and then CALLS that bool -- ``TypeError: 'bool' object is not
    # callable`` -- which FastAPI turns into an HTTP 500 on every request where a
    # daemon is actually attached (i.e. every real deployment; see
    # desktop/orchestrator_daemon.py's set_daemon() wiring right after
    # daemon.start()). Read it as a plain attribute. Do NOT "fix" the
    # ``rec.handle.is_running()`` calls elsewhere in this file to match -- those
    # are gui.orchestrator_runner.RunHandle, where is_running() IS a method.
    if daemon is not None and daemon.is_running:
        raise HTTPException(
            status_code=409,
            detail="Cannot restart while an orchestrator run is currently active.",
        )

    # A plain background thread (NOT asyncio.get_event_loop().call_later),
    # so the process exits reliably regardless of which thread is hosting
    # this request handler. `os._exit()` is an unconditional OS-level
    # process exit -- unlike `sys.exit()` (raises SystemExit, which only
    # terminates the CALLING thread when that thread isn't the main one,
    # e.g. when uvicorn is hosted on a background thread as it is inside
    # desktop/orchestrator_daemon.py), this reliably kills the whole process.
    threading.Timer(0.5, os._exit, args=(0,)).start()
    return {
        "restarting": True,
        "message": (
            "Process exiting in ~0.5s. Whether it comes back up depends on "
            "the process supervisor (systemd/launchd auto-restart, or none)."
        ),
    }


# ---------------------------------------------------------------------------
# Background Job Execution & Log Streaming Endpoints (JOBS_API_ENABLED=True)
# ---------------------------------------------------------------------------


def _require_jobs_api_enabled() -> None:
    if not settings.JOBS_API_ENABLED:
        raise HTTPException(status_code=403, detail="JOBS_API_ENABLED is False.")


class JobCreateRequest(BaseModel):
    job_type: str = Field(..., description="Job type to execute")
    params: Optional[Dict[str, Any]] = Field(default=None, description="Optional job parameters")


@app.post(
    "/jobs",
    dependencies=[Depends(require_command_token), Depends(_require_jobs_api_enabled)],
)
def create_job(body: JobCreateRequest) -> Dict[str, Any]:
    """Launch a background process job (preflight, pytest, validation, verify, gravity, advisory, orchestrator)."""
    from api._jobs import JobType, job_manager

    try:
        jtype = JobType(body.job_type)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=f"Unknown job_type: {body.job_type!r}") from err

    try:
        rec = job_manager.start_job(jtype, body.params)
    except RuntimeError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except PermissionError as err:
        raise HTTPException(status_code=403, detail=str(err)) from err

    # Broadcast a training-status "started" event over /ws/training/status
    # for the two model-retraining job types. Deliberately placed AFTER the
    # exception-mapping try/except above (rec is bound, job creation already
    # succeeded) so a broadcast failure can never be misreported to the
    # client as a 400/409/403 about the job itself. getattr-guarded (not a
    # direct JobType.TRAIN_META/TRAIN_LGBM attribute reference) so this
    # never AttributeErrors against a JobType enum build that hasn't landed
    # those members yet -- every other job type simply never broadcasts.
    _training_job_types = (
        getattr(JobType, "TRAIN_META", None),
        getattr(JobType, "TRAIN_LGBM", None),
    )
    if jtype in _training_job_types and _broadcast_training_status is not None:
        try:
            msg = json.dumps({
                "job_id": rec.job_id,
                "status": "started",
                "message": f"{rec.job_type.value} started",
            })
            _broadcast_training_status(msg)
        except Exception as exc:  # noqa: BLE001 - a broadcast must never break job creation
            logger.warning("training-status broadcast failed for job %s: %s", rec.job_id, exc)

    return {
        "job_id": rec.job_id,
        "job_type": rec.job_type.value,
        "status": rec.status(),
        "cancellable": rec.cancellable,
        "command_name": rec.command_name,
        "created_at": rec.created_at,
    }


@app.get(
    "/jobs/{job_id}",
    dependencies=[Depends(require_read_token), Depends(_require_jobs_api_enabled)],
)
def get_job_status(job_id: str) -> Dict[str, Any]:
    """Inspect the status of a launched background job."""
    from api._jobs import job_manager

    rec = job_manager.get_job(job_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"No job found with ID {job_id}")

    return {
        "job_id": rec.job_id,
        "job_type": rec.job_type.value,
        "status": rec.status(),
        "exit_code": rec.exit_code(),
        "is_running": rec.handle.is_running(),
        "cancellable": rec.cancellable,
        "command_name": rec.command_name,
        "created_at": rec.created_at,
    }


@app.post(
    "/jobs/{job_id}/cancel",
    dependencies=[Depends(require_command_token), Depends(_require_jobs_api_enabled)],
)
def cancel_job(job_id: str) -> Dict[str, Any]:
    """Cancel a running background job. ``cancelled: false`` (200, not an
    error) reports an honest "asked, but stop could not be confirmed" rather
    than claiming success stop_run() didn't actually achieve."""
    from api._jobs import job_manager

    try:
        confirmed = job_manager.cancel_job(job_id)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=f"No job found with ID {job_id}") from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    return {"job_id": job_id, "cancelled": confirmed}


_SSE_HEARTBEAT_SECONDS = 15.0
_SSE_POLL_SECONDS = 0.5


@app.get(
    "/jobs/{job_id}/stream",
    dependencies=[Depends(require_stream_token), Depends(_require_jobs_api_enabled)],
)
def stream_job_logs(
    job_id: str,
    offset: int = 0,
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
):
    """Stream live logs for a job over Server-Sent Events (SSE).

    Auth via ``require_stream_token`` (not ``require_read_token``): the
    browser's native ``EventSource`` cannot set an ``Authorization`` header,
    so this endpoint also accepts ``?token=``.

    Resume: a reconnecting ``EventSource`` automatically resends the last
    ``id:`` it saw as a ``Last-Event-ID`` header — that's the standard
    signal a real reconnect (network blip, backgrounded tab) sends, so it
    takes priority over ``?offset=`` (which only reflects the URL the
    component was first mounted with).
    """
    import time as _time
    from fastapi.responses import StreamingResponse
    from api._jobs import JobType, job_manager
    from api._redact import redact_line

    rec = job_manager.get_job(job_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"No job found with ID {job_id}")

    log_path = rec.handle.log_path

    resume_offset = offset
    if last_event_id:
        try:
            resume_offset = int(last_event_id)
        except ValueError:
            pass  # malformed header -> fall back to ?offset=

    def _read_lines(path, offset):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            return f.readlines(), f.tell()

    async def log_event_generator():
        current_offset = max(0, resume_offset)
        last_sent = _time.monotonic()
        while True:
            sent_any = False
            if log_path.exists():
                lines, new_offset = await asyncio.to_thread(_read_lines, log_path, current_offset)
                if lines:
                    for line in lines:
                        scrubbed = redact_line(line.rstrip("\n"))
                        yield f"id: {current_offset}\ndata: {scrubbed}\n\n"
                    current_offset = new_offset
                    sent_any = True

            if not rec.handle.is_running():
                # Stream final lines if any and stop
                yield f"event: end\ndata: Job completed with exit code {rec.exit_code()}\n\n"
                # getattr-guarded for the same reason as create_job's
                # broadcast above -- never AttributeError against a JobType
                # enum build predating TRAIN_META/TRAIN_LGBM.
                _training_job_types = (
                    getattr(JobType, "TRAIN_META", None),
                    getattr(JobType, "TRAIN_LGBM", None),
                )
                if rec.job_type in _training_job_types and _training_status_manager is not None:
                    try:
                        await _training_status_manager.broadcast(json.dumps({
                            "job_id": job_id,
                            "status": "finished",
                            "exit_code": rec.exit_code(),
                        }))
                    except Exception:
                        pass
                break

            now = _time.monotonic()
            if sent_any:
                last_sent = now
            elif now - last_sent >= _SSE_HEARTBEAT_SECONDS:
                # Keep-alive comment (ignored by EventSource.onmessage) so
                # an idle job's connection survives a proxy's read timeout.
                yield ": heartbeat\n\n"
                last_sent = now

            await asyncio.sleep(_SSE_POLL_SECONDS)

    return StreamingResponse(log_event_generator(), media_type="text/event-stream")


@app.get("/system/cron-status", dependencies=[Depends(require_read_token)])
def get_system_cron_status() -> Dict[str, Any]:
    """Parse deploy/crontab.txt and return the schedule.

    Delegates to ``pilots.run_status.parse_crontab_status`` -- see that
    function's docstring for the title/description-reset contract this used
    to duplicate (and get subtly wrong) as an inline copy."""
    return parse_crontab_status()
