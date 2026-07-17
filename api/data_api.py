"""
api/data_api.py
================
STANDALONE FastAPI service exposing data ingestion, market data, and universe endpoints.
Consumed by the React PWA to view raw data and trigger updates.

Run standalone:
    uvicorn api.data_api:app --port 8603
"""
from __future__ import annotations

import hmac
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
import dataclasses

from fastapi import Depends, FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from settings import settings
from data.historical_store import HistoricalStore
from data.market_data import get_provider
from data.robinhood_portfolio import fetch_account_snapshot
from data.portfolio_sync import build_sync_report
from data_engine import DataEngine

logger = logging.getLogger(__name__)

app = FastAPI(
    title="InvestYo Data API",
    description="Data ingestion and market data endpoints for the Web App.",
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

def require_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> None:
    token = settings.STATE_API_TOKEN
    if not token:
        return
    presented = credentials.credentials if credentials else ""
    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")

@app.get("/health")
def health_check() -> Dict[str, str]:
    return {"status": "ok", "service": "data_api"}

@app.get("/data/bars/{symbol}", dependencies=[Depends(require_token)])
def get_bars(symbol: str, lookback_days: int = 252) -> List[Dict[str, Any]]:
    symbol = symbol.upper()
    store = HistoricalStore(readonly=True)
    provider = get_provider()
    try:
        df = store.get_bars(symbol, lookback_days=lookback_days, provider=provider)
        if df.empty:
            return []
        
        df = df.reset_index()
        # Rename 'Date'/'Datetime' to 'date' if present
        if 'Date' in df.columns:
            df.rename(columns={'Date': 'date'}, inplace=True)
        elif 'Datetime' in df.columns:
            df.rename(columns={'Datetime': 'date'}, inplace=True)
            
        records = df.to_dict(orient="records")
        for r in records:
            if 'date' in r and hasattr(r['date'], 'isoformat'):
                r['date'] = r['date'].isoformat()
        return records
    except Exception as e:
        logger.exception(f"Error fetching bars for {symbol}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/data/fundamentals/{symbol}", dependencies=[Depends(require_token)])
def get_current_fundamentals(symbol: str) -> Dict[str, float]:
    symbol = symbol.upper()
    provider = get_provider()
    try:
        f = provider.get_fundamentals(symbol)
        if not f:
            raise HTTPException(status_code=404, detail=f"No fundamentals found for {symbol}")
        return f.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error fetching fundamentals for {symbol}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/data/fundamentals/{symbol}/history", dependencies=[Depends(require_token)])
def get_fundamental_history(symbol: str) -> Dict[str, Dict[str, float]]:
    symbol = symbol.upper()
    store = HistoricalStore(readonly=True)
    try:
        history = store.get_fundamentals_history(symbol)
        if not history:
            return {}
        return history
    except Exception as e:
        logger.exception(f"Error fetching fundamental history for {symbol}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/data/macro", dependencies=[Depends(require_token)])
def get_macro_raw() -> Dict[str, Any]:
    engine = DataEngine(settings.FRED_API_KEY or "")
    try:
        macro = engine.fetch_macro_raw()
        return macro
    except Exception as e:
        logger.exception("Error fetching macro data")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/data/universe", dependencies=[Depends(require_token)])
def get_universe() -> Dict[str, Any]:
    from data.robinhood_client import discover_universe, RobinhoodClient
    import os
    
    try:
        client = RobinhoodClient()
        if client.is_authenticated():
            universe = discover_universe(client)
        else:
            watchlist_env = os.environ.get("WATCHLIST", "")
            universe = [x.strip() for x in watchlist_env.split(",") if x.strip()]
            
        return {"symbols": universe, "count": len(universe)}
    except Exception as e:
        logger.exception("Error fetching universe")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/data/universe", dependencies=[Depends(require_token)])
def update_universe(watchlist: List[str] = Body(...)) -> Dict[str, Any]:
    from gui.env_io import write_setting
    try:
        wl_str = ",".join([s.strip().upper() for s in watchlist if s.strip()])
        write_setting("WATCHLIST", wl_str)
        return {"status": "updated", "symbols": wl_str.split(",")}
    except Exception as e:
        logger.exception("Error updating universe")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/data/quotes", dependencies=[Depends(require_token)])
def get_quotes(symbols: str) -> Dict[str, Any]:
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        return {}
    provider = get_provider()
    try:
        quotes = provider.get_quotes(sym_list)
        return {s: q.to_dict() for s, q in quotes.items() if q}
    except Exception as e:
        logger.exception("Error fetching quotes")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/data/sync-report", dependencies=[Depends(require_token)])
def get_sync_report() -> Dict[str, Any]:
    try:
        snapshot = fetch_account_snapshot(force=False)
        report = build_sync_report(snapshot)
        return dataclasses.asdict(report)
    except Exception as e:
        logger.exception("Error building sync report")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/data/account", dependencies=[Depends(require_token)])
def get_account() -> Dict[str, Any]:
    try:
        snapshot = fetch_account_snapshot(force=False)
        if not snapshot:
            raise HTTPException(status_code=404, detail="No account snapshot available")
        return dataclasses.asdict(snapshot)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error fetching account snapshot")
        raise HTTPException(status_code=500, detail=str(e))
