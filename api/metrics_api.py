"""
api/metrics_api.py
==================
STANDALONE FastAPI service exposing computed indicators, forecasts, options
directives, and signal breakdowns. Consumed by the React PWA.

Run standalone::

    uvicorn api.metrics_api:app --port 8604

Auth posture: a **fail-open** ``settings.STATE_API_TOKEN`` bearer (copy of
``api/state_api.py``); ``/health`` always open. GET-only CORS.

Honesty (CONSTRAINT #4): NaN/inf → ``null`` via ``_clean_nan``; a metric that
cannot be computed is ``null``, never a fabricated ``0.0``. Dead-letter safe
(CONSTRAINT #6): a per-symbol compute failure degrades to 404 / partial rather
than crashing the service.

This module MAY import the heavy calculation engines (unlike ``state_api.py`` /
``control_api.py``, which are AST-guarded against exactly that).
"""
from __future__ import annotations

import hmac
import logging
import math
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from settings import settings
from data.historical_store import HistoricalStore
from data.market_data import MarketDataError, get_provider
from processing_engine import ProcessingEngine
from forecasting_engine import ForecastingEngine
from technical_options_engine import build_premium_directive, validate_directive_integrity
from signals.registry import global_registry
from signals.aggregator import SignalAggregator
from signals.base import SignalContext
from dto_models import FundamentalDataDTO, MacroEconomicDTO, MarketBarDTO
import engine.advisory

logger = logging.getLogger(__name__)

app = FastAPI(
    title="InvestYo Metrics & Signals API",
    description="Computed indicators, forecasts, options, and signal breakdowns.",
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


def require_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> None:
    token = settings.STATE_API_TOKEN
    if not token:
        return
    presented = credentials.credentials if credentials else ""
    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


def _clean_nan(obj: Any) -> Any:
    """Recursively convert NaN/inf floats to ``None`` (JSON ``null``)."""
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_nan(x) for x in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def _fetch_bars(symbol: str, lookback_days: int) -> Optional[pd.DataFrame]:
    """HistoricalStore-routed bar fetch (provider top-up). None on failure/empty."""
    try:
        store = HistoricalStore(readonly=True)
        df = store.get_bars(symbol, lookback_days=lookback_days, provider=get_provider())
        if df is None or df.empty:
            return None
        return df
    except Exception as exc:
        logger.warning("metrics_api: bars fetch failed for %s: %s", symbol, exc)
        return None


def _current_price(symbol: str, bars: pd.DataFrame) -> float:
    """Live quote price, falling back to the last bar Close (never fabricated)."""
    try:
        return float(get_provider().get_latest_quote(symbol).price)
    except (MarketDataError, Exception):  # noqa: B014 - defensive fallback
        return float(bars["Close"].iloc[-1])


@app.get("/health")
def health_check() -> Dict[str, str]:
    return {"status": "ok", "service": "metrics_api"}


@app.get("/metrics/technicals/{symbol}", dependencies=[Depends(require_token)])
def get_technicals(symbol: str) -> Dict[str, Any]:
    """Last-row technical indicators for ``symbol`` (NaN → null).

    ``ProcessingEngine.calculate_technicals_vectorized`` takes a ``{symbol: df}``
    dict (NOT a bare DataFrame) and returns a ``{symbol: metrics_dict}`` mapping;
    each value is already a flat dict of last-row scalar indicators.
    """
    symbol = symbol.upper()
    bars = _fetch_bars(symbol, 252)
    if bars is None:
        raise HTTPException(status_code=404, detail=f"No bar data available for {symbol}")
    try:
        res = ProcessingEngine().calculate_technicals_vectorized({symbol: bars})
    except Exception as exc:
        logger.warning("metrics_api: technicals failed for %s: %s", symbol, exc)
        raise HTTPException(status_code=404, detail="Technicals calculation failed")
    metrics = res.get(symbol)
    if not metrics:
        raise HTTPException(status_code=404, detail="Insufficient data for technicals")
    return _clean_nan(dict(metrics))


@app.get("/metrics/forecast/{symbol}", dependencies=[Depends(require_token)])
def get_forecast(symbol: str) -> Dict[str, Any]:
    """Multi-horizon forecast (Forecast_10/30/60/90, ARIMA, Monte Carlo bands).

    ``ForecastingEngine.generate_forecast`` takes a ``row: pd.Series`` (for the
    sector/symbol lookup) plus ``current_price`` and the OHLCV ``history_df``;
    it internally derives the Close series, so a minimal ``row`` suffices.
    """
    symbol = symbol.upper()
    bars = _fetch_bars(symbol, 504)
    if bars is None:
        raise HTTPException(status_code=404, detail=f"No bar data available for {symbol}")
    current_price = _current_price(symbol, bars)
    row = pd.Series({"sector": "Unknown", "Symbol": symbol})
    try:
        result = ForecastingEngine().generate_forecast(
            row=row,
            current_price=current_price,
            history_series=bars["Close"],
            history_df=bars,
        )
    except Exception as exc:
        logger.warning("metrics_api: forecast failed for %s: %s", symbol, exc)
        raise HTTPException(status_code=404, detail="Forecast calculation failed")
    return _clean_nan(dict(result))


@app.get("/metrics/options/{symbol}", dependencies=[Depends(require_token)])
def get_options(symbol: str) -> Dict[str, Any]:
    """Hydrated premium-selling directive for ``symbol`` (NaN → null).

    Uses ``build_premium_directive`` — the public dict-returning helper
    (``TechnicalOptionsEngine.generate_option_strategy_matrix`` takes
    ``(true_ivr, aroon_osc, coppock_val, ...)`` and returns a *string*, so it is
    NOT the right call here). Integrity verdict is merged in.
    """
    symbol = symbol.upper()
    bars = _fetch_bars(symbol, 252)
    if bars is None:
        raise HTTPException(status_code=404, detail=f"No bar data available for {symbol}")
    is_stale = True
    try:
        quote = get_provider().get_latest_quote(symbol)
        spot = float(quote.price)
        is_stale = bool(quote.is_stale)
    except Exception:
        spot = float(bars["Close"].iloc[-1])
    try:
        directive = build_premium_directive(symbol, bars, spot_price=spot, is_stale=is_stale)
        integrity = validate_directive_integrity(directive)
        directive = dict(directive)
        directive["Integrity_OK"] = bool(integrity.get("ok"))
        directive["Integrity_Issues"] = integrity.get("issues", [])
    except Exception as exc:
        logger.warning("metrics_api: options directive failed for %s: %s", symbol, exc)
        raise HTTPException(status_code=404, detail="Options directive unavailable")
    return _clean_nan(directive)


@app.get("/metrics/sentiment/{symbol}", dependencies=[Depends(require_token)])
async def get_sentiment(symbol: str) -> Dict[str, Any]:
    """Live Sentiment Dynamics for ``symbol``, backed by Antigravity Agent."""
    symbol = symbol.upper()
    bars = _fetch_bars(symbol, 252)
    if bars is None:
        raise HTTPException(status_code=404, detail=f"No bar data available for {symbol}")
        
    from datetime import datetime
    import pandas as pd
    from sentiment_risk_engine import SentimentRiskEngine
    
    # We need the close series for returns to compute the leverage effect
    returns = bars['Close'].pct_change().dropna()
    date = datetime.now()
    
    try:
        engine = SentimentRiskEngine()
        sentiment_dto = await engine.get_live_sentiment(symbol, date, returns)
    except Exception as exc:
        logger.warning("metrics_api: sentiment dynamics failed for %s: %s", symbol, exc)
        raise HTTPException(status_code=404, detail="Sentiment calculation failed")
        
    # Convert DTO to dict for the API response
    result = {
        "ticker": sentiment_dto.ticker,
        "date": sentiment_dto.date.isoformat(),
        "sentiment_score": sentiment_dto.sentiment_score,
        "sentiment_intensity": sentiment_dto.sentiment_intensity,
        "credibility_score": sentiment_dto.credibility_score,
        "volatility_persistence": sentiment_dto.volatility_persistence
    }
    
    return _clean_nan(result)



@app.get("/metrics/signals/registry", dependencies=[Depends(require_token)])
def get_signal_registry() -> Dict[str, Any]:
    """Registered signal modules and their configured weights.

    ``SignalModule`` exposes only ``.name`` (no ``signal_type``/``description``),
    so we emit the real fields: ``id`` (== name) and its ``SIGNAL_WEIGHTS`` weight.
    """
    registry: List[Dict[str, Any]] = []
    for name, module in global_registry.get_all().items():
        registry.append(
            {
                "id": getattr(module, "name", name),
                "weight": settings.SIGNAL_WEIGHTS.get(name),
                "disabled": name in settings.DISABLED_SIGNAL_MODULES,
            }
        )
    registry.sort(key=lambda r: r["id"])
    return {"registry": registry, "count": len(registry)}


def _neutral_macro() -> MacroEconomicDTO:
    """The same neutral macro default the advisory layer uses when no FRED."""
    return MacroEconomicDTO(
        yield_curve_10y_2y=0.50,
        high_yield_oas=3.50,
        inflation_rate=3.0,
        nominal_10y=4.5,
        vix_value=18.0,
        sahm_rule_indicator=0.0,
    )


def _bar_dto(symbol: str, bars: pd.DataFrame, price: float) -> MarketBarDTO:
    last = bars.iloc[-1]
    idx = bars.index[-1]
    from datetime import datetime

    bar_date = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else datetime.now()
    return MarketBarDTO(
        date=bar_date,
        ticker=symbol,
        open_price=float(last.get("Open", price)),
        high_price=float(last.get("High", price)),
        low_price=float(last.get("Low", price)),
        close_price=price,
        volume=int(last.get("Volume", 0)),
    )


def _module_breakdown(symbol: str, provider: Any) -> Dict[str, Any]:
    """Run the SignalAggregator directly to produce ``final_score`` + per-module
    ``[{name, score, weight, contribution}]``.

    ``Recommendation`` has no ``.score``/``.signals``; the real per-module
    decomposition comes from ``SignalAggregator.aggregate()`` (6-tuple), whose
    ``outputs`` dict carries each module's raw ``.score`` in ``[-1, 1]``.
    """
    bars = _fetch_bars(symbol, 252)
    if bars is None:
        return {"final_score": None, "modules": []}
    current_price = _current_price(symbol, bars)

    tech: Dict[str, Any] = {}
    try:
        tech = ProcessingEngine().calculate_technical_metrics({symbol: bars}).get(symbol, {})
    except Exception as exc:
        logger.warning("metrics_api: technicals for signals failed for %s: %s", symbol, exc)

    fund_raw = provider.get_fundamentals(symbol) or {}
    fund_dto = FundamentalDataDTO.from_raw_dict(symbol, fund_raw)
    macro_dto = _neutral_macro()
    bar_dto = _bar_dto(symbol, bars, current_price)

    # Row shaped exactly like StrategyEngine.evaluate_security's internal row so
    # the signal modules read the fields they expect (keys mapped from the
    # ProcessingEngine metrics dict). forecast_price/garch_vol are unavailable in
    # this lightweight view → left neutral (honest, never fabricated).
    row = pd.Series(
        {
            "forecast_price": 0.0,
            "trend_strength": float(tech.get("Aroon Oscillator") or 50.0),
            "atr": float(tech.get("ATR") or 0.0),
            "macd_line": float(tech.get("MACD_Line") or 0.0),
            "macd_signal": float(tech.get("MACD_Signal") or 0.0),
            "aroon_osc": tech.get("Aroon Oscillator"),
            "rsi": tech.get("RSI"),
            "sortino_ratio": tech.get("Sortino Ratio"),
            "max_drawdown": tech.get("Max Drawdown"),
            "relative_strength": tech.get("RS vs SPY"),
            "garch_vol": float("nan"),
            "GARCH_Vol": float("nan"),
            "edge_ratio": tech.get("RS-MACD"),
            "chandelier_long": float(tech.get("Chandelier Exit") or 0.0),
            "chandelier_short": 0.0,
            "current_price": current_price,
            "Close": current_price,
            "ticker": symbol,
            "sector": fund_dto.sector,
            "roc_12m": float(tech.get("ROC_12M") or 0.0),
            "ROC_12M": float(tech.get("ROC_12M") or 0.0),
            "SMA_200": float(tech.get("SMA_200") or 0.0),
            "RSI_2": float(tech.get("RSI_2") or 50.0),
            "SMA_5": tech.get("SMA_5"),
        }
    )
    context = SignalContext(bar=bar_dto, fundamentals=fund_dto, macro=macro_dto)
    try:
        final_score_raw, _, _, _, outputs, _ = SignalAggregator(global_registry).aggregate(
            row, context
        )
    except Exception as exc:
        logger.warning("metrics_api: aggregate failed for %s: %s", symbol, exc)
        return {"final_score": None, "modules": []}

    modules: List[Dict[str, Any]] = []
    for name, out in outputs.items():
        weight = settings.SIGNAL_WEIGHTS.get(name, 0.0)
        score = getattr(out, "score", None) if out is not None else None
        if score is None or (isinstance(score, float) and math.isnan(score)):
            score = None
            contribution = None
        else:
            score = float(score)
            contribution = score * float(weight)
        modules.append(
            {"name": name, "score": score, "weight": float(weight), "contribution": contribution}
        )
    modules.sort(key=lambda m: m["name"])
    return {"final_score": round(float(final_score_raw)), "modules": modules}


@app.get("/metrics/signals/{symbol}", dependencies=[Depends(require_token)])
def get_symbol_signals(symbol: str) -> Dict[str, Any]:
    """Per-symbol signal breakdown.

    ``action`` + ``conviction`` come from the authoritative, dead-letter-safe
    ``engine.advisory.evaluate`` (it never raises). ``final_score`` +
    ``modules`` come from a direct ``SignalAggregator.aggregate`` (see
    ``_module_breakdown``). No Robinhood/FRED dependency: ``position``/
    ``snapshot`` are ``None`` and macro uses neutral defaults.
    """
    symbol = symbol.upper()
    provider = get_provider()

    action: Optional[str] = None
    conviction: Optional[float] = None
    try:
        rec = engine.advisory.evaluate(
            symbol=symbol,
            position=None,
            market=provider,
            snapshot=None,
            macro_dto=None,
        )
        action = rec.action
        conviction = rec.conviction
    except Exception as exc:  # advisory.evaluate contractually never raises, but be safe
        logger.warning("metrics_api: advisory evaluate failed for %s: %s", symbol, exc)

    breakdown = _module_breakdown(symbol, provider)

    return _clean_nan(
        {
            "symbol": symbol,
            "action": action,
            "conviction": conviction,
            "final_score": breakdown["final_score"],
            "modules": breakdown["modules"],
        }
    )
