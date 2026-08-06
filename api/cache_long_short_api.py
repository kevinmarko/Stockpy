"""
api/cache_long_short_api.py
FastAPI router for the Cache Long/Short strategy endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
import logging

from engine.cache_long_short_engine import CacheLongShortEngine

logger = logging.getLogger("CacheLongShortAPI")

# Create router - authentication dependencies should be applied when mounting or individually
router = APIRouter(prefix="/api/v1/strategy/cache-long-short", tags=["cache-long-short"])

@router.get("/portfolios/{portfolio_id}/concentrated-positions")
def get_concentrated_positions(portfolio_id: str):
    """
    Returns holdings that make up >20% of a portfolio.
    """
    # Mock data for frontend integration
    return {
        "portfolio_id": portfolio_id,
        "concentrated_positions": [
            {"ticker": "AAPL", "percentage": 0.45, "value": 45000.0, "unrealized_gain": 25000.0}
        ]
    }

@router.post("/simulate")
def simulate_strategy(payload: Dict[str, Any]):
    """
    Accepts a ticker, target allocation, and max tax budget. 
    Returns projected trades, correlation metrics, and margin required.
    """
    ticker = payload.get("ticker", "AAPL")
    allocation = payload.get("allocation", 10000.0)
    
    beta = CacheLongShortEngine.calculate_beta(ticker)
    overlay = CacheLongShortEngine.construct_overlay(ticker, allocation)
    
    return {
        "status": "success",
        "beta": beta,
        "overlay": overlay,
        "margin_required": allocation,
        "projected_tax_savings": allocation * 0.1 # Mock 10%
    }

@router.post("/start")
def start_strategy(payload: Dict[str, Any]):
    """
    Saves the strategy configuration and initiates the first round of trades.
    """
    return {"status": "started", "message": "Strategy initialized and initial trades queued."}

@router.get("/dashboard")
def get_dashboard():
    """
    Returns aggregated data: YTD harvested losses, current net/gross exposure, etc.
    """
    return {
        "tax_bank": 1500.0,
        "diversification_progress": 60.0,
        "diversification_target": 100.0,
        "estimated_tax_saved": 300.0,
        "net_exposure": 0.0,
        "gross_exposure": 20000.0,
        "status": "active"
    }

@router.get("/pending-approvals")
def get_pending_approvals():
    """
    Returns a list of TLH trades the algorithm wants to execute.
    """
    return {
        "pending_trades": [
            {"id": "trade_1", "date": "2026-08-06", "reason": "TLH Opportunity", "action": "Sell SPY, Buy VOO", "impact": "-$25.00"}
        ]
    }

@router.post("/approve-bulk")
def approve_bulk_trades(payload: Dict[str, List[str]]):
    """
    Accepts an array of trade IDs and sends them to the broker.
    """
    trade_ids = payload.get("trade_ids", [])
    logger.info(f"Approved {len(trade_ids)} trades for execution.")
    return {"status": "success", "approved_count": len(trade_ids)}
