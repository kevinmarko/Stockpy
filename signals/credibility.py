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
    Placeholder full-weight (1.0) by default, pending a real per-document
    check. As of Sentiment Pipeline Phase 2 PR2 ("AI-Assisted Credibility
    Filtering"), :func:`score_documents` can OPTIONALLY replace this
    placeholder with a real LLM verdict for documents whose HEURISTIC
    composite (S_authority + S_humanity) falls in a borderline band --
    gated behind ``settings.SENTIMENT_LLM_VERIFICATION_ENABLED`` (default
    ``False``, preserving today's exact 1.0-for-everyone behavior) and a
    per-cycle call budget (``settings.SENTIMENT_LLM_VERIFICATION_MAX_CALLS_PER_CYCLE``)
    plus an optional wall-clock ceiling (``remaining_seconds``). This still
    respects the "no per-row network I/O" rule (M3 in the review): the LLM
    call is bounded, cached by content hash, and skipped entirely for
    clearly-trusted/clearly-bot-flagged documents and for institutional
    sources.

All thresholds below are tunables to calibrate on this platform's own
ingested data, not values copied from any external source or paper (M5).
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

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

# LLM verification prompt -- deliberately narrow. Only the document's own
# already-archived fields go in (source_name, symbol, text_content); nothing
# computed from data after the document's own `as_of` timestamp is ever
# included (point-in-time safety -- see module docstring).
_VERIFICATION_SYSTEM_PROMPT = (
    "You are a credibility filter for a financial sentiment-ingestion "
    "pipeline. Given a single social-media post or news headline about a "
    "stock, judge whether it reads as genuine, plausible commentary -- as "
    "opposed to spam, bot-generated filler, or an obviously fabricated or "
    "manipulative claim. You are NOT judging whether the claim is true or "
    "whether the stock is a good investment -- only whether the text "
    "itself looks like real human (or legitimate institutional) commentary. "
    "Respond ONLY via the structured tool."
)


@dataclass(frozen=True)
class CredibilityScore:
    s_authority: float
    s_humanity: float
    s_verification: float
    credibility_weight: float
    is_bot: bool
    # 'placeholder' (hardcoded 1.0, pre-PR2 and still-default behavior) |
    # 'llm' (a real LLMProvider.call_structured verdict, cached or fresh) |
    # 'heuristic' (reserved for a future non-LLM check). Written through to
    # sentiment_ingestion_audit.verification_method for PIT honesty about
    # which rows got a real check.
    verification_method: str = "placeholder"


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


def score_document(
    doc: SentimentDocument,
    posts_per_minute: Optional[float] = None,
    llm_verification: Optional[float] = None,
) -> CredibilityScore:
    """Score one document. ``posts_per_minute`` must come from the batched
    per-author computation in :func:`score_documents` -- this function does
    no I/O and has no visibility into other documents in the batch.

    ``llm_verification``, when supplied, REPLACES the hardcoded
    ``S_verification=1.0`` placeholder with a real LLM-derived value in
    [0, 1] (see :func:`score_documents`). ``None`` (the default) preserves
    today's exact placeholder behavior byte-for-byte -- this function does
    no I/O itself regardless; the caller is responsible for computing
    ``llm_verification`` (batched, budgeted, cached) and passing it in.
    Institutional sources ignore this parameter entirely -- they are fully
    trusted by policy (see module docstring) and never need verification.
    """
    if doc.source_name in _INSTITUTIONAL_SOURCES:
        return CredibilityScore(
            s_authority=1.0, s_humanity=1.0, s_verification=1.0,
            credibility_weight=1.0, is_bot=False,
        )

    s_authority = _score_authority(doc.author_followers)
    s_humanity = _score_humanity(posts_per_minute)
    s_verification = 1.0 if llm_verification is None else float(llm_verification)
    verification_method = "placeholder" if llm_verification is None else "llm"
    is_bot = s_humanity < _BOT_HUMANITY_THRESHOLD
    composite = (s_authority + s_humanity + s_verification) / 3.0
    weight = max(_MIN_CREDIBILITY_WEIGHT, min(_MAX_CREDIBILITY_WEIGHT, composite))
    return CredibilityScore(
        s_authority=s_authority, s_humanity=s_humanity, s_verification=s_verification,
        credibility_weight=weight, is_bot=is_bot, verification_method=verification_method,
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


def _doc_content_hash(doc: SentimentDocument) -> str:
    """Stable cache key: ``sha256(source_name|symbol|text_content)``.

    Deliberately NOT ``data.sentiment_sources._dedup_key`` (which also mixes
    in ``trading_day``) -- that key changes across a trading-day roll for
    the SAME underlying document, which would double-charge an LLM call for
    a document that straddles a market-close roll. This key is stable
    across that roll.
    """
    raw = f"{doc.source_name}|{doc.symbol.upper()}|{doc.text_content}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _verification_user_prompt(doc: SentimentDocument) -> str:
    """Prompt content -- ONLY the document's own already-archived fields.

    Never includes anything computed from data after the document's own
    ``as_of`` timestamp (point-in-time safety -- see module docstring).
    """
    return (
        f"Source: {doc.source_name}\n"
        f"Symbol: {doc.symbol}\n"
        f"Text: {doc.text_content}"
    )


def _get_verification_provider():
    """Resolve the configured LLM provider for verification, or ``None``.

    Lazy import (project convention -- see ``llm/providers.py``'s module
    docstring): when ``SENTIMENT_LLM_VERIFICATION_ENABLED`` is False (the
    default), ``llm.router`` is never imported and no provider SDK is ever
    loaded. Soft-fails to ``None`` on any resolution error (CONSTRAINT #6).
    """
    try:
        from llm.router import get_sentiment_verification_provider
        return get_sentiment_verification_provider()
    except Exception as exc:
        logger.warning("credibility: failed to resolve verification provider: %s", exc)
        return None


def _get_verification_cache_store():
    """Construct the ``HistoricalStore`` used for the verification cache, or
    ``None`` on any construction failure (CONSTRAINT #6). Lazy import per
    this codebase's convention (see module docstring examples in
    ``data/historical_store.py``)."""
    try:
        from data.historical_store import HistoricalStore
        return HistoricalStore()
    except Exception as exc:
        logger.warning(
            "credibility: failed to construct HistoricalStore for verification cache: %s",
            exc,
        )
        return None


def score_documents(
    docs: List[SentimentDocument],
    remaining_seconds: Optional[float] = None,
) -> List[CredibilityScore]:
    """Batch entry point -- the only function ``CompositeSentimentSource``
    calls. Computes the shared per-author posting-cadence statistic once
    (vectorized pandas, not a Python loop over pairs) then scores each
    document against it.

    When ``settings.SENTIMENT_LLM_VERIFICATION_ENABLED`` is True (default
    False -- a complete no-op preserving today's exact ``S_verification=1.0``
    behavior), documents whose HEURISTIC composite
    ``(S_authority + S_humanity) / 2`` falls within
    ``[SENTIMENT_LLM_VERIFICATION_BORDERLINE_LOW,
    SENTIMENT_LLM_VERIFICATION_BORDERLINE_HIGH]`` are additionally verified
    by an LLM (via ``llm/providers.py``'s established ``call_structured``
    soft-fail contract). Institutional sources are never candidates --
    :func:`score_document` already fully trusts them.

    Bounded by TWO independent budgets, either of which silently falls the
    remaining borderline documents back to the ``S_verification=1.0``
    placeholder (never raises, never blocks ingestion -- CONSTRAINT #6):

    * ``settings.SENTIMENT_LLM_VERIFICATION_MAX_CALLS_PER_CYCLE`` -- a hard
      cap on real LLM calls made by this function.
    * ``remaining_seconds`` -- an optional wall-clock ceiling threaded in by
      the caller (``CompositeSentimentSource._archive()``, derived from its
      own per-cycle deadline) so a slow LLM never stacks its latency across
      the remainder of an already-budgeted ingestion cycle.

    A verification result is cached by content hash (see
    :func:`_doc_content_hash`) in ``sentiment_llm_verification_cache`` --
    a cache hit costs neither budget and is checked BEFORE either budget is
    consulted, so repeat documents across cycles/trading-day rolls are free.
    """
    if not docs:
        return []
    posts_per_minute = _batch_posts_per_minute(docs)
    scores = [
        score_document(doc, posts_per_minute=ppm)
        for doc, ppm in zip(docs, posts_per_minute)
    ]

    from settings import settings as _settings

    if not getattr(_settings, "SENTIMENT_LLM_VERIFICATION_ENABLED", False):
        return scores

    provider = _get_verification_provider()
    if provider is None:
        return scores

    try:
        low = float(_settings.SENTIMENT_LLM_VERIFICATION_BORDERLINE_LOW)
        high = float(_settings.SENTIMENT_LLM_VERIFICATION_BORDERLINE_HIGH)
        max_calls = int(_settings.SENTIMENT_LLM_VERIFICATION_MAX_CALLS_PER_CYCLE)
    except Exception as exc:
        logger.warning("credibility: invalid verification settings, skipping LLM step: %s", exc)
        return scores

    store = _get_verification_cache_store()
    start = time.monotonic()
    calls_made = 0

    for i, doc in enumerate(docs):
        if doc.source_name in _INSTITUTIONAL_SOURCES:
            continue  # already fully trusted -- never a verification candidate.

        score = scores[i]
        heuristic = (score.s_authority + score.s_humanity) / 2.0
        if not (low <= heuristic <= high):
            continue  # clearly-trusted or clearly-bot-flagged -- skip LLM cost.

        doc_hash = _doc_content_hash(doc)

        cached: Optional[Tuple[bool, float]] = None
        if store is not None:
            try:
                cached = store.get_cached_verification(doc_hash)
            except Exception as exc:
                logger.warning("credibility: cache read failed for %s: %s", doc_hash, exc)

        if cached is not None:
            verifiable, confidence = cached
            s_verification = confidence if verifiable else (1.0 - confidence)
            scores[i] = score_document(
                doc, posts_per_minute=posts_per_minute[i], llm_verification=s_verification,
            )
            continue

        elapsed = time.monotonic() - start
        budget_exhausted = calls_made >= max_calls or (
            remaining_seconds is not None and elapsed >= remaining_seconds
        )
        if budget_exhausted:
            continue  # falls back to the S_verification=1.0 placeholder already in scores[i].

        calls_made += 1
        try:
            from llm.schemas import SentimentDocumentVerification
            result = provider.call_structured(
                system=_VERIFICATION_SYSTEM_PROMPT,
                user=_verification_user_prompt(doc),
                schema_model=SentimentDocumentVerification,
            )
        except Exception as exc:
            logger.warning("credibility: verification call failed for %s: %s", doc_hash, exc)
            result = None

        if result is None:
            continue  # soft-fail (CONSTRAINT #6) -- placeholder 1.0 stands.

        s_verification = result.confidence if result.verifiable else (1.0 - result.confidence)
        scores[i] = score_document(
            doc, posts_per_minute=posts_per_minute[i], llm_verification=s_verification,
        )

        if store is not None:
            try:
                store.save_verification(doc_hash, result.verifiable, result.confidence)
            except Exception as exc:
                logger.warning("credibility: cache write failed for %s: %s", doc_hash, exc)

    return scores
