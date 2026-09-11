import logging
from typing import Optional

from pilots.scoring import load_snapshot
from pilots.radar_ranking import radar_feed
from data.symbol_view_store import SymbolViewStore
from pilots.sector_gap import find_underrepresented_sectors
from pilots.digest_models import DigestPayload, DigestItem

logger = logging.getLogger(__name__)

def compose_digest(snapshot_path: Optional[str] = None) -> DigestPayload:
    """
    Combines top-ranked radar symbols with viewing history and sector gaps
    to produce a curated weekly digest of up to 5 items.
    """
    snapshot = load_snapshot(snapshot_path)
    if not snapshot:
        return DigestPayload(items=[])

    feed = radar_feed(snapshot, limit=50)
    items = feed.get("items", [])
    if not items:
        return DigestPayload(items=[])

    try:
        store = SymbolViewStore(readonly=True)
        recently_viewed = set(store.get_recently_viewed_symbols(days=14))
    except Exception as exc:
        logger.warning(f"Failed to fetch recently viewed symbols: {exc}")
        recently_viewed = set()
    
    underrepresented_sectors = set(find_underrepresented_sectors())

    digest_items = []
    for item in items:
        if len(digest_items) >= 5:
            break
            
        symbol = item.get("symbol")
        if not symbol:
            continue
            
        sector = item.get("sector", "")
        reason = item.get("reason", "")
        
        if symbol not in recently_viewed:
            selection_type = "Personalized"
        elif sector and sector in underrepresented_sectors:
            selection_type = "Sector Gap"
        else:
            selection_type = "Today's Radar"
            
        digest_items.append(DigestItem(
            symbol=symbol,
            reason=reason,
            selection_type=selection_type
        ))

    return DigestPayload(items=digest_items)
