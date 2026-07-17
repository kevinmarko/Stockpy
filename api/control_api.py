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
  PUT  /interval              -> command-token guarded (same posture as
                                POST /run — no separate master-switch flag;
                                see the docstring on the endpoint itself for
                                why this is the right posture even though
                                api/pilots_api.py's equivalent write also
                                requires AUTOMATION_WRITES_ENABLED). Changes
                                the daemon's internal timer cadence LIVE, no
                                restart required.

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

import hmac
import logging
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

from settings import INTERVAL_MAX_SECONDS, settings, validate_interval_seconds
from desktop.daemon_runtime import OrchestratorDaemon, RunRecord, TriggerOutcome
from execution.kill_switch import GlobalKillSwitch

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
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Authorization", "Content-Type"],
)

_bearer = HTTPBearer(auto_error=False)

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


# ---------------------------------------------------------------------------
# Auth guards
# ---------------------------------------------------------------------------


def require_read_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> None:
    """Bearer-token guard for read endpoints. FAIL-OPEN when
    STATE_API_TOKEN is unset (mirrors api/state_api.py's require_token
    exactly). Constant-time compare; token never logged (CONSTRAINT #3)."""
    token = settings.STATE_API_TOKEN
    if not token:  # unset/empty -> auth disabled (open)
        return
    presented = credentials.credentials if credentials else ""
    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


def require_command_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> None:
    """Bearer-token guard for the command endpoint (POST /run). FAIL-CLOSED
    when ORCHESTRATOR_DAEMON_TOKEN is unset -- unlike the read guard, silence
    must never mean "open" here since this endpoint can trigger a real
    pipeline run. Constant-time compare; token never logged (CONSTRAINT #3)."""
    token = settings.ORCHESTRATOR_DAEMON_TOKEN
    if not token:
        raise HTTPException(
            status_code=403,
            detail="Command endpoint disabled: ORCHESTRATOR_DAEMON_TOKEN not configured.",
        )
    presented = credentials.credentials if credentials else ""
    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


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
