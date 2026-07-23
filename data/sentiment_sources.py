"""
data/sentiment_sources.py — Multi-Source Sentiment Ingestion (Phase 3)
=======================================================================
Provider abstraction for the sentiment-ingestion pipeline, structurally
mirroring ``data/market_data.py``'s ``MarketDataProvider`` ABC /
``CompositeProvider`` pattern -- with one key difference: market data
selects ONE backend, but sentiment FANS OUT across every enabled source,
since each source contributes independent documents rather than competing
answers to the same question.

Free-first by design
---------------------
Default sources (``settings.SENTIMENT_SOURCES``): Yahoo Finance RSS, GDELT,
Reddit's official API, SEC/EDGAR, and the existing Finnhub feed
(``signals/news_catalyst.py``). This runs locally on the platform's usual
Mac-mini / free-tier footprint -- no paid feed is wired up. A future paid
source (e.g. a licensed X/Twitter tier) can be added later as a new
``SentimentSource`` subclass with zero changes to ``CompositeSentimentSource``
or the signal that consumes it -- the same seam ``MarketDataProvider`` leaves
open for Alpaca vs. yfinance today.

``GoogleNewsRSSSource`` (``"google_news"``) is registered but deliberately
NOT in the ``SENTIMENT_SOURCES`` default -- it's free/no-auth like the
others, but opt-in only (add ``"google_news"`` to ``SENTIMENT_SOURCES`` in
``.env`` to enable it), consistent with ``SENTIMENT_INGESTION_ENABLED``
also defaulting to ``False``. See its class docstring for the query
simplification and opaque-redirect-link caveats.

No execution surface
---------------------
This module does ONLY data ingestion. It has no import of, or reference to,
``OrderManager``/``BrokerBase``/any execution module, and must never gain
one -- rate-limit or backpressure handling here sheds or defers DATA WORK
only (lower-priority sources skipped for the rest of a cycle), never touches
orders. This is the fix for the reviewed plan's most severe finding
(execution code inside an ingestion component).

Backpressure
------------
``CompositeSentimentSource`` runs a bounded, in-process (not distributed)
per-cycle document budget (``settings.SENTIMENT_MAX_DOCUMENTS_PER_CYCLE``).
Sources are polled in priority order (``_SOURCE_PRIORITY`` -- regulatory/
established feeds first, noisier social feeds last); once the budget is
exhausted, remaining lower-priority sources are skipped for the rest of the
cycle. Call ``CompositeSentimentSource.reset_cycle()`` once per orchestrator
cycle before iterating symbols.

Two more bounds close the gap a per-request ``timeout`` alone leaves open --
a per-request timeout bounds ONE call, but nothing previously bounded the
whole cycle if a source kept timing out symbol after symbol:

- **Wall-clock ceiling** (``settings.SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE``,
  default 60s): once elapsed (tracked from ``reset_cycle()``), ``fetch_all()``
  returns immediately for every remaining symbol this cycle -- fails fast and
  moves on rather than stalling the whole pipeline refresh.
- **Per-source circuit breaker** (``settings.SENTIMENT_CIRCUIT_BREAKER_THRESHOLD``,
  default 3): after that many consecutive failures for one source within a
  cycle, that source is skipped for the rest of the cycle instead of being
  re-attempted (and re-timing-out) for every remaining symbol.

Credibility-field honesty
--------------------------
``SentimentDocument`` carries optional credibility-relevant raw inputs
(``author_followers``, ``account_age_days``, ``posts_per_minute``). Sources
that don't carry this metadata (Finnhub headlines, Yahoo RSS, GDELT, EDGAR
filings) leave these ``None`` -- never fabricated (CONSTRAINT #4). Only
``RedditSource`` can plausibly populate ``posts_per_minute``-adjacent
signals; full author-account lookups (follower counts, account age) are
deferred to Phase 4's credibility engine, which may make additional batched
calls rather than one-per-document in the hot path (see signals/credibility.py).

Trading-day roll
-----------------
Every document is stamped with a resolved ``trading_day`` via
``HistoricalStore.resolve_trading_day()`` (imported lazily per this
codebase's existing convention for avoiding circular imports with
``data/historical_store.py``) -- the leakage-critical UTC->ET post-close
roll, computed once, shared by every source, rather than each provider
reimplementing it.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Type

import requests

logger = logging.getLogger(__name__)

# Sources polled first when the per-cycle document budget is under pressure --
# established/regulatory feeds outrank noisier social feeds (never orders).
# "google_news" sits alongside yahoo_rss/gdelt (a news-aggregator tier, noisier
# than the regulatory/single-publisher feeds ahead of it but not the social
# tier that follows) -- ahead of reddit.
_SOURCE_PRIORITY: List[str] = ["finnhub", "edgar", "yahoo_rss", "gdelt", "google_news", "reddit"]


# ---------------------------------------------------------------------------
# Document dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SentimentDocument:
    """One ingested headline/post/filing, prior to credibility scoring.

    Attributes
    ----------
    as_of : datetime
        Raw publish/post timestamp (UTC-aware; naive is treated as UTC by
        downstream trading-day resolution).
    symbol : str
        Ticker this document is about.
    source_name : str
        One of ``_SOURCE_PRIORITY``'s names.
    text_content : str
        Headline/title or post body text.
    raw_sentiment_score : float
        FinBERT/lexicon score in [-1, 1] -- pre-credibility.
    author_handle : Optional[str]
        Author/username where the source carries one; ``None`` otherwise.
    author_followers : Optional[int]
        Follower count where the source exposes it; ``None`` when unknown
        (never fabricated -- CONSTRAINT #4).
    account_age_days : Optional[float]
        Author account age in days where known; ``None`` otherwise.
    """

    as_of: datetime
    symbol: str
    source_name: str
    text_content: str
    raw_sentiment_score: float
    author_handle: Optional[str] = None
    author_followers: Optional[int] = None
    account_age_days: Optional[float] = None

    def to_audit_row(self) -> Dict[str, Any]:
        """Shape this document for ``HistoricalStore.save_sentiment_documents()``."""
        return {
            "as_of": self.as_of,
            "symbol": self.symbol,
            "source_name": self.source_name,
            "author_handle": self.author_handle,
            "text_content": self.text_content,
            "raw_sentiment_score": self.raw_sentiment_score,
        }


# ---------------------------------------------------------------------------
# Dedup + optional desentencize preprocessing
# ---------------------------------------------------------------------------

def _dedup_key(doc: SentimentDocument, trading_day: str) -> str:
    """Rolling dedup hash: same source+symbol+trading_day+text is one document,
    even if two overlapping fetch windows both return it."""
    raw = f"{doc.source_name}|{doc.symbol.upper()}|{trading_day}|{doc.text_content}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# Known abbreviations whose internal period must NOT become a semicolon.
_PROTECTED_ABBREVIATIONS: List[str] = [
    "U.S.", "U.K.", "Inc.", "Corp.", "Co.", "Ltd.", "Mr.", "Mrs.", "Dr.", "vs.", "etc.",
]
# Decimal-number periods (e.g. the "." in "$4.50") must also survive.
_DECIMAL_PATTERN = re.compile(r"\d\.\d")
_SENTINEL = ""  # private-use codepoint; never appears in real ingested text


def desentencize(text: str) -> str:
    """Replace sentence-ending periods with semicolons, protecting decimal
    numbers (``$4.50``) and known abbreviations (``U.S.``) from corruption.

    Gated behind ``settings.SENTIMENT_DESENTENCIZE_ENABLED`` (default False)
    -- see the review finding this addresses (M7): a real but marginal
    FinBERT trick that must not mangle numerics or abbreviations.
    """
    protected = _DECIMAL_PATTERN.sub(lambda m: m.group(0).replace(".", _SENTINEL), text)
    for abbr in _PROTECTED_ABBREVIATIONS:
        protected = protected.replace(abbr, abbr.replace(".", _SENTINEL))
    protected = protected.replace(".", ";")
    return protected.replace(_SENTINEL, ".")


# ---------------------------------------------------------------------------
# Abstract provider interface
# ---------------------------------------------------------------------------

class SentimentSource(ABC):
    """Abstract contract for all sentiment-ingestion backends.

    Implementations must never raise out of ``fetch()`` for network/parse
    failures -- catch internally and return ``[]`` (CONSTRAINT #6, dead-letter
    resilience: one source's outage never blocks the others).
    """

    name: str = ""

    @abstractmethod
    def fetch(self, symbol: str, since: datetime) -> List[SentimentDocument]:
        """Return documents for ``symbol`` published at/after ``since``.

        Must return ``[]`` (never raise) on any network/parse/config failure.
        """


# ---------------------------------------------------------------------------
# Finnhub — wraps the existing signals/news_catalyst.py client helpers so
# Finnhub participates in this abstraction rather than living outside it.
# ---------------------------------------------------------------------------

class FinnhubSentimentSource(SentimentSource):
    """Wraps ``signals.news_catalyst``'s existing Finnhub client + scoring
    helpers. No credibility metadata (Finnhub headlines carry no author/
    follower data) -- ``author_followers``/``account_age_days`` stay ``None``.
    """

    name = "finnhub"

    def fetch(self, symbol: str, since: datetime) -> List[SentimentDocument]:
        try:
            from signals.news_catalyst import (
                build_finnhub_client,
                fetch_company_news,
                _score_headline,
                _get_finbert_pipeline,
            )
            from settings import settings as _settings

            client = build_finnhub_client()
            if client is None:
                return []
            lookback_days = max(1, (datetime.now(timezone.utc) - since).days or 1)
            pipeline = _get_finbert_pipeline() if _settings.FINBERT_ENABLED else None
            items = fetch_company_news(client, symbol, lookback_days)
            docs: List[SentimentDocument] = []
            for item in items:
                headline = item.get("headline", "")
                if not headline:
                    continue
                ts = item.get("datetime")
                as_of = (
                    datetime.fromtimestamp(ts, tz=timezone.utc)
                    if ts else datetime.now(timezone.utc)
                )
                if as_of < since:
                    continue
                score = _score_headline(headline, pipeline)
                docs.append(SentimentDocument(
                    as_of=as_of, symbol=symbol.upper(), source_name=self.name,
                    text_content=headline, raw_sentiment_score=score,
                ))
            return docs
        except Exception as exc:
            logger.warning("FinnhubSentimentSource.fetch(%s) failed: %s", symbol, exc)
            return []


# ---------------------------------------------------------------------------
# Yahoo Finance RSS — free, no auth.
# ---------------------------------------------------------------------------

class YahooRSSSource(SentimentSource):
    """Yahoo Finance per-symbol headline RSS feed. No credibility metadata."""

    name = "yahoo_rss"
    _FEED_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"

    def fetch(self, symbol: str, since: datetime) -> List[SentimentDocument]:
        try:
            from bs4 import BeautifulSoup
            resp = requests.get(
                self._FEED_URL,
                params={"s": symbol, "region": "US", "lang": "en-US"},
                timeout=10,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, "xml")
            docs: List[SentimentDocument] = []
            for item in soup.find_all("item"):
                title = item.title.get_text(strip=True) if item.title else ""
                if not title:
                    continue
                as_of = self._parse_pubdate(item.pubDate.get_text(strip=True) if item.pubDate else "")
                if as_of is None or as_of < since:
                    continue
                docs.append(SentimentDocument(
                    as_of=as_of, symbol=symbol.upper(), source_name=self.name,
                    text_content=title, raw_sentiment_score=self._score(title),
                ))
            return docs
        except Exception as exc:
            logger.warning("YahooRSSSource.fetch(%s) failed: %s", symbol, exc)
            return []

    @staticmethod
    def _parse_pubdate(raw: str) -> Optional[datetime]:
        if not raw:
            return None
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _score(headline: str) -> float:
        from signals.news_catalyst import _score_headline
        return _score_headline(headline, None)  # lexicon fallback (no FinBERT here)


# ---------------------------------------------------------------------------
# GDELT — free, no auth, global news-tone database.
# ---------------------------------------------------------------------------

class GDELTSource(SentimentSource):
    """GDELT 2.0 DOC API -- free, no auth. Article-level tone as a proxy score.

    Historical backfill: GDELT's DOC API caps at ``_MAX_RECORDS_PER_CALL``
    (250) results per call, so a ``since`` more than a few days in the past
    is queried in ``_CHUNK_DAYS``-wide date-bounded windows
    (``startdatetime``/``enddatetime``) rather than one "most recent" call
    client-side-filtered by ``since`` -- that approach would silently return
    only today's newest articles for any backfill request, never genuine
    historical ones, since GDELT always sorts/caps before any date filtering
    happens on our end. Each window is independently try/excepted
    (CONSTRAINT #6): one failed window is skipped, not fatal to the rest of
    the range. Capped at ``_MAX_WINDOWS`` chunks as a safety bound against an
    unreasonably distant ``since``.
    """

    name = "gdelt"
    _API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
    _MAX_RECORDS_PER_CALL = 250  # GDELT DOC API's own per-call ceiling
    _CHUNK_DAYS = 7
    _MAX_WINDOWS = 60  # safety bound (~14 months at 7-day chunks)

    def fetch(self, symbol: str, since: datetime) -> List[SentimentDocument]:
        now = datetime.now(timezone.utc)
        docs: List[SentimentDocument] = []
        window_start = since
        windows = 0
        while window_start < now and windows < self._MAX_WINDOWS:
            window_end = min(window_start + timedelta(days=self._CHUNK_DAYS), now)
            docs.extend(self._fetch_window(symbol, window_start, window_end))
            window_start = window_end
            windows += 1
        return docs

    def _fetch_window(
        self, symbol: str, window_start: datetime, window_end: datetime,
    ) -> List[SentimentDocument]:
        try:
            resp = requests.get(
                self._API_URL,
                params={
                    "query": symbol,
                    "mode": "artlist",
                    "format": "json",
                    "maxrecords": self._MAX_RECORDS_PER_CALL,
                    "sort": "datedesc",
                    "startdatetime": window_start.strftime("%Y%m%d%H%M%S"),
                    "enddatetime": window_end.strftime("%Y%m%d%H%M%S"),
                },
                timeout=10,
            )
            resp.raise_for_status()
            payload = resp.json()
            docs: List[SentimentDocument] = []
            for article in payload.get("articles", []):
                title = article.get("title", "")
                if not title:
                    continue
                as_of = self._parse_seendate(article.get("seendate", ""))
                if as_of is None:
                    continue
                tone = article.get("tone")
                score = self._tone_to_score(tone) if tone is not None else self._score(title)
                docs.append(SentimentDocument(
                    as_of=as_of, symbol=symbol.upper(), source_name=self.name,
                    text_content=title, raw_sentiment_score=score,
                ))
            return docs
        except Exception as exc:
            logger.warning(
                "GDELTSource.fetch(%s) window [%s, %s] failed: %s",
                symbol, window_start.isoformat(), window_end.isoformat(), exc,
            )
            return []

    @staticmethod
    def _parse_seendate(raw: str) -> Optional[datetime]:
        # GDELT seendate format: "YYYYMMDDTHHMMSSZ"
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _tone_to_score(tone: Any) -> float:
        # GDELT tone is roughly [-100, 100]; scale to [-1, 1] and clamp.
        try:
            return max(-1.0, min(1.0, float(tone) / 10.0))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _score(text: str) -> float:
        from signals.news_catalyst import _score_headline
        return _score_headline(text, None)


# ---------------------------------------------------------------------------
# Google News RSS — free, no auth, aggregator of many publishers' headlines.
# ---------------------------------------------------------------------------

class GoogleNewsRSSSource(SentimentSource):
    """Google News RSS search feed -- free, no auth.

    Query simplification (documented, not hidden): a bare ticker symbol
    (e.g. ``"AAPL"``) makes for a poor Google News search query on its own
    (ticker collisions, thin results). Rather than adding a symbol -> company
    -name resolution dependency (a new API call or lookup table) just to
    disambiguate, this source queries as ``f"{symbol} stock"`` -- a simple,
    dependency-free disambiguator that is good enough for headline-level
    sentiment, per the research that motivated this feature. This means
    results skew toward whatever Google's own relevance ranking considers
    "<TICKER> stock" news, not a precise company-name match.

    Time window: ``settings.GOOGLE_NEWS_LOOKBACK_WINDOW`` (default ``"7d"``)
    is appended to the query as Google News' own ``when:`` search operator
    (e.g. ``"AAPL stock when:7d"``) -- narrows the feed server-side, on top
    of (not instead of) this method's own client-side ``since`` filter.

    Opaque redirect links: every ``<link>`` in a Google News RSS response is
    an opaque ``news.google.com/rss/articles/...`` redirect token, NOT the
    publisher's real URL -- this defeats URL-based dedup entirely (there is
    no publisher URL to dedup on). The same story run by multiple outlets
    would otherwise be double-counted, so this source performs its own
    fuzzy-title dedup (``_normalize_title`` + Jaccard token-overlap
    similarity, see ``_fuzzy_dedup``) on the titles it parses, BEFORE
    documents are even returned from ``fetch()`` -- on top of (not instead
    of) the composite's own exact-text dedup in ``CompositeSentimentSource``.

    Capped at ``_MAX_ITEMS`` (~100) -- Google's own feed limit; there is no
    pagination parameter to request more than one page.

    No credibility metadata (Google News headlines carry no author/follower
    data) -- ``author_followers``/``account_age_days`` stay ``None``.
    """

    name = "google_news"
    _FEED_URL = "https://news.google.com/rss/search"
    _MAX_ITEMS = 100  # Google's own feed cap; no pagination exists
    _SIMILARITY_THRESHOLD = 0.8  # Jaccard token-overlap treated as "same story"

    def fetch(self, symbol: str, since: datetime) -> List[SentimentDocument]:
        try:
            from bs4 import BeautifulSoup
            from settings import settings as _settings

            window = (_settings.GOOGLE_NEWS_LOOKBACK_WINDOW or "7d").strip()
            query = f"{symbol} stock when:{window}" if window else f"{symbol} stock"
            resp = requests.get(
                self._FEED_URL,
                params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
                timeout=10,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, "xml")

            candidates: List[Dict[str, Any]] = []
            for item in soup.find_all("item")[: self._MAX_ITEMS]:
                title = item.title.get_text(strip=True) if item.title else ""
                if not title:
                    continue
                as_of = self._parse_pubdate(
                    item.pubDate.get_text(strip=True) if item.pubDate else ""
                )
                if as_of is None or as_of < since:
                    continue
                candidates.append({"title": title, "as_of": as_of})

            deduped = self._fuzzy_dedup(candidates)
            if not deduped:
                return []

            scores = self._score_batch([c["title"] for c in deduped])
            docs: List[SentimentDocument] = []
            for candidate, score in zip(deduped, scores):
                docs.append(SentimentDocument(
                    as_of=candidate["as_of"], symbol=symbol.upper(), source_name=self.name,
                    text_content=candidate["title"], raw_sentiment_score=score,
                ))
            return docs
        except Exception as exc:
            logger.warning("GoogleNewsRSSSource.fetch(%s) failed: %s", symbol, exc)
            return []

    @staticmethod
    def _parse_pubdate(raw: str) -> Optional[datetime]:
        if not raw:
            return None
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _normalize_title(title: str) -> str:
        """Lowercase + collapse whitespace so trivial formatting differences
        between outlets (extra spaces, casing) don't defeat the similarity
        check below."""
        return re.sub(r"\s+", " ", title.strip().lower())

    @classmethod
    def _fuzzy_dedup(cls, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Collapse near-duplicate titles (the same story syndicated/rewritten
        by multiple outlets) using Jaccard token-overlap similarity on
        normalized titles -- see the class docstring for why URL-based dedup
        cannot work here (every ``<link>`` is an opaque redirect token).
        Keeps the first occurrence of each cluster (candidates arrive in the
        feed's own order); O(n^2) over at most ``_MAX_ITEMS`` items, so this
        stays cheap.
        """
        kept: List[Dict[str, Any]] = []
        kept_token_sets: List[set] = []
        for candidate in candidates:
            tokens = set(cls._normalize_title(candidate["title"]).split())
            if not tokens:
                continue
            is_duplicate = False
            for existing_tokens in kept_token_sets:
                union = tokens | existing_tokens
                if not union:
                    continue
                jaccard = len(tokens & existing_tokens) / len(union)
                if jaccard >= cls._SIMILARITY_THRESHOLD:
                    is_duplicate = True
                    break
            if not is_duplicate:
                kept.append(candidate)
                kept_token_sets.append(tokens)
        return kept

    @staticmethod
    def _score_batch(titles: List[str]) -> List[float]:
        """Batch-score every headline from one ``fetch()`` call through
        ``signals.news_catalyst.score_headlines()`` -- PR417's batched entry
        point -- in a single call, rather than looping single-headline
        scoring, mirroring ``GDELTSource._score()``'s lazy import of
        ``signals.news_catalyst`` but calling the batched function directly
        since batching one fetch() call's headlines together is the whole
        point of the newly available path. Falls back to an all-zero score
        list (never raises -- CONSTRAINT #6) if scoring itself fails.
        """
        if not titles:
            return []
        try:
            from signals.news_catalyst import (
                _distribution_to_signed,
                _get_finbert_pipeline,
                score_headlines,
            )
            from settings import settings as _settings

            pipeline = _get_finbert_pipeline() if _settings.FINBERT_ENABLED else None
            distributions = score_headlines(titles, pipeline=pipeline)
            return [
                max(-1.0, min(1.0, _distribution_to_signed(dist)))
                for dist in distributions
            ]
        except Exception as exc:
            logger.warning("GoogleNewsRSSSource: batch scoring failed: %s", exc)
            return [0.0] * len(titles)


# ---------------------------------------------------------------------------
# Reddit — official API, OAuth2 client-credentials (script app).
# ---------------------------------------------------------------------------

class RedditSource(SentimentSource):
    """Reddit official API (OAuth2 client-credentials grant, read-only search).

    Requires ``REDDIT_CLIENT_ID``/``REDDIT_CLIENT_SECRET``; degrades to an
    empty result (no crash) when absent, same shape as Finnhub's degrade-mode.
    Only ``author_handle`` is populated -- ``author_followers``/
    ``account_age_days`` require a separate per-author lookup, deferred to
    Phase 4's credibility engine rather than fetched per-document here.

    Historical backfill caveat (documented, not hidden): Reddit's search API
    can reach posts well beyond ``t=day`` -- ``_time_bucket_for()`` picks the
    narrowest ``t=`` bucket (hour/day/week/month/year/all) that still covers
    ``since``, and pagination follows the ``after`` cursor up to
    ``settings.REDDIT_BACKFILL_MAX_PAGES`` pages. But a backfilled post's
    credibility sub-scores (``signals/credibility.py``'s ``S_authority``,
    driven by ``author_followers``) can only ever reflect the author's
    CURRENT account state -- Reddit's API has no way to ask "what was this
    account's standing 5 months ago." A backfilled post is therefore scored
    with today's credibility, not the account's credibility at post time --
    a real degradation the sentiment-pipeline review flagged (M1), not
    something this implementation can close. This is unlike GDELT/EDGAR/
    Finnhub, which are institutional sources policy-trusted at 1.0
    regardless of when they're scored (see ``signals/credibility.py``'s
    ``_INSTITUTIONAL_SOURCES``), so backfilling them carries no such caveat.
    """

    name = "reddit"
    _TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
    _SEARCH_URL = "https://oauth.reddit.com/search"

    def __init__(self) -> None:
        self._token: Optional[str] = None

    def fetch(self, symbol: str, since: datetime) -> List[SentimentDocument]:
        try:
            from settings import settings as _settings
            if not _settings.REDDIT_CLIENT_ID or not _settings.REDDIT_CLIENT_SECRET:
                return []
            token = self._get_token(_settings)
            if token is None:
                return []

            docs: List[SentimentDocument] = []
            after: Optional[str] = None
            max_pages = int(_settings.REDDIT_BACKFILL_MAX_PAGES)
            time_bucket = self._time_bucket_for(since)

            for _page in range(max_pages):
                params: Dict[str, Any] = {
                    "q": f"${symbol}", "sort": "new", "limit": 100, "t": time_bucket,
                }
                if after:
                    params["after"] = after
                try:
                    resp = requests.get(
                        self._SEARCH_URL,
                        headers={
                            "Authorization": f"bearer {token}",
                            "User-Agent": _settings.REDDIT_USER_AGENT,
                        },
                        params=params,
                        timeout=10,
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                except Exception as exc:
                    logger.warning("RedditSource.fetch(%s) page failed: %s", symbol, exc)
                    break

                children = payload.get("data", {}).get("children", [])
                if not children:
                    break

                hit_cutoff = False
                for child in children:
                    post = child.get("data", {})
                    title = post.get("title", "")
                    if not title:
                        continue
                    created = post.get("created_utc")
                    as_of = (
                        datetime.fromtimestamp(created, tz=timezone.utc)
                        if created is not None else datetime.now(timezone.utc)
                    )
                    if as_of < since:
                        # sort=new -> every subsequent post (this page and
                        # later pages) is even older; stop entirely.
                        hit_cutoff = True
                        break
                    docs.append(SentimentDocument(
                        as_of=as_of, symbol=symbol.upper(), source_name=self.name,
                        text_content=title, raw_sentiment_score=self._score(title),
                        author_handle=post.get("author"),
                    ))

                if hit_cutoff:
                    break
                after = payload.get("data", {}).get("after")
                if not after:
                    break

            return docs
        except Exception as exc:
            logger.warning("RedditSource.fetch(%s) failed: %s", symbol, exc)
            return []

    @staticmethod
    def _time_bucket_for(since: datetime) -> str:
        """Narrowest Reddit ``t=`` search bucket that still covers ``since``."""
        now = datetime.now(timezone.utc)
        delta = now - since
        if delta <= timedelta(hours=1):
            return "hour"
        if delta <= timedelta(days=1):
            return "day"
        if delta <= timedelta(days=7):
            return "week"
        if delta <= timedelta(days=31):
            return "month"
        if delta <= timedelta(days=366):
            return "year"
        return "all"

    def _get_token(self, settings_obj: Any) -> Optional[str]:
        if self._token is not None:
            return self._token
        try:
            resp = requests.post(
                self._TOKEN_URL,
                auth=(settings_obj.REDDIT_CLIENT_ID, settings_obj.REDDIT_CLIENT_SECRET),
                data={"grant_type": "client_credentials"},
                headers={"User-Agent": settings_obj.REDDIT_USER_AGENT},
                timeout=10,
            )
            resp.raise_for_status()
            self._token = resp.json().get("access_token")
            return self._token
        except Exception as exc:
            logger.warning("RedditSource: token request failed: %s", exc)
            return None

    @staticmethod
    def _score(text: str) -> float:
        from signals.news_catalyst import _score_headline
        return _score_headline(text, None)


# ---------------------------------------------------------------------------
# SEC EDGAR — free, no auth, requires a compliant User-Agent.
# ---------------------------------------------------------------------------

class EdgarSource(SentimentSource):
    """SEC EDGAR recent-filings feed (8-K current reports) for a ticker.

    Requires ``EDGAR_USER_AGENT`` (SEC's fair-access policy); degrades to an
    empty result rather than send a non-compliant request. No credibility
    metadata -- filings have no author/follower concept.
    """

    name = "edgar"
    _TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    _SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:0>10}.json"

    def __init__(self) -> None:
        self._ticker_to_cik: Optional[Dict[str, str]] = None

    def fetch(self, symbol: str, since: datetime) -> List[SentimentDocument]:
        try:
            from settings import settings as _settings
            if not _settings.EDGAR_USER_AGENT:
                return []
            headers = {"User-Agent": _settings.EDGAR_USER_AGENT}
            cik = self._lookup_cik(symbol, headers)
            if cik is None:
                return []
            resp = requests.get(
                self._SUBMISSIONS_URL.format(cik=cik), headers=headers, timeout=10,
            )
            resp.raise_for_status()
            payload = resp.json()
            recent = payload.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])
            descriptions = recent.get("primaryDocDescription", [])
            docs: List[SentimentDocument] = []
            for form, date_str, desc in zip(forms, dates, descriptions):
                if form != "8-K":
                    continue
                try:
                    as_of = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if as_of < since:
                    continue
                text = desc or f"{symbol} 8-K filing"
                docs.append(SentimentDocument(
                    as_of=as_of, symbol=symbol.upper(), source_name=self.name,
                    text_content=text, raw_sentiment_score=self._score(text),
                ))
            return docs
        except Exception as exc:
            logger.warning("EdgarSource.fetch(%s) failed: %s", symbol, exc)
            return []

    def _lookup_cik(self, symbol: str, headers: Dict[str, str]) -> Optional[str]:
        if self._ticker_to_cik is None:
            resp = requests.get(self._TICKERS_URL, headers=headers, timeout=10)
            resp.raise_for_status()
            payload = resp.json()
            self._ticker_to_cik = {
                entry["ticker"].upper(): str(entry["cik_str"])
                for entry in payload.values()
            }
        return self._ticker_to_cik.get(symbol.upper())

    @staticmethod
    def _score(text: str) -> float:
        from signals.news_catalyst import _score_headline
        return _score_headline(text, None)


# ---------------------------------------------------------------------------
# Composite fan-out
# ---------------------------------------------------------------------------

_SOURCE_REGISTRY: Dict[str, Type[SentimentSource]] = {
    "finnhub": FinnhubSentimentSource,
    "yahoo_rss": YahooRSSSource,
    "gdelt": GDELTSource,
    "google_news": GoogleNewsRSSSource,
    "reddit": RedditSource,
    "edgar": EdgarSource,
}


class CompositeSentimentSource:
    """Fans out to every enabled source (``settings.SENTIMENT_SOURCES``),
    applies shared preprocessing (trading-day roll, dedup, optional
    desentencize), and archives to ``sentiment_ingestion_audit`` when
    ``settings.SENTIMENT_AUDIT_ENABLED``.

    Unlike ``data.market_data.CompositeProvider`` (selects ONE backend), this
    fans OUT -- every enabled source's documents are merged (deduplicated),
    not compared/selected between.
    """

    def __init__(self, sources: Optional[Dict[str, SentimentSource]] = None) -> None:
        self._sources = sources if sources is not None else self._build_enabled_sources()
        self._documents_this_cycle = 0
        self._consecutive_failures: Dict[str, int] = {}
        self._tripped_sources: set = set()
        self._cycle_deadline: Optional[float] = None
        self._deadline_logged = False

    @staticmethod
    def _build_enabled_sources() -> Dict[str, SentimentSource]:
        from settings import settings as _settings
        enabled_names = [
            n.strip().lower() for n in _settings.SENTIMENT_SOURCES.split(",") if n.strip()
        ]
        sources: Dict[str, SentimentSource] = {}
        for name in enabled_names:
            cls = _SOURCE_REGISTRY.get(name)
            if cls is None:
                logger.warning(
                    "CompositeSentimentSource: unknown source %r in "
                    "SENTIMENT_SOURCES, skipping.", name,
                )
                continue
            try:
                sources[name] = cls()
            except Exception as exc:
                logger.warning(
                    "CompositeSentimentSource: failed to construct source %r: %s",
                    name, exc,
                )
        return sources

    def reset_cycle(self) -> None:
        """Reset the per-cycle document budget, circuit breaker, and
        wall-clock ceiling. Call once per orchestrator cycle before
        iterating symbols."""
        from settings import settings as _settings
        self._documents_this_cycle = 0
        self._consecutive_failures = {}
        self._tripped_sources = set()
        self._deadline_logged = False
        self._cycle_deadline = time.monotonic() + float(
            _settings.SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE
        )

    def fetch_all(self, symbol: str, since: Optional[datetime] = None) -> List[SentimentDocument]:
        """Fetch and merge documents for ``symbol`` from every enabled source.

        Sources are polled in ``_SOURCE_PRIORITY`` order; once the per-cycle
        document budget (``settings.SENTIMENT_MAX_DOCUMENTS_PER_CYCLE``) is
        reached, remaining lower-priority sources are skipped for the rest of
        the cycle -- data-only backpressure, never touches orders (see module
        docstring's C1 note). Also bounded by a wall-clock ceiling
        (``settings.SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE``, tracked from
        ``reset_cycle()``) and a per-source circuit breaker
        (``settings.SENTIMENT_CIRCUIT_BREAKER_THRESHOLD`` consecutive
        failures) -- see the module docstring's Backpressure section.
        """
        from settings import settings as _settings
        from data.historical_store import HistoricalStore  # lazy import (project convention)

        if self._cycle_deadline is not None and time.monotonic() >= self._cycle_deadline:
            if not self._deadline_logged:
                logger.warning(
                    "CompositeSentimentSource: per-cycle wall-clock budget "
                    "(%.0f s) exceeded; skipping ingestion for the rest of "
                    "this cycle.",
                    _settings.SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE,
                )
                self._deadline_logged = True
            return []

        if since is None:
            since = datetime.now(timezone.utc) - timedelta(
                days=_settings.SENTIMENT_INGESTION_LOOKBACK_DAYS
            )

        merged: List[SentimentDocument] = []
        seen_hashes: set = set()
        budget = int(_settings.SENTIMENT_MAX_DOCUMENTS_PER_CYCLE)
        breaker_threshold = int(_settings.SENTIMENT_CIRCUIT_BREAKER_THRESHOLD)

        ordered_names = [n for n in _SOURCE_PRIORITY if n in self._sources]
        ordered_names += [n for n in self._sources if n not in _SOURCE_PRIORITY]

        for name in ordered_names:
            if name in self._tripped_sources:
                continue
            if self._documents_this_cycle >= budget:
                logger.warning(
                    "CompositeSentimentSource: per-cycle document budget (%d) "
                    "reached; skipping remaining lower-priority source %r for %s.",
                    budget, name, symbol,
                )
                continue
            source = self._sources[name]
            try:
                docs = source.fetch(symbol, since)
                self._consecutive_failures[name] = 0
            except Exception as exc:
                logger.warning(
                    "CompositeSentimentSource: source %r failed for %s: %s",
                    name, symbol, exc,
                )
                failures = self._consecutive_failures.get(name, 0) + 1
                self._consecutive_failures[name] = failures
                if failures >= breaker_threshold:
                    self._tripped_sources.add(name)
                    logger.warning(
                        "CompositeSentimentSource: source %r tripped the "
                        "circuit breaker after %d consecutive failures; "
                        "skipping it for the rest of this cycle.",
                        name, failures,
                    )
                continue
            for doc in docs:
                trading_day = HistoricalStore.resolve_trading_day(doc.as_of)
                key = _dedup_key(doc, trading_day)
                if key in seen_hashes:
                    continue
                seen_hashes.add(key)
                if _settings.SENTIMENT_DESENTENCIZE_ENABLED:
                    doc = SentimentDocument(
                        as_of=doc.as_of, symbol=doc.symbol, source_name=doc.source_name,
                        text_content=desentencize(doc.text_content),
                        raw_sentiment_score=doc.raw_sentiment_score,
                        author_handle=doc.author_handle,
                        author_followers=doc.author_followers,
                        account_age_days=doc.account_age_days,
                    )
                merged.append(doc)
                self._documents_this_cycle += 1

        return merged

    def fetch_and_archive(
        self, symbol: str, since: Optional[datetime] = None,
    ) -> List[SentimentDocument]:
        """``fetch_all()`` plus a best-effort archive write to
        ``sentiment_ingestion_audit`` (CONSTRAINT #6: archive failures never
        propagate).

        Threads the remaining wall-clock budget in THIS cycle (derived from
        ``self._cycle_deadline``, set by ``reset_cycle()``) into
        ``_archive()`` so ``signals.credibility.score_documents()``'s
        optional LLM-verification step can stop early rather than stack a
        slow LLM call's latency on top of an already-budgeted ingestion
        cycle. ``None`` when no cycle deadline is set (e.g. ``reset_cycle()``
        was never called) -- the LLM step then only bounds itself by
        ``SENTIMENT_LLM_VERIFICATION_MAX_CALLS_PER_CYCLE``.
        """
        docs = self.fetch_all(symbol, since)
        remaining_seconds: Optional[float] = None
        if self._cycle_deadline is not None:
            remaining_seconds = self._cycle_deadline - time.monotonic()
        self._archive(docs, remaining_seconds=remaining_seconds)
        return docs

    @staticmethod
    def _archive(docs: List[SentimentDocument], remaining_seconds: Optional[float] = None) -> None:
        """Score (Phase 4 credibility) then persist a batch of documents.

        Credibility scoring runs once per batch here -- the only call site
        -- so ``signals/credibility.py``'s per-author cadence statistic sees
        the whole cycle's documents at once (see its module docstring).
        ``remaining_seconds`` (default ``None``) is threaded into
        ``score_documents()`` to bound its optional LLM-verification step
        (Sentiment Pipeline Phase 2 PR2) by the same per-cycle wall-clock
        budget this class already enforces for fetching.
        """
        if not docs:
            return
        try:
            from settings import settings as _settings
            if not _settings.SENTIMENT_AUDIT_ENABLED:
                return
            from data.historical_store import HistoricalStore
            from signals.credibility import score_documents

            scores = score_documents(docs, remaining_seconds=remaining_seconds)
            rows = []
            for doc, score in zip(docs, scores):
                row = doc.to_audit_row()
                row["s_authority"] = score.s_authority
                row["s_humanity"] = score.s_humanity
                row["s_verification"] = score.s_verification
                row["credibility_weight"] = score.credibility_weight
                row["is_bot"] = int(score.is_bot)
                row["verification_method"] = score.verification_method
                row["final_weighted_score"] = doc.raw_sentiment_score * score.credibility_weight
                rows.append(row)
            HistoricalStore().save_sentiment_documents(rows)
        except Exception as exc:
            logger.warning("CompositeSentimentSource: audit archive failed: %s", exc)


# ---------------------------------------------------------------------------
# Module-level singleton (mirrors data.market_data.get_provider/reset_provider)
# ---------------------------------------------------------------------------

_default_source: Optional[CompositeSentimentSource] = None


def get_sentiment_source() -> CompositeSentimentSource:
    """Return the module-level ``CompositeSentimentSource`` singleton.

    Constructing on first call so import-time side effects are avoided
    (tests can set env vars / settings before calling this).
    """
    global _default_source
    if _default_source is None:
        _default_source = CompositeSentimentSource()
    return _default_source


def reset_sentiment_source() -> None:
    """Force-reset the singleton (useful in tests to re-evaluate settings)."""
    global _default_source
    _default_source = None
