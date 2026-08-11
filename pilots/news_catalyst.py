"""
pilots/news_catalyst.py
========================
Helper to compute news-catalyst pilot telemetry without eagerly importing
`signals/news_catalyst.py` -- kept dependency-light per this repo's
`pilots-endpoint` skill (see `tests/test_pilots_strategy_matrix.py`'s
``test_pilots_read_helpers_stay_dependency_light`` AST guard). Reads from
`output/state_snapshot.json` and `HistoricalStore` (`finbert_score_cache`).
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from data.historical_store import HistoricalStore
from db_config import get_dbapi_connection, session_scope


def get_news_catalyst_coverage(
    *, store: Optional[HistoricalStore] = None, snapshot_path: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """
    Returns telemetry for the news-catalyst pilot.
    Gracefully degrades to returning `None` (never raises -- CONSTRAINT #6)
    if files/tables are missing or unreadable.

    ``store``/``snapshot_path`` are injectable (matching
    ``pilots.sector_selection.sector_selection_view``'s convention) purely
    for test isolation -- the production caller (``api/pilots_api.py``)
    leaves both ``None`` and gets the real ``output/state_snapshot.json`` /
    a fresh readonly ``HistoricalStore``.
    """
    try:
        # 1. Read universe_score_distribution from state_snapshot.json
        if snapshot_path is None:
            snapshot_path = Path("output/state_snapshot.json")
        universe_score_distribution: Dict[str, Any] = {}
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

        # 2. Read archived_score_count and headline_volume_7d from
        # HistoricalStore. readonly=True -- this is a pure telemetry read
        # helper, so it must not open a write-capable engine or run
        # _ensure_tables() DDL on every request (see HistoricalStore's own
        # docstring for the readonly contract; matches the established
        # pattern in pilots/observability.py).
        if store is None:
            store = HistoricalStore(readonly=True)
        seven_days_ago_str = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        with session_scope(store.Session) as session:
            raw_conn = session.connection().connection
            conn = get_dbapi_connection(raw_conn)

            total_row = conn.execute("SELECT COUNT(*) FROM finbert_score_cache").fetchone()
            archived_score_count = int(total_row[0]) if total_row and total_row[0] is not None else 0

            recent_row = conn.execute(
                "SELECT COUNT(*) FROM finbert_score_cache WHERE scored_at >= ?",
                (seven_days_ago_str,),
            ).fetchone()
            headline_volume_7d = int(recent_row[0]) if recent_row and recent_row[0] is not None else 0

        return {
            "archived_score_count": archived_score_count,
            "headline_volume_7d": headline_volume_7d,
            "universe_score_distribution": universe_score_distribution,
        }
    except Exception:
        return None
