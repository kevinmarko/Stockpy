"""
signals/credibility.py — Sentiment Credibility Scoring (Phase 4)
==================================================================
Computes per-document credibility sub-scores at INGEST time, batched over
a whole cycle's documents (never a per-document lookup in a hot per-ticker
loop). This is a data-layer concern living BEHIND the sentiment signal, not
a ``SignalModule`` itself — the sentiment-pipeline review's C5 finding was
that credibility machinery must not masquerade as the signal; the signal
(``signals/news_catalyst.py``) only ever reads the pre-aggregated output
this module produces, via ``sentiment_ingestion_audit``.

The only caller is ``data.sentiment_sources.CompositeSentimentSource._archive()``.

Sub-scores
----------
``S_authority``
    Source/account-age/follower-count based. Institutional/editorial
    sources (Finnhub, Yahoo RSS, GDELT, EDGAR) carry no author metadata by
    design — they are a deliberate MODELING CHOICE of full trust (1.0), not
    a fabricated per-document measurement (CONSTRAINT #4): we are not
    inventing a follower count for a Reuters-style headline, we are stating
    a policy that editorial copy starts fully trusted.
``S_humanity``
    Posting-cadence bot-heuristic, computed from a BATCHED per-author
    posts-per-minute statistic across the current ingestion cycle (pandas
    groupby, not a per-document network lookup or Python loop).
``S_verification``
    Placeholder full-weight (1.0) pending a real verified-claim embedding
    corpus — an unimplemented per-document RAG lookup would itself violate
    the "no per-row network I/O" rule (M3 in the review), so this field is
    kept separate and honestly un-filled rather than faked, ready for a
    future batched extension.

All thresholds below are tunables to calibrate on this platform's own
ingested data, not values copied from any external source or paper (M5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from data.sentiment_sources import SentimentDocument

logger = logging.getLogger(__name__)

# Sources with no author/follower/account-age concept at all — editorial or
# regulatory copy, treated as fully trusted by policy (see module docstring).
_INSTITUTIONAL_SOURCES = frozenset({"finnhub", "yahoo_rss", "gdelt", "edgar"})

# --- Tunables (calibrate on real ingested data before relying on these) ---
_AUTHORITY_FOLLOWER_FLOOR = 50
_AUTHORITY_FOLLOWER_CEILING = 5000
_HUMANITY_MAX_POSTS_PER_MINUTE = 5.0
_BOT_HUMANITY_THRESHOLD = 0.3
_MIN_CREDIBILITY_WEIGHT = 0.1
_MAX_CREDIBILITY_WEIGHT = 1.0


@dataclass(frozen=True)
class CredibilityScore:
    s_authority: float
    s_humanity: float
    s_verification: float
    credibility_weight: float
    is_bot: bool


def _score_authority(followers: Optional[int]) -> float:
    """[0, 1] -- higher follower count => higher authority.

    ``None`` (source doesn't expose follower counts, or the specific
    author's count is unknown) resolves to a neutral 0.5 rather than
    penalizing missing metadata as if it were a known low-authority signal.
    """
    if followers is None:
        return 0.5
    if followers <= _AUTHORITY_FOLLOWER_FLOOR:
        return 0.1
    if followers >= _AUTHORITY_FOLLOWER_CEILING:
        return 1.0
    span = _AUTHORITY_FOLLOWER_CEILING - _AUTHORITY_FOLLOWER_FLOOR
    return 0.1 + 0.9 * (followers - _AUTHORITY_FOLLOWER_FLOOR) / span


def _score_humanity(posts_per_minute: Optional[float]) -> float:
    """[0, 1] -- higher posting cadence => lower (more bot-like) score.

    ``None`` (cadence not computable -- single-post author, or source with
    no author concept) resolves to a neutral-leaning 0.7, not a penalty.
    """
    if posts_per_minute is None:
        return 0.7
    if posts_per_minute <= 0:
        return 1.0
    ratio = posts_per_minute / _HUMANITY_MAX_POSTS_PER_MINUTE
    return max(0.0, 1.0 - ratio)


def score_document(doc: SentimentDocument, posts_per_minute: Optional[float] = None) -> CredibilityScore:
    """Score one document. ``posts_per_minute`` must come from the batched
    per-author computation in :func:`score_documents` -- this function does
    no I/O and has no visibility into other documents in the batch."""
    if doc.source_name in _INSTITUTIONAL_SOURCES:
        return CredibilityScore(
            s_authority=1.0, s_humanity=1.0, s_verification=1.0,
            credibility_weight=1.0, is_bot=False,
        )

    s_authority = _score_authority(doc.author_followers)
    s_humanity = _score_humanity(posts_per_minute)
    s_verification = 1.0  # placeholder -- see module docstring
    is_bot = s_humanity < _BOT_HUMANITY_THRESHOLD
    composite = (s_authority + s_humanity + s_verification) / 3.0
    weight = max(_MIN_CREDIBILITY_WEIGHT, min(_MAX_CREDIBILITY_WEIGHT, composite))
    return CredibilityScore(
        s_authority=s_authority, s_humanity=s_humanity, s_verification=s_verification,
        credibility_weight=weight, is_bot=is_bot,
    )


def _batch_posts_per_minute(docs: List[SentimentDocument]) -> List[Optional[float]]:
    """Vectorized per-author posting-cadence stat across this batch.

    A coarse per-cycle proxy (count of this author's posts in the batch,
    normalized by the batch's own time span) -- a real per-author rolling
    history is a future extension, not fabricated here. Authors with no
    handle (``None``) get ``None`` (unknown), never a fabricated rate.
    """
    if not docs:
        return []
    df = pd.DataFrame({
        "author_handle": [d.author_handle for d in docs],
        "as_of": [d.as_of for d in docs],
    })
    has_author = df["author_handle"].notna()
    if not has_author.any():
        return [None] * len(docs)

    span_seconds = (df["as_of"].max() - df["as_of"].min()).total_seconds()
    span_minutes = max(span_seconds / 60.0, 1.0)
    counts = df.groupby("author_handle")["as_of"].transform("count")
    ppm = counts / span_minutes
    return [
        float(ppm.iloc[i]) if has_author.iloc[i] else None
        for i in range(len(docs))
    ]


def score_documents(docs: List[SentimentDocument]) -> List[CredibilityScore]:
    """Batch entry point -- the only function ``CompositeSentimentSource``
    calls. Computes the shared per-author posting-cadence statistic once
    (vectorized pandas, not a Python loop over pairs) then scores each
    document against it."""
    if not docs:
        return []
    posts_per_minute = _batch_posts_per_minute(docs)
    return [
        score_document(doc, posts_per_minute=ppm)
        for doc, ppm in zip(docs, posts_per_minute)
    ]
