"""
pilots/sector_gap.py

Sector gap composition module for Weekly Digest feature.
Computes underrepresented sectors in current paper holdings compared to the tracked universe.
"""

import logging

from data.historical_store import HistoricalStore
from data.paper_account_store import PaperAccountStore
from data.portfolio_sync import resolve_universe

logger = logging.getLogger(__name__)


def find_underrepresented_sectors() -> list[str]:
    """
    Computes sectors that are present in the tracked universe but missing
    in the current paper account holdings.
    
    This function reads from existing stores and does not trigger fresh data fetches.
    """
    try:
        # 1. Get tracked universe
        universe = resolve_universe("all", allow_live_broker_fetch=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to resolve universe: {exc}")
        universe = []

    try:
        # 2. Get open positions from PaperAccountStore
        store = PaperAccountStore(readonly=True)
        positions = store.get_open_positions()
        held_symbols = {p.symbol for p in positions}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to get paper account positions: {exc}")
        held_symbols = set()

    try:
        # 3. Resolve sectors using HistoricalStore
        hist_store = HistoricalStore(readonly=True)
        
        # We pass a large max_age_days to act strictly as a cache read 
        # avoiding fresh fetches.
        def get_sector(sym: str) -> str | None:
            try:
                raw = hist_store.get_fundamentals_raw(sym, max_age_days=99999)
                return raw.get("sector") if raw else None
            except Exception:  # noqa: BLE001
                return None
        
        universe_sectors = set()
        for sym in universe:
            sec = get_sector(sym)
            if sec:
                universe_sectors.add(sec)
                
        held_sectors = set()
        for sym in held_symbols:
            sec = get_sector(sym)
            if sec:
                held_sectors.add(sec)

        # 4. Compute underrepresented sectors (sectors in universe but missing in holdings)
        underrepresented = [sec for sec in universe_sectors if sec not in held_sectors]
        
        return sorted(underrepresented)

    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Error computing underrepresented sectors: {exc}")
        return []
