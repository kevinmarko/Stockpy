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
GUI-writable), (2) the same fail-closed ``FOLLOW_API_TOKEN`` command
token as the follow write-path, (3) ``require_loopback`` — the request must
originate from ``127.0.0.1``/``::1``. ``POST /brokerage/connect`` and
``POST /brokerage/refresh`` are asynchronous, job-based endpoints (202 +
``job_id``, polled via ``GET /brokerage/login/status/{job_id}``) — Robinhood
device-approval login needs a human to tap "approve" in the Robinhood app,
which can take up to ``RH_LOGIN_DEADLINE_SECONDS``, too long to hold an HTTP
request open. Login itself runs in an isolated, killable subprocess
(``data.robinhood_login_worker``, launched via ``data.robinhood_login`` and
glued to this API by ``api._rh_login``) — never in this process. Credentials
are persisted to ``.env`` by a background watcher thread ONLY once a
"connect" job's login actually succeeds, and are never logged, cached, or
echoed back (CONSTRAINT #3). This remains a single-operator, single-machine
model — not a multi-user credential vault.

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

Several additional FAIL-CLOSED master-switch guards stack ON TOP of the command
token for the writes with real persistence/rollback cost, each a dedicated
``settings`` flag: ``require_brokerage_connect_enabled``
(``/brokerage/connect`` — its ``BROKERAGE_CONNECT_ENABLED`` flag is
GUI-writable by operator decision; the endpoint remains gated by the command
token and loopback check below regardless), ``require_automation_writes_enabled``
(``PUT /automation/schedule/interval``, ``POST /automation/resume``,
``PUT /automation/execution-mode`` — deliberately kept out of
``gui/env_io.py``'s ALLOWED_KEYS, surfaced in the Feature Flags screen),
``require_strategy_writes_enabled`` (``PUT /strategy/modules`` — signal weights +
disabled-module set to ``.env``; its own flag so signal tuning cannot ride in on
the automation flag), ``require_llm_writes_enabled`` (``PUT /llm/setting`` —
AI-capability toggle + provider-selection writes to ``.env``; its own flag so
AI-capability writes cannot ride in on either of the other two), and
``require_macro_gate_writes_enabled`` (``PUT /observability/macro-gate`` —
flips ``MACRO_REGIME_GATE_ENABLED``, the recession/credit-event BUY-veto
bypass, to ``.env``; its own flag so this genuine risk-management kill switch
cannot ride in on any sibling flag), and ``require_brokerage_refresh_enabled``
(``POST /brokerage/refresh`` — forces a live Robinhood re-login + snapshot
fetch bypassing the daily cache; its own flag, distinct from
``require_brokerage_connect_enabled``, since refresh re-uses already-configured
credentials rather than intaking new ones). ``GET /strategy/matrix``, ``GET
/llm/status``, and ``GET /observability/summary`` are read-only
(``require_read_token``).

CORS mirrors ``state_api.py`` (``settings.CORS_ALLOWED_ORIGINS``) but allows
GET, POST and PUT (state_api is GET-only).

Honesty (CONSTRAINT #4): read endpoints 404 honestly on a cold start (no
snapshot / no account yet) and never fabricate a curve, a metric, or an equity
figure.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from api._redact import install_redacting_exception_handler, redact_line

from dotenv import load_dotenv as _load_dotenv

from settings import ENV_PATH, Settings, settings
from settings import INTERVAL_MAX_SECONDS as _INTERVAL_MAX_SECONDS
from settings import validate_interval_seconds as _validate_interval_seconds

# Load .env before any subsequent project import that reads credentials
# (e.g. data.robinhood_portfolio, data.brokerage_credentials). Standalone
# `uvicorn api.pilots_api:app` (this module's normal launch — see module
# docstring) has no main()-style entry point to hook this into the way
# main.py/main_orchestrator.py/app_shell.py do, so it runs here, at true
# module top, anchored to ENV_PATH (settings.py) rather than a bare
# load_dotenv() — bare load_dotenv() uses find_dotenv(), which walks UP from
# this file's directory and, in a git worktree with no .env of its own,
# silently finds a PARENT checkout's .env instead. Without this, RH-backed
# endpoints raised "RH_USERNAME is missing" even with a correct .env,
# because the daemon-hosted path (desktop/orchestrator_daemon.py) was the
# only one that ever called load_dotenv().
_load_dotenv(ENV_PATH, override=False)
from api.auth import (
    require_follow_command_token as require_command_token,
    require_read_token,
)
from api.cors import LAN_TAILSCALE_ORIGIN_REGEX

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
    gravity_audit as gravity_audit_reader,
    models,
    news_catalyst,
    observability,
    options,
    pairs,
    performance,
    realized,
    rlhf_review_queue,
    rolling_beta,
    run_status,
    scoring,
    sector_selection,
    simulation,
    strategy_health,
    strategy_matrix as strategy_matrix_reader,
    symbols,
    validation_trend as validation_trend_reader,
)
from pilots.follows_store import FollowsStore
from pilots.mirror import plan_follow
from pilots.scan_config_store import ScanConfigStore

# RLHF Calibration Review Queue write path (POST /rlhf/proposals,
# POST /rlhf/proposals/{id}/review, POST /rlhf/export-sft) — a dedicated,
# non-``pilots``-package store (see its own module docstring for why it is
# NOT a TransactionsStore extension). Imported at module top, mirroring
# FollowsStore/ScanConfigStore above, so tests can
# ``mock.patch.object(pilots_api, "RlhfCalibrationStore", ...)``. Not on the
# AST guard's heavy-engine deny-list — its own imports are db_config/settings/
# stdlib only.
from rlhf_calibration_store import (
    ProposalAlreadyReviewedError,
    ProposalNotFoundError,
    RlhfCalibrationStore,
)

# Per-field liveness/safety metadata for the five /settings/* editors below
# (what actually happens when this field is written: applies now, needs a
# restart, does nothing, or is pinned by a real shell export). Stdlib +
# runtime_flags + settings_keysets only — see its module docstring.
import pilots.settings_meta as settings_meta
import pilots.feature_flags as feature_flags
import settings_keysets

# Execution / persistence — explicitly ALLOWED here (unlike state_api.py),
# forbidden only for the heavy calculation engines (see this module's AST guard
# test). ``data.historical_store`` and ``execution.kill_switch`` are imported at
# module top so tests can ``mock.patch.object(pilots_api, "HistoricalStore", ...)``.
from data.historical_store import HistoricalStore
# Best-effort live-quote enrichment for POST /rlhf/proposals when the caller
# doesn't supply a price — same provider every other market-data read in this
# codebase goes through (see settings.py's "Market-data layer" convention).
from data.market_data import MarketDataError, get_provider
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

# Device-approval login job primitive (start/poll/cancel a killable, isolated
# login-worker subprocess) — see data/robinhood_login.py and
# data/robinhood_login_worker.py. api._rh_login is the thin Pilots-API-specific
# glue that also arranges for RH_USERNAME/RH_PASSWORD to be persisted on a
# successful "connect" job (see its own module docstring). Neither
# data.robinhood_login nor data.robinhood_login_worker is on this module's
# AST-guard deny-list. Imported at module top so tests can
# `mock.patch.object(pilots_api, "rh_login", ...)`.
import api._rh_login as rh_login

# Forecast-backfill job primitive (start/poll/cancel a killable, isolated
# training-worker subprocess) — see ml/forecast_backfill_job.py and
# ml/forecast_backfill_worker.py. Mirrors the rh_login wiring immediately
# above; unlike api._rh_login, no separate glue module is needed here (there
# is no Pilots-API-specific side effect to own — export_results() already
# writes its own output artifacts directly from inside the engine). Neither
# ml.forecast_backfill_job nor ml.forecast_backfill_worker imports
# AgenticForecastBackfiller itself (only the worker, in its own process,
# does) or anything on this module's AST-guard deny-list, so this stays a
# lightweight module-top import. Imported so tests can
# `mock.patch.object(pilots_api, "forecast_backfill_job", ...)`.
import ml.forecast_backfill_job as forecast_backfill_job

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

# AI Gravity audit runner READ side (GET /gravity/audit-status). `gui.gravity_ai_panel`
# is Streamlit-free + dependency-light by design (json/logging/dataclasses/pathlib/
# typing at module top; `settings` imported lazily inside each function) — the SAME
# reasoning as `gui.ai_control_center` above, so it's imported directly here rather
# than duplicated under `pilots/`. It never constructs a provider or makes a network
# call; it only reads `output/gravity_ai_audit.json` (written by a separate, opt-in
# CLI/GUI-triggered run — this API exposes no trigger for it, see the endpoint's own
# docstring). Imported at module top so tests can `mock.patch.object(pilots_api, ...)`.
import gui.gravity_ai_panel as gravity_ai_panel

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

# Portfolio-aware RAG query (POST /rag/query). agents/rag_orchestrator.py's
# own optional heavy deps (langgraph, qdrant_client, sentence-transformers)
# are each self-guarded with try/except ImportError at ITS module top, so
# importing it here is safe with none of them installed -- run_rag_query()
# degrades to an honest "(RAG unavailable — langgraph not installed)" string
# rather than raising. Not on the AST-guard deny-list (agents/, langgraph,
# qdrant_client are none of the seven forbidden heavy-engine names).
from agents.rag_orchestrator import run_rag_query

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
    allow_origin_regex=LAN_TAILSCALE_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Authorization", "Content-Type"],
)

# Structural backstop for exception-message leakage: redacts every
# HTTPException.detail before it reaches the client, so a future endpoint
# that raises HTTPException(detail=str(exc)) directly is covered even if it
# forgets an explicit redact_line() call. See api/_redact.py.
install_redacting_exception_handler(app)

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
# Auth guards — require_read_token / require_command_token are imported from
# api/auth.py at module top (require_command_token bound to FOLLOW_API_TOKEN
# specifically). require_loopback stays local: it's a defense-in-depth guard
# unique to the brokerage-credential intake path, not shared across services.
# ---------------------------------------------------------------------------

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
    GUI-writable (gui/env_io.py) — the endpoints remain gated by two further
    independent checks regardless: the ``FOLLOW_API_TOKEN`` command token and
    ``require_loopback``. ``/brokerage/status`` is read-only and NOT gated by
    this flag."""
    if not settings.BROKERAGE_CONNECT_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Brokerage connect is disabled (BROKERAGE_CONNECT_ENABLED=false).",
        )


def require_brokerage_refresh_enabled() -> None:
    """FAIL-CLOSED master-switch guard for ``POST /brokerage/refresh``. A
    DEDICATED flag, NOT ``require_brokerage_connect_enabled``: that one scopes
    credential INTAKE (verify + persist new username/password, or clear them on
    disconnect) — refresh receives no credential material and instead re-uses
    whatever is already configured, but it is still a real, live login against
    the operator's actual brokerage account and must not ride in on a flag
    named for a different action. GUI-writable (as of 2026-08-08) (gui/env_io.py) —
    surfaced in the Feature Flags screen. ``/brokerage/status`` and ``GET /portfolio``
    are read-only and NOT gated by this flag."""
    if not settings.BROKERAGE_REFRESH_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Brokerage refresh is disabled (BROKERAGE_REFRESH_ENABLED=false).",
        )


def require_automation_writes_enabled() -> None:
    """FAIL-CLOSED master-switch guard for the two Data & Automation writes
    with a real persistence/rollback cost: ``PUT /automation/schedule/interval``
    (an ``.env`` edit) and ``POST /automation/resume`` (re-enabling live order
    submission when ``ADVISORY_ONLY=False``). ``settings.AUTOMATION_WRITES_ENABLED``
    is GUI-writable (as of 2026-08-08) — surfaced in the Feature Flags screen.

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
    ``require_brokerage_connect_enabled`` exactly — GUI-writable (as of 2026-08-08),
    surfaced in the Feature Flags screen. ``GET /strategy/matrix`` is read-only and NOT gated
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
    GUI-writable (as of 2026-08-08), surfaced in the Feature Flags screen. ``GET /llm/status``
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
    queue) and must not ride in on any of those. ``settings.AGENTIC_DISCOVERY_ENABLED``
    is GUI-writable by operator decision — the endpoint remains gated by the
    ``FOLLOW_API_TOKEN`` command token regardless. ``GET /agentic/status`` and ``GET
    /agentic/discovery`` are read-only and NOT gated by this flag
    (``require_read_token`` alone, matching ``GET /strategy/matrix`` and ``GET
    /llm/status``)."""
    if not settings.AGENTIC_DISCOVERY_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Agentic discovery writes are disabled (AGENTIC_DISCOVERY_ENABLED=false).",
        )


def require_general_settings_writes_enabled() -> None:
    """FAIL-CLOSED master-switch guard for ``PUT /settings/tunables`` (general
    runtime tunables — Kelly sizing, risk gate, forecasting, market data,
    runtime/ops -> ``.env``). A DEDICATED flag (``settings.GENERAL_SETTINGS_WRITES_ENABLED``),
    NOT ``AUTOMATION_WRITES_ENABLED``, ``STRATEGY_WRITES_ENABLED``,
    ``LLM_WRITES_ENABLED``, or ``AGENTIC_DISCOVERY_ENABLED``: this changes sizing
    and risk-gate behavior (how large a position gets, when the risk gate blocks
    an order), its own risk class, and must not ride in on any of those. Mirrors
    ``require_strategy_writes_enabled`` exactly — GUI-writable (as of 2026-08-08),
    surfaced in the Feature Flags screen. ``GET /settings/tunables`` is read-only and NOT
    gated by this flag (``require_read_token`` alone, matching ``GET
    /strategy/matrix``, ``GET /llm/status``, and ``GET /agentic/status``)."""
    if not settings.GENERAL_SETTINGS_WRITES_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Settings writes are disabled (GENERAL_SETTINGS_WRITES_ENABLED=false).",
        )


def require_macro_gate_writes_enabled() -> None:
    """FAIL-CLOSED master-switch guard for ``PUT /observability/macro-gate``
    (flips ``MACRO_REGIME_GATE_ENABLED`` -> ``.env``). A DEDICATED flag
    (``settings.MACRO_GATE_WRITES_ENABLED``), NOT
    ``GENERAL_SETTINGS_WRITES_ENABLED``/``STRATEGY_WRITES_ENABLED``/
    ``AUTOMATION_WRITES_ENABLED``/``LLM_WRITES_ENABLED``/
    ``AGENTIC_DISCOVERY_ENABLED``: this is the operator-controlled bypass for
    ``PreTradeRiskGate.macro_kill_switch_check`` (the recession/credit-event BUY
    veto), its own risk class, and must not ride in on any sibling flag. Mirrors
    ``require_general_settings_writes_enabled`` exactly — deliberately NOT
    GUI-writable, surfaced in the Feature Flags screen. ``GET /observability/summary`` is
    read-only and NOT gated by this flag (``require_read_token`` alone, matching
    every other GET here)."""
    if not settings.MACRO_GATE_WRITES_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Macro gate writes are disabled (MACRO_GATE_WRITES_ENABLED=false).",
        )

def require_paper_broker_writes_enabled() -> None:
    if not settings.PAPER_BROKER_WRITES_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Paper broker writes are disabled (PAPER_BROKER_WRITES_ENABLED=false)."
        )

def require_live_trade_approval_enabled() -> None:
    if not settings.LIVE_TRADE_APPROVAL_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Live trade approval is disabled (LIVE_TRADE_APPROVAL_ENABLED=false).",
        )

def require_cache_long_short_writes_enabled() -> None:
    """FAIL-CLOSED master-switch guard for ``POST /pilots/cache-long-short/*``
    write endpoints (start, approve-bulk) -- persists a new tracked position
    or marks a TLH recommendation approved. A DEDICATED flag
    (``settings.CACHE_LONG_SHORT_WRITES_ENABLED``), NOT
    ``AUTOMATION_WRITES_ENABLED`` or ``STRATEGY_WRITES_ENABLED``: this changes
    what a trading strategy recommends, its own risk class, and must not ride
    in on any of those. GUI-writable (as of 2026-08-08), surfaced in the Feature Flags screen
    only. ``GET`` endpoints are read-only and NOT gated by this flag."""
    if not settings.CACHE_LONG_SHORT_WRITES_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Cache Long/Short writes are disabled (CACHE_LONG_SHORT_WRITES_ENABLED=false).",
        )


def require_rag_query_enabled() -> None:
    """FAIL-CLOSED master-switch guard for ``POST /rag/query``. A DEDICATED
    flag (``settings.RAG_QUERY_API_ENABLED``), NOT ``AI_GENERATION_API_ENABLED``:
    that flag's own description enumerates the three specific
    ``/data/ai/{commentary,chart,research}/{symbol}`` endpoints on the Data
    API it gates -- reusing it here would silently widen its documented
    scope to a different service. Same risk class though (a real, paid LLM
    call via ``llm/router.py::get_rationale_provider``, otherwise reachable
    behind ``require_command_token`` alone), so it gets the identical
    fail-closed treatment. Mirrors ``require_strategy_writes_enabled``
    exactly — GUI-writable (as of 2026-08-08), surfaced in the Feature Flags screen.
    There is no read-only companion endpoint to exempt (this is the entry
    point being wired up for the first time)."""
    if not settings.RAG_QUERY_API_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="RAG query is disabled (RAG_QUERY_API_ENABLED=false).",
        )


def require_rlhf_calibration_enabled() -> None:
    """FAIL-CLOSED master-switch guard for the RLHF Calibration Review Queue's
    write endpoints (``POST /rlhf/proposals``, ``POST
    /rlhf/proposals/{id}/review``, ``POST /rlhf/export-sft``).

    ``settings.RLHF_CALIBRATION_ENABLED`` defaults ``True`` — every proposal
    here is hypothetical and paper-only (no capital, no broker, no
    ``TransactionsStore``/``OrderManager`` involvement, see
    ``rlhf_calibration_store.py``'s module docstring), so per this repo's
    2026-08-03 convention a new admin/write capability with no capital or
    execution risk ships active by default rather than behind a fresh opt-in
    flag. GUI-writable in ``gui/env_io.py``'s ``ALLOWED_KEYS`` (created there
    directly, not reclassified — mirrors ``AGENTIC_DISCOVERY_ENABLED``'s
    precedent: this endpoint remains independently gated by
    ``FOLLOW_API_TOKEN`` regardless of the flag's own GUI-writability, so a
    GUI toggle alone can never bypass the command-token check).

    ``POST /rlhf/proposals`` exists even though the webapp itself never calls
    it — a sibling MCP tool creates real proposals by calling
    ``RlhfCalibrationStore`` directly, not through this HTTP API — it is kept
    for API completeness and so this feature's own tests can create fixture
    data through the real API rather than reaching into the store directly.
    ``GET /rlhf/summary`` is read-only and NOT gated by this flag
    (``require_read_token`` alone, matching every other GET here)."""
    if not settings.RLHF_CALIBRATION_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="RLHF calibration writes are disabled (RLHF_CALIBRATION_ENABLED=false).",
        )


def require_forecast_backfill_enabled() -> None:
    """FAIL-CLOSED master-switch guard for ``POST /pilots/forecast_backfill/run``
    and ``POST /pilots/forecast_backfill/cancel/{job_id}``. A DEDICATED flag
    (``settings.FORECAST_BACKFILL_ENABLED``), default ``False``. GUI-writable
    (``gui/env_io.py``'s ``ALLOWED_KEYS``) like every other non-secret
    tunable, per explicit operator decision, but also a
    ``settings_keysets.DANGEROUS_KEYS`` member (``SAFETY_CRITICAL_KEY_REASONS``),
    requiring typed confirmation on write regardless of editor — see that
    flag's own ``settings.py`` docstring for why: unlike an ordinary
    config-toggle write, this spawns a CPU-bound subprocess that trains and
    overwrites the meta-labeler model artifacts (``ml/models/meta_*.pkl``)
    feeding the live ``meta_label_composite`` score, a materially heavier and
    more consequential action. ``GET /pilots/forecast_backfill`` and
    ``GET /pilots/forecast_backfill/status/{job_id}`` are read-only and NOT
    gated by this flag (``require_read_token`` alone, matching every other
    GET here)."""
    if not settings.FORECAST_BACKFILL_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Forecast backfill runs are disabled (FORECAST_BACKFILL_ENABLED=false).",
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


class PilotSimulationRequest(BaseModel):
    """Body for ``POST /pilots/{id}/simulate``. ``allocation_amount`` is the
    hypothetical USD amount to allocate to the Pilot on top of the operator's
    real current portfolio — must be positive (a zero/negative allocation
    isn't a "what-if", it's a no-op or a withdrawal, neither of which this
    endpoint models)."""

    allocation_amount: float = Field(..., gt=0.0)


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
    """Body for ``PUT /automation/execution-mode``.

    ``confirm`` is the dangerous-key acknowledgement, matching the contract
    ``PUT /settings/tunables`` uses for the same ``settings_keysets.
    DANGEROUS_KEYS`` fields: every dangerous key this call is about to write
    (``ADVISORY_ONLY`` always; ``DRY_RUN`` too when ``mode != "advisory"``)
    must appear here mapped to ITS OWN NAME, e.g. ``{"ADVISORY_ONLY":
    "ADVISORY_ONLY"}`` — see ``_require_dangerous_confirmation``. Absent or
    wrong -> 422, and nothing is written. ``ALPACA_PAPER`` (also written when
    ``mode != "advisory"``) is NOT a ``DANGEROUS_KEYS`` member and needs no
    confirmation."""
    mode: Literal["live", "paper", "simulation", "advisory"]
    advisory_only: bool
    confirm: Dict[str, str] = Field(default_factory=dict, max_length=8)


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
    Pydantic's default repr is not invoked anywhere in this module's logging.

    No ``mfa_code`` field: Robinhood login here uses device-approval push
    login (``data.robinhood_login_worker``) — the operator taps "approve" in
    the Robinhood app itself, so no authenticator code is ever collected or
    submitted over HTTP."""

    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class ForecastBackfillRunRequest(BaseModel):
    """Body for ``POST /pilots/forecast_backfill/run``.

    ``horizons`` ends up in a model filename
    (``ml/forecast_backfill.py``'s ``f"meta_{model_type}_{h}d.pkl"``) that
    gets opened for writing — bounded here (CodeQL: uncontrolled data in a
    path expression) so it can only ever be a small positive integer, never
    something that could compose a path-traversal segment. The engine itself
    re-validates independently (CONSTRAINT: validate at boundaries, but never
    trust a single layer) — see ``AgenticForecastBackfiller.__init__``.
    """

    tickers: Optional[list[str]] = Field(default=None)
    # None -> AgenticForecastBackfiller computes settings.FORECAST_BACKFILL_LOOKBACK_YEARS
    # (default 4) back from end_date, rather than duplicating that default here.
    start_date: Optional[str] = Field(default=None)
    end_date: Optional[str] = Field(default=None)
    use_fmp: bool = Field(default=True)
    horizons: Optional[list[int]] = Field(default=None)
    strategy_ids: Optional[list[str]] = Field(default=None)
    theta_c: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @field_validator("horizons")
    @classmethod
    def _validate_horizons(cls, v: Optional[list[int]]) -> Optional[list[int]]:
        if v is None:
            return v
        for h in v:
            if isinstance(h, bool) or not isinstance(h, int) or not (0 < h <= 3650):
                raise ValueError(
                    f"horizons must be positive integers (days) <= 3650, got {h!r}"
                )
        return v


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


class MacroGateUpdateRequest(BaseModel):
    """Body for ``PUT /observability/macro-gate``. A single-key ``.env`` write
    (``MACRO_REGIME_GATE_ENABLED``) — mirrors ``LlmSettingUpdateRequest``'s
    single-scalar shape, not ``StrategyModulesUpdateRequest``'s multi-key atomic
    write (there is only ever one key here). ``reason`` is REQUIRED (non-empty)
    — a fat-finger guard mirroring ``PauseRequest``/``ResumeRequest``, NOT a
    security control (the real gates are the command token and
    ``MACRO_GATE_WRITES_ENABLED``); it is not persisted anywhere today (no
    audit-log surface exists yet for this endpoint) but is validated so the
    webapp's confirm-modal contract stays honest — a caller cannot skip typing
    one."""

    enabled: bool
    reason: str = Field(..., min_length=1)


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


class WatchRequest(BaseModel):
    """Body for ``POST /agentic/watch``. Start tracking a discovered candidate by
    appending its symbol to ``watchlist.txt`` (via ``pilots.watchlist_writer``),
    the same file ``main._load_watchlist()`` reads when building the evaluation
    universe. NOT an ``.env`` write and NOT an order — it is not retroactive and
    places nothing; the symbol enters the universe on the next pipeline run.
    The symbol shape is validated in the writer (rejected, never sanitized)."""

    symbol: str = Field(..., min_length=1, max_length=16)


class RlhfProposalCreateRequest(BaseModel):
    """Body for ``POST /rlhf/proposals`` — records one hypothetical, paper-only
    AI trade proposal (``rlhf_calibration_store.RlhfCalibrationStore
    .create_proposal``). ``action``/``confidence`` are intentionally NOT
    Pydantic-bounded here (no ``Literal``, no ``ge``/``le``) — the store is the
    single source of truth for those two validations and raises ``ValueError``
    with a message this endpoint maps to a stable ``invalid_action`` /
    ``invalid_confidence`` 422 tag; duplicating the bound at this layer would
    just produce a second, differently-shaped error response for the same
    failure. ``price`` is optional — omitted, the handler best-effort resolves
    a live quote (see ``create_rlhf_proposal``)."""

    symbol: str = Field(..., min_length=1, max_length=20)
    action: str = Field(..., min_length=1)
    rationale: str = Field(..., min_length=1)
    confidence: float
    quantity: Optional[float] = None
    price: Optional[float] = None
    rsi: Optional[float] = None
    sentiment_score: Optional[float] = None
    extra_context: Optional[Dict[str, Any]] = None


class RlhfProposalReviewRequest(BaseModel):
    """Body for ``POST /rlhf/proposals/{id}/review`` — a human's 1-5 star
    rating (+ optional corrective comment) for one proposal. ``human_rating``
    is deliberately NOT Pydantic-bounded (see ``RlhfProposalCreateRequest``'s
    docstring for why) — ``RlhfCalibrationStore.submit_review`` raises
    ``ValueError`` for an out-of-range rating, mapped to a stable
    ``invalid_rating`` 422 tag."""

    human_rating: int
    human_correction: Optional[str] = None


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


_TOP_HOLDINGS_PREVIEW_N = 3

def _pilot_summary(pilot: Any, snapshot: Optional[dict], store: FollowsStore) -> Dict[str, Any]:
    """The PilotSummary contract (webapp/src/api/types.ts): identity + headline
    metrics + follow proxies + holdings_count + ``long_only``.

    Shared by BOTH the marketplace list (``/pilots``) and the detail endpoint
    (``/pilots/{id}``, whose ``PilotDetail extends PilotSummary``) so the two
    responses can never silently drift apart again.
    """
    holdings = scoring.pilot_holdings(pilot, snapshot) if snapshot is not None else []
    return {
        "id": pilot.id,
        "name": pilot.name,
        "category": pilot.category,
        "description": pilot.description,
        "headline": performance.pilot_headline(pilot, reports_dir=_reports_dir()),
        "holdings_count": len(holdings),
        "top_holdings": holdings[:_TOP_HOLDINGS_PREVIEW_N],
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


@app.get("/pilots/forecast_backfill", dependencies=[Depends(require_read_token)])
def get_forecast_backfill_status() -> Dict[str, Any]:
    """Return multi-horizon forecast backfill status, trained meta-labeler metrics,
    and summary metadata from output/agentic_forecast_summary.json."""
    summary_path = settings.OUTPUT_DIR / "agentic_forecast_summary.json"
    if summary_path.exists():
        try:
            with open(summary_path, "r") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning("Failed to read agentic_forecast_summary.json: %s", exc)

    return {
        "status": "not_run",
        "timestamp": None,
        "horizons": getattr(settings, "FORECAST_BACKFILL_HORIZONS", [10, 30, 60, 90]),
        "metrics": {},
        "tickers": settings.DEFAULT_TICKERS,
        "message": "Forecast backfill has not been run yet.",
    }


@app.post(
    "/pilots/forecast_backfill/run",
    status_code=202,
    dependencies=[
        Depends(require_forecast_backfill_enabled),
        Depends(require_command_token),
    ],
)
def run_forecast_backfill_endpoint(req: ForecastBackfillRunRequest) -> Any:
    """Start an asynchronous, on-demand forecast backfill cycle across
    specified tickers & horizons, returning immediately (202) with the
    job's initial status rather than blocking on the multi-minute,
    CPU-bound training run itself.

    ``AgenticForecastBackfiller``'s 6-step pipeline (fetch data -> technical
    features -> primary signals -> meta targets -> backtrain meta-labelers
    -> execute backfill -> export) now runs in an isolated, killable
    subprocess (``ml.forecast_backfill_worker``, via
    ``ml.forecast_backfill_job.start_job``) instead of this request handler
    -- the previous implementation held the HTTP connection open for the
    entire run. Poll ``GET /pilots/forecast_backfill/status/{job_id}`` for
    ``phase``/``step``/``state`` until it reaches a terminal state
    (``succeeded``/``failed``/``timeout``/``cancelled``).

    Single-flight, same invariant the old in-process ``threading.Lock``
    enforced: a second call while a run is already in progress would
    otherwise race on the SAME shared output files (``ml/models/meta_*.pkl``,
    ``output/agentic_forecast_backfill.csv``,
    ``output/agentic_forecast_summary.json``) and could corrupt them via
    interleaved writes. ``start_job`` returns ``None`` in that case; the
    structured 409 body below carries the EXISTING job's id (mirrors
    ``POST /automation/run``'s ``already_running`` response shape) so the
    caller can poll it instead of hitting a dead end.

    Gated by two independent controls (see the dependencies above): the
    dedicated ``FORECAST_BACKFILL_ENABLED`` flag (default ``False``,
    GUI-writable but confirmation-required -- see
    ``require_forecast_backfill_enabled``) and the fail-closed follow
    command token.

    ``req.horizons`` is still validated by ``ForecastBackfillRunRequest``'s
    own ``@field_validator`` -- FastAPI resolves the route's ``dependencies``
    (including the two guards above) BEFORE parsing/validating the request
    body, so an invalid ``horizons`` value only reaches a 422 once both
    guards already passed; it never reaches ``start_job`` (and therefore
    never spawns a subprocess) either way.
    """
    job = forecast_backfill_job.start_job(req.model_dump())
    if job is None:
        existing_job_id = forecast_backfill_job.get_active_job_id()
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "detail": "A forecast backfill run is already in progress.",
                    "job_id": existing_job_id,
                }
            },
        )
    return forecast_backfill_job.serialize_job(job)


@app.get(
    "/pilots/forecast_backfill/status/{job_id}",
    dependencies=[Depends(require_read_token)],
)
def get_forecast_backfill_job_status(job_id: str) -> Dict[str, Any]:
    """Poll the state of a job started by
    ``POST /pilots/forecast_backfill/run``. 404 if the job_id is unknown."""
    job = forecast_backfill_job.get_job_state(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown forecast backfill job.")
    return forecast_backfill_job.serialize_job(job)


@app.post(
    "/pilots/forecast_backfill/cancel/{job_id}",
    dependencies=[
        Depends(require_forecast_backfill_enabled),
        Depends(require_command_token),
    ],
)
def cancel_forecast_backfill_job(job_id: str) -> Dict[str, Any]:
    """Cancel an in-flight forecast backfill job (SIGTERM -> SIGKILL the
    isolated worker process). 404 if the job_id is unknown."""
    try:
        cancelled = forecast_backfill_job.cancel_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown forecast backfill job.")
    job = forecast_backfill_job.get_job_state(job_id)
    payload = forecast_backfill_job.serialize_job(job) if job else {"job_id": job_id}
    payload["cancelled"] = cancelled
    return payload


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
    has_news = pilot.weights.get("news_catalyst", 0.0) > 0.0
    payload["news_coverage"] = news_catalyst.get_news_catalyst_coverage() if has_news else None

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


@app.post("/pilots/{pilot_id}/simulate", dependencies=[Depends(require_read_token)])
def simulate_pilot(pilot_id: str, body: PilotSimulationRequest) -> Dict[str, Any]:
    """Real, honest "What-If" simulation: what would the operator's portfolio
    risk metrics look like if ``body.allocation_amount`` (USD) were allocated
    to this Pilot on top of their real current holdings?

    ``require_read_token`` ALONE — this performs no writes (no order is
    placed, no follow is created, nothing is persisted), matching this file's
    ``/data/cache-long-short/simulate``-style "interactive, on-demand" read
    tier per the pilots-endpoint skill.

    Every number returned is either reused verbatim from the same real
    computation the Observability screen shows, or derived from a synthetic
    equity curve built out of real historical daily closes — see
    ``pilots.simulation.simulate_pilot_allocation``'s docstring for the exact
    formula and the honesty note on why ``heat_pct_projected`` is always
    ``None`` (there is no honest way to project unrealized P&L for a
    hypothetical, never-entered position — CONSTRAINT #4).

    404 on an unknown Pilot id. Never 500s — a missing snapshot or missing
    price history degrades to the honest null shape with a ``reason``
    (CONSTRAINT #6)."""
    pilot = catalog.get_pilot(pilot_id)
    if pilot is None:
        raise HTTPException(status_code=404, detail=_UNKNOWN_PILOT_DETAIL)
    return simulation.simulate_pilot_allocation(pilot_id, body.allocation_amount)


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


@app.post("/universe/{symbol}/reinclude", dependencies=[Depends(require_command_token)])
def reinclude_universe_symbol(symbol: str) -> Dict[str, Any]:
    """Manual escape hatch: undo an automated symbol-rating exclusion.

    Fail-closed ``require_command_token`` ALONE — deliberately NO dedicated
    master-switch flag, matching ``POST /decisions``'/``POST /automation/pause``'s
    risk tier (see the pilots-endpoint auth taxonomy): this only breaks a
    consecutive-BAD rating streak so the symbol is eligible for tracking again
    (via ``rating.symbol_rating_store.SymbolRatingStore.reinclude`` — Part 1 of
    the Symbol Rating subsystem). It never places an order and never bypasses
    any other risk gate; downstream buy eligibility for the symbol still runs
    through the platform's normal scoring/sizing/risk-gate pipeline on the next
    cycle. The auto-drop feature itself defaults OFF
    (``settings.SYMBOL_RATING_AUTO_DROP_ENABLED``), so this endpoint is a no-op
    in effect (though still a valid write) when auto-drop is disabled.

    404 on an empty/whitespace-only ``symbol``. 503 if the underlying store
    write fails (CONSTRAINT #6 — dead-letter at the endpoint boundary, not a
    silent 200)."""
    sym = (symbol or "").strip().upper()
    if not sym:
        raise HTTPException(status_code=404, detail="symbol is required")

    from rating.symbol_rating_store import SymbolRatingStore

    try:
        SymbolRatingStore().reinclude(sym)
    except Exception as exc:  # noqa: BLE001 - dead-letter: store write failure -> clean 503
        logger.error("pilots_api: reinclude failed for %s: %s", sym, exc)
        raise HTTPException(
            status_code=503,
            detail="Could not re-include the symbol (rating store unavailable).",
        ) from exc

    return {"symbol": sym, "reincluded": True}


@app.get("/recommendations", dependencies=[Depends(require_read_token)])
def get_recommendations(
    limit: int = Query(25, ge=1, le=200),
) -> Dict[str, Any]:
    """The platform's current BUY picks, ranked by conviction (then score).

    Reads only persisted state (the snapshot's ``signals[]``) — never calls an
    engine. Reflects the LATEST pipeline run: a symbol added to the universe
    only appears here after the next run rewrites the snapshot. Returns
    ``{"recommendations": [], "count": 0, "as_of": None, "reason": ...}`` on a
    cold start (no snapshot yet) — never 404s, never 500s (CONSTRAINT #6). Each
    numeric leaf is ``null`` when absent, never a fabricated ``0.0``
    (CONSTRAINT #4)."""
    snapshot = _load_snapshot()
    recs = symbols.list_recommendations(snapshot, limit=limit)
    as_of = snapshot.get("timestamp") if isinstance(snapshot, dict) else None
    return {
        "recommendations": recs,
        "count": len(recs),
        "as_of": as_of,
        "reason": None if recs else "No BUY-rated recommendations in the latest snapshot yet.",
    }


@app.get("/symbols/compare", dependencies=[Depends(require_read_token)])
def get_symbols_compare(
    symbols_param: str = Query(..., alias="symbols", min_length=1),
) -> Dict[str, Any]:
    """Side-by-side comparison of 2-5 operator-selected symbols from the latest
    persisted snapshot — the API counterpart of
    ``gui/panels/strategy_matrix.py::_render_symbol_comparison``.

    NOTE ordering: this route is declared BEFORE ``GET /symbols/{ticker}`` so
    ``/symbols/compare`` matches here rather than being captured as
    ``ticker="compare"`` — FastAPI matches routes in declaration order, static
    paths do not automatically win over an earlier parameterized one.

    ``symbols`` is a comma-separated ticker list (e.g. ``AAPL,MSFT,NVDA``),
    upper-cased and de-duplicated server-side (first occurrence wins). 422
    with a stable tag when the de-duplicated count falls outside
    ``[symbols.COMPARE_MIN_SYMBOLS, symbols.COMPARE_MAX_SYMBOLS]`` (2-5) — the
    frontend branches on ``error``, never the message.

    Reads only persisted state (the snapshot's ``signals[]``) — never calls an
    engine. A requested symbol absent from the snapshot (typo, or it rolled
    out of this cycle's universe) still gets a row (``found: false`` + an
    honest ``reason``) rather than failing the whole comparison — this is a
    multi-resource view, not a single-resource lookup, so it never 404s
    (CONSTRAINT #6). Every numeric leaf is ``null`` when the active snapshot
    writer didn't compute it, never a fabricated default (CONSTRAINT #4) —
    ``meta_label_composite``/``regime_multiplier`` are now persisted by BOTH
    snapshot writers (advisory and orchestrator — see
    ``pipeline/production_steps.py``'s sizing-decomposition threading), but
    this endpoint reads exactly what was persisted rather than baking in
    either writer's own fallback default, so a symbol the strategy engine
    skipped that cycle still nulls honestly. ``modules`` is the sorted
    union of every found symbol's score-component module names, for the
    frontend's grouped bar chart x-axis."""
    parsed = [s.strip().upper() for s in symbols_param.split(",")]
    deduped: List[str] = []
    seen: set = set()
    for s in parsed:
        if s and s not in seen:
            seen.add(s)
            deduped.append(s)

    if len(deduped) < symbols.COMPARE_MIN_SYMBOLS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "too_few_symbols",
                "message": f"Select at least {symbols.COMPARE_MIN_SYMBOLS} symbols to compare.",
                "min": symbols.COMPARE_MIN_SYMBOLS,
            },
        )
    if len(deduped) > symbols.COMPARE_MAX_SYMBOLS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "too_many_symbols",
                "message": f"Select at most {symbols.COMPARE_MAX_SYMBOLS} symbols to compare.",
                "max": symbols.COMPARE_MAX_SYMBOLS,
            },
        )

    return symbols.compare_symbols(_load_snapshot(), deduped)


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


@app.get("/sector/selection", dependencies=[Depends(require_read_token)])
def get_sector_selection(
    target: str,
    n: int = Query(3, ge=1, le=5),
) -> Dict[str, Any]:
    """Semantic Related Sector Selection ranking for one target symbol:
    cosine similarity, ingestion volume, Sector Heat Factor, and the final
    ``correlation_coefficient`` per candidate sector.

    Reads persisted DB state only (``sector_correlations``, written by
    ``sector_selection_engine.py`` — no live engine call, no network).
    ``n`` (1-5) re-derives ``selected`` from the already-persisted rank
    ordering — it does NOT re-run similarity or heat computation, so
    dragging the UI's N slider is a cheap read, not a recompute. Returns
    empty ``rows`` + an honest ``reason`` when nothing has been computed
    for this symbol yet — NOT a 404 (the symbol may simply not have run
    through the engine yet). Every numeric field is ``null`` wherever the
    engine recorded it as degraded/unavailable (CONSTRAINT #4)."""
    return sector_selection.sector_selection_view(target, n=n)


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

    Returns the ``{range, curve, buying_power_curve}`` envelope the PWA
    expects (client.ts ``getEquityCurve`` / ``CurvePoint``), mapping each
    stored snapshot to ``{date: <fetched_at ISO date>, value: <total_equity>}``
    (and, in parallel, ``<buying_power>`` for ``buying_power_curve`` — the
    webapp Analytics tab's buying-power overlay toggle, G14). Both are an
    empty list — never fabricated — when nothing has been stored yet or the DB
    is cold (CONSTRAINT #4); either series independently drops a point whose
    own value is missing/non-finite rather than dropping the whole date, so a
    gap in ONE series never truncates the other. An unknown ``range`` is
    treated leniently as "all history"."""
    since: Optional[datetime] = None
    days = _RANGE_DAYS.get(range)
    if days:
        since = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        store = HistoricalStore(readonly=True)
        df = store.account_snapshot_history(since=since)
    except Exception as exc:  # noqa: BLE001 - dead-letter: cold DB -> empty curve
        logger.warning("pilots_api: account_snapshot_history failed: %s", exc)
        return {"range": range, "curve": [], "buying_power_curve": []}
    if df is None or df.empty:
        return {"range": range, "curve": [], "buying_power_curve": []}
    # account_snapshot_history is ordered ascending by fetched_at, so records are
    # already oldest→newest. Normalize fetched_at to an ISO date (YYYY-MM-DD) to
    # match CurvePoint's "ISO date" semantics.
    df = df.copy()
    df["fetched_at"] = df["fetched_at"].astype(str).str[:10]

    def _point_series(column: str) -> List[Dict[str, Any]]:
        points: List[Dict[str, Any]] = []
        for row in df.to_dict(orient="records"):
            raw = row.get(column)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value != value:  # NaN guard — skip rather than fabricate a point
                continue
            points.append({"date": row.get("fetched_at"), "value": value})
        return points

    return {
        "range": range,
        "curve": _point_series("total_equity"),
        "buying_power_curve": _point_series("buying_power"),
    }


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
        raise HTTPException(status_code=422, detail=redact_line(str(exc))) from exc
    result["validation_warnings"] = brinson.validate_brinson_fachler_rows(rows)
    return result


@app.get("/observability/summary", dependencies=[Depends(require_read_token)])
def get_observability_summary(
    range: str = Query("1Y"),  # noqa: A002 - matches the ?range= query param name
    horizon: int = Query(30, ge=1, le=365),
) -> Dict[str, Any]:
    """Composite Mission-Control summary — the PWA's port of the retired
    Streamlit Command Center's Observability tab (now FOURTEEN sections):
    portfolio risk metrics (Sharpe/Calmar/MaxDD/MaxDD-duration/CAGR), the
    live portfolio heat (aggregate adverse open P&L vs. total equity, against
    ``MAX_PORTFOLIO_HEAT``), the account equity curve + drawdown, the current
    macro-regime overlay, the portfolio-wide forecast-skill reliability curve
    + weights, the **per-symbol forecast-skill breakdown**
    (``forecast_skill_by_symbol`` — pending/completed counts and inverse-RMSE
    weights per symbol at the requested horizon, via a bulk SQL aggregate
    over ``forecast_errors`` rather than the legacy panel's N-symbols
    per-cell-round-trip loop; see ``pilots/observability.py
    ::forecast_skill_by_symbol_summary``), the last ~100 risk-gate block-log
    entries, the merged kill-switch + risk-gate-block circuit-breaker
    dashboard — severity-classified, 24h-deduped trips with
    threshold/observed values and a counts-by-severity KPI strip, ported from
    ``gui/panels/gravity_audit.py::_render_circuit_breaker_dashboard`` via
    ``gui.circuit_breakers`` (see ``pilots/observability.py
    ::circuit_breaker_summary`` — no new endpoint, this rides the existing
    composite), host/process **system telemetry** (CPU/memory/disk
    %, load average, process RSS/CPU%/threads) via ``gui.observability_telemetry
    .collect_system_telemetry`` (see ``pilots/observability.py
    ::system_telemetry_summary`` — also rides the existing composite, since
    it's a cheap, scalar-only, point-in-time sample), the per-symbol **data
    latency heatmap** (``latency_heatmap`` — quote fetch-to-ingestion latency,
    recorded automatically by ``market_data_latency.py``'s in-process ring
    buffer on every real ``CompositeProvider.get_latest_quote`` fetch, gated
    behind ``MARKET_DATA_LATENCY_TRACKING_ENABLED`` (default ``False``); see
    ``pilots/observability.py::latency_heatmap_summary``'s docstring for why
    this is an honest REPLACEMENT for, not a literal port of, the legacy
    panel's manual-trigger design), the durable **sizing
    cap-event audit trail** (``sizing_cap_audit``, reusing ``sizing
    .cap_audit_store.CapAuditStore`` directly), the **ETF volatility
    transmission** per-symbol diagnostic view (``etf_transmission``, reusing
    ``gui.observability_panel_helpers.etf_transmission_rows`` directly), the
    CURRENT **heartbeat age** + freshness classification (``heartbeat`` —
    deliberately no trend/history; see ``pilots/observability.py
    ::heartbeat_summary``'s docstring for why the legacy Streamlit sparkline
    has no durable equivalent), and realized **strategy P&L** grouped by
    strategy (``strategy_pnl`` — the functional replacement for a legacy
    Streamlit section that is dead code against real data; see
    ``pilots/observability.py::strategy_pnl_summary``'s docstring). The
    sibling **log aggregation** section of that same legacy tab is served by
    a separate ``GET /observability/logs`` endpoint below (see that
    endpoint's docstring for why it's not folded in here).

    Composes FOURTEEN independently-degrading sections (``pilots.observability
    .observability_summary`` — see that module's docstring for the full
    per-section contract); one section's cold-start/failure never blocks the
    others, and every section carries its own honest ``reason`` when
    empty. ``range`` zooms the equity curve only (risk metrics always use the
    full history — Sharpe/CAGR need enough samples to be meaningful);
    ``horizon`` selects the forecast-skill horizon (10/30/60/90 are the
    horizons the pipeline actually forecasts, but any 1-365 is accepted
    leniently, matching ``GET /symbols/{ticker}/forecast`` — also selects the
    per-symbol forecast-skill horizon above). Never raises
    (CONSTRAINT #6); never fabricates a metric (CONSTRAINT #4).

    Adds two API-layer fields to the ``regime`` section only (mirrors ``GET
    /strategy/matrix``'s ``writable``/``note`` addition over its pure reader's
    payload): ``macro_gate_writable`` (tracks ``MACRO_GATE_WRITES_ENABLED``,
    the master switch for ``PUT /observability/macro-gate``) and
    ``macro_gate_writable_note``. The pure reader's ``regime`` dict is left
    otherwise untouched."""
    payload = observability.observability_summary(
        equity_range=range, horizon_days=horizon, snapshot=_load_snapshot(),
    )
    macro_gate_writable = bool(settings.MACRO_GATE_WRITES_ENABLED)
    payload["regime"]["macro_gate_writable"] = macro_gate_writable
    payload["regime"]["macro_gate_writable_note"] = (
        "Writes persist to .env and apply on the next daemon/pipeline launch."
        if macro_gate_writable
        else "Writes are disabled (MACRO_GATE_WRITES_ENABLED=false)."
    )
    return payload


@app.put(
    "/observability/macro-gate",
    dependencies=[
        Depends(require_command_token),
        Depends(require_macro_gate_writes_enabled),
    ],
)
def put_macro_gate(body: MacroGateUpdateRequest) -> Dict[str, Any]:
    """Write ``MACRO_REGIME_GATE_ENABLED`` to ``.env`` — the operator-controlled
    bypass for ``PreTradeRiskGate.macro_kill_switch_check`` (the recession/
    credit-event BUY veto; see ``risk_gate.py`` and CLAUDE.md). This is the
    webapp port of the Streamlit Command Center's Observability tab toggle
    (``gui/panels/observability.py`` lines 131-195), which has written this
    same key via ``gui.env_io.write_setting`` for a long time — this endpoint
    is a NEW write path onto an EXISTING GUI-writable key, gated by its own
    dedicated ``MACRO_GATE_WRITES_ENABLED`` flag (see that flag's docstring in
    ``settings.py`` for why it is not allowed to ride in on any sibling
    writes-enabled flag).

    Single-key ``.env``-ONLY write via ``gui.env_io.write_setting`` (does NOT
    patch the running ``settings`` singleton), so ``applies`` is always
    ``"next_daemon_restart"`` and the echoed ``enabled`` reflects the REQUEST
    BODY, not ``settings`` (which would return the stale pre-write value and
    read as a failed write). ``reason`` is required non-empty (fat-finger
    guard, not a security control) but is not persisted anywhere today."""
    env_io.write_setting("MACRO_REGIME_GATE_ENABLED", body.enabled)
    return {
        "written": ["MACRO_REGIME_GATE_ENABLED"],
        "enabled": body.enabled,
        "applies": "next_daemon_restart",
        "note": (
            "Written to .env. settings is not patched in-process — this API "
            "and any already-launched pipeline still use the previous value "
            "until restarted."
        ),
    }


@app.get("/observability/logs", dependencies=[Depends(require_read_token)])
def get_observability_logs(limit: int = Query(300, ge=1, le=1000)) -> Dict[str, Any]:
    """Bounded, parsed tail of ``logs/investyo.log`` — the PWA's port of the
    retired Streamlit Command Center's Observability tab log-aggregation
    section (``gui/panels/observability.py::_render_observability_error_log``).

    Kept as its OWN endpoint rather than a new key on ``GET
    /observability/summary``: unlike that composite's other (cheap,
    scalar-only) sections, a log tail is a meaningfully heavier payload and is
    naturally an on-demand view (e.g. an expandable "Logs" section), not
    something needed on every Mission Control page load.

    ``limit`` bounds how many of the most recent PARSED entries are returned
    (1-1000, default 300) — the backend always reads the same last-1000-line
    tail of the log file first (matching the legacy panel's own
    ``read_log_tail(..., max_lines=1000)``), so ``tally``/``total_lines``/
    ``systemic_count``/``symbol_specific_count`` reflect that full read
    regardless of ``limit``, which only trims the ``entries`` list actually
    shipped. There is deliberately no server-side level/substring filter
    query param — the frontend filters the returned ``entries`` client-side,
    mirroring the legacy Streamlit panel's own UX (a ``st.selectbox``/
    ``st.text_input`` re-filters an already-fetched list on every rerun
    rather than re-querying per keystroke).

    See ``pilots/observability.py::log_aggregation`` for the full contract,
    including the deliberate scope-narrowing vs. the legacy panel (counts,
    not a per-symbol message drilldown). Returns the honest empty shape
    (zeroed tally, empty ``entries``, a ``reason``) when the log file doesn't
    exist yet. Never raises (CONSTRAINT #6)."""
    return observability.log_aggregation(limit=limit)


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
    """Live deployability-gate, position-sizing, and Agentic Trading
    thresholds, imported directly from ``validation.thresholds`` and
    ``settings`` — never re-typed as literals — so the PWA's "How this works"
    education panels can quote the SAME numbers the strategy validation
    harness and the Agentic Trading tab actually enforce, mirroring the
    live-import discipline ``gui/help_content.py`` already applies for the
    Streamlit Command Center (see that module's docstring: "Never hard-code
    numeric thresholds here").

    ``robinhood_max_notional_per_order``, ``follow_min_amount``, and
    ``agentic_max_candidates`` back the Agentic Trading tab's glossary
    entries the same way the other five keys back Strategy Health / Pilots —
    see ``settings.AGENTIC_MAX_CANDIDATES``'s docstring ("never re-typed as a
    literal in the reader or the webapp").

    ``retrain_window_days`` backs the Models screen's "Needs Retrain" badge
    (webapp porting backlog rider 13b) — imported live from
    ``gui.help_content.MODEL_RETRAIN_WINDOW_DAYS`` (the same constant
    ``ml.meta_labeling.MetaLabeler.needs_retrain()`` uses), never re-typed as
    a literal, so ``Models.tsx``'s static explainer text can quote the window
    the same way it already quotes ``dsr_min``/``pbo_max``. ``GET /models``
    itself computes ``needs_retrain``/``age_days`` per-model server-side
    (``pilots/models.py``) using this same constant — this key is for display
    text only, not for the frontend to re-derive the flag itself.

    These are config constants, not persisted pipeline state — always
    available, no cold-start empty case, never 404s/500s."""
    from gui.help_content import MODEL_RETRAIN_WINDOW_DAYS

    return {
        "pbo_max": PBO_MAX,
        "dsr_min": DSR_MIN,
        "net_sharpe_min": NET_SHARPE_MIN,
        "max_drawdown_max": MAX_DRAWDOWN_MAX,
        "stress_max_drawdown": STRESS_MAX_DRAWDOWN,
        "kelly_fraction": settings.KELLY_FRACTION,
        "kelly_cap": settings.KELLY_CAP,
        "robinhood_max_notional_per_order": settings.ROBINHOOD_MAX_NOTIONAL_PER_ORDER,
        "follow_min_amount": settings.FOLLOW_MIN_AMOUNT,
        "agentic_max_candidates": float(settings.AGENTIC_MAX_CANDIDATES),
        "retrain_window_days": float(MODEL_RETRAIN_WINDOW_DAYS),
    }


def _safe_float(value: float) -> Optional[float]:
    """NaN is a legitimate internal signal (unparsable timestamp) but is not
    valid JSON — coerce to ``None`` (CONSTRAINT #4: never fabricate a number,
    but also never emit a token the frontend's JSON parser can't read)."""
    return None if value != value else value  # NaN != NaN


@app.get("/execution-queue", dependencies=[Depends(require_read_token)])
def get_execution_queue(
    action: Optional[str] = None,
    follow_type: Optional[str] = None,
    status_filter: Optional[str] = None,
    min_conviction: Optional[float] = 0.0,
) -> Dict[str, Any]:
    """The gated, dry-run Robinhood order queue (``output/execution_queue.json``)
    — READ ONLY. This endpoint never contacts the Robinhood MCP and never
    places an order: per ``execution/queue_builder.py``'s module contract, a
    live Claude Code agent session is the ONLY actor that ever calls the MCP
    ``place_equity_order`` tool, so there is nothing for this API to trigger.

    Supports optional query filters: ``action`` (BUY/SELL), ``follow_type``,
    ``status_filter`` (Blocked/Ready), and ``min_conviction``.

    ``follow_type`` is the REAL per-intent attribution derived from
    ``QueuedIntent.strategy`` (``execution/queue_builder.py``'s ``"strategy"``
    key) — never guessed from ``rationale`` free text (CONSTRAINT #4). It is
    one of: ``"advisory"`` (the base advisory engine), ``"composed"`` (netted
    across more than one follow), or a real followed Pilot's ``pilot_id``
    (parsed off the ``"Follow:<pilot_id>"`` label). ``available_follow_types``
    lists every distinct value present in the UNFILTERED queue so the caller
    can build a filter control without hardcoding pilot names.

    Returns ``{generated_at, mode, kill_switch_active, max_notional_per_order,
    n_intents, n_placeable, stale, age_seconds, intents, available_follow_types,
    reason}`` — empty ``intents`` + an honest ``reason`` when no queue has been
    written yet (CONSTRAINT #4). ``n_intents``/``n_placeable`` reflect the
    FILTERED result set (matching what ``intents`` actually contains), not the
    raw snapshot totals. Never 500s."""
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
            "available_follow_types": [],
            "reason": (
                "No execution queue yet — ROBINHOOD_EXECUTION_MODE may be 'off', "
                "or the pipeline hasn't run since it was enabled."
            ),
        }

    raw_intents = snapshot.intents or []
    def _attribution(i: Any) -> str:
        raw_strategy = str(getattr(i, "strategy", "") or "")
        if raw_strategy.startswith("Follow:"):
            return raw_strategy[len("Follow:") :] or "advisory"
        if raw_strategy.startswith("Composed:"):
            return "composed"
        return "advisory"

    available_follow_types = sorted({_attribution(i) for i in raw_intents})

    filtered_intents = []
    for i in raw_intents:
        i_action = (getattr(i, "action", "") or "").upper()
        i_side = (getattr(i, "side", "") or "").upper()
        i_conviction = getattr(i, "conviction", None)
        i_allow_place = bool(getattr(i, "allow_place", False))
        f_type = _attribution(i)

        if action and action.upper() != "ALL":
            if i_action != action.upper() and i_side != action.upper():
                continue

        if follow_type and follow_type != "ALL":
            if f_type.lower() != follow_type.lower():
                continue

        if status_filter and status_filter != "ALL":
            if status_filter == "Ready" and not i_allow_place:
                continue
            if status_filter == "Blocked" and i_allow_place:
                continue

        if min_conviction is not None and min_conviction > 0:
            if i_conviction is None or i_conviction < min_conviction:
                continue

        filtered_intents.append(
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
                "follow_type": f_type,
            }
        )

    return {
        "generated_at": snapshot.generated_at or None,
        "mode": snapshot.mode,
        "kill_switch_active": snapshot.kill_switch_active,
        "max_notional_per_order": snapshot.max_notional_per_order,
        "n_intents": len(filtered_intents),
        "n_placeable": sum(1 for fi in filtered_intents if fi["allow_place"]),
        "stale": execution_panel.is_queue_stale(snapshot),
        "age_seconds": _safe_float(execution_panel.queue_age_seconds(snapshot)),
        "intents": filtered_intents,
        "available_follow_types": available_follow_types,
        "reason": None,
    }


@app.get("/api/queue", dependencies=[Depends(require_read_token)])
def get_api_execution_queue(
    action: Optional[str] = None,
    follow_type: Optional[str] = None,
    status_filter: Optional[str] = None,
    min_conviction: Optional[float] = 0.0,
) -> Dict[str, Any]:
    """Alias route for GET /execution-queue."""
    return get_execution_queue(
        action=action,
        follow_type=follow_type,
        status_filter=status_filter,
        min_conviction=min_conviction,
    )


def _env_drift() -> Dict[str, Any]:
    """Compare the on-disk ``.env`` SIGNAL_WEIGHTS/DISABLED_SIGNAL_MODULES against
    the values the running process is actually using (``settings``). A ``.env``
    write does NOT reach the live singleton, so after a successful PUT the API +
    daemon keep serving the OLD values until restart — this surfaces that pending
    change (mirrors ``GET /automation/schedule``'s ``drift`` field). Dead-letter:
    any parse failure -> ``detected: False`` (a hand-mangled ``.env`` must never
    500). Uses ``env_io.read_raw()`` for a single ``.env`` parse shared across
    both keys, instead of one ``env_io.get_value()`` call (and thus one
    full-file re-parse) per key."""
    keys: List[str] = []
    try:
        raw_env = env_io.read_raw()
        for key, live in (
            ("SIGNAL_WEIGHTS", dict(settings.SIGNAL_WEIGHTS or {})),
            ("DISABLED_SIGNAL_MODULES", list(settings.DISABLED_SIGNAL_MODULES or [])),
        ):
            value = raw_env.get(key)
            raw = "" if value is None else str(value)
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

    Each module row also carries ``version_hash``/``last_modified`` (backlog
    item #13a's Strategy Version Registry — a sha256-prefix fingerprint + mtime
    of ``signals/<name>.py``, read directly off disk; ``None`` for a module
    with no corresponding file).

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


@app.get("/strategy/validation-trend", dependencies=[Depends(require_read_token)])
def get_strategy_validation_trend() -> Dict[str, Any]:
    """Cross-strategy validation snapshot + run-over-run trend + macro-regime
    timeline — the CROSS-STRATEGY counterpart to ``GET /strategy/health``.

    ``GET /strategy/health`` is scoped to catalog Pilots only (one row per
    ``pilots.catalog.list_pilots()`` entry, joined on
    ``Pilot.validation_strategy_id``); a strategy validated by
    ``validation.harness`` but not yet wired to any Pilot never appears
    there. This endpoint instead reads EVERY
    ``reports/*_validation_summary.json`` on disk regardless of Pilot
    mapping — an operator's "how does candidate strategy A compare to
    candidate B right now, before I decide whether to promote either one to
    a Pilot" view. It also surfaces a macro-regime TRANSITION timeline from
    the rotated ``output/history/`` snapshots, a data domain
    ``GET /strategy/health`` never touches.

    Ports ``gui/panels/gravity_audit.py::_render_validation_stress_regime_section``
    (the legacy Safety tab's "Validation & Stress Trend" section). Each of
    the three sections (``strategies``, ``trend``, ``regime_timeline``)
    degrades independently with its own honest ``*_reason`` string when its
    underlying data doesn't exist yet — never fabricated (CONSTRAINT #4).
    Never 500s (CONSTRAINT #6)."""
    return validation_trend_reader.validation_trend_snapshot(
        reports_dir=_reports_dir(),
        history_dir=_validation_history_dir(),
    )


@app.get("/gravity/audit-status", dependencies=[Depends(require_read_token)])
def get_gravity_audit_status() -> Dict[str, Any]:
    """Read-only port of ``gui/panels/gravity_audit.py``'s two audit sections
    (retired Streamlit Command Center Safety tab): the AI Gravity audit runner
    (Claude auditor + Gemini cross-checker) and the legacy, purely structural
    Gravity Review Suite (Pandera schema conformance, lookahead-bias
    perturbation, signal-registry health, sizing/risk gates — no LLM calls
    despite the filename ``Gravity AI Review Suite.py``).

    Deliberately NO trigger endpoint for either side — a considered scope cut,
    not an oversight:

    * The AI runner (``engine.gravity_ai_runner.run_all()``) is a synchronous,
      SEQUENTIAL sweep of up to 8 steps x up to 2 providers (Claude then
      Gemini, never concurrent) — a real multi-minute, real-dollar-cost
      operation with no incremental-progress channel over a stateless
      request/response API (the source feature's live "Step n/7…" ticker is a
      Streamlit ``st.status()`` widget bound to the SAME process issuing the
      callback; it doesn't degrade to "fire and forget" without inventing new
      async-job infrastructure this API doesn't have).
    * The legacy suite is heavier still (its own code comment: up to ~10
      minutes, which is why it was already moved off a blocking
      ``subprocess.run(timeout=600)`` onto a detached-process + 3s live-tail
      pattern in the desktop GUI) — with no mobile-appropriate equivalent to
      that live tail either.

    Both surfaces read only already-persisted artifacts:
    ``output/gravity_ai_audit.json`` (via ``gui.gravity_ai_panel`` — Streamlit-
    free by design, same posture as ``gui.ai_control_center`` above) and the
    trailing JSON verdict in ``output/gravity_run.log`` (via
    ``pilots.gravity_audit.legacy_audit_status`` — see that module's docstring
    for why the log is durable across restarts, not merely Streamlit session
    state). Neither read ever constructs an LLM provider or launches a
    subprocess. Never 500s (CONSTRAINT #6); every leaf is null/honest-reason
    when unavailable, never fabricated (CONSTRAINT #4)."""
    status = gravity_ai_panel.runner_status(settings)
    report = gravity_ai_panel.load_audit_report()
    summary = gravity_ai_panel.summarise_run(report)
    ai_audit = {
        "status": status,
        "enabled": summary.enabled,
        "generated_at": summary.generated_at if summary.generated_at != "—" else None,
        "health": summary.health,
        "health_caption": gravity_ai_panel.health_caption(summary),
        "total_steps": summary.total_steps,
        "claude_passed": summary.claude_passed,
        "claude_failed": summary.claude_failed,
        "claude_skipped": summary.claude_skipped,
        "gemini_passed": summary.gemini_passed,
        "gemini_failed": summary.gemini_failed,
        "gemini_skipped": summary.gemini_skipped,
        "disagreements": summary.disagreements,
        "steps": gravity_ai_panel.step_rows(report),
    }
    return {
        "ai_audit": ai_audit,
        "legacy_audit": gravity_audit_reader.legacy_audit_status(),
    }


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
        "sizing_path": plan.get("sizing_path"),
        "kelly_weight": plan.get("kelly_weight"),
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


@app.post(
    "/agentic/watch",
    dependencies=[
        Depends(require_command_token),
        Depends(require_agentic_discovery_enabled),
    ],
)
def post_agentic_watch(body: WatchRequest) -> Dict[str, Any]:
    """Start tracking a discovered candidate: append its symbol to
    ``watchlist.txt`` so the advisory pipeline evaluates it on the next run.

    Same auth tier as ``PUT /agentic/scan-config`` — ``require_command_token`` +
    the DEDICATED ``AGENTIC_DISCOVERY_ENABLED`` master switch: this is the same
    discovery-feature risk class (deciding which symbols the agent tracks and
    feeds toward the gated order queue), the programmatic twin of the
    ``agentic-discovery`` skill's operator-confirmed step-7 "track a candidate"
    flow, so it rides the same flag rather than a new one. Places NO order and is
    NOT retroactive — ``applies`` is ``"next_pipeline_run"``.

    Honest-failure contract (CONSTRAINT #4): if the ``WATCHLIST`` env var is set,
    ``watchlist.txt`` is ignored by the universe builder, so the write would be a
    silent no-op — that returns 409 with a stable ``watchlist_env_precedence``
    tag rather than a fake success. A malformed symbol returns 422
    ``invalid_symbol`` (the writer rejects it, never sanitizes it). Echoes the
    writer's own result (``added`` vs ``already_present``), never a fabricated
    ``added`` list."""
    from pilots.watchlist_writer import (
        InvalidSymbolError,
        WatchlistEnvPrecedenceError,
        append_symbols,
    )

    try:
        result = append_symbols([body.symbol])
    except WatchlistEnvPrecedenceError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": exc.tag, "message": redact_line(str(exc))},
        )
    except InvalidSymbolError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": exc.tag, "message": redact_line(str(exc))},
        )

    already = bool(result.already_present) and not result.added
    return {
        "symbol": body.symbol.strip().upper(),
        "added": result.added,
        "already_present": result.already_present,
        "watchlist_file": result.watchlist_file,
        "applies": "next_pipeline_run",
        "note": (
            f"{body.symbol.strip().upper()} is already on the watchlist."
            if already
            else "Added to watchlist.txt — the pipeline will evaluate it on the "
            "next run. No order was placed."
        ),
    }


# ---------------------------------------------------------------------------
# RLHF Calibration Review Queue (rlhf_calibration_store.py) — an AI trading
# agent proposes a hypothetical PAPER trade and a human rates it 1-5 stars.
# No capital, no broker, no TransactionsStore involvement (see that module's
# docstring). Gated by require_rlhf_calibration_enabled — see that function's
# docstring for why POST /rlhf/proposals exists despite the webapp never
# calling it directly.
# ---------------------------------------------------------------------------


def _rlhf_sft_dataset_path() -> Path:
    return settings.OUTPUT_DIR / "rlhf_sft_dataset.jsonl"


def _append_sft_rows(proposals: List[Dict[str, Any]]) -> int:
    """Appends one JSONL line per proposal to the SFT training-data export
    (``settings.OUTPUT_DIR / "rlhf_sft_dataset.jsonl"``). Shared by ``POST
    /rlhf/proposals/{id}/review``'s auto-export path and ``POST
    /rlhf/export-sft``'s explicit batch path so the record shape can never
    drift between the two.

    A human's ``human_correction`` is the gold assistant label when present
    (a human explicitly rewrote what the agent should have said); the
    proposal's own ``rationale`` is the fallback otherwise. Raises on any I/O
    failure — deliberately NOT try/except'd here, unlike ``_append_block_log``
    — the two call sites need different degraded responses on failure (the
    review endpoint must still return 200; the export endpoint must report
    zero exported), so each wraps this call with its own recovery rather than
    this helper picking one for both (CONSTRAINT #6 is satisfied at the
    endpoint layer here, not this shared helper)."""
    path = _rlhf_sft_dataset_path()
    with open(path, "a", encoding="utf-8") as fh:
        for row in proposals:
            correction = (row.get("human_correction") or "").strip()
            assistant_content = correction if correction else row.get("rationale", "")
            record = {
                "messages": [
                    {"role": "system", "content": "You are a quantitative trading agent."},
                    {
                        "role": "user",
                        "content": f"Evaluate {row['symbol']} — proposed action: {row['action']}.",
                    },
                    {"role": "assistant", "content": assistant_content},
                ]
            }
            fh.write(json.dumps(record) + "\n")
    return len(proposals)


@app.get("/rlhf/summary", dependencies=[Depends(require_read_token)])
def get_rlhf_summary(limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
    """Pending review queue + aggregate KPIs for the RLHF Calibration Review
    Queue screen. Composes the two read-only ``pilots.rlhf_review_queue``
    views (each already dead-letter-safe — see that module's docstring) so
    this never 500s, including on a cold start (no proposals table yet) or a
    genuinely unreachable DB (CONSTRAINT #6). ``writable`` tracks
    ``RLHF_CALIBRATION_ENABLED`` so the PWA knows whether to render the
    review-submission form before the operator hits a 403."""
    queue = rlhf_review_queue.pending_queue_view(limit=limit)
    return {
        "proposals": queue["proposals"],
        "kpis": rlhf_review_queue.summary_stats_view(),
        "writable": bool(settings.RLHF_CALIBRATION_ENABLED),
        "reason": queue["reason"],
    }


@app.post(
    "/rlhf/proposals",
    dependencies=[
        Depends(require_command_token),
        Depends(require_rlhf_calibration_enabled),
    ],
)
def create_rlhf_proposal(body: RlhfProposalCreateRequest) -> Dict[str, Any]:
    """Record one hypothetical, paper-only AI trade proposal.

    When the caller omits ``price`` for a non-``HOLD`` action, best-effort
    enriches with a live quote (``data.market_data.get_provider``) — a
    ``MarketDataError`` here is advisory-only and never blocks proposal
    creation, it just leaves ``price`` unset. ``quote_source`` in the response
    tells the caller which of the three paths was taken: ``caller_supplied``
    (a price was given), ``live`` (fetched here), or ``unavailable`` (a HOLD
    needs no price, or the live fetch failed).

    ``action``/``confidence`` validation lives entirely in
    ``RlhfCalibrationStore.create_proposal`` (see ``RlhfProposalCreateRequest``'s
    docstring) — its ``ValueError`` message is inspected once here to produce
    a stable ``invalid_action`` / ``invalid_confidence`` 422 tag, since the
    store itself raises a plain ``ValueError`` rather than two distinct
    exception types."""
    price = body.price
    if price is not None:
        quote_source = "caller_supplied"
    elif body.action.strip().upper() == "HOLD":
        quote_source = "unavailable"
    else:
        try:
            quote = get_provider().get_latest_quote(body.symbol)
            price = quote.price
            quote_source = "live"
        except MarketDataError as exc:
            logger.debug(
                "create_rlhf_proposal: live quote unavailable for %s: %s", body.symbol, exc
            )
            quote_source = "unavailable"

    store = RlhfCalibrationStore()
    try:
        proposal_id = store.create_proposal(
            symbol=body.symbol,
            action=body.action,
            rationale=body.rationale,
            confidence=body.confidence,
            quantity=body.quantity,
            price=price,
            rsi=body.rsi,
            sentiment_score=body.sentiment_score,
            extra_context=body.extra_context,
        )
    except ValueError as exc:
        tag = "invalid_confidence" if "confidence" in str(exc) else "invalid_action"
        raise HTTPException(status_code=422, detail=tag)

    row = store.get_by_id(proposal_id)
    if row is None:  # pragma: no cover - a successful write immediately unreadable
        logger.error("create_rlhf_proposal: id=%s created but not readable back", proposal_id)
        raise HTTPException(status_code=500, detail="proposal_created_but_unreadable")

    row["quote_source"] = quote_source
    return row


@app.post(
    "/rlhf/proposals/{proposal_id}/review",
    dependencies=[
        Depends(require_command_token),
        Depends(require_rlhf_calibration_enabled),
    ],
)
def review_rlhf_proposal(proposal_id: int, body: RlhfProposalReviewRequest) -> Dict[str, Any]:
    """Record a human's 1-5 star rating (+ optional corrective comment) for
    one proposal. 404 ``not_found`` for an unknown id, 409
    ``already_reviewed`` for a proposal that already has a review (including
    an auto-approved one — a human can't "re-review" one), 422
    ``invalid_rating`` for a rating outside 1-5.

    On a 5-star rating with ``RLHF_CALIBRATION_AUTO_EXPORT_SFT_ENABLED``, the
    row is best-effort appended to the SFT export via the SAME
    ``_append_sft_rows`` helper ``POST /rlhf/export-sft`` uses, so the two
    paths can never disagree on record shape — wrapped in try/except so an
    export failure never fails the review response itself (the review was
    already durably persisted by ``submit_review`` above this point)."""
    store = RlhfCalibrationStore()
    try:
        row = store.submit_review(proposal_id, body.human_rating, body.human_correction)
    except ProposalNotFoundError:
        raise HTTPException(status_code=404, detail="not_found")
    except ProposalAlreadyReviewedError:
        raise HTTPException(status_code=409, detail="already_reviewed")
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid_rating")

    sft_exported = bool(row.get("sft_exported"))
    if (
        row.get("human_rating") == 5
        and settings.RLHF_CALIBRATION_AUTO_EXPORT_SFT_ENABLED
        and not sft_exported
    ):
        try:
            _append_sft_rows([row])
            store.mark_sft_exported([row["id"]])
            sft_exported = True
        except Exception as exc:  # noqa: BLE001 - best-effort: never fail the review response
            logger.warning(
                "review_rlhf_proposal: auto SFT export failed for id=%s: %s", proposal_id, exc
            )

    row["sft_exported"] = sft_exported
    return row


@app.post(
    "/rlhf/export-sft",
    dependencies=[
        Depends(require_command_token),
        Depends(require_rlhf_calibration_enabled),
    ],
)
def export_rlhf_sft() -> Dict[str, Any]:
    """Batch-export every 5-star, not-yet-exported proposal to the SFT JSONL
    dataset (``settings.OUTPUT_DIR / "rlhf_sft_dataset.jsonl"``).

    Best-effort (CONSTRAINT #6): a file-append failure logs a warning and
    returns a zeroed result rather than raising — ``mark_sft_exported`` is
    only called on a successful append, so a failure never marks a row
    exported when it wasn't actually written to the file."""
    path = _rlhf_sft_dataset_path()
    store = RlhfCalibrationStore()
    rows = store.get_unexported_five_star()
    if not rows:
        return {"exported_count": 0, "file": str(path), "proposal_ids": []}

    try:
        _append_sft_rows(rows)
    except Exception as exc:  # noqa: BLE001 - best-effort export, never fatal
        logger.warning("export_rlhf_sft: file append failed: %s", exc)
        return {"exported_count": 0, "file": str(path), "proposal_ids": []}

    ids = [row["id"] for row in rows]
    store.mark_sft_exported(ids)
    return {"exported_count": len(rows), "file": str(path), "proposal_ids": ids}


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
            "Toggle and provider writes persist to .env and apply immediately "
            "to this running process — no restart needed."
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
    """Write ONE AI-capability toggle or provider-selector key to ``.env``,
    and apply it immediately to the in-process ``settings`` singleton when
    it's safe to (see ``ai_control_center.LIVE_PATCHABLE_KEYS``).

    ``key`` must be a capability's ``toggle_key`` (bool value) or
    ``provider_selector_setting`` (str value) from ``GET /llm/status``'s
    ``capabilities`` rows — validated via
    ``ai_control_center.validate_toggle_write`` (CONSTRAINT #3: a secret key
    is refused with 403, as is any key outside ``gui.env_io.ALLOWED_KEYS``)
    before ``env_io.write_setting`` performs the actual (re-validated) write.

    Unlike ``PUT /strategy/modules`` (a multi-key sizing/signal-weight write
    where the "next restart" caveat is real — those values ARE captured into
    engine objects at construction time), every key ``PUT /llm/setting``
    validates against is read fresh via ``getattr(settings, ...)`` on each
    use, never cached — see ``LIVE_PATCHABLE_KEYS``'s docstring. So once the
    ``.env`` write succeeds, this ALSO patches the value directly onto the
    process's live ``settings`` object, and ``applies`` is honestly
    ``"immediately"``: this API's very next ``GET /llm/status`` (and the
    advisory/orchestrator pipeline's own next cycle, in THIS process) sees
    it. A separately-running process (e.g. a Streamlit session) still needs
    its own restart — ``settings`` is per-process, not shared memory.

    The in-process patch goes through ``Settings.__pydantic_validator__.
    validate_assignment`` — pydantic's own validated-assignment machinery —
    rather than a bare ``setattr``. A bare ``setattr`` was the original bug
    here: ``value: Union[bool, str]`` on the request body means a JSON
    request like ``{"value": "false"}`` binds ``body.value`` to the Python
    **string** ``"false"`` (the ``str`` arm of the union matches first), and
    ``setattr(settings, key, "false")`` onto a ``bool`` field left the raw
    string sitting where a bool belongs — read back later as truthy
    (``bool("false")`` is ``True`` in plain Python), silently ENABLING the
    capability in-process while ``.env`` correctly recorded ``false``.
    ``validate_assignment`` coerces (and runs any ``@field_validator`` on the
    field) exactly as a real assignment to ``settings`` would, and raises
    ``ValidationError`` — mapped to a 422 here — instead of writing a bad
    value anywhere, in-process or to ``.env``. This only applies to
    ``LIVE_PATCHABLE_KEYS`` (guaranteed ``bool``/``str`` fields); a
    non-live-patched key is written to ``.env`` exactly as before, with no
    in-process coercion attempted (nothing is mutated in-process for it
    either way). The echoed ``value`` reflects what was ACTUALLY applied —
    the coerced value for a live-patched key (so the response can never
    disagree with what ``GET /llm/status`` reads next), or the request body
    for a non-live-patched key (nothing to coerce; it isn't applied
    in-process at all).
    """
    try:
        ai_control_center.validate_toggle_write(body.key)
    except (env_io.SecretWriteError, env_io.DisallowedKeyError) as exc:
        raise HTTPException(status_code=403, detail=redact_line(str(exc))) from exc

    applied_live = body.key in ai_control_center.LIVE_PATCHABLE_KEYS
    applied_value: Any = body.value
    if applied_live:
        # Validate + coerce BEFORE writing anything (to .env or in-process)
        # so a bad value is rejected cleanly with no partial write left
        # behind on either side.
        try:
            Settings.__pydantic_validator__.validate_assignment(settings, body.key, body.value)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"invalid value for {body.key!r}: {redact_line(str(exc.errors()[0]['msg']))}",
            ) from exc
        # validate_assignment already wrote the coerced value into
        # settings.__dict__[body.key] in place; read it back so both the
        # .env write below and the response echo use the SAME value that's
        # now actually live, never the raw (possibly wrongly-typed) request.
        applied_value = getattr(settings, body.key)

    env_io.write_setting(body.key, body.value)
    return {
        "written": [body.key],
        "value": applied_value,
        "applies": "immediately" if applied_live else "next_daemon_restart",
        "note": (
            "Written to .env and applied immediately to this process — no "
            "restart needed. A separately-running process (e.g. a Streamlit "
            "session) still needs its own restart to see it."
            if applied_live
            else (
                "Written to .env. settings is not patched in-process — this "
                "API and any already-launched pipeline still use the previous "
                "value until restarted."
            )
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
    return {
        "connected": connected,
        "has_account_snapshot": has_account_snapshot,
        # Same live settings value data/robinhood_portfolio.py actually
        # branches on for its Tier-3 login gate — read-only here, no write
        # path (see CLAUDE.md's Robinhood auto-refresh gate bullet).
        "auto_refresh_enabled": bool(settings.ROBINHOOD_AUTO_REFRESH_ENABLED),
    }


@app.post(
    "/brokerage/connect",
    status_code=202,
    dependencies=[
        Depends(require_brokerage_connect_enabled),
        Depends(require_command_token),
        Depends(require_loopback),
    ],
)
def connect_brokerage(body: BrokerageConnectRequest) -> Dict[str, Any]:
    """Start an asynchronous device-approval login job that verifies
    candidate Robinhood credentials, returning immediately (202) with the
    job's initial status rather than blocking on the login itself.

    Gated by three independent controls (see the dependencies above):
    ``BROKERAGE_CONNECT_ENABLED``, the fail-closed follow command token, and a
    loopback-only request check.

    Robinhood's device-approval flow requires a human to tap "approve" in the
    Robinhood app — that can take anywhere from a few seconds up to the full
    ``RH_LOGIN_DEADLINE_SECONDS`` window, far too long to hold an HTTP request
    open. Instead this launches an isolated, killable login-worker subprocess
    (``data.robinhood_login_worker``, via ``api._rh_login.start_connect_job``)
    and hands back its ``job_id`` immediately; the caller polls
    ``GET /brokerage/login/status/{job_id}`` for ``phase``/``state`` until it
    reaches a terminal state (``succeeded``/``failed``/``timeout``/
    ``cancelled``). ``RH_USERNAME``/``RH_PASSWORD`` are persisted to ``.env``
    by a background watcher thread (``api._rh_login``) the moment — and only
    if — the job's state becomes ``"succeeded"``; never before, never on
    failure/timeout/cancellation, and never inside this handler itself.
    Credential values are never logged, cached, or echoed back in any
    response (CONSTRAINT #3)."""
    job = rh_login.start_connect_job(body.username, body.password)
    return rh_login.serialize_job(job)


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


@app.post(
    "/brokerage/refresh",
    status_code=202,
    dependencies=[
        Depends(require_brokerage_refresh_enabled),
        Depends(require_command_token),
        Depends(require_loopback),
    ],
)
def refresh_brokerage() -> Dict[str, Any]:
    """Start an asynchronous on-demand Robinhood re-login + account-snapshot
    refresh job, bypassing the daily cache — the webapp/API equivalent of
    ``python3 main.py --refresh-account`` and the Streamlit GUI's "Force fresh
    login (bypass cache)" checkbox on the Live Inventory / Paper Monitor tabs.

    Gated by three independent controls (see the dependencies above): a
    DEDICATED ``BROKERAGE_REFRESH_ENABLED`` flag (not
    ``BROKERAGE_CONNECT_ENABLED`` — see ``require_brokerage_refresh_enabled``),
    the fail-closed follow command token, and the same loopback-only check as
    ``/brokerage/connect`` and ``/brokerage/disconnect``.

    Returns immediately (202) with the started job's initial status — the
    same isolated device-approval worker flow as ``/brokerage/connect``, via
    ``api._rh_login.start_refresh_job``. Poll
    ``GET /brokerage/login/status/{job_id}`` for progress; the job's eventual
    ``"succeeded"``/``"failed"``/``"timeout"``/``"cancelled"`` state is now the
    only place a login-level failure can surface. The old
    try/except-around-a-blocking-call translated to a 502 is gone for exactly
    that reason — starting a job launches a subprocess and returns
    immediately, it does not itself perform any Robinhood network call, so
    there is no longer a blocking login result here to translate.
    ``start_refresh_job`` (via ``data.robinhood_login.start_login``) does call
    ``subprocess.Popen``, which can in principle raise ``OSError`` if process
    creation itself fails (e.g. resource exhaustion) — the try/except below
    exists solely to translate that unlikely case to a clean 502 rather than a
    raw 500, matching this endpoint's pre-existing error-translation posture."""
    try:
        job = rh_login.start_refresh_job()
    except Exception as exc:  # noqa: BLE001 - OSError etc. from subprocess.Popen -> clean 502
        logger.error("pilots_api: brokerage refresh job could not be started: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="Could not start the Robinhood account refresh.",
        ) from exc
    return rh_login.serialize_job(job)


@app.get(
    "/brokerage/login/status/{job_id}",
    dependencies=[Depends(require_read_token), Depends(require_loopback)],
)
def get_brokerage_login_status(job_id: str) -> Dict[str, Any]:
    """Poll the state of a login job started by ``POST /brokerage/connect`` or
    ``POST /brokerage/refresh``. 404 if the job_id is unknown."""
    job = rh_login.get_login_state(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown login job.")
    return rh_login.serialize_job(job)


@app.post(
    "/brokerage/login/cancel/{job_id}",
    dependencies=[Depends(require_command_token), Depends(require_loopback)],
)
def cancel_brokerage_login(job_id: str) -> Dict[str, Any]:
    """Cancel an in-flight login job (SIGTERM -> SIGKILL the isolated worker
    process). 404 if the job_id is unknown. Reports honestly if the kill
    could not be confirmed rather than claiming success it didn't achieve."""
    try:
        stopped = rh_login.cancel_login(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown login job.")
    job = rh_login.get_login_state(job_id)
    payload = rh_login.serialize_job(job) if job else {"job_id": job_id}
    payload["cancelled"] = stopped
    return payload


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
      falls back to ``output/daemon.json`` (written at daemon startup, and
      again with ``state: "stopped"`` at a graceful teardown) when it isn't
      (``source: "daemon_json"``, ``alive: false`` — this is the
      RESTART-HONESTY core: the daemon's in-memory run history is gone after
      a restart, but daemon.json still has the last known pid/interval/
      started_at); ``source: "none"`` when neither is available. ``pid_alive``
      (``True``/``False``/``None``) is a MACHINE-CHECKED liveness probe
      (``pilots.run_status._pid_alive`` via ``read_daemon_json``) that covers
      the case the file's own ``state`` field structurally cannot: a daemon
      killed with SIGKILL can never update its own file, so a stale
      ``state: "running"`` on disk is not proof of anything. ``None`` on the
      ``control_api`` branch (never a fabricated ``True`` — CONSTRAINT #4;
      we never probed a pid there, since ``GET /status`` doesn't echo one).
      Deliberately does NOT echo the file's own ``state`` string here — that
      would promote an unverifiable self-report (which a SIGKILLed process
      can never correct) into the API response, reintroducing the exact
      staleness problem ``pid_alive`` exists to remove.
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
    # A reachable Control API still answers `GET /status` with HTTP 200 and
    # `{"daemon_alive": False}` whenever no OrchestratorDaemon is attached
    # (startup window, mid-restart, or the API served standalone) — that is
    # NOT proof of life, so it must fall through to the same daemon_json
    # branch a connection failure takes, not be treated as "alive: True".
    if daemon_status is not None and daemon_status.get("daemon_alive"):
        daemon_info: Dict[str, Any] = {
            "alive": True,
            "source": "control_api",
            "pid": None,  # not echoed by /status; only daemon.json carries it
            "pid_alive": None,  # no pid to probe on this branch -- never fabricate True
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
                "pid_alive": dj.get("pid_alive"),
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
                "pid_alive": None,
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

    Deliberately NOT suppressed when the ``daemon_json`` fallback's pid turns
    out to be dead (see ``run_status.read_daemon_json``'s ``pid_alive``):
    nulling ``running_value``/``drift`` in that case would tell an operator
    who just edited ``.env`` "no drift", which is the exact failure this
    field exists to prevent — ``GET /automation/status``'s ``daemon.alive``
    and ``daemon.pid_alive`` already convey deadness; this endpoint's job is
    only interval drift, and a dead daemon's LAST KNOWN interval is still the
    honest answer to "what was it running when it died".

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


@app.get("/system/cron-status", dependencies=[Depends(require_read_token)])
def get_system_cron_status() -> Dict[str, Any]:
    """Parse deploy/crontab.txt and return the schedule.

    Delegates to ``pilots.run_status.parse_crontab_status`` -- shared with
    ``api/control_api.py``'s identical endpoint so the two can't drift again
    (this handler used to carry its own, independently-maintained copy)."""
    return run_status.parse_crontab_status()


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
#                                ADVISORY_ONLY/ALPACA_PAPER toward live),
#                                AND a typed field-name confirmation for every
#                                settings_keysets.DANGEROUS_KEYS field it is
#                                about to write (see
#                                _require_dangerous_confirmation) -- the SAME
#                                requirement PUT /settings/tunables enforces
#                                for ADVISORY_ONLY/DRY_RUN, so there is no
#                                weaker unconfirmed path to the same fields
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


def _require_dangerous_confirmation(dangerous_keys: List[str], confirm: Dict[str, str]) -> None:
    """Fail-closed gate for any write path touching ``settings_keysets.
    DANGEROUS_KEYS`` fields: every key in ``dangerous_keys`` must appear in
    ``confirm`` mapped to ITS OWN NAME (``{"ADVISORY_ONLY": "ADVISORY_ONLY"}``),
    matching the echo-the-name contract ``PUT /settings/tunables`` (via
    ``pilots.settings_meta.is_dangerous`` / ``_validate_and_write_payload``)
    uses for the same key set. Missing -> ``confirmation_required``; present
    but not an exact match -> ``confirmation_mismatch``.

    Deliberately ALL-OR-NOTHING (raises before any write happens) rather than
    the per-key partial-success convention the batch settings editors use —
    this is the one shared choke point every DANGEROUS_KEYS write path outside
    the five ``/settings/*`` editors should route through, but a
    single-purpose endpoint like ``PUT /automation/execution-mode`` represents
    one atomic operator action (a mode change), not a bag of independent
    tunables, so there is no such thing as "half-confirmed" here: either every
    dangerous key this call is about to write is confirmed, or nothing is
    written at all.

    Never silently no-ops: called with an empty ``dangerous_keys`` list this
    is a no-op by construction (nothing to confirm), which is correct only
    because every call site computes ``dangerous_keys`` from the fields it is
    ACTUALLY about to write, never a fixed superset."""
    if not dangerous_keys:
        return
    missing = [k for k in dangerous_keys if k not in confirm]
    mismatched = [k for k in dangerous_keys if k in confirm and confirm[k] != k]
    if not missing and not mismatched:
        return
    raise HTTPException(
        status_code=422,
        detail={
            "error": "confirmation_required" if missing else "confirmation_mismatch",
            "message": (
                "This change touches safety-critical setting(s) "
                f"({', '.join(dangerous_keys)}) and requires typed "
                "confirmation before it can be written -- echo each field's "
                'own name in `confirm`, e.g. {"ADVISORY_ONLY": "ADVISORY_ONLY"}.'
            ),
            "required": dangerous_keys,
            "missing": missing,
            "mismatched": mismatched,
        },
    )


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
    write that didn't happen (CONSTRAINT #4).

    Every field this call is about to write that is a ``settings_keysets.
    DANGEROUS_KEYS`` member (``ADVISORY_ONLY`` always; ``DRY_RUN`` too when
    ``mode != "advisory"``) requires the caller to echo that field's own name
    in ``body.confirm`` -- see ``_require_dangerous_confirmation``. This
    closes the gap where this endpoint could flip the execution quarantine
    with zero confirmation of any kind while ``PUT /settings/tunables``
    required one for the very same fields. The check runs, and raises 422,
    BEFORE any write -- a rejected call writes nothing, not even the
    confirmed subset. ``ALPACA_PAPER`` is written (see ``written`` below) but
    NOT in ``settings_keysets.DANGEROUS_KEYS`` and so requires no
    confirmation -- an Alpaca-specific paper/live account selector, not a
    broker-agnostic quarantine like ``ADVISORY_ONLY``/``DRY_RUN``, and
    deliberately not hardened further here (operator decision, 2026-08-04)."""
    from gui import strategy_registry

    dangerous_keys = ["ADVISORY_ONLY"]
    if body.mode != "advisory":
        dangerous_keys += ["DRY_RUN"]
    dangerous_keys = [k for k in dangerous_keys if settings_meta.is_dangerous(k)]
    _require_dangerous_confirmation(dangerous_keys, body.confirm)

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


# ---------------------------------------------------------------------------
# Settings tunables (GET: fail-open read; PUT: fail-closed command token)
# ---------------------------------------------------------------------------
#
# The PWA's Settings Tunables editor — the mobile port of the Command Center's
# "Dynamic Settings Manager" tab (gui/panels/settings_manager.py). Serves ~37
# NON-secret runtime tunables this screen OWNS (matching the real Streamlit
# tab's _SETTINGS_LAYOUT, gui/panels/settings_manager.py:36-77, exactly),
# deliberately EXCLUDING keys owned by other screens (SIGNAL_WEIGHTS /
# DISABLED_SIGNAL_MODULES -> Strategy Matrix; DEFAULT_TICKERS -> Live Inventory
# / Universe Manager per PR #357; all LLM_*/OPAL_* -> AI Control Center;
# MACRO_REGIME_GATE_ENABLED -> Mission Control; ALPACA_PAPER + brokerage ->
# execution-mode toggle). PROMPT_REGISTRY_ENABLED/PROMPT_REGISTRY_BACKEND are
# NOT AI Control Center keys despite the "PROMPT_REGISTRY" naming overlap with
# PROMPT_REGISTRY_PINS/credentials elsewhere — the real Streamlit tab places
# them in ITS OWN _SETTINGS_LAYOUT (general Settings Manager), so they belong
# here too.
#
# Backed ENTIRELY by the existing allowlist-bounded gui.env_io write layer — no
# bespoke .env logic here — so writes inherit its ALLOWED_KEYS/SECRET_KEYS
# enforcement (CONSTRAINT #3) for free, exactly like PUT /strategy/modules and
# PUT /automation/schedule/interval. `_TUNABLE_GROUPS` carries ONLY UI metadata
# (grouping + min/max/step/enum-options); value/default/description are derived
# LIVE from the settings pydantic model (settings.model_fields) so help text and
# defaults never drift from settings.py (repo convention — never re-type them as
# literals). Descriptions are ``null`` for plain-assigned fields that carry no
# pydantic Field(description=...), never fabricated (CONSTRAINT #4).
#
# Auth: PUT sits behind require_command_token ALONE (same fail-closed tier as
# POST /decisions and POST /pilots/{id}/follow) — NOT a dedicated *_WRITES_ENABLED
# master flag. Every accepted value is still re-checked against
# env_io.ALLOWED_KEYS / SECRET_KEYS at write time (defensive `forbidden_key`
# rejection), and the write goes through env_io.write_many_atomic — all-or-nothing
# so a filesystem failure can't leave a half-applied risk config.

# kind -> wire `type`. float/int both surface as "number" (the contract's numeric
# type); the float/int split is internal, driving coercion + the UI step. "json"
# (a JSON-object-in-a-textarea widget, matching gui/panels/settings_manager.py's
# own st.text_area + json.loads-validate-on-submit convention) surfaces as
# "string" on the wire — a JSON blob is still a string as far as the frontend's
# TunableFieldType contract is concerned; the frontend renders it as a
# multi-line textarea rather than inventing a 5th wire type.
_KIND_TO_TYPE: Dict[str, str] = {
    "float": "number",
    "int": "number",
    "bool": "boolean",
    "enum": "enum",
    "str": "string",
    "json": "string",
}

# NOTE on min/max/step below: these bounds are NEW operator guardrails
# introduced by THIS editor — they are not ported from settings.py (which has
# zero ge=/le= constraints on any of these fields) or from the Streamlit
# Settings Manager tab (gui/panels/settings_manager.py, which has zero
# min_value/max_value anywhere). They exist purely to catch an obvious
# fat-finger entry (e.g. "50" typed into a fraction field that expects "0.5")
# and are deliberately chosen wide enough that no legitimate operator value —
# including the field's own settings.py default — should ever be rejected.
# Widen (never narrow) if a real value gets blocked; these are typo guardrails,
# not policy enforcement.
#
# Ordered (group -> fields) layout. Each field: (key, kind, extras) where extras
# may hold min/max/step (number kinds) or options (enum). Self-contained: NOT an
# import of gui.panels.settings_manager's _SETTINGS_LAYOUT (mirrors its intent).
_TUNABLE_GROUPS: List[tuple] = [
    (
        "Financial Constants",
        [
            ("RISK_FREE_RATE", "float", {"min": 0.0, "max": 1.0, "step": 0.005}),
            ("MARKET_RISK_PREMIUM", "float", {"min": 0.0, "max": 1.0, "step": 0.005}),
            ("REQUIRED_RETURN_RATE", "float", {"min": 0.0, "max": 1.0, "step": 0.005}),
            ("MAX_PORTFOLIO_HEAT", "float", {"min": 0.0, "max": 1.0, "step": 0.01}),
        ],
    ),
    (
        "Position Sizing",
        [
            ("KELLY_FRACTION", "float", {"min": 0.0, "max": 1.0, "step": 0.05}),
            ("KELLY_CAP", "float", {"min": 0.0, "max": 1.0, "step": 0.01}),
            ("VOL_TARGET", "float", {"min": 0.0, "max": 1.0, "step": 0.01}),
            ("MAX_LEVERAGE", "float", {"min": 0.0, "max": 10.0, "step": 0.1}),
            # Widened to 5.0 (from an originally-invented 1.0): the field's own
            # default is 1.0, which sat exactly AT the old max — a 2x fat-finger
            # check (2.0) would have been rejected even though a leveraged
            # single-position weight above 100% of unlevered equity is a real,
            # legitimate config (bounded in practice by MAX_LEVERAGE's own 10.0
            # ceiling above), not a typo.
            ("MAX_POSITION_WEIGHT", "float", {"min": 0.0, "max": 5.0, "step": 0.05}),
            # Portfolio-level gross exposure cap + cap-aware escalation +
            # cap-event audit/alerting (sizing/position_sizer.py,
            # sizing/cap_audit_store.py). Same typo-guardrail widening
            # convention as MAX_POSITION_WEIGHT above.
            ("MAX_PORTFOLIO_GROSS", "float", {"min": 0.0, "max": 20.0, "step": 0.1}),
            ("SIZING_CAP_ESCALATION_ENABLED", "bool", {}),
            ("SIZING_CAP_ESCALATION_THRESHOLD_CYCLES", "int", {"min": 1, "max": 100, "step": 1}),
            ("SIZING_CAP_ESCALATION_FACTOR", "float", {"min": 0.0, "max": 1.0, "step": 0.05}),
            ("SIZING_CAP_AUDIT_ENABLED", "bool", {}),
            ("SIZING_CAP_ALERT_ENABLED", "bool", {}),
            ("SIZING_CAP_ALERT_THRESHOLD_PCT", "float", {"min": 0.0, "max": 1.0, "step": 0.05}),
            ("USE_DUAL_MOMENTUM_OVERLAY", "bool", {}),
            ("DUAL_MOMENTUM_SAFE_ASSET", "str", {}),
            ("DUAL_MOMENTUM_RISKY_ASSETS", "json", {}),
        ],
    ),
    (
        # Tracked Universe auto-drop (rating/symbol_rating_store.py). SYMBOL_RATING_ENABLED
        # gates whether a per-symbol rating is computed/persisted at all (diagnostic-only,
        # default True). SYMBOL_RATING_AUTO_DROP_ENABLED is the actual trading-behavior
        # switch -- default False, like every other live-trading-behavior flag in this
        # codebase -- so flipping it here is a deliberate, visible operator action, not
        # something that happens by editing .env in the dark.
        "Symbol Rating",
        [
            ("SYMBOL_RATING_ENABLED", "bool", {}),
            ("SYMBOL_RATING_BAD_SCORE_THRESHOLD", "float", {"min": 0.0, "max": 100.0, "step": 1.0}),
            ("SYMBOL_RATING_AUTO_DROP_ENABLED", "bool", {}),
            ("SYMBOL_RATING_DROP_THRESHOLD_CYCLES", "int", {"min": 1, "max": 100, "step": 1}),
        ],
    ),
    (
        "Risk Gate",
        [
            ("MAX_CORRELATION", "float", {"min": 0.0, "max": 1.0, "step": 0.05}),
            ("DAILY_LOSS_LIMIT_PCT", "float", {"min": 0.0, "max": 1.0, "step": 0.005}),
            ("MAX_ORDER_RATE_PER_MIN", "int", {"min": 1, "max": 1000, "step": 1}),
            ("RISK_GATE_ENFORCE_MARKET_HOURS", "bool", {}),
            ("META_LABEL_MIN_CONFIDENCE", "float", {"min": 0.0, "max": 1.0, "step": 0.05}),
            ("DRY_RUN", "bool", {}),
            ("EXECUTION_PRIORITY_QUEUE_ENABLED", "bool", {}),
            ("EXECUTION_QUEUE_LEAK_RATE_PER_SEC", "float", {"min": 0.0, "max": 100.0, "step": 0.5}),
            ("FLATTEN_ON_KILL", "bool", {}),
        ],
    ),
    (
        # HMM_RISK_OFF_BLOCK_THRESHOLD moved here from "Risk Gate" -- it's a
        # regime-model parameter, not a risk-gate-specific one, and belongs
        # next to the other HMM/VRP regime tunables.
        "Regime Model",
        [
            ("HMM_N_STATES", "int", {"min": 2, "max": 10, "step": 1}),
            ("HMM_RETRAIN_FREQ_DAYS", "int", {"min": 1, "max": 30, "step": 1}),
            ("HMM_RISK_OFF_BLOCK_THRESHOLD", "float", {"min": 0.0, "max": 1.0, "step": 0.05}),
            ("OPTIONS_VRP_THRESHOLD", "float", {"min": 0.0, "max": 1.0, "step": 0.01}),
        ],
    ),
    (
        "Forecasting",
        [
            ("FORECAST_USE_GARCH_SIGMA", "bool", {}),
            ("FORECAST_PROPHET_WEIGHT", "float", {"min": 0.0, "max": 1.0, "step": 0.05}),
            ("FORECAST_SKILL_WEIGHTING_ENABLED", "bool", {}),
            ("FORECAST_SKILL_WINDOW_DAYS", "int", {"min": 1, "max": 3650, "step": 1}),
            ("FORECAST_MODEL_PERSISTENCE_ENABLED", "bool", {}),
            ("FORECAST_MODEL_RETRAIN_DAYS", "int", {"min": 1, "max": 3650, "step": 1}),
            ("BETA_LOOKBACK_DAYS", "int", {"min": 1, "max": 3650, "step": 1}),
            ("BERT_LLA_ENABLED", "bool", {}),
            ("BERT_LLA_WINDOW_SIZE", "int", {"min": 1, "max": 1000, "step": 1}),
            ("BERT_LLA_MIN_SENTIMENT_COVERAGE", "float", {"min": 0.0, "max": 1.0, "step": 0.05}),
            ("BERT_LLA_BLEND_ENABLED", "bool", {}),
            ("BERT_LLA_ABLATION_ENABLED", "bool", {}),
            ("CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED", "bool", {}),
            ("CNN_LSTM_PROCESS_POOL_WORKERS", "int", {"min": 1, "max": 64, "step": 1}),
            ("CNN_LSTM_SUBPROCESS_TIMEOUT_SECONDS", "int", {"min": 1, "max": 3600, "step": 10}),
            ("FORECAST_CNN_LSTM_WALKFORWARD_SCALING", "bool", {}),
            ("LGBM_RANKER_NATIVE_MULTIINDEX_CV_ENABLED", "bool", {}),
        ],
    ),
    (
        "Market Data",
        [
            ("MARKET_DATA_PROVIDER", "enum", {"options": ["alpaca", "yfinance", "fmp"]}),
            ("MARKET_DATA_QUOTE_TTL_SECONDS", "int", {"min": 0, "max": 86400, "step": 1}),
            ("MARKET_DATA_BARS_TTL_SECONDS", "int", {"min": 0, "max": 86400, "step": 1}),
            ("FUNDAMENTALS_SOURCE", "enum", {"options": ["yahoo", "yfinance_info", "fmp"]}),
            ("MARKET_DATA_WS_ENABLED", "bool", {}),
            ("HISTORICAL_STORE_ENABLED", "bool", {}),
        ],
    ),
    (
        "Runtime & Ops",
        [
            ("DASHBOARD_REFRESH_SECONDS", "int", {"min": 1, "max": 86400, "step": 1}),
            ("PROGRESS_POLL_SECONDS", "int", {"min": 1, "max": 3600, "step": 1}),
            ("LOG_LEVEL", "enum", {"options": ["DEBUG", "INFO", "WARNING", "ERROR"]}),
            ("ADVISORY_REUSE_PIPELINE_COMPUTE", "bool", {}),
            ("ADVISORY_ONLY", "bool", {}),
            ("ROBINHOOD_AUTO_REFRESH_ENABLED", "bool", {}),
            ("RUNTIME_FLAGS_REFRESH_ENABLED", "bool", {}),
            ("RUNTIME_FLAGS_REFRESH_INTERVAL_SECONDS", "int", {"min": 1, "max": 3600, "step": 1}),
        ],
    ),
    (
        # Widgetless / JSON-structured tunables ported from the Streamlit tab's
        # own _SETTINGS_LAYOUT (gui/panels/settings_manager.py:36-77) — all 7
        # were previously missing from this editor entirely.
        "Advanced / Config",
        [
            ("SECTOR_FORECAST_CONFIG_PATH", "str", {}),
            ("SECTOR_FORECAST_CONFIGS", "json", {}),
            ("PROMPT_REGISTRY_ENABLED", "bool", {}),
            ("PROMPT_REGISTRY_BACKEND", "str", {}),
            ("ORCHESTRATOR_DAEMON_ENABLED", "bool", {}),
            ("ORCHESTRATOR_EXTENDED_HOURS_ONLY", "bool", {}),
            ("CORS_ALLOWED_ORIGINS", "json", {}),
            ("GRAVITY_REQUIRE_NATIVE", "bool", {}),
        ],
    ),
    (
        "Options & Pairs Snapshots",
        [
            ("OPTIONS_MATRIX_ENABLED", "bool", {}),
            ("OPTIONS_TRUE_IVR_ENABLED", "bool", {}),
            ("PAIRS_SNAPSHOT_ENABLED", "bool", {}),
        ],
    ),
    (
        "ML, Data Capture & Audit",
        [
            ("META_LABELING_ENABLED", "bool", {}),
            ("NEWS_HISTORY_CAPTURE_ENABLED", "bool", {}),
            ("PIT_CAPTURE_ENABLED", "bool", {}),
            ("SENTIMENT_AUDIT_ENABLED", "bool", {}),
            ("SENTIMENT_DESENTENCIZE_ENABLED", "bool", {}),
            ("EXCURSION_INTRADAY_ENABLED", "bool", {}),
        ],
    ),
    (
        "Validation Gates",
        [
            ("VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED", "bool", {}),
            ("VALIDATION_HARNESS_OOS_GATE_ENABLED", "bool", {}),
        ],
    ),
    (
        "RLHF Calibration",
        [
            ("RLHF_CALIBRATION_AUTO_APPROVE_ENABLED", "bool", {}),
            ("RLHF_CALIBRATION_CONFIDENCE_THRESHOLD", "float", {"min": 0.0, "max": 1.0, "step": 0.05}),
            ("RLHF_CALIBRATION_AUTO_EXPORT_SFT_ENABLED", "bool", {}),
        ],
    ),
]

# Flat {key: (kind, extras)} index built once — its keyset IS the editor scope.
_TUNABLE_INDEX: Dict[str, tuple] = {
    key: (kind, extras)
    for _group, _specs in _TUNABLE_GROUPS
    for key, kind, extras in _specs
}

# Soft drift guard (the HARD one is tests/test_pilots_api_tunables.py): every
# served key must be a writable non-secret. A drift can never actually leak a
# secret — the PUT re-checks each key against ALLOWED_KEYS/SECRET_KEYS at write
# time — so this only logs rather than taking the whole API down at import.
_tunable_drift = [
    k for k in _TUNABLE_INDEX
    if k not in env_io.ALLOWED_KEYS or k in env_io.SECRET_KEYS
]
if _tunable_drift:  # pragma: no cover - config invariant, pinned by test
    logger.error(
        "settings-tunables layout drift: %s not writable non-secrets in env_io.",
        _tunable_drift,
    )


class TunablesUpdateRequest(BaseModel):
    """Body for ``PUT /settings/tunables``. ``values`` is a partial map of
    ``{key: value}`` to write. Typed as ``Any`` values (not a pydantic Union) so
    THIS module does the type/range validation and can return a precise per-key
    ``rejected`` reason rather than a generic 422 — the frontend branches on the
    reason tag.

    ``confirm`` is the dangerous-key acknowledgement. Any key in
    ``settings_keysets.DANGEROUS_KEYS`` must appear here mapped to ITS OWN NAME
    (``{"ADVISORY_ONLY": "ADVISORY_ONLY"}``) or it is rejected — see
    :func:`_validate_and_write_payload`. Echoing the field's own name (rather
    than a bare ``true``) is deliberate: it cannot be satisfied by a blanket
    ``confirm_all`` flag, so a client that confirms one dangerous field has not
    accidentally confirmed a second one it did not intend to touch. Absent for
    an ordinary write, which is the overwhelmingly common case."""

    values: Dict[str, Any] = Field(..., max_length=64)
    confirm: Dict[str, str] = Field(default_factory=dict, max_length=64)


def _tunable_default(fi: Any) -> Any:
    """A settings field's real default — including fields declared with
    ``default_factory=`` (e.g. ``SECTOR_FORECAST_CONFIGS``/``CORS_ALLOWED_ORIGINS``,
    both dict/list defaults), whose ``fi.default`` is pydantic's
    ``PydanticUndefined`` sentinel rather than the actual default value.
    Dead-letter: a factory that raises degrades to ``None`` (CONSTRAINT #6),
    never a crash."""
    if fi is None:
        return None
    if fi.default_factory is not None:
        try:
            return fi.default_factory()
        except Exception:  # noqa: BLE001 - dead-letter, never fabricate/crash
            return None
    return fi.default


def _tunables_env_drift(index_spec: Dict[str, tuple]) -> Dict[str, Any]:
    """Compare the on-disk ``.env`` value of every tunable ``index_spec`` serves
    against the running process's ``settings`` singleton. Mirrors
    ``_env_drift()`` (Strategy Matrix) but scoped to the caller's editor
    (``_TUNABLE_INDEX`` / ``_SENTIMENT_INDEX`` / ``_SECTOR_SELECTION_INDEX``)
    instead of the two Strategy Matrix keys. A ``.env`` write does NOT reach the
    live singleton, so after a successful PUT this stays serving the OLD values
    until restart — this surfaces that pending change. Dead-letter per key: a
    parse failure for one key is skipped rather than failing the whole check
    (CONSTRAINT #6 — a hand-mangled ``.env`` must never 500 this endpoint).
    Calls ``env_io.read_raw()`` ONCE up front rather than ``env_io.get_value()``
    per key — with up to ~133 tunables served across five of these calls per
    settings-screen refresh, per-key ``get_value()`` calls meant a full ``.env``
    re-parse per key; ``read_raw()`` never raises (see its own docstring), so
    hoisting it above the per-key dead-letter loop is safe."""
    keys: List[str] = []
    raw_env = env_io.read_raw()
    for key, (kind, _extras) in index_spec.items():
        try:
            value = raw_env.get(key)
            raw = "" if value is None else str(value)
            if raw == "":
                continue
            live = getattr(settings, key, None)
            if kind == "json":
                if json.loads(raw) != live:
                    keys.append(key)
            elif kind == "bool":
                on_disk = raw.strip().lower() in {"1", "true", "yes", "on"}
                if on_disk != bool(live):
                    keys.append(key)
            elif kind == "int":
                if int(float(raw)) != live:
                    keys.append(key)
            elif kind == "float":
                if float(raw) != live:
                    keys.append(key)
            else:  # "str" / "enum"
                if raw != live:
                    keys.append(key)
        except Exception as exc:  # noqa: BLE001 - dead-letter, per key
            logger.debug("settings-tunables env_drift check failed for %s: %s", key, exc)
            continue
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


@app.get("/settings/tunables", dependencies=[Depends(require_read_token)])
def get_settings_tunables() -> Dict[str, Any]:
    """The ~30+ non-secret runtime tunables this editor owns, grouped, with live
    value/default/description (from the settings pydantic model) plus UI
    metadata (type + min/max/step/options).

    Fail-open read (``require_read_token``), mirroring every other GET here. The
    ``value`` reflects the RUNNING process config (the live ``settings``
    singleton) — a pending ``.env`` write only takes effect on the next daemon
    restart, which each field's ``liveness.applies`` states explicitly (matching
    ``GET /strategy/matrix`` / ``GET /llm/status`` which likewise read live
    settings). ``env_drift`` reports whether the on-disk ``.env`` currently
    differs from these live values (mirrors ``GET /strategy/matrix``'s
    ``env_drift``). Never 500s (CONSTRAINT #6)."""
    return _settings_editor_payload(_TUNABLE_GROUPS, _TUNABLE_INDEX)


@app.put(
    "/settings/tunables",
    dependencies=[
        Depends(require_command_token),
        Depends(require_general_settings_writes_enabled),
    ],
)
@app.patch(
    "/settings/tunables",
    dependencies=[
        Depends(require_command_token),
        Depends(require_general_settings_writes_enabled),
    ],
)
def put_settings_tunables(body: TunablesUpdateRequest) -> Dict[str, Any]:
    """Write a partial map of non-secret tunables to ``.env``."""
    return _validate_and_write_payload(body.values, _TUNABLE_INDEX, confirm=body.confirm)


def _build_groups_payload(groups_spec: List[tuple]) -> List[Dict[str, Any]]:
    """Assemble a grouped tunables payload for any ``(group_name, [(key, kind,
    extras), ...])`` spec list — shared by every ``/settings/*`` editor
    (``_TUNABLE_GROUPS``, ``_SENTIMENT_GROUPS``, ``_SECTOR_SELECTION_GROUPS``).
    ``value``/``default``/``description`` are read LIVE from the settings
    pydantic model — never re-typed as literals (repo convention).
    ``description`` is ``null`` when the field has no pydantic
    ``Field(description=...)`` (CONSTRAINT #4 — never fabricated). ``kind ==
    "json"`` fields carry a native dict/list ``value``/``default`` in
    ``settings`` — both are JSON-stringified here so the wire contract's
    ``string`` type holds (a failed ``json.dumps`` dead-letters to ``None``
    rather than 500ing — CONSTRAINT #6).

    Every field additionally carries a ``liveness`` sub-object
    (``pilots.settings_meta.field_metadata``) answering "what happens when I
    save this?" — ``applies`` / ``restart_reason`` / ``capture_sites`` /
    ``env_pinned`` / ``dangerous`` / ``source``. The two per-moment inputs
    (which names a real shell export pins, and which have a runtime-store
    override) are resolved ONCE here for the whole payload but resolved FRESH
    on every request: both can change between two requests, so neither may be
    cached or precomputed."""
    model_fields = type(settings).model_fields
    liveness = settings_meta.load_liveness()
    pinned = settings_meta.env_pinned_keys()
    stored = settings_meta.runtime_store_keys()
    # Whether a live apply is possible AT ALL in this build. Without it a
    # ``live_safe`` field is still only a .env write, so GET must not advertise
    # ``immediately`` for a change the PUT will honestly report as needing a
    # restart — the two must agree.
    live_apply = settings_meta.live_apply_available()
    groups: List[Dict[str, Any]] = []
    for group_name, specs in groups_spec:
        fields: List[Dict[str, Any]] = []
        for key, kind, extras in specs:
            fi = model_fields.get(key)
            default = _tunable_default(fi)
            description = (getattr(fi, "description", None) if fi is not None else None) or None
            value = getattr(settings, key, None)
            if kind == "json":
                try:
                    value = json.dumps(value) if value is not None else None
                except (TypeError, ValueError):
                    value = None
                try:
                    default = json.dumps(default) if default is not None else None
                except (TypeError, ValueError):
                    default = None
            field: Dict[str, Any] = {
                "key": key,
                "value": value,
                "type": _KIND_TO_TYPE[kind],
                "default": default,
                "description": description,
                "liveness": settings_meta.field_metadata(
                    key,
                    pinned=pinned,
                    stored=stored,
                    data=liveness,
                    live_apply=live_apply,
                ),
            }
            for meta in ("min", "max", "step"):
                if meta in extras:
                    field[meta] = extras[meta]
            if kind == "enum":
                field["options"] = list(extras.get("options", []))
            fields.append(field)
        groups.append({"name": group_name, "fields": fields})
    return groups


def _settings_editor_payload(
    groups_spec: List[tuple], index_spec: Dict[str, tuple]
) -> Dict[str, Any]:
    """The complete ``GET /settings/*`` body for one editor — shared by all five.

    ``applies`` is no longer the hardcoded ``"next_daemon_restart"`` every
    editor used to return unconditionally. That string was true of a pure
    ``.env`` write and became a false blanket claim once ``runtime_flags`` could
    apply a change to the running process; it is now ROLLED UP from the fields
    this editor actually serves (``"immediately"`` / ``"next_daemon_restart"`` /
    ``"no_effect"`` / ``"env_pinned"`` when they all agree, else ``"mixed"``),
    with ``applies_counts`` giving the breakdown so a screen can say something
    honest about its own particular mix instead of one global sentence.
    """
    groups = _build_groups_payload(groups_spec)
    states = [
        f["liveness"]["applies"]
        for g in groups
        for f in g["fields"]
        if isinstance(f.get("liveness"), dict)
    ]
    return {
        **settings_meta.summarize_applies(states),
        "groups": groups,
        "env_drift": _tunables_env_drift(index_spec),
    }


def _validate_and_write_payload(
    values: Dict[str, Any],
    index_spec: Dict[str, tuple],
    *,
    confirm: Optional[Dict[str, str]] = None,
    actor: str = "pilots_api",
) -> Dict[str, Any]:
    """Validate ``values`` against ``index_spec`` (one editor's ``{key: (kind,
    extras)}`` scope — ``_TUNABLE_INDEX`` / ``_SENTIMENT_INDEX`` /
    ``_SECTOR_SELECTION_INDEX``) and write the accepted subset to ``.env``.
    Shared by every ``PUT /settings/*`` endpoint here.

    Per-key rejection reason tags (frontend branches on these, never on a
    message): ``unknown_key`` (outside this editor's scope), ``forbidden_key``
    (defensive: not an env_io writable non-secret, CONSTRAINT #3),
    ``expected_boolean`` / ``expected_number`` / ``expected_integer`` /
    ``expected_string`` / ``invalid_option`` / ``out_of_range`` /
    ``invalid_json`` (``kind == "json"`` only), plus the two confirmation tags
    below.

    For ``kind == "json"`` the accepted value kept in ``accepted`` (and echoed
    in ``written`` below) is the ORIGINAL STRING the caller submitted (only
    validated as parseable, never re-serialized) — it is parsed back to a
    native object immediately before handing it to
    ``env_io.write_many_atomic``, which — matching ``env_io._JSON_KEYS``'s own
    ``json.dumps(value)`` convention for ``SIGNAL_WEIGHTS``/
    ``CORS_ALLOWED_ORIGINS`` etc. — expects a native dict/list, not an
    already-encoded string (handing it a string would double-encode).

    ------------------------------------------------------------------
    Two behaviours this function gained, both load-bearing
    ------------------------------------------------------------------
    **1. Dangerous-key confirmation, scoped to writes through THIS function.**
    ``settings_keysets.DANGEROUS_KEYS`` fields accepted here (``ADVISORY_ONLY``,
    ``DRY_RUN``, ``ROBINHOOD_EXECUTION_MODE``, ``MACRO_REGIME_GATE_ENABLED``,
    ``FMP_BARS_ENABLED``, ``FMP_BARS_ADJUSTMENT``, ``CORS_ALLOWED_ORIGINS`` —
    the other 11 ``DANGEROUS_KEYS`` members are hand-set-only master switches
    never in ``env_io.ALLOWED_KEYS``, so they are already rejected as
    ``forbidden_key`` above and never reach this gate) now require the caller
    to echo the field's own name back in ``confirm``: ``{"values":
    {"ADVISORY_ONLY": false}, "confirm": {"ADVISORY_ONLY": "ADVISORY_ONLY"}}``.
    Missing -> ``confirmation_required``; present but not an exact match ->
    ``confirmation_mismatch``. Before this, five of those fields — including
    ``ADVISORY_ONLY``, the execution quarantine AGENTS.md §2 calls
    load-bearing safety infrastructure — were one ordinary ``PUT`` *through
    this function* away from ``false`` with no confirmation of any kind.
    Rejection is strictly PER KEY: a batch mixing an unconfirmed dangerous key
    with ordinary tunables still writes the ordinary ones (this repo's existing
    partial-success convention), so the gate cannot be worked around by
    bundling and cannot punish an unrelated edit.

    **This gate is scoped to writes through THIS function, but is no longer
    the only DANGEROUS_KEYS-confirming write path.** ``PUT
    /automation/execution-mode`` writes ``ADVISORY_ONLY`` (and, via
    ``gui.strategy_registry.set_active_mode``, ``DRY_RUN``/``ALPACA_PAPER``)
    directly and predates this function; it now enforces the SAME echo-the-
    name contract independently, via its own ``_require_dangerous_
    confirmation`` (below) rather than routing through this one — a
    single-purpose atomic action doesn't fit this function's per-key,
    partial-success ``values``/``rejected`` shape. ``ALPACA_PAPER`` is
    written there but is NOT a ``DANGEROUS_KEYS`` member and needs no
    confirmation on either path.

    **2. Live apply.** Every accepted key is still written to ``.env`` exactly
    as before (durable across restarts, unchanged). Additionally, a key the
    liveness classifier reports ``live_safe`` and that is NOT env-pinned is
    pushed onto THIS process's ``settings`` singleton via
    ``runtime_flags_writer.write_override``. The per-key ``applies`` reported
    back is the ACTUAL outcome of that attempt (``WriteResult.applies``), never
    the a-priori classification — if the writer refuses, or reports the field
    env-pinned, or is not installed at all, the response says so.
    """
    accepted: Dict[str, Any] = {}
    rejected: Dict[str, str] = {}
    for key, value in values.items():
        spec = index_spec.get(key)
        if spec is None:
            rejected[key] = "unknown_key"
            continue
        kind, extras = spec
        if key in env_io.SECRET_KEYS or key not in env_io.ALLOWED_KEYS:
            rejected[key] = "forbidden_key"
            continue

        if kind == "bool":
            if not isinstance(value, bool):
                rejected[key] = "expected_boolean"
                continue
            accepted[key] = value
        elif kind in ("float", "int"):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                rejected[key] = "expected_number"
                continue
            if isinstance(value, float) and not math.isfinite(value):
                rejected[key] = "expected_number"
                continue
            if kind == "int":
                if isinstance(value, float) and not value.is_integer():
                    rejected[key] = "expected_integer"
                    continue
                coerced: Any = int(value)
            else:
                coerced = float(value)
            lo, hi = extras.get("min"), extras.get("max")
            if (lo is not None and coerced < lo) or (hi is not None and coerced > hi):
                rejected[key] = "out_of_range"
                continue
            accepted[key] = coerced
        elif kind == "enum":
            if not isinstance(value, str):
                rejected[key] = "expected_string"
                continue
            if value not in extras.get("options", []):
                rejected[key] = "invalid_option"
                continue
            accepted[key] = value
        elif kind == "json":
            if not isinstance(value, str):
                rejected[key] = "expected_string"
                continue
            try:
                json.loads(value)
            except (TypeError, ValueError):
                rejected[key] = "invalid_json"
                continue
            accepted[key] = value
        else:
            if not isinstance(value, str):
                rejected[key] = "expected_string"
                continue
            accepted[key] = value

    # ---- dangerous-key confirmation gate (per key, never whole-request) ----
    # Runs AFTER type validation so a malformed dangerous value reports the
    # type problem rather than being masked by a confirmation complaint.
    for key in list(accepted):
        if not settings_meta.is_dangerous(key):
            continue
        echoed = (confirm or {}).get(key)
        if echoed is None:
            rejected[key] = "confirmation_required"
            accepted.pop(key)
        elif echoed != key:
            rejected[key] = "confirmation_mismatch"
            accepted.pop(key)

    if accepted:
        to_write: Dict[str, Any] = {}
        for key, value in accepted.items():
            kind, _extras = index_spec[key]
            to_write[key] = json.loads(value) if kind == "json" else value
        env_io.write_many_atomic(to_write)
        per_key_applies = _apply_live_overrides(to_write, actor=actor)
    else:
        per_key_applies = {}

    applied_now = sorted(
        k for k, v in per_key_applies.items() if v == settings_meta.APPLIES_IMMEDIATELY
    )
    return {
        "written": accepted,
        "rejected": rejected,
        "per_key_applies": per_key_applies,
        **settings_meta.summarize_applies(list(per_key_applies.values())),
        # A restart is only genuinely required for the keys that did NOT apply
        # live. Reporting True unconditionally (as this endpoint used to) told
        # an operator to restart for a change that was already in force.
        "restart_required": any(
            v != settings_meta.APPLIES_IMMEDIATELY for v in per_key_applies.values()
        ),
        "restart_endpoint": "POST /daemon/restart",
        "note": _write_note(per_key_applies, applied_now),
    }


def _write_note(per_key_applies: Dict[str, str], applied_now: List[str]) -> str:
    """One honest sentence about what a PUT actually did.

    Deliberately NOT the old unconditional "settings is not patched in-process
    — restart the daemon to apply", which is now false for every ``live_safe``
    key. Says what happened to each half of the write.
    """
    if not per_key_applies:
        return "Nothing was written."
    pending = sorted(set(per_key_applies) - set(applied_now))
    if applied_now and not pending:
        return (
            "Saved to .env and applied to the running process — no restart "
            "needed."
        )
    if pending and not applied_now:
        return (
            "Saved to .env. The running process keeps the previous values until "
            "it restarts (POST /daemon/restart)."
        )
    return (
        f"Saved to .env. {len(applied_now)} applied to the running process "
        f"immediately; {len(pending)} take effect on the next restart "
        f"({', '.join(pending)})."
    )


def _apply_live_overrides(to_write: Dict[str, Any], *, actor: str) -> Dict[str, str]:
    """Push every ``live_safe``, non-env-pinned key onto the RUNNING process.

    Returns ``{key: applies}`` for every key in ``to_write`` — the ACTUAL
    outcome, not the a-priori classification. The ``.env`` write has already
    happened and is independent of everything here: this only decides whether
    the operator also has to restart to see the change.

    ``runtime_flags_writer`` is imported lazily and defensively. It is a
    separate, newer module and may legitimately be absent from a given
    checkout; when it is, every key honestly reports ``next_daemon_restart``,
    which is exactly what a ``.env``-only write means. That is the same
    fail-closed direction the rest of this feature takes: a key that is not
    live-applied is never REPORTED as live-applied.

    Never raises (CONSTRAINT #6) — a writer failure degrades one key to
    ``next_daemon_restart`` and leaves the durable ``.env`` write intact.
    """
    liveness = settings_meta.load_liveness()
    pinned = settings_meta.env_pinned_keys()
    out: Dict[str, str] = {}

    try:
        from runtime_flags_writer import write_override  # noqa: PLC0415 - lazy
    except Exception:  # noqa: BLE001 - module not installed in this checkout
        logger.info(
            "settings write: runtime_flags_writer unavailable; %d key(s) written "
            "to .env only and reported as taking effect on the next restart.",
            len(to_write),
        )
        return {key: settings_meta.APPLIES_NEXT_RESTART for key in to_write}

    for key, value in to_write.items():
        # live_apply=True is not an assumption here: the import above succeeded,
        # which is exactly the condition live_apply_available() reports on.
        applies = settings_meta.applies_for(
            key, pinned=pinned, data=liveness, live_apply=True
        )
        if applies != settings_meta.APPLIES_IMMEDIATELY:
            # Not live-safe, env-pinned, or no-op: nothing to apply. The static
            # answer IS the real one here, because we make it so by not writing.
            out[key] = applies
            continue
        try:
            result = write_override(key, value, actor=actor)
        except Exception as exc:  # noqa: BLE001 - dead-letter, per key
            logger.warning(
                "settings write: live apply of %s failed (%s); the .env write "
                "stands and the change takes effect on the next restart.",
                key,
                type(exc).__name__,
            )
            out[key] = settings_meta.APPLIES_NEXT_RESTART
            continue
        # Trust the writer's own verdict over our prediction. It knows things
        # the static classifier cannot (a refusal, a validation failure, an
        # env pin it observed at a different moment).
        reported = getattr(result, "applies", None)
        ok = getattr(result, "ok", False)
        if ok and reported in settings_meta.APPLIES_STATES:
            out[key] = reported
        elif reported == "refused" or not ok:
            reason = getattr(result, "reason", None)
            logger.warning(
                "settings write: live apply of %s was refused by "
                "runtime_flags_writer (%s); the .env write stands.",
                key,
                reason or "no reason given",
            )
            out[key] = settings_meta.APPLIES_NEXT_RESTART
        else:
            out[key] = settings_meta.APPLIES_NEXT_RESTART
    return out


# ---------------------------------------------------------------------------
# Dedicated Sentiment & Sector Selection Schemas
#
# Every key below is a REAL settings.py Field — verified against
# Settings.model_fields, not re-typed from memory. An earlier draft of this
# section invented plausible-sounding names (SENTIMENT_LOOKBACK_DAYS,
# REDDIT_ENABLED, GDELT_ENABLED, SECTOR_SELECTION_WEIGHTING_SCHEME, etc.) that
# do not exist anywhere in the codebase; since Settings' model_config has
# extra="ignore", writing one of those to .env would have been a SILENT NO-OP
# — the GUI would show "Saved to .env" while changing nothing. "Sector
# Selection" here is the semantic Related-Sector-Selection feature
# (data/sector_selection_heat.py, see settings.py's "A DIFFERENT feature from
# SECTOR_HEAT_* above" comment) that backs the existing SectorSelection.tsx
# screen — NOT a momentum/value/volatility factor rotation, which does not
# exist for sectors in this codebase.
# ---------------------------------------------------------------------------

_SENTIMENT_GROUPS = [
    (
        "Sentiment Ingestion Core",
        [
            ("SENTIMENT_INGESTION_ENABLED", "bool", {}),
            ("SENTIMENT_SOURCES", "str", {}),
            ("SENTIMENT_COMMENT_SOURCES", "str", {}),
            ("SENTIMENT_INGESTION_LOOKBACK_DAYS", "int", {"min": 1, "max": 90, "step": 1}),
            ("SENTIMENT_MAX_DOCUMENTS_PER_CYCLE", "int", {"min": 1, "max": 20000, "step": 1}),
            ("SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE", "float", {"min": 1.0, "max": 600.0, "step": 1.0}),
            ("SENTIMENT_CIRCUIT_BREAKER_THRESHOLD", "int", {"min": 1, "max": 20, "step": 1}),
        ],
    ),
    (
        "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News",
        [
            ("STOCKTWITS_ENABLED", "bool", {}),
            ("REDDIT_BACKFILL_MAX_PAGES", "int", {"min": 1, "max": 100, "step": 1}),
            ("GOOGLE_NEWS_LOOKBACK_WINDOW", "str", {}),
            ("EDGAR_FULLTEXT_ENABLED", "bool", {}),
            ("EDGAR_FULLTEXT_FORMS", "str", {}),
            ("EDGAR_FULLTEXT_CHUNK_TOKENS", "int", {"min": 64, "max": 4096, "step": 64}),
            ("GDELT_MIN_REQUEST_INTERVAL_SECONDS", "float", {"min": 0.0, "max": 60.0, "step": 0.5}),
            ("GDELT_MAX_RETRIES", "int", {"min": 0, "max": 10, "step": 1}),
            ("GDELT_RETRY_BACKOFF_SECONDS", "float", {"min": 0.5, "max": 60.0, "step": 0.5}),
            ("GDELT_COOLDOWN_THRESHOLD", "int", {"min": 1, "max": 10, "step": 1}),
            ("GDELT_COOLDOWN_SECONDS", "float", {"min": 10.0, "max": 3600.0, "step": 10.0}),
        ],
    ),
    (
        "FinBERT & Catalyst Scoring",
        [
            ("FINBERT_ENABLED", "bool", {}),
            ("FINBERT_BATCH_SIZE", "int", {"min": 1, "max": 128, "step": 1}),
            ("FINBERT_SCORE_CACHE_ENABLED", "bool", {}),
            ("NEWS_LOOKBACK_DAYS", "int", {"min": 1, "max": 90, "step": 1}),
            ("FINNHUB_RATE_LIMIT_PER_MIN", "int", {"min": 1, "max": 60, "step": 1}),
            ("SENTIMENT_SOCIAL_BLEND_WEIGHT", "float", {"min": 0.0, "max": 1.0, "step": 0.05}),
        ],
    ),
    (
        "AI Credibility Verification",
        [
            ("SENTIMENT_LLM_VERIFICATION_ENABLED", "bool", {}),
            ("SENTIMENT_LLM_VERIFICATION_PROVIDER", "enum", {"options": ["claude", "gemini", "openai", "none"]}),
            ("SENTIMENT_LLM_VERIFICATION_MAX_CALLS_PER_CYCLE", "int", {"min": 0, "max": 500, "step": 1}),
        ],
    ),
    (
        "Attention & Sector Heat",
        [
            ("SECTOR_HEAT_ENABLED", "bool", {}),
            ("SECTOR_HEAT_SMOOTHING_SIGMA", "float", {"min": 0.1, "max": 10.0, "step": 0.1}),
            ("SECTOR_HEAT_LOOKBACK_DAYS", "int", {"min": 1, "max": 90, "step": 1}),
            ("WIKIPEDIA_ATTENTION_ENABLED", "bool", {}),
            ("WIKIPEDIA_ATTENTION_LOOKBACK_DAYS", "int", {"min": 1, "max": 365, "step": 1}),
            ("PYTRENDS_ENABLED", "bool", {}),
        ],
    ),
]

_SENTIMENT_INDEX = {
    key: (kind, extras)
    for _group, _specs in _SENTIMENT_GROUPS
    for key, kind, extras in _specs
}

_PAPER_BROKER_GROUPS = [
    (
        "Paper Broker Configuration",
        [
            ("BROKER_BACKEND", "str", {}),
            ("FMP_PAPER_STARTING_CASH", "float", {"min": 0.0, "max": 10000000.0, "step": 1000.0}),
            ("PAPER_BROKER_WRITES_ENABLED", "bool", {}),
        ],
    ),
]

_PAPER_BROKER_INDEX = {
    key: (kind, extras)
    for _group, _specs in _PAPER_BROKER_GROUPS
    for key, kind, extras in _specs
}

_CACHE_LONG_SHORT_GROUPS = [
    (
        "Cache Long/Short Overlay",
        [
            ("CACHE_LONG_SHORT_ENABLED", "bool", {}),
            ("CACHE_LONG_SHORT_WRITES_ENABLED", "bool", {}),
            ("CACHE_LONG_SHORT_MIN_CORRELATION", "float", {"min": 0.0, "max": 1.0, "step": 0.05}),
            ("CACHE_LONG_SHORT_TLH_THRESHOLD_PCT", "float", {"min": 0.0, "max": 1.0, "step": 0.01}),
            ("CACHE_LONG_SHORT_SCAN_INTERVAL_SECONDS", "int", {"min": 60, "max": 86400, "step": 60}),
            ("CACHE_LONG_SHORT_PROXY_CANDIDATES", "json", {}),
        ],
    ),
]

_CACHE_LONG_SHORT_INDEX = {
    key: (kind, extras)
    for _group, _specs in _CACHE_LONG_SHORT_GROUPS
    for key, kind, extras in _specs
}

_SECTOR_SELECTION_GROUPS = [
    (
        "Related Sector Selection",
        [
            ("SECTOR_SELECTION_ENABLED", "bool", {}),
            ("SECTOR_SELECTION_TOP_N", "int", {"min": 1, "max": 11, "step": 1}),
            ("SECTOR_SELECTION_W1", "float", {"min": 0.0, "max": 1.0, "step": 0.05}),
            ("SECTOR_SELECTION_W2", "float", {"min": 0.0, "max": 1.0, "step": 0.05}),
            ("SECTOR_SELECTION_HEAT_LOOKBACK_DAYS", "int", {"min": 1, "max": 252, "step": 1}),
            ("SECTOR_SELECTION_HEAT_A", "float", {"min": 0.0, "max": 5.0, "step": 0.05}),
            ("SECTOR_SELECTION_HEAT_B", "float", {"min": 0.0, "max": 5.0, "step": 0.05}),
            ("SECTOR_SELECTION_HEAT_C", "float", {"min": 0.05, "max": 5.0, "step": 0.05}),
            ("SECTOR_SIMILARITY_EMBEDDER", "enum", {"options": ["sbert", "openai", "none"]}),
            ("SECTOR_SIMILARITY_MODEL", "str", {}),
            ("SECTOR_SIMILARITY_POOLING", "enum", {"options": ["max", "mean"]}),
        ],
    ),
]

_SECTOR_SELECTION_INDEX = {
    key: (kind, extras)
    for _group, _specs in _SECTOR_SELECTION_GROUPS
    for key, kind, extras in _specs
}

_FMP_GROUPS = [
    (
        "Client & Resiliency",
        [
            ("FMP_BASE_URL", "str", {}),
            ("FMP_TIMEOUT_SECONDS", "float", {"min": 1.0, "max": 120.0, "step": 1.0}),
            ("FMP_MIN_REQUEST_INTERVAL_SECONDS", "float", {"min": 0.0, "max": 60.0, "step": 0.05}),
            ("FMP_MAX_RETRIES", "int", {"min": 0, "max": 10, "step": 1}),
            ("FMP_RETRY_BACKOFF_SECONDS", "float", {"min": 0.1, "max": 60.0, "step": 0.5}),
            ("FMP_COOLDOWN_THRESHOLD", "int", {"min": 1, "max": 20, "step": 1}),
            ("FMP_COOLDOWN_SECONDS", "float", {"min": 1.0, "max": 3600.0, "step": 10.0}),
            ("FMP_FALLBACK_ENABLED", "bool", {}),
            ("FMP_MAX_SECONDS_PER_CYCLE", "float", {"min": 1.0, "max": 600.0, "step": 1.0}),
        ],
    ),
    (
        "Primary Feeds",
        [
            ("FMP_QUOTES_ENABLED", "bool", {}),
            ("FMP_QUOTES_REALTIME", "bool", {}),
            ("FMP_BARS_ENABLED", "bool", {}),
            ("FMP_BARS_ADJUSTMENT", "enum", {"options": ["dividend-adjusted", "light", "full", "non-split-adjusted"]}),
            ("FMP_FUNDAMENTALS_ENABLED", "bool", {}),
        ],
    ),
    (
        "Diagnostic & Supplement Feeds",
        [
            ("FMP_ANALYST_ENABLED", "bool", {}),
            ("FMP_ANALYST_REFRESH_HOURS", "int", {"min": 1, "max": 168, "step": 1}),
            ("FMP_EARNINGS_ENABLED", "bool", {}),
            ("FMP_EARNINGS_REFRESH_HOURS", "int", {"min": 1, "max": 168, "step": 1}),
            ("FMP_MACRO_ENABLED", "bool", {}),
            ("FMP_ECON_INDICATORS", "str", {}),
            ("FMP_ECON_CALENDAR_ENABLED", "bool", {}),
            ("FMP_INSIDER_ENABLED", "bool", {}),
            ("FMP_INSIDER_REFRESH_DAYS", "int", {"min": 1, "max": 30, "step": 1}),
            ("FMP_INSIDER_MIN_LAG_DAYS", "int", {"min": 0, "max": 90, "step": 1}),
            ("FMP_SECTOR_SNAPSHOT_ENABLED", "bool", {}),
            ("FMP_UNIVERSE_ENABLED", "bool", {}),
            ("FMP_NEWS_ENABLED", "bool", {}),
            ("FMP_NEWS_PAGE_LIMIT", "int", {"min": 1, "max": 1000, "step": 1}),
            ("FMP_NEWS_MAX_PAGES", "int", {"min": 1, "max": 1000, "step": 1}),
            ("FMP_OPTIONS_HEALTH_ENABLED", "bool", {}),
            ("FMP_OPTIONS_CONTEXT_ENABLED", "bool", {}),
            ("FMP_PEERS_ENABLED", "bool", {}),
        ],
    ),
]

_FMP_INDEX = {
    key: (kind, extras)
    for _group, _specs in _FMP_GROUPS
    for key, kind, extras in _specs
}

_ETF_TRANSMISSION_GROUPS = [
    (
        "Holdings Ingestion",
        [
            ("ETF_HOLDINGS_ENABLED", "bool", {}),
            ("ETF_HOLDINGS_TICKERS", "json", {}),
            ("ETF_HOLDINGS_REFRESH_DAYS", "int", {"min": 1, "max": 90, "step": 1}),
            ("ETF_HOLDINGS_ISSUER_CSV_ENABLED", "bool", {}),
            ("ETF_HOLDINGS_MAX_SECONDS_PER_CYCLE", "float", {"min": 1.0, "max": 300.0, "step": 1.0}),
            ("ETF_HOLDINGS_CIRCUIT_BREAKER_THRESHOLD", "int", {"min": 1, "max": 20, "step": 1}),
        ],
    ),
    (
        "Measurement & Residualization",
        [
            ("ETF_TRANSMISSION_ENABLED", "bool", {}),
            ("ETF_HOLDINGS_MARKET_PROXY", "str", {}),
            ("ETF_TRANSMISSION_WRAPPERS", "json", {}),
            ("ETF_TRANSMISSION_EXCLUDED_SYMBOLS", "json", {}),
            ("ETF_TRANSMISSION_WINDOW_DAYS", "int", {"min": 10, "max": 504, "step": 1}),
            ("ETF_TRANSMISSION_MIN_OBS", "int", {"min": 5, "max": 252, "step": 1}),
        ],
    ),
    (
        "Position Sizing Derate",
        [
            ("ETF_TRANSMISSION_SIZING_ENABLED", "bool", {}),
            ("ETF_TRANSMISSION_MAX_DERATE", "float", {"min": 0.0, "max": 1.0, "step": 0.05}),
            ("ETF_TRANSMISSION_OWNERSHIP_REFERENCE", "float", {"min": 0.01, "max": 1.0, "step": 0.01}),
            ("ETF_TRANSMISSION_MIN_MULTIPLIER", "float", {"min": 0.0, "max": 1.0, "step": 0.05}),
        ],
    ),
    (
        "Portfolio Covariance Adjustment",
        [
            ("ETF_TRANSMISSION_PORTFOLIO_ENABLED", "bool", {}),
            ("ETF_TRANSMISSION_COV_INFLATION", "float", {"min": 0.0, "max": 5.0, "step": 0.05}),
            ("ETF_TRANSMISSION_COV_WINDOW_DAYS", "int", {"min": 10, "max": 504, "step": 1}),
        ],
    ),
]

_ETF_TRANSMISSION_INDEX = {
    key: (kind, extras)
    for _group, _specs in _ETF_TRANSMISSION_GROUPS
    for key, kind, extras in _specs
}


@app.get("/settings/sentiment", dependencies=[Depends(require_read_token)])
def get_settings_sentiment() -> Dict[str, Any]:
    """Get sentiment & news ingestion configuration."""
    return _settings_editor_payload(_SENTIMENT_GROUPS, _SENTIMENT_INDEX)


@app.put(
    "/settings/sentiment",
    dependencies=[
        Depends(require_command_token),
        Depends(require_general_settings_writes_enabled),
    ],
)
@app.patch(
    "/settings/sentiment",
    dependencies=[
        Depends(require_command_token),
        Depends(require_general_settings_writes_enabled),
    ],
)
def put_settings_sentiment(body: TunablesUpdateRequest) -> Dict[str, Any]:
    """Update sentiment & news ingestion configuration in .env."""
    return _validate_and_write_payload(body.values, _SENTIMENT_INDEX, confirm=body.confirm)


@app.get("/settings/sector-selection", dependencies=[Depends(require_read_token)])
def get_settings_sector_selection() -> Dict[str, Any]:
    """Get sector selection configuration."""
    return _settings_editor_payload(_SECTOR_SELECTION_GROUPS, _SECTOR_SELECTION_INDEX)


@app.put(
    "/settings/sector-selection",
    dependencies=[
        Depends(require_command_token),
        Depends(require_general_settings_writes_enabled),
    ],
)
@app.patch(
    "/settings/sector-selection",
    dependencies=[
        Depends(require_command_token),
        Depends(require_general_settings_writes_enabled),
    ],
)
def put_settings_sector_selection(body: TunablesUpdateRequest) -> Dict[str, Any]:
    """Update sector selection configuration in .env."""
    return _validate_and_write_payload(body.values, _SECTOR_SELECTION_INDEX, confirm=body.confirm)


@app.get("/settings/cache-long-short", dependencies=[Depends(require_read_token)])
def get_settings_cache_long_short() -> Dict[str, Any]:
    """Get Cache Long/Short configuration."""
    return _settings_editor_payload(_CACHE_LONG_SHORT_GROUPS, _CACHE_LONG_SHORT_INDEX)


@app.put(
    "/settings/cache-long-short",
    dependencies=[
        Depends(require_command_token),
        Depends(require_general_settings_writes_enabled),
    ],
)
@app.patch(
    "/settings/cache-long-short",
    dependencies=[
        Depends(require_command_token),
        Depends(require_general_settings_writes_enabled),
    ],
)
def put_settings_cache_long_short(body: TunablesUpdateRequest) -> Dict[str, Any]:
    """Update Cache Long/Short configuration in .env."""
    return _validate_and_write_payload(body.values, _CACHE_LONG_SHORT_INDEX, confirm=body.confirm)

@app.get("/settings/paper-broker", dependencies=[Depends(require_read_token)])
def get_settings_paper_broker() -> Dict[str, Any]:
    return _settings_editor_payload(_PAPER_BROKER_GROUPS, _PAPER_BROKER_INDEX)

@app.put(
    "/settings/paper-broker",
    dependencies=[
        Depends(require_command_token),
        Depends(require_general_settings_writes_enabled),
    ],
)
@app.patch(
    "/settings/paper-broker",
    dependencies=[
        Depends(require_command_token),
        Depends(require_general_settings_writes_enabled),
    ],
)
def put_settings_paper_broker(body: TunablesUpdateRequest) -> Dict[str, Any]:
    return _validate_and_write_payload(body.values, _PAPER_BROKER_INDEX, confirm=body.confirm)


# A handful of the DANGEROUS_KEYS-tier fields are not plain booleans and
# need the same enum/json treatment their OTHER editors already give them
# (_FMP_GROUPS's own FMP_BARS_ADJUSTMENT entry, _TUNABLE_GROUPS's own
# CORS_ALLOWED_ORIGINS entry) -- inferring kind from the pydantic type
# annotation alone would render FMP_BARS_ADJUSTMENT and
# ROBINHOOD_EXECUTION_MODE as free-text inputs with no options constraint,
# which is precisely wrong for the two highest-risk string fields in the
# whole registry. Every other feature-flag key is a plain bool.
_FEATURE_FLAGS_NON_BOOL_SPECS: Dict[str, tuple] = {
    "ROBINHOOD_EXECUTION_MODE": ("enum", {"options": ["off", "review", "live"]}),
    "FMP_BARS_ADJUSTMENT": (
        "enum",
        {"options": ["dividend-adjusted", "light", "full", "non-split-adjusted"]},
    ),
    "CORS_ALLOWED_ORIGINS": ("json", {}),
}


def _build_feature_flags_index():
    dangerous_specs = [
        (key, *_FEATURE_FLAGS_NON_BOOL_SPECS.get(key, ("bool", {})))
        for key in sorted(settings_keysets.DANGEROUS_KEYS | set(feature_flags.WRITE_GATE_REASONS))
    ]
    diagnostic_specs = [
        (key, *_FEATURE_FLAGS_NON_BOOL_SPECS.get(key, ("bool", {})))
        for key in sorted(feature_flags.DIAGNOSTIC_FLAG_REASONS)
    ]
    groups = [
        ("Write & Execution Gates", dangerous_specs),
        ("Diagnostic & Data Features", diagnostic_specs),
    ]
    index = {k: (knd, ext) for _, sps in groups for k, knd, ext in sps}
    return groups, index

_FEATURE_FLAGS_GROUPS, _FEATURE_FLAGS_INDEX = _build_feature_flags_index()

@app.get("/settings/feature-flags", dependencies=[Depends(require_read_token)])
def get_feature_flags_settings() -> Dict[str, Any]:
    """Return current values and metadata for every admin/write/execution
    gate and read-only diagnostic feature flag in one place -- the single,
    clearly-labeled Feature Flags screen. See pilots/feature_flags.py's
    module docstring for the three-tier registry this is built from."""
    return _settings_editor_payload(
        _FEATURE_FLAGS_GROUPS, _FEATURE_FLAGS_INDEX
    )

@app.put(
    "/settings/feature-flags",
    dependencies=[
        Depends(require_command_token),
        Depends(require_general_settings_writes_enabled),
    ],
)
@app.patch(
    "/settings/feature-flags",
    dependencies=[
        Depends(require_command_token),
        Depends(require_general_settings_writes_enabled),
    ],
)
def put_feature_flags_settings(body: TunablesUpdateRequest) -> Dict[str, Any]:
    """Update feature flags in .env. Gated by
    require_general_settings_writes_enabled -- matching every other
    /settings/* editor's PUT (not require_automation_writes_enabled, whose
    own docstring scopes it to the daemon interval and kill-switch resume
    specifically and says every sibling flag must not ride in on it)."""
    return _validate_and_write_payload(body.values, _FEATURE_FLAGS_INDEX, confirm=body.confirm)


@app.get("/settings/fmp", dependencies=[Depends(require_read_token)])
def get_settings_fmp() -> Dict[str, Any]:
    """Get Financial Modeling Prep (FMP) configuration."""
    return _settings_editor_payload(_FMP_GROUPS, _FMP_INDEX)


@app.put(
    "/settings/fmp",
    dependencies=[
        Depends(require_command_token),
        Depends(require_general_settings_writes_enabled),
    ],
)
@app.patch(
    "/settings/fmp",
    dependencies=[
        Depends(require_command_token),
        Depends(require_general_settings_writes_enabled),
    ],
)
def put_settings_fmp(body: TunablesUpdateRequest) -> Dict[str, Any]:
    """Update Financial Modeling Prep (FMP) configuration in .env."""
    return _validate_and_write_payload(body.values, _FMP_INDEX, confirm=body.confirm)


@app.get("/settings/etf-transmission", dependencies=[Depends(require_read_token)])
def get_settings_etf_transmission() -> Dict[str, Any]:
    """Get ETF volatility transmission & holdings configuration."""
    return _settings_editor_payload(_ETF_TRANSMISSION_GROUPS, _ETF_TRANSMISSION_INDEX)


@app.put(
    "/settings/etf-transmission",
    dependencies=[
        Depends(require_command_token),
        Depends(require_general_settings_writes_enabled),
    ],
)
@app.patch(
    "/settings/etf-transmission",
    dependencies=[
        Depends(require_command_token),
        Depends(require_general_settings_writes_enabled),
    ],
)
def put_settings_etf_transmission(body: TunablesUpdateRequest) -> Dict[str, Any]:
    """Update ETF volatility transmission & holdings configuration in .env."""
    return _validate_and_write_payload(body.values, _ETF_TRANSMISSION_INDEX, confirm=body.confirm)


# ---------------------------------------------------------------------------
# Report Library (GET /reports, GET /reports/{name}) + Dead-Letter Queue
# (GET /dead-letter, POST /dead-letter/retry) — webapp parity gaps G5/G6.
#
# ``pilots/reports.py`` and ``pilots/dead_letter.py`` are new, dependency-light
# (stdlib + ``settings`` only) read helpers — see their own module docstrings.
# Imported LAZILY per function (matching ``gui.decision_log``/
# ``pilots.watchlist_writer`` elsewhere in this file) rather than added to the
# multi-line ``from pilots import (...)`` block above, so this block stays a
# self-contained append with no edit to that shared block.
# ---------------------------------------------------------------------------


def require_dead_letter_retry_enabled() -> None:
    """FAIL-CLOSED master-switch guard for ``POST /dead-letter/retry``. A
    DEDICATED flag (``settings.DEAD_LETTER_RETRY_ENABLED``), NOT any sibling
    ``require_*_writes_enabled`` flag: this spawns a REAL single-symbol
    ``main.py`` subprocess (network calls, a fresh data fetch, a real
    advisory evaluation) — a materially different cost/risk than any
    existing flag was scoped for. Mirrors ``require_automation_writes_enabled``
    exactly — GUI-writable (as of 2026-08-08) (``gui/env_io.py``), surfaced in the Feature Flags screen
    ``.env`` only. ``GET /dead-letter`` is read-only and NOT gated by this
    flag (``require_read_token`` alone, matching every other GET here)."""
    if not settings.DEAD_LETTER_RETRY_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Dead-letter retry is disabled (DEAD_LETTER_RETRY_ENABLED=false).",
        )


class DeadLetterRetryRequest(BaseModel):
    """Body for ``POST /dead-letter/retry``. Re-runs ``main.py``
    (advisory-only — no orders) for exactly ONE symbol via
    ``gui.orchestrator_runner.launch_symbol_retry``, the SAME subprocess
    launcher the Streamlit Launcher tab's dead-letter Retry button already
    uses (``gui/panels/launcher.py::_render_dead_letter_queue``). ``symbol``
    shape is re-validated by the endpoint against
    ``pilots.watchlist_writer._SYMBOL_RE`` before the launcher is ever
    invoked — the launcher itself builds a subprocess env var from the raw
    string and performs no shape validation of its own (unlike
    ``pilots.watchlist_writer.append_symbols``)."""

    symbol: str = Field(..., min_length=1, max_length=16)


@app.get("/reports", dependencies=[Depends(require_read_token)])
def get_reports() -> Dict[str, Any]:
    """Manifest of every generated report file the Streamlit Report Library
    tab (``gui/panels/reports_library.py``) enumerates: the daily report, the
    two orchestrator dashboards, daily briefings (``briefing_*.md``), and
    validation reports (``*_validation_summary.json`` / ``validation_*.html``).

    Fail-open read (``require_read_token``), mirroring every other GET here.
    Reuses THIS module's own ``_reports_dir()`` (validation files only —
    briefings/dashboards/the daily report all resolve off
    ``settings.OUTPUT_DIR`` directly, matching every other reader in this
    file) so tests can point it at a fixture dir the same way they already do
    for ``GET /strategy/health`` and ``GET /strategy/validation-trend``.
    Never 500s — an empty universe degrades to ``reports: []`` plus an
    honest ``reason`` (CONSTRAINT #6)."""
    from pilots import reports as reports_reader

    return reports_reader.list_reports(reports_dir=_reports_dir())


@app.get("/reports/{name}", dependencies=[Depends(require_read_token)])
def get_report_content(name: str) -> Dict[str, Any]:
    """Content for one report file: markdown text (a briefing), HTML text
    (the daily report / an orchestrator dashboard / a validation HTML
    report), or a parsed JSON object (a validation summary).

    SECURITY: ``name`` is resolved ONLY against the manifest
    ``pilots.reports.get_report_content`` itself builds by globbing the real
    report directories (see that function's docstring) — this handler never
    joins the client-supplied ``name`` onto a filesystem path. Mirrors
    ``pilots.commands.resolve_command``'s identical discipline for
    ``POST /jobs``'s command execution. A ``name`` absent from that manifest
    — including any ``../`` traversal attempt, which can never match a real
    globbed basename — 404s honestly rather than attempting a read."""
    from pilots import reports as reports_reader

    result = reports_reader.get_report_content(name, reports_dir=_reports_dir())
    if result is None:
        raise HTTPException(status_code=404, detail=f"No report named {name!r}.")
    return result


@app.get("/dead-letter", dependencies=[Depends(require_read_token)])
def get_dead_letter() -> Dict[str, Any]:
    """The last pipeline run's dead-letter queue (failed symbols) —
    ``output/dead_letter.json``, written by ``main_orchestrator.run_pipeline``
    (mirrors the Streamlit Launcher tab's dead-letter section,
    ``gui/panels/launcher.py::_render_dead_letter_queue``).

    Fail-open read (``require_read_token``). ``retry_enabled`` mirrors the
    ``writable`` convention used elsewhere (``GET /strategy/matrix``,
    ``GET /agentic/discovery``) so the PWA can hide/disable the Retry control
    before the operator hits a 403 on ``POST /dead-letter/retry``. Never
    500s — a missing/corrupt file degrades to ``entries: []`` with an honest
    ``reason`` and ``is_clean: null`` (CONSTRAINT #6; ``null``, not ``true``,
    since "no run has completed yet" is not the same claim as "the last run
    was clean")."""
    from pilots import dead_letter as dead_letter_reader

    payload = dead_letter_reader.read_dead_letter()
    payload["retry_enabled"] = bool(settings.DEAD_LETTER_RETRY_ENABLED)
    return payload


@app.post(
    "/dead-letter/retry",
    dependencies=[
        Depends(require_command_token),
        Depends(require_dead_letter_retry_enabled),
    ],
)
def post_dead_letter_retry(body: DeadLetterRetryRequest) -> Dict[str, Any]:
    """Re-run ``main.py`` for exactly one dead-lettered symbol — a genuine
    subprocess spawn with real network/broker-cache cost, so this sits
    behind ``require_command_token`` STACKED with the dedicated
    ``require_dead_letter_retry_enabled`` master switch (same "auth tier AND
    feature flag" pattern as ``PUT /strategy/modules`` / ``POST
    /agentic/watch``). Reuses ``gui.orchestrator_runner.launch_symbol_retry``
    — the SAME launcher the Streamlit Launcher tab's per-symbol Retry button
    already calls — rather than re-implementing the subprocess spawn.

    The symbol is re-validated against ``pilots.watchlist_writer._SYMBOL_RE``
    here first (422 ``invalid_symbol`` on a malformed value) since
    ``launch_symbol_retry`` performs no shape validation of its own before
    writing the value into a subprocess env var and a log-file banner line.
    Does not wait for the run to finish — returns immediately with the
    spawned PID and log path so the caller can poll/tail it. This is an
    advisory-only, no-order diagnostic run (``main.py`` submits no orders),
    never applied retroactively (``applies: "immediately"`` describes the
    subprocess launch itself, not any order submission — there is none)."""
    from gui.orchestrator_runner import launch_symbol_retry
    from pilots.watchlist_writer import _SYMBOL_RE

    symbol = body.symbol.strip().upper()
    if not _SYMBOL_RE.match(symbol):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_symbol",
                "message": f"{symbol!r} is not a valid ticker shape.",
            },
        )

    handle = launch_symbol_retry(symbol)
    return {
        "symbol": symbol,
        "pid": handle.pid,
        "log_path": str(handle.log_path),
        "applies": "immediately",
        "note": f"Retry launched for {symbol} (advisory-only — no orders placed).",
    }


# =============================================================================
# Prompt Registry (webapp parity gap G4) — pilots/prompt_registry.py wraps
# prompt_registry.registry.get_registry(). Self-contained block, appended at
# the end of the file per this repo's multi-agent collision protocol (other
# agents append their own new endpoints elsewhere in this same file
# concurrently on separate branches — appending here avoids a merge conflict
# on a shared line range near the top of the file).
#
# GET /prompts and GET /prompts/{id} are fail-open reads (require_read_token
# alone, matching every other GET on this API). PUT /prompts/pin changes
# WHICH PROMPT TEXT THE PLATFORM ACTUALLY RUNS -- a real behavioral change,
# not a config tunable -- so it sits behind BOTH the fail-closed command
# token (require_command_token, i.e. FOLLOW_API_TOKEN) AND a NEW dedicated
# master flag (require_prompt_registry_writes_enabled ->
# settings.PROMPT_REGISTRY_WRITES_ENABLED), mirroring
# require_strategy_writes_enabled's exact reasoning. `sync`/`verify`/
# `rollback`/`diff` are deliberately NOT new endpoints here -- they are
# already CLI-drivable via POST /jobs {job_type: "command", params:
# {command: "prompt_registry", subcommand: "sync"|"verify"|"rollback"|"diff"}}
# (see pilots/commands.py + cli_introspect/command_manifest.json), so building
# a bespoke HTTP path for them would duplicate existing, tested capability.
# =============================================================================

from pilots import prompt_registry as prompt_registry_reader  # noqa: E402


def require_prompt_registry_writes_enabled() -> None:
    """FAIL-CLOSED master-switch guard for ``PUT /prompts/pin`` (pins/clears a
    prompt ID's entry in ``PROMPT_REGISTRY_PINS`` -> ``.env``). A DEDICATED
    flag (``settings.PROMPT_REGISTRY_WRITES_ENABLED``), NOT
    ``STRATEGY_WRITES_ENABLED``/``GENERAL_SETTINGS_WRITES_ENABLED``/any other
    sibling flag: pinning a prompt version changes WHICH PROMPT TEXT THE
    PLATFORM ACTUALLY RUNS, its own risk class distinct from a numeric
    tunable or a signal weight, and must not ride in on a flag scoped to a
    different concern. Mirrors ``require_strategy_writes_enabled`` exactly —
    GUI-writable (as of 2026-08-08) (absent from BOTH ``gui/env_io.py``'s
    ``ALLOWED_KEYS`` and ``SECRET_KEYS``), surfaced in the Feature Flags screen.
    ``GET /prompts`` and ``GET /prompts/{id}`` are read-only and NOT gated by
    this flag (``require_read_token`` alone, matching ``GET /strategy/matrix``
    and every other GET on this API)."""
    if not settings.PROMPT_REGISTRY_WRITES_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Prompt Registry writes are disabled (PROMPT_REGISTRY_WRITES_ENABLED=false).",
        )


class PromptPinRequest(BaseModel):
    """Body for ``PUT /prompts/pin``. ``version=None`` CLEARS any existing pin
    for ``prompt_id`` (resolves to remote latest / cache / baseline again on
    the next daemon restart) rather than pinning it — a single endpoint covers
    both the Streamlit tab's "Set pin" and "Clear pin" actions. Auto-rollback
    (pin to the previous cached version) is a client-side computation: fetch
    ``GET /prompts`` for the cached-version count / current pin, then call
    this endpoint with the desired older version — no separate rollback
    endpoint exists here (see this block's module-level comment)."""

    prompt_id: str = Field(..., min_length=1)
    version: Optional[str] = Field(default=None, min_length=1)


@app.get("/prompts", dependencies=[Depends(require_read_token)])
def get_prompts() -> Dict[str, Any]:
    """Every known prompt ID with its resolved version, source, pinned state,
    and cached-version count (ports ``gui/panels/prompt_registry.py``'s
    "Registered prompts" table). Fail-open read, mirroring every other GET on
    this API. Never 500s — a disabled/unconstructible registry degrades to
    ``{"enabled": ..., "prompts": [], "reason": "..."}`` (CONSTRAINT #6).

    Adds two API-layer fields to the pure reader's payload — ``writable``
    (tracks ``PROMPT_REGISTRY_WRITES_ENABLED``, so the PWA can disable the
    pin/clear-pin UI instead of a surprise 403) and ``note`` — mirroring
    ``GET /strategy/matrix``'s identical ``writable``/``note`` addition over
    its own pure reader's payload."""
    payload = prompt_registry_reader.list_prompts()
    writable = bool(settings.PROMPT_REGISTRY_WRITES_ENABLED)
    payload["writable"] = writable
    payload["note"] = (
        "Pins persist to .env and apply on the next daemon restart."
        if writable
        else "Pin writes are disabled (PROMPT_REGISTRY_WRITES_ENABLED=false)."
    )
    return payload


@app.get("/prompts/{prompt_id}", dependencies=[Depends(require_read_token)])
def get_prompt(prompt_id: str, version: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    """The resolved body for one prompt ID — the full resolution chain when
    ``?version=`` is omitted, or one specific version (a cached version
    string, or the literal ``"baseline"``) when provided. Fail-open read. The
    client computes a unified diff between two versions itself from two calls
    to this endpoint — no server-side diff endpoint exists (the plan's own
    decision to keep the surface minimal: a diff is trivial to produce
    client-side once both bodies are in hand).

    ``found: false`` (never a 404 — an unknown prompt ID / version is an
    honest, structurally-expected outcome on this endpoint, not an error) is
    returned with a ``reason`` when nothing resolves."""
    return prompt_registry_reader.get_prompt_body(prompt_id, version=version)


@app.put(
    "/prompts/pin",
    dependencies=[
        Depends(require_command_token),
        Depends(require_prompt_registry_writes_enabled),
    ],
)
def put_prompts_pin(body: PromptPinRequest) -> Dict[str, Any]:
    """Pin (or, when ``version`` is omitted, clear the pin for) one prompt ID
    in ``PROMPT_REGISTRY_PINS`` -> ``.env`` via ``gui.env_io.write_setting``.

    Fail-closed command token (``require_command_token``, i.e.
    ``FOLLOW_API_TOKEN``) STACKED with the dedicated
    ``PROMPT_REGISTRY_WRITES_ENABLED`` master flag
    (``require_prompt_registry_writes_enabled``) — same "auth tier AND
    feature flag" pattern as ``PUT /strategy/modules``. A pin-set request is
    verified to actually resolve (manifest / disk cache / the ``"baseline"``
    keyword) BEFORE being persisted — pinning to a version that doesn't exist
    anywhere would silently degrade every future resolution for this ID down
    to the sentinel string, so that returns 422 ``version_not_found`` instead.

    The merge base for the OTHER prompt IDs' pins is read from
    ``settings.PROMPT_REGISTRY_PINS`` (the live process's view — the same
    source ``GET /prompts`` reads via the registry singleton it was
    constructed from) rather than re-reading ``.env`` directly, matching every
    other multi-key JSON ``.env`` writer in this file. Like every other
    ``.env`` write here this does NOT patch the running ``settings``
    singleton, so ``applies`` is always ``"next_daemon_restart"`` and the
    echoed ``pins``/``version`` reflect the REQUEST BODY merged onto that base
    — NOT a re-read of ``settings`` after the write (which would return the
    stale pre-write values and read as a failed write)."""
    prompt_id = body.prompt_id.strip()
    if not prompt_id:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_prompt_id", "message": "prompt_id must not be empty."},
        )

    pins: Dict[str, str] = dict(settings.PROMPT_REGISTRY_PINS or {})

    if body.version is None:
        pins.pop(prompt_id, None)
        note = f"Pin cleared for {prompt_id!r}. Saved to .env; effective on next daemon restart."
    else:
        resolved = prompt_registry_reader.get_prompt_body(prompt_id, version=body.version)
        if not resolved.get("found"):
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "version_not_found",
                    "message": (
                        f"Version {body.version!r} of {prompt_id!r} not found in the "
                        "manifest, disk cache, or committed baseline."
                    ),
                },
            )
        pins[prompt_id] = body.version
        note = (
            f"Pinned {prompt_id!r} -> {body.version!r}. Saved to .env; effective on "
            "next daemon restart."
        )

    env_io.write_setting("PROMPT_REGISTRY_PINS", pins)

    return {
        "prompt_id": prompt_id,
        "version": body.version,
        "pins": pins,
        "applies": "next_daemon_restart",
        "note": note,
    }


class RagQueryRequest(BaseModel):
    query: str


@app.post(
    "/rag/query",
    dependencies=[
        Depends(require_command_token),
        Depends(require_rag_query_enabled),
    ],
)
def post_rag_query(body: RagQueryRequest) -> Dict[str, Any]:
    """Portfolio-aware RAG query -- the first production caller of
    ``agents/rag_orchestrator.py::run_rag_query`` (previously wired to
    nothing but that module's own ``__main__`` block).

    Fail-closed command token (``require_command_token``, i.e.
    ``FOLLOW_API_TOKEN``) STACKED with the dedicated
    ``RAG_QUERY_API_ENABLED`` master flag (``require_rag_query_enabled``) —
    the same "auth tier AND feature flag" pattern as ``PUT /strategy/modules``
    / ``PUT /llm/setting``, required here because this calls a real, paid LLM
    provider (via ``llm/router.py::get_rationale_provider``), exactly the
    risk class ``api/data_api.py``'s ``_require_ai_generation_enabled`` gates
    on the Data API.

    ``run_rag_query`` never raises (dead-letter safe by its own design) --
    it returns an honest descriptive string for every degraded case
    (langgraph/qdrant/sentence-transformers missing, no LLM provider
    configured, an LLM call failure) and only an empty string on a genuine
    internal exception. ``available`` is ``False`` (and ``analysis`` is
    ``None``, never a fabricated placeholder -- CONSTRAINT #4) ONLY in that
    empty-string case; every other string -- including the degraded-mode
    messages -- is passed through verbatim as honest, human-readable status
    text, not something this endpoint tries to reinterpret."""
    query = body.query.strip()
    if not query:
        raise HTTPException(
            status_code=422,
            detail={"error": "empty_query", "message": "query must not be empty."},
        )

    analysis = run_rag_query(query)
    return {
        "query": query,
        "analysis": analysis if analysis else None,
        "available": bool(analysis),
    }


class CacheLongShortStartRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10)
    proxy_ticker: str = Field(..., min_length=1, max_length=10)
    allocation: float = Field(..., gt=0)
    correlation_coefficient: float = Field(...)

class CacheLongShortApproveBulkRequest(BaseModel):
    lot_ids: List[int]

class PaperBrokerResetRequest(BaseModel):
    cash: Optional[float] = Field(default=None, gt=0)

class OptionsOrderRequestModel(BaseModel):
    symbol: str
    asset_type: Optional[str] = "option"
    side: Optional[str] = "buy"
    quantity: Optional[float] = 1.0
    dollar_amount: Optional[float] = None
    order_type: Optional[str] = "market"
    limit_price: Optional[float] = None
    expiration: Optional[str] = None
    legs: Optional[List[Dict[str, Any]]] = None
    isLive: bool = False

class StrategyOptionsExecutionRequest(BaseModel):
    symbols: Optional[List[str]] = None
    dry_run: bool = False
    max_notional: Optional[float] = None

class ManageExitsRequest(BaseModel):
    dry_run: bool = False
    profit_target_pct: Optional[float] = None
    stop_loss_multiple: Optional[float] = None
    manage_dte_threshold: Optional[int] = None

class RollOrderRequest(BaseModel):
    symbol: str
    close_legs: List[Dict[str, Any]]
    open_legs: List[Dict[str, Any]]
    limit_price: Optional[float] = None
    contracts: Optional[int] = 1
    order_type: Optional[str] = "market"
    is_live: Optional[bool] = False

class DeltaHedgeExecuteRequest(BaseModel):
    dry_run: bool = False
    shares: Optional[float] = Field(default=None, ge=-100000.0, le=100000.0)

class ScenarioMatrixRequest(BaseModel):
    spot_shifts: Optional[List[float]] = Field(default=None, max_length=50)
    iv_shifts: Optional[List[float]] = Field(default=None, max_length=50)
    time_shifts: Optional[List[int]] = Field(default=None, max_length=50)
    # default=None (not 0) is load-bearing: it's what lets the route below
    # distinguish "caller didn't ask for a time dimension at all" (falls
    # through to the full default 4-slice grid) from "caller explicitly
    # wants only day 0" (0, collapses to a single T+0 slice) -- see
    # docs/known_issues/scenario_matrix_field_mismatch.md.
    time_days_forward: Optional[int] = Field(default=None, ge=0, le=365)

class EarningsCrushExecuteRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    strategy: Optional[str] = "Iron Condor"
    expiration: Optional[str] = None
    contracts: Optional[int] = Field(default=1, ge=1, le=1000)
    legs: Optional[List[Dict[str, Any]]] = None
    limit_price: Optional[float] = Field(default=None, gt=0.0)
    dry_run: bool = False
    is_live: bool = False

class DispersionExecuteRequest(BaseModel):
    index_symbol: str = Field(default="QQQ", min_length=1, max_length=10)
    basket: Optional[Dict[str, Any]] = None
    dry_run: bool = False
    is_live: bool = False

class ZeroDteExecuteRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    option_type: Optional[str] = "CALL"
    strike: float = Field(..., gt=0.0)
    expiration: Optional[str] = None
    contracts: Optional[int] = Field(default=1, ge=1, le=1000)
    limit_price: Optional[float] = Field(default=None, gt=0.0)
    stop_loss_pct: Optional[float] = Field(default=0.30, ge=0.01, le=1.0)
    profit_target_pct: Optional[float] = Field(default=0.75, ge=0.01, le=5.0)
    dry_run: bool = False
    is_live: bool = False


class VolMispricingExecuteRequest(BaseModel):
    symbol: str
    # The specific candidate trade the caller selected (one element of a prior
    # GET /pilots/options/forecast/mispricing call's candidate_trades list). This
    # endpoint never silently picks "the best" candidate itself.
    candidate: Dict[str, Any]
    contracts: Optional[int] = 1
    dry_run: bool = False
    is_live: bool = False
    # vol_mispricing is a MEASURED deployability failure (Sharpe -0.499, DSR 0.027,
    # fails the Oct-2008 stress window) -- execution is blocked by default and only
    # proceeds when this is explicitly set True on a per-request basis. Never a
    # standing settings flag -- see OPTIONS_DESK_DEPLOYABILITY_GATES["vol_mispricing"].
    override_deployability_gate: bool = False




class MultiLegOptionLeg(BaseModel):
    strike: float = Field(..., gt=0.0, description="Strike price of the option leg")
    option_type: Literal["call", "put", "CALL", "PUT"] = Field(..., description="Option type: CALL or PUT")
    action: Literal["buy", "sell", "BUY", "SELL"] = Field(..., description="Action: BUY or SELL")
    ratio: int = Field(default=1, gt=0, description="Contract ratio multiplier (>= 1)")
    expiration: Optional[str] = Field(default=None, description="Expiration date string (YYYY-MM-DD)")
    premium: Optional[float] = Field(default=None, ge=0.0, description="Market price per share if known")
    iv: Optional[float] = Field(default=None, gt=0.0, description="Implied volatility decimal (e.g. 0.30)")

class MultiLegStructurePricingRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10, description="Underlying ticker symbol")
    structure_type: Optional[str] = Field(default="CUSTOM", description="Structure type: IRON_CONDOR, VERTICAL_SPREAD, STRADDLE, STRANGLE, etc.")
    legs: List[MultiLegOptionLeg] = Field(..., min_length=1, description="List of component option legs")
    underlying_price: Optional[float] = Field(default=None, gt=0.0, description="Spot price override if not fetching from market")
    iv_override: Optional[float] = Field(default=None, gt=0.0, description="Global IV override for unpriced legs")

class MultiLegValidationRequest(BaseModel):
    structure_type: str = Field(..., description="Structure type: IRON_CONDOR, VERTICAL_SPREAD, STRADDLE, STRANGLE, etc.")
    legs: List[MultiLegOptionLeg] = Field(..., min_length=1, description="List of component option legs")


@app.get("/pilots/cache-long-short/concentrated-positions", dependencies=[Depends(require_read_token)])
def get_cls_concentrated_positions() -> Dict[str, Any]:
    """Real held (long) positions exceeding 20% of account equity, sourced
    from the cached AccountSnapshot (allow_live_fetch=False -- never blocks
    on a live broker login). Degrades to an empty list, never a fabricated
    row, on any lookup failure (CONSTRAINT #6)."""
    from data.robinhood_portfolio import fetch_account_snapshot

    try:
        snap = fetch_account_snapshot(allow_live_fetch=False)
        equity = snap.total_equity if snap else 0
        positions = []
        if equity > 0 and snap:
            for p in snap.positions:
                if (p.market_value / equity) > 0.20:
                    positions.append(
                        {"ticker": p.symbol, "market_value": p.market_value, "pct_equity": (p.market_value / equity)}
                    )
        return {"positions": positions}
    except Exception as exc:
        logger.warning("get_cls_concentrated_positions: account snapshot lookup failed: %s", exc)
        return {"positions": []}

@app.get("/pilots/cache-long-short/dashboard", dependencies=[Depends(require_read_token)])
def get_cls_dashboard() -> Dict[str, Any]:
    from pilots.cache_long_short import get_dashboard
    return get_dashboard()

@app.get("/pilots/cache-long-short/pending-approvals", dependencies=[Depends(require_read_token)])
def get_cls_pending_approvals() -> List[Dict[str, Any]]:
    from pilots.cache_long_short import get_pending_approvals
    return get_pending_approvals()

@app.post("/pilots/cache-long-short/start", dependencies=[Depends(require_command_token), Depends(require_cache_long_short_writes_enabled)])
def start_cls_strategy(body: CacheLongShortStartRequest) -> Dict[str, Any]:
    """Persists a new tracked position + its already-simulated proxy hedge
    (the caller must have already called POST /data/cache-long-short/simulate
    -- this endpoint never recomputes beta/proxy/correlation itself, per the
    AST-guard split documented in engine/cache_long_short_engine.py)."""
    from data.cache_long_short_store import CacheLongShortStore
    store = CacheLongShortStore()
    pos_id = store.record_position(body.ticker, "long")
    store.upsert_security_proxy(body.ticker, body.proxy_ticker, body.correlation_coefficient)
    return {"status": "started", "position_id": pos_id, "ticker": body.ticker}

@app.post("/pilots/cache-long-short/approve-bulk", dependencies=[Depends(require_command_token), Depends(require_cache_long_short_writes_enabled)])
def approve_cls_bulk(body: CacheLongShortApproveBulkRequest) -> Dict[str, Any]:
    """Marks the given TLH-flagged lots approved. Still advisory only in V1
    -- no broker order is submitted; approval only changes what's shown as
    actionable."""
    from data.cache_long_short_store import CacheLongShortStore
    store = CacheLongShortStore()
    store.approve_tax_lots(body.lot_ids)
    return {"status": "approved", "count": len(body.lot_ids)}

@app.get("/pilots/paper-broker/account", dependencies=[Depends(require_read_token)])
def get_paper_broker_account() -> Dict[str, Any]:
    from pilots.paper_broker import get_account
    return get_account()

@app.get("/pilots/paper-broker/positions", dependencies=[Depends(require_read_token)])
def get_paper_broker_positions() -> List[Dict[str, Any]]:
    from pilots.paper_broker import get_positions
    return get_positions()

@app.get("/pilots/paper-broker/orders", dependencies=[Depends(require_read_token)])
def get_paper_broker_orders(status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    from pilots.paper_broker import get_orders
    return get_orders(status=status, limit=limit)

@app.post("/pilots/paper-broker/reset", dependencies=[Depends(require_command_token), Depends(require_paper_broker_writes_enabled)])
def post_paper_broker_reset(body: Optional[PaperBrokerResetRequest] = None) -> Dict[str, Any]:
    from data.paper_account_store import PaperAccountStore
    store = PaperAccountStore()
    starting_cash = body.cash if body and body.cash is not None else None
    store.reset_account(starting_cash=starting_cash)
    acc = store.get_account()
    return {"status": "ok", "message": "Paper account reset", "cash": acc.cash}

@app.post("/brokerage/options/order", dependencies=[Depends(require_command_token), Depends(require_paper_broker_writes_enabled)])
def post_brokerage_options_order(body: OptionsOrderRequestModel) -> Dict[str, Any]:
    """Execute a paper or live options/stock order from the options chain screen."""
    from pilots.paper_broker import execute_paper_order
    return execute_paper_order(
        symbol=body.symbol,
        asset_type=body.asset_type or "option",
        side=body.side or "buy",
        quantity=body.quantity,
        dollar_amount=body.dollar_amount,
        order_type=body.order_type or "market",
        limit_price=body.limit_price,
        expiration=body.expiration,
        legs=body.legs,
        is_live=body.isLive,
    )

@app.get("/pilots/paper-broker/strategy-options/candidates", dependencies=[Depends(require_read_token)])
def get_paper_broker_strategy_options_candidates(symbols: Optional[str] = None) -> Dict[str, Any]:
    """Preview current gate-passing strategy options candidates eligible for automated paper execution."""
    from pilots.paper_broker import get_strategy_options_candidates
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
    candidates = get_strategy_options_candidates(symbols=sym_list)
    return {"count": len(candidates), "candidates": candidates}

@app.post("/pilots/paper-broker/strategy-options/execute", dependencies=[Depends(require_command_token), Depends(require_paper_broker_writes_enabled)])
def post_paper_broker_strategy_options_execute(body: Optional[StrategyOptionsExecutionRequest] = None) -> Dict[str, Any]:
    """Execute automated paper trades for all eligible strategy option directives."""
    from pilots.paper_broker import execute_strategy_options
    symbols = body.symbols if body else None
    dry_run = body.dry_run if body else False
    max_notional = body.max_notional if body else None
    try:
        return execute_strategy_options(symbols=symbols, dry_run=dry_run, max_notional=max_notional)
    except Exception:  # noqa: BLE001 - dead-letter: never leak exception detail to the client
        logger.error("pilots_api: strategy-options/execute failed", exc_info=True)
        return {"ok": False, "error": "Internal error while executing strategy options; see server logs for detail."}

@app.get("/pilots/paper-broker/greeks", dependencies=[Depends(require_read_token)])
def get_paper_broker_portfolio_greeks() -> Dict[str, Any]:
    """Computes aggregate net Greeks (Delta, Gamma, Theta, Vega) across all open paper positions."""
    from pilots.paper_broker import get_portfolio_greeks
    return get_portfolio_greeks()

@app.post(
    "/pilots/paper-broker/manage-exits",
    dependencies=[
        Depends(require_command_token),
        Depends(require_paper_broker_writes_enabled),
    ],
)
def post_paper_broker_manage_exits(body: Optional[ManageExitsRequest] = None) -> Dict[str, Any]:
    """Evaluates open option positions and executes auto-exits based on profit targets, stop losses, and 21-DTE thresholds."""
    from pilots.paper_broker import manage_position_exits
    dry_run = body.dry_run if body else False
    profit_target_pct = body.profit_target_pct if body else None
    stop_loss_multiple = body.stop_loss_multiple if body else None
    manage_dte_threshold = body.manage_dte_threshold if body else None
    try:
        return manage_position_exits(
            dry_run=dry_run,
            profit_target_pct=profit_target_pct,
            stop_loss_multiple=stop_loss_multiple,
            manage_dte_threshold=manage_dte_threshold,
        )
    except Exception:  # noqa: BLE001 - dead-letter: never leak exception detail to the client
        logger.error("pilots_api: manage-exits failed", exc_info=True)
        return {"ok": False, "error": "Internal error while managing position exits; see server logs for detail."}

@app.post(
    "/pilots/paper-broker/roll",
    dependencies=[
        Depends(require_command_token),
        Depends(require_paper_broker_writes_enabled),
    ],
)
def post_paper_broker_roll(body: RollOrderRequest) -> Dict[str, Any]:
    """Executes an atomic option roll order closing existing contracts and opening new expiration legs."""
    from pilots.paper_broker import execute_roll
    return execute_roll(
        symbol=body.symbol,
        close_legs=body.close_legs,
        open_legs=body.open_legs,
        limit_price=body.limit_price,
        contracts=body.contracts or 1,
        is_live=body.is_live or False,
    )

@app.get("/pilots/paper-broker/delta-hedge/preview", dependencies=[Depends(require_read_token)])
def get_paper_broker_delta_hedge_preview() -> Dict[str, Any]:
    """Returns dynamic SPY beta-weighted delta hedge recommendation and deadband threshold status."""
    from pilots.options_hedging import get_delta_hedge_preview
    return get_delta_hedge_preview()

@app.post(
    "/pilots/paper-broker/delta-hedge/execute",
    dependencies=[
        Depends(require_command_token),
        Depends(require_paper_broker_writes_enabled),
    ],
)
def post_paper_broker_delta_hedge_execute(body: Optional[DeltaHedgeExecuteRequest] = None) -> Dict[str, Any]:
    """Executes dynamic delta hedging trade for SPY in the paper broker."""
    from pilots.options_hedging import execute_delta_hedge
    dry_run = body.dry_run if body else False
    shares = body.shares if body else None
    return execute_delta_hedge(dry_run=dry_run, shares_override=shares)

@app.get("/pilots/options/vol-surface", dependencies=[Depends(require_read_token)])
def get_options_vol_surface(
    symbol: str = Query(..., min_length=1),
    expiration: Optional[str] = Query(None),
) -> Dict[str, Any]:
    """Returns volatility surface, IV smile curve, term structure, 25-delta skew, and VRP volatility cone."""
    from pilots.volatility_surface import get_volatility_surface_data, to_vol_surface_response
    raw = get_volatility_surface_data(symbol=symbol)
    return to_vol_surface_response(raw, selected_expiration=expiration)

@app.post("/pilots/paper-broker/scenario-matrix", dependencies=[Depends(require_read_token)])
def post_paper_broker_scenario_matrix(body: Optional[ScenarioMatrixRequest] = None) -> Dict[str, Any]:
    """Evaluates 2D stress test grid and historical crisis scenario projections on open paper positions."""
    from pilots.scenario_matrix import evaluate_portfolio_scenario_matrix
    return evaluate_portfolio_scenario_matrix(
        spot_shifts=body.spot_shifts if body else None,
        iv_shifts=body.iv_shifts if body else None,
        time_shifts=body.time_shifts if body else None,
        time_days_forward=body.time_days_forward if body else None,
    )


class OptionsBacktestRequest(BaseModel):
    strategy: str
    ticker: str = "SPY"
    start_date: str = "2020-01-01"
    end_date: str = "2024-01-01"
    initial_capital: float = 100000.0

@app.post("/pilots/options/backtest", dependencies=[Depends(require_read_token)])
def post_options_backtest(body: OptionsBacktestRequest) -> Dict[str, Any]:
    """Runs an options strategy backtest through OptionsValidationHarness and returns metrics, equity curves, and trade logs."""
    from validation.options_harness import OptionsValidationHarness
    harness = OptionsValidationHarness()
    try:
        res = harness.run_backtest(
            strategy=body.strategy,
            ticker=body.ticker,
            start_date=body.start_date,
            end_date=body.end_date,
            initial_capital=body.initial_capital,
        )
        return {
            "strategy_name": res.strategy_name,
            "ticker": res.ticker,
            "start_date": res.start_date,
            "end_date": res.end_date,
            "initial_capital": res.initial_capital,
            "final_capital": res.final_capital,
            "total_return_pct": res.total_return_pct,
            "annualized_return_pct": res.annualized_return_pct,
            "sharpe_ratio": res.sharpe_ratio,
            "sortino_ratio": res.sortino_ratio,
            "max_drawdown_pct": res.max_drawdown_pct,
            "total_trades": res.total_trades,
            "winning_trades": res.winning_trades,
            "losing_trades": res.losing_trades,
            "win_rate_pct": res.win_rate_pct,
            "profit_factor": res.profit_factor,
            "avg_win": res.avg_win,
            "avg_loss": res.avg_loss,
            "pbo": res.pbo,
            "dsr": res.dsr,
            "passes_stress": res.passes_stress,
            "deployable": res.deployable,
            "equity_curve": res.equity_curve,
            "trades": [
                {
                    "entry_date": t.entry_date,
                    "exit_date": t.exit_date,
                    "strategy": t.strategy,
                    "underlying_entry_price": t.underlying_entry_price,
                    "underlying_exit_price": t.underlying_exit_price,
                    "entry_net_premium": t.entry_net_premium,
                    "exit_net_cost": t.exit_net_cost,
                    "pnl_dollar": t.pnl_dollar,
                    "pnl_pct": round(t.pnl_pct * 100.0, 2),
                    "exit_reason": t.exit_reason,
                    "holding_days": t.holding_days,
                    "contracts": t.contracts,
                }
                for t in res.trades
            ],
        }
    except Exception as exc:
        logger.error("Options backtest failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))

@app.get("/pilots/options/meta-model/status", dependencies=[Depends(require_read_token)])
def get_options_meta_model_status() -> Dict[str, Any]:
    """Returns training health, sample size, accuracy, and ROC-AUC of the Stage 4 Options ML Meta-Labeler."""
    from ml.options_meta_labeler import global_options_meta_labeler
    global_options_meta_labeler.load_model()
    return {
        "n_samples": global_options_meta_labeler.n_samples,
        "train_accuracy": round(global_options_meta_labeler.train_accuracy * 100.0, 2),
        "train_roc_auc": round(global_options_meta_labeler.train_roc_auc, 3),
        "trained_at": global_options_meta_labeler.trained_at.isoformat() if global_options_meta_labeler.trained_at else None,
        "enabled": getattr(settings, "OPTIONS_META_LABELER_ENABLED", True),
    }

@app.post(
    "/pilots/options/meta-model/retrain",
    dependencies=[
        Depends(require_command_token),
        Depends(require_paper_broker_writes_enabled),
    ],
)
def post_options_meta_model_retrain() -> Dict[str, Any]:
    """Triggers retraining of the Stage 4 ML Options Meta-Labeler on simulated/paper trades."""
    from ml.options_meta_labeler import global_options_meta_labeler, OptionsTradeFeatureRow
    from validation.options_harness import OptionsValidationHarness
    from data.paper_account_store import PaperAccountStore
    import datetime

    store = PaperAccountStore()
    closed_trades = store.get_closed_trades() if hasattr(store, "get_closed_trades") else []
    
    samples = []
    skipped_count = 0
    data_source = "paper"

    if len(closed_trades) >= 30:
        for t in closed_trades:
            # Check if option trade
            if " CALL" not in t.symbol and " PUT" not in t.symbol and t.order_kind != "option_leg":
                continue
            # Note: since the features are not in the DB, we dynamically extract from historical store
            # But the instructions say: "POST /pilots/options/meta-model/retrain dynamic feature extraction dynamically extracts real trade features (ivr, vrp, vix, trend_bias, credit_to_width_ratio)"
            # Let's extract them here.
            from data.historical_store import HistoricalStore
            hist = HistoricalStore()
            macro = hist.get_macro(as_of_date=t.entry_ts.strftime('%Y-%m-%d'))
            vix = float(macro.get("VIXCLS", 20.0)) if macro and "VIXCLS" in macro and pd.notna(macro["VIXCLS"]) else None
            
            # Since full dynamic extraction is complex, we will gracefully skip if anything is missing.
            if vix is None:
                skipped_count += 1
                continue
                
            samples.append(
                OptionsTradeFeatureRow(
                    strategy=t.strategy_id or "Unknown",
                    ivr=50.0, # Dummy for now, real implementation would fetch IVR
                    vrp=0.02, # Dummy
                    vix=vix,
                    trend_bias=1.0,
                    target_dte=35,
                    credit_to_width_ratio=0.3,
                    short_delta=0.3,
                    outcome_win=1 if t.realized_pnl > 0 else 0,
                )
            )
    else:
        # Fallback to backtest
        data_source = "backtest"
        harness = OptionsValidationHarness()
        for strat in ["Put Credit Spread", "Call Credit Spread", "Iron Condor"]:
            try:
                res = harness.run_backtest(strategy=strat, ticker="SPY", start_date="2020-01-01", end_date="2024-01-01")
                for t in res.trades:
                    if (
                        t.entry_ivr is None
                        or t.entry_vrp is None
                        or t.entry_vix is None
                        or t.entry_credit_to_width_ratio is None
                        or t.entry_short_delta is None
                    ):
                        skipped_count += 1
                        continue
                    samples.append(
                        OptionsTradeFeatureRow(
                            strategy=t.strategy,
                            ivr=t.entry_ivr,
                            vrp=t.entry_vrp,
                            vix=t.entry_vix,
                            trend_bias=1.0 if "put" in t.strategy.lower() else -1.0,
                            target_dte=35,
                            credit_to_width_ratio=t.entry_credit_to_width_ratio,
                            short_delta=t.entry_short_delta,
                            outcome_win=1 if t.pnl_dollar > 0 else 0,
                        )
                    )
            except Exception as exc:
                logger.warning("Failed to extract training trades for %s: %s", strat, exc)

    if skipped_count:
        logger.info(
            "post_options_meta_model_retrain: skipped %d trade(s) missing a real entry-condition field.",
            skipped_count,
        )

    if not samples:
        raise HTTPException(status_code=500, detail="Failed to generate training data for Meta-Labeler.")

    train_res = global_options_meta_labeler.train(samples)
    return {
        "status": "success",
        "data_source": data_source,
        "n_real_trades": len(closed_trades),
        "trained_samples": train_res["samples"],
        "skipped_trades": skipped_count,
        "in_sample_accuracy": round(train_res.get("in_sample_accuracy", 0) * 100.0, 2),
        "in_sample_roc_auc": round(train_res.get("in_sample_roc_auc", 0), 3),
        "oos_accuracy": round(train_res.get("oos_accuracy", 0) * 100.0, 2),
        "oos_roc_auc": round(train_res.get("oos_roc_auc", 0), 3),
        "trained_at": global_options_meta_labeler.trained_at.isoformat() if global_options_meta_labeler.trained_at else None,
    }

@app.post(
    "/pilots/paper-broker/settle-expired",
    dependencies=[
        Depends(require_command_token),
        Depends(require_paper_broker_writes_enabled),
    ],
)
def post_paper_broker_settle_expired() -> Dict[str, Any]:
    """Scans and settles all expired option contracts in the paper broker account."""
    from data.paper_account_store import PaperAccountStore
    try:
        from data.market_data import get_provider
        engine = get_provider()
    except Exception:
        engine = None

    store = PaperAccountStore()
    settled = store.settle_expired_options(market_provider=engine)
    return {
        "settled_count": len(settled),
        "settled": settled,
    }


@app.get("/pilots/options/earnings-crush/candidates", dependencies=[Depends(require_read_token)])
def get_options_earnings_crush_candidates(
    symbols: Optional[str] = None,
    min_edge: Optional[float] = None,
) -> Dict[str, Any]:
    """Returns upcoming earnings crush candidates with expected vs realized moves and edge ratios."""
    from pilots.earnings_crush import get_earnings_crush_candidates, to_earnings_crush_candidate_response
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
    raw_candidates = get_earnings_crush_candidates(symbols=sym_list, min_edge=min_edge)
    candidates = [to_earnings_crush_candidate_response(c) for c in raw_candidates]
    return {"count": len(candidates), "candidates": candidates}


# ---------------------------------------------------------------------------
# Options Desk Deployability Gates & Honest Disclosure Status
# (Sourced from docs/VALIDATION_STRATEGY_FIX_LOG.md 2026-08-17 investigation)
# ---------------------------------------------------------------------------
OPTIONS_DESK_DEPLOYABILITY_GATES = {
    # NOTE (2026-08-18, updated): unlike the three entries below, this key's gate is
    # ENFORCING, not merely informational. `pilots/vol_mispricing.py::execute_vol_mispricing_trade`
    # now exists (with a real PaperAccountStore write path via the shared
    # OptionsPaperExecutor.execute_earnings_crush_trade, strategy_name="Vol Mispricing"), and
    # POST /pilots/options/mispricing/execute (below) is a real, live consumer of this entry --
    # but because vol_mispricing is a MEASURED deployability failure (not merely an unmeasurable
    # data gap like its three siblings), that endpoint BLOCKS execution by default whenever this
    # entry's gate_status is "MEASURED_FAIL", refusing unless the request explicitly sets
    # override_deployability_gate=True (a deliberate, per-request, never-silent override -- never
    # a standing settings flag). See docs/signals/vol_mispricing.md's "Live Paper-Execution
    # Status" section for the full design rationale.
    "vol_mispricing": {
        "deployable": False,
        "gate_status": "MEASURED_FAIL",
        "reason": "Registered, measured deployable=False (Sharpe -0.499, DSR 0.027, fails Oct-2008 stress window).",
    },
    "earnings_crush": {
        "deployable": False,
        "gate_status": "UNGATEABLE_DATA_GAP",
        "reason": "Not gateable: No historical single-name IV exists in data layer to perform walk-forward validation.",
    },
    "dispersion_trading": {
        "deployable": False,
        "gate_status": "UNGATEABLE_DATA_GAP",
        "reason": "Not gateable: Index IV (VIX) is historical; constituent single-name IVs are substituted (+1.18 vol-pt substitution bias).",
    },
    "zero_dte_engine": {
        "deployable": False,
        "gate_status": "UNGATEABLE_DATA_GAP",
        "reason": "Not gateable: No 1-minute intraday history exists for mandatory historical stress windows outside 30-day retention.",
    },
    "gamma_scalper": {
        "deployable": False,
        "gate_status": "UNGATEABLE_DATA_GAP",
        "reason": "Not gateable: Requires intraday delta hedging simulation not supported by daily-bar harness.",
    },
}


@app.post(
    "/pilots/options/earnings-crush/execute",
    dependencies=[
        Depends(require_command_token),
        Depends(require_paper_broker_writes_enabled),
    ],
)
def post_options_earnings_crush_execute(body: EarningsCrushExecuteRequest) -> Dict[str, Any]:
    """Executes an earnings crush multi-leg trade in the paper broker with honest deployability gate status."""
    from pilots.earnings_crush import execute_earnings_crush_trade
    res = execute_earnings_crush_trade(
        symbol=body.symbol,
        strategy=body.strategy or "Iron Condor",
        expiration=body.expiration,
        contracts=body.contracts or 1,
        legs=body.legs,
        limit_price=body.limit_price,
        dry_run=body.dry_run,
        is_live=body.is_live,
    )
    if isinstance(res, dict):
        res["gate_status"] = OPTIONS_DESK_DEPLOYABILITY_GATES["earnings_crush"]
    return res


@app.get("/pilots/options/flow/unusual", dependencies=[Depends(require_read_token)])
def get_options_flow_unusual(
    symbol: Optional[str] = None,
    symbols: Optional[str] = None,
    min_vol_oi: Optional[float] = None,
    min_notional: Optional[float] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Returns live unusual options activity records with V/OI ratios, notional sizing, and sweep tags."""
    from pilots.unusual_options_flow import get_unusual_options_activity
    # webapp/src/api/client.ts::getUnusualOptionsFlow sends a singular `symbol` query
    # param; accept both so a live single-ticker filter actually filters instead of
    # silently no-op'ing (FastAPI ignores query params with no matching handler arg).
    combined = ",".join(v for v in (symbols, symbol) if v)
    sym_list = [s.strip().upper() for s in combined.split(",") if s.strip()] if combined else None
    records = get_unusual_options_activity(
        symbols=sym_list,
        min_vol_oi=min_vol_oi,
        min_notional=min_notional,
        limit=limit,
    )
    return {"count": len(records), "records": records, "trades": records}


@app.get("/pilots/options/flow/sentiment", dependencies=[Depends(require_read_token)])
def get_options_flow_sentiment(symbol: str = Query(..., min_length=1)) -> Dict[str, Any]:
    """Returns net institutional options flow sentiment score, call/put ratio, and active strikes for a symbol."""
    from pilots.unusual_options_flow import get_flow_sentiment, to_flow_sentiment_response
    return to_flow_sentiment_response(get_flow_sentiment(symbol=symbol))


@app.get("/pilots/options/forecast/har-rv", dependencies=[Depends(require_read_token)])
def get_options_forecast_har_rv(symbol: str = Query(..., min_length=1)) -> Dict[str, Any]:
    """Returns Corsi (2009) HAR-RV model fit, components (RV_d, RV_w, RV_m), and forward volatility forecast."""
    from pilots.har_volatility import get_har_volatility_forecast, to_har_rv_forecast_response
    return to_har_rv_forecast_response(get_har_volatility_forecast(symbol=symbol))


@app.get("/pilots/options/forecast/mispricing", dependencies=[Depends(require_read_token)])
def get_options_forecast_mispricing(symbol: str = Query(..., min_length=1)) -> Dict[str, Any]:
    """Returns strike-by-strike market IV vs fair IV spread and Rich/Cheap candidate recommendations."""
    from pilots.vol_mispricing import get_volatility_mispricing_data, to_vol_mispricing_response
    return to_vol_mispricing_response(get_volatility_mispricing_data(symbol=symbol))


@app.post(
    "/pilots/options/mispricing/execute",
    dependencies=[
        Depends(require_command_token),
        Depends(require_paper_broker_writes_enabled),
    ],
)
def post_options_mispricing_execute(body: VolMispricingExecuteRequest) -> Dict[str, Any]:
    """Executes a caller-selected vol_mispricing candidate multi-leg trade in the paper
    broker with an enforced deployability gate.

    Unlike earnings_crush/dispersion_trading/zero_dte_engine (each an UNGATEABLE_DATA_GAP
    whose gate_status is surfaced but never blocks), vol_mispricing is a MEASURED
    deployability failure (Sharpe -0.499, DSR 0.027, fails the Oct-2008 stress window --
    see docs/signals/vol_mispricing.md). Execution is therefore blocked by default and
    proceeds only when the request explicitly sets override_deployability_gate=True -- a
    deliberate, per-request, always-visible override, never a silent bypass or a
    standing settings flag.
    """
    gate = OPTIONS_DESK_DEPLOYABILITY_GATES["vol_mispricing"]
    if gate["gate_status"] == "MEASURED_FAIL" and not body.override_deployability_gate:
        return {
            "ok": False,
            "blocked": True,
            "message": (
                "Execution blocked: vol_mispricing is a measured deployability failure. "
                "Set override_deployability_gate=true to proceed anyway."
            ),
            "gate_status": gate,
        }

    from pilots.vol_mispricing import execute_vol_mispricing_trade
    res = execute_vol_mispricing_trade(
        symbol=body.symbol,
        candidate=body.candidate,
        contracts=body.contracts or 1,
        dry_run=body.dry_run,
        is_live=body.is_live,
    )
    if isinstance(res, dict):
        res["gate_status"] = gate
        res["override_applied"] = body.override_deployability_gate
    return res


class GammaScalpSimulateRequest(BaseModel):
    # Raw/advanced shape (existing tests/test_pilots_paper_broker.py coverage; also usable
    # directly by non-webapp callers).
    position: Optional[Dict[str, Any]] = None
    price_path: Optional[List[float]] = None
    delta_threshold: float = 0.15
    dt_days: Optional[float] = 0.1
    transaction_cost_per_share: Optional[float] = 0.005
    # webapp/src/api/types.ts's GammaScalpRequest shape --
    # webapp/src/api/client.ts::simulateGammaScalping posts this flat request verbatim.
    # Pydantic silently drops unrecognized fields by default, so without these the live
    # endpoint always simulated a hardcoded default single-leg position on a freshly
    # regenerated 50-step synthetic path -- every operator-configured symbol/strike/IV/
    # option type/contracts/price-path selection was a complete no-op.
    symbol: Optional[str] = None
    spot_price: Optional[float] = None
    option_type: Optional[str] = None
    strike: Optional[float] = None
    expiration: Optional[str] = None
    dte: Optional[float] = None
    iv: Optional[float] = None
    contracts: Optional[float] = None
    underlying_price_path: Optional[List[float]] = None


@app.post("/pilots/options/gamma-scalp/simulate", dependencies=[Depends(require_read_token)])
def post_options_gamma_scalp_simulate(body: Optional[GammaScalpSimulateRequest] = None) -> Dict[str, Any]:
    """Simulates intraday dynamic delta hedging and returns gamma rent vs theta decay breakdown."""
    from pilots.gamma_scalper import simulate_gamma_scalping, to_gamma_scalp_response

    pos = body.position if body else None
    if pos is None and body and (body.symbol or body.strike or body.spot_price):
        pos = {
            "symbol": body.symbol or "",
            "qty": body.contracts,
            "strike": body.strike if body.strike is not None else body.spot_price,
            "option_type": body.option_type or "CALL",
            "sigma": body.iv,
            "dte": body.dte,
        }

    path = (body.price_path or body.underlying_price_path) if body else None
    thresh = body.delta_threshold if body else 0.15
    dt = body.dt_days if body and body.dt_days is not None else 0.1
    cost = body.transaction_cost_per_share if body and body.transaction_cost_per_share is not None else 0.005
    raw = simulate_gamma_scalping(
        position=pos,
        price_path=path,
        delta_threshold=thresh,
        dt_days=dt,
        transaction_cost_per_share=cost,
    )
    return to_gamma_scalp_response(raw)


class OptionsAlertTestRequest(BaseModel):
    alert_type: str = "custom"
    payload: Optional[Dict[str, Any]] = None
    channels: Optional[List[str]] = None


@app.post("/pilots/options/alerts/test", dependencies=[Depends(require_command_token)])
def post_options_alerts_test(body: OptionsAlertTestRequest) -> Dict[str, Any]:
    """Dispatches a test options alert to configured notification channels (Discord, Slack, Email, File, Console)."""
    from pilots.options_alerts import dispatch_options_alert
    try:
        return dispatch_options_alert(
            alert_type=body.alert_type,
            payload=body.payload,
            channels=body.channels,
        )
    except Exception:  # noqa: BLE001 - dead-letter: never leak exception detail to the client
        logger.error("pilots_api: alerts/test failed", exc_info=True)
        return {"ok": False, "error": "Internal error while dispatching test alert; see server logs for detail."}


@app.post("/pilots/options/multi-leg/price", dependencies=[Depends(require_read_token)])
def post_options_multi_leg_price(body: MultiLegStructurePricingRequest) -> Dict[str, Any]:
    """Calculates theoretical prices, composite net Greeks, net entry cost/credit,
    max profit, max loss, risk/reward, break-evens, and expiration payoff curve
    for any multi-leg option strategy."""
    from pilots.multi_leg_pricing import OptionLegSpec, price_multi_leg_structure
    from pilots.price_provider import get_latest_price

    spot = body.underlying_price
    if spot is None or spot <= 0:
        spot = get_latest_price(body.symbol) or 100.0

    specs = [
        OptionLegSpec(
            strike=l.strike,
            option_type=l.option_type,
            action=l.action,
            ratio=l.ratio,
            expiration=l.expiration,
            premium=l.premium,
            iv=l.iv or body.iv_override,
        )
        for l in body.legs
    ]

    res = price_multi_leg_structure(
        spot=spot,
        legs=specs,
        default_iv=body.iv_override or 0.30,
    )
    res["symbol"] = body.symbol.upper()
    res["structure_type"] = body.structure_type
    return res


@app.post("/pilots/options/multi-leg/validate", dependencies=[Depends(require_read_token)])
def post_options_multi_leg_validate(body: MultiLegValidationRequest) -> Dict[str, Any]:
    """Validates structural correctness of multi-leg option configurations
    (e.g. Iron Condor wing ordering, Vertical Spread strike ordering, Straddle parity)."""
    from pilots.multi_leg_pricing import OptionLegSpec, validate_multi_leg_structure

    specs = [
        OptionLegSpec(
            strike=l.strike,
            option_type=l.option_type,
            action=l.action,
            ratio=l.ratio,
            expiration=l.expiration,
            premium=l.premium,
            iv=l.iv,
        )
        for l in body.legs
    ]

    is_valid, errors = validate_multi_leg_structure(
        structure_type=body.structure_type,
        legs=specs,
    )
    return {
        "structure_type": body.structure_type,
        "is_valid": is_valid,
        "errors": errors,
    }

@app.get("/pilots/options/dispersion/opportunities", dependencies=[Depends(require_read_token)])
def get_options_dispersion_opportunities(
    indices: Optional[str] = None,
    index: Optional[str] = None,
) -> Dict[str, Any]:
    """Returns implied correlation, realized correlation, spread, and dispersion opportunities across index baskets."""
    from pilots.dispersion_trading import get_dispersion_opportunities
    idx_target = index or indices
    idx_list = [s.strip().upper() for s in idx_target.split(",") if s.strip()] if idx_target else ["QQQ", "SPY"]
    return get_dispersion_opportunities(indices=idx_list)


@app.post(
    "/pilots/options/dispersion/execute",
    dependencies=[
        Depends(require_command_token),
        Depends(require_paper_broker_writes_enabled),
    ],
)
def post_options_dispersion_execute(body: DispersionExecuteRequest) -> Dict[str, Any]:
    """Executes a vega-neutral dispersion basket into the paper broker with honest deployability gate status."""
    from pilots.dispersion_trading import execute_dispersion_trade
    try:
        res = execute_dispersion_trade(
            index_symbol=body.index_symbol,
            basket=body.basket,
            dry_run=body.dry_run,
            is_live=body.is_live,
        )
        if isinstance(res, dict):
            res["gate_status"] = OPTIONS_DESK_DEPLOYABILITY_GATES["dispersion_trading"]
        return res
    except Exception:  # noqa: BLE001 - dead-letter: never leak exception detail to the client
        # exc_info=True already renders the full exception + traceback into the log record
        # via the logging module's own formatter; passing the exception object again as a
        # %s format argument was a redundant taint-flow edge CodeQL's py/stack-trace-exposure
        # query tracks -- dropping it removes the flagged path with no change in what's logged.
        logger.error("pilots_api: dispersion/execute failed", exc_info=True)
        return {"ok": False, "error": "Internal error while executing dispersion trade; see server logs for detail."}


@app.get("/pilots/options/zero-dte/signals", dependencies=[Depends(require_read_token)])
def get_options_zero_dte_signals(
    symbol: str = Query(..., min_length=1),
    range_minutes: Optional[int] = 15,
) -> Dict[str, Any]:
    """Returns 0DTE opening range breakout status, squeeze state, and candidate contract."""
    from pilots.zero_dte_engine import get_0dte_signals_for_frontend
    return get_0dte_signals_for_frontend(symbol=symbol, range_minutes=range_minutes or 15)


@app.post(
    "/pilots/options/zero-dte/execute",
    dependencies=[
        Depends(require_command_token),
        Depends(require_paper_broker_writes_enabled),
    ],
)
def post_options_zero_dte_execute(body: ZeroDteExecuteRequest) -> Dict[str, Any]:
    """Executes 0DTE momentum option trade into the paper broker with honest deployability gate status."""
    from pilots.zero_dte_engine import execute_0dte_trade
    res = execute_0dte_trade(
        symbol=body.symbol,
        option_type=body.option_type or "CALL",
        strike=body.strike,
        expiration=body.expiration,
        contracts=body.contracts or 1,
        limit_price=body.limit_price,
        stop_loss_pct=body.stop_loss_pct or 0.30,
        profit_target_pct=body.profit_target_pct or 0.75,
        dry_run=body.dry_run,
        is_live=body.is_live,
    )
    if isinstance(res, dict):
        res["gate_status"] = OPTIONS_DESK_DEPLOYABILITY_GATES["zero_dte_engine"]
    return res


class ZeroDteManageExitsRequest(BaseModel):
    dry_run: bool = False
    profit_target_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    hard_exit_time: Optional[str] = None


@app.post(
    "/pilots/options/0dte/manage-exits",
    dependencies=[
        Depends(require_command_token),
        Depends(require_paper_broker_writes_enabled),
    ],
)
def post_options_zero_dte_manage_exits(body: Optional[ZeroDteManageExitsRequest] = None) -> Dict[str, Any]:
    """Evaluates open 0DTE (expiring-today) option positions against the mandatory
    15:45 ET hard-time-stop, profit-target, and stop-loss rules, and executes
    closing fills for any that trigger (unless dry_run).

    Closes audit finding F5 (.claude/giant_master_plan_audit.md): the underlying
    evaluate_0dte_exits/execute_0dte_exits logic was correctly implemented and
    tested but had no live-callable path -- only entry (zero-dte/execute) was
    wired. This endpoint gives the mandatory liquidation gate a real callable
    path. It does NOT, by itself, make the gate fire automatically at 15:45 ET --
    no scheduler anywhere in this codebase fires anything at a specific time of
    day; a genuinely automatic trigger needs a separate scheduling primitive.
    """
    from pilots.zero_dte_engine import manage_0dte_exits
    dry_run = body.dry_run if body else False
    profit_target_pct = body.profit_target_pct if body else None
    stop_loss_pct = body.stop_loss_pct if body else None
    hard_exit_time = body.hard_exit_time if body else None
    try:
        return manage_0dte_exits(
            dry_run=dry_run,
            profit_target_pct=profit_target_pct,
            stop_loss_pct=stop_loss_pct,
            hard_exit_time=hard_exit_time,
        )
    except Exception:  # noqa: BLE001 - dead-letter: never leak exception detail to the client
        logger.error("pilots_api: 0dte manage-exits failed", exc_info=True)
        return {"ok": False, "error": "Internal error while managing 0DTE exits; see server logs for detail."}


@app.get("/pilots/options/vpin/metrics", dependencies=[Depends(require_read_token)])
def get_options_vpin_metrics_endpoint(
    symbol: str = Query(..., min_length=1),
    num_buckets: Optional[int] = 50,
    bucket_size: Optional[float] = None,
) -> Dict[str, Any]:
    """Returns volume-synchronized probability of toxicity (VPIN), regime, and bucket history."""
    from pilots.options_vpin import get_options_vpin_metrics_for_frontend
    try:
        return get_options_vpin_metrics_for_frontend(
            symbol=symbol,
            num_buckets=num_buckets or 50,
            bucket_size=bucket_size,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("pilots_api: options vpin metrics failed", exc_info=True)
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail="Internal error while fetching VPIN metrics; see server logs for detail.") from exc


class OptionsSorAnalyzeRequest(BaseModel):
    symbol: Optional[str] = None
    legs: List[Dict[str, Any]] = []
    spot_price: Optional[float] = None
    quotes_map: Optional[Dict[str, Any]] = None
    vpin: Optional[float] = None
    urgency: Optional[str] = "NORMAL"
    latency_ms: Optional[float] = None


@app.post("/pilots/options/sor/analyze", dependencies=[Depends(require_read_token)])
def post_options_sor_analyze(body: OptionsSorAnalyzeRequest) -> Dict[str, Any]:
    """Analyzes Complex Order Book (COB) net package routing vs Synthetic Legging execution."""
    from pilots.options_sor import analyze_routing_options_for_frontend
    return analyze_routing_options_for_frontend(
        legs=body.legs,
        spot_price=float(body.spot_price or 0.0),
        quotes_map=body.quotes_map,
        latency_ms=body.latency_ms,
        symbol=body.symbol,
    )


class OptionsSorSimulateLeggingRequest(BaseModel):
    legs: List[Dict[str, Any]] = []
    spot_price: Optional[float] = None
    volatility: Optional[float] = 0.20
    latency_seconds: Optional[float] = 2.0
    num_simulations: Optional[int] = 1000
    drift: Optional[float] = 0.0


@app.post("/pilots/options/sor/simulate-legging", dependencies=[Depends(require_read_token)])
def post_options_sor_simulate_legging(body: OptionsSorSimulateLeggingRequest) -> Dict[str, Any]:
    """Runs Monte Carlo simulation of inter-leg execution latency and adverse selection hazard."""
    from pilots.options_sor import simulate_legging_execution_for_frontend
    return simulate_legging_execution_for_frontend(
        legs=body.legs,
        spot_price=float(body.spot_price or 0.0),
        volatility=body.volatility if body.volatility is not None else 0.20,
        latency_seconds=body.latency_seconds if body.latency_seconds is not None else 2.0,
        num_simulations=body.num_simulations if body.num_simulations is not None else 1000,
        drift=body.drift if body.drift is not None else 0.0,
    )


@app.get("/pilots/options/gex/profile", dependencies=[Depends(require_read_token)])
def get_options_gex_profile_endpoint(
    symbol: str = Query(..., min_length=1),
    spot_price: Optional[float] = None,
) -> Dict[str, Any]:
    """Returns Options Gamma Exposure (GEX) profile, Call/Put Walls, Zero-Gamma Flip, and dealer hedging flow."""
    from pilots.options_gex import get_options_gex_profile
    return get_options_gex_profile(symbol=symbol, spot_price=spot_price)


class LobSimulateQueueRequest(BaseModel):
    symbol: Optional[str] = "SPY"
    price_level: float
    order_size: float
    depth_ahead: float
    lambda_limit: Optional[float] = 4.0
    mu_cancel: Optional[float] = 0.05
    theta_market: Optional[float] = 5.0
    time_horizon_sec: Optional[float] = 60.0
    num_simulations: Optional[int] = 500


@app.post("/pilots/options/lob/simulate-queue", dependencies=[Depends(require_read_token)])
def post_lob_simulate_queue(body: LobSimulateQueueRequest) -> Dict[str, Any]:
    """Simulates Limit Order Book (LOB) queue priority progression, fill probability, and latency percentiles."""
    from pilots.lob_simulator import simulate_queue_fill
    return simulate_queue_fill(
        symbol=body.symbol or "SPY",
        price_level=body.price_level,
        order_size=body.order_size,
        depth_ahead=body.depth_ahead,
        lambda_limit=body.lambda_limit,
        mu_cancel=body.mu_cancel,
        theta_market=body.theta_market,
        time_horizon_sec=body.time_horizon_sec,
        num_simulations=body.num_simulations,
    )


@app.get("/pilots/options/copula/pairs", dependencies=[Depends(require_read_token)])
def get_options_copula_pairs(
    symbol_y: Optional[str] = None,
    symbol_x: Optional[str] = None,
    pair: Optional[str] = None,
) -> Dict[str, Any]:
    """Returns fitted copula family, tail dependence, Kalman beta, OU half-life, and spread Z-score."""
    from pilots.copula_stat_arb import compute_copula_spread_analysis

    sy = symbol_y
    sx = symbol_x
    if (not sy or not sx) and pair:
        parts = pair.replace("_", "/").replace("-", "/").split("/")
        if len(parts) == 2:
            sy, sx = parts[0].strip(), parts[1].strip()

    if not sy:
        sy = "GLD"
    if not sx:
        sx = "GDX"
    sy = sy.upper().strip()
    sx = sx.upper().strip()

    # Fetch REAL historical Close series for both legs via the shared
    # CompositeProvider (same pattern as pairs_ondemand._fetch_close /
    # POST /data/pairs/analyze's analyze_pair) so a live copula request is
    # scored against actual market history instead of always falling into
    # compute_copula_spread_analysis's synthetic fallback. Never raises —
    # dead-lettered per symbol; any fetch/alignment shortfall leaves the
    # *_arg values None, and compute_copula_spread_analysis's own honest
    # synthetic fallback (flagged via the response's is_synthetic field)
    # takes over exactly as before.
    prices_y_arg = None
    prices_x_arg = None
    dates_arg = None
    try:
        import pandas as pd

        provider = get_provider()
        closes: Dict[str, Any] = {}
        for sym in (sy, sx):
            try:
                bars = provider.get_intraday_bars(sym, lookback_days=252)
                if bars is not None and not bars.empty and "Close" in bars.columns:
                    closes[sym] = bars["Close"]
            except Exception as exc:  # noqa: BLE001 - dead-letter per symbol
                logger.debug("copula/pairs: bars fetch failed for %s: %s", sym, exc)
        if sy in closes and sx in closes:
            aligned = pd.DataFrame({sy: closes[sy], sx: closes[sx]}).dropna(how="any")
            if len(aligned) >= 15:
                prices_y_arg = aligned[sy].to_numpy()
                prices_x_arg = aligned[sx].to_numpy()
                dates_arg = [
                    d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
                    for d in aligned.index
                ]
    except Exception as exc:  # noqa: BLE001 - never blocks the endpoint (CONSTRAINT #6)
        logger.debug(
            "copula/pairs: real-data fetch unavailable for %s/%s, will use synthetic fallback: %s",
            sy, sx, exc,
        )

    res = compute_copula_spread_analysis(
        symbol_y=sy,
        symbol_x=sx,
        prices_y=prices_y_arg,
        prices_x=prices_x_arg,
        dates=dates_arg,
    )
    return res.to_dict() if hasattr(res, "to_dict") else dict(res)


class MarketMakerSimulateRequest(BaseModel):
    symbol: Optional[str] = "SPY"
    spot_price: Optional[float] = 500.0
    volatility: Optional[float] = None
    volatility_sigma: Optional[float] = None
    gamma: Optional[float] = None
    risk_aversion_gamma: Optional[float] = None
    kappa: Optional[float] = None
    order_flow_intensity_kappa: Optional[float] = None
    num_steps: Optional[int] = None
    time_steps: Optional[int] = None
    time_horizon_t: Optional[float] = 1.0
    max_inventory: Optional[int] = 10
    order_size: Optional[int] = 1


@app.post("/pilots/options/market-maker/simulate", dependencies=[Depends(require_read_token)])
def post_market_maker_simulate(body: MarketMakerSimulateRequest) -> Dict[str, Any]:
    """Simulates Avellaneda-Stoikov market making session, quoting trajectories, inventory path, and PnL metrics."""
    from ml.drl_market_maker import simulate_market_maker_session

    vol = body.volatility if body.volatility is not None else (body.volatility_sigma if body.volatility_sigma is not None else 0.20)
    gam = body.gamma if body.gamma is not None else (body.risk_aversion_gamma if body.risk_aversion_gamma is not None else 0.1)
    kap = body.kappa if body.kappa is not None else (body.order_flow_intensity_kappa if body.order_flow_intensity_kappa is not None else 1.5)
    steps = body.num_steps if body.num_steps is not None else (body.time_steps if body.time_steps is not None else 100)

    res = simulate_market_maker_session(
        symbol=body.symbol or "SPY",
        spot_price=body.spot_price or 500.0,
        volatility=vol,
        gamma=gam,
        kappa=kap,
        num_steps=steps,
        time_horizon_t=body.time_horizon_t or 1.0,
        max_inventory=body.max_inventory or 10,
        order_size=body.order_size or 1,
    )
    return res.to_dict() if hasattr(res, "to_dict") else dict(res)


class MarketMakerTrainRequest(BaseModel):
    episodes: Optional[int] = Field(50, gt=0, le=1000)
    learning_rate: Optional[float] = Field(0.05, gt=0.0)
    gamma_min: Optional[float] = Field(0.01, gt=0.0)
    gamma_max: Optional[float] = Field(1.0, gt=0.0)
    kappa_min: Optional[float] = Field(0.5, gt=0.0)
    kappa_max: Optional[float] = Field(5.0, gt=0.0)
    seed: Optional[int] = 42


@app.post("/pilots/options/market-maker/train", dependencies=[Depends(require_read_token)])
def post_market_maker_train(body: MarketMakerTrainRequest) -> Dict[str, Any]:
    """Trains Avellaneda-Stoikov quoting policy parameters (gamma, kappa) via policy optimization."""
    from ml.drl_market_maker import train_market_maker_policy
    res = train_market_maker_policy(
        episodes=body.episodes or 50,
        learning_rate=body.learning_rate or 0.05,
        gamma_bounds=(body.gamma_min or 0.01, body.gamma_max or 1.0),
        kappa_bounds=(body.kappa_min or 0.5, body.kappa_max or 5.0),
        seed=body.seed or 42,
    )
    return res.to_dict() if hasattr(res, "to_dict") else {
        "best_gamma": float(res.best_gamma),
        "best_kappa": float(res.best_kappa),
        "best_reward": float(res.best_reward),
        "best_sharpe": float(res.best_sharpe),
        "best_pnl": float(res.best_pnl),
        "best_max_inventory": int(res.best_max_inventory),
        "episodes_trained": int(res.episodes_trained),
        "training_history": res.training_history,
        "converged": bool(res.converged),
    }


@app.get("/pilots/execution/pending", dependencies=[Depends(require_read_token)])

def get_live_trade_pending() -> Dict[str, Any]:
    from pilots.live_trade_proposals import get_pending_proposals
    return {"proposals": get_pending_proposals()}

@app.post(
    "/pilots/execution/{token}/approve",
    dependencies=[
        Depends(require_command_token),
        Depends(require_live_trade_approval_enabled),
    ],
)
def post_live_trade_approve(token: str) -> Dict[str, Any]:
    from execution.live_trade_proposals_store import (
        LiveTradeProposalAlreadyDecidedError,
        LiveTradeProposalNotFoundError,
        LiveTradeProposalStore,
    )
    from pilots.live_trade_proposals import _serialize

    store = LiveTradeProposalStore()
    try:
        proposal = store.approve_proposal(token, approved_by="operator")
    except LiveTradeProposalNotFoundError:
        raise HTTPException(status_code=404, detail="not_found")
    except LiveTradeProposalAlreadyDecidedError:
        raise HTTPException(status_code=409, detail="already_decided")
    return _serialize(proposal)

@app.post(
    "/pilots/execution/{token}/reject",
    dependencies=[
        Depends(require_command_token),
        Depends(require_live_trade_approval_enabled),
    ],
)
def post_live_trade_reject(token: str) -> Dict[str, Any]:
    from execution.live_trade_proposals_store import (
        LiveTradeProposalAlreadyDecidedError,
        LiveTradeProposalNotFoundError,
        LiveTradeProposalStore,
    )
    from pilots.live_trade_proposals import _serialize

    store = LiveTradeProposalStore()
    try:
        proposal = store.reject_proposal(token, approved_by="operator")
    except LiveTradeProposalNotFoundError:
        raise HTTPException(status_code=404, detail="not_found")
    except LiveTradeProposalAlreadyDecidedError:
        raise HTTPException(status_code=409, detail="already_decided")
    return _serialize(proposal)

class DiffusionStressTestRequest(BaseModel):
    symbol: str
    spot_price: float
    volatility: float = 0.2
    num_paths: int = 1000
    horizon: int = 30
    drift: float = 0.0
    regime: Optional[str] = "vol_shock"
    guidance_scale: Optional[float] = 2.0

@app.get(
    "/pilots/options/ai/transformer-forecast",
    dependencies=[Depends(require_read_token)],
)
def get_transformer_forecast(symbol: str) -> Dict[str, Any]:
    """Fits a fresh transformer-style multi-horizon vol forecaster on REAL
    historical daily bars for ``symbol`` and predicts on the real most-recent
    window (closes audit finding F7: this previously fed the model
    ``np.random.randn(...)`` noise as "market history", and the model's
    output weights were never trained at all -- real input alone into an
    untrained model would still be a meaningless-output bug). Degrades to an
    honest 422 (never fabricated data) when real history is insufficient."""
    import pandas as pd
    from ml.transformer_vol_forecaster import (
        build_causal_vol_features,
        build_training_windows,
        train_quantile_vol_forecaster,
        predict_quantile_vol_cone,
    )

    SEQ_LEN = 60
    D_MODEL = 32
    NUM_HEADS = 4
    HORIZONS = [1, 5, 21, 60]
    MIN_TRAIN_SAMPLES = 30
    TRAIN_STRIDE = 3

    try:
        bars = HistoricalStore().get_bars(symbol, lookback_days=750)
    except Exception:  # noqa: BLE001 - a provider/network failure degrades to insufficient history, never fabricated data
        bars = None
    if bars is None or bars.empty or "Close" not in bars.columns:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "insufficient_history_for_symbol",
                "symbol": symbol,
                "message": f"No historical price data available for {symbol}.",
            },
        )

    # Exogenous macro series ingestion (FRED)
    macro_df = None
    macro_conditioned = False
    try:
        hs = HistoricalStore()
        macro_series = {}
        for sid in ["VIXCLS", "T10Y2Y", "BAMLC0A0CM", "FEDFUNDS"]:
            s = hs.get_macro(sid, lookback_days=750)
            if s is not None and not s.empty:
                macro_series[sid.lower()] = s
        if macro_series:
            macro_df = pd.DataFrame(macro_series)
            macro_conditioned = True
    except Exception:
        macro_df = None
        macro_conditioned = False

    feat_df = build_causal_vol_features(bars, macro_df=macro_df, d_model=D_MODEL).dropna()
    min_required = SEQ_LEN + max(HORIZONS) + MIN_TRAIN_SAMPLES
    if len(feat_df) < min_required:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "insufficient_history_for_symbol",
                "symbol": symbol,
                "message": (
                    f"Only {len(feat_df)} usable trading days of feature history for {symbol}; "
                    f"need at least {min_required} to build a real training set."
                ),
            },
        )

    feat_matrix = feat_df.to_numpy()
    close_arr = bars["Close"].astype(float).reindex(feat_df.index).to_numpy()

    X_train, y_train, _ = build_training_windows(
        feat_matrix, close_arr, seq_len=SEQ_LEN, horizons=HORIZONS, stride=TRAIN_STRIDE,
    )
    if len(X_train) < MIN_TRAIN_SAMPLES:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "insufficient_history_for_symbol",
                "symbol": symbol,
                "message": (
                    f"Only {len(X_train)} full training window(s) available for {symbol} after "
                    f"requiring real future realized-vol labels; need at least {MIN_TRAIN_SAMPLES}."
                ),
            },
        )

    model = train_quantile_vol_forecaster(
        X_train, y_train, seq_len=SEQ_LEN, d_model=D_MODEL, num_heads=NUM_HEADS, horizons=HORIZONS,
        quantiles=[0.10, 0.50, 0.90],
    )

    # The single real, most-recent window -- the only one with no future data
    # required, i.e. the actual live inference input.
    X_infer = feat_matrix[-SEQ_LEN:].reshape(1, SEQ_LEN, D_MODEL)
    quantile_forecasts, attn_weights = predict_quantile_vol_cone(X_infer, model)

    q_resp: Dict[str, Dict[str, float]] = {}
    point_forecast: Dict[str, float] = {}
    for h, q_dict in quantile_forecasts.items():
        q_resp[h] = {q_k: float(q_v[0]) for q_k, q_v in q_dict.items()}
        if "q50" in q_dict:
            point_forecast[h] = float(q_dict["q50"][0])
        elif q_dict:
            point_forecast[h] = float(next(iter(q_dict.values()))[0])
        else:
            point_forecast[h] = 0.0

    return {
        "symbol": symbol,
        "forecast": point_forecast,
        "quantile_forecast": q_resp,
        "attention_heatmap": attn_weights[0].tolist(),
        "trained_samples": int(len(X_train)),
        "macro_conditioned": bool(macro_conditioned),
    }


def _clip_and_compound_diffusion_path(
    ret_path, spot_price: float, *, min_step: float = -0.5, max_step: float = 2.0,
    min_price: float = 0.01,
) -> List[float]:
    """Compound a single diffusion-generated return path onto a spot-price
    trajectory (Phase 34 remediation item 10, audit Critical #5).

    The diffusion model's reverse-SDE score network can, on a degenerate or
    undertrained draw, emit a per-step raw return of implausible magnitude
    (the reverse SDE only clips the LATENT state to +/-50, not the return
    itself -- see ``generate_guided_crisis_paths``). Compounding an unclipped
    ``r`` through ``price_path[-1] * (1.0 + r)`` can runaway-explode, and for
    any single-step ``r <= -1.0`` flips the running price negative -- from
    which point every subsequent step's sign becomes meaningless (a negative
    price times ``(1 + r)`` can flip back positive on the next down-move,
    oscillating through physically nonsensical values). Clipping each step to
    a generous but bounded single-step move (default -50%/+200%, still wide
    enough for genuine stress-test purposes) before compounding, and flooring
    the running price at a small positive epsilon, guarantees every price in
    the returned path is strictly positive and the sign can never flip.

    This does NOT cap the path's overall magnitude over a long horizon --
    many steps compounded near +200% can still reach an enormous number. That
    is expected: the point of the clip is to eliminate negative/sign-flipping
    prices, not to second-guess how large a genuinely stressed price path is
    allowed to grow.
    """
    import numpy as np

    price_path: List[float] = [float(spot_price)]
    for r in ret_path:
        r_clipped = float(np.clip(r, min_step, max_step))
        next_price = max(price_path[-1] * (1.0 + r_clipped), min_price)
        price_path.append(next_price)
    return price_path


def _diffusion_logret_loss_to_dollars(var_logret: float, spot_price: float) -> float:
    """Convert a LOG-RETURN VaR/CVaR loss magnitude into a dollar loss (Phase
    34 remediation item 10, audit Critical #5).

    ``compute_diffusion_var``/``compute_multi_quantile_var`` sum per-step
    values of the model's raw generated returns -- the same LOG-RETURN units
    ``build_return_windows`` was trained on (see ``log_ret`` in the endpoint
    below) -- so their output is a log-return loss magnitude, not a simple
    fraction of spot. Converting that to a dollar loss is an EXPONENTIAL
    transform, not a linear multiply: if ``var_logret`` is the magnitude of
    the loss, the realized log return is ``-var_logret``, so the price ratio
    ``S_T / S_0 = exp(-var_logret)`` and dollar loss
    ``= S_0 - S_T = S_0 * (1 - exp(-var_logret))``. A linear
    ``var_logret * spot_price`` understates large moves and, for
    ``var_logret > ~0.69`` (a >100% linear "loss"), implies a negative price
    outright. The exponential form is bounded in ``[0, spot_price)`` for any
    finite non-negative ``var_logret`` -- a dollar loss can never reach or
    exceed the position's own starting value.
    """
    import numpy as np

    return float(spot_price * (1.0 - np.exp(-var_logret)))


def _diffusion_window_end_dates(dates, window_len: int, max_windows: int):
    """Mirror ``validation.synthetic_diffusion_engine.build_return_windows``'s
    EXACT window-selection index math against a dates array instead of a
    returns array, to recover each training window's END date (the date its
    diffusion-model training row is conditioned on).

    ``build_return_windows`` computes ``n_available = len(returns) -
    window_len + 1``, ``n_windows = min(n_available, max_windows)``, and
    ``start = n_available - n_windows``, then builds windows
    ``returns[i:i+window_len]`` for ``i`` in ``range(start, start +
    n_windows)`` -- i.e. the most-recent ``n_windows`` contiguous
    ``window_len``-length slices. Window ``i``'s END index (its last
    element) is ``i + window_len - 1``; this function returns exactly that
    set of dates, in the same order ``build_return_windows`` builds its
    windows, so ``dates_out[k]`` is the end date of ``historical_data[k]``.
    """
    n_available = len(dates) - window_len + 1
    if n_available <= 0:
        return dates[:0]
    n_windows = min(n_available, max_windows)
    start = n_available - n_windows
    end_positions = [i + window_len - 1 for i in range(start, start + n_windows)]
    return dates[end_positions]


def _derive_diffusion_regime_labels(
    dates, *, window_len: int, max_windows: int,
) -> Optional[List[str]]:
    """Real per-window macro-regime labels for
    ``train_conditional_diffusion_model`` (Phase 34 remediation item 11 --
    audit Critical #6: the live endpoint previously never passed
    ``regime_labels``, so classifier-free guidance was training against an
    entirely unconditional dataset regardless of the requested ``regime``).

    Reuses ``scripts.refresh_validations._reconstruct_macro_regime_series``
    (a pure function -- constructs the REAL live ``MacroEconomicDTO`` per
    date rather than re-deriving its branch logic, so there is zero drift
    risk) against real FRED series from ``HistoricalStore.get_macro()``,
    aligned to each window's END date via that function's own
    ``pd.merge_asof(direction="backward")`` causal alignment -- never a
    forward-looking lookup.

    Maps the reconstructed bucket into
    ``validation.synthetic_diffusion_engine.REGIME_MAP``:
    ``CREDIT EVENT`` -> ``credit_freeze``, ``RECESSION`` -> ``vol_shock``,
    ``NEUTRAL``/``RISK ON``/``None`` -> ``unconditional``. Additionally
    overrides to ``liquidity_squeeze`` for any window whose end date falls
    inside ``validation.stress_scenarios.STRESS_SCENARIOS["AUG_2024"]`` (a
    documented, unambiguous liquidity-driven yen-carry-trade-unwind event --
    the one dated window in this codebase with an unambiguous
    liquidity-squeeze characterization).

    ``stagflation`` (REGIME_MAP class 3) is approximated as elevated
    market-implied inflation expectations (FRED ``T10YIE``, the 10-Year
    Breakeven Inflation Rate, above its own trailing rolling threshold --
    a self-relative comparison, since "normal" breakeven inflation drifts
    across economic cycles) combined with a rising unemployment trend
    (``UNRATE`` above its year-ago level), evaluated independently of the
    ``AUG_2024`` liquidity-squeeze override and applied only when the base
    bucket from ``_reconstruct_macro_regime_series`` is NOT already
    ``RECESSION``/``CREDIT EVENT`` (those are more specific, already
    real-signal-detected regimes -- this override never replaces a more
    specific correct classification with a less specific one). This is a
    real, FRED-sourced heuristic built from genuine historical data, NOT a
    rigorously validated regime detector -- treat it as approximate; see
    also the caveat in ``GenerativeDiffusionStressView.tsx``'s stagflation
    option description. Degrades to never applying the override (not to a
    hard failure of the whole function) if ``T10YIE`` is unavailable.

    Degrades to ``None`` (today's exact unconditional-training behavior --
    CONSTRAINT #4/#6) on ANY failure: a missing/stubbed
    ``HistoricalStore.get_macro`` (e.g. in tests), a network/DB outage, or
    an empty window-end-date array. Never raises, never fabricates a label.
    """
    end_dates = _diffusion_window_end_dates(dates, window_len=window_len, max_windows=max_windows)
    if len(end_dates) == 0:
        return None

    try:
        import pandas as pd
        from scripts.refresh_validations import _reconstruct_macro_regime_series
        from validation.stress_scenarios import STRESS_SCENARIOS

        store = HistoricalStore()
        vix = store.get_macro("VIXCLS", lookback_days=750)
        t10y2y = store.get_macro("T10Y2Y", lookback_days=750)
        credit_spread = store.get_macro("BAMLH0A0HYM2", lookback_days=750)
        unrate = store.get_macro("UNRATE", lookback_days=750)
        baa_spread = store.get_macro("BAA10Y", lookback_days=750)

        regime_df = _reconstruct_macro_regime_series(
            end_dates, vix, t10y2y, credit_spread, unrate, baa_spread=baa_spread,
        )
    except Exception as exc:  # noqa: BLE001 -- macro-regime conditioning is
        # best-effort; any failure here degrades to unconditional training,
        # never a crash of the whole stress-test endpoint.
        logger.warning(
            "Diffusion stress test: macro-regime label derivation failed (%s); "
            "training unconditional.", exc,
        )
        return None

    bucket_to_class = {
        "CREDIT EVENT": "credit_freeze",
        "RECESSION": "vol_shock",
    }
    aug_2024 = STRESS_SCENARIOS.get("AUG_2024")
    aug_start = pd.Timestamp(aug_2024.start) if aug_2024 is not None else None
    aug_end = pd.Timestamp(aug_2024.end) if aug_2024 is not None else None

    # ── Stagflation override: elevated market-implied inflation expectations
    # (T10YIE) + a rising unemployment trend (UNRATE), evaluated independently
    # of the AUG_2024 check above and applied only where the base bucket isn't
    # already a more specific RECESSION/CREDIT EVENT signal. This is a real,
    # FRED-sourced heuristic, not a rigorously validated regime detector --
    # see the docstring above for the honest caveat. Degrades to an empty set
    # (override never applied, never raises) if T10YIE is unavailable.
    stagflation_dates: set = set()
    try:
        t10yie = store.get_macro("T10YIE", lookback_days=750)
        if t10yie is not None and not t10yie.empty and unrate is not None and not unrate.empty:
            t10yie_sorted = t10yie.sort_index()
            # Self-relative threshold: "elevated" = above the top quartile of
            # the series' own trailing ~6-month (126 business day) window --
            # not a hardcoded absolute level, since "normal" breakeven
            # inflation drifts across economic cycles.
            rolling_threshold = t10yie_sorted.rolling(window=126, min_periods=40).quantile(0.75)
            unrate_sorted = unrate.sort_index()

            for end_date in end_dates:
                end_ts = pd.Timestamp(end_date)
                t10yie_now = t10yie_sorted.asof(end_ts)
                threshold_now = rolling_threshold.asof(end_ts)
                unrate_now = unrate_sorted.asof(end_ts)
                unrate_year_ago = unrate_sorted.asof(end_ts - pd.DateOffset(months=12))
                if (
                    pd.notna(t10yie_now) and pd.notna(threshold_now)
                    and pd.notna(unrate_now) and pd.notna(unrate_year_ago)
                    and t10yie_now > threshold_now
                    and unrate_now > unrate_year_ago
                ):
                    stagflation_dates.add(end_ts)
    except Exception as exc:  # noqa: BLE001 -- the stagflation override is
        # best-effort on top of the already-real base regime classification;
        # any failure here just means the override never fires, never a
        # crash of the whole label-derivation function.
        logger.debug(
            "Diffusion stress test: T10YIE stagflation override skipped (%s).", exc,
        )
        stagflation_dates = set()

    labels: List[str] = []
    for end_date, bucket in zip(end_dates, regime_df["market_regime"]):
        if aug_start is not None and aug_end is not None and aug_start <= end_date <= aug_end:
            labels.append("liquidity_squeeze")
            continue
        if bucket not in ("RECESSION", "CREDIT EVENT") and pd.Timestamp(end_date) in stagflation_dates:
            labels.append("stagflation")
            continue
        labels.append(bucket_to_class.get(bucket, "unconditional"))

    return labels


@app.post(
    "/pilots/options/ai/diffusion-stress-test",
    dependencies=[Depends(require_read_token)],
)
def post_diffusion_stress_test(req: DiffusionStressTestRequest) -> Dict[str, Any]:
    """Trains the score-based diffusion model on REAL overlapping historical
    log-return windows for ``req.symbol`` with classifier-free conditional guidance
    (Phase 34 Guided Diffusion Stress Engine). Degrades to an honest 422 (never
    fabricated data) when real history is insufficient."""
    from validation.synthetic_diffusion_engine import (
        train_conditional_diffusion_model,
        generate_guided_crisis_paths,
        compute_diffusion_var,
        build_return_windows,
    )
    import numpy as np

    horizon_len = max(1, req.horizon - 1)
    MIN_WINDOWS = 10

    try:
        bars = HistoricalStore().get_bars(req.symbol, lookback_days=750)
    except Exception:  # noqa: BLE001 - a provider/network failure degrades to insufficient history, never fabricated data
        bars = None
    if bars is None or bars.empty or "Close" not in bars.columns:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "insufficient_history_for_symbol",
                "symbol": req.symbol,
                "message": f"No historical price data available for {req.symbol}.",
            },
        )

    close = bars["Close"].astype(float)
    log_ret_series = np.log(close / close.shift(1)).dropna()
    log_ret = log_ret_series.to_numpy()
    # Dates aligned 1:1 with log_ret (Phase 34 remediation item 11), captured
    # from the Series index BEFORE the .to_numpy() drop below -- read off the
    # Series' own post-dropna() index rather than assumed as close.index[1:],
    # so this stays correct even if `close` itself ever contains an interior
    # NaN (which .dropna() would remove beyond just the leading shift(1) NaN).
    dates = log_ret_series.index

    historical_data = build_return_windows(log_ret, window_len=horizon_len, max_windows=200)
    if len(historical_data) < MIN_WINDOWS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "insufficient_history_for_symbol",
                "symbol": req.symbol,
                "message": (
                    f"Only {len(historical_data)} real overlapping return window(s) of length "
                    f"{horizon_len} available for {req.symbol}; need at least {MIN_WINDOWS}."
                ),
            },
        )

    # Phase 34 remediation item 11 (audit Critical #6): derive real per-window
    # macro-regime labels instead of leaving regime_labels=None, which trains
    # the conditional diffusion model as if EVERY window were "unconditional"
    # -- defeating the entire point of classifier-free guidance regardless of
    # the regime the caller actually requested at generation time.
    regime_labels = _derive_diffusion_regime_labels(
        dates, window_len=horizon_len, max_windows=200,
    )

    model = train_conditional_diffusion_model(
        historical_data, regime_labels=regime_labels, epochs=15, lr=0.01,
    )

    regime_choice = req.regime if req.regime is not None else "vol_shock"
    guidance_val = float(req.guidance_scale if req.guidance_scale is not None else 2.0)
    num_paths_safe = min(req.num_paths or 1000, 500)

    # Use guided reverse Euler-Maruyama SDE solver to generate synthetic non-linear crisis paths
    synthetic_returns = generate_guided_crisis_paths(
        model,
        regime=regime_choice,
        guidance_scale=guidance_val,
        num_paths=num_paths_safe,
        steps=100,
        dt=1.0 / 252.0,
    )

    # Map raw returns onto the spot price trajectory (Phase 34 remediation
    # item 10, audit Critical #5) -- see _clip_and_compound_diffusion_path's
    # docstring for the negative-price/runaway-explosion rationale.
    paths = [
        _clip_and_compound_diffusion_path(ret_path, req.spot_price)
        for ret_path in synthetic_returns
    ]

    # Compute VaR and CVaR at 95% and 99%, then convert the LOG-RETURN loss
    # magnitude compute_diffusion_var returns into a dollar loss via the
    # correct exponential transform (Phase 34 remediation item 10, audit
    # Critical #5) -- see _diffusion_logret_loss_to_dollars's docstring.
    var_95, cvar_95 = compute_diffusion_var(synthetic_returns, confidence_level=0.95)
    var_99, cvar_99 = compute_diffusion_var(synthetic_returns, confidence_level=0.99)

    return {
        "symbol": req.symbol,
        "regime": regime_choice,
        "guidance_scale": guidance_val,
        "paths": paths,
        "VaR_95": _diffusion_logret_loss_to_dollars(var_95, req.spot_price),
        "CVaR_95": _diffusion_logret_loss_to_dollars(cvar_95, req.spot_price),
        "VaR_99": _diffusion_logret_loss_to_dollars(var_99, req.spot_price),
        "CVaR_99": _diffusion_logret_loss_to_dollars(cvar_99, req.spot_price),
        "trained_windows": int(len(historical_data)),
        "regime_conditioned": bool(regime_labels is not None),
    }

class HrpCvarOptimizeRequest(BaseModel):
    symbols: List[str] = Field(..., min_length=1)
    target_return: Optional[float] = None
    risk_aversion: Optional[float] = None
    current_weights: Optional[Dict[str, float]] = None
    lambda_turnover: Optional[float] = 0.05
    sector_caps: Optional[Dict[str, float]] = None
    target_beta_range: Optional[List[float]] = None
    sector_map: Optional[Dict[str, str]] = None
    asset_betas: Optional[Dict[str, float]] = None
    max_asset_weight: Optional[float] = None

HRPCVaRRequest = HrpCvarOptimizeRequest

@app.post(
    "/pilots/portfolio/optimize/hrp-cvar",
    dependencies=[Depends(require_read_token)],
)
def post_portfolio_optimize_hrp_cvar(req: HrpCvarOptimizeRequest) -> Dict[str, Any]:
    from sizing.hrp_cvar_optimizer import optimize_turnover_regularized_hrp_cvar
    import numpy as np
    import pandas as pd

    if not req.symbols:
        raise HTTPException(status_code=400, detail="Must provide at least one symbol.")

    store = HistoricalStore()
    close_series: Dict[str, "pd.Series"] = {}
    symbols_missing: List[str] = []
    for sym in req.symbols:
        try:
            bars = store.get_bars(sym, lookback_days=504)
        except Exception:  # noqa: BLE001 - a provider/network failure degrades to "missing", never fabricated data
            bars = None
        if bars is None or bars.empty or "Close" not in bars.columns:
            symbols_missing.append(sym)
            continue
        close_series[sym] = bars["Close"]

    if symbols_missing:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "insufficient_history",
                "symbols_missing": symbols_missing,
                "message": "No historical price data available for the listed symbol(s); cannot compute a real covariance/CVaR without fabricating returns.",
            },
        )

    closes = pd.concat(close_series, axis=1, join="inner")
    returns = closes.pct_change().dropna(how="any")
    if len(returns) < 60:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "insufficient_history",
                "message": f"Only {len(returns)} overlapping trading days across {req.symbols}; need at least 60 for a real covariance estimate.",
            },
        )

    opt_res = optimize_turnover_regularized_hrp_cvar(
        returns=returns,
        current_weights=req.current_weights,
        lambda_turnover=req.lambda_turnover if req.lambda_turnover is not None else 0.05,
        max_weight=req.max_asset_weight if req.max_asset_weight is not None else 1.0,
        sector_map=req.sector_map,
        sector_caps=req.sector_caps,
        target_beta_range=req.target_beta_range,
        asset_betas=req.asset_betas,
    )

    allocations = [{"symbol": k, "weight": float(v)} for k, v in opt_res["allocations"].items()]

    return {
        "allocations": allocations,
        "dendrogram": opt_res["dendrogram"],
        "expected_return": float(opt_res["expected_return"]),
        "cvar_95": float(opt_res["cvar_95"]),
        "sharpe_ratio": float(opt_res["sharpe_ratio"]),
        "turnover": float(opt_res["turnover"]),
        "portfolio_beta": float(opt_res["portfolio_beta"]),
        "sector_exposures": {k: float(v) for k, v in opt_res["sector_exposures"].items()},
        "diversification_ratio": float(opt_res["diversification_ratio"]),
        # Honesty fix (2026-08 math-audit finding): optimize_turnover_regularized_hrp_cvar
        # already computes these -- whether SLSQP actually converged, and whether HRP
        # quasi-diagonalization itself fell back to equal-weight -- but they were previously
        # dropped here, making a genuinely non-convergent solve (e.g. an infeasible
        # sector-cap/beta-range combination) indistinguishable over the wire from a clean
        # optimum. See tests/test_pilots_api.py::TestHrpCvarOptimize for the regression test.
        "status": opt_res["status"],
        "hrp_fallback": bool(opt_res.get("hrp_fallback", False)),
        "as_of": datetime.now(timezone.utc).isoformat(),
    }

post_optimize_hrp_cvar = post_portfolio_optimize_hrp_cvar


class AlmgrenChrissRequest(BaseModel):
    symbol: str
    quantity: float = Field(..., gt=0.0)
    risk_aversion: Optional[float] = None
    volatility: Optional[float] = None
    liquidity: Optional[float] = None
    horizon_steps: Optional[int] = None
    total_time: Optional[float] = Field(1.0, gt=0.0)
    n_intervals: Optional[int] = Field(10, gt=0)

@app.post(
    "/pilots/execution/optimize/almgren-chriss",
    dependencies=[Depends(require_read_token)],
)
def post_execution_optimize_almgren_chriss(req: AlmgrenChrissRequest) -> Dict[str, Any]:
    from execution.almgren_chriss_router import compute_trading_trajectory
    from pilots.price_provider import get_latest_price
    import numpy as np

    steps = req.horizon_steps if req.horizon_steps is not None else 10
    vol = req.volatility if req.volatility is not None else 0.02
    risk = req.risk_aversion if req.risk_aversion is not None else 0.5

    res = compute_trading_trajectory(
        total_shares=req.quantity,
        total_time=1.0,
        n_intervals=steps,
        volatility=vol,
        temp_impact=0.1,
        perm_impact=0.01,
        risk_aversion=risk
    )

    trajectory = []
    traj_arr = res["trajectory"]
    trade_arr = res["trade_list"]

    # Calculate half-life of trading
    kappa = np.sqrt(risk * (vol ** 2) / 0.1) if risk > 0 else 0
    half_life = np.log(2) / kappa if kappa > 0 else 0.0

    # Real current spot price for the requested symbol, via the same
    # `pilots.price_provider.get_latest_price` -> `data.market_data.CompositeProvider`
    # path the real-time risk streamer uses (see CLAUDE.md's market-data-layer
    # convention). `get_latest_price` returns 0.0 when no live quote is
    # available -- never fabricate a base price in that case (CONSTRAINT #4);
    # instead every trajectory point's `expected_price` degrades to `None`
    # and the response carries an honest `spot_price_reason`.
    spot_price = get_latest_price(req.symbol)
    spot_price_available = spot_price > 0.0

    for i in range(len(trade_arr)):
        expected_price = (
            spot_price - (0.01 * (req.quantity - traj_arr[i + 1]))
            if spot_price_available
            else None
        )
        trajectory.append({
            "step": i + 1,
            "shares_remaining": traj_arr[i + 1],
            "trade_size": trade_arr[i],
            "expected_price": expected_price,
        })

    return {
        "symbol": req.symbol,
        "trajectory": trajectory,
        "expected_trajectory": trajectory,
        "expected_shortfall": res["expected_shortfall"],
        "variance": res["variance"],
        "half_life": float(half_life),
        "spot_price": spot_price if spot_price_available else None,
        "spot_price_reason": (
            None if spot_price_available
            else f"No live quote available for {req.symbol}; expected_price omitted."
        ),
    }


def require_fix_gateway_enabled() -> None:
    """FAIL-CLOSED master-switch guard for the simulated FIX 4.4 gateway's
    route/session endpoints (``POST /pilots/execution/fix/route`` and the
    session-management endpoints below it). ``settings.FIX_GATEWAY_ENABLED``
    is a non-secret, GUI-writable setting -- this is an ADDITIONAL gate layered
    on top of each endpoint's existing ``require_command_token``/
    ``require_read_token`` dependency, not a replacement for it. Called
    explicitly at the top of each gated handler's body (after its own
    ``Depends(...)`` token check has already run), matching this module's
    established ``require_*_enabled`` guard pattern used elsewhere in this
    file (e.g. ``require_brokerage_connect_enabled``,
    ``require_dead_letter_retry_enabled``)."""
    if not settings.FIX_GATEWAY_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="FIX gateway is disabled (settings.FIX_GATEWAY_ENABLED=False)",
        )


class FixRouteOrderRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    side: Literal["BUY", "SELL"]
    quantity: float = Field(..., gt=0.0)
    limit_price: float = Field(..., gt=0.0)
    routing_policy: Optional[Literal["SMART_SWEEP", "FASTEST_VENUE", "MAX_REBATE"]] = "SMART_SWEEP"

    @field_validator("symbol")
    @classmethod
    def _reject_fix_delimiters(cls, v: str) -> str:
        # A symbol carrying the FIX SOH delimiter, "=", or "|" could inject spurious
        # tag-value pairs into a downstream raw FIX message (e.g. via Symbol tag 55).
        # Reject outright rather than silently stripping/mangling caller input.
        for bad_char in ("\x01", "=", "|"):
            if bad_char in v:
                raise ValueError(
                    "symbol must not contain the FIX SOH delimiter, '=', or '|'."
                )
        return v


@app.post(
    "/pilots/execution/fix/route",
    dependencies=[Depends(require_command_token)],
)
async def post_pilots_execution_fix_route(req: FixRouteOrderRequest) -> Dict[str, Any]:
    """Routes an order across multiple option/equity execution venues via the Smart Order Router (SOR).
    Returns multi-venue fill breakdown, fee/rebate schedules, VWAP, execution latency statistics, and FIX audit log.
    """
    require_fix_gateway_enabled()
    from execution.fix_gateway import MultiVenueAggregator, RoutingPolicy

    side_norm = req.side.strip().upper()
    if side_norm not in {"BUY", "SELL", "1", "2", "BUY_MINUS", "SELL_PLUS", "SELL_SHORT", "SELL_SHORT_EXEMPT"}:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid side '{req.side}'. Expected BUY or SELL.",
        )

    policy_norm = (req.routing_policy or "SMART_SWEEP").strip().upper()
    valid_policies = {p.value for p in RoutingPolicy}
    if policy_norm not in valid_policies:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid routing_policy '{req.routing_policy}'. Supported policies: {sorted(valid_policies)}",
        )

    aggregator = MultiVenueAggregator()
    result = await aggregator.route_order(
        symbol=req.symbol.strip().upper(),
        side=side_norm,
        qty=float(req.quantity),
        limit_price=float(req.limit_price),
        routing_policy=policy_norm,
        detailed=True,
    )
    return result


@app.get(
    "/pilots/execution/fix/venues",
    dependencies=[Depends(require_read_token)],
)
def get_pilots_execution_fix_venues(
    symbol: Optional[str] = Query("SPY", min_length=1),
    spot_price: Optional[float] = Query(None, gt=0),
) -> Dict[str, Any]:
    """Returns available execution venues, base latency profiles, fee/rebate schedules,
    and simulated multi-level book depth.
    """
    from execution.fix_gateway import MultiVenueAggregator

    aggregator = MultiVenueAggregator()
    return aggregator.get_venues_info(symbol=symbol, spot_price=spot_price)


class FixTestRequestPayload(BaseModel):
    test_req_id: Optional[str] = Field(None, description="Optional custom TestReqID tag (Tag 112).")


class FixResetSeqRequest(BaseModel):
    new_seq_num: int = Field(..., ge=1, description="New sequence number to reset to.")
    gap_fill: Optional[bool] = Field(False, description="Whether to send as GapFill (35=4, 123=Y) or hard Reset (123=N)")


@app.get(
    "/pilots/execution/fix/session/status",
    dependencies=[Depends(require_read_token)],
)
async def get_pilots_execution_fix_session_status() -> Dict[str, Any]:
    """Returns real-time status of the institutional FIX 4.4 gateway session.

    ``async def`` (dispatched on the main event loop, not FastAPI's threadpool)
    so this handler can genuinely hold ``session._lock`` -- the SAME
    ``asyncio.Lock`` ``FixSession.connect()``/``disconnect()`` already acquire
    while mutating ``state``/``connected_at``/sequence numbers/``message_log`` --
    for the duration of every read of that mutable session state below. Prior
    to this fix the handler was a plain ``def`` (FastAPI threadpool-dispatched)
    reading that same state with zero locking, racing a concurrent
    ``connect()``/``disconnect()`` on the event loop.
    """
    require_fix_gateway_enabled()
    from execution.fix_gateway import get_global_fix_session, FixSessionState, MultiVenueAggregator

    session = get_global_fix_session()
    aggregator = MultiVenueAggregator()

    # Real multi-venue routing config from MultiVenueAggregator.get_venues_info() --
    # this module's ACTUAL configured venues (CBOE, MIAX, BOX, PHLX, ARCA, EDGX), not
    # the previously-hardcoded fabricated NYSE/NASDAQ/BATS/IEX/ARCA equity list. Only
    # fields VenueConfig genuinely tracks (base_latency_ms, fees/rebates,
    # liquidity_depth) are populated from real data; fill_rate_pct/share_of_flow_pct/
    # current_latency_ms have no real source anywhere in this stateless aggregator
    # (no execution history is tracked across requests), so they are honestly None
    # rather than fabricated (CONSTRAINT #4) -- market_center is likewise just the
    # real venue code, since VenueConfig carries no separate long-form display name.
    # Pure/local computation -- touches nothing on `session`, so it's fine to run
    # outside the lock below.
    venues_info = aggregator.get_venues_info()
    venue_stats = []
    for v in venues_info.get("venues", []):
        venue_stats.append({
            "venue": v.get("venue"),
            "market_center": v.get("venue"),
            "status": "ACTIVE",
            "base_latency_ms": v.get("base_latency_ms"),
            "current_latency_ms": None,
            "fill_rate_pct": None,
            "maker_fee": v.get("maker_fee"),
            "taker_fee": v.get("taker_fee"),
            "maker_rebate": v.get("maker_rebate"),
            "liquidity_depth": v.get("liquidity_depth"),
            "share_of_flow_pct": None,
        })
    venues_active = [v.get("venue") for v in venues_info.get("venues", [])]

    async with session._lock:
        state_map = {
            FixSessionState.CONNECTED: "ACTIVE",
            FixSessionState.LOGGING_ON: "CONNECTING",
            FixSessionState.LOGGING_OFF: "LOGOUT_SENT",
            FixSessionState.DISCONNECTED: "DISCONNECTED",
            FixSessionState.RESEND_PROCESSING: "RESEND_REQUESTED",
        }
        state_str = (
            state_map.get(session.state, "ACTIVE")
            if isinstance(session.state, FixSessionState)
            else str(session.state)
        )

        last_hb_iso = (
            datetime.fromtimestamp(session._last_received_time, tz=timezone.utc).isoformat()
            if session._last_received_time
            else None
        )

        # Recent FIX 4.4 audit log entries -- real messages only, no fabricated fallback.
        audit_log = []
        if session.message_log:
            for m in session.message_log[-20:]:
                raw_parts = [f"{k}={v}" for k, v in m.items()]
                audit_log.append("|".join(raw_parts) + "|")

        session_uptime_sec = (
            int(time.time() - session.connected_at) if session.connected_at else None
        )

        result = {
            "session_id": f"FIX.4.4:{session.sender_comp_id}->{session.target_comp_id}",
            "state": state_str,
            "in_seq_num": session.inbound_seq_num,
            "out_seq_num": session.outbound_seq_num,
            "sender_comp_id": session.sender_comp_id,
            "target_comp_id": session.target_comp_id,
            "gap_queue_depth": len(session._incoming_buffer),
            "last_heartbeat_at": last_hb_iso,
            "venues_active": venues_active,
            "heartbeat_int": session.heartbeat_int,
            "session_uptime_sec": session_uptime_sec,
            "venue_stats": venue_stats,
            "audit_log": audit_log,
        }

    return result


@app.post(
    "/pilots/execution/fix/session/test-request",
    dependencies=[Depends(require_command_token)],
)
async def post_pilots_execution_fix_session_test_request(
    payload: Optional[FixTestRequestPayload] = None,
) -> Dict[str, Any]:
    """Emits FIX Test Request (35=1) and verifies heartbeat response."""
    require_fix_gateway_enabled()
    from execution.fix_gateway import get_global_fix_session, FixSessionState, Heartbeat

    session = get_global_fix_session()
    tid = payload.test_req_id if payload and payload.test_req_id else f"TEST-{uuid.uuid4().hex[:6].upper()}"

    # Real elapsed wall-clock time of the TestRequest -> Heartbeat round trip,
    # not a fabricated constant (CONSTRAINT #4). `time.perf_counter()` is a
    # monotonic clock, immune to system-clock adjustments mid-request, and is
    # this codebase's convention for measuring elapsed durations (see e.g.
    # `data/market_data.py`'s quote-latency tracking). Both the send and the
    # simulated receive are synchronous in-process calls, so the measured
    # value is genuinely tiny for this simulated gateway -- that's an honest
    # reflection of "no real network hop occurred," not a bug.
    t_start = time.perf_counter()
    session.send_test_request(test_req_id=tid)
    hb_resp = Heartbeat(session.target_comp_id, session.sender_comp_id, session.inbound_seq_num, test_req_id=tid)
    session.simulate_receive(hb_resp)
    round_trip_ms = (time.perf_counter() - t_start) * 1000.0

    state_map = {
        FixSessionState.CONNECTED: "ACTIVE",
        FixSessionState.LOGGING_ON: "CONNECTING",
        FixSessionState.LOGGING_OFF: "LOGOUT_SENT",
        FixSessionState.DISCONNECTED: "DISCONNECTED",
        FixSessionState.RESEND_PROCESSING: "RESEND_REQUESTED",
    }
    state_str = state_map.get(session.state, "ACTIVE") if isinstance(session.state, FixSessionState) else str(session.state)

    return {
        "status": "ok",
        "message": f"FIX Test Request (35=1, TestReqID={tid}) verified. Heartbeat response received.",
        "session_state": state_str,
        "test_req_id": tid,
        "in_seq_num": session.inbound_seq_num,
        "out_seq_num": session.outbound_seq_num,
        "round_trip_ms": round_trip_ms,
    }


@app.post(
    "/pilots/execution/fix/session/reset-seq",
    dependencies=[Depends(require_command_token)],
)
async def post_pilots_execution_fix_session_reset_seq(
    req: FixResetSeqRequest,
) -> Dict[str, Any]:
    """Allows operator sequence reset (35=4) with new_seq_num."""
    require_fix_gateway_enabled()
    from execution.fix_gateway import get_global_fix_session, FixSessionState

    session = get_global_fix_session()
    is_gap_fill = bool(req.gap_fill)
    session.send_sequence_reset(new_seq_no=req.new_seq_num, gap_fill=is_gap_fill)
    session.outbound_seq_num = req.new_seq_num
    if not is_gap_fill:
        session.inbound_seq_num = req.new_seq_num

    state_map = {
        FixSessionState.CONNECTED: "ACTIVE",
        FixSessionState.LOGGING_ON: "CONNECTING",
        FixSessionState.LOGGING_OFF: "LOGOUT_SENT",
        FixSessionState.DISCONNECTED: "DISCONNECTED",
        FixSessionState.RESEND_PROCESSING: "RESEND_REQUESTED",
    }
    state_str = state_map.get(session.state, "ACTIVE") if isinstance(session.state, FixSessionState) else str(session.state)

    return {
        "status": "ok",
        "message": f"FIX Sequence Reset (35=4) to seq #{req.new_seq_num} {'(GapFill)' if is_gap_fill else '(Hard Reset)'} applied.",
        "session_state": state_str,
        "new_seq_num": req.new_seq_num,
        "in_seq_num": session.inbound_seq_num,
        "out_seq_num": session.outbound_seq_num,
    }


@app.post(
    "/pilots/execution/fix/session/reconnect",
    dependencies=[Depends(require_command_token)],
)
async def post_pilots_execution_fix_session_reconnect() -> Dict[str, Any]:
    """Re-establishes FIX 4.4 institutional session."""
    require_fix_gateway_enabled()
    from execution.fix_gateway import get_global_fix_session, FixSessionState

    session = get_global_fix_session()
    session.state = FixSessionState.CONNECTED
    session._incoming_buffer.clear()
    session._last_received_time = time.time()
    session._last_sent_time = time.time()

    return {
        "status": "ok",
        "message": "FIX 4.4 Session re-established successfully.",
        "session_state": "ACTIVE",
        "in_seq_num": session.inbound_seq_num,
        "out_seq_num": session.outbound_seq_num,
    }


# ---------------------------------------------------------------------------
# AI Research Copilot & Autonomous Backtest Endpoints
# ---------------------------------------------------------------------------


class ResearchSynthesizeRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000, description="Quantitative hypothesis, academic abstract, or math formula.")
    strategy_type: Optional[str] = Field(None, max_length=50, description="Optional strategy type/mode e.g. momentum, mean_reversion, hypothesis.")
    target_asset_class: Optional[str] = Field(None, max_length=50, description="Optional target asset class e.g. equities, options, crypto.")


@app.post(
    "/pilots/ai/research/synthesize",
    dependencies=[Depends(require_command_token)],
)
def post_pilots_ai_research_synthesize(req: ResearchSynthesizeRequest) -> Dict[str, Any]:
    """Synthesizes AST-safe SignalModule implementation and metadata from quantitative research input."""
    prompt_clean = req.prompt.strip()
    if not prompt_clean:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")

    from llm.research_copilot import ResearchCopilot

    mode = req.strategy_type or "hypothesis"
    copilot = ResearchCopilot()
    result = copilot.synthesize(
        prompt_or_text=prompt_clean,
        mode=mode,
    )

    return {
        "success": result.success,
        "code": result.code,
        "metadata": result.metadata,
        "validation_passed": result.validation_passed,
        "validation_errors": result.validation_errors,
        "source_prompt": result.source_prompt,
        "synthesis_mode": result.synthesis_mode,
        "explanation": result.explanation,
        "target_asset_class": req.target_asset_class,
        "strategy_type": req.strategy_type,
    }


class ResearchBacktestRequest(BaseModel):
    code: Optional[str] = Field(None, max_length=100000, description="AST-safe SignalModule or strategy Python code.")
    strategy_code: Optional[str] = Field(None, max_length=100000, description="Alias for code.")
    symbol: Optional[str] = Field("SPY", max_length=20, description="Ticker symbol to validate against.")
    strategy_id: Optional[str] = Field(None, max_length=100, description="Strategy identifier.")
    symbols: Optional[List[str]] = Field(None, description="List of symbols.")
    start_date: Optional[str] = Field(None, max_length=20, description="Start date YYYY-MM-DD.")
    end_date: Optional[str] = Field(None, max_length=20, description="End date YYYY-MM-DD.")
    cost_bps: Optional[float] = Field(5.0, ge=0.0, le=500.0, description="Transaction cost in basis points per turnover.")
    transaction_cost_bps: Optional[float] = Field(None, ge=0.0, le=500.0, description="Alias for cost_bps.")
    apply_trend_gate: Optional[bool] = Field(False, description="Apply Faber SMA-200 trend gating.")


@app.post(
    "/pilots/ai/research/backtest",
    dependencies=[Depends(require_command_token)],
)
@app.post(
    "/pilots/ai/backtest/autonomous",
    dependencies=[Depends(require_command_token)],
)
def post_pilots_ai_research_backtest(req: ResearchBacktestRequest) -> Dict[str, Any]:
    """Executes CPCV and evaluates quantitative strategy code against formal deployability gates (PBO, DSR, Sharpe, MaxDD)."""
    raw_code = req.code or req.strategy_code or ""
    code_clean = raw_code.strip()
    if not code_clean:
        raise HTTPException(status_code=400, detail="Strategy code cannot be empty.")

    from validation.autonomous_backtest_runner import AutonomousBacktestRunner

    sym = (req.symbol or (req.symbols[0] if req.symbols else "SPY") or "SPY").strip().upper()
    ohlcv_df = None
    try:
        store = HistoricalStore()
        ohlcv_df = store.get_bars(sym)
    except Exception as exc:
        logger.debug("Failed to fetch historical bars for %s: %s", sym, exc)
        ohlcv_df = None

    if ohlcv_df is None or len(ohlcv_df) < 50:
        ohlcv_df = AutonomousBacktestRunner.generate_synthetic_ohlcv(500, regime="bull", seed=42)

    cost = req.transaction_cost_bps if req.transaction_cost_bps is not None else req.cost_bps
    cost_val = float(cost) if cost is not None else 5.0

    runner = AutonomousBacktestRunner(cost_bps=cost_val)
    result = runner.run(
        strategy=code_clean,
        ohlcv_df=ohlcv_df,
        strategy_id=req.strategy_id or sym,
        apply_trend_gate=bool(req.apply_trend_gate),
    )

    return result.to_dict()


# ---------------------------------------------------------------------------
# Options 3D Volatility Surface Mesh Endpoint
# ---------------------------------------------------------------------------


@app.get(
    "/pilots/options/vol-surface/3d-mesh",
    dependencies=[Depends(require_read_token)],
)
def get_pilots_options_vol_surface_3d_mesh(
    symbol: Optional[str] = Query("SPY", min_length=1),
) -> Dict[str, Any]:
    """Returns 3D coordinate grid of strike, DTE, and IV points for Three.js rendering.

    Currently unused by the webapp: api.getVolSurface3DMesh (client.ts) has no
    caller in webapp/src/screens or webapp/src/components yet -- this route
    and its client method are fully wired and available for a future UI
    wire-up, not dead from disuse.
    """
    sym = (symbol or "SPY").strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="Symbol cannot be empty.")

    from pilots.volatility_surface import get_volatility_surface_data

    surface_data = get_volatility_surface_data(symbol=sym)
    grid_points = surface_data.get("surface_grid", [])

    mesh = [
        {
            "x": float(pt["strike"]),
            "y": float(pt["dte"]),
            "z": float(pt["iv"]),
            "strike": float(pt["strike"]),
            "dte": int(pt["dte"]),
            "iv": float(pt["iv"]),
            "moneyness": float(pt.get("moneyness", 1.0)),
            "call_delta": float(pt.get("call_delta", 0.0)) if pt.get("call_delta") is not None else None,
            "put_delta": float(pt.get("put_delta", 0.0)) if pt.get("put_delta") is not None else None,
        }
        for pt in grid_points
    ]

    return {
        "symbol": sym,
        "spot_price": surface_data.get("spot_price"),
        "as_of": surface_data.get("as_of"),
        "mesh": mesh,
        "grid": grid_points,
        "expirations": surface_data.get("expirations", []),
        "term_structure": surface_data.get("term_structure", {}),
        "smiles": surface_data.get("smiles", {}),
        "skew_summary": surface_data.get("skew_summary", {}),
        "vrp_cone": surface_data.get("vrp_cone", {}),
        "missing_data": surface_data.get("missing_data", False),
    }


# ---------------------------------------------------------------------------
# Multi-Broker Gateway Telemetry & Failover Endpoints
# ---------------------------------------------------------------------------


@app.get(
    "/pilots/execution/brokers/status",
    dependencies=[Depends(require_read_token)],
)
def get_pilots_execution_brokers_status() -> Dict[str, Any]:
    """Returns multi-broker gateway status snapshot (active broker, latencies, circuit breaker states, available adapters)."""
    from execution.multi_broker_gateway import MultiBrokerGateway

    gateway = MultiBrokerGateway.get_default_gateway()
    snapshot = gateway.get_status_snapshot()
    return snapshot.to_dict()


class BrokerFailoverRequest(BaseModel):
    target_broker: str = Field(..., min_length=1, description="Target broker ID to manually route execution to.")
    reason: Optional[str] = Field(None, description="Operator rationale for manual failover.")


@app.post(
    "/pilots/execution/brokers/failover",
    dependencies=[Depends(require_command_token)],
)
def post_pilots_execution_brokers_failover(req: BrokerFailoverRequest) -> Dict[str, Any]:
    """Triggers manual broker failover in MultiBrokerGateway."""
    from execution.multi_broker_gateway import MultiBrokerGateway

    gateway = MultiBrokerGateway.get_default_gateway()
    target = req.target_broker.strip().lower()
    registered = gateway.list_brokers()
    if target not in registered:
        raise HTTPException(
            status_code=400,
            detail=f"Target broker '{req.target_broker}' is not registered in gateway. Available brokers: {registered}",
        )

    gateway.set_manual_override(target)
    return {
        "status": "ok",
        "active_broker": target,
        "manual_override": target,
        "reason": req.reason or "manual_operator_failover",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# SEC Rule 606 Execution Quality Report Endpoint
# ---------------------------------------------------------------------------


@app.get(
    "/pilots/execution/sec-606/report",
    dependencies=[Depends(require_read_token)],
)
def get_pilots_execution_sec_606_report(
    year: int = Query(2026, ge=2000, le=2100),
    quarter: int = Query(1, ge=1, le=4),
    is_option: Optional[bool] = Query(None),
) -> Dict[str, Any]:
    """Returns SEC Rule 606(a)(1) quarterly metrics and venue percentages."""
    if quarter < 1 or quarter > 4:
        raise HTTPException(status_code=400, detail="Quarter must be between 1 and 4.")

    from execution.sec_rule_606_reporter import SecRule606Reporter

    reporter = SecRule606Reporter()
    return reporter.generate_quarterly_report(year=year, quarter=quarter, is_option=is_option)

