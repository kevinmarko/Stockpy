"""Weekly Digest composer.

Implements WP-C for the Weekly Digest feature.
Builds a curated list of up to 5 symbols using a fallback ladder:
1. Unviewed symbols ("Personalized")
2. Underrepresented sectors ("Sector Gap")
3. Highest ranked ("Today's Radar")
"""

import logging
from typing import Any, Dict, List

from data.symbol_view_store import SymbolViewStore
from pilots.radar_ranking import radar_feed
from pilots.scoring import load_snapshot
from pilots.sector_gap import find_underrepresented_sectors

logger = logging.getLogger(__name__)

def compose_weekly_digest(limit: int = 5) -> List[Dict[str, Any]]:
    """Compose the weekly digest fallback ladder.
    
    Returns a list of up to `limit` dicts with keys:
        - symbol: str
        - reason: str (prefixed with type)
        - type: str ("Personalized", "Sector Gap", or "Today's Radar")
    """
    snapshot = load_snapshot()
    
    # Get a deep enough radar feed to draw candidates from (e.g., top 50)
    radar = radar_feed(snapshot, limit=50)
    radar_items = radar.get("items", [])
    
    if not radar_items:
        return []

    # Get WP-A: recently viewed symbols
    try:
        view_store = SymbolViewStore(readonly=True)
        viewed = set(view_store.get_recently_viewed_symbols(days=14))
    except Exception as exc:
        logger.warning("Failed to load recently viewed symbols: %s", exc)
        viewed = set()
    
    # Get WP-B: underrepresented sectors
    try:
        sector_gaps = set(find_underrepresented_sectors())
    except Exception as exc:
        logger.warning("Failed to load underrepresented sectors: %s", exc)
        sector_gaps = set()
    
    digest: List[Dict[str, Any]] = []
    seen = set()
    
    # Helper to add a candidate
    def add_candidate(item: dict, pick_type: str):
        sym = item["symbol"]
        seen.add(sym)
        reason_str = item.get("reason", "")
        digest.append({
            "symbol": sym,
            "reason": f"{pick_type}: {reason_str}",
            "type": pick_type
        })
        
    # Rung 1: Personalized (unviewed symbols)
    for item in radar_items:
        if len(digest) >= limit:
            break
        sym = item["symbol"]
        if sym not in viewed and sym not in seen:
            add_candidate(item, "Personalized")
            
    # Rung 2: Sector Gap
    for item in radar_items:
        if len(digest) >= limit:
            break
        sym = item["symbol"]
        sec = item.get("sector")
        if sym not in seen and sec in sector_gaps:
            add_candidate(item, "Sector Gap")
            
    # Rung 3: Today's Radar (fallback)
    for item in radar_items:
        if len(digest) >= limit:
            break
        sym = item["symbol"]
        if sym not in seen:
            add_candidate(item, "Today's Radar")
            
    return digest
