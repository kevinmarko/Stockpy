"""
api/pilots_api.py
==================
STANDALONE FastAPI service (port 8602) serving the Autopilot "Pilots"
marketplace — the read/write API the mobile-first PWA under ``webapp/``
consumes.

Why a THIRD, separate app (not an extension of ``api/state_api.py``)
--------------------------------------------------------------------
``api/state_api.py`` is deliberately pure: a test-enforced AST guard proves it
NEVER imports engine/calculation OR broker/execution modules. That purity is
load-bearing and must never regress. This module, by contrast, needs the
follow write-path (``pilots.mirror`` → ``execution.queue_builder``) and the
kill switch (``execution.kill_switch``), so it gets its own file — mirroring
exactly how ``api/control_api.py`` split off from ``state_api.py`` for the same
reason.

What this module MAY import (and its own AST guard test enforces): the pure
``pilots.*`` package, ``execution.kill_switch``, ``data.historical_store``,
``data.robinhood_portfolio``, ``data.brokerage_credentials``. What it must
NEVER import directly: the heavy calculation engines (``processing_engine``,
``strategy_engine``, ``forecasting_engine``, ``macro_engine``,
``technical_options_engine``, ``main_orchestrator``) — all Pilot reads run off
already-persisted state, and the follow write reaches execution only through
``pilots.mirror``.

Brokerage-connect credential intake (``/brokerage/*``)
--------------------------------------------------------
A deliberate, narrowly-scoped exception to this codebase's normal
hand-edit-``.env`` posture for secrets — see ``data/brokerage_credentials.py``
for the full rationale. Gated behind THREE independent controls, all of which
must pass: (1) ``settings.BROKERAGE_CONNECT_ENABLED`` (default ``False``,
never GUI-writable), (2) the same fail-closed ``FOLLOW_API_TOKEN`` command
token as the follow write-path, (3) ``require_loopback`` — the request must
originate from ``127.0.0.1``/``::1``. Credentials are verified with a
read-only login (``data.robinhood_portfolio.verify_credentials``) BEFORE they
are ever persisted, and are never logged, cached, or echoed back
(CONSTRAINT #3). This remains a single-operator, single-machine model — not a
multi-user credential vault.

Run standalone:
    uvicorn api.pilots_api:app --port 8602

Auth
----
Two independent bearer-token guards (both ``HTTPBearer(auto_error=False)`` +
``hmac.compare_digest`` — constant-time, token never logged, CONSTRAINT #3):

  * ``require_read_token`` — reads ``settings.STATE_API_TOKEN`` live per
    request. FAIL-OPEN when unset (mirrors ``api/state_api.py`` exactly). Guards
    every GET *read* endpoint.
  * ``require_command_token`` — reads ``settings.FOLLOW_API_TOKEN`` live per
    request. FAIL-CLOSED when unset: the follow endpoints (``GET/PUT /follows``,
    ``POST /pilots/{id}/follow``) are disabled entirely (403), because
    persisting a follow that produces a gated order queue is a materially
    different risk than reading persisted state (mirrors
    ``api/control_api.py``'s ``ORCHESTRATOR_DAEMON_TOKEN`` posture).

Four additional FAIL-CLOSED master-switch guards stack ON TOP of the command
token for the writes with real persistence/rollback cost, each a dedicated
``settings`` flag deliberately kept out of ``gui/env_io.py``'s ALLOWED_KEYS
(hand-set in ``.env`` only): ``require_brokerage_connect_enabled``
(``/brokerage/connect``), ``require_automation_writes_enabled``
(``PUT /automation/schedule/interval``, ``POST /automation/resume``,
``PUT /automation/execution-mode``),
``require_strategy_writes_enabled`` (``PUT /strategy/modules`` — signal weights +
disabled-module set to ``.env``; its own flag so signal tuning cannot ride in on
the automation flag), and ``require_llm_writes_enabled`` (``PUT /llm/setting`` —
AI-capability toggle + provider-selection writes to ``.env``; its own flag so
AI-capability writes cannot ride in on either of the other two). ``GET
/strategy/matrix`` and ``GET /llm/status`` are read-only (``require_read_token``).

CORS mirrors ``state_api.py`` (``settings.CORS_ALLOWED_ORIGINS``) but allows
GET, POST and PUT (state_api is GET-only).

Honesty (CONSTRAINT #4): read endpoints 404 honestly on a cold start (no
snapshot / no account yet) and never fabricate a curve, a metric, or an equity
figure.
"""

from __future__ import annotations

import hmac
import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional, Union

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

from settings import settings
from settings import INTERVAL_MAX_SECONDS as _INTERVAL_MAX_SECONDS
from settings import validate_interval_seconds as _validate_interval_seconds

# Deployability-gate thresholds — a pure, import-free leaf module (see its own
# docstring: "Never hard-code these numbers elsewhere"). Backs GET /thresholds
# so the PWA's education panels render the SAME numbers the validation harness
# actually enforces, mirroring gui/help_content.py's live-import discipline.
from validation.thresholds import (
    DSR_MIN,
    MAX_DRAWDOWN_MAX,
    NET_SHARPE_MIN,
    PBO_MAX,
    STRESS_MAX_DRAWDOWN,
)

# Pilot layer (pure, persisted-state readers) + the gated follow write-path.
from pilots import (
    agentic,
    alerts_feed,
    attribution,
    brinson,
    calibration,
    catalog,
    commands as commands_reader,
    discovery as discovery_reader,
    forecast_skill,
    models,
    observability,
    options,
    pairs,
    performance,
    realized,
    rolling_beta,
    run_status,
    scoring,
    strategy_health,
    strategy_matrix as strategy_matrix_reader,
    symbols,
)
from pilots.follows_store import FollowsStore
from pilots.mirror import plan_follow
from pilots.scan_config_store import ScanConfigStore

# Execution / persistence — explicitly ALLOWED here (unlike state_api.py),
# forbidden only for the heavy calculation engines (see this module's AST guard
# test). ``data.historical_store`` and ``execution.kill_switch`` are imported at
# module top so tests can ``mock.patch.object(pilots_api, "HistoricalStore", ...)``.
from data.historical_store import HistoricalStore
from execution.kill_switch import GlobalKillSwitch

# The Data & Automation surface (GET/POST/PUT /automation/*) reaches the
# orchestrator daemon ONLY over loopback HTTP via gui.daemon_client — never by
# importing the daemon object directly (api.control_api.get_daemon() only
# works in the single co-hosted-process deployment shape, not the documented
# standalone one; see gui/daemon_client.py's module docstring). ``desktop.*``
# is a forbidden import for this module (see this file's AST guard test)
# precisely because it would pull main_orchestrator in transitively. Imported
# at module top, aliased, so tests can ``mock.patch.object(pilots_api, "daemon_client", ...)``.
import gui.daemon_client as daemon_client
# The interval WRITE (PUT /automation/schedule/interval) goes through the same
# allowlist-bounded .env writer the GUI Settings tab uses — NOT a bespoke file
# write — so it inherits the exact same ALLOWED_KEYS/SECRET_KEYS enforcement
# (CONSTRAINT #3) with zero new code. gui/env_io.py's own imports are stdlib +
# dotenv only (see this file's gui-import-inertness test's sibling reasoning).
import gui.env_io as env_io
from reporting.progress import read_progress

# Brokerage-connect credential intake — read-only verification + the dedicated,
# hard-scoped .env writer (see data/brokerage_credentials.py). Imported at
# module top (not lazily) so tests can `mock.patch.object(pilots_api, ...)`.
import data.robinhood_portfolio as robinhood_portfolio
import data.brokerage_credentials as brokerage_credentials

# LLM configuration status (GET /llm/status). `gui.ai_control_center` is
# stdlib-only + Streamlit-free (the headless status logic); `llm.status_store`
# is a leaf module that imports no SDK. Neither is on the AST-guard deny-list.
# NOTE: control_center_overview() calls importlib.util.find_spec on the backing
# modules (e.g. "engine.gravity_ai_runner"), which imports the `engine` package
# — kept import-inert by tests precisely so this stays safe (see the
# test_engine_package_init_stays_import_inert guard). Imported at module top so
# tests can `mock.patch.object(pilots_api, ...)`.
import gui.ai_control_center as ai_control_center
import llm.status_store as llm_status_store

# Robinhood execution-queue READ side (GET /execution-queue). Reuses the
# existing Streamlit-free, dependency-light reader the GUI Launcher tab already
# uses (json/logging/dataclasses/datetime/pathlib/typing at module top; settings
# imported lazily inside one function) — same reasoning as daemon_client/env_io
# above: don't duplicate a tested parser. This module NEVER contacts the
# Robinhood MCP and NEVER places an order — see execution/queue_builder.py's
# module docstring: a live Claude Code agent session is the ONLY actor that
# ever calls the MCP place_equity_order tool. Imported at module top so tests
# can `mock.patch.object(pilots_api, "execution_panel", ...)`.
import gui.robinhood_execution_panel as execution_panel

logger = logging.getLogger(__name__)

app = FastAPI(
    title="InvestYo Pilots API",
    description=(
        "Read/follow API for the Autopilot 'Pilots' marketplace. Serves Pilot "
        "catalog, holdings, sector allocation, recent signal-change trades, "
        "honest backtest headlines, the account portfolio, and the gated, "
        "paper-first follow write-path. Reads only already-persisted state; "
        "never calls the heavy calculation engines."
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

# The performance ?range= toggles the PWA exposes (echoed for API symmetry — no
# per-range curve is persisted yet, see pilots/performance.py).
_ALLOWED_RANGES = ("1W", "1M", "3M", "6M", "1Y", "2Y")

# Approx calendar days per range, for the equity-curve ``since`` cutoff.
_RANGE_DAYS: Dict[str, int] = {
    "1W": 7,
    "1M": 31,
    "3M": 93,
    "6M": 186,
    "1Y": 366,
    "2Y": 731,
}

_MISSING_SNAPSHOT_DETAIL = "No state snapshot yet — run the pipeline first."
_MISSING_PORTFOLIO_DETAIL = "No account snapshot yet — run the pipeline first."
_UNKNOWN_PILOT_DETAIL = "No such pilot."
_UNKNOWN_SYMBOL_DETAIL = "No such symbol in the latest snapshot."
_DEFAULT_TRADES_LIMIT = 20
_DETAIL_TRADES_LIMIT = 10


# ---------------------------------------------------------------------------
# Auth guards
# ---------------------------------------------------------------------------


def require_read_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> None:
    """Read-endpoint guard. FAIL-OPEN when ``STATE_API_TOKEN`` is unset
    (mirrors ``api/state_api.py``). Constant-time compare; token never logged."""
    token = settings.STATE_API_TOKEN
    if not token:  # unset/empty -> auth disabled (open)
        return
    presented = credentials.credentials if credentials else ""
    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


def require_command_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> None:
    """Follow write-path guard. FAIL-CLOSED when ``FOLLOW_API_TOKEN`` is unset —
    silence must never mean "open" here, since a follow produces a gated order
    queue. Constant-time compare; token never logged (CONSTRAINT #3)."""
    token = settings.FOLLOW_API_TOKEN
    if not token:
        raise HTTPException(
            status_code=403,
            detail="Follow endpoints disabled: FOLLOW_API_TOKEN not configured.",
        )
    presented = credentials.credentials if credentials else ""
    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def require_loopback(request: Request) -> None:
    """Defense-in-depth for brokerage-credential intake ONLY: reject any
    request whose client host is not loopback. ``request.client`` can be
    ``None`` under some ASGI transports — treated as NOT loopback (fail
    closed), never assumed safe. Tests override this dependency or construct
    ``TestClient(app, client=("127.0.0.1", <port>))`` for the loopback case."""
    host = request.client.host if request.client else None
    if host not in _LOOPBACK_HOSTS:
        raise HTTPException(
            status_code=403,
            detail="Brokerage credential endpoints are loopback-only.",
        )


def require_brokerage_connect_enabled() -> None:
    """FAIL-CLOSED master-switch guard for ``/brokerage/connect`` and
    ``/brokerage/disconnect``. ``settings.BROKERAGE_CONNECT_ENABLED`` is
    deliberately NOT GUI-writable (gui/env_io.py) — it must be hand-set in
    ``.env``. ``/brokerage/status`` is read-only and NOT gated by this flag."""
    if not settings.BROKERAGE_CONNECT_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Brokerage connect is disabled (BROKERAGE_CONNECT_ENABLED=false).",
        )


def require_automation_writes_enabled() -> None:
    """FAIL-CLOSED master-switch guard for the two Data & Automation writes
    with a real persistence/rollback cost: ``PUT /automation/schedule/interval``
    (an ``.env`` edit) and ``POST /automation/resume`` (re-enabling live order
    submission when ``ADVISORY_ONLY=False``). Mirrors
    ``require_brokerage_connect_enabled`` exactly. ``settings.AUTOMATION_WRITES_ENABLED``
    is deliberately NOT GUI-writable — hand-set in ``.env`` only.

    ``POST /automation/run`` and ``POST /automation/pause`` are NOT gated by
    this — they sit behind ``require_command_token`` alone, matching
    ``POST /pilots/{id}/follow``'s existing risk posture (an order-queue write
    under ``FOLLOW_API_TOKEN`` alone, no master flag)."""
    if not settings.AUTOMATION_WRITES_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Automation writes are disabled (AUTOMATION_WRITES_ENABLED=false).",
        )


def require_strategy_writes_enabled() -> None:
    """FAIL-CLOSED master-switch guard for ``PUT /strategy/modules`` (signal
    weights + disabled-module set -> ``.env``). A DEDICATED flag
    (``settings.STRATEGY_WRITES_ENABLED``), NOT ``AUTOMATION_WRITES_ENABLED``:
    that one was scoped to the daemon interval and kill-switch resume, and
    signal-weight tuning changes WHAT THE PLATFORM RECOMMENDS. Mirrors
    ``require_brokerage_connect_enabled`` exactly — deliberately NOT GUI-writable,
    hand-set in ``.env`` only. ``GET /strategy/matrix`` is read-only and NOT gated
    by this flag (``require_read_token`` alone, matching ``/brokerage/status``)."""
    if not settings.STRATEGY_WRITES_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Strategy writes are disabled (STRATEGY_WRITES_ENABLED=false).",
        )


def require_llm_writes_enabled() -> None:
    """FAIL-CLOSED master-switch guard for ``PUT /llm/setting`` (AI-capability
    toggle + provider-selection writes -> ``.env``). A DEDICATED flag
    (``settings.LLM_WRITES_ENABLED``), NOT ``AUTOMATION_WRITES_ENABLED`` or
    ``STRATEGY_WRITES_ENABLED``: those were scoped to the daemon interval/
    kill-switch resume and to signal-weight tuning respectively — flipping
    which LLM provider narrates a rationale, or whether the Gravity AI runner
    / Opal research agent can fire, is its own risk class and must not ride
    in on either. Mirrors ``require_strategy_writes_enabled`` exactly —
    deliberately NOT GUI-writable, hand-set in ``.env`` only. ``GET /llm/status``
    is read-only and NOT gated by this flag (``require_read_token`` alone,
    matching ``/brokerage/status`` and ``GET /strategy/matrix``)."""
    if not settings.LLM_WRITES_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="LLM writes are disabled (LLM_WRITES_ENABLED=false).",
        )


def require_agentic_discovery_enabled() -> None:
    """FAIL-CLOSED master-switch guard for ``PUT /agentic/scan-config`` (Robinhood
    broker-scan config -> ``output/scan_configs.json``, consumed by the
    ``agentic-discovery`` skill). A DEDICATED flag
    (``settings.AGENTIC_DISCOVERY_ENABLED``), NOT ``AUTOMATION_WRITES_ENABLED``,
    ``STRATEGY_WRITES_ENABLED``, or ``LLM_WRITES_ENABLED``: this changes WHAT THE
    AGENT DISCOVERS (which symbols get scanned and fed toward the gated order
    queue) and must not ride in on any of those. Mirrors
    ``require_strategy_writes_enabled`` exactly — deliberately NOT GUI-writable,
    hand-set in ``.env`` only. ``GET /agentic/status`` and ``GET
    /agentic/discovery`` are read-only and NOT gated by this flag
    (``require_read_token`` alone, matching ``GET /strategy/matrix`` and ``GET
    /llm/status``)."""
    if not settings.AGENTIC_DISCOVERY_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Agentic discovery writes are disabled (AGENTIC_DISCOVERY_ENABLED=false).",
        )


if not settings.STATE_API_TOKEN:
    logger.warning(
        "STATE_API_TOKEN not set — Pilots read endpoints are UNAUTHENTICATED. "
        "Set STATE_API_TOKEN to require a bearer token before exposing this API."
    )
if not settings.FOLLOW_API_TOKEN:
    logger.warning(
        "FOLLOW_API_TOKEN not set — follow endpoints (GET/PUT /follows, "
        "POST /pilots/{id}/follow) are DISABLED (fail-closed, 403 on every "
        "call). Set FOLLOW_API_TOKEN to enable them."
    )


# ---------------------------------------------------------------------------
# Path resolvers (read live from settings so tests can monkeypatch OUTPUT_DIR)
# ---------------------------------------------------------------------------


def _snapshot_path() -> str:
    """Resolve ``output/state_snapshot.json`` from live settings per call."""
    return str(settings.OUTPUT_DIR / "state_snapshot.json")


def _history_dir() -> str:
    """Resolve the rotated-snapshot history dir from live settings per call."""
    return str(settings.OUTPUT_DIR / "history")


def _options_matrix_path() -> str:
    """Resolve ``output/options_matrix.json`` from live settings per call."""
    return str(settings.OUTPUT_DIR / "options_matrix.json")


def _pairs_snapshot_path() -> str:
    """Resolve ``output/pairs.json`` from live settings per call."""
    return str(settings.OUTPUT_DIR / "pairs.json")


def _reports_dir() -> Optional[str]:
    """Directory of ``*_validation_summary.json`` files.

    ``None`` -> ``pilots.performance`` uses its default ``reports/`` dir. Tests
    monkeypatch this to point at ``tests/fixtures``.
    """
    return None


def _validation_history_dir() -> str:
    """Directory of ``*_validation_history.jsonl`` run-over-run files.

    Independent of ``_history_dir()`` (the rotated STATE-SNAPSHOT history used
    by ``scoring.pilot_trades`` — a different concept entirely) and of
    ``_reports_dir()`` (the CURRENT validation summary, not its history).
    Defaults to the real ``reports/history`` dir; tests monkeypatch this to
    point at a fixture directory.
    """
    return "reports/history"


def _decision_log_path():
    """Resolve ``output/decision_log.jsonl`` from live settings per call.

    The WRITE side (``POST /decisions``) and the READ side
    (``pilots.calibration`` recommendation-tracking / recent-decisions) both
    resolve from ``settings.OUTPUT_DIR`` so they agree and stay isolatable under
    a tests-patched OUTPUT_DIR (matching ``_snapshot_path`` et al.)."""
    return settings.OUTPUT_DIR / "decision_log.jsonl"


def _load_snapshot() -> Optional[dict]:
    """Load the current state snapshot, or ``None`` (never raises)."""
    return scoring.load_snapshot(_snapshot_path())


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class FollowUpsertRequest(BaseModel):
    """Body for ``PUT /follows``. ``amount == 0`` cancels the follow."""

    pilot_id: str = Field(..., min_length=1)
    amount: float = Field(..., ge=0.0)


class FollowRequest(BaseModel):
    """Body for ``POST /pilots/{id}/follow``. Must allocate a positive amount."""

    amount: float = Field(..., gt=0.0)


class PauseRequest(BaseModel):
    """Body for ``POST /automation/pause``. A non-empty reason is required —
    mirrors ``docs/RUNBOOK.md`` §6's own pause-procedure example, and guards
    against a fat-fingered click leaving no record of why."""

    reason: str = Field(..., min_length=1)


class ResumeRequest(BaseModel):
    """Body for ``POST /automation/resume``. ``confirm`` guards against a
    fat-fingered click (not an attacker — the real gates are the command
    token, AUTOMATION_WRITES_ENABLED, and the ADVISORY_ONLY check)."""

    confirm: bool = Field(..., description="Must be true.")
    reason: str = Field(..., min_length=1)


class IntervalUpdateRequest(BaseModel):
    """Body for ``PUT /automation/schedule/interval``. ``0`` disables the
    daemon's internal timer (on-demand only); otherwise MUST be in
    ``[settings.INTERVAL_MIN_SECONDS, settings.INTERVAL_MAX_SECONDS]``.
    Validation bounds match ``api/control_api.py``'s equivalent body — the
    shared policy function is what keeps all three from drifting apart (see
    ``settings.py``'s docstring on it)."""

    interval_seconds: int = Field(..., ge=0, le=_INTERVAL_MAX_SECONDS)

    @field_validator("interval_seconds")
    @classmethod
    def _validate(cls, v: int) -> int:
        return _validate_interval_seconds(v)


class ExecutionModeUpdateRequest(BaseModel):
    """Body for ``PUT /automation/execution-mode``."""
    mode: Literal["live", "paper", "simulation", "advisory"]
    advisory_only: bool


class DecisionCreateRequest(BaseModel):
    """Body for ``POST /decisions`` — append one operator decision to the
    journal (``gui/decision_log.py``). ``action_taken`` is validated against the
    ``{acted, passed, modified}`` set (422 with a stable ``invalid_action`` tag
    otherwise — the frontend branches on the tag, not the message)."""

    symbol: str = Field(..., min_length=1)
    action_taken: str = Field(..., min_length=1)
    signal_action: str = Field(default="")
    conviction: Optional[float] = Field(default=None)
    notes: str = Field(default="")
    signal_ts: str = Field(default="")


class BrokerageConnectRequest(BaseModel):
    """Body for ``POST /brokerage/connect``. Never logged (CONSTRAINT #3) —
    Pydantic's default repr is not invoked anywhere in this module's logging."""

    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    mfa_code: str = Field(
        default="",
        description=(
            "Current 6-digit code from the user's authenticator app. "
            "Required — interactive MFA prompting is not available over "
            "HTTP, so a login attempt with no MFA code is treated as a "
            "verification failure. Used once to verify the login, then "
            "discarded — never persisted to .env or anywhere else."
        ),
    )


class BrinsonFachlerRow(BaseModel):
    """One sector row of the wire-format matrix for
    ``POST /portfolio/attribution/brinson-fachler``. All weight/return fields
    are PERCENT (e.g. ``28.0`` for 28%, not the fraction ``0.28`` the engine
    itself consumes) — ``pilots.brinson.build_brinson_fachler_frames`` does
    the ``/100`` conversion server-side."""

    sector: str = Field(..., min_length=1)
    portfolio_weight_pct: float = 0.0
    portfolio_return_pct: float = 0.0
    benchmark_weight_pct: float = 0.0
    benchmark_return_pct: float = 0.0


class BrinsonFachlerRequest(BaseModel):
    """Body for ``POST /portfolio/attribution/brinson-fachler``."""

    rows: List[BrinsonFachlerRow] = Field(..., min_length=1)


# Stable 422 tags for PUT /strategy/modules validation failures — the frontend
# branches on these, never on a message string.
_MODULE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class StrategyModulesUpdateRequest(BaseModel):
    """Body for ``PUT /strategy/modules``. Full idempotent replacement of the
    two ``.env`` keys ``SIGNAL_WEIGHTS`` + ``DISABLED_SIGNAL_MODULES``.

    ``weights`` MUST cover every currently-known module: ``write_setting`` replaces
    the WHOLE ``SIGNAL_WEIGHTS`` JSON, so an omitted module would be silently zeroed
    (``_effective_weights.get(name, 0.0)``). The PWA always echoes back the full set
    it read, so full coverage is free. Validation raises ``ValueError`` with a
    stable tag string (``incomplete_weights`` / ``weight_out_of_bounds`` /
    ``pinned_zero_module`` / ``invalid_module_name`` / ``unknown_module``); the
    ``/strategy/modules`` handler maps these to 422 with the tag preserved."""

    weights: Dict[str, float] = Field(..., max_length=128)
    disabled: List[str] = Field(default_factory=list, max_length=128)


class LlmSettingUpdateRequest(BaseModel):
    """Body for ``PUT /llm/setting``. A single-key ``.env`` write: ``key`` is
    either a capability's ``toggle_key`` (bool, e.g. ``LLM_COMMENTARY_ENABLED``)
    or a ``provider_selector_setting`` (str, e.g.
    ``LLM_COMMENTARY_RATIONALE_PROVIDER`` -> ``"claude"``/``"gemini"``/``"none"``).
    Unlike ``PUT /strategy/modules`` this is NOT a multi-key atomic write — each
    AI-capability toggle/selector is an independent scalar, so
    ``gui.env_io.write_setting`` (single-key) is the right primitive, not
    ``write_many_atomic``. ``key`` is validated against
    ``gui.ai_control_center.validate_toggle_write`` (CONSTRAINT #3: secret keys
    are rejected, as is any key outside ``gui.env_io.ALLOWED_KEYS``) before the
    write is attempted."""

    key: str = Field(..., min_length=1)
    value: Union[bool, str]


class ScanConfigRequest(BaseModel):
    """Body for ``PUT /agentic/scan-config``. Create/replace ONE named Robinhood
    broker-scan config in ``output/scan_configs.json`` (``pilots.scan_config_store.
    ScanConfigStore``), consumed by the ``agentic-discovery`` Claude Code skill —
    NOT an ``.env`` write (scan configs are structured, multi-row, operator-editable
    data, same shape as a Pilot follow, not a global tunable). ``filters`` is stored
    verbatim; this API has no knowledge of the Robinhood scanner's filter schema
    (``get_scanner_filter_specs`` on the Robinhood MCP is the source of truth for
    that — only the discovery skill calls it), so nothing here validates filter
    keys/values beyond basic JSON-ability."""

    name: str = Field(..., min_length=1, max_length=64)
    filters: Dict[str, Any] = Field(default_factory=dict, max_length=64)
    enabled: bool = True


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _pilot_summary(pilot: Any, snapshot: Optional[dict], store: FollowsStore) -> Dict[str, Any]:
    """The PilotSummary contract (webapp/src/api/types.ts): identity + headline
    metrics + follow proxies + holdings_count + ``long_only``.

    Shared by BOTH the marketplace list (``/pilots``) and the detail endpoint
    (``/pilots/{id}``, whose ``PilotDetail extends PilotSummary``) so the two
    responses can never silently drift apart again.
    """
    holdings_count = len(scoring.pilot_holdings(pilot, snapshot)) if snapshot is not None else 0
    return {
        "id": pilot.id,
        "name": pilot.name,
        "category": pilot.category,
        "description": pilot.description,
        "headline": performance.pilot_headline(pilot, reports_dir=_reports_dir()),
        "holdings_count": holdings_count,
        "aum_proxy": store.aum_for(pilot.id),
        "followers_proxy": store.followers_for(pilot.id),
        "long_only": pilot.long_only,
    }


def _serialize_portfolio(snap: Any) -> Dict[str, Any]:
    """Reshape an ``AccountSnapshot`` into the PWA ``Portfolio`` contract
    (webapp/src/api/types.ts).

    ``AccountSnapshot.to_dict()`` emits ``positions`` as a *dict* keyed by symbol
    with ``quantity``/``average_cost`` field names and carries no
    ``position_count``/``total_unrealized_pl``/``source`` — none of which match
    the frontend's ``Portfolio``/``PortfolioPositionView``. This serializer maps
    them across without touching ``to_dict()`` itself (whose shape is load-bearing
    for the JSON-cache ``from_dict`` round-trip). Every value is read from the real
    snapshot — nothing is fabricated (CONSTRAINT #4); ``source`` is honestly
    ``"db"`` because this endpoint reads DB-first via ``HistoricalStore``.
    """
    data = snap.to_dict()
    raw_positions = data.get("positions") or {}
    positions: List[Dict[str, Any]] = []
    total_unrealized_pl = 0.0
    for pos in raw_positions.values():
        upl = pos.get("unrealized_pl")
        if isinstance(upl, (int, float)) and upl == upl:  # skip None / NaN
            total_unrealized_pl += float(upl)
        positions.append(
            {
                "symbol": pos.get("symbol"),
                "qty": pos.get("quantity"),
                "avg_cost": pos.get("average_cost"),
                "current_price": pos.get("current_price"),
                "market_value": pos.get("market_value"),
                "unrealized_pl": pos.get("unrealized_pl"),
                "unrealized_pl_pct": pos.get("unrealized_pl_pct"),
                "name": pos.get("name"),
            }
        )
    return {
        "total_equity": data.get("total_equity"),
        "buying_power": data.get("buying_power"),
        "total_unrealized_pl": total_unrealized_pl,
        "total_dividends": data.get("total_dividends"),
        "position_count": len(positions),
        "positions": positions,
        "fetched_at": data.get("fetched_at"),
        "source": "db",
        "is_stale": snap.is_stale(),
        "age_hours": snap.age_hours(),
    }


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> Dict[str, str]:
    """Liveness of this API process. Always open, no auth."""
    return {"status": "ok"}


@app.get("/pilots", dependencies=[Depends(require_read_token)])
def list_pilots() -> List[Dict[str, Any]]:
    """Return every Pilot with its headline metrics, follow proxies and the
    count of names it currently holds (0 when no snapshot exists — the list is
    never 404'd on a cold start)."""
    snapshot = _load_snapshot()
    store = FollowsStore()
    return [_pilot_summary(p, snapshot, store) for p in catalog.list_pilots()]


@app.get("/pilots/{pilot_id}", dependencies=[Depends(require_read_token)])
def get_pilot_detail(pilot_id: str) -> Any:
    """Full Pilot detail: identity + top-N holdings + sector allocation +
    headline + recent signal-change trades + ``as_of``.

    404s on an unknown Pilot id. When no snapshot exists yet the Pilot is still
    returned with empty holdings/sector/trades, ``as_of=null`` and an honest
    ``reason`` — never fabricated (CONSTRAINT #4)."""
    pilot = catalog.get_pilot(pilot_id)
    if pilot is None:
        raise HTTPException(status_code=404, detail=_UNKNOWN_PILOT_DETAIL)

    snapshot = _load_snapshot()
    store = FollowsStore()
    # Start from the full PilotSummary contract (headline + proxies + long_only)
    # so detail carries every summary field it extends, then layer on the
    # detail-only identity + holdings fields.
    payload = _pilot_summary(pilot, snapshot, store)
    payload["validation_strategy_id"] = pilot.validation_strategy_id
    payload["weights"] = dict(pilot.weights)

    if snapshot is None:
        payload.update(
            {
                "holdings": [],
                "sector_allocation": [],
                "recent_trades": [],
                "as_of": None,
                "reason": _MISSING_SNAPSHOT_DETAIL,
            }
        )
        return payload

    holdings = scoring.pilot_holdings(pilot, snapshot)
    trades = scoring.pilot_trades(pilot, history_dir=_history_dir())
    payload.update(
        {
            "holdings": holdings,
            "sector_allocation": scoring.sector_allocation(holdings),
            "recent_trades": trades[-_DETAIL_TRADES_LIMIT:],
            "as_of": snapshot.get("timestamp"),
            "reason": None,
        }
    )
    return payload


@app.get("/pilots/{pilot_id}/performance", dependencies=[Depends(require_read_token)])
def get_pilot_performance(
    pilot_id: str,
    range: str = Query("1M"),  # noqa: A002 - matches the ?range= query param name
) -> Dict[str, Any]:
    """Honest backtest performance for a Pilot. 404 on unknown Pilot, 422 on an
    out-of-set ``range``. ``curve`` is the real downsampled base-100 OOS equity
    series persisted by the harness, tail-sliced to ``range`` — ``null`` when the
    Pilot has no backtest or the summary predates the field; never synthesized
    (CONSTRAINT #4). ``benchmark`` is the buy-&-hold-of-the-underlying overlay;
    ``macro_benchmark`` is a SEPARATE, explicitly-labeled SPY (broad-market)
    overlay — ``null`` when SPY was unavailable or the underlying already IS SPY
    (redundant), never fabricated."""
    pilot = catalog.get_pilot(pilot_id)
    if pilot is None:
        raise HTTPException(status_code=404, detail=_UNKNOWN_PILOT_DETAIL)
    if range not in _ALLOWED_RANGES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid range '{range}'. Allowed: {list(_ALLOWED_RANGES)}.",
        )
    return performance.pilot_performance(pilot, range=range, reports_dir=_reports_dir())


@app.get("/pilots/{pilot_id}/holdings", dependencies=[Depends(require_read_token)])
def get_pilot_holdings(pilot_id: str) -> List[Dict[str, Any]]:
    """Top-N Pilot holdings. 404 on unknown Pilot; empty list when no snapshot."""
    pilot = catalog.get_pilot(pilot_id)
    if pilot is None:
        raise HTTPException(status_code=404, detail=_UNKNOWN_PILOT_DETAIL)
    snapshot = _load_snapshot()
    if snapshot is None:
        return []
    return scoring.pilot_holdings(pilot, snapshot)


@app.get("/pilots/{pilot_id}/trades", dependencies=[Depends(require_read_token)])
def get_pilot_trades(
    pilot_id: str,
    limit: int = Query(_DEFAULT_TRADES_LIMIT, ge=1, le=500),
) -> List[Dict[str, Any]]:
    """Recent signal-change trades (ENTER/EXIT/REWEIGHT) for a Pilot, most
    recent last, capped at ``limit``. 404 on unknown Pilot; empty when history
    holds fewer than two snapshots."""
    pilot = catalog.get_pilot(pilot_id)
    if pilot is None:
        raise HTTPException(status_code=404, detail=_UNKNOWN_PILOT_DETAIL)
    trades = scoring.pilot_trades(pilot, history_dir=_history_dir())
    return trades[-limit:]


@app.get("/universe", dependencies=[Depends(require_read_token)])
def get_universe() -> Dict[str, Any]:
    """The tracked-symbol universe (held positions ∪ watchlist) for the PWA's
    symbol autocomplete — every entry resolves to a real ``GET /symbols/{ticker}``
    detail page.

    Reads only persisted state (the snapshot's ``signals[]``) — never calls an
    engine. Returns ``{"symbols": []}`` on a cold start (no snapshot yet); never
    404s and never 500s (CONSTRAINT #6). Each row's ``action`` is the holding-aware
    advisory action when present, else the raw signal action, else ``null`` — it
    only decorates the suggestion and is never fabricated (CONSTRAINT #4)."""
    return {"symbols": symbols.list_universe(_load_snapshot())}


@app.get("/symbols/{ticker}", dependencies=[Depends(require_read_token)])
def get_symbol_detail(ticker: str) -> Any:
    """Per-symbol detail for one ticker from the latest persisted snapshot, plus
    the reverse cross-link of which Pilots hold it and at what weight.

    Reads only persisted state — never calls an engine. Two honest 404s, checked
    in this order: cold start (no snapshot yet → ``_MISSING_SNAPSHOT_DETAIL``)
    and unknown ticker (not in the snapshot's ``signals[]`` →
    ``_UNKNOWN_SYMBOL_DETAIL``). An absent per-symbol field is ``null``, never
    ``0.0`` (CONSTRAINT #4); a non-positive price is nulled. "Held by" means the
    symbol survives a Pilot's blend into its advertised top-N. Case-insensitive
    ticker. Never 500s (CONSTRAINT #6)."""
    snapshot = _load_snapshot()
    if snapshot is None:
        raise HTTPException(status_code=404, detail=_MISSING_SNAPSHOT_DETAIL)
    detail = symbols.symbol_detail(snapshot, ticker)
    if detail is None:
        raise HTTPException(status_code=404, detail=_UNKNOWN_SYMBOL_DETAIL)
    return detail


@app.get("/symbols/{ticker}/forecast", dependencies=[Depends(require_read_token)])
def get_symbol_forecast(
    ticker: str,
    horizon: int = Query(30, ge=1, le=365),
) -> Dict[str, Any]:
    """Per-symbol forecast reliability curve + live inverse-RMSE skill weights +
    pending/completed counts, from the ``forecast_errors`` history.

    Reads persisted DB state only (no engine, no network). Returns empty
    collections + an honest ``reason`` when no forecast history exists yet — NOT
    a 404 (the symbol is valid; there's simply nothing tracked). A bin with too
    few samples has ``mean_pct_error=null``; never fabricated (CONSTRAINT #4)."""
    return forecast_skill.forecast_skill_view(ticker, horizon_days=horizon)


@app.get("/symbols/{ticker}/rolling-beta", dependencies=[Depends(require_read_token)])
def get_symbol_rolling_beta(
    ticker: str,
    window: int = Query(60, ge=5, le=252),
) -> Dict[str, Any]:
    """Time-varying beta vs SPY for one ticker (rolling covariance/variance),
    distinct from the single point-in-time static ``Beta`` column elsewhere in
    the platform.

    Computed on demand from ``HistoricalStore``-cached daily bars (see
    ``pilots/rolling_beta.py`` for the full contract) — never imports
    ``processing_engine``. Returns an empty ``series`` + an honest ``reason``
    (not a 404 — the symbol is valid, there's simply not enough cached history
    yet) when bars for the symbol or SPY aren't cached, or the date-aligned
    overlap is shorter than ``window`` trading days. Never 500s (CONSTRAINT #6)."""
    return rolling_beta.rolling_beta_view(ticker, window=window)


@app.get("/symbols/{ticker}/options", dependencies=[Depends(require_read_token)])
def get_symbol_options(ticker: str) -> Any:
    """The persisted options premium-selling directive for one ticker
    (Strategy/Action, short/long strike + delta legs, net premium, ATM Greeks,
    integrity verdict).

    Reads only ``output/options_matrix.json`` (written upstream by
    ``reporting/options_snapshot.py`` when ``OPTIONS_MATRIX_ENABLED`` is on) —
    never imports ``technical_options_engine``. Returns ``{directive: null,
    reason}`` (200, not 404) when the matrix is disabled/absent or the symbol
    isn't in it, so the PWA renders an honest "no options data yet"."""
    directive = options.symbol_options(ticker, path=_options_matrix_path())
    if directive is None:
        return {
            "symbol": str(ticker or "").upper(),
            "directive": None,
            "reason": "No options directive for this symbol yet.",
        }
    return {"symbol": str(ticker or "").upper(), "directive": directive, "reason": None}


@app.get("/portfolio", dependencies=[Depends(require_read_token)])
def get_portfolio() -> Any:
    """Serialize the latest account snapshot (DB-first, read-only, no
    Robinhood login) plus ``is_stale`` / ``age_hours``.

    404s honestly when no account snapshot has ever been stored. Dead-letter
    resilient: a cold/unavailable DB degrades to the same 404, never a 500."""
    try:
        store = HistoricalStore(readonly=True)
        snap = store.latest_account_snapshot()
    except Exception as exc:  # noqa: BLE001 - dead-letter: cold DB -> honest 404
        logger.warning("pilots_api: latest_account_snapshot failed: %s", exc)
        snap = None
    if snap is None:
        return JSONResponse(status_code=404, content={"detail": _MISSING_PORTFOLIO_DETAIL})
    try:
        return _serialize_portfolio(snap)
    except Exception as exc:  # noqa: BLE001 - defensive: malformed snapshot -> 404
        logger.warning("pilots_api: portfolio serialization failed: %s", exc)
        return JSONResponse(status_code=404, content={"detail": _MISSING_PORTFOLIO_DETAIL})


@app.get("/portfolio/equity-curve", dependencies=[Depends(require_read_token)])
def get_equity_curve(
    range: str = Query("1Y"),  # noqa: A002 - matches the ?range= query param name
) -> Dict[str, Any]:
    """Account equity curve from stored snapshots, oldest→newest.

    Returns the ``{range, curve}`` envelope the PWA expects (client.ts
    ``getEquityCurve`` / ``CurvePoint``), mapping each stored snapshot to
    ``{date: <fetched_at ISO date>, value: <total_equity>}``. ``curve`` is an
    empty list — never fabricated — when nothing has been stored yet or the DB is
    cold (CONSTRAINT #4). An unknown ``range`` is treated leniently as "all
    history"."""
    since: Optional[datetime] = None
    days = _RANGE_DAYS.get(range)
    if days:
        since = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        store = HistoricalStore(readonly=True)
        df = store.account_snapshot_history(since=since)
    except Exception as exc:  # noqa: BLE001 - dead-letter: cold DB -> empty curve
        logger.warning("pilots_api: account_snapshot_history failed: %s", exc)
        return {"range": range, "curve": []}
    if df is None or df.empty:
        return {"range": range, "curve": []}
    # account_snapshot_history is ordered ascending by fetched_at, so records are
    # already oldest→newest. Normalize fetched_at to an ISO date (YYYY-MM-DD) to
    # match CurvePoint's "ISO date" semantics.
    df = df.copy()
    df["fetched_at"] = df["fetched_at"].astype(str).str[:10]
    curve: List[Dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        equity = row.get("total_equity")
        if equity is None:
            continue
        try:
            value = float(equity)
        except (TypeError, ValueError):
            continue
        if value != value:  # NaN guard — skip rather than fabricate a point
            continue
        curve.append({"date": row.get("fetched_at"), "value": value})
    return {"range": range, "curve": curve}


@app.get("/portfolio/realized", dependencies=[Depends(require_read_token)])
def get_realized_performance() -> Dict[str, Any]:
    """Realized broker P&L (win rate / profit factor / realized P&L / holding
    stats) reconstructed by PURE FIFO lot-matching of the Robinhood filled-order
    history — the account's TRUE realized performance, distinct from any internal
    paper P&L.

    Cache-only: reads the warm ``cache/robinhood_orders.json`` and NEVER triggers
    a live Robinhood login on this request path. NaN summary fields (win rate /
    profit factor when there are no trades) serialize as ``null``, never a
    fabricated ``0.0`` (CONSTRAINT #4); ``available=false`` when nothing is cached
    yet. Never 500s (CONSTRAINT #6)."""
    return realized.realized_performance_view()


# Bounds a pathologically large book's bars-fetch fanout for the correlation-
# cluster section below. 40 comfortably covers any realistic retail portfolio;
# symbols beyond this are simply not included in clustering (never fabricated).
_ATTRIBUTION_MAX_SYMBOLS = 40


def _held_market_values(account_snap: Any) -> Dict[str, float]:
    """``{symbol: market_value}`` for every position with quantity > 0.

    A non-positive or unparseable ``market_value`` is preserved as ``NaN``
    (never coerced to a fabricated ``0.0``) so ``pilots.attribution`` can
    honestly exclude it from weighting rather than silently zero-weighting a
    real position (CONSTRAINT #4)."""
    if account_snap is None:
        return {}
    positions = getattr(account_snap, "positions", None) or {}
    out: Dict[str, float] = {}
    for sym, p in positions.items():
        try:
            qty = float(getattr(p, "quantity", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        try:
            mv_f = float(getattr(p, "market_value", None))
        except (TypeError, ValueError):
            mv_f = float("nan")
        out[str(sym).upper()] = mv_f
    return out


def _attribution_returns_df(symbols_list: List[str], lookback_days: int) -> Any:
    """Build a daily-returns DataFrame from ``HistoricalStore``-cached bars.

    Reuses the SAME incrementally-cached bars source the rest of the platform
    reads (``HistoricalStore.get_bars()``) rather than a fresh live yfinance
    download via ``research_engine.fetch_returns_for_clustering`` — a symbol
    whose bars are already persisted from a prior advisory/orchestrator cycle
    needs no network call at all. Per-symbol try/except (one bad symbol can't
    abort the batch); returns an empty DataFrame on total failure
    (CONSTRAINT #4 — no fabricated rows, CONSTRAINT #6 — never raises)."""
    import pandas as pd

    if not symbols_list:
        return pd.DataFrame()
    store = HistoricalStore(readonly=True)
    fetch_days = lookback_days + 15  # small buffer so pct_change() keeps `lookback_days` rows
    closes: Dict[str, Any] = {}
    for sym in symbols_list[:_ATTRIBUTION_MAX_SYMBOLS]:
        try:
            bars = store.get_bars(sym, lookback_days=fetch_days)
        except Exception as exc:  # noqa: BLE001 - dead-letter per symbol
            logger.debug("attribution: get_bars(%s) failed: %s", sym, exc)
            continue
        if bars is None or bars.empty or "Close" not in bars.columns:
            continue
        closes[sym] = bars["Close"]
    if not closes:
        return pd.DataFrame()
    prices = pd.DataFrame(closes).sort_index()
    return prices.pct_change().dropna(how="all")


@app.get("/portfolio/attribution", dependencies=[Depends(require_read_token)])
def get_portfolio_attribution(
    lookback_days: int = Query(60, ge=20, le=252),
) -> Dict[str, Any]:
    """Portfolio-level factor exposure + correlation-cluster attribution.

    Two independent, honestly-degrading sections (see ``pilots/attribution.py``
    for the full contract):

    * ``factor_exposure`` — position-size-weighted average Value/Quality/LowVol/
      Size/Composite z-score across HELD symbols matched in the latest pipeline
      snapshot (``output/state_snapshot.json`` via ``pilots.scoring.load_snapshot``).
      A held symbol absent from the snapshot contributes nothing (never
      zero-filled — CONSTRAINT #4); ``coverage`` reports how much of portfolio
      value the exposure numbers actually describe.
    * ``correlation_clusters`` — hierarchical clustering
      (``research_engine.compute_correlation_clusters``) of held symbols' daily
      returns, built from ``HistoricalStore.get_bars()`` (the same
      incrementally-cached bars source the rest of the platform uses — no
      separate live yfinance download). Empty with an honest ``reason`` when
      there are no held positions, no DB-backed price history, or clustering is
      unavailable (e.g. scipy not installed).

    Cold-start (no account snapshot, empty book, no pipeline snapshot yet)
    degrades to the honest empty shape for both sections rather than a 404 —
    this is a portfolio-level view, not a single-resource lookup
    (CONSTRAINT #6)."""
    try:
        account_snap = HistoricalStore(readonly=True).latest_account_snapshot()
    except Exception as exc:  # noqa: BLE001 - dead-letter: cold DB -> empty book
        logger.warning("pilots_api: attribution account snapshot read failed: %s", exc)
        account_snap = None

    held_market_values = _held_market_values(account_snap)

    pipeline_snap = _load_snapshot()
    factor_exposure = attribution.portfolio_factor_exposure(pipeline_snap, held_market_values)

    try:
        returns_df = _attribution_returns_df(sorted(held_market_values), lookback_days)
    except Exception as exc:  # noqa: BLE001 - dead-letter: never crash the endpoint
        logger.warning("pilots_api: attribution returns fetch failed: %s", exc)
        returns_df = None

    correlation_clusters = attribution.portfolio_correlation_clusters(
        returns_df,
        held_market_values,
        distance_threshold=settings.CORRELATION_CLUSTER_THRESHOLD,
    )
    correlation_clusters["lookback_days"] = lookback_days

    return {
        "as_of": factor_exposure.get("as_of"),
        "factor_exposure": factor_exposure,
        "correlation_clusters": correlation_clusters,
    }


@app.post(
    "/portfolio/attribution/brinson-fachler",
    dependencies=[Depends(require_read_token)],
)
def post_brinson_fachler_attribution(body: BrinsonFachlerRequest) -> Dict[str, Any]:
    """Manual-input Brinson-Fachler sector attribution calculator.

    STATELESS — nothing is persisted; this is the POST-with-a-body analogue
    of the read-only ``GET /portfolio/attribution`` above, not a write, hence
    the fail-open ``require_read_token`` guard rather than the command token.

    Distinct from ``GET /portfolio/attribution``'s ``factor_exposure`` /
    ``correlation_clusters`` sections (which are auto-derived from real
    holdings + the pipeline snapshot): this endpoint's sector-level
    portfolio/benchmark weight+return matrix is entirely OPERATOR-SUPPLIED —
    point-in-time sector-level benchmark returns aren't available anywhere in
    this platform, so there is no honest way to auto-derive this. Mirrors the
    legacy Streamlit Command Center's interactive
    ``gui/panels/report_viewer.py::_render_brinson_fachler_section`` calculator.

    Delegates to ``pilots.brinson.compute_brinson_fachler`` (see that module's
    docstring for the wire-format-percent -> engine-format-fraction conversion
    and the one documented residual-risk case: a request whose rows pass this
    endpoint's own pre-validation but still trip an internal exception in
    ``EvaluationEngine._calculate_brinson_fachler_compat`` gets that engine's
    pre-existing all-zero fallback shape back, not a 500 — this endpoint does
    not attempt to distinguish that case from a genuine all-zero result).

    422 (not 500) on a structurally unusable matrix (e.g. every row has a
    blank sector name) — the request body schema itself already rejects an
    empty ``rows`` list."""
    rows = [r.model_dump() for r in body.rows]
    try:
        result = brinson.compute_brinson_fachler(rows)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result["validation_warnings"] = brinson.validate_brinson_fachler_rows(rows)
    return result


@app.get("/observability/summary", dependencies=[Depends(require_read_token)])
def get_observability_summary(
    range: str = Query("1Y"),  # noqa: A002 - matches the ?range= query param name
    horizon: int = Query(30, ge=1, le=365),
) -> Dict[str, Any]:
    """Composite Mission-Control summary — the PWA's port of the retired
    Streamlit Command Center's Observability tab (bounded to four sections):
    portfolio risk metrics (Sharpe/Calmar/MaxDD/MaxDD-duration/CAGR), the
    account equity curve + drawdown, the current macro-regime overlay, the
    portfolio-wide forecast-skill reliability curve + weights, and the last
    ~100 risk-gate block-log entries.

    Composes FOUR independently-degrading sections (``pilots.observability
    .observability_summary`` — see that module's docstring for the full
    per-section contract); one section's cold-start/failure never blocks the
    other three, and every section carries its own honest ``reason`` when
    empty. ``range`` zooms the equity curve only (risk metrics always use the
    full history — Sharpe/CAGR need enough samples to be meaningful);
    ``horizon`` selects the forecast-skill horizon (10/30/60/90 are the
    horizons the pipeline actually forecasts, but any 1-365 is accepted
    leniently, matching ``GET /symbols/{ticker}/forecast``). Never raises
    (CONSTRAINT #6); never fabricates a metric (CONSTRAINT #4)."""
    return observability.observability_summary(
        equity_range=range, horizon_days=horizon, snapshot=_load_snapshot(),
    )


@app.get("/alerts", dependencies=[Depends(require_read_token)])
def get_alerts(limit: int = Query(50, ge=1, le=500)) -> Dict[str, Any]:
    """Newest-first tail of the structured alert feed (``observability/alerts.py``
    file channel, JSONL at ``settings.ALERT_FILE_PATH``).

    Returns ``{entries, reason}``. Honest empty ``entries`` + a ``reason`` when
    ``ALERT_FILE_PATH`` is unset or the file does not exist yet — never a
    fabricated alert (CONSTRAINT #4). Never 500s (CONSTRAINT #6)."""
    return alerts_feed.alerts_feed(limit=limit)


@app.get("/models", dependencies=[Depends(require_read_token)])
def get_models() -> List[Dict[str, Any]]:
    """The ML model registry (``ml/registry.yaml``): per-model role, trained
    date, CPCV-DSR, PBO, and deployable flag — a transparency surface for the
    models behind the platform.

    ``cpcv_dsr``/``pbo`` are ``null`` for an un-validated model (CONSTRAINT #4).
    ``[]`` when the registry is missing/unreadable; never 500s (CONSTRAINT #6)."""
    return models.model_registry_rows()


@app.get("/options", dependencies=[Depends(require_read_token)])
def get_options_matrix() -> Dict[str, Any]:
    """The persisted options premium-selling matrix across the universe.

    Reads only ``output/options_matrix.json`` (never imports
    ``technical_options_engine``). Returns ``{as_of, directives, reason}`` — empty
    ``directives`` + an honest ``reason`` when ``OPTIONS_MATRIX_ENABLED`` is off or
    the artifact hasn't been written yet (CONSTRAINT #4). Never 500s."""
    return options.options_matrix(path=_options_matrix_path())


@app.get("/pairs", dependencies=[Depends(require_read_token)])
def get_pairs_radar() -> Dict[str, Any]:
    """The persisted pairs-trading radar (ranked cointegrated pairs + current
    spread state — z-score, half-life, advisory signal label). ADVISORY ONLY.

    Reads only ``output/pairs.json`` (never imports the pairs engine /
    ``statsmodels``). Returns ``{as_of, universe, pairs, reason}`` — empty
    ``pairs`` + an honest ``reason`` when ``PAIRS_SNAPSHOT_ENABLED`` is off or the
    artifact hasn't been written yet (CONSTRAINT #4). Never 500s."""
    return pairs.pairs_radar(path=_pairs_snapshot_path())


@app.get("/commands", dependencies=[Depends(require_read_token)])
def get_commands() -> Dict[str, Any]:
    """The CLI command manifest powering the PWA command bar's autocomplete.

    Reads only the committed ``cli_introspect/command_manifest.json`` artifact
    (produced offline by ``scripts/build_command_manifest.py`` — this endpoint
    NEVER introspects the live argparse parsers, which would import the heavy
    engines the AST guard forbids). Returns ``{generated_at, command_count,
    dead_letters, commands, reason}`` — empty ``commands`` + an honest ``reason``
    when the manifest hasn't been generated yet (CONSTRAINT #4). Never 500s."""
    return commands_reader.command_manifest()


@app.get("/thresholds", dependencies=[Depends(require_read_token)])
def get_thresholds() -> Dict[str, float]:
    """Live deployability-gate and position-sizing thresholds, imported
    directly from ``validation.thresholds`` and ``settings`` — never re-typed
    as literals — so the PWA's "How this works" education panels can quote the
    SAME numbers the strategy validation harness actually enforces, mirroring
    the live-import discipline ``gui/help_content.py`` already applies for the
    Streamlit Command Center (see that module's docstring: "Never hard-code
    numeric thresholds here").

    These are config constants, not persisted pipeline state — always
    available, no cold-start empty case, never 404s/500s."""
    return {
        "pbo_max": PBO_MAX,
        "dsr_min": DSR_MIN,
        "net_sharpe_min": NET_SHARPE_MIN,
        "max_drawdown_max": MAX_DRAWDOWN_MAX,
        "stress_max_drawdown": STRESS_MAX_DRAWDOWN,
        "kelly_fraction": settings.KELLY_FRACTION,
        "kelly_cap": settings.KELLY_CAP,
    }


def _safe_float(value: float) -> Optional[float]:
    """NaN is a legitimate internal signal (unparsable timestamp) but is not
    valid JSON — coerce to ``None`` (CONSTRAINT #4: never fabricate a number,
    but also never emit a token the frontend's JSON parser can't read)."""
    return None if value != value else value  # NaN != NaN


@app.get("/execution-queue", dependencies=[Depends(require_read_token)])
def get_execution_queue() -> Dict[str, Any]:
    """The gated, dry-run Robinhood order queue (``output/execution_queue.json``)
    — READ ONLY. This endpoint never contacts the Robinhood MCP and never
    places an order: per ``execution/queue_builder.py``'s module contract, a
    live Claude Code agent session is the ONLY actor that ever calls the MCP
    ``place_equity_order`` tool, so there is nothing for this API to trigger.

    Returns ``{generated_at, mode, kill_switch_active, max_notional_per_order,
    n_intents, n_placeable, stale, age_seconds, intents, reason}`` — empty
    ``intents`` + an honest ``reason`` when no queue has been written yet
    (CONSTRAINT #4). Never 500s (reuses ``gui.robinhood_execution_panel``'s
    dead-letter-tolerant reader)."""
    snapshot = execution_panel.read_execution_queue()
    if snapshot is None:
        return {
            "generated_at": None,
            "mode": "off",
            "kill_switch_active": False,
            "max_notional_per_order": 0.0,
            "n_intents": 0,
            "n_placeable": 0,
            "stale": False,
            "age_seconds": None,
            "intents": [],
            "reason": (
                "No execution queue yet — ROBINHOOD_EXECUTION_MODE may be 'off', "
                "or the pipeline hasn't run since it was enabled."
            ),
        }
    return {
        "generated_at": snapshot.generated_at or None,
        "mode": snapshot.mode,
        "kill_switch_active": snapshot.kill_switch_active,
        "max_notional_per_order": snapshot.max_notional_per_order,
        "n_intents": snapshot.n_intents,
        "n_placeable": snapshot.n_placeable,
        "stale": execution_panel.is_queue_stale(snapshot),
        "age_seconds": _safe_float(execution_panel.queue_age_seconds(snapshot)),
        "intents": [
            {
                "symbol": i.symbol,
                "action": i.action,
                "side": i.side,
                "qty": i.qty,
                "target_notional": i.target_notional,
                "conviction": i.conviction,
                "gate_allowed": i.gate_allowed,
                "gate_reasons": i.gate_reasons,
                "allow_place": i.allow_place,
                "rationale": i.rationale,
                "client_order_id": i.client_order_id,
            }
            for i in snapshot.intents
        ],
        "reason": None,
    }


def _env_drift() -> Dict[str, Any]:
    """Compare the on-disk ``.env`` SIGNAL_WEIGHTS/DISABLED_SIGNAL_MODULES against
    the values the running process is actually using (``settings``). A ``.env``
    write does NOT reach the live singleton, so after a successful PUT the API +
    daemon keep serving the OLD values until restart — this surfaces that pending
    change (mirrors ``GET /automation/schedule``'s ``drift`` field). Dead-letter:
    any parse failure -> ``detected: False`` (a hand-mangled ``.env`` must never
    500)."""
    keys: List[str] = []
    try:
        for key, live in (
            ("SIGNAL_WEIGHTS", dict(settings.SIGNAL_WEIGHTS or {})),
            ("DISABLED_SIGNAL_MODULES", list(settings.DISABLED_SIGNAL_MODULES or [])),
        ):
            raw = env_io.get_value(key, "")
            if not raw:
                continue
            on_disk = json.loads(raw)
            if key == "DISABLED_SIGNAL_MODULES":
                if sorted(on_disk) != sorted(live):
                    keys.append(key)
            elif on_disk != live:
                keys.append(key)
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("strategy env_drift check failed: %s", exc)
        return {"detected": False, "keys": [], "note": ""}
    return {
        "detected": bool(keys),
        "keys": keys,
        "note": (
            "An .env write is pending — the API and daemon are still running the "
            "previous values. Restart to apply."
            if keys
            else ""
        ),
    }


@app.get("/strategy/matrix", dependencies=[Depends(require_read_token)])
def get_strategy_matrix() -> Dict[str, Any]:
    """The signal-module weight/enablement matrix the Strategy Matrix screen
    renders — assembled from ``settings`` + the persisted
    ``output/state_snapshot.json`` (never imports ``signals`` / any heavy engine;
    see ``pilots/strategy_matrix.py``'s docstring for why).

    Adds three API-layer fields to the pure reader's payload: ``writable`` (tracks
    ``STRATEGY_WRITES_ENABLED``), ``note``, and ``env_drift`` (whether an ``.env``
    write is pending against the running values). Never 500s (CONSTRAINT #6)."""
    payload = strategy_matrix_reader.strategy_matrix(snapshot_path=_snapshot_path())
    writable = bool(settings.STRATEGY_WRITES_ENABLED)
    payload["writable"] = writable
    payload["note"] = (
        "Writes persist to .env and apply on the next daemon/pipeline launch."
        if writable
        else "Writes are disabled (STRATEGY_WRITES_ENABLED=false)."
    )
    payload["env_drift"] = _env_drift()
    return payload


@app.get("/strategy/health", dependencies=[Depends(require_read_token)])
def get_strategy_health() -> List[Dict[str, Any]]:
    """Deployability-gate breakdown for EVERY catalog Pilot — a bird's-eye view
    across the whole marketplace of WHY each Pilot's underlying validated
    strategy is or isn't deployable, not just the pass/fail badge
    ``GET /pilots/{id}/performance`` already surfaces for one Pilot at a time.

    Each entry carries the actual per-gate value vs. required threshold (PBO,
    DSR, net Sharpe, Max Drawdown — thresholds read live from
    ``validation.thresholds``, never re-typed here), the aggregate
    ``stress_gate_passed`` for options-selling Pilots, and a best-effort
    run-over-run ``trend`` from the persisted validation history. A Pilot with
    no validated backtest, or whose summary file is missing/unreadable, reports
    ``deployable=None`` + empty ``gates`` + an honest ``reason`` — never a
    fabricated gate result (CONSTRAINT #4). Never 500s (CONSTRAINT #6)."""
    return strategy_health.strategy_health_rows(
        reports_dir=_reports_dir(),
        history_dir=_validation_history_dir(),
    )


# ---------------------------------------------------------------------------
# Calibration & Recommendation Tracking (read: fail-open; write: fail-closed cmd)
# ---------------------------------------------------------------------------

_VALID_DECISION_ACTIONS = frozenset({"acted", "passed", "modified"})


@app.get("/calibration/summary", dependencies=[Depends(require_read_token)])
def get_calibration_summary(
    horizon: int = Query(30, ge=1, le=365),
) -> Dict[str, Any]:
    """Composite "did our actual calls work?" summary — the PWA's port of the
    retired Streamlit Report Viewer's evaluation-analytics sections (bounded to
    four): the conviction-calibration reliability diagram, the model-vs-operator
    recommendation-tracking report, the per-signal MFE/MAE points, and the
    recent operator-decision journal tail.

    Composes FOUR independently-degrading sections (``pilots.calibration
    .calibration_summary`` — see that module's docstring for the full per-section
    contract); one section's cold-start/failure never blocks the others, and
    each carries its own honest ``reason`` when empty. Deliberately EXCLUDES the
    heavier edge-by-strategy recompute (``GET /calibration/edge-by-strategy``) so
    this summary never blocks on per-trade bar fetches. ``horizon`` selects the
    recommendation-tracking look-forward window. Never raises (CONSTRAINT #6);
    never fabricates a metric (CONSTRAINT #4)."""
    return calibration.calibration_summary(horizon_days=horizon, snapshot=_load_snapshot())


@app.get("/calibration/edge-by-strategy", dependencies=[Depends(require_read_token)])
def get_edge_by_strategy() -> Dict[str, Any]:
    """MFE/MAE/Edge-Ratio recomputed per CLOSED trade and grouped by the
    ``strategy`` tag recorded at entry (``pilots.calibration.edge_by_strategy_view``).

    The heavier recompute — it fetches OHLC bars per traded symbol via
    ``HistoricalStore.get_bars`` — so it lives behind its OWN endpoint (the PWA
    lazy-loads it) rather than blocking ``GET /calibration/summary``. Honest
    empty ``rows`` + ``reason`` on cold start (no closed trades / none with
    recoverable history). Never 500s (CONSTRAINT #6); NaN aggregates → ``null``
    (CONSTRAINT #4)."""
    return calibration.edge_by_strategy_view()


@app.get("/decisions", dependencies=[Depends(require_read_token)])
def get_decisions(
    limit: int = Query(50, ge=1, le=500),
    symbol: Optional[str] = Query(None),
) -> List[Dict[str, Any]]:
    """Decision Journal history, most-recent-first, optionally filtered to one
    symbol. A COLLECTION view — an empty or not-yet-created log degrades to
    ``[]``, never a 404 (CONSTRAINT #6). Distinct from ``GET /calibration/summary``'s
    bundled ``recent_decisions`` (a fixed-size portfolio-wide preview): this is
    the standalone, paginated, symbol-filterable read a symbol detail page
    needs. ``gui.decision_log.read_decisions`` already tolerates a missing
    file / corrupt lines internally; the ``try/except`` here is a second
    dead-letter layer for an unexpected read failure (e.g. a permissions
    error)."""
    from gui.decision_log import read_decisions

    try:
        entries = read_decisions(_decision_log_path())
    except Exception as exc:  # noqa: BLE001 - dead-letter: unreadable log -> empty
        logger.warning("pilots_api: read_decisions failed: %s", exc)
        return []

    if symbol:
        sym_upper = symbol.strip().upper()
        entries = [e for e in entries if e.symbol.upper() == sym_upper]

    entries.sort(key=lambda e: e.timestamp, reverse=True)
    return [
        {
            "symbol": e.symbol,
            "action_taken": e.action_taken,
            "signal_action": e.signal_action,
            "conviction": e.conviction,
            "notes": e.notes,
            "timestamp": e.timestamp,
            "signal_ts": e.signal_ts,
            "trade_id": e.trade_id,
        }
        for e in entries[:limit]
    ]


@app.post("/decisions", dependencies=[Depends(require_command_token)])
def create_decision(body: DecisionCreateRequest) -> Dict[str, Any]:
    """Append one operator decision to the journal (``output/decision_log.jsonl``).

    Fail-closed ``require_command_token`` ALONE — deliberately NO dedicated
    master-switch flag: appending a local operator note carries no order/money/
    config risk, so it matches ``POST /automation/pause``'s risk tier, not the
    ``require_*_writes_enabled`` tier reserved for materially riskier writes
    (see the pilots-endpoint auth taxonomy).

    ``action_taken`` MUST be one of ``{acted, passed, modified}`` (422 with a
    stable ``invalid_action`` tag otherwise). For an ``"acted"`` decision, the
    entry is best-effort linked to the nearest ``TransactionsStore`` trade within
    24h (READ-ONLY store) — ``trade_id`` is ``null`` when no match exists (never
    fabricated — CONSTRAINT #4). Returns the created entry incl. the resolved
    ``trade_id`` + a ``trade_linked`` convenience flag."""
    action = body.action_taken.strip().lower()
    if action not in _VALID_DECISION_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_action",
                "allowed": sorted(_VALID_DECISION_ACTIONS),
            },
        )

    from gui.decision_log import log_decision
    from transactions_store import TransactionsStore

    # READ-ONLY store — used only to link an "acted" decision to an existing
    # trade via join_to_store; never written to here (CONSTRAINT #4).
    store: Any = None
    try:
        store = TransactionsStore(readonly=True)
    except Exception as exc:  # noqa: BLE001 — dead-letter: no store -> no trade link
        logger.warning("create_decision: TransactionsStore unavailable: %s", exc)
        store = None

    entry = log_decision(
        symbol=body.symbol,
        action_taken=action,  # type: ignore[arg-type]  — validated above
        signal_action=body.signal_action,
        conviction=body.conviction,
        notes=body.notes.strip(),
        signal_ts=body.signal_ts,
        transactions_store=store,
        log_path=_decision_log_path(),
    )

    return {
        "symbol": entry.symbol,
        "action_taken": entry.action_taken,
        "signal_action": entry.signal_action,
        "conviction": entry.conviction,
        "notes": entry.notes,
        "timestamp": entry.timestamp,
        "signal_ts": entry.signal_ts,
        "trade_id": entry.trade_id,
        "trade_linked": entry.trade_id is not None,
    }


# ---------------------------------------------------------------------------
# Follow endpoints (fail-closed command token)
# ---------------------------------------------------------------------------


@app.get("/follows", dependencies=[Depends(require_command_token)])
def list_follows() -> List[Dict[str, Any]]:
    """Return the active follows. Guarded by the fail-closed command token
    (follow-state is more sensitive than public read data)."""
    return FollowsStore().list_active()


@app.put("/follows", dependencies=[Depends(require_command_token)])
def upsert_follow(body: FollowUpsertRequest) -> Dict[str, Any]:
    """Create/update a follow. ``amount == 0`` cancels it. 404 on unknown
    Pilot. Returns the updated follow row."""
    pilot = catalog.get_pilot(body.pilot_id)
    if pilot is None:
        raise HTTPException(status_code=404, detail=_UNKNOWN_PILOT_DETAIL)
    follow = FollowsStore().upsert(body.pilot_id, body.amount)
    return {"follow": follow}


@app.post("/pilots/{pilot_id}/follow", dependencies=[Depends(require_command_token)])
def follow_pilot(pilot_id: str, body: FollowRequest) -> Any:
    """Follow a Pilot with a dollar amount: persist the follow, then build the
    gated, paper-first dry-run order queue via ``pilots.mirror.plan_follow``.

    Order (auth is already checked by the dependency): 404 unknown Pilot →
    423 if the kill switch is active → persist the follow → plan the gated
    queue. Idempotent. When no account snapshot is available the follow is still
    persisted and a preview-only result (empty ``planned_intents`` + an honest
    ``note``) is returned rather than a fabricated equity figure (CONSTRAINT #4).
    """
    pilot = catalog.get_pilot(pilot_id)
    if pilot is None:
        raise HTTPException(status_code=404, detail=_UNKNOWN_PILOT_DETAIL)

    ks = GlobalKillSwitch()
    if ks.is_active():
        raise HTTPException(
            status_code=423,
            detail={
                "detail": "Kill switch active — following is paused.",
                "kill_switch_reason": ks.reason() or "",
            },
        )

    follow = FollowsStore().upsert(pilot_id, body.amount)

    snapshot = _load_snapshot()
    account_snapshot = None
    try:
        account_snapshot = HistoricalStore(readonly=True).latest_account_snapshot()
    except Exception as exc:  # noqa: BLE001 - dead-letter: no account -> preview only
        logger.warning("pilots_api: follow could not load account snapshot: %s", exc)

    plan = plan_follow(pilot, body.amount, account_snapshot, snapshot=snapshot)

    # Always render a human-readable gating notice — the PWA Follow modal renders
    # `notice` unconditionally, so an empty/missing value shows a blank banner.
    notice = (
        "This creates a gated, paper-first order queue that you must confirm. "
        "No order is placed automatically."
    )
    note = None
    if account_snapshot is None:
        note = (
            "No account snapshot available — follow persisted, but a "
            "proportional order preview requires a stored account snapshot "
            "(run the pipeline). No equity was fabricated."
        )
        # Merge the honesty message into the always-rendered notice so it isn't
        # dropped by clients that only read `notice`.
        notice = f"{notice} {note}"

    response: Dict[str, Any] = {
        "follow": follow,
        "planned_intents": plan.get("planned_intents", []),
        "mode": plan.get("mode"),
        "queue_written": plan.get("queue_written", False),
        # Fields the FollowResult UI contract (webapp/src/api/types.ts) requires.
        # notional_cap is the live per-order ceiling (0.0 = unset — the UI renders
        # "not configured" rather than "$0.00"); min_amount is the PWA's dollar floor.
        "notional_cap": float(settings.ROBINHOOD_MAX_NOTIONAL_PER_ORDER),
        "min_amount": float(settings.FOLLOW_MIN_AMOUNT),
        "notice": notice,
    }
    if note is not None:
        # Retained for back-compat with any client reading `note` directly.
        response["note"] = note
    return response


# ---------------------------------------------------------------------------
# Agentic Trading tab — composite status + scan-based discovery (read + gated write)
# ---------------------------------------------------------------------------


@app.get("/agentic/status", dependencies=[Depends(require_read_token)])
def get_agentic_status() -> Dict[str, Any]:
    """Composite "what is the agent doing" answer for the Agentic Trading tab.

    Composes FOUR already-imported, dependency-light sources exactly like
    ``GET /automation/status`` does (no monolithic ``pilots/*.py`` helper
    needed — each piece already has one): ``gui.robinhood_execution_panel``
    for the gated execution queue, ``pilots.follows_store.FollowsStore`` for
    active Pilot follows, ``execution.kill_switch.GlobalKillSwitch`` for the
    kill switch, and ``pilots.agentic.agent_loop_status`` (the one piece with
    no existing reader) for the advisory-loop agent's persisted cadence state.

    Never raises, never 500s (CONSTRAINT #6) — every sub-read already degrades
    to an honest empty/``None`` shape on its own failure."""
    queue = execution_panel.read_execution_queue()
    if queue is None:
        queue_summary: Dict[str, Any] = {
            "mode": "off",
            "generated_at": None,
            "n_intents": 0,
            "n_placeable": 0,
            "stale": False,
            "age_seconds": None,
        }
    else:
        queue_summary = {
            "mode": queue.mode,
            "generated_at": queue.generated_at or None,
            "n_intents": queue.n_intents,
            "n_placeable": queue.n_placeable,
            "stale": execution_panel.is_queue_stale(queue),
            "age_seconds": _safe_float(execution_panel.queue_age_seconds(queue)),
        }

    ks = GlobalKillSwitch()
    ks_active = ks.is_active()
    active_follows = FollowsStore().list_active()

    return {
        "mode": queue_summary["mode"],
        "advisory_only": settings.ADVISORY_ONLY,
        "kill_switch": {
            "active": ks_active,
            "reason": ks.reason() if ks_active else None,
        },
        "queue": queue_summary,
        "follows": {
            "n_active": len(active_follows),
            "total_amount": float(sum(f.get("amount", 0.0) for f in active_follows)),
        },
        "agent_loop": agentic.agent_loop_status(),
    }


@app.get("/agentic/discovery", dependencies=[Depends(require_read_token)])
def get_agentic_discovery() -> Dict[str, Any]:
    """Scan-discovered candidates for the Agentic Trading tab's Discovery
    section — READ ONLY. Populated by the ``agentic-discovery`` Claude Code
    skill; this API never contacts the Robinhood MCP itself (mirrors ``GET
    /execution-queue``'s module contract — see ``pilots.discovery``'s module
    docstring). Empty ``candidates`` + an honest ``reason`` when no scan has
    run yet (CONSTRAINT #4). Never 500s.

    Adds ``writable`` (tracks ``AGENTIC_DISCOVERY_ENABLED``) on top of the pure
    reader's payload — same pattern as ``GET /strategy/matrix`` — so the PWA
    knows whether to render the scan-config write form before the operator
    hits a 403 on ``PUT /agentic/scan-config``."""
    payload = discovery_reader.discovery()
    writable = bool(settings.AGENTIC_DISCOVERY_ENABLED)
    payload["writable"] = writable
    payload["note"] = (
        "Scan configs are saved immediately and take effect on the agentic-discovery "
        "skill's next run."
        if writable
        else "Scan-config writes are disabled (AGENTIC_DISCOVERY_ENABLED=false)."
    )
    return payload


@app.put(
    "/agentic/scan-config",
    dependencies=[
        Depends(require_command_token),
        Depends(require_agentic_discovery_enabled),
    ],
)
def put_agentic_scan_config(body: ScanConfigRequest) -> Dict[str, Any]:
    """Create/replace one named Robinhood broker-scan config
    (``output/scan_configs.json`` via ``pilots.scan_config_store.ScanConfigStore``
    — NOT an ``.env`` write, see ``ScanConfigRequest``'s docstring for why).

    Unlike the ``.env``-backed write endpoints, this takes effect the NEXT TIME
    the ``agentic-discovery`` skill runs a scan (there is no daemon restart
    involved), so ``applies`` is ``"next_discovery_run"``, not
    ``"next_daemon_restart"``. Echoes the STORE'S RETURNED ROW (which already
    reflects exactly what was written, including timestamps) rather than the
    raw request body."""
    row = ScanConfigStore().upsert(body.name, body.filters, enabled=body.enabled)
    return {
        "scan_config": row,
        "applies": "next_discovery_run",
        "note": (
            "Saved to output/scan_configs.json. Takes effect the next time the "
            "agentic-discovery skill runs a scan — it is not applied automatically."
        ),
    }


# ---------------------------------------------------------------------------
# LLM configuration status + writes (AI Control Center — see module docstring)
# ---------------------------------------------------------------------------


@app.get("/llm/status", dependencies=[Depends(require_read_token)])
def get_llm_status() -> Dict[str, Any]:
    """LLM provider configuration + last-real-call telemetry.

    Read-only — deliberately NOT gated by ``LLM_COMMENTARY_ENABLED`` /
    ``OPAL_RESEARCH_ENABLED`` / ``GRAVITY_AI_RUNNER_ENABLED`` (mirrors
    ``GET /brokerage/status``'s posture exactly: a status endpoint REPORTS
    configuration, it does not enforce it — and the whole point is to be
    readable precisely WHEN a feature is off and the operator is working out
    why the narratives are null).

    NEVER probes a provider. Every verdict here was recorded from a REAL call
    the platform already made (``llm/status_store.py``, written from
    ``llm/providers.py``'s own except blocks) — this endpoint makes ZERO
    network calls and constructs ZERO providers (constructing one is what fires
    an SDK import; settings are read directly, never via
    ``llm.router.get_*_provider()``).

    Never returns a key, a key prefix, or a key fingerprint. The fingerprint is
    module-private to ``llm/status_store.py`` and is stripped before any value
    crosses that boundary (CONSTRAINT #3).

    Sources are NAMED per-field (mirrors ``GET /automation/status``):
    ``capabilities_source``, ``providers_source``, and each provider record's
    own ``source``. A null telemetry record is the EXPECTED state, not a
    failure — see ``telemetry_note``. No ``try/except``: both sub-reads are
    non-raising by their own contracts (CONSTRAINT #6), a property pinned by
    test rather than papered over here.

    ``writable``/``writable_note`` track whether ``PUT /llm/setting`` would
    actually succeed right now (``settings.LLM_WRITES_ENABLED`` — the same
    fail-closed master switch that endpoint requires), mirroring
    ``GET /automation/schedule``'s ``interval.writable`` and
    ``GET /strategy/matrix``'s ``writable`` — so the PWA can show a read-only
    notice up front instead of letting the operator hit a 403.
    """
    last_calls = llm_status_store.read_all()
    rows = ai_control_center.control_center_overview(settings, last_calls=last_calls)
    # attention = at least one ENABLED capability is misconfigured. invalid_key
    # (a rejected key) outranks missing_key (an unset key) as the reason.
    attention_reason: Optional[str] = None
    for row in rows:
        if not row.get("enabled"):
            continue
        if row.get("status") == "invalid_key":
            attention_reason = "invalid_key"
            break
        if row.get("status") == "missing_key" and attention_reason is None:
            attention_reason = "missing_key"
    writable = bool(settings.LLM_WRITES_ENABLED)
    return {
        "capabilities": rows,
        "capabilities_source": "gui.ai_control_center.control_center_overview",
        "providers": last_calls,
        "providers_source": "llm.status_store.read_all",
        "telemetry_note": llm_status_store.LLM_STATUS_ADVISORY_NOTE,
        "attention": attention_reason is not None,
        "attention_reason": attention_reason,
        "writable": writable,
        "writable_note": (
            "Toggle and provider writes persist to .env and apply on the next "
            "daemon restart."
            if writable
            else "AI-capability writes are disabled (LLM_WRITES_ENABLED=false)."
        ),
    }


@app.put(
    "/llm/setting",
    dependencies=[
        Depends(require_command_token),
        Depends(require_llm_writes_enabled),
    ],
)
def set_llm_setting(body: LlmSettingUpdateRequest) -> Dict[str, Any]:
    """Write ONE AI-capability toggle or provider-selector key to ``.env``.

    ``key`` must be a capability's ``toggle_key`` (bool value) or
    ``provider_selector_setting`` (str value) from ``GET /llm/status``'s
    ``capabilities`` rows — validated via
    ``ai_control_center.validate_toggle_write`` (CONSTRAINT #3: a secret key
    is refused with 403, as is any key outside ``gui.env_io.ALLOWED_KEYS``)
    before ``env_io.write_setting`` performs the actual (re-validated) write.

    Like ``PUT /strategy/modules`` this is an ``.env``-ONLY write: it does NOT
    patch the running ``settings`` singleton (a process-lifetime object), so
    this API and any already-launched pipeline keep using the previous value
    until restart. ``applies`` is therefore always ``"next_daemon_restart"``,
    and the echoed ``value`` reflects the REQUEST BODY, not ``settings``
    (which would return the stale value and read as a failed write).
    """
    try:
        ai_control_center.validate_toggle_write(body.key)
    except (env_io.SecretWriteError, env_io.DisallowedKeyError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    env_io.write_setting(body.key, body.value)
    return {
        "written": [body.key],
        "value": body.value,
        "applies": "next_daemon_restart",
        "note": (
            "Written to .env. settings is not patched in-process — this API "
            "and any already-launched pipeline still use the previous value "
            "until restarted."
        ),
    }


# ---------------------------------------------------------------------------
# Brokerage-connect endpoints (credential intake — see module docstring)
# ---------------------------------------------------------------------------


@app.get("/brokerage/status", dependencies=[Depends(require_read_token)])
def get_brokerage_status() -> Dict[str, Any]:
    """Whether Robinhood portfolio-snapshot credentials are configured and
    whether an account snapshot has ever been stored. Read-only — NOT gated by
    ``BROKERAGE_CONNECT_ENABLED`` (status is safe to read even when connect
    intake is disabled; the operator may have set credentials by hand in
    ``.env``, the normal path). Never returns credential values."""
    connected = brokerage_credentials.rh_credentials_present()
    has_account_snapshot = False
    try:
        has_account_snapshot = HistoricalStore(readonly=True).latest_account_snapshot() is not None
    except Exception as exc:  # noqa: BLE001 - dead-letter: cold DB -> honest False
        logger.warning("pilots_api: brokerage status account-snapshot check failed: %s", exc)
    return {"connected": connected, "has_account_snapshot": has_account_snapshot}


@app.post(
    "/brokerage/connect",
    dependencies=[
        Depends(require_brokerage_connect_enabled),
        Depends(require_command_token),
        Depends(require_loopback),
    ],
)
def connect_brokerage(body: BrokerageConnectRequest) -> Dict[str, Any]:
    """Verify Robinhood credentials with a read-only login, then persist them
    to the local ``.env`` (and the live process environment) ONLY on success.

    Gated by three independent controls (see the dependencies above):
    ``BROKERAGE_CONNECT_ENABLED``, the fail-closed follow command token, and a
    loopback-only request check. Credential values are never logged, cached,
    or echoed back in the response (CONSTRAINT #3) — on failure this returns a
    plain 401 with no detail about which field was wrong (username vs.
    password vs. MFA), since that distinction itself would leak information
    about a candidate credential.

    ``body.mfa_code`` (a one-time 6-digit authenticator code) is used only to
    verify the login and is never persisted — only username/password are
    written to ``.env`` on success. Ongoing unattended re-fetches rely on
    ``robin_stocks``' own device-session pickle established by this verify
    call, not a stored MFA secret."""
    verified = robinhood_portfolio.verify_credentials(
        body.username, body.password, body.mfa_code
    )
    if not verified:
        raise HTTPException(
            status_code=401,
            detail="Could not verify Robinhood credentials.",
        )
    brokerage_credentials.write_rh_credentials(body.username, body.password)
    account_present = False
    try:
        account_present = HistoricalStore(readonly=True).latest_account_snapshot() is not None
    except Exception as exc:  # noqa: BLE001 - dead-letter: cold DB -> honest False
        logger.warning("pilots_api: connect account-snapshot check failed: %s", exc)
    return {"connected": True, "verified": True, "has_account_snapshot": account_present}


@app.post(
    "/brokerage/disconnect",
    dependencies=[
        Depends(require_brokerage_connect_enabled),
        Depends(require_command_token),
        Depends(require_loopback),
    ],
)
def disconnect_brokerage() -> Dict[str, Any]:
    """Log out of the active Robinhood session (best-effort) and clear
    RH_USERNAME/RH_PASSWORD from ``.env`` and the process environment
    (RH_MFA_SECRET, if the operator has set one for the main pipeline, is
    never touched by this webapp-facing flow). Idempotent — safe to call when
    nothing is connected."""
    try:
        robinhood_portfolio.logout()
    except Exception as exc:  # noqa: BLE001 - logout failure must not block disconnect
        logger.warning("pilots_api: brokerage logout failed (ignored): %s", exc)
    brokerage_credentials.clear_rh_credentials()
    return {"connected": False}


# ---------------------------------------------------------------------------
# Data & Automation — read-only pipeline run status + schedule (Phase 2 of the
# Data & Automation settings dashboard; the webapp/ /settings screen's backend).
# Both endpoints are read-only GETs guarded by the fail-open require_read_token,
# same posture as every other read endpoint in this module. Manual "Run Now"
# and schedule/pause writes are a later phase — this phase exists to get
# "did the pipeline run?" off the operator's SSH/journalctl critical path.
# ---------------------------------------------------------------------------


def _serialize_progress(state: Any) -> Optional[Dict[str, Any]]:
    """JSON-safe dict from a ``reporting.progress.ProgressState``, or ``None``.

    Adds ``age_seconds``/``stale`` on top of the raw fields: a ``"running"``
    progress.json that hasn't been touched in 15+ minutes is a DEAD run, not a
    live one (the daemon/process that owned it crashed without cleaning up) —
    the PWA needs that distinction to avoid rendering a permanently-spinning
    progress bar."""
    if state is None:
        return None
    age = state.age_seconds()
    return {
        "run_id": state.run_id,
        "state": state.state,
        "stage": state.stage,
        "stage_index": state.stage_index,
        "stage_total": state.stage_total,
        "symbols_done": state.symbols_done,
        "symbols_total": state.symbols_total,
        "percent": state.percent,
        "message": state.message,
        "started_at": state.started_at.isoformat(),
        "updated_at": state.updated_at.isoformat(),
        "age_seconds": age,
        "is_terminal": state.is_terminal,
        "stale": (not state.is_terminal) and age > 900,
    }


@app.get("/automation/status", dependencies=[Depends(require_read_token)])
def get_automation_status() -> Dict[str, Any]:
    """Composite "did the pipeline run?" answer for the Settings screen.

    Composes FIVE independent sources and NAMES which one supplied each field
    — the honesty contract this endpoint exists for:

    * ``daemon`` — ``gui.daemon_client.get_status()`` (live, over loopback
      HTTP to the Control API) when reachable (``source: "control_api"``);
      falls back to ``output/daemon.json`` (written once at daemon startup)
      when it isn't (``source: "daemon_json"``, ``alive: false`` — this is the
      RESTART-HONESTY core: the daemon's in-memory run history is gone after
      a restart, but daemon.json still has the last known pid/interval/
      started_at); ``source: "none"`` when neither is available.
    * ``last_run`` — ``gui.daemon_client.get_latest_run()``. ``None`` (with
      ``last_run_source: "state_snapshot"``) when the daemon has never
      triggered a run this process lifetime (a fresh restart with an empty
      in-memory ring) — NOTHING is synthesized in that case; the caller must
      fall back to ``pipeline.snapshot_age_seconds`` for "the pipeline last
      produced output at T" instead of a fabricated run record.
    * ``pipeline`` — ``pilots.run_status``'s file-backed snapshot/heartbeat
      age readers. ``heartbeat_age_seconds`` is ``null`` in advisory mode by
      design (see ``heartbeat_note``) — never render that as "engine down".
    * ``progress`` — live ``reporting.progress.read_progress()``, with
      ``stale`` computed here (a "running" progress file untouched for 15+
      minutes is a dead run, not a live one).
    * ``kill_switch`` / ``errors`` — ``execution.kill_switch.GlobalKillSwitch``
      (already imported at module top) and the bounded, structured
      ``output/dead_letter.json`` tail (capped at 50 entries, true count
      echoed) — deliberately NOT a raw log tail (CLAUDE.md: never fabricate,
      dead-letter don't crash; the actual log files run 100+ MB and may carry
      secrets, both disqualifying for an API response).

    Never raises, never 500s (CONSTRAINT #6) — every sub-read already degrades
    to an honest ``None``/empty shape on its own failure."""
    daemon_status = daemon_client.get_status()
    if daemon_status is not None:
        daemon_info: Dict[str, Any] = {
            "alive": True,
            "source": "control_api",
            "pid": None,  # not echoed by /status; only daemon.json carries it
            "port": settings.ORCHESTRATOR_API_PORT,
            "started_at": daemon_status.get("started_at"),
            "interval_seconds": daemon_status.get("interval_seconds"),
            "is_running": daemon_status.get("is_running"),
            "current_run_id": daemon_status.get("current_run_id"),
            "engines_warm": daemon_status.get("engines_warm"),
        }
    else:
        dj = run_status.read_daemon_json()
        if dj is not None:
            daemon_info = {
                "alive": False,
                "source": "daemon_json",
                "pid": dj.get("pid"),
                "port": dj.get("port"),
                "started_at": dj.get("started_at"),
                "interval_seconds": dj.get("interval_seconds"),
                "is_running": None,
                "current_run_id": None,
                "engines_warm": None,
            }
        else:
            daemon_info = {
                "alive": False,
                "source": "none",
                "pid": None,
                "port": None,
                "started_at": None,
                "interval_seconds": None,
                "is_running": None,
                "current_run_id": None,
                "engines_warm": None,
            }

    last_run = daemon_client.get_latest_run()
    last_run_source = "daemon_memory" if last_run is not None else "state_snapshot"

    snapshot_age, snapshot_source = run_status.snapshot_age_seconds()
    heartbeat_age = run_status.heartbeat_age_seconds()

    ks = GlobalKillSwitch()
    ks_active = ks.is_active()

    return {
        "daemon": daemon_info,
        "last_run": last_run,
        "last_run_source": last_run_source,
        "pipeline": {
            "snapshot_age_seconds": snapshot_age,
            "snapshot_age_source": snapshot_source,
            "heartbeat_age_seconds": heartbeat_age,
            "heartbeat_note": run_status.HEARTBEAT_ADVISORY_NOTE,
        },
        "progress": _serialize_progress(read_progress()),
        "kill_switch": {
            "active": ks_active,
            "reason": ks.reason() if ks_active else None,
        },
        "errors": run_status.read_dead_letter(),
        "advisory_only": settings.ADVISORY_ONLY,
        "dry_run": settings.DRY_RUN,
        "alpaca_paper": settings.ALPACA_PAPER,
    }


@app.get("/automation/schedule", dependencies=[Depends(require_read_token)])
def get_automation_schedule() -> Dict[str, Any]:
    """Interval drift display + the read-only cron schedule.

    ``interval.running_value`` is what the LIVE daemon (or its last-known
    ``daemon.json`` startup record) is actually running on; ``configured_value``
    is what ``.env``/``settings.ORCHESTRATOR_INTERVAL_SECONDS`` currently says.
    They can legitimately disagree (a `.env` edit doesn't reach a live daemon
    until it restarts) — ``drift`` flags that explicitly rather than letting
    the operator assume a `.env` edit already took effect.

    ``cron`` is parsed from the checked-in ``deploy/crontab.txt`` — NEVER via
    ``crontab -l`` (a subprocess call from this API is exactly the RCE-adjacent
    surface cron/systemd *writing* was excluded for elsewhere in this feature;
    the read side gets the same posture). ``installed`` is honestly ``null``:
    this endpoint cannot confirm what's actually installed on the host, only
    what the repo says is intended.

    ``interval.writable`` reflects whether ``PUT /automation/schedule/interval``
    would actually succeed right now (``settings.AUTOMATION_WRITES_ENABLED`` —
    the same fail-closed master switch that endpoint requires), so the PWA can
    disable its own Save button instead of letting the operator hit a 403."""
    daemon_status = daemon_client.get_status()
    if daemon_status is not None:
        running_value = daemon_status.get("interval_seconds")
    else:
        dj = run_status.read_daemon_json()
        running_value = dj.get("interval_seconds") if dj else None

    configured_value = settings.ORCHESTRATOR_INTERVAL_SECONDS
    drift = running_value is not None and running_value != configured_value
    writable = bool(settings.AUTOMATION_WRITES_ENABLED)

    return {
        "interval": {
            "running_value": running_value,
            "configured_value": configured_value,
            "drift": drift,
            "writable": writable,
            "note": (
                "Writes persist to .env and apply on the daemon's next restart."
                if writable
                else "Writes are disabled (AUTOMATION_WRITES_ENABLED=false)."
            ),
        },
        "cron": {
            "source": "deploy/crontab.txt",
            "installed": None,
            "note": (
                "Parsed from the repo file — the intended schedule. This API "
                "never runs `crontab -l`, so it cannot confirm what is "
                "actually installed on the host; it may differ."
            ),
            "entries": run_status.parse_crontab(),
        },
    }


# ---------------------------------------------------------------------------
# Data & Automation — WRITE endpoints (Phase 3). Auth posture, per endpoint:
#
#   POST /automation/run     -> require_command_token alone (matches
#                                POST /pilots/{id}/follow's existing posture:
#                                an order-queue write under FOLLOW_API_TOKEN
#                                alone, no master flag — gating a run trigger
#                                MORE strictly would invert the risk ordering)
#   POST /automation/pause   -> require_command_token alone (same reasoning;
#                                pausing is the SAFE direction)
#   POST /automation/resume  -> + require_automation_writes_enabled, AND
#                                fails 403 when settings.ADVISORY_ONLY is False
#                                (re-enabling LIVE order submission remotely)
#   PUT  /automation/schedule/interval -> + require_automation_writes_enabled
#                                (persists to .env)
#   PUT  /automation/execution-mode    -> + require_automation_writes_enabled
#                                (same risk tier as resume -- can flip
#                                ADVISORY_ONLY/ALPACA_PAPER toward live)
# ---------------------------------------------------------------------------


_TRIGGER_ERROR_STATUS: Dict[str, int] = {
    "already_running": 409,
    "kill_switch_active": 423,
    "command_disabled": 503,
    "unauthorized": 503,  # deliberately same as command_disabled -- never
    "unavailable": 503,   # leak which side's token/config is wrong
    "network_error": 503,
    "unexpected_response": 503,
}


@app.post("/automation/run", dependencies=[Depends(require_command_token)])
def trigger_automation_run() -> JSONResponse:
    """Trigger an immediate pipeline cycle. Pure proxy over
    ``gui.daemon_client.trigger_run()`` — no new orchestration logic here, all
    single-flight/kill-switch/auth enforcement already lives in
    ``desktop/daemon_runtime.py`` and ``api/control_api.py``.

    Status mapping (from ``TriggerResponse.error``, see ``gui/daemon_client.py``):
    202 (ok) / 409 already_running / 423 kill_switch_active / 503 for
    command_disabled, unauthorized, unavailable, network_error, and
    unexpected_response — ``unauthorized`` and ``command_disabled`` return the
    IDENTICAL generic message so a caller can never learn which side's token
    is misconfigured (this API's ``FOLLOW_API_TOKEN`` vs. the daemon's own
    ``ORCHESTRATOR_DAEMON_TOKEN``).

    Requires the operator to have set BOTH ``FOLLOW_API_TOKEN`` (browser to
    this API) and ``ORCHESTRATOR_DAEMON_TOKEN`` (this API to the Control API,
    read live by ``gui.daemon_client._auth_headers()``) — same host, same
    ``.env``."""
    result = daemon_client.trigger_run()
    if result.ok:
        return JSONResponse(
            status_code=202, content={"run_id": result.run_id, "state": result.state}
        )

    status_code = _TRIGGER_ERROR_STATUS.get(result.error or "", 503)
    if result.error == "already_running":
        detail: Any = {"detail": "A run is already in flight.", "run_id": result.existing_run_id}
    elif result.error == "kill_switch_active":
        detail = {
            "detail": "Kill switch active — pipeline triggering is paused.",
            "kill_switch_reason": result.kill_switch_reason,
        }
    elif result.error in ("command_disabled", "unauthorized"):
        detail = "Orchestrator daemon command channel is not available."
    else:
        detail = "Orchestrator daemon is not reachable."
    return JSONResponse(status_code=status_code, content={"detail": detail})


@app.post("/automation/pause", dependencies=[Depends(require_command_token)])
def pause_automation(body: PauseRequest) -> Dict[str, Any]:
    """Activate the global kill switch (``execution.kill_switch.GlobalKillSwitch``
    — already imported at module top). Idempotent (the class's own contract).

    This is the DOCUMENTED existing pause mechanism (``docs/RUNBOOK.md`` §6),
    not a new one: in advisory mode the sentinel gates SIGNAL GENERATION (no
    broker to halt); in live mode the same sentinel gates ORDER SUBMISSION.
    Pausing is the safe direction in either mode, so it needs no extra gate
    beyond the command token.

    IMPORTANT caveat the PWA must surface: this does NOT stop the daemon's
    interval timer — cycles still run on schedule, they just produce no
    recommendations (advisory) or submit no orders (live). ``POST
    /automation/run`` returns 423 while paused; the timer keeps ticking."""
    ks = GlobalKillSwitch()
    ks.activate(reason=body.reason)
    return {"active": True, "reason": body.reason}


@app.post(
    "/automation/resume",
    dependencies=[
        Depends(require_command_token),
        Depends(require_automation_writes_enabled),
    ],
)
def resume_automation(body: ResumeRequest) -> Dict[str, Any]:
    """Deactivate the global kill switch.

    FAILS 403 when ``settings.ADVISORY_ONLY is False`` — remote resume is
    allowed exactly while the broker surface is quarantined (resuming just
    resumes recommendations); once live order submission is enabled the same
    sentinel is the last line of defense against a compromised/leaked token
    re-enabling it remotely, so resume must be done at the console in that
    mode. This maps the gate to the actual risk rather than treating pause and
    resume symmetrically."""
    if not settings.ADVISORY_ONLY:
        raise HTTPException(
            status_code=403,
            detail=(
                "Resume is disabled while ADVISORY_ONLY=false (live order "
                "submission is enabled) — deactivate the kill switch at the "
                "console, not remotely."
            ),
        )
    ks = GlobalKillSwitch()
    ks.deactivate()
    return {"active": False, "reason": None}


@app.put(
    "/automation/schedule/interval",
    dependencies=[
        Depends(require_command_token),
        Depends(require_automation_writes_enabled),
    ],
)
def set_automation_interval(body: IntervalUpdateRequest) -> Dict[str, Any]:
    """Write ``ORCHESTRATOR_INTERVAL_SECONDS`` to ``.env`` via the SAME
    allowlist-bounded writer (``gui.env_io.write_setting``) the GUI Settings
    tab uses — not a bespoke file write, so it inherits CONSTRAINT #3's
    enforcement for free. THEN attempts a LIVE apply against a running
    daemon over loopback HTTP (``gui.daemon_client.set_interval`` ->
    ``api/control_api.py``'s ``PUT /interval`` ->
    ``desktop.daemon_runtime.OrchestratorDaemon.set_interval``).

    The ``.env`` write happens FIRST and UNCONDITIONALLY — it is the durable
    record of operator intent and must land even when no daemon is
    reachable (daemon mode off, daemon down, wrong
    ``ORCHESTRATOR_DAEMON_TOKEN``, network error). ``applies`` is
    ``"immediately"`` ONLY when the live apply actually confirms success
    (``live.ok``) — it is NEVER inferred from the ``.env`` write succeeding,
    which says nothing about whether a daemon is even running. Any
    live-apply failure degrades to ``"next_daemon_restart"``, the exact
    honest fallback this endpoint always returned before a live setter
    existed. Pair with ``GET /automation/schedule``'s ``drift`` field so the
    operator SEES a pending live-apply failure rather than assuming the
    change already took effect."""
    encoded = env_io.write_setting("ORCHESTRATOR_INTERVAL_SECONDS", body.interval_seconds)

    live = daemon_client.set_interval(body.interval_seconds)
    applies = "immediately" if live.ok else "next_daemon_restart"

    return {
        "configured_value": body.interval_seconds,
        "written": encoded,
        "applies": applies,
    }


def _validate_strategy_modules(body: StrategyModulesUpdateRequest) -> None:
    """Validate a strategy-modules write, raising ``HTTPException(422)`` with a
    STABLE tag (the frontend branches on the tag, never on the message). Enforces:
    every weight key is a known module (union of configured SIGNAL_WEIGHTS +
    last-run score_components), weights cover EVERY known module (an omitted key
    would be silently zeroed on write), each weight is finite and in
    [0, max_weight], the pinned ``regime_multiplier`` stays 0.0, and every
    disabled entry is a known module."""
    matrix = strategy_matrix_reader.strategy_matrix(snapshot_path=_snapshot_path())
    known = {m["name"] for m in matrix["modules"]}
    max_weight = float(matrix["max_weight"])

    def _fail(tag: str, message: str, **extra: Any) -> None:
        raise HTTPException(status_code=422, detail={"error": tag, "message": message, **extra})

    for name in list(body.weights) + list(body.disabled):
        if not _MODULE_NAME_RE.match(name):
            _fail("invalid_module_name", f"'{name}' is not a valid module name.")
        if name not in known:
            _fail("unknown_module", f"'{name}' is not a known signal module.")

    missing = sorted(known - set(body.weights))
    if missing:
        _fail(
            "incomplete_weights",
            "weights must cover every known module (an omitted module is silently "
            "zeroed on write).",
            missing=missing,
        )

    for name, value in body.weights.items():
        if not math.isfinite(value) or value < 0.0 or value > max_weight:
            _fail(
                "weight_out_of_bounds",
                f"weight for '{name}' must be a finite number in [0, {max_weight}].",
            )
        if name in strategy_matrix_reader._PINNED_ZERO_WEIGHT_MODULES and value != 0.0:
            _fail(
                "pinned_zero_module",
                f"'{name}' is structurally pinned to weight 0.0 and cannot be changed.",
            )


@app.put(
    "/strategy/modules",
    dependencies=[
        Depends(require_command_token),
        Depends(require_strategy_writes_enabled),
    ],
)
def set_strategy_modules(body: StrategyModulesUpdateRequest) -> Dict[str, Any]:
    """Replace ``SIGNAL_WEIGHTS`` + ``DISABLED_SIGNAL_MODULES`` in ``.env`` (full
    idempotent replacement, hence PUT). Both keys are written ATOMICALLY via
    ``env_io.write_many_atomic`` — they are one logical unit (new weights + a stale
    disabled-set silently changes what the platform recommends), so a half-applied
    write is not acceptable.

    Like ``PUT /automation/schedule/interval`` this is an ``.env``-ONLY write: it
    does NOT patch the running ``settings`` singleton (a process-lifetime object),
    so the API + daemon keep using the previous values until restart. ``applies`` is
    therefore always ``"next_daemon_restart"``, and the echoed ``configured_weights``
    reflect the REQUEST BODY, not ``settings`` (which would return the stale values
    and read as a failed write). Pair with ``GET /strategy/matrix``'s ``env_drift``."""
    _validate_strategy_modules(body)
    disabled = sorted(set(body.disabled))
    env_io.write_many_atomic(
        {
            "SIGNAL_WEIGHTS": dict(body.weights),
            "DISABLED_SIGNAL_MODULES": disabled,
        }
    )
    return {
        "written": ["SIGNAL_WEIGHTS", "DISABLED_SIGNAL_MODULES"],
        "configured_weights": dict(body.weights),
        "disabled": disabled,
        "applies": "next_daemon_restart",
        "note": (
            "Written to .env. settings is not patched in-process — this API, the "
            "running daemon, and any already-launched pipeline still use the "
            "previous values until restarted."
        ),
    }


@app.put(
    "/automation/execution-mode",
    dependencies=[
        Depends(require_command_token),
        Depends(require_automation_writes_enabled),
    ],
)
def update_execution_mode(body: ExecutionModeUpdateRequest) -> Dict[str, Any]:
    """1-Click Go Live / Execution Mode Toggle. Sets ``ADVISORY_ONLY`` and,
    unless ``mode == "advisory"`` (which carries no ``DRY_RUN``/``ALPACA_PAPER``
    pairing of its own), the ``DRY_RUN``/``ALPACA_PAPER`` pair via
    ``gui.strategy_registry.set_active_mode`` (see its docstring for the
    mode -> env-var mapping). ``written`` always reflects exactly which keys
    this call touched -- never a fixed list -- so the response can't claim a
    write that didn't happen (CONSTRAINT #4)."""
    from gui import strategy_registry

    env_io.write_setting("ADVISORY_ONLY", body.advisory_only)
    written = ["ADVISORY_ONLY"]

    if body.mode != "advisory":
        strategy_registry.set_active_mode(body.mode)
        written += ["DRY_RUN", "ALPACA_PAPER"]

    return {
        "written": written,
        "advisory_only": body.advisory_only,
        "mode": body.mode,
        "applies": "next_daemon_restart",
        "note": "Execution mode updated.",
    }
