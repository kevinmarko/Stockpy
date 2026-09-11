import logging
from collections import Counter
from typing import List

from data.paper_account_store import PaperAccountStore
from data.historical_store import HistoricalStore
from data.portfolio_sync import resolve_universe

logger = logging.getLogger(__name__)

def find_underrepresented_sectors() -> List[str]:
    """
    Computes diagnostic list of sectors that are missing or underrepresented
    in the current paper holdings compared to the tracked universe.
    """
    try:
        # 1. Read current paper holdings
        paper_store = PaperAccountStore(readonly=True)
        open_positions = paper_store.get_open_positions()
        holdings = [p.symbol for p in open_positions]

        # 2. Get tracked universe
        universe = resolve_universe()
        if not universe:
            return []

        # 3. Resolve sectors using HistoricalStore
        store = HistoricalStore(readonly=True)
        
        holding_sectors = []
        for sym in holdings:
            raw = store.get_fundamentals_raw(sym, max_age_days=99999) or {}
            sector = raw.get("sector")
            if sector:
                holding_sectors.append(sector)
                
        universe_sectors = []
        for sym in universe:
            raw = store.get_fundamentals_raw(sym, max_age_days=99999) or {}
            sector = raw.get("sector")
            if sector:
                universe_sectors.append(sector)

        if not universe_sectors:
            return []

        # 4. Compute underrepresented sectors
        uni_counts = Counter(universe_sectors)
        hold_counts = Counter(holding_sectors)
        
        uni_total = sum(uni_counts.values())
        hold_total = sum(hold_counts.values())
        
        underrepresented = []
        for sector, uni_count in uni_counts.items():
            uni_pct = uni_count / uni_total
            hold_pct = hold_counts.get(sector, 0) / hold_total if hold_total > 0 else 0.0
            
            # Missing completely or underrepresented proportionally
            if hold_counts.get(sector, 0) == 0 or hold_pct < uni_pct:
                underrepresented.append(sector)

        return underrepresented

    except Exception as e:
        logger.warning(f"Sector gap computation degraded gracefully: {e}")
        return []
