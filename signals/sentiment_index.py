"""
signals/sentiment_index.py — Composite Sentiment Index (S_t)
==================================================================
Computes the source methodology's composite daily sentiment index:

    S_t = w1 * news_score_t + w2 * review_score_t

Not a registered ``SignalModule`` — this is a data-layer read composing
Sentiment Source Class Phase 0's per-(symbol, trading_day) news/comment
aggregation (``HistoricalStore.get_sentiment_daily_by_source_class``) into
one scalar per day, the way ``signals/credibility.py`` sits behind
``signals/news_catalyst.py`` rather than scoring anything itself. Reuses
already-persisted FinBERT scores (``sentiment_ingestion_audit.
final_weighted_score``, averaged per class per day) — no re-scoring, no new
model call.

``w1``/``w2`` reuse ``settings.SECTOR_SELECTION_W1``/``SECTOR_SELECTION_W2``
(added alongside the semantic Related Sector Selection feature) rather than
a second, redundant pair of weight settings — those fields were introduced
specifically as the shared w1/w2 concept between the two features (see
their descriptions in ``settings.py``); this module is simply the first
actual consumer of them.

Honest degradation (CONSTRAINT #4)
------------------------------------
A day's ``news_score``/``review_score`` comes from Phase 0's per-class MEAN
final_weighted_score for that day. Unlike a volume COUNT (where zero
observations is a meaningful "0"), a MEAN over zero observations has no
valid value — Phase 0 already encodes this correctly: a class with zero
rows that day carries a ``NaN`` mean score regardless of whether the
channel is generally active (contrast with ``data.sector_selection_heat``'s
count-based degradation, which DOES need the "channel ever observed"
distinction; a mean does not).

- Both terms available: ``S_t = w1*news + w2*review``, ``degraded_reason=None``.
- Only news available: ``S_t = w1*news`` alone — NEVER ``w1*news + w2*0``,
  which would assert a neutral investor sentiment never actually observed.
  ``degraded_reason="review_unavailable"``.
- Only review available (rare): symmetric, ``degraded_reason="news_unavailable"``.
- Neither available: ``S_t = NaN``, ``degraded_reason="no_data"`` (should not
  occur given Phase 0's own contract that a present day has at least one
  real class, but handled defensively rather than crashing).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

_REASON_REVIEW_UNAVAILABLE = "review_unavailable"
_REASON_NEWS_UNAVAILABLE = "news_unavailable"
_REASON_NO_DATA = "no_data"


def compute_sentiment_index(
    symbols: List[str],
    start_day: str,
    end_day: str,
    *,
    w1: Optional[float] = None,
    w2: Optional[float] = None,
    historical_store: Optional[Any] = None,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Compute ``S_t`` per ``(symbol, trading_day)`` over ``[start_day,
    end_day]``.

    Returns ``{symbol: {trading_day: {news_score, review_score, s_t,
    degraded_reason}}}``. ``news_score``/``review_score``/``s_t`` are
    ``None`` (JSON-null / Python ``None``, never ``NaN`` in the returned
    dict) wherever unavailable.

    Returns ``{}`` immediately, with no DB read, when
    ``settings.SENTIMENT_INDEX_ENABLED`` is False or ``symbols`` is empty.
    Never raises (CONSTRAINT #6) — a read failure degrades to ``{}``.
    """
    from settings import settings

    if not settings.SENTIMENT_INDEX_ENABLED or not symbols:
        return {}

    w1 = float(w1) if w1 is not None else float(settings.SECTOR_SELECTION_W1)
    w2 = float(w2) if w2 is not None else float(settings.SECTOR_SELECTION_W2)

    if historical_store is None:
        from data.historical_store import HistoricalStore
        historical_store = HistoricalStore()

    try:
        daily = historical_store.get_sentiment_daily_by_source_class(symbols, start_day, end_day)
    except Exception:
        return {}

    result: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for symbol, by_day in daily.items():
        sym_result: Dict[str, Dict[str, Any]] = {}
        for trading_day, entry in by_day.items():
            sym_result[trading_day] = _score_day(entry, w1, w2)
        result[symbol] = sym_result
    return result


def _score_day(entry: Dict[str, float], w1: float, w2: float) -> Dict[str, Any]:
    news_score = entry.get("news_mean_score", float("nan"))
    review_score = entry.get("comment_mean_score", float("nan"))
    news_nan = math.isnan(news_score)
    review_nan = math.isnan(review_score)

    if news_nan and review_nan:
        s_t, reason = float("nan"), _REASON_NO_DATA
    elif review_nan:
        s_t, reason = w1 * news_score, _REASON_REVIEW_UNAVAILABLE
    elif news_nan:
        s_t, reason = w2 * review_score, _REASON_NEWS_UNAVAILABLE
    else:
        s_t, reason = w1 * news_score + w2 * review_score, None

    return {
        "news_score": None if news_nan else news_score,
        "review_score": None if review_nan else review_score,
        "s_t": None if math.isnan(s_t) else s_t,
        "degraded_reason": reason,
    }
