"""
api/pilots/news_catalyst.py
===========================
Helper to compute news-catalyst metrics without eagerly importing `signals/news_catalyst.py`.
Reads from `output/state_snapshot.json` and `HistoricalStore` (finbert_score_cache).
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from data.historical_store import HistoricalStore


def get_news_catalyst_coverage() -> Optional[Dict[str, Any]]:
    """
    Returns telemetry for the news-catalyst pilot.
    Gracefully degrades to returning `None` or zeroes if files/tables are missing.
    """
    try:
        # 1. Read universe_score_distribution from state_snapshot.json
        snapshot_path = Path("output/state_snapshot.json")
        universe_score_distribution = {}
        if snapshot_path.exists():
            try:
                with open(snapshot_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                breakdown = data.get("signal_breakdown", [])
                for sig in breakdown:
                    if sig.get("name") == "news_catalyst":
                        universe_score_distribution = sig.get("distribution", {})
                        break
            except Exception:
                pass  # Ignore malformed snapshot; distribution stays {}

        # 2. Read archived_score_count and headline_volume_7d from HistoricalStore
        store = HistoricalStore()
        
        # Query total count
        query_total = "SELECT COUNT(*) FROM finbert_score_cache"
        res_total = store.execute_query(query_total, fetch=True)
        archived_score_count = res_total[0][0] if res_total and res_total[0] else 0

        # Query 7d volume
        now_utc = datetime.now(timezone.utc)
        seven_days_ago = now_utc - timedelta(days=7)
        seven_days_ago_str = seven_days_ago.isoformat()
        
        query_7d = "SELECT COUNT(*) FROM finbert_score_cache WHERE scored_at >= ?"
        res_7d = store.execute_query(query_7d, (seven_days_ago_str,), fetch=True)
        headline_volume_7d = res_7d[0][0] if res_7d and res_7d[0] else 0

        return {
            "archived_score_count": archived_score_count,
            "headline_volume_7d": headline_volume_7d,
            "universe_score_distribution": universe_score_distribution,
        }
    except Exception:
        return None
