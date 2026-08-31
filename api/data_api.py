"""
api/data_api.py
================
STANDALONE FastAPI service exposing data-ingestion, market-data, and universe
endpoints. Consumed by the React PWA to view raw data and manage the universe.

Run standalone::

    uvicorn api.data_api:app --port 8603

Auth posture: every GET endpoint, plus the compute-only ``POST /data/pairs/*``
/ ``POST /data/options/recompute`` endpoints (no side effects — they read
market data and return a computed result, never persist anything), use
``require_token`` (``api.auth.require_read_token``, copied from
``api/state_api.py``) — a **fail-open** bearer token when
``settings.STATE_API_TOKEN`` is set, and open for zero-config local use when
unset. ``PUT /data/universe`` is the one endpoint here that actually mutates
persisted config (writes ``DEFAULT_TICKERS`` to ``.env``), so it uses
``require_write_token`` instead — always FAIL-CLOSED on ``STATE_API_TOKEN``.
``/health`` is ALWAYS open so a load-balancer / watchdog can probe without a
token. The token is NEVER logged (CONSTRAINT #3).

Honesty (CONSTRAINT #4): a value that cannot be computed degrades to ``null``
(``NaN``/``inf`` → ``null``) rather than a fabricated ``0.0``; dead-letter
resilient (CONSTRAINT #6) — a single failed fetch never crashes the service.

This module MAY import the engine/data layer (unlike ``api/state_api.py`` /
``api/control_api.py``, whose read-only purity is AST-guarded); it is a
data-facing service, not the kill-switch/daemon control plane.
"""
from __future__ import annotations

import base64
import logging
import math
from typing import Any, Dict, List, Optional
import json
import asyncio

from fastapi import Body, Depends, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from api._redact import install_redacting_exception_handler, redact_line

from dotenv import load_dotenv as _load_dotenv

from settings import ENV_PATH, settings

# Load .env before any subsequent project import that reads credentials
# (e.g. data.robinhood_portfolio). Standalone `uvicorn api.data_api:app` has
# no main()-style entry point to hook this into the way main.py/
# main_orchestrator.py/app_shell.py do, so it runs here, at true module top,
# anchored to ENV_PATH (settings.py) — a bare load_dotenv() walks UP from
# this file's directory via find_dotenv() and, in a git worktree with no
# .env of its own, silently finds a PARENT checkout's .env instead.
_load_dotenv(ENV_PATH, override=False)

from api.auth import require_read_token as require_token, require_write_token
from api.cors import LAN_TAILSCALE_ORIGIN_REGEX
from data.historical_store import HistoricalStore
from data.market_data import get_provider
from data.robinhood_portfolio import fetch_account_snapshot
from data.portfolio_sync import async_sync_now, build_sync_report
from data_engine import DataEngine
import options_ondemand
import pairs_ondemand

# ── On-demand AI generation (Section: /data/ai/*) ──────────────────────────
# Imported by NAME (not by submodule reference) so tests can monkeypatch each
# generator directly on this module's namespace, e.g.
# ``monkeypatch.setattr(data_api, "generate_for_symbol_row", fake)``.
# None of these modules import streamlit at module top (verified) and this
# file carries no AST import guard (unlike ``api/pilots_api.py`` /
# ``api/state_api.py``), so importing them here is safe and intentional.
from gui.ai_insights_panel import (
    derive_disagreement_overview,
    disagreement_summary,
    insights_status,
    latest_verdict_maps_from_cache,
)
from gui.llm_commentary_panel import commentary_status, generate_for_symbol_row
from llm.chart_insight import generate_chart_pattern_read, render_price_chart_png
from llm.research import generate_research_brief
from pilots.catalog import get_pilot as _catalog_get_pilot, list_pilots as _catalog_list_pilots
from pilots.observability import observability_summary as _pilots_observability_summary
from pilots.scoring import load_snapshot
from pilots.scoring import pilot_holdings as _pilots_pilot_holdings
from pilots.scoring import pilot_trades as _pilots_pilot_trades

logger = logging.getLogger(__name__)

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(app):
    """Start/stop the WebSocket streamer with the FastAPI process."""
    try:
        from data.websocket_streamer import start_streamer, stop_streamer
        if getattr(settings, "ALPACA_API_KEY", None):
            start_streamer()
    except Exception as _e:
        logger.warning("WebSocketStreamer startup skipped: %s", _e)
    yield
    try:
        from data.websocket_streamer import stop_streamer
        stop_streamer()
    except Exception:
        pass


app = FastAPI(
    title="InvestYo Data API",
    description="Data ingestion and market-data endpoints for the Web App.",
    version="0.1.0",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOWED_ORIGINS,
    allow_origin_regex=LAN_TAILSCALE_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["GET", "PUT", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

# Structural backstop for exception-message leakage: redacts every
# HTTPException.detail before it reaches the client, so a future endpoint
# that raises HTTPException(detail=str(exc)) directly is covered even if it
# forgets an explicit redact_line() call. See api/_redact.py.
install_redacting_exception_handler(app)

# Mount WebSocket tick router only -- NOT training_router. The training-status
# broadcast singletons (training_status_manager/_MAIN_LOOP) are only ever
# populated by api/control_api.py's own startup hook and create_job/
# stream_job_logs call sites, so a /ws/training/status route mounted here
# could never broadcast anything; see api/ws_api.py's module docstring.
try:
    from api.ws_api import tick_router, live_chat_router, risk_router
    app.include_router(tick_router)
    app.include_router(live_chat_router)
    app.include_router(risk_router)
except Exception as _ws_e:
    logger.warning("ws routers mount skipped: %s", _ws_e)


def require_ai_capability_enabled(flag_name: str, capability_label: str):
    """Return a FastAPI dependency that 403s when the named settings flag is False.

    Checked in ADDITION to ``require_token``, not instead of it -- the three
    ``/data/ai/*`` generation endpoints below call out to paid external LLM
    APIs, so an auth check alone isn't enough; a capability opt-in must also
    pass. Mirrors ``api/pilots_api.py``'s ``require_llm_writes_enabled``-style
    fail-closed dependency factories, but gates a FEATURE flag (does the
    operator want this generator to run at all) rather than a config-WRITE
    flag (can this token mutate ``.env``) -- there is no persistence/rollback
    concern here, only "should this endpoint spend money."

    Used here as the HARD master gate on all three ``/data/ai/*`` endpoints via
    ``settings.AI_GENERATION_API_ENABLED`` (see that field's docstring in
    ``settings.py``) — a SEPARATE concern from each endpoint's own per-
    capability soft-fail below (``{"available": false, "reason": "disabled"}``
    for ``LLM_COMMENTARY_ENABLED``/``OPAL_RESEARCH_ENABLED`` etc., an HONEST,
    EXPECTED response mirroring the Streamlit AI Insights tab's inline info
    caption, not an error). ``api/data_api.py`` is fail-open by design when
    ``STATE_API_TOKEN`` is unset, so a hard 403 here is the ONLY thing that
    stops these three endpoints from being remotely triggerable — paid
    external API calls — the moment an operator enables the underlying
    capability for their own Streamlit desktop use. Off by default; two
    independent kill switches exist: this flag (all three endpoints, 403) and
    each capability's own existing flag (one generator, honest soft-fail 200).
    """

    def _dependency() -> None:
        if not getattr(settings, flag_name, False):
            raise HTTPException(
                status_code=403,
                detail=f"{capability_label} is disabled ({flag_name}=false).",
            )

    return _dependency


# The master gate for all three /data/ai/* endpoints below (see
# require_ai_capability_enabled's docstring) — defined once so the flag name/
# label aren't repeated at each of the three call sites.
_require_ai_generation_enabled = require_ai_capability_enabled(
    "AI_GENERATION_API_ENABLED", "On-demand AI generation"
)


def _clean_nan(obj: Any) -> Any:
    """Recursively convert NaN/inf floats to ``None`` (JSON ``null``).

    JSON has no NaN/Infinity; emitting them yields invalid JSON. Honesty rule
    (CONSTRAINT #4): an uncomputable metric becomes ``null``, never a fabricated
    ``0.0``.
    """
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_nan(x) for x in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


@app.get("/health")
def health_check() -> Dict[str, str]:
    return {"status": "ok", "service": "data_api"}


@app.get("/data/bars/{symbol}", dependencies=[Depends(require_token)])
def get_bars(
    symbol: str, lookback_days: int = Query(252, ge=1, le=3650)
) -> List[Dict[str, Any]]:
    """Daily OHLCV bars for ``symbol`` — ``[]`` when none are available.

    Routes through ``HistoricalStore`` (incremental DB cache) with the live
    provider as the top-up source, matching the rest of the pipeline.

    ``lookback_days`` is bounded (matching the ``/data/macro/{series_id}``
    and ``/data/sentiment/history/{symbol}`` siblings below, which already
    use this exact ``Query(..., ge=1, le=3650)`` pattern) — an unbounded
    value here would eventually reach ``data.fmp_client.historical_eod``
    (via ``HistoricalStore``'s live-provider top-up →
    ``FMPProvider.get_intraday_bars``) with a multi-decade window, silently
    truncated by FMP's undocumented ~5,000-row-per-request cap rather than
    erroring (see ``docs/FMP_INTEGRATION.md``'s "Known risks" section). This
    endpoint isn't currently a call site of that cap in practice — the
    shipped webapp caller only ever sends 21/63/120/126/252 — but the bound
    closes the gap defensively rather than relying on every future caller
    behaving.
    """
    symbol = symbol.upper()
    store = HistoricalStore(readonly=True)
    provider = get_provider()
    try:
        df = store.get_bars(symbol, lookback_days=lookback_days, provider=provider)
    except Exception as exc:  # dead-letter: bad symbol / provider outage
        logger.warning("data_api: bars fetch failed for %s: %s", symbol, exc)
        return []

    if df is None or df.empty:
        return []

    df = df.reset_index()
    # The DatetimeIndex resets to a column named 'Date', 'Datetime', or 'index'.
    for candidate in ("Date", "Datetime", "index"):
        if candidate in df.columns:
            df = df.rename(columns={candidate: "date"})
            break

    records: List[Dict[str, Any]] = df.to_dict(orient="records")
    for row in records:
        val = row.get("date")
        if hasattr(val, "isoformat"):
            row["date"] = val.isoformat()
    return _clean_nan(records)


@app.post("/data/backfill/{symbol}", dependencies=[Depends(require_write_token)])
def trigger_symbol_backfill(symbol: str) -> Dict[str, Any]:
    """On-demand, user-triggered "spot data download": force a full
    ``settings.BARS_BACKFILL_DAYS`` bar backfill for one arbitrary symbol and
    PERSIST it, rather than only ever backfilling lazily as a side effect of
    some other read. Unlike ``GET /data/bars/{symbol}`` above (and every
    other symbol-detail GET in this file), this constructs ``HistoricalStore``
    in WRITE mode on purpose — every read endpoint here deliberately uses
    ``readonly=True``, whose live-fetch-but-never-persist short-circuit
    (``data/historical_store.py``'s ``_get_bars_db_path``) is exactly why a
    symbol discovered via the FMP Symbol Screener / Paper Broker Quick Trade
    never actually landed in local storage before this endpoint existed. See
    ``investyo_mcp_server.py::trigger_data_engine``, this endpoint's
    write-mode MCP-tool precedent, whose body this ports into a REST route
    the webapp can call directly.

    Never a fabricated success (CONSTRAINT #4): an unknown/unfetchable symbol
    returns HTTP 200 with ``status: "no_data"`` and ``rows_persisted: 0``, not
    a 500 — the request itself succeeded, there was just nothing to persist
    (bad ticker, provider outage, etc.). Any other unexpected failure also
    dead-letters to the same honest ``"no_data"`` shape rather than crashing
    the request (CONSTRAINT #6).
    """
    sym = symbol.upper().strip()
    if not sym:
        raise HTTPException(status_code=422, detail="symbol is required")

    try:
        store = HistoricalStore()  # write mode, deliberately not readonly=True
        df = store.get_bars(sym, lookback_days=settings.BARS_BACKFILL_DAYS, provider=get_provider())
    except Exception as exc:  # noqa: BLE001 -- dead-letter: bad symbol / provider outage / DB hiccup
        logger.warning("data_api: symbol backfill failed for %s: %s", sym, exc)
        df = None

    if df is None or df.empty:
        return {"symbol": sym, "rows_persisted": 0, "last_bar_date": None, "status": "no_data"}

    last_date = df.index[-1]
    last_str = last_date.strftime("%Y-%m-%d") if hasattr(last_date, "strftime") else str(last_date)
    return {"symbol": sym, "rows_persisted": len(df), "last_bar_date": last_str, "status": "ok"}


@app.get("/data/fundamentals/{symbol}", dependencies=[Depends(require_token)])
def get_current_fundamentals(symbol: str) -> Dict[str, Any]:
    """Current fundamental metrics for ``symbol`` (yfinance ``.info``-shaped).

    ``provider.get_fundamentals`` returns a **plain dict** and never raises
    (it degrades to ``{}``). An empty dict → 404 (honest "no coverage").
    """
    symbol = symbol.upper()
    provider = get_provider()
    fundamentals = provider.get_fundamentals(symbol) or {}
    if not fundamentals:
        raise HTTPException(status_code=404, detail=f"No fundamentals available for {symbol}")
    return _clean_nan(fundamentals)


@app.get("/data/fundamentals/{symbol}/history", dependencies=[Depends(require_token)])
def get_fundamental_history(symbol: str) -> Dict[str, Dict[str, Any]]:
    """Point-in-time fundamentals history keyed by ISO ``as_of`` date.

    ``HistoricalStore.get_fundamentals_history`` returns a **DataFrame**; it is
    converted to ``{iso_date: {metric: val}}`` here (never returned raw).
    Empty history → ``{}``.
    """
    symbol = symbol.upper()
    store = HistoricalStore(readonly=True)
    try:
        history_df = store.get_fundamentals_history(symbol)
    except Exception as exc:
        logger.warning("data_api: fundamentals history failed for %s: %s", symbol, exc)
        return {}

    if history_df is None or history_df.empty:
        return {}

    df = history_df.copy()
    if "as_of" in df.columns:
        # ISO-date string keys, drop the now-redundant column.
        df = df.set_index("as_of")
    df.index = [
        idx.isoformat() if hasattr(idx, "isoformat") else str(idx) for idx in df.index
    ]
    # Drop opaque blobs that aren't per-metric scalars.
    df = df.drop(columns=[c for c in ("raw_json",) if c in df.columns])
    return _clean_nan(df.to_dict(orient="index"))


@app.get("/data/peers/{symbol}", dependencies=[Depends(require_token)])
def get_peer_group(symbol: str) -> Dict[str, Any]:
    """On-demand FMP peer-comparison ticker group for one symbol (``/peers``),
    powering the webapp's "Suggest peers for this ticker" affordance on
    ``SymbolComparison.tsx``.

    Gated by ``settings.FMP_PEERS_ENABLED`` (default ``False``) — a
    DIFFERENT gate from ``FMP_OPTIONS_CONTEXT_ENABLED``, which already
    covers a per-cycle BATCH ``fetch_peer_group`` call across the whole
    options-matrix universe; this is a single, per-click, operator-triggered
    fetch with its own rate-limit/cadence shape (mirrors the
    ``FMP_INSIDER_ENABLED``/``FMP_SECTOR_SNAPSHOT_ENABLED`` precedent of one
    flag per call-site shape). ``fetch_peer_group`` itself already never
    raises (CONSTRAINT #6 — it degrades to ``[]`` on any failure), so the
    flag-off path and any live fetch/parse failure both degrade to an
    honest empty list + ``reason`` string here, never a 500.
    """
    sym = symbol.upper().strip()
    if not getattr(settings, "FMP_PEERS_ENABLED", False):
        return {
            "symbol": sym,
            "peers": [],
            "reason": "FMP peer-group lookup is disabled (FMP_PEERS_ENABLED=False).",
        }
    from data.fmp_feeds_market import fetch_peer_group

    try:
        peers = fetch_peer_group(sym)
    except Exception as exc:  # defensive — fetch_peer_group already dead-letters
        logger.warning("data_api: peer-group fetch failed for %s: %s", sym, exc)
        peers = []
    return {
        "symbol": sym,
        "peers": peers,
        "reason": None if peers else "No peer data available for this symbol.",
    }


@app.get("/data/symbol-search", dependencies=[Depends(require_token)])
def get_symbol_search(query: str = Query(..., min_length=1), limit: Optional[int] = Query(None)) -> Dict[str, Any]:
    """Company-name/ticker search, independent of the platform's tracked
    watchlist/pipeline universe — powers the webapp's Symbol Screener free-text
    search box.

    Gated by ``settings.FMP_SCREENER_ENABLED`` (default ``True``).
    ``data.fmp_screener.search_symbols`` never raises (CONSTRAINT #6 — it
    degrades to ``[]`` on any failure), so the flag-off path and any live
    fetch/parse failure both degrade to an honest empty list + ``reason``
    string here, never a 500.
    """
    q = query.strip()
    if not getattr(settings, "FMP_SCREENER_ENABLED", False):
        return {
            "query": q,
            "results": [],
            "reason": "Symbol search is disabled (FMP_SCREENER_ENABLED=False).",
        }
    from data.fmp_screener import search_symbols

    try:
        results = search_symbols(q, limit=limit)
    except Exception as exc:  # defensive — search_symbols already dead-letters
        logger.warning("data_api: symbol search failed for %r: %s", q, exc)
        results = []
    return {
        "query": q,
        "results": results,
        "reason": None if results else "No matching symbols found.",
    }


@app.get("/data/screener", dependencies=[Depends(require_token)])
def get_screener_results(
    sector: Optional[str] = Query(None),
    industry: Optional[str] = Query(None),
    market_cap_more_than: Optional[float] = Query(None),
    market_cap_lower_than: Optional[float] = Query(None),
    price_more_than: Optional[float] = Query(None),
    price_lower_than: Optional[float] = Query(None),
    beta_more_than: Optional[float] = Query(None),
    beta_lower_than: Optional[float] = Query(None),
    dividend_more_than: Optional[float] = Query(None),
    dividend_lower_than: Optional[float] = Query(None),
    volume_more_than: Optional[float] = Query(None),
    exchange: Optional[str] = Query(None),
    country: Optional[str] = Query(None),
    is_actively_trading: Optional[bool] = Query(None),
    exclude_funds: bool = Query(False),
    limit: Optional[int] = Query(None),
    page: Optional[int] = Query(None),
) -> Dict[str, Any]:
    """Sector/industry/market-cap/price/beta/dividend/volume stock screener,
    independent of the platform's tracked watchlist/pipeline universe —
    powers the webapp's Symbol Screener filter form. ``exclude_funds=true``
    filters out both ETFs and mutual funds client-side of the FMP call
    (``isEtf``/``isFund`` are separate FMP filter params but the operator's
    "just show me real companies" intent is one checkbox).

    Gated by ``settings.FMP_SCREENER_ENABLED`` (default ``True``).
    ``data.fmp_screener.screen_companies`` never raises (CONSTRAINT #6 — it
    degrades to ``[]`` on any failure), so the flag-off path and any live
    fetch/parse failure both degrade to an honest empty list + ``reason``
    string here, never a 500.
    """
    if not getattr(settings, "FMP_SCREENER_ENABLED", False):
        return {
            "results": [],
            "reason": "The symbol screener is disabled (FMP_SCREENER_ENABLED=False).",
        }
    from data.fmp_screener import screen_companies

    filters: Dict[str, Any] = {
        "sector": sector,
        "industry": industry,
        "marketCapMoreThan": market_cap_more_than,
        "marketCapLowerThan": market_cap_lower_than,
        "priceMoreThan": price_more_than,
        "priceLowerThan": price_lower_than,
        "betaMoreThan": beta_more_than,
        "betaLowerThan": beta_lower_than,
        "dividendMoreThan": dividend_more_than,
        "dividendLowerThan": dividend_lower_than,
        "volumeMoreThan": volume_more_than,
        "exchange": exchange,
        "country": country,
        "isActivelyTrading": is_actively_trading,
        "limit": limit,
        "page": page,
    }
    if exclude_funds:
        filters["isEtf"] = False
        filters["isFund"] = False

    try:
        results = screen_companies(**filters)
    except Exception as exc:  # defensive — screen_companies already dead-letters
        logger.warning("data_api: screener query failed: %s", exc)
        results = []
    return {
        "results": _clean_nan(results),
        "reason": None if results else "No symbols matched these filters.",
    }


@app.get("/data/screener/filters", dependencies=[Depends(require_token)])
def get_screener_filter_options() -> Dict[str, Any]:
    """Sector/industry enum lists for the Symbol Screener's filter dropdowns.

    Gated by ``settings.FMP_SCREENER_ENABLED`` (default ``True``). Never
    raises — degrades to empty lists on any failure.
    """
    if not getattr(settings, "FMP_SCREENER_ENABLED", False):
        return {"sectors": [], "industries": []}
    from data.fmp_screener import list_industries, list_sectors

    try:
        sectors = list_sectors()
    except Exception as exc:  # defensive — list_sectors already dead-letters
        logger.warning("data_api: sector list fetch failed: %s", exc)
        sectors = []
    try:
        industries = list_industries()
    except Exception as exc:  # defensive — list_industries already dead-letters
        logger.warning("data_api: industry list fetch failed: %s", exc)
        industries = []
    return {"sectors": sectors, "industries": industries}


@app.get("/data/macro", dependencies=[Depends(require_token)])
def get_macro_raw() -> Dict[str, Any]:
    """Raw current-snapshot macro dict (VIX, yield curve, Sahm, etc.)."""
    engine = DataEngine(settings.FRED_API_KEY or "")
    try:
        return _clean_nan(engine.fetch_macro_raw())
    except Exception as exc:
        logger.warning("data_api: macro fetch failed: %s", exc)
        raise HTTPException(status_code=503, detail="Macro data unavailable")


@app.get("/data/macro/history", dependencies=[Depends(require_token)])
def get_macro_history(
    series: str = Query("VIXCLS"),
    lookback_days: int = Query(180, ge=1, le=3650),
) -> Dict[str, Any]:
    """Daily historical values for one FRED macro series (default VIXCLS)
    from ``HistoricalStore``'s ``macro_history`` cache — distinct from
    ``GET /data/macro`` above, which is a current-snapshot SCALAR dict only.

    Reads the local cache; ``get_macro()``'s own top-up logic still applies
    (refreshes from FRED if stale and a key is configured) but never raises
    on a missing key or an unwritable read-only connection — both degrade to
    whatever's already cached (CONSTRAINT #6). A gap day (FRED didn't
    publish, e.g. a market holiday) is a real ``null``, never a fabricated
    carry-forward value.
    """
    series_id = series.upper().strip()
    store = HistoricalStore(readonly=True)
    try:
        series_data = store.get_macro(series_id, lookback_days=lookback_days)
    except Exception as exc:
        logger.warning("data_api: macro history failed for %s: %s", series_id, exc)
        series_data = None

    if series_data is None or series_data.empty:
        return _clean_nan(
            {
                "series_id": series_id,
                "points": [],
                "reason": f"No cached history for {series_id} yet.",
            }
        )

    points = [
        {"date": idx.strftime("%Y-%m-%d"), "value": float(v)} for idx, v in series_data.items()
    ]
    return _clean_nan({"series_id": series_id, "points": points, "reason": None})


@app.get("/data/sentiment/{symbol}/history", dependencies=[Depends(require_token)])
def get_sentiment_history(
    symbol: str,
    lookback_days: int = Query(180, ge=1, le=3650),
) -> Dict[str, Any]:
    """Daily archived news-sentiment score history for ``symbol``, from
    ``HistoricalStore``'s ``news_history`` table (forward-archive only — see
    ``HistoricalStore.get_news_sentiment_history``'s docstring). Distinct
    from ``GET /metrics/sentiment/{symbol}`` (``metrics_api.py``), which is
    a live, point-in-time agent-derived read with no history.

    A point's ``score`` is ``null`` exactly when the pipeline had a genuine
    fetch/scoring failure or zero headlines that day — never a fabricated
    neutral ``0.0`` (CONSTRAINT #4). The archive only started 2026-07 (see
    ``pilots/catalog.py``), so most symbols will have only a few weeks of
    points today; callers should not assume enough depth for a lead-lag
    claim.
    """
    symbol = symbol.upper()
    store = HistoricalStore(readonly=True)
    try:
        series_data = store.get_news_sentiment_history(symbol, lookback_days=lookback_days)
    except Exception as exc:
        logger.warning("data_api: sentiment history failed for %s: %s", symbol, exc)
        series_data = None

    if series_data is None or series_data.empty:
        return _clean_nan(
            {
                "symbol": symbol,
                "points": [],
                "reason": f"No archived sentiment history for {symbol} yet.",
            }
        )

    points = [
        {"date": idx.strftime("%Y-%m-%d"), "score": float(v)} for idx, v in series_data.items()
    ]
    return _clean_nan({"symbol": symbol, "points": points, "reason": None})


@app.get("/data/universe", dependencies=[Depends(require_token)])
def get_universe() -> Dict[str, Any]:
    """The operator's configured ticker universe.

    Reads ``settings.DEFAULT_TICKERS`` — the canonical, GUI-writable universe
    key (the same one the GUI Live Inventory "Sync Now" persists). We
    deliberately do NOT call ``data.robinhood_client.discover_universe`` here:
    that triggers an interactive Robinhood/MFA login, which is inappropriate
    for a read HTTP endpoint.
    """
    symbols = list(settings.DEFAULT_TICKERS or [])
    return {"symbols": symbols, "count": len(symbols)}


@app.put("/data/universe", dependencies=[Depends(require_write_token)])
def update_universe(watchlist: List[str] = Body(...)) -> Dict[str, Any]:
    """Replace the configured universe.

    Writes ``DEFAULT_TICKERS`` via ``env_io.write_setting`` — the
    allowlist-bounded env writer. (``WATCHLIST`` is intentionally NOT in
    ``ALLOWED_KEYS``, so ``DEFAULT_TICKERS`` is the correct, writable key.)
    """
    from env_io import write_setting

    symbols = [s.strip().upper() for s in watchlist if s and s.strip()]
    try:
        write_setting("DEFAULT_TICKERS", symbols)
    except Exception as exc:
        logger.warning("data_api: universe write failed: %s", exc)
        raise HTTPException(status_code=400, detail=f"Could not update universe: {redact_line(str(exc))}")
    return {"status": "updated", "symbols": symbols}


@app.get("/data/quotes", dependencies=[Depends(require_token)])
def get_quotes(symbols: str) -> Dict[str, Any]:
    """Latest quotes for a comma-separated symbol list.

    Uses ``MarketDataProvider.get_quotes_batch()`` (F6, docs/
    module_efficiency_redundancy_audit.md) -- one batched call when the
    active provider supports it (FMPProvider's real ``/batch-quote``
    override), instead of N individual ``/quote`` requests. Same
    dead-letter contract as before: a symbol that fails to resolve is
    simply absent from the response, never raises.

    The pre-F6 per-symbol loop caught every exception per ticker, so a
    total provider outage still returned ``{}`` rather than a 500 -- this
    endpoint could never crash from a quote-fetch failure. A single
    ``get_quotes_batch()`` call collapses that per-symbol try/except into
    one call site; wrap it the same way the other two F6 call sites
    (``pilots/options_risk.py``, ``pilots/scenario_matrix.py``) already do,
    so a whole-batch failure degrades to an empty result instead of
    propagating.
    """
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        return {}
    provider = get_provider()
    try:
        quotes = provider.get_quotes_batch(sym_list)
    except Exception as exc:
        logger.warning("data_api: get_quotes_batch failed for %s: %s", sym_list, exc)
        quotes = {}
    out: Dict[str, Any] = {}
    for sym, q in quotes.items():
        out[sym] = _clean_nan(
            {
                "symbol": q.symbol,
                "price": q.price,
                "bid": q.bid,
                "ask": q.ask,
                "timestamp": q.timestamp.isoformat() if q.timestamp else None,
                "is_stale": q.is_stale,
                "source": q.source,
            }
        )
    return out


@app.get("/data/sync-report", dependencies=[Depends(require_token)])
def get_sync_report() -> Dict[str, Any]:
    """Portfolio & watchlist coverage report (holdings ∪ watchlists).

    Enriches each symbol entry with two rating fields sourced from
    ``rating.symbol_rating_store.SymbolRatingStore`` (Part 1 of the Symbol
    Rating subsystem — this endpoint never writes to that store, only reads):
    ``rating_consecutive_bad_cycles`` (int) and ``rating_excluded`` (bool,
    True only for a non-held symbol whose consecutive-BAD streak has reached
    ``settings.SYMBOL_RATING_DROP_THRESHOLD_CYCLES`` — mirrors
    ``SymbolRatingStore.get_excluded_symbols``'s own "never exclude a held
    symbol" rule so the badge can't disagree with what auto-drop would
    actually do). Enrichment is best-effort and self-contained to this file
    (``data/portfolio_sync.py`` is untouched) — a rating-store failure
    (missing DB, import error, etc.) degrades to leaving the two keys off
    every symbol rather than failing the whole endpoint (CONSTRAINT #6)."""
    try:
        snapshot = fetch_account_snapshot(force=False)
    except Exception as exc:
        logger.warning("data_api: account snapshot unavailable for sync report: %s", exc)
        snapshot = None
    try:
        from forecasting.forecast_tracker import ForecastTracker
        forecast_symbols = ForecastTracker().get_covered_symbols(horizon_days=30)
    except Exception as exc:
        logger.warning("data_api: forecast coverage lookup failed, degrading to none: %s", exc)
        forecast_symbols = []

    try:
        report = build_sync_report(snapshot, forecast_symbols=forecast_symbols)
    except Exception as exc:
        logger.warning("data_api: sync report failed: %s", exc)
        raise HTTPException(status_code=503, detail="Sync report unavailable")

    resp = _clean_nan(report.to_dict())
    try:
        from rating.symbol_rating_store import SymbolRatingStore
        from settings import settings as _settings

        store = SymbolRatingStore(readonly=True)
        threshold = _settings.SYMBOL_RATING_DROP_THRESHOLD_CYCLES
        for sym, entry in (resp.get("symbols") or {}).items():
            is_held = bool(entry.get("held"))
            cycles = store.get_consecutive_bad_cycles(sym)
            entry["rating_consecutive_bad_cycles"] = cycles
            entry["rating_excluded"] = (not is_held) and cycles >= threshold
    except Exception as exc:
        logger.warning("data_api: symbol-rating enrichment failed for sync report (%s)", exc)
        # Degrade: leave symbols without the two new keys rather than failing
        # the whole endpoint (CONSTRAINT #6).
    return resp


@app.get("/data/account", dependencies=[Depends(require_token)])
def get_account() -> Dict[str, Any]:
    """Robinhood account snapshot (DB → JSON cache → live). 404 on cold state."""
    try:
        snapshot = fetch_account_snapshot(force=False)
    except Exception as exc:
        logger.warning("data_api: account snapshot fetch failed: %s", exc)
        snapshot = None
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No account snapshot available")
    return _clean_nan(snapshot.to_dict())


# ---------------------------------------------------------------------------
# On-demand Options / Pairs recompute — /data/options/recompute,
# /data/pairs/analyze, /data/pairs/scan
# ---------------------------------------------------------------------------
# Backlog items 8a/8b: the persisted-snapshot views (GET /options, GET /pairs
# on api/pilots_api.py) only ever serve the LAST PIPELINE-WRITTEN artifact —
# there was no way for an operator to recompute against parameters/symbols
# they choose. These heavy engines (technical_options_engine,
# pairs.cointegration / signals.pairs_trading / statsmodels) must live here,
# not on the AST-guarded api/pilots_api.py. Mirrors GET /symbols/compare's
# (PR #379) "cap the input, stay synchronous, 422 outside the cap" convention
# rather than building a job/poll pattern — these are single-request,
# bounded-size computations, not a whole-pipeline run.


def _dedupe_symbols(symbols: List[str]) -> List[str]:
    """Upper-case + de-dup a symbol list, first occurrence wins, order
    preserved. Never raises on malformed input (a non-string entry is
    stringified)."""
    seen: set = set()
    out: List[str] = []
    for s in symbols or []:
        u = str(s or "").strip().upper()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


class PairsAnalyzeRequest(BaseModel):
    """Body for ``POST /data/pairs/analyze``. One named pair — the wedge for
    backlog item 8a. ``symbol_y`` is the dependent leg, ``symbol_x`` the hedge
    leg (mirrors ``gui/panels/pairs.py``'s "Analyze a pair" mode)."""

    symbol_y: str = Field(..., min_length=1, max_length=12)
    symbol_x: str = Field(..., min_length=1, max_length=12)


class PairsScanRequest(BaseModel):
    """Body for ``POST /data/pairs/scan``. An operator-chosen symbol list —
    2-15 after de-dup (422 with a stable tag outside that range, see
    ``pairs_ondemand.SCAN_MIN_SYMBOLS``/``SCAN_MAX_SYMBOLS``)."""

    symbols: List[str] = Field(..., min_length=1, max_length=64)
    p_threshold: float = Field(0.05, ge=0.01, le=0.10)
    max_pairs: int = Field(20, ge=1, le=50)


class OptionsRecomputeRequest(BaseModel):
    """Body for ``POST /data/options/recompute``. A capped, operator-chosen
    symbol list (1-8 after de-dup — see
    ``options_ondemand.RECOMPUTE_MIN_SYMBOLS``/``RECOMPUTE_MAX_SYMBOLS``) plus
    the same directive controls ``gui/panels/options_matrix.py`` exposes.
    Every field defaults to the engine constant, so an untouched request
    reproduces the pipeline writer's own defaults byte-for-byte."""

    symbols: List[str] = Field(..., min_length=1, max_length=64)
    target_dte: int = Field(30, ge=1, le=120)
    delta_target_scale: float = Field(1.0, ge=0.25, le=2.0)
    ivr_sell_threshold: float = Field(50.0, ge=0.0, le=100.0)
    ivr_buy_threshold: float = Field(30.0, ge=0.0, le=100.0)
    risk_free_rate_pct: Optional[float] = Field(
        None, ge=0.0, le=15.0,
        description="Annualized %, e.g. 4.5. None -> settings.RISK_FREE_RATE.",
    )
    strike_grid: float = Field(0.50, ge=0.5, le=10.0)
    delta_tolerance: float = Field(0.05, ge=0.01, le=0.25)


class CacheLongShortSimulateRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10)
    allocation: float = Field(..., gt=0)


@app.post("/data/cache-long-short/simulate", dependencies=[Depends(require_token)])
def simulate_cache_long_short(body: CacheLongShortSimulateRequest) -> Dict[str, Any]:
    from engine.cache_long_short_engine import CacheLongShortEngine
    sym = body.ticker.strip().upper()
    
    beta = CacheLongShortEngine.calculate_beta(sym)
    proxy, corr = CacheLongShortEngine.find_correlated_proxy(sym)
    
    if proxy is None or beta is None:
        return {
            "found": False,
            "reason": "Insufficient price history for ticker or suitable proxy",
            "beta": None,
            "proxy_ticker": None,
            "correlation_coefficient": None
        }
        
    return {
        "found": True,
        "reason": None,
        "beta": beta,
        "proxy_ticker": proxy,
        "correlation_coefficient": corr
    }


@app.post("/data/pairs/analyze", dependencies=[Depends(require_token)])
def analyze_pairs_ondemand(body: PairsAnalyzeRequest) -> Dict[str, Any]:
    """On-demand cointegration + spread-signal analysis for ONE named pair.

    Ports ``gui/panels/pairs.py``'s "Analyze a pair" mode to a stateless HTTP
    call. Advisory only (CONSTRAINT: no order code). Symbol Y and Symbol X
    must differ and both be non-empty (422 with a stable tag) — beyond that,
    this never 422s on an unresolved/degenerate pair: "no cointegration" or
    "insufficient history" is an honest, common, EXPECTED outcome for
    statistical arbitrage, surfaced as ``found: false`` + a ``reason``, not a
    client error (CONSTRAINT #6). Every numeric leaf is ``null`` when the
    underlying primitive is unavailable (CONSTRAINT #4).
    """
    sym_y = body.symbol_y.strip().upper()
    sym_x = body.symbol_x.strip().upper()
    if not sym_y or not sym_x:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "missing_symbol",
                "message": "Both Symbol Y and Symbol X are required.",
            },
        )
    if sym_y == sym_x:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "identical_symbols",
                "message": "Symbol Y and Symbol X must be different tickers.",
            },
        )

    provider = get_provider()
    result = pairs_ondemand.analyze_pair(sym_y, sym_x, provider)
    return _clean_nan(result)


@app.post("/data/pairs/scan", dependencies=[Depends(require_token)])
def scan_pairs_ondemand(body: PairsScanRequest) -> Dict[str, Any]:
    """On-demand cointegration scan over an operator-chosen symbol list.

    Ports ``gui/panels/pairs.py``'s "Scan for pairs" mode. 2-15 distinct
    symbols after upper-casing + de-dup (422 with a stable tag outside that
    range, mirroring ``GET /symbols/compare``'s convention). A symbol that
    fails to fetch is dead-lettered into the response's ``missing`` list
    rather than aborting the whole scan (CONSTRAINT #6); an honest empty
    ``pairs: []`` + ``reason`` is a valid 200, not an error (statistical
    arbitrage candidates are genuinely rare).
    """
    deduped = _dedupe_symbols(body.symbols)
    if len(deduped) < pairs_ondemand.SCAN_MIN_SYMBOLS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "too_few_symbols",
                "message": f"Enter at least {pairs_ondemand.SCAN_MIN_SYMBOLS} distinct symbols to scan.",
                "min": pairs_ondemand.SCAN_MIN_SYMBOLS,
            },
        )
    if len(deduped) > pairs_ondemand.SCAN_MAX_SYMBOLS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "too_many_symbols",
                "message": f"Enter at most {pairs_ondemand.SCAN_MAX_SYMBOLS} symbols to scan.",
                "max": pairs_ondemand.SCAN_MAX_SYMBOLS,
            },
        )

    provider = get_provider()
    result = pairs_ondemand.scan_pairs(
        deduped, provider, p_threshold=body.p_threshold, max_pairs=body.max_pairs
    )
    return _clean_nan(result)


@app.post("/data/options/recompute", dependencies=[Depends(require_token)])
def recompute_options_ondemand(body: OptionsRecomputeRequest) -> Dict[str, Any]:
    """On-demand premium-selling directive recompute over a capped symbol
    list, with adjustable delta-scale/IVR/risk-free-rate/strike-grid/DTE
    controls.

    Ports ``gui/panels/options_matrix.py``'s controls form + per-symbol
    compute loop to a stateless HTTP call. 1-8 symbols after de-dup (422 with
    a stable tag outside that range — each symbol pays a GJR-GARCH MLE fit,
    the heaviest per-symbol compute in this codebase). A bad symbol
    dead-letters into its own error-shaped row in ``directives`` (never aborts
    the batch — CONSTRAINT #6); its message is also collected into
    ``errors``. The VRP regime gate (VIX>=30 / CREDIT EVENT) is forwarded from
    the latest persisted snapshot's macro state, exactly as the live pipeline
    does — no premium-selling advice in a stress regime.
    """
    deduped = _dedupe_symbols(body.symbols)
    if len(deduped) < options_ondemand.RECOMPUTE_MIN_SYMBOLS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "too_few_symbols",
                "message": f"Enter at least {options_ondemand.RECOMPUTE_MIN_SYMBOLS} symbol.",
                "min": options_ondemand.RECOMPUTE_MIN_SYMBOLS,
            },
        )
    if len(deduped) > options_ondemand.RECOMPUTE_MAX_SYMBOLS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "too_many_symbols",
                "message": f"Enter at most {options_ondemand.RECOMPUTE_MAX_SYMBOLS} symbols.",
                "max": options_ondemand.RECOMPUTE_MAX_SYMBOLS,
            },
        )

    snapshot = load_snapshot()
    vix, market_regime = options_ondemand.macro_from_snapshot(snapshot)
    risk_free_rate_pct = (
        body.risk_free_rate_pct
        if body.risk_free_rate_pct is not None
        else float(settings.RISK_FREE_RATE) * 100.0
    )

    provider = get_provider()
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    for sym in deduped:
        result = options_ondemand.compute_directive_row(
            sym,
            provider=provider,
            target_dte=body.target_dte,
            vix=vix,
            market_regime=market_regime,
            risk_free_rate=risk_free_rate_pct / 100.0,
            ivr_sell_threshold=body.ivr_sell_threshold,
            ivr_buy_threshold=body.ivr_buy_threshold,
            delta_target_scale=body.delta_target_scale,
            delta_tolerance=body.delta_tolerance,
            strike_grid=body.strike_grid,
        )
        rows.append(result["row"])
        if result["error"]:
            errors.append(result["error"])

    return _clean_nan(
        {
            "directives": rows,
            "errors": errors,
            "vix": vix,
            "market_regime": market_regime,
            "target_dte": body.target_dte,
        }
    )


@app.get("/data/options/chain/{symbol}", dependencies=[Depends(require_token)])
def get_options_chain(symbol: str, expiration: Optional[str] = None) -> Dict[str, Any]:
    """On-demand full options chain fetcher.
    If `expiration` is omitted, returns a list of available expiration dates.
    If `expiration` is provided, returns the calls and puts for that date, enriched with Greeks.
    """
    symbol = symbol.upper()
    from data.market_data import get_options_provider, get_provider
    options_provider = get_options_provider()
    provider = get_provider()
    
    if expiration is None:
        expirations = options_provider.fetch_options_chain(symbol)
        return _clean_nan({
            "symbol": symbol,
            "expirations": expirations
        })
    
    chain = options_provider.fetch_options_chain(symbol, expiration)
    if chain is None:
        raise HTTPException(status_code=404, detail=f"Option chain not found for {symbol} at {expiration}")
        
    try:
        # We rely on the configured provider (which we prefer to be FMP based on config)
        # to fetch a reliable spot price for the Greek calculations.
        quote = provider.get_latest_quote(symbol)
        spot_price = quote.price
        if spot_price is not None and isinstance(spot_price, float) and math.isnan(spot_price):
            spot_price = None
    except Exception:
        spot_price = None

    if spot_price is None:
        # CONSTRAINT #4 (never fabricate): every Greek below is derived from
        # `spot_price` -- silently substituting $0.00 here would compute a
        # deeply-in-the-money Delta≈1 for every strike and present it as real,
        # with no signal to the caller that the underlying quote never
        # actually loaded. Fail honestly instead of returning invented Greeks.
        raise HTTPException(status_code=503, detail=f"Unable to fetch a live spot price for {symbol}; cannot compute option Greeks.")


    from technical_options_engine import OptionsPricingRecommender
    import datetime
    
    # Calculate DTE
    try:
        exp_date = datetime.datetime.strptime(expiration, "%Y-%m-%d").date()
        today = datetime.date.today()
        dte = max(1, (exp_date - today).days)
    except Exception:
        dte = 30
        
    T = dte / 365.0
    recommender = OptionsPricingRecommender(stock_price=spot_price, risk_free_rate=float(settings.RISK_FREE_RATE))
    
    def enrich_contract(row, option_type):
        iv = float(row.get('impliedVolatility', 0.0))
        strike = float(row.get('strike', 0.0))
        greeks = recommender.black_scholes_pricing_and_greeks(strike, T, iv, option_type)
        
        # CONSTRAINT #4 (never fabricate): a contract with genuinely unreported
        # volume/open-interest (common for far-OTM/illiquid strikes) must stay
        # `null`, not be coerced to a fabricated `0` that's indistinguishable
        # from a verified-zero reading. `_clean_nan` below only nulls actual
        # NaN floats, so the "missing" case is passed through as `None` here
        # rather than defaulted to `0` up front.
        vol = row.get("volume", None)
        vol = None if vol is None or (isinstance(vol, float) and math.isnan(vol)) else int(vol)

        oi = row.get("openInterest", None)
        oi = None if oi is None or (isinstance(oi, float) and math.isnan(oi)) else int(oi)

        return {
            "contractSymbol": row.get("contractSymbol"),
            "strike": strike,
            "lastPrice": float(row.get("lastPrice", 0.0)),
            "bid": float(row.get("bid", 0.0)),
            "ask": float(row.get("ask", 0.0)),
            "volume": vol,
            "openInterest": oi,
            "impliedVolatility": iv,
            "inTheMoney": bool(row.get("inTheMoney", False)),
            "greeks": {
                "delta": float(greeks['Delta']),
                "gamma": float(greeks['Gamma']),
                "theta": float(greeks['Theta_Daily']),
                # OptionsPricingRecommender.black_scholes_pricing_and_greeks
                # returns raw Black-Scholes vega (per 1.00/100% IV change) --
                # rescale to the "per 1% IV" convention this chain response
                # displays here, at this boundary only, so the shared engine
                # primitive (and its existing ATM_Vega consumer) stays on its
                # original scale.
                "vega": float(greeks['Vega']) / 100.0,
                "rho": float(greeks['Rho']),
                "chanceOfProfit": float(greeks['ChanceOfProfit']),
            }
        }
    
    calls = [enrich_contract(row, 'call') for _, row in chain.calls.iterrows()] if not chain.calls.empty else []
    puts = [enrich_contract(row, 'put') for _, row in chain.puts.iterrows()] if not chain.puts.empty else []
    
    return _clean_nan({
        "symbol": symbol,
        "expiration": expiration,
        "spot_price": spot_price,
        "calls": calls,
        "puts": puts
    })



# ---------------------------------------------------------------------------
# On-demand AI generation — /data/ai/*
# ---------------------------------------------------------------------------
# Three POST endpoints (not GET: they call out to a paid external LLM API on
# every uncached hit, so they must never be treated as a cacheable read) that
# port the Streamlit AI Insights tab's (``gui/panels/ai_insights.py``)
# on-demand generation flows onto the webapp's data API. Each underlying
# generator (``generate_for_symbol_row`` / ``generate_chart_pattern_read`` /
# ``generate_research_brief``) ALREADY self-caches to
# ``output/llm_commentary_cache.json`` via ``llm/cache.py`` — this file adds
# NO new caching layer, it is a thin, stateless HTTP wrapper. Every failure
# mode (capability off, missing key, generator returned ``None``, generator
# raised) is a soft-fail 200 with an honest ``reason`` field, never a 500
# (CONSTRAINT #6) -- these are expected, self-describing states the frontend
# renders inline, not exceptional ones.


def _find_signal_row(symbol: str) -> Optional[Dict[str, Any]]:
    """Return the raw ``signals[]`` entry for ``symbol`` from the current
    snapshot, or ``None`` when there is no snapshot or no matching entry.

    Mirrors ``gui/panels/ai_insights.py``'s own lookup
    (``sig_df[sig_df["symbol"] == selected].iloc[0].to_dict()``) but without
    a pandas round-trip. Never raises (CONSTRAINT #6).
    """
    snapshot = load_snapshot()
    if not isinstance(snapshot, dict):
        return None
    signals = snapshot.get("signals")
    if not isinstance(signals, list):
        return None
    for sig in signals:
        if isinstance(sig, dict) and str(sig.get("symbol") or "").upper() == symbol:
            return sig
    return None


@app.post(
    "/data/ai/commentary/{symbol}",
    dependencies=[Depends(require_token), Depends(_require_ai_generation_enabled)],
)
def generate_commentary(symbol: str) -> Dict[str, Any]:
    """On-demand Claude analyst note for ``symbol`` (Tier 9 analyst rationale).

    Ports ``gui/panels/ai_insights.py``'s "Claude analyst note" section
    (``_render_llm_commentary_button`` / ``gui.llm_commentary_panel``) to a
    stateless HTTP call. Gate: ``settings.LLM_COMMENTARY_ENABLED`` +
    ``settings.ANTHROPIC_API_KEY`` (via ``commentary_status``).

    Response shape (always 200 on a soft-fail, 404 only when the symbol
    itself isn't in the current snapshot -- never a fabricated row):
    ``{"available": bool, "reason": Optional[str], "payload": Optional[dict]}``
    where ``reason`` is one of ``"disabled"``, ``"missing_key"``,
    ``"generation_failed"``, or ``None`` on success. ``payload`` is an
    ``AnalystRationale.model_dump()``-shaped dict on success.
    """
    sym = symbol.upper()
    row = _find_signal_row(sym)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"{sym} not found in current snapshot signals"
        )

    status = commentary_status(settings)
    if status == "disabled":
        return _clean_nan({"available": False, "reason": "disabled", "payload": None})
    if status == "missing_key":
        return _clean_nan({"available": False, "reason": "missing_key", "payload": None})

    try:
        payload = generate_for_symbol_row(row)
    except Exception as exc:  # dead-letter — a generator bug must never 500 this endpoint
        logger.warning("data_api: commentary generation failed for %s: %s", sym, exc)
        return _clean_nan({"available": False, "reason": "generation_failed", "payload": None})

    if payload is None:
        return _clean_nan({"available": False, "reason": "generation_failed", "payload": None})
    return _clean_nan({"available": True, "reason": None, "payload": payload})


@app.post(
    "/data/ai/chart/{symbol}",
    dependencies=[Depends(require_token), Depends(_require_ai_generation_enabled)],
)
def generate_chart_insight(symbol: str) -> Dict[str, Any]:
    """On-demand Gemini Vision chart-pattern read for ``symbol`` (Tier 9 Scope 3).

    Ports ``gui/panels/ai_insights.py``'s "Gemini chart pattern
    interpretation" section (``_render_gemini_chart_section``) to a stateless
    HTTP call: fetch 252 daily bars via the same
    ``data.market_data.get_provider().get_intraday_bars`` path, render a PNG
    chart, then (capability permitting) send it to Gemini Vision.

    Gate: ``settings.LLM_COMMENTARY_ENABLED`` + ``settings.GEMINI_API_KEY``
    via ``gui.ai_insights_panel.insights_status`` -- the SAME status
    classifier ``render_ai_insights()`` uses to gate this exact section
    (deliberately NOT ``gui.llm_commentary_panel.commentary_status``, which
    additionally requires ``ANTHROPIC_API_KEY`` -- that's the Claude
    analyst-note gate, a different key requirement than the chart section
    actually uses at its real call site, ``_get_vision_provider()``).

    Response shape (always 200 on a soft-fail -- there is no 404 path, an
    unknown/no-data symbol just yields ``"no_bars"``):
    ``{"available": bool, "reason": Optional[str], "payload": Optional[dict],
    "chart_png_base64": Optional[str]}``. The rendered chart PNG is returned
    base64-encoded whenever it was successfully rendered -- INCLUDING when
    the AI read itself is disabled, missing a key, or failed -- so the
    frontend can always show the deterministic chart even when the AI
    narrative is unavailable.
    """
    sym = symbol.upper()

    try:
        bars = get_provider().get_intraday_bars(sym, lookback_days=252)
    except Exception as exc:
        logger.info("data_api: chart bars fetch failed for %s: %s", sym, exc)
        bars = None
    if bars is None or bars.empty:
        return _clean_nan(
            {"available": False, "reason": "no_bars", "payload": None, "chart_png_base64": None}
        )

    try:
        png = render_price_chart_png(sym, bars)
    except Exception as exc:
        logger.warning("data_api: chart render failed for %s: %s", sym, exc)
        png = None
    if not png:
        return _clean_nan(
            {
                "available": False,
                "reason": "chart_render_failed",
                "payload": None,
                "chart_png_base64": None,
            }
        )
    chart_b64 = base64.b64encode(png).decode("ascii")

    status = insights_status(settings)
    if status == "disabled":
        return _clean_nan(
            {"available": False, "reason": "disabled", "payload": None, "chart_png_base64": chart_b64}
        )
    if status == "missing_key":
        return _clean_nan(
            {
                "available": False,
                "reason": "missing_key",
                "payload": None,
                "chart_png_base64": chart_b64,
            }
        )

    try:
        result = generate_chart_pattern_read(sym, bars)
    except Exception as exc:  # dead-letter — a generator bug must never 500 this endpoint
        logger.warning("data_api: chart pattern generation failed for %s: %s", sym, exc)
        return _clean_nan(
            {
                "available": False,
                "reason": "generation_failed",
                "payload": None,
                "chart_png_base64": chart_b64,
            }
        )

    if result is None:
        return _clean_nan(
            {
                "available": False,
                "reason": "generation_failed",
                "payload": None,
                "chart_png_base64": chart_b64,
            }
        )

    payload = result.model_dump() if hasattr(result, "model_dump") else result
    return _clean_nan(
        {"available": True, "reason": None, "payload": payload, "chart_png_base64": chart_b64}
    )


@app.post(
    "/data/ai/research/{symbol}",
    dependencies=[Depends(require_token), Depends(_require_ai_generation_enabled)],
)
def generate_research(symbol: str) -> Dict[str, Any]:
    """On-demand Opal grounded research brief for ``symbol`` (Tier 9 Scope 4).

    Ports ``gui/panels/ai_insights.py``'s "Opal research brief" section
    (``_render_opal_research_section``) to a stateless HTTP call. Gate:
    ``settings.OPAL_RESEARCH_ENABLED`` alone -- mirrors that function's own
    gate check exactly (it does not consult ``commentary_status`` /
    ``insights_status``; Opal has its own independent master switch,
    decoupled from ``LLM_COMMENTARY_ENABLED``). No separate "missing_key"
    state is surfaced here (the provider layer routes between
    ``OPENAI_API_KEY`` / ``GEMINI_API_KEY`` internally); a missing key simply
    makes ``generate_research_brief`` return ``None``, which this endpoint
    reports as ``"generation_failed"`` -- identical to what the Streamlit
    section does (no dedicated missing-key caption for Opal either).

    Response shape (always 200 on a soft-fail; no 404 path -- research is not
    scoped to a snapshot's symbol universe):
    ``{"available": bool, "reason": Optional[str], "payload": Optional[dict]}``
    where ``payload`` is a ``ResearchBrief.model_dump()``-shaped dict on
    success.
    """
    sym = symbol.upper()
    if not getattr(settings, "OPAL_RESEARCH_ENABLED", False):
        return _clean_nan({"available": False, "reason": "disabled", "payload": None})

    try:
        result = generate_research_brief(sym, context={})
    except Exception as exc:  # dead-letter — a generator bug must never 500 this endpoint
        logger.warning("data_api: research brief generation failed for %s: %s", sym, exc)
        return _clean_nan({"available": False, "reason": "generation_failed", "payload": None})

    if result is None:
        return _clean_nan({"available": False, "reason": "generation_failed", "payload": None})

    payload = result.model_dump() if hasattr(result, "model_dump") else result
    return _clean_nan({"available": True, "reason": None, "payload": payload})


# ---------------------------------------------------------------------------
# G15 — Aggregate Claude-vs-Gemini disagreement (per-symbol), DURABLE variant.
#
# The legacy Streamlit AI Insights tab's "Aggregate Claude vs Gemini
# disagreement" table (gui/panels/ai_insights.py, Section 3) is built from
# TWO st.session_state mirrors (`ai_insights_claude_by_symbol` /
# `ai_insights_gemini_by_symbol`) populated only when the operator clicks the
# Claude-analyst-note / Gemini-chart-pattern buttons during that browser
# session — never persisted, so a stateless HTTP endpoint has no equivalent
# of THAT exact table. What IS durable is the on-disk LLM commentary cache
# both buttons already write through (llm/cache.py's
# output/llm_commentary_cache.json, a real file, not session state) --
# gui.ai_insights_panel.latest_verdict_maps_from_cache reconstructs the same
# {symbol: verdict} maps derive_disagreement_overview needs FROM that cache,
# so this endpoint answers the same question ("where do Claude and Gemini
# disagree, per symbol") from a genuinely durable source instead of
# fabricating one. This is a NEW, small addition to the AST-guard-free
# api/data_api.py (which already imports gui.ai_insights_panel above), not a
# reuse of api/pilots_api.py's dependency-light pilots/*.py read layer.
# ---------------------------------------------------------------------------


@app.get("/data/ai/disagreements", dependencies=[Depends(require_token)])
def get_ai_disagreements() -> Dict[str, Any]:
    """Durable per-symbol Claude-vs-Gemini verdict comparison.

    Reads every entry ever written to ``output/llm_commentary_cache.json``
    (``llm.cache.read_all_entries`` — real disk state, not
    ``st.session_state``), keeps the MOST RECENT Claude analyst-rationale and
    Gemini chart-pattern-read entry per symbol
    (``gui.ai_insights_panel.latest_verdict_maps_from_cache``), and compares
    them against the current snapshot's tracked symbol universe
    (``gui.ai_insights_panel.derive_disagreement_overview`` /
    ``disagreement_summary`` — the SAME pure comparison logic the legacy
    Streamlit tab used, just fed from a durable source instead of a
    per-browser-session dict).

    A row's ``claude_verdict``/``gemini_verdict`` is ``None`` — never a
    fabricated verdict — whenever that side was never generated for the
    symbol, or was generated but its cache entry has since aged out (a fresh
    LLM cache is possible any time an operator clears
    ``output/llm_commentary_cache.json``). ``disagreement`` is ``True`` only
    when BOTH sides are present and differ (CONSTRAINT #4: partial coverage
    never flags a disagreement).

    Fail-open read (no ``LLM_COMMENTARY_ENABLED``/AI-generation gate): this
    endpoint spends nothing and calls no provider — it only reads already-
    cached results, so it degrades to an honest all-``None``-verdicts table
    rather than a 403 when the capability is off. Returns
    ``{"rows": [], "summary": ..., "reason": "..."}`` when there is no
    current snapshot (nothing to compare against) or the helper module can't
    be imported. Never raises (CONSTRAINT #6)."""
    snapshot = load_snapshot()
    signals = snapshot.get("signals") if isinstance(snapshot, dict) else None
    if not isinstance(signals, list) or not signals:
        return {
            "rows": [],
            "summary": {"total_symbols": 0, "both_present": 0, "agreements": 0, "disagreements": 0},
            "reason": "No state snapshot yet — run the pipeline to populate the signal universe.",
        }

    try:
        from llm.cache import read_all_entries

        entries = read_all_entries()
        claude_map, gemini_map = latest_verdict_maps_from_cache(entries)
        rows = derive_disagreement_overview(
            signals=signals, claude_map=claude_map, gemini_map=gemini_map
        )
        summary = disagreement_summary(rows)
    except Exception as exc:  # noqa: BLE001 - dead-letter: cache/helper failure
        logger.warning("data_api: get_ai_disagreements failed: %s", exc)
        return {
            "rows": [],
            "summary": {"total_symbols": 0, "both_present": 0, "agreements": 0, "disagreements": 0},
            "reason": "AI disagreement view unavailable.",
        }

    row_dicts = [
        {
            "symbol": r.symbol,
            "advisory_action": r.advisory_action,
            "claude_verdict": r.claude_verdict,
            "gemini_verdict": r.gemini_verdict,
            "disagreement": r.disagreement,
        }
        for r in rows
    ]
    return {"rows": row_dicts, "summary": summary, "reason": None}


# =============================================================================
# Universe sync write + Market Data provider status (webapp parity gaps G8/G9)
# Appended at the end of the file per this repo's multi-agent collision
# protocol (other agents append their own new endpoints elsewhere in this
# same file concurrently on separate branches — appending here avoids a
# merge conflict on a shared line range near the top of the file).
# =============================================================================

_require_universe_sync_enabled = require_ai_capability_enabled(
    "UNIVERSE_SYNC_ENABLED", "Universe sync"
)


@app.post(
    "/data/sync",
    dependencies=[
        Depends(require_write_token),
        Depends(_require_universe_sync_enabled),
    ],
)
async def post_data_sync() -> Dict[str, Any]:
    """Run ``data.portfolio_sync.async_sync_now`` and persist the discovered
    universe to ``DEFAULT_TICKERS`` — the HTTP port of the Streamlit Live
    Inventory tab's "Sync Now" button (``gui/panels/live_inventory.py``).

    Deliberately does NOT pass ``client=`` (a ``data.robinhood_client.
    RobinhoodClient``): the Streamlit panel's best-effort
    ``RobinhoodClient().login()`` call is a live broker auth flow that can
    block on interactive MFA input — unsafe for a headless HTTP request
    handler backed by a bounded thread pool (a hung request would tie up a
    worker indefinitely). This endpoint folds in held positions
    (``fetch_account_snapshot(force=False)`` — NEVER ``force=True``, same
    reasoning) and file-backed watchlists (``SYNC_WATCHLIST_FILES`` /
    ``watchlist.txt``) only; Robinhood-hosted watchlists are not discovered
    here. ``GET /data/sync-report`` has the identical limitation already (it
    also never passes ``client=``).

    Fail-closed ``require_write_token`` (``STATE_API_TOKEN``) STACKED with the
    dedicated ``UNIVERSE_SYNC_ENABLED`` master flag — a real broker-adjacent
    read plus a ``DEFAULT_TICKERS`` ``.env`` write, matching
    ``PUT /data/universe``'s existing auth tier plus the additional feature
    flag every ``.env`` write with real side effects carries elsewhere in this
    codebase.

    The persisted ``DEFAULT_TICKERS`` write happens INSIDE ``async_sync_now``
    and is itself best-effort (a write failure there is caught and logged,
    never raised — see that function's own docstring), so this endpoint
    cannot confirm the ``.env`` write actually succeeded; it reports the
    tickers it SUBMITTED for persistence, not a re-read of ``settings``
    (which would never reflect an in-process ``.env`` write anyway — the same
    reasoning behind every other ``.env``-write endpoint in this codebase
    echoing the request/computed value rather than the stale settings
    singleton)."""
    try:
        snapshot = fetch_account_snapshot(force=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("data_api: account snapshot unavailable for sync: %s", exc)
        snapshot = None

    snap = load_snapshot()
    forecast_syms = [
        s.get("symbol") for s in (snap or {}).get("signals", []) if s.get("symbol")
    ]

    try:
        report = await async_sync_now(
            snapshot,
            forecast_symbols=forecast_syms,
            persist_default_tickers=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("data_api: sync failed: %s", exc)
        raise HTTPException(status_code=503, detail="Sync failed")

    tickers = sorted(report.symbols.keys())
    return _clean_nan(
        {
            "report": report.to_dict(),
            "default_tickers": tickers,
            "applies": "next_daemon_restart",
            "note": (
                f"Synced {len(tickers)} symbol(s). Submitted to DEFAULT_TICKERS "
                "in .env (best-effort persist — see server logs on failure); "
                "effective on next daemon restart."
            ),
        }
    )


@app.get("/data/provider-status", dependencies=[Depends(require_token)])
def get_provider_status() -> Dict[str, Any]:
    """Active market-data provider, delivery mode, and quote TTL — the HTTP
    port of the Streamlit Market Data tab's provider/mode/TTL tiles
    (``gui/panels/market_data.py``).

    Fail-open read, matching every other GET on this API. ``provider`` and
    ``is_realtime`` introspect the actually-constructed
    ``data.market_data.CompositeProvider`` singleton (``get_provider()``)
    rather than re-deriving the answer from ``settings.MARKET_DATA_PROVIDER``
    — so this reports what is REALLY running even if provider construction
    fell back to auto-detection. ``quote_ttl_seconds`` reads
    ``settings.MARKET_DATA_QUOTE_TTL_SECONDS`` directly.

    Connection-health tracking (Streamlit's sliding 20-fetch-window
    Healthy/Degraded/Down badge,
    ``gui.market_data_diagnostics.FetchHealthTracker``) is DELIBERATELY NOT
    duplicated server-side here: ``components/MarketDataHealth.tsx`` already
    implements the identical session-local sliding-window tracker
    client-side (its own ``useRef``-based ledger, mirroring
    ``FetchHealthTracker``'s exact thresholds — see that component's
    docstring), derived from THIS browser tab's own observed
    ``GET /data/quotes`` responses. A second, server-side tracker updated by
    unrelated requests from other tabs/users at other times would be a
    DIFFERENT signal, not a duplicate of the same one — surfacing it
    alongside the client's own tracker would be confusing, not more honest.
    Connection health therefore stays entirely client-side/session-local by
    design; this endpoint answers a different, complementary question (which
    provider, what mode, what TTL) that the client genuinely cannot answer on
    its own."""
    provider = get_provider()
    is_realtime = bool(getattr(provider, "is_realtime", False))
    return {
        "provider": getattr(provider, "quote_source", "unknown"),
        "is_realtime": is_realtime,
        "mode": "real_time" if is_realtime else "delayed",
        "quote_ttl_seconds": settings.MARKET_DATA_QUOTE_TTL_SECONDS,
        "fundamentals_source": getattr(provider, "source_name", "unknown"),
    }


def _clamp01_to_100(x: float) -> float:
    return max(0.0, min(100.0, x))


# Normalization anchors are this codebase's OWN authoritative, already-load-
# bearing kill-switch/regime thresholds (dto_models.py::MacroEconomicDTO.
# killSwitch / market_regime), not invented cutoffs: Sahm Rule kill switch at
# >= 0.5, VIX kill switch at > 30, credit spread ("high_yield_oas") RISK ON
# <= 4.5 / CREDIT EVENT >= 6.0, yield curve RECESSION signal at < -0.25. Each
# metric maps its own bad-threshold to 0 and a representative calm value to
# 100, linearly, clamped -- a real (if necessarily approximate) health score,
# not a fabricated one, because the anchors are the platform's own numbers.
def _vix_score(vix: float) -> float:
    return _clamp01_to_100(100.0 - (vix - 15.0) / (30.0 - 15.0) * 100.0)


def _sahm_score(sahm: float) -> float:
    return _clamp01_to_100(100.0 - (sahm / 0.5) * 100.0)


def _credit_spread_score(spread: float) -> float:
    return _clamp01_to_100(100.0 - (spread - 4.5) / (6.0 - 4.5) * 100.0)


def _yield_curve_score(spread: float) -> float:
    return _clamp01_to_100((spread - (-0.25)) / (0.5 - (-0.25)) * 100.0)


_REGIME_SCORE = {"RISK ON": 100.0, "NEUTRAL": 60.0, "CREDIT EVENT": 25.0, "RECESSION": 0.0}
_REGIME_ORDER = {"RECESSION": 0, "CREDIT EVENT": 1, "NEUTRAL": 2, "RISK ON": 3}


def _trend_from_delta(delta: float, higher_is_better: bool, epsilon: float) -> str:
    if abs(delta) < epsilon:
        return "flat"
    improved = delta > 0 if higher_is_better else delta < 0
    return "up" if improved else "down"


def _read_json_or_none(path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


@app.get("/data/macro/sentiment", dependencies=[Depends(require_token)])
def get_macro_sentiment() -> Dict[str, Any]:
    """Macroeconomic indicator sentiment scores and trends, from the real
    macro telemetry this platform tracks (VIX, Sahm Rule, High-Yield OAS,
    yield curve, market regime -- output/state_snapshot.json, itself sourced
    from MacroEconomicDTO). Each 0-100 "value" is a health score normalized
    against this codebase's own kill-switch/regime thresholds (see
    dto_models.py::MacroEconomicDTO.killSwitch/market_regime) -- not an
    invented scale. "trend" compares against the most recently rotated prior
    snapshot in output/history/ (scripts/snapshot_diff.py's own reader) --
    "flat" (never fabricated up/down) when there is no prior snapshot to
    compare against yet.
    """
    current = _read_json_or_none(settings.OUTPUT_DIR / "state_snapshot.json")
    if current is None:
        return {"macro_data": [], "is_synthetic": False, "reason": "No state snapshot yet — run the pipeline first."}

    previous: Optional[Dict[str, Any]] = None
    try:
        from scripts.snapshot_diff import list_rotated_snapshots, load_snapshot
        rotated = list_rotated_snapshots(settings.OUTPUT_DIR)
        if rotated:
            previous = load_snapshot(rotated[-1])
    except Exception as exc:
        logger.warning("get_macro_sentiment: history read failed: %s", exc)

    vix = float(current.get("vix", 0.0) or 0.0)
    sahm = float(current.get("sahm_rule", 0.0) or 0.0)
    spread = float(current.get("high_yield_oas", 0.0) or 0.0)
    curve = float(current.get("yield_curve", 0.0) or 0.0)
    regime = str(current.get("market_regime", "UNKNOWN") or "UNKNOWN")

    prev_vix = float(previous.get("vix", vix) or vix) if previous else vix
    prev_sahm = float(previous.get("sahm_rule", sahm) or sahm) if previous else sahm
    prev_spread = float(previous.get("high_yield_oas", spread) or spread) if previous else spread
    prev_curve = float(previous.get("yield_curve", curve) or curve) if previous else curve
    prev_regime = str(previous.get("market_regime", regime) or regime) if previous else regime

    macro_data = [
        {
            "subject": "VIX (Volatility)",
            "value": round(_vix_score(vix), 1),
            "trend": _trend_from_delta(vix - prev_vix, higher_is_better=False, epsilon=0.5) if previous else "flat",
        },
        {
            "subject": "Sahm Rule (Recession Signal)",
            "value": round(_sahm_score(sahm), 1),
            "trend": _trend_from_delta(sahm - prev_sahm, higher_is_better=False, epsilon=0.02) if previous else "flat",
        },
        {
            "subject": "High-Yield OAS (Credit Stress)",
            "value": round(_credit_spread_score(spread), 1),
            "trend": _trend_from_delta(spread - prev_spread, higher_is_better=False, epsilon=0.05) if previous else "flat",
        },
        {
            "subject": "Yield Curve (10Y-2Y)",
            "value": round(_yield_curve_score(curve), 1),
            "trend": _trend_from_delta(curve - prev_curve, higher_is_better=True, epsilon=0.02) if previous else "flat",
        },
    ]
    if regime in _REGIME_SCORE:
        regime_trend = "flat"
        if previous and prev_regime in _REGIME_ORDER and regime in _REGIME_ORDER:
            order_delta = _REGIME_ORDER[regime] - _REGIME_ORDER[prev_regime]
            regime_trend = "flat" if order_delta == 0 else ("up" if order_delta > 0 else "down")
        macro_data.append({
            "subject": "Market Regime",
            "value": _REGIME_SCORE[regime],
            "trend": regime_trend,
        })

    return {"macro_data": macro_data, "is_synthetic": False, "reason": None}


@app.get("/data/ladder/{symbol}", dependencies=[Depends(require_token)])
def get_order_book_ladder(symbol: str) -> Dict[str, Any]:
    """Active Trader order book depth ladder for a symbol.

    ``current_price`` is a REAL quote (via CompositeProvider, same source
    api/ws_api.py's REST fallback uses) when available. The depth ladder
    itself (bid/ask sizes at each price level) is SYNTHETIC
    (``is_synthetic: True``) -- this platform has no Level 2 / consolidated
    order book feed to compute real depth from (CLAUDE.md: Alpaca's free
    IEX feed and yfinance are both top-of-book only). Never present the
    sizes as real liquidity.
    """
    sym = symbol.upper()
    current_price: Optional[float] = None
    try:
        # get_provider() (the module singleton every other endpoint in this
        # file already uses), not a fresh CompositeProvider() -- the latter
        # constructs its own brand-new, cold TTL cache on every call,
        # silently defeating MARKET_DATA_QUOTE_TTL_SECONDS and re-hitting the
        # underlying network provider on every request.
        quote = get_provider().get_latest_quote(sym)
        if quote.price is not None and not math.isnan(quote.price):
            current_price = float(quote.price)
    except Exception as exc:
        logger.warning("get_order_book_ladder: quote fetch failed for %s: %s", sym, exc)

    if current_price is None:
        current_price = 450.00 if sym == "SPY" else 150.00

    bids = [
        {"price": round(current_price - 0.05 * i - 0.05, 2), "size": 1000 - i * 100, "type": "bid"}
        for i in range(5)
    ]
    asks = [
        {"price": round(current_price + 0.05 * i + 0.05, 2), "size": 800 + i * 150, "type": "ask"}
        for i in range(5)
    ]
    return {
        "symbol": sym,
        "current_price": current_price,
        "bids": bids,
        "asks": asks,
        "is_synthetic": True,
    }


class ChatMessageRequest(BaseModel):
    message: str
    history: Optional[List[Dict[str, Any]]] = None
    # Optional pre-formatted text block the frontend builds client-side from
    # data it already has on screen (e.g. the Options Matrix's currently
    # displayed directives -- symbol, Altman Z, days to earnings, ...). This
    # endpoint never re-fetches anything to build it; it only threads the
    # caller-supplied string into the prompt below. Backward compatible:
    # omitted/empty produces byte-identical behavior to before this field
    # existed.
    context: Optional[str] = None
    # Optional provider selection: 'auto', 'gemini', 'anthropic', 'openai', 'local'
    provider: Optional[str] = None
    # Optional model slug (e.g. 'claude-3-5-sonnet-20241022', 'gpt-4o', 'deepseek-r1', 'llama3.3')
    model: Optional[str] = None
    # Deliberately NO client-supplied base_url field. The "local" provider's
    # outbound endpoint is always settings.LOCAL_LLM_BASE_URL (operator-set,
    # server-side only) -- letting a request body pick an arbitrary base_url
    # would make this endpoint an open SSRF/credential-relay: it would POST
    # req.message/history/context (which can carry real portfolio/grounding
    # data) to any URL an unauthenticated-or-loopback caller supplies, using
    # settings.LOCAL_LLM_API_KEY (a real credential) as the bearer token. A
    # pydantic BaseModel with no `extra="forbid"` silently drops unknown
    # request fields, so a client that still sends "custom_base_url" is
    # simply ignored, not rejected -- see
    # TestMultiProviderRouting::test_local_routing_ignores_client_supplied_base_url
    # in tests/test_data_api_chat.py.


_ITER_BLOCKING_EXHAUSTED = object()


async def _iter_blocking(sync_iterable):
    """Bridge a blocking/synchronous iterator into an async generator.

    Each ``next()`` call runs in the default threadpool executor instead of
    the event loop thread, so waiting on the next network chunk from a
    synchronous SDK (Gemini's generate_content_stream, Anthropic's
    text_stream) never blocks other coroutines sharing this loop — a chat
    stream in flight would otherwise serialize every other request the Data
    API is handling.

    Deliberately calls ``next(it, _ITER_BLOCKING_EXHAUSTED)`` (a sentinel
    default) rather than bare ``next(it)`` inside the executor. A bare
    ``next()`` raises ``StopIteration`` when the iterator is exhausted, and
    asyncio's ``Future`` machinery cannot propagate a ``StopIteration``
    through ``run_in_executor`` (PEP 479 — see ``asyncio.futures``'s own
    ``"StopIteration interacts badly with generators and cannot be raised
    into a Future"`` guard): the executor's internal future-chaining callback
    raises a ``TypeError`` trying to set it, which is swallowed by asyncio's
    "exception was never retrieved" handling, and the ``await`` on that
    future then hangs forever rather than completing with an error. This is
    silent in real usage (a live Gemini/Anthropic stream essentially never
    yields zero chunks before completing) but reproduces deterministically
    whenever the wrapped iterable is empty or already exhausted — exactly
    the case a unit test that only inspects call kwargs and returns
    ``iter([])`` naturally hits. See tests/test_data_api_chat.py::TestContextField.
    """
    it = iter(sync_iterable)
    loop = asyncio.get_running_loop()
    while True:
        item = await loop.run_in_executor(None, next, it, _ITER_BLOCKING_EXHAUSTED)
        if item is _ITER_BLOCKING_EXHAUSTED:
            return
        yield item


def _sse(event_type: str, content: str) -> str:
    """Format one Server-Sent Event frame. Real newlines, not the literal
    two-character sequence '\\n' -- SSE delimits messages on an actual blank
    line, and the frontend parses with buffer.split('\\n')."""
    return f"data: {json.dumps({'type': event_type, 'content': content})}\n\n"


# ---------------------------------------------------------------------------
# Chat grounding tools -- READ-ONLY function-calling tools for POST /api/chat
# (Gemini automatic function calling; see chat_endpoint's docstring). Each
# function below is a plain Python callable the google-genai SDK introspects
# (type hints + docstring) to build its own tool schema and invoke
# automatically mid-turn -- passed directly into
# ``types.GenerateContentConfig(tools=[...])``, per the SDK's automatic
# function calling support.
#
# HARD CONSTRAINTS for every function in this section:
#   * READ-ONLY, always -- never a mutating action (no order placement, no
#     follow/unfollow, no watch-rule writes, no settings writes). This
#     section must NEVER grow an execute_paper_trade / follow_pilot /
#     update_watch_rules / etc. tool.
#   * MUST NEVER raise (CONSTRAINT #6) -- any failure is caught and returns
#     an honest ``{"error": "..."}`` dict so one bad tool call can never
#     crash the chat turn (matches every other dead-letter boundary in this
#     codebase).
#   * Reuses REAL existing platform read helpers -- nothing here
#     reimplements pilot scoring, holdings, trades, or observability logic.
# ---------------------------------------------------------------------------


def _chat_tool_history_dir() -> str:
    """Resolve the rotated-snapshot history dir from live settings per call
    (mirrors api/pilots_api.py::_history_dir -- kept local since this file
    has no other reason to import that module)."""
    return str(settings.OUTPUT_DIR / "history")


def _chat_tool_pilot_to_dict(pilot: Any) -> Dict[str, Any]:
    return {
        "id": pilot.id,
        "name": pilot.name,
        "category": pilot.category,
        "description": pilot.description,
        "weights": dict(pilot.weights),
        "long_only": pilot.long_only,
        "validation_strategy_id": pilot.validation_strategy_id,
    }


def list_all_pilots() -> Dict[str, Any]:
    """List every Pilot (copyable strategy) available on the Stockpy
    platform: id, display name, category, description, signal-module weight
    blend, and whether it is long-only. Use this to answer "what
    strategies/pilots exist" or to resolve a Pilot's id from its display
    name before calling another tool that needs ``pilot_id``."""
    try:
        return {"pilots": [_chat_tool_pilot_to_dict(p) for p in _catalog_list_pilots()]}
    except Exception as exc:  # noqa: BLE001 - dead-letter: a tool call must never raise
        logger.warning("chat tool list_all_pilots failed: %s", exc)
        return {"error": "Could not list pilots."}


def get_pilot_holdings(pilot_id: str) -> Dict[str, Any]:
    """Get a specific Pilot's current TARGET holdings -- the symbols it
    would hold, each one's target weight, sector, score, and current
    action -- computed from the latest persisted platform snapshot.
    ``pilot_id`` is the Pilot's stable slug (e.g. "trend-following"); call
    list_all_pilots first if you don't already know it."""
    try:
        pilot = _catalog_get_pilot(pilot_id)
        if pilot is None:
            return {"error": f"Unknown pilot_id '{pilot_id}'."}
        snapshot = load_snapshot()
        if snapshot is None:
            return {
                "pilot_id": pilot_id,
                "holdings": [],
                "reason": "No platform snapshot available yet.",
            }
        return {"pilot_id": pilot_id, "holdings": _pilots_pilot_holdings(pilot, snapshot)}
    except Exception as exc:  # noqa: BLE001 - dead-letter
        logger.warning("chat tool get_pilot_holdings failed: %s", exc)
        return {"error": "Could not fetch pilot holdings."}


def get_pilot_recent_trades(pilot_id: str) -> Dict[str, Any]:
    """Get a specific Pilot's recent signal-change trades (ENTER/EXIT/
    REWEIGHT events), most recent last, derived by diffing its holdings
    across recent historical platform snapshots. ``pilot_id`` is the Pilot's
    stable slug; call list_all_pilots first if you don't already know it."""
    try:
        pilot = _catalog_get_pilot(pilot_id)
        if pilot is None:
            return {"error": f"Unknown pilot_id '{pilot_id}'."}
        trades = _pilots_pilot_trades(pilot, history_dir=_chat_tool_history_dir())
        return {"pilot_id": pilot_id, "trades": trades}
    except Exception as exc:  # noqa: BLE001 - dead-letter
        logger.warning("chat tool get_pilot_recent_trades failed: %s", exc)
        return {"error": "Could not fetch pilot trades."}


def get_current_portfolio() -> Dict[str, Any]:
    """Get the operator's REAL current brokerage portfolio: total equity,
    buying power, and every open position (symbol, quantity, average cost,
    current price, market value, unrealized P&L). Sourced from the latest
    persisted account snapshot -- never a live brokerage call."""
    try:
        snap = HistoricalStore(readonly=True).latest_account_snapshot()
        if snap is None:
            return {"reason": "No account snapshot available yet."}
        return {
            "total_equity": snap.total_equity,
            "buying_power": snap.buying_power,
            "total_dividends": snap.total_dividends,
            "fetched_at": snap.fetched_at.isoformat() if snap.fetched_at else None,
            "positions": [
                {
                    "symbol": pos.symbol,
                    "quantity": pos.quantity,
                    "average_cost": pos.average_cost,
                    "current_price": pos.current_price,
                    "market_value": pos.market_value,
                    "unrealized_pl": pos.unrealized_pl,
                }
                for pos in (snap.positions or {}).values()
            ],
        }
    except Exception as exc:  # noqa: BLE001 - dead-letter
        logger.warning("chat tool get_current_portfolio failed: %s", exc)
        return {"error": "Could not fetch the current portfolio."}


def get_platform_status() -> Dict[str, Any]:
    """Get the platform's current regime/risk status: portfolio risk metrics
    (Sharpe, Calmar, max drawdown), portfolio heat vs. its limit, and the
    current macro regime overlay (Sahm rule, high-yield OAS, HMM risk-on
    probability). Use this to answer questions about overall platform/
    portfolio health or the current market regime."""
    try:
        summary = _pilots_observability_summary()
        return {
            "portfolio_risk": summary.get("portfolio_risk"),
            "portfolio_heat": summary.get("portfolio_heat"),
            "regime_overlay": summary.get("regime"),
        }
    except Exception as exc:  # noqa: BLE001 - dead-letter
        logger.warning("chat tool get_platform_status failed: %s", exc)
        return {"error": "Could not fetch platform status."}


# The fixed, read-only tool set handed to Gemini's automatic function
# calling. NEVER add a mutating function here (see the hard constraints in
# this section's header comment above).
_CHAT_TOOLS = [
    list_all_pilots,
    get_pilot_holdings,
    get_pilot_recent_trades,
    get_current_portfolio,
    get_platform_status,
]


@app.get(
    "/data/ai/models",
    dependencies=[Depends(require_token)],
)
async def list_ai_models_endpoint():
    """Returns list of available and supported LLM providers and model presets."""
    providers = [
        {
            "id": "gemini",
            "name": "Google Gemini",
            "available": bool(getattr(settings, "GEMINI_API_KEY", None)),
            "default_model": getattr(settings, "GEMINI_CHAT_MODEL", "gemini-2.5-flash"),
            "models": [
                "gemini-2.5-flash",
                "gemini-2.5-pro",
                "gemini-1.5-pro",
                "gemini-3.1-flash-live-preview",
            ],
        },
        {
            "id": "anthropic",
            "name": "Anthropic Claude",
            "available": bool(getattr(settings, "ANTHROPIC_API_KEY", None)),
            "default_model": "claude-3-5-sonnet-20241022",
            "models": [
                "claude-3-5-sonnet-20241022",
                "claude-3-5-haiku-20241022",
                "claude-3-opus-20240229",
            ],
        },
        {
            "id": "openai",
            "name": "OpenAI ChatGPT",
            "available": bool(getattr(settings, "OPENAI_API_KEY", None)),
            "default_model": "gpt-4o",
            "models": [
                "gpt-4o",
                "gpt-4o-mini",
                "o1",
                "o3-mini",
            ],
        },
        {
            "id": "local",
            "name": "Local / Open Source (Ollama, vLLM)",
            "available": bool(getattr(settings, "LOCAL_LLM_BASE_URL", None)),
            "base_url": getattr(settings, "LOCAL_LLM_BASE_URL", "http://localhost:11434/v1"),
            "default_model": getattr(settings, "LOCAL_LLM_MODEL", "llama3.3"),
            "models": [
                "llama3.3",
                "deepseek-r1",
                "qwen2.5",
                "mistral",
            ],
        },
    ]
    return {
        "default_provider": getattr(settings, "AI_CHAT_DEFAULT_PROVIDER", "auto"),
        "default_model": getattr(settings, "AI_CHAT_DEFAULT_MODEL", None),
        "providers": providers,
    }


@app.post(
    "/api/chat",
    dependencies=[Depends(require_token), Depends(_require_ai_generation_enabled)],
)
async def chat_endpoint(req: ChatMessageRequest):
    """Streaming multi-model chat endpoint for AI Chat Interface.

    Gated by _require_ai_generation_enabled (settings.AI_GENERATION_API_ENABLED)
    in addition to require_token. Supports multi-provider routing:
    - Google Gemini (with automatic platform function calling)
    - Anthropic Claude (with system context grounding)
    - OpenAI ChatGPT (gpt-4o, o3-mini, etc.)
    - Local / Open-source LLMs via OpenAI-compatible endpoints (Ollama, vLLM, DeepSeek, etc.)
    """

    async def stream_generator():
        yield _sse("THOUGHT", "Analyzing query...")
        await asyncio.sleep(0.05)

        try:
            # Resolve target provider
            requested_provider = (req.provider or "").strip().lower()
            if requested_provider in ("gemini", "google"):
                provider = "gemini"
            elif requested_provider in ("anthropic", "claude"):
                provider = "anthropic"
            elif requested_provider in ("openai", "chatgpt"):
                provider = "openai"
            elif requested_provider in ("local", "ollama", "vllm", "openrouter", "custom"):
                provider = "local"
            else:
                # Auto / fallback detection
                default_p = getattr(settings, "AI_CHAT_DEFAULT_PROVIDER", "auto").lower()

                def _is_provider_available(p_name: str) -> bool:
                    if p_name == "gemini":
                        return bool(getattr(settings, "GEMINI_API_KEY", None))
                    elif p_name in ("anthropic", "claude"):
                        return bool(getattr(settings, "ANTHROPIC_API_KEY", None))
                    elif p_name in ("openai", "chatgpt"):
                        return bool(getattr(settings, "OPENAI_API_KEY", None))
                    elif p_name in ("local", "ollama", "vllm"):
                        return bool(getattr(settings, "LOCAL_LLM_BASE_URL", None))
                    return False

                if default_p != "auto" and _is_provider_available(default_p):
                    provider = "gemini" if default_p == "google" else ("anthropic" if default_p == "claude" else default_p)
                elif _is_provider_available("gemini"):
                    provider = "gemini"
                elif _is_provider_available("anthropic"):
                    provider = "anthropic"
                elif _is_provider_available("openai"):
                    provider = "openai"
                elif _is_provider_available("local"):
                    provider = "local"
                else:
                    provider = "none"

            if provider == "gemini":
                if not getattr(settings, "GEMINI_API_KEY", None):
                    yield _sse("MESSAGE", "Error: GEMINI_API_KEY is not configured in settings.")
                    yield "data: [DONE]\n\n"
                    return

                model_name = req.model or getattr(settings, "GEMINI_CHAT_MODEL", "gemini-2.5-flash")
                yield _sse("THOUGHT", f"Routing to Gemini ({model_name})...")
                from google import genai
                from google.genai import types
                # 2026-08 fix: google-genai's own default is NO TIMEOUT AT
                # ALL when unset (confirmed against the installed SDK
                # source) -- see settings.AI_CHAT_TIMEOUT_SECONDS.
                client = genai.Client(
                    api_key=settings.GEMINI_API_KEY,
                    http_options=types.HttpOptions(
                        timeout=int(settings.AI_CHAT_TIMEOUT_SECONDS * 1000)
                    ),
                )

                contents = []
                if req.context:
                    contents.append(
                        types.Content(
                            role="user",
                            parts=[types.Part.from_text(text=f"Context:\n{req.context}")]
                        )
                    )
                for msg in (req.history or []):
                    role = msg.get("role", "user")
                    if role == "assistant":
                        role = "model"
                    contents.append(
                        types.Content(
                            role=role,
                            parts=[types.Part.from_text(text=msg.get("content", ""))]
                        )
                    )
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=req.message)]
                    )
                )

                response_stream = client.models.generate_content_stream(
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(tools=_CHAT_TOOLS),
                )

                async for chunk in _iter_blocking(response_stream):
                    if chunk.text:
                        yield _sse("MESSAGE", chunk.text)

            elif provider == "anthropic":
                if not getattr(settings, "ANTHROPIC_API_KEY", None):
                    yield _sse("MESSAGE", "Error: ANTHROPIC_API_KEY is not configured in settings.")
                    yield "data: [DONE]\n\n"
                    return

                model_name = req.model or "claude-3-5-sonnet-20241022"
                yield _sse("THOUGHT", f"Routing to Claude ({model_name})...")
                import anthropic
                # 2026-08 fix: previously inherited the SDK's 10-minute
                # default -- see settings.AI_CHAT_TIMEOUT_SECONDS.
                client = anthropic.Anthropic(
                    api_key=settings.ANTHROPIC_API_KEY,
                    timeout=settings.AI_CHAT_TIMEOUT_SECONDS,
                )

                messages = []
                for msg in (req.history or []):
                    role = msg.get("role", "user")
                    if role == "model": role = "assistant"
                    messages.append({"role": role, "content": msg.get("content", "")})
                messages.append({"role": "user", "content": req.message})

                stream_kwargs: Dict[str, Any] = {
                    "model": model_name,
                    "max_tokens": 1024,
                    "messages": messages,
                }
                if req.context:
                    stream_kwargs["system"] = f"Context:\n{req.context}"

                loop = asyncio.get_running_loop()
                stream_cm = client.messages.stream(**stream_kwargs)
                stream = await loop.run_in_executor(None, stream_cm.__enter__)
                try:
                    async for text in _iter_blocking(stream.text_stream):
                        yield _sse("MESSAGE", text)
                finally:
                    await loop.run_in_executor(None, stream_cm.__exit__, None, None, None)

            elif provider == "openai":
                if not getattr(settings, "OPENAI_API_KEY", None):
                    yield _sse("MESSAGE", "Error: OPENAI_API_KEY is not configured in settings.")
                    yield "data: [DONE]\n\n"
                    return

                model_name = req.model or "gpt-4o"
                yield _sse("THOUGHT", f"Routing to OpenAI ({model_name})...")
                import openai
                # 2026-08 fix: previously inherited the SDK's 10-minute
                # default -- see settings.AI_CHAT_TIMEOUT_SECONDS.
                client = openai.AsyncOpenAI(
                    api_key=settings.OPENAI_API_KEY,
                    timeout=settings.AI_CHAT_TIMEOUT_SECONDS,
                )

                messages = []
                if req.context:
                    messages.append({"role": "system", "content": f"Platform Grounding Context:\n{req.context}"})
                for msg in (req.history or []):
                    role = msg.get("role", "user")
                    if role == "model": role = "assistant"
                    messages.append({"role": role, "content": msg.get("content", "")})
                messages.append({"role": "user", "content": req.message})

                response = await client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    stream=True,
                )

                async for chunk in response:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield _sse("MESSAGE", chunk.choices[0].delta.content)

            elif provider == "local":
                # base_url is ALWAYS the operator's own server-side setting,
                # never anything from the request body -- see
                # ChatMessageRequest's comment above for why a client-
                # supplied base_url would be an SSRF/credential-relay risk.
                base_url = (getattr(settings, "LOCAL_LLM_BASE_URL", None) or "http://localhost:11434/v1").rstrip("/")
                model_name = req.model or getattr(settings, "LOCAL_LLM_MODEL", "llama3.3")
                api_key = getattr(settings, "LOCAL_LLM_API_KEY", None) or "ollama"

                yield _sse("THOUGHT", f"Routing to Local LLM ({model_name} at {base_url})...")
                import openai
                # 2026-08 fix: previously inherited the SDK's 10-minute
                # default -- a self-hosted/local endpoint is arguably MORE
                # likely to hang (stuck model load, OOM, no upstream
                # monitoring) than a hosted SaaS API. See
                # settings.AI_CHAT_TIMEOUT_SECONDS.
                client = openai.AsyncOpenAI(
                    base_url=base_url,
                    api_key=api_key,
                    timeout=settings.AI_CHAT_TIMEOUT_SECONDS,
                )

                messages = []
                if req.context:
                    messages.append({"role": "system", "content": f"Platform Grounding Context:\n{req.context}"})
                for msg in (req.history or []):
                    role = msg.get("role", "user")
                    if role == "model": role = "assistant"
                    messages.append({"role": role, "content": msg.get("content", "")})
                messages.append({"role": "user", "content": req.message})

                response = await client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    stream=True,
                )

                async for chunk in response:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield _sse("MESSAGE", chunk.choices[0].delta.content)

            else:
                yield _sse("MESSAGE", "Error: No AI model provider configured. Set GEMINI_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY, or LOCAL_LLM_BASE_URL.")

            yield _sse("SUGGESTION", "Show portfolio risk")
            yield _sse("SUGGESTION", "Explain option overlay")

        except Exception as e:
            logger.error("Chat streaming error: %s", e, exc_info=True)
            yield _sse("MESSAGE", "**Error:** something went wrong generating a response. Please try again.")

        yield "data: [DONE]\n\n"

    return StreamingResponse(stream_generator(), media_type="text/event-stream")


@app.get(
    "/risk/circuit-breaker/status",
    dependencies=[Depends(require_token)],
)
async def get_circuit_breaker_status():
    """Get the active dynamic circuit breaker status.

    Reads ``output/circuit_breaker_state.json`` or queries DynamicCircuitBreaker.
    Degrades gracefully to NORMAL state if uninitialized or file is missing.
    Never fabricates or raises (CONSTRAINT #4 / #6).
    """
    from datetime import datetime, timezone
    from pathlib import Path

    # 1. Try reading persisted output/circuit_breaker_state.json
    try:
        output_dir = getattr(settings, "OUTPUT_DIR", None) or Path("output")
        cb_path = Path(output_dir) / "circuit_breaker_state.json"
        if cb_path.exists():
            with open(cb_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {
                    "state": data.get("state", "NORMAL"),
                    "volatility_zscore": float(data.get("volatility_zscore", 0.0) or 0.0),
                    "vpin": float(data.get("vpin", 0.0) or 0.0),
                    "ofi": float(data.get("ofi", 0.0) or 0.0),
                    "loss_velocity_per_min": float(data.get("loss_velocity_per_min", 0.0) or 0.0),
                    "reason": data.get("reason"),
                    "updated_at": data.get("updated_at") or datetime.now(timezone.utc).isoformat(),
                }
    except Exception as exc:
        logger.warning("data_api: Failed to read circuit breaker state file: %s", exc)

    # 2. Try querying DynamicCircuitBreaker if available in memory
    try:
        from execution.dynamic_circuit_breaker import DynamicCircuitBreaker  # type: ignore
        cb = DynamicCircuitBreaker()
        status = cb.load_metrics()
        if status is not None:
            s_dict = status.to_dict() if hasattr(status, "to_dict") else status
            return {
                "state": s_dict.get("state", "NORMAL"),
                "volatility_zscore": float(s_dict.get("volatility_zscore", 0.0) or 0.0),
                "vpin": float(s_dict.get("vpin", 0.0) or 0.0),
                "ofi": float(s_dict.get("ofi", 0.0) or 0.0),
                "loss_velocity_per_min": float(s_dict.get("loss_velocity_per_min", 0.0) or 0.0),
                "reason": s_dict.get("reason"),
                "updated_at": s_dict.get("updated_at") or datetime.now(timezone.utc).isoformat(),
            }
    except (ImportError, AttributeError, Exception) as exc:
        logger.debug("data_api: DynamicCircuitBreaker in-memory status not available: %s", exc)

    # 3. Fallback to NORMAL
    return {
        "state": "NORMAL",
        "volatility_zscore": 0.0,
        "vpin": 0.0,
        "ofi": 0.0,
        "loss_velocity_per_min": 0.0,
        "reason": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

@app.get("/data/trends/stitch-demo", dependencies=[Depends(require_token)])
def get_trends_stitch_demo() -> Dict[str, Any]:
    """
    Demonstrates the Google Trends SVI overlapping-window stitching algorithm
    (data.trends_stitcher.GoogleTrendsStitcher) against real market data.

    Live Google Trends Search Volume Index (SVI) fetching is NOT wired up in this
    codebase (no SVI provider exists here). Per CONSTRAINT #4 (never fabricate a
    metric), this endpoint does not synthesize a fake SVI series. Instead it uses
    real SPY trading volume (via HistoricalStore) as an honestly-labeled PROXY
    input to exercise the real stitching algorithm end-to-end -- every curve name
    in the response discloses this explicitly ("SPY Volume Proxy"), never
    presented as if it were real Google Trends data.

    Raises HTTPException(503) -- rather than returning a fabricated placeholder
    series -- if SPY bar history is insufficient or unavailable.
    """
    import pandas as pd

    from data.trends_stitcher import GoogleTrendsStitcher

    n_bars = 240

    try:
        store = HistoricalStore(readonly=True)
        bars = store.get_bars("SPY")
        if bars.empty or len(bars) < n_bars:
            raise ValueError(f"Insufficient SPY bar history: {len(bars)} rows (need >= {n_bars})")
        # Keep the real tz-naive DatetimeIndex intact -- GoogleTrendsStitcher.stitch_intervals
        # aligns overlapping periods via index intersection, and the response needs real
        # calendar dates as epoch-ms timestamps, not a fabricated/positional index.
        true_series = bars["Volume"].tail(n_bars)
    except Exception as exc:
        logger.warning(
            "get_trends_stitch_demo: unable to build SPY-volume-proxy SVI stitching demo (%s): %s",
            type(exc).__name__,
            exc,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Live Google Trends SVI fetching is not implemented -- this demo uses real "
                "SPY trading volume as an honest proxy input, and insufficient SPY bar history "
                "is currently available to build it. Use mock mode to view the demo."
            ),
        )

    slice_a = true_series.iloc[0:90]
    period_a = slice_a / slice_a.max() * 100.0

    slice_b = true_series.iloc[75:165]
    period_b = slice_b / slice_b.max() * 100.0

    slice_c = true_series.iloc[150:240]
    period_c = slice_c / slice_c.max() * 100.0

    stitched_ab = GoogleTrendsStitcher.stitch_intervals(period_a, period_b)
    stitched_all = GoogleTrendsStitcher.stitch_intervals(stitched_ab, period_c)

    def to_curve(name: str, series: pd.Series) -> Dict[str, Any]:
        points: List[List[float]] = []
        for ts, val in series.items():
            if val is None or (isinstance(val, float) and math.isnan(val)):
                continue
            points.append([int(ts.timestamp() * 1000), float(val)])
        return {"name": name, "data": points}

    return {
        "raw_curves": [
            to_curve("SPY Volume Proxy — Period A", period_a),
            to_curve("SPY Volume Proxy — Period B", period_b),
            to_curve("SPY Volume Proxy — Period C", period_c),
        ],
        "stitched_curve": to_curve("Stitched SPY Volume Proxy", stitched_all),
    }
