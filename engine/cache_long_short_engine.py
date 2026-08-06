import logging
import math
from datetime import datetime, timedelta, timezone
import pandas as pd
from typing import List, Tuple, Optional, Dict, Any

from data.historical_store import HistoricalStore
from data.cache_long_short_store import CacheLongShortStore, TaxLotDTO, CacheLongShortPositionDTO
from processing_engine import calculate_rolling_beta
from pairs_ondemand import analyze_pair
from data_engine import DataEngine
from settings import settings

logger = logging.getLogger(__name__)

class CacheLongShortEngine:
    @staticmethod
    def calculate_beta(ticker: str, window: int = 60) -> Optional[float]:
        """Calculate the rolling beta of the ticker against SPY."""
        try:
            store = HistoricalStore()
            # Need a bit more than window to have a rolling value
            price_df = store.get_bars(ticker, lookback_days=window + 30)
            if price_df.empty or len(price_df) < window:
                return None
            spy_df = store.get_bars("SPY", lookback_days=window + 30)
            if spy_df.empty or len(spy_df) < window:
                return None

            beta_series = calculate_rolling_beta(price_df, spy_df, window=window)
            if beta_series.empty or beta_series.isna().all():
                return None
            val = float(beta_series.dropna().iloc[-1])
            return val if math.isfinite(val) else None
        except Exception as exc:
            logger.debug("calculate_beta failed for %s: %s", ticker, exc)
            return None

    @staticmethod
    def find_correlated_proxy(ticker: str, candidates: Optional[List[str]] = None) -> Tuple[Optional[str], Optional[float]]:
        """Find the best proxy ticker from the candidate list."""
        if not candidates:
            candidates = settings.CACHE_LONG_SHORT_PROXY_CANDIDATES
            
        best_proxy = None
        best_corr = None
        lowest_p = float('inf')
        
        provider = DataEngine()
        for candidate in candidates:
            if candidate.upper() == ticker.upper():
                continue
            res = analyze_pair(ticker, candidate, provider)
            if res.get("found", False) is False and "insufficient" in str(res.get("reason", "")).lower():
                continue
            
            p_val = res.get("rolling_p")
            if p_val is not None and p_val < lowest_p:
                lowest_p = p_val
                best_proxy = candidate
                # Estimate correlation via pandas directly for now
                try:
                    store = HistoricalStore()
                    df_t = store.get_bars(ticker, lookback_days=90)
                    df_c = store.get_bars(candidate, lookback_days=90)
                    if not df_t.empty and not df_c.empty:
                        # naive alignment
                        joined = df_t[['Close']].join(df_c[['Close']], lsuffix='_t', rsuffix='_c', how='inner')
                        corr = joined['Close_t'].corr(joined['Close_c'])
                        best_corr = float(corr) if math.isfinite(corr) else 0.0
                except Exception:
                    best_corr = 0.85 # fallback

        if best_proxy and best_corr is not None:
            # persist it
            store = CacheLongShortStore()
            store.upsert_security_proxy(ticker, best_proxy, best_corr)
        return best_proxy, best_corr

    @staticmethod
    def check_correlation_drift(ticker: str, proxy: str) -> bool:
        """Return True if correlation has drifted below the configured minimum."""
        provider = DataEngine()
        res = analyze_pair(ticker, proxy, provider)
        p_val = res.get("rolling_p")
        # Just compute actual correlation to check drift
        try:
            store = HistoricalStore()
            df_t = store.get_bars(ticker, lookback_days=90)
            df_p = store.get_bars(proxy, lookback_days=90)
            if not df_t.empty and not df_p.empty:
                joined = df_t[['Close']].join(df_p[['Close']], lsuffix='_t', rsuffix='_p', how='inner')
                corr = joined['Close_t'].corr(joined['Close_p'])
                if math.isfinite(corr) and corr < settings.CACHE_LONG_SHORT_MIN_CORRELATION:
                    return True
        except Exception:
            pass
        return False

    @staticmethod
    def check_wash_sale(ticker: str) -> bool:
        """Returns True if any closed lot for `ticker` has realized_pnl < 0 in the last 30 days."""
        store = CacheLongShortStore()
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        closed_lots = store.get_closed_lots_since(cutoff)
        
        # Filter lots to the specific ticker
        # We need to map lot_id -> position -> ticker, but our get_closed_lots_since just returns TaxLotDTOs
        # Let's get open/all positions to map position_id to ticker
        # Since get_closed_lots_since doesn't have the ticker directly, we fetch positions manually or rely on a new method.
        # For simplicity here, we assume CacheLongShortStore can fetch position by ID or we just do a quick loop if possible.
        # Actually, let's just use the store's SQL directly to check if there are any for this ticker.
        # Wait, get_closed_lots_since doesn't filter by ticker. Let's just do it cleanly inside the engine by instantiating a session.
        session = store.Session()
        try:
            from data.cache_long_short_store import CacheLongShortTaxLot, CacheLongShortPosition
            naive_cutoff = cutoff.replace(tzinfo=None)
            blocked = session.query(CacheLongShortTaxLot).join(CacheLongShortPosition).filter(
                CacheLongShortPosition.ticker == ticker.upper().strip(),
                CacheLongShortTaxLot.status == 'closed',
                CacheLongShortTaxLot.close_date >= naive_cutoff,
                CacheLongShortTaxLot.realized_pnl < 0
            ).first()
            return blocked is not None
        finally:
            session.close()

    @staticmethod
    def scan_tlh_opportunities() -> List[TaxLotDTO]:
        """Flag open lots beyond the TLH threshold percentage."""
        store = CacheLongShortStore()
        h_store = HistoricalStore()
        opportunities = []
        lots = store.get_open_tax_lots()
        
        # group lots by position_id to avoid redundant price lookups
        # wait, we need ticker per position_id
        session = store.Session()
        try:
            from data.cache_long_short_store import CacheLongShortPosition
            for lot in lots:
                pos = session.query(CacheLongShortPosition).filter(CacheLongShortPosition.id == lot.position_id).first()
                if not pos:
                    continue
                df = h_store.get_bars(pos.ticker, lookback_days=5)
                if df.empty:
                    continue
                curr_price = df.iloc[-1]['Close']
                if lot.cost_basis_per_share <= 0:
                    continue
                
                if pos.position_type == 'long':
                    unrealized_pct = (curr_price - lot.cost_basis_per_share) / lot.cost_basis_per_share
                else:
                    unrealized_pct = (lot.cost_basis_per_share - curr_price) / lot.cost_basis_per_share
                    
                if unrealized_pct < 0 and abs(unrealized_pct) > settings.CACHE_LONG_SHORT_TLH_THRESHOLD_PCT:
                    opportunities.append(lot)
        finally:
            session.close()
            
        return opportunities

    @staticmethod
    def generate_sell_down_orders(ticker: str) -> Dict[str, Any]:
        """Sizes an advisory sell-down recommendation against the tax bank, checking wash sale."""
        store = CacheLongShortStore()
        if CacheLongShortEngine.check_wash_sale(ticker):
            return {"status": "blocked", "reason": "Wash sale guardrail active (loss realized within 30d)"}
            
        tax_bank = store.tax_bank()
        if tax_bank <= 0:
            return {"status": "blocked", "reason": "No harvested tax losses available to offset gains"}
            
        return {
            "status": "approved",
            "recommended_sell_value": tax_bank, # 1:1 offset for simplicity in V1
            "reason": f"Sized to match ${tax_bank:,.2f} tax bank"
        }
