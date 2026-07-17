"""
api/metrics_api.py
===================
STANDALONE FastAPI service exposing computed indicators, models, and ML predictions.
Consumed by the React PWA to view indicators, options directives, and signals.

Run standalone:
    uvicorn api.metrics_api:app --port 8604
"""
from __future__ import annotations

import hmac
import logging
import math
from typing import Any, Dict, List, Optional
import dataclasses

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from settings import settings
from data.historical_store import HistoricalStore
from data.market_data import get_provider
from processing_engine import ProcessingEngine
from forecasting_engine import ForecastingEngine
from technical_options_engine import TechnicalOptionsEngine
from signals.registry import global_registry
import engine.advisory
from data.robinhood_portfolio import fetch_account_snapshot
from dto_models import MacroEconomicDTO
from data_engine import DataEngine

logger = logging.getLogger(__name__)

app = FastAPI(
    title="InvestYo Metrics & Signals API",
    description="Computed indicators, models, and ML predictions for the Web App.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET"],
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

def _clean_nan(d):
    if isinstance(d, dict):
        return {k: _clean_nan(v) for k, v in d.items()}
    elif isinstance(d, list):
        return [_clean_nan(x) for x in d]
    elif isinstance(d, float) and (math.isnan(d) or math.isinf(d)):
        return None
    return d

@app.get("/health")
def health_check() -> Dict[str, str]:
    return {"status": "ok", "service": "metrics_api"}

@app.get("/metrics/technicals/{symbol}", dependencies=[Depends(require_token)])
def get_technicals(symbol: str) -> Dict[str, Any]:
    symbol = symbol.upper()
    store = HistoricalStore(readonly=True)
    provider = get_provider()
    try:
        df = store.get_bars(symbol, lookback_days=252, provider=provider)
        if df.empty:
            raise HTTPException(status_code=404, detail="No bar data available")
        pe = ProcessingEngine()
        tech_df = pe.calculate_technicals_vectorized(df)
        if tech_df.empty:
            return {}
        last_row = tech_df.iloc[-1].to_dict()
        return _clean_nan(last_row)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error computing technicals for {symbol}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/metrics/forecast/{symbol}", dependencies=[Depends(require_token)])
def get_forecast(symbol: str) -> Dict[str, Any]:
    symbol = symbol.upper()
    store = HistoricalStore(readonly=True)
    provider = get_provider()
    try:
        df = store.get_bars(symbol, lookback_days=504, provider=provider)
        if df.empty:
            raise HTTPException(status_code=404, detail="No bar data available")
        quotes = provider.get_quotes([symbol])
        current_price = quotes.get(symbol).price if quotes.get(symbol) else df['Close'].iloc[-1]
        
        pe = ProcessingEngine()
        tech_df = pe.calculate_technicals_vectorized(df)
        if tech_df.empty:
            raise HTTPException(status_code=404, detail="Technicals calculation failed")
        
        fe = ForecastingEngine()
        row = tech_df.iloc[-1].copy()
        row['symbol'] = symbol
        row['current_price'] = current_price
        
        forecast_result = fe.generate_forecast(row=row, current_price=current_price, history_df=df)
        return _clean_nan(forecast_result)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error computing forecast for {symbol}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/metrics/options/{symbol}", dependencies=[Depends(require_token)])
def get_options_directive(symbol: str) -> Dict[str, Any]:
    symbol = symbol.upper()
    store = HistoricalStore(readonly=True)
    provider = get_provider()
    try:
        df = store.get_bars(symbol, lookback_days=252, provider=provider)
        if df.empty:
            raise HTTPException(status_code=404, detail="No bar data available")
        quotes = provider.get_quotes([symbol])
        current_price = quotes.get(symbol).price if quotes.get(symbol) else df['Close'].iloc[-1]
        
        toe = TechnicalOptionsEngine(stock_price=current_price)
        matrix = toe.generate_option_strategy_matrix(df, symbol=symbol)
        return _clean_nan(matrix)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error computing options matrix for {symbol}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/metrics/signals/registry", dependencies=[Depends(require_token)])
def get_signal_registry() -> Dict[str, Any]:
    try:
        signals = global_registry.get_all()
        res = []
        for s_id, s in signals.items():
            res.append({
                "id": s.name,
                "type": s.signal_type,
                "description": s.description
            })
        return {"registry": res, "count": len(res)}
    except Exception as e:
        logger.exception("Error getting signal registry")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/metrics/signals/{symbol}", dependencies=[Depends(require_token)])
def get_symbol_signals(symbol: str) -> Dict[str, Any]:
    symbol = symbol.upper()
    provider = get_provider()
    try:
        snapshot = fetch_account_snapshot(force=False)
        position = snapshot.positions.get(symbol) if snapshot else None
        
        d_eng = DataEngine(settings.FRED_API_KEY or "")
        macro_raw = d_eng.fetch_macro_raw()
        macro_dto = MacroEconomicDTO.from_raw_dict(macro_raw)
        
        rec = engine.advisory.evaluate(
            symbol=symbol,
            position=position,
            market=provider,
            snapshot=snapshot,
            macro_dto=macro_dto,
            transactions_store=None,
        )
        return {
            "symbol": rec.symbol,
            "action": rec.action,
            "score": rec.score,
            "conviction": rec.conviction,
            "signals": _clean_nan(rec.signals)
        }
    except Exception as e:
        logger.exception(f"Error evaluating signals for {symbol}")
        raise HTTPException(status_code=500, detail=str(e))
