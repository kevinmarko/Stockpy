"""
api/data_api.py
================
STANDALONE FastAPI service exposing data-ingestion, market-data, and universe
endpoints. Consumed by the React PWA to view raw data and manage the universe.

Run standalone::

    uvicorn api.data_api:app --port 8603

Auth posture (copied from ``api/state_api.py``): a **fail-open** bearer token —
when ``settings.STATE_API_TOKEN`` is set, every data endpoint requires
``Authorization: Bearer <token>`` (constant-time compare, 401 on mismatch);
when unset the endpoints are open for zero-config local use. ``/health`` is
ALWAYS open so a load-balancer / watchdog can probe without a token. The token
is NEVER logged (CONSTRAINT #3).

Honesty (CONSTRAINT #4): a value that cannot be computed degrades to ``null``
(``NaN``/``inf`` → ``null``) rather than a fabricated ``0.0``; dead-letter
resilient (CONSTRAINT #6) — a single failed fetch never crashes the service.

This module MAY import the engine/data layer (unlike ``api/state_api.py`` /
``api/control_api.py``, whose read-only purity is AST-guarded); it is a
data-facing service, not the kill-switch/daemon control plane.
"""
from __future__ import annotations

import base64
import hmac
import logging
import math
from typing import Any, Dict, List, Optional

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from settings import settings
from data.historical_store import HistoricalStore
from data.market_data import MarketDataError, get_provider
from data.robinhood_portfolio import fetch_account_snapshot
from data.portfolio_sync import build_sync_report
from data_engine import DataEngine

# ── On-demand AI generation (Section: /data/ai/*) ──────────────────────────
# Imported by NAME (not by submodule reference) so tests can monkeypatch each
# generator directly on this module's namespace, e.g.
# ``monkeypatch.setattr(data_api, "generate_for_symbol_row", fake)``.
# None of these modules import streamlit at module top (verified) and this
# file carries no AST import guard (unlike ``api/pilots_api.py`` /
# ``api/state_api.py``), so importing them here is safe and intentional.
from gui.ai_insights_panel import insights_status
from gui.llm_commentary_panel import commentary_status, generate_for_symbol_row
from llm.chart_insight import generate_chart_pattern_read, render_price_chart_png
from llm.research import generate_research_brief
from pilots.scoring import load_snapshot

logger = logging.getLogger(__name__)

app = FastAPI(
    title="InvestYo Data API",
    description="Data ingestion and market-data endpoints for the Web App.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "PUT", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

_bearer = HTTPBearer(auto_error=False)


def require_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> None:
    """Fail-open bearer-token guard (mirrors ``api/state_api.py``).

    When ``settings.STATE_API_TOKEN`` is unset/empty, this is a no-op (local
    zero-config use). When set, a constant-time compare is enforced.
    """
    token = settings.STATE_API_TOKEN
    if not token:
        return
    presented = credentials.credentials if credentials else ""
    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


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

    NOT wired into any of the three ``/data/ai/*`` endpoints below as a hard
    block, deliberately: each of those endpoints' "capability is off" state is
    an HONEST, EXPECTED response (mirrors the Streamlit AI Insights tab's
    inline info caption), not an error -- the caller wants a 200 with
    ``{"available": false, "reason": "disabled"}`` so the webapp can render a
    "turn this on in .env" hint, not a bare 403. Each handler checks its
    capability flag inline (via ``commentary_status()`` / ``insights_status()``
    / a direct ``settings.OPAL_RESEARCH_ENABLED`` read) instead. This factory
    is kept as the reusable fail-closed-403 primitive for a FUTURE endpoint
    that genuinely wants a hard block on a disabled capability rather than a
    self-describing soft-fail body.
    """

    def _dependency() -> None:
        if not getattr(settings, flag_name, False):
            raise HTTPException(
                status_code=403,
                detail=f"{capability_label} is disabled ({flag_name}=false).",
            )

    return _dependency


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
def get_bars(symbol: str, lookback_days: int = 252) -> List[Dict[str, Any]]:
    """Daily OHLCV bars for ``symbol`` — ``[]`` when none are available.

    Routes through ``HistoricalStore`` (incremental DB cache) with the live
    provider as the top-up source, matching the rest of the pipeline.
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


@app.get("/data/macro", dependencies=[Depends(require_token)])
def get_macro_raw() -> Dict[str, Any]:
    """Raw current-snapshot macro dict (VIX, yield curve, Sahm, etc.)."""
    engine = DataEngine(settings.FRED_API_KEY or "")
    try:
        return _clean_nan(engine.fetch_macro_raw())
    except Exception as exc:
        logger.warning("data_api: macro fetch failed: %s", exc)
        raise HTTPException(status_code=503, detail="Macro data unavailable")


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


@app.put("/data/universe", dependencies=[Depends(require_token)])
def update_universe(watchlist: List[str] = Body(...)) -> Dict[str, Any]:
    """Replace the configured universe.

    Writes ``DEFAULT_TICKERS`` via ``gui.env_io.write_setting`` — the
    allowlist-bounded env writer. (``WATCHLIST`` is intentionally NOT in
    ``ALLOWED_KEYS``, so ``DEFAULT_TICKERS`` is the correct, writable key.)
    """
    from gui.env_io import write_setting

    symbols = [s.strip().upper() for s in watchlist if s and s.strip()]
    try:
        write_setting("DEFAULT_TICKERS", symbols)
    except Exception as exc:
        logger.warning("data_api: universe write failed: %s", exc)
        raise HTTPException(status_code=400, detail=f"Could not update universe: {exc}")
    return {"status": "updated", "symbols": symbols}


@app.get("/data/quotes", dependencies=[Depends(require_token)])
def get_quotes(symbols: str) -> Dict[str, Any]:
    """Latest quotes for a comma-separated symbol list.

    There is no batch ``get_quotes`` on the provider — the real accessor is
    ``get_latest_quote(symbol) -> Quote`` (raises ``MarketDataError`` on
    failure). We loop per symbol with per-symbol dead-lettering so one bad
    ticker never drops the whole batch; failed symbols are simply omitted.
    """
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        return {}
    provider = get_provider()
    out: Dict[str, Any] = {}
    for sym in sym_list:
        try:
            q = provider.get_latest_quote(sym)
        except MarketDataError as exc:
            logger.info("data_api: quote unavailable for %s: %s", sym, exc)
            continue
        except Exception as exc:  # defensive dead-letter
            logger.warning("data_api: quote error for %s: %s", sym, exc)
            continue
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
    """Portfolio & watchlist coverage report (holdings ∪ watchlists)."""
    try:
        snapshot = fetch_account_snapshot(force=False)
    except Exception as exc:
        logger.warning("data_api: account snapshot unavailable for sync report: %s", exc)
        snapshot = None
    try:
        report = build_sync_report(snapshot)
    except Exception as exc:
        logger.warning("data_api: sync report failed: %s", exc)
        raise HTTPException(status_code=503, detail="Sync report unavailable")
    return _clean_nan(report.to_dict())


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


@app.post("/data/ai/commentary/{symbol}", dependencies=[Depends(require_token)])
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


@app.post("/data/ai/chart/{symbol}", dependencies=[Depends(require_token)])
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


@app.post("/data/ai/research/{symbol}", dependencies=[Depends(require_token)])
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
