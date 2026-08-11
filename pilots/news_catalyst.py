"""pilots/news_catalyst.py -- News Catalyst Pilot telemetry (READ-ONLY).

Answers "how much real news-catalyst coverage backs this Pilot right now":
how many FinBERT-scored headlines have ever been archived, how many landed
in the last 7 days, and how the CURRENT cycle's per-symbol news-sentiment
scores are distributed across the tracked universe.

Design invariants (matches the rest of the Pilots read layer -- see
``pilots/strategy_matrix.py``/``pilots/options.py`` for the same pattern):

* **Dependency-light** -- stdlib + ``settings`` + ``data.historical_store``
  only. Never imports ``signals.news_catalyst`` (or any of ``signals`` at
  all): ``api/pilots_api.py`` is AST-guarded against directly importing a
  heavy calculation engine, and while the guard's literal denylist doesn't
  include ``signals``, importing it anyway would eagerly pull in all ~17
  signal modules -- passing the guard's letter while defeating its intent
  (see ``.claude/skills/pilots-endpoint/SKILL.md``). Everything here reads
  data the pipeline already persisted -- ``output/state_snapshot.json`` and
  the ``finbert_score_cache`` table -- never a live signal computation.
* **Never raises (CONSTRAINT #6)** -- a missing/corrupt snapshot, an
  unreachable DB, or any other failure degrades to ``None``, always with a
  ``logger.debug(...)`` call first so a silent-forever failure is at least
  traceable.
* **Never fabricates (CONSTRAINT #4)** -- ``universe_score_distribution`` is
  built only from the CURRENT cycle's real, already-persisted per-symbol
  ``news_sentiment`` field (see ``main_orchestrator.py``'s
  ``_write_state_snapshot``/``reporting/state_snapshot.py``'s
  ``write_state_snapshot`` -- both thread ``NewsCatalystSignal.pre_compute()``'s
  raw score through under this exact key). A symbol with no real score this
  cycle is simply excluded from the histogram, never counted as neutral.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_SNAPSHOT_FILENAME = "state_snapshot.json"

# Bucket thresholds for the current-cycle universe distribution -- match the
# lexicon/FinBERT convention elsewhere in this codebase (a small dead zone
# around 0 counts as neutral rather than splitting hairs on noise).
_NEUTRAL_BAND = 0.05


def _default_snapshot_path() -> Path:
    from settings import settings

    return settings.OUTPUT_DIR / _SNAPSHOT_FILENAME


def _load_snapshot(snapshot_path: Optional[str] = None) -> Optional[dict]:
    path = Path(snapshot_path) if snapshot_path else _default_snapshot_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("news_catalyst: failed to read/parse %s: %s", path, exc)
        return None


def _universe_score_distribution(snapshot: Optional[dict]) -> Dict[str, int]:
    """Bucket the CURRENT cycle's real per-symbol ``news_sentiment`` values
    (written by ``NewsCatalystSignal.pre_compute()`` every cycle -- see
    module docstring) into positive/neutral/negative counts. A symbol with
    no ``news_sentiment`` entry (feature disabled, or a fetch/scoring
    failure that cycle) is excluded entirely -- never counted as neutral,
    which would misrepresent "no data" as "measured and flat" (CONSTRAINT #4).
    """
    dist = {"positive": 0, "neutral": 0, "negative": 0}
    if not snapshot:
        return dist
    for row in snapshot.get("signals", []) or []:
        score = row.get("news_sentiment")
        if score is None:
            continue
        try:
            score = float(score)
        except (TypeError, ValueError):
            continue
        if score > _NEUTRAL_BAND:
            dist["positive"] += 1
        elif score < -_NEUTRAL_BAND:
            dist["negative"] += 1
        else:
            dist["neutral"] += 1
    return dist


def get_news_catalyst_coverage(snapshot_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Telemetry for the ``news-catalyst`` Pilot's ``GET /pilots/{pilot_id}``
    ``news_coverage`` field.

    Returns::

        {
          "archived_score_count": int,   # total finbert_score_cache rows, ever
          "headline_volume_7d": int,     # finbert_score_cache rows in the last 7 days
          "universe_score_distribution": {"positive": int, "neutral": int, "negative": int},
        }

    or ``None`` on any failure (dead DB, corrupt snapshot, etc.) -- never
    raises, never a fabricated partial result (CONSTRAINT #6).
    """
    try:
        from data.historical_store import HistoricalStore

        store = HistoricalStore(readonly=True)
        archived_score_count = store.count_finbert_scores()
        since = datetime.now(timezone.utc) - timedelta(days=7)
        headline_volume_7d = store.count_finbert_scores(since=since)
    except Exception as exc:
        logger.debug("news_catalyst.get_news_catalyst_coverage: DB read failed: %s", exc)
        return None

    snapshot = _load_snapshot(snapshot_path)
    universe_score_distribution = _universe_score_distribution(snapshot)

    return {
        "archived_score_count": archived_score_count,
        "headline_volume_7d": headline_volume_7d,
        "universe_score_distribution": universe_score_distribution,
    }
