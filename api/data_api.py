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
    allow_methods=["GET", "PUT"],
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
