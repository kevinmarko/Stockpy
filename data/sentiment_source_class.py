"""
data/sentiment_source_class.py — News vs. Comment Source Taxonomy
============================================================================
A single, pure classifier deciding whether a ``sentiment_ingestion_audit``
row's ``source_name`` (Yahoo RSS, GDELT, Reddit, EDGAR, Finnhub, ...) counts
as an objective NEWS source or a subjective investor-forum COMMENT source.

This is the shared substrate for two downstream features that both need the
distinction:

- Sector Selection's Sector Heat Factor (``numNews`` + ``Review`` volume
  terms, see ``data/sector_selection_heat.py``)
- the composite sentiment index S_t = w1*news_score + w2*review_score
  (see ``signals/sentiment_index.py``)

Deliberately NOT a new ingestion source or a new table -- every document is
already persisted once in ``sentiment_ingestion_audit`` via
``HistoricalStore.save_sentiment_documents`` (Sentiment Pipeline Phase 2).
This module only decides which bucket an already-ingested ``source_name``
falls into; ``HistoricalStore.get_sentiment_daily_by_source_class`` (Phase 0
of the Sector Selection / BERT-LLA integration) does the actual aggregation.
"""

from __future__ import annotations

from typing import Literal

from settings import settings

SourceClass = Literal["news", "comment", "unknown"]


def classify_source(source_name: str) -> SourceClass:
    """Classify a ``sentiment_ingestion_audit.source_name`` value.

    Driven entirely by ``settings.SENTIMENT_COMMENT_SOURCES`` (comma-
    separated, case-insensitive) -- any source_name found there is
    ``"comment"``; any other source_name actually present in
    ``settings.SENTIMENT_SOURCES`` (or a recognized-but-not-yet-enabled
    name) is ``"news"``. A source_name recognized by neither list returns
    ``"unknown"`` rather than defaulting to news -- an unclassified source
    should never silently inflate the news volume term.

    Pure function, no I/O, no exceptions -- a malformed or empty
    ``source_name`` simply falls through to ``"unknown"``.
    """
    if not source_name:
        return "unknown"
    name = source_name.strip().lower()
    if not name:
        return "unknown"

    comment_names = _parse_names(settings.SENTIMENT_COMMENT_SOURCES)
    if name in comment_names:
        return "comment"

    news_names = _parse_names(settings.SENTIMENT_SOURCES)
    # Sources that exist in data/sentiment_sources.py but are excluded from
    # the default SENTIMENT_SOURCES fan-out (e.g. 'finnhub', 'google_news')
    # are still legitimate news sources if an operator's audit table
    # happens to carry them (e.g. after changing the setting). Recognize
    # the full known-source vocabulary here, not just what's active today.
    news_names |= _KNOWN_NEWS_SOURCES
    if name in news_names:
        return "news"

    return "unknown"


def _parse_names(csv: str) -> set[str]:
    return {n.strip().lower() for n in (csv or "").split(",") if n.strip()}


# Every SentimentSource subclass in data/sentiment_sources.py that is NOT a
# comment/forum source, whether or not it's in today's SENTIMENT_SOURCES
# default. Kept in sync manually (small, stable list) rather than importing
# data/sentiment_sources.py, which would pull in network client construction
# for a pure taxonomy check.
_KNOWN_NEWS_SOURCES = {"yahoo_rss", "gdelt", "edgar", "finnhub", "google_news"}
