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

CORS mirrors ``state_api.py`` (``settings.CORS_ALLOWED_ORIGINS``) but allows
GET, POST and PUT (state_api is GET-only).

Honesty (CONSTRAINT #4): read endpoints 404 honestly on a cold start (no
snapshot / no account yet) and never fabricate a curve, a metric, or an equity
figure.
"""

from __future__ import annotations

import hmac
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from settings import settings

# Pilot layer (pure, persisted-state readers) + the gated follow write-path.
from pilots import (
    alerts_feed,
    catalog,
    forecast_skill,
    models,
    options,
    pairs,
    performance,
    realized,
    scoring,
    symbols,
)
from pilots.follows_store import FollowsStore
from pilots.mirror import plan_follow

# Execution / persistence — explicitly ALLOWED here (unlike state_api.py),
# forbidden only for the heavy calculation engines (see this module's AST guard
# test). ``data.historical_store`` and ``execution.kill_switch`` are imported at
# module top so tests can ``mock.patch.object(pilots_api, "HistoricalStore", ...)``.
from data.historical_store import HistoricalStore
from execution.kill_switch import GlobalKillSwitch

# Brokerage-connect credential intake — read-only verification + the dedicated,
# hard-scoped .env writer (see data/brokerage_credentials.py). Imported at
# module top (not lazily) so tests can `mock.patch.object(pilots_api, ...)`.
import data.robinhood_portfolio as robinhood_portfolio
import data.brokerage_credentials as brokerage_credentials

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


class BrokerageConnectRequest(BaseModel):
    """Body for ``POST /brokerage/connect``. Never logged (CONSTRAINT #3) —
    Pydantic's default repr is not invoked anywhere in this module's logging."""

    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    mfa_secret: str = Field(
        default="",
        description=(
            "Base32 TOTP secret. Required — interactive MFA prompting is not "
            "available over HTTP, so a login attempt with no MFA secret is "
            "treated as a verification failure."
        ),
    )


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
    about a candidate credential."""
    verified = robinhood_portfolio.verify_credentials(
        body.username, body.password, body.mfa_secret
    )
    if not verified:
        raise HTTPException(
            status_code=401,
            detail="Could not verify Robinhood credentials.",
        )
    brokerage_credentials.write_rh_credentials(body.username, body.password, body.mfa_secret)
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
    RH_USERNAME/RH_PASSWORD/RH_MFA_SECRET from ``.env`` and the process
    environment. Idempotent — safe to call when nothing is connected."""
    try:
        robinhood_portfolio.logout()
    except Exception as exc:  # noqa: BLE001 - logout failure must not block disconnect
        logger.warning("pilots_api: brokerage logout failed (ignored): %s", exc)
    brokerage_credentials.clear_rh_credentials()
    return {"connected": False}
