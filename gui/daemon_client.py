"""Thin, dependency-free HTTP client for the orchestrator daemon's Control API.

The Control API (``api/control_api.py``, built in a parallel workstream) is
hosted inside the persistent orchestrator daemon process
(``desktop/orchestrator_daemon.py``) and exposes a small set of endpoints for
triggering/monitoring pipeline runs without spawning a fresh subprocess. This
module is the GUI-facing client for that API — a later piece of work wires it
into ``gui/orchestrator_runner.py``; this file does NOT do that wiring and
does not import anything from the daemon/orchestrator/engine layers. Its only
real dependency is :mod:`settings`.

Stdlib only (``urllib.request`` / ``json``) — this mirrors the codebase's
established convention for small internal HTTP calls (see
``desktop/net_util.py``'s ``wait_for_http`` and ``alerting.py``'s webhook
POSTs), despite ``requests`` being available in requirements.txt for heavier
external API work.

CRITICAL: every public function in this module is NON-RAISING (CONSTRAINT #6,
dead-letter resilience). A down daemon, connection-refused, a timeout, a
malformed JSON response, or an unexpected status code all degrade to a
documented sentinel return value — never an exception. A down daemon during
normal operation (e.g. daemon mode simply not enabled) is an expected,
routine condition, so failures are logged at DEBUG/WARNING, never ERROR, and
never printed.

The HTTP contract (base URL, endpoints, status codes, and response shapes) is
frozen and documented on each function below; it is implemented by
``api/control_api.py`` which may not exist as running code in every checkout
of this module — tests here mock the HTTP layer and never require a real
server.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

from settings import settings

logger = logging.getLogger(__name__)


def _base_url() -> str:
    """Build the Control API base URL from the CURRENT ``settings.ORCHESTRATOR_API_PORT``.

    Read at call time (not import time) so tests can monkeypatch the port
    per-test and so a future settings change (e.g. the GUI Settings tab
    writing a new port) takes effect without reimporting this module.
    """
    return f"http://127.0.0.1:{settings.ORCHESTRATOR_API_PORT}"


def _auth_headers() -> dict:
    """Build the ``Authorization`` header from the CURRENT
    ``settings.ORCHESTRATOR_DAEMON_TOKEN``. The header key is omitted
    entirely (not set to an empty-string token) when the setting is unset.
    """
    token = settings.ORCHESTRATOR_DAEMON_TOKEN
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _get_json(path: str, timeout: float) -> Optional[dict]:
    """GET ``path`` against the Control API and return the parsed JSON body.

    Returns None on ANY failure (connection refused, timeout, non-2xx status,
    malformed JSON) — never raises. This is the shared helper for the
    read-only endpoints (/health, /status, /run/{id}/status, /run/latest);
    callers needing to distinguish specific status codes (only ``trigger_run``
    does) implement their own request handling instead of using this helper.
    """
    url = _base_url() + path
    req = urllib.request.Request(url, method="GET", headers=_auth_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
        return json.loads(body)
    except urllib.error.HTTPError as exc:
        logger.debug("daemon_client: GET %s -> HTTP %s", path, exc.code)
        return None
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("daemon_client: GET %s failed: %s", path, exc)
        return None


def daemon_available(timeout: float = 0.5) -> bool:
    """GET /health.

    True only on a real 200 response reporting ``daemon_alive: true``. False
    on ANY failure — connection refused, timeout, non-200 status, a
    malformed/non-JSON body, or a well-formed body reporting
    ``daemon_alive: false``. Never raises. No auth required by the contract,
    so no bearer header is sent here (matching /health being open even when
    a token is configured).
    """
    url = _base_url() + "/health"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False
            body = resp.read()
        data = json.loads(body)
        return bool(data.get("status") == "ok" and data.get("daemon_alive") is True)
    except urllib.error.HTTPError as exc:
        logger.debug("daemon_client: /health -> HTTP %s", exc.code)
        return False
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("daemon_client: /health failed: %s", exc)
        return False


def get_status(timeout: float = 2.0) -> Optional[dict]:
    """GET /status.

    Returns the parsed JSON dict on success (200), None on ANY failure
    (connection refused, timeout, non-200 status, malformed JSON body,
    missing/invalid bearer token). Never raises.
    """
    return _get_json("/status", timeout)


@dataclass(frozen=True)
class TriggerResponse:
    """Result of a :func:`trigger_run` call.

    ``error`` is a stable, code-like tag callers can branch on without
    string-parsing the human-readable detail message: one of
    "already_running", "kill_switch_active", "command_disabled",
    "unauthorized", "unavailable", "network_error", or "unexpected_response".
    ``None`` when ``ok`` is True.
    """

    ok: bool
    run_id: Optional[str]
    state: Optional[str]  # "queued" on success
    error: Optional[str]
    existing_run_id: Optional[str] = None  # populated only for the 409 case
    kill_switch_reason: Optional[str] = None  # populated only for the 423 case


def _parse_json_body(raw: bytes) -> dict:
    """Best-effort JSON parse of an HTTPError body. Returns {} (never raises)
    on empty/malformed bodies so callers can safely use ``.get(...)``."""
    try:
        return json.loads(raw) if raw else {}
    except (ValueError, json.JSONDecodeError):
        return {}


def trigger_run(timeout: float = 5.0) -> TriggerResponse:
    """POST /run.

    Maps each documented status code to a :class:`TriggerResponse`:

    - 202 -> ok=True, run_id, state="queued"
    - 409 -> ok=False, error="already_running", existing_run_id=<parsed>
    - 423 -> ok=False, error="kill_switch_active", kill_switch_reason=<parsed>
    - 401 -> ok=False, error="unauthorized"
    - 403 -> ok=False, error="command_disabled"
    - 503 -> ok=False, error="unavailable"
    - any other status / connection failure / timeout / malformed JSON ->
      ok=False, error="network_error" (or "unexpected_response" for a
      recognized-but-undocumented status code)

    Never raises under any of these branches.
    """
    url = _base_url() + "/run"
    req = urllib.request.Request(url, method="POST", headers=_auth_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            if resp.status == 202:
                data = json.loads(body)
                return TriggerResponse(
                    ok=True,
                    run_id=data.get("run_id"),
                    state=data.get("state"),
                    error=None,
                )
            # Unexpected 2xx we don't recognize.
            logger.debug("daemon_client: POST /run -> unexpected status %s", resp.status)
            return TriggerResponse(
                ok=False, run_id=None, state=None, error="unexpected_response"
            )
    except urllib.error.HTTPError as exc:
        data = _parse_json_body(exc.read())
        if exc.code == 409:
            return TriggerResponse(
                ok=False,
                run_id=None,
                state=None,
                error="already_running",
                existing_run_id=data.get("run_id"),
            )
        if exc.code == 423:
            return TriggerResponse(
                ok=False,
                run_id=None,
                state=None,
                error="kill_switch_active",
                kill_switch_reason=data.get("kill_switch_reason"),
            )
        if exc.code == 401:
            return TriggerResponse(ok=False, run_id=None, state=None, error="unauthorized")
        if exc.code == 403:
            return TriggerResponse(ok=False, run_id=None, state=None, error="command_disabled")
        if exc.code == 503:
            return TriggerResponse(ok=False, run_id=None, state=None, error="unavailable")
        logger.debug("daemon_client: POST /run -> HTTP %s", exc.code)
        return TriggerResponse(ok=False, run_id=None, state=None, error="unexpected_response")
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("daemon_client: POST /run failed: %s", exc)
        return TriggerResponse(ok=False, run_id=None, state=None, error="network_error")


def get_run_status(run_id: str, timeout: float = 2.0) -> Optional[dict]:
    """GET /run/{run_id}/status.

    Returns the parsed run-record dict on 200 (including a still-in-progress
    run with ``"state": "running"`` and ``"finished_at": null`` — that's just
    a dict key with a JSON null value, no special handling needed). Returns
    None on a 404 (unknown run_id) or ANY other failure. Never raises.

    The dict's ``"progress"`` key (reporting/progress.py telemetry, added to
    ``RunRecord``/``_serialize_run`` alongside the other fields — see
    desktop/daemon_runtime.py and api/control_api.py) requires NO handling
    here: ``_get_json`` does a raw ``json.loads()`` with no field
    allowlisting, so it flows through automatically as either a nested dict
    (``{"state": "running", "stage": "forecasting", "percent": 58.3, ...}``)
    or ``None`` when the API served it as JSON ``null``.
    """
    return _get_json(f"/run/{run_id}/status", timeout)


def get_latest_run(timeout: float = 2.0) -> Optional[dict]:
    """GET /run/latest.

    Returns the parsed run-record dict on 200, or None on a 404 (nothing
    triggered yet) or ANY other failure. Never raises.

    See :func:`get_run_status` for the ``"progress"`` key note — it applies
    here identically.
    """
    return _get_json("/run/latest", timeout)
