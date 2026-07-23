"""
data/attention_sources.py — Investor Attention Proxy (Wikipedia Pageviews)
============================================================================
Grounded in Da, Engelberg & Gao (2011), "In Search of Attention," Journal of
Finance 66(5):1461-1499 -- the finding that spikes in a stock's Google
Search Volume Index (SVI) predict higher prices over the following ~2 weeks,
with an eventual partial reversal, and that investor-attention proxies more
generally carry real, tradeable signal distinct from price/volume momentum.

Why Wikipedia, not Google Trends, is PRIMARY
---------------------------------------------
Google Trends via the unofficial `pytrends` scraper is the obvious modern
proxy for DEG's SVI, but it is explicitly NOT the primary mechanism here:
`pytrends`'s underlying repo (`GeneralMills/pytrends`) was archived
read-only in April 2025 (no further maintenance, ever) and is well known to
be rate-limit-fragile -- frequent HTTP 429s even under light, single-
operator use, because it scrapes an undocumented, unstable Google endpoint
rather than calling a real public API. That fragility is a genuine
limitation of the very research this feature is grounded in: SVI-based
attention measures are only as good as continued access to Google's data,
and that access has degraded since 2011. Wikipedia's official Pageviews
REST API (`https://wikimedia.org/api/rest_v1/metrics/pageviews/...`) is
free, documented, stable, requires no auth, and is the reliable substitute
recommended in the literature that motivated this feature -- so it is the
PRIMARY mechanism here. `pytrends` is wired in as a strictly OPTIONAL,
best-effort SUPPLEMENTARY path (see `_fetch_pytrends_attention_score()`
below) that is NEVER load-bearing: it is only even attempted when Wikipedia
produced nothing usable for a symbol, it makes exactly one attempt with no
retries, and any failure (429, timeout, missing optional dependency,
malformed response) degrades silently to "no score" -- never raises, never
blocks, never substitutes for a real value (CONSTRAINT #6 / CONSTRAINT #4).

Provider abstraction, NOT a SentimentSource
--------------------------------------------
This module deliberately does NOT subclass `data.sentiment_sources
.SentimentSource` -- that ABC's contract is document ingestion
(`fetch(symbol, since) -> List[SentimentDocument]`), a structurally
different concern from this module's numeric attention/volume score. This
module instead defines its own small, analogous abstraction
(`AttentionSource`), following the same house conventions as
`sentiment_sources.py` / `market_data.py`: lazy imports of `settings` /
`data.historical_store`, `requests`-based HTTP with `timeout=10` +
`raise_for_status()`, and a strict "never raise out of a fetch -- degrade
to `None`/NaN" dead-letter discipline.

Ticker -> Wikipedia article-title resolution (SIMPLE, best-effort)
--------------------------------------------------------------------
Wikipedia article titles are almost always company names, not ticker
symbols -- e.g. the real article for `AAPL` is "Apple Inc.", not "AAPL".
This module does NOT build a dedicated ticker->company-name resolution
service (that would be a new API dependency all its own, out of scope for
this first cut). Instead `resolve_article_title()` tries the ticker symbol
itself first (works for the minority of tickers that also happen to be a
real article/redirect title), then falls back to a `company_name` string if
the caller supplies one (e.g. from `dto_models.FundamentalDataDTO
.company_name`, already computed this cycle by `ProcessingStep` in
`pipeline/production_steps.py`). For most symbols with no usable
`company_name` on hand, this will legitimately 404 and degrade to NaN --
that is an accepted, documented limitation of this first cut, not a bug.

Attention-score transform (documented reasoning)
---------------------------------------------------
`_abnormal_attention_score()` computes a Da/Engelberg/Gao-style "abnormal
attention" measure, substituting Wikipedia daily pageviews for Google SVI:

    Attention_Score = log(1 + recent_mean) - log(1 + baseline_median)

where `recent_mean` is the mean of the most recent `_RECENT_WINDOW_DAYS`
(3) days of pageviews and `baseline_median` is the MEDIAN (not mean --
robust to a single earlier spike, matching DEG's own choice of median over
their 8-week baseline) of every earlier day within
`settings.WIKIPEDIA_ATTENTION_LOOKBACK_DAYS`. The log-difference (rather
than a raw ratio) keeps the score symmetric around 0 and comparable across
tickers of very different baseline pageview magnitude (a mega-cap's article
gets orders of magnitude more traffic than a small-cap's) -- this mirrors
DEG's own Abnormal Search Volume Index definition, `log(SVI) -
log(median(SVI, 8 prior weeks))`. Returns NaN (never a fabricated 0.0 --
CONSTRAINT #4) whenever there isn't enough distinct daily history to trust
the recent/baseline split.

No lookahead
------------
`_abnormal_attention_score(daily_series, as_of=...)` explicitly drops any
row dated strictly after `as_of` before computing recent/baseline splits --
defense-in-depth against a malformed or (in tests) deliberately
future-dated response ever leaking into "today's" score. See
`tests/test_attention_pit_lookahead.py`.
"""

from __future__ import annotations

import logging
import math
import statistics
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

# Identifies this platform to the Wikimedia REST API per its API etiquette
# policy (https://meta.wikimedia.org/wiki/User-Agent_policy) -- a generic,
# non-secret string, not fabricated data.
_WIKIMEDIA_USER_AGENT = "InvestYo-QuantPlatform/1.0 (github.com/kevinmarko/stockpy)"

# "Recent" window for the abnormal-attention transform -- last N days'
# average pageviews vs. the earlier baseline median.
_RECENT_WINDOW_DAYS = 3
# Minimum days of BASELINE (i.e. excluding the recent window) history
# required before the transform is trusted; below this, NaN.
_MIN_BASELINE_DAYS = 7


# ---------------------------------------------------------------------------
# Small provider abstraction (analogous to, but distinct from,
# data.sentiment_sources.SentimentSource -- see module docstring).
# ---------------------------------------------------------------------------

class AttentionSource(ABC):
    """Abstract contract for daily attention/volume proxies.

    Implementations must never raise out of ``fetch_daily_series()`` for
    network/parse/config failures -- catch internally and return ``None``
    (CONSTRAINT #6, dead-letter resilience). ``None`` must never be papered
    over with a fabricated value downstream (CONSTRAINT #4).
    """

    name: str = ""

    @abstractmethod
    def fetch_daily_series(self, query: str, lookback_days: int) -> Optional[Dict[str, float]]:
        """Return ``{"YYYY-MM-DD": value}`` for the trailing ``lookback_days``.

        Must return ``None`` (never raise, never an empty dict standing in
        for "confirmed zero attention") on any failure.
        """


# ---------------------------------------------------------------------------
# Wikipedia Pageviews REST API -- PRIMARY mechanism (free, official, no auth).
# ---------------------------------------------------------------------------

class WikipediaPageviewsSource(AttentionSource):
    """Wikimedia Pageviews REST API, ``per-article`` endpoint -- daily human
    ("user", i.e. excluding bots/spiders) pageview counts for one English
    Wikipedia article.

    See the module docstring for why this (not Google Trends/pytrends) is
    the primary mechanism, and for the ticker->article-title resolution
    limitation.
    """

    name = "wikipedia"
    _BASE_URL = (
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        "en.wikipedia/all-access/user/{article}/daily/{start}/{end}"
    )

    def fetch_daily_series(self, article_title: str, lookback_days: int) -> Optional[Dict[str, float]]:
        if not article_title or not str(article_title).strip():
            return None
        try:
            end = datetime.now(timezone.utc).date()
            start = end - timedelta(days=int(lookback_days))
            url = self._BASE_URL.format(
                article=quote(str(article_title).strip().replace(" ", "_"), safe=""),
                start=start.strftime("%Y%m%d"),
                end=end.strftime("%Y%m%d"),
            )
            resp = requests.get(
                url, headers={"User-Agent": _WIKIMEDIA_USER_AGENT}, timeout=10,
            )
            resp.raise_for_status()
            payload = resp.json()
            series: Dict[str, float] = {}
            for item in payload.get("items", []):
                ts = item.get("timestamp")  # "YYYYMMDD00" (hourly granularity token, unused)
                views = item.get("views")
                if ts is None or views is None:
                    continue
                try:
                    date_str = datetime.strptime(str(ts)[:8], "%Y%m%d").strftime("%Y-%m-%d")
                    series[date_str] = float(views)
                except (ValueError, TypeError):
                    continue
            return series if series else None
        except Exception as exc:
            logger.warning(
                "WikipediaPageviewsSource.fetch_daily_series(%r) failed: %s",
                article_title, exc,
            )
            return None


def resolve_article_title(symbol: str, company_name: Optional[str] = None) -> List[str]:
    """Ordered, best-effort list of candidate Wikipedia article titles for
    ``symbol`` -- SIMPLE by design, see module docstring. Try order:

    1. The ticker symbol itself.
    2. ``company_name`` (if supplied and distinct/non-placeholder) -- the
       more reliable candidate in practice, tried second since it requires
       the caller to already have fundamentals for this symbol on hand.
    """
    candidates: List[str] = []
    sym = (symbol or "").strip()
    if sym:
        candidates.append(sym)
    if company_name:
        cleaned = str(company_name).strip()
        if cleaned and cleaned.upper() not in ("N/A", "UNKNOWN", "UNKNOWN ASSET") and cleaned != sym:
            candidates.append(cleaned)
    return candidates


# ---------------------------------------------------------------------------
# Abnormal-attention transform (Da/Engelberg/Gao-style) -- no lookahead.
# ---------------------------------------------------------------------------

def _abnormal_attention_score(
    daily_series: Optional[Dict[str, float]], as_of: Optional[datetime] = None,
) -> float:
    """See module docstring's "Attention-score transform" section for the
    full reasoning. Returns NaN (never fabricated) on insufficient history.

    ``as_of`` bounds the computation to rows dated at/before it (defense-in-
    depth against any future-dated row in ``daily_series`` -- see
    ``tests/test_attention_pit_lookahead.py``).
    """
    if not daily_series:
        return float("nan")
    if as_of is None:
        as_of = datetime.now(timezone.utc)
    cutoff = as_of.date() if hasattr(as_of, "date") else as_of

    dated: Dict[object, float] = {}
    for date_str, value in daily_series.items():
        try:
            d = datetime.strptime(str(date_str), "%Y-%m-%d").date()
        except ValueError:
            continue
        if d > cutoff:
            # Never let a future-dated row (malformed response, or a
            # deliberately future-dated fixture in tests) leak into a
            # same-day-or-earlier attention score.
            continue
        dated[d] = value

    if not dated:
        return float("nan")

    ordered_dates = sorted(dated.keys())
    recent_dates = ordered_dates[-_RECENT_WINDOW_DAYS:]
    baseline_dates = ordered_dates[:-_RECENT_WINDOW_DAYS]
    if len(baseline_dates) < _MIN_BASELINE_DAYS or not recent_dates:
        return float("nan")

    recent_mean = sum(dated[d] for d in recent_dates) / len(recent_dates)
    baseline_median = statistics.median(dated[d] for d in baseline_dates)
    return math.log1p(recent_mean) - math.log1p(baseline_median)


# ---------------------------------------------------------------------------
# Optional pytrends (Google Trends) supplementary path -- NEVER load-bearing.
# ---------------------------------------------------------------------------

def _fetch_pytrends_attention_score(symbol: str, lookback_days: int) -> Optional[float]:
    """Best-effort, strictly supplementary Google Trends overlay.

    Only ever called when Wikipedia produced no usable score for ``symbol``
    (see ``compute_attention_score``) -- pytrends is never preferred over,
    and never required alongside, Wikipedia data. See the module docstring
    for why `pytrends` (archived, rate-limit-fragile) must never be
    load-bearing. This function:

      - makes exactly ONE attempt, no retries -- a 429 is a terminal
        soft-fail for this symbol this cycle, never something to hammer
        again (that would only make the rate-limiting worse);
      - never raises -- any exception (429/`TooManyRequestsError`, timeout,
        the optional `pytrends` package not being installed, a malformed
        response) degrades silently to ``None`` (CONSTRAINT #6);
      - never fabricates a value on failure (CONSTRAINT #4).
    """
    try:
        from pytrends.request import TrendReq  # optional dep -- requirements-optional.txt
    except Exception as exc:
        logger.info(
            "pytrends not installed / import failed (%s); skipping optional overlay.", exc,
        )
        return None
    try:
        pytrends = TrendReq(hl="en-US", tz=0)
        months = max(1, int(lookback_days) // 30)
        pytrends.build_payload([symbol], timeframe=f"today {months}-m")
        df = pytrends.interest_over_time()
        if df is None or df.empty or symbol not in df.columns:
            return None
        series = df[symbol].astype(float)
        if len(series) < 2:
            return None
        recent = float(series.iloc[-1])
        baseline = float(series.iloc[:-1].median())
        return math.log1p(recent) - math.log1p(baseline)
    except Exception as exc:
        # Deliberately broad and logged at WARNING, not ERROR: a pytrends
        # failure (most commonly an HTTP 429) is routine and expected, not
        # a pipeline-level problem -- see docstring above.
        logger.warning(
            "pytrends optional overlay failed for %s (non-fatal, never "
            "load-bearing): %s", symbol, exc,
        )
        return None


# ---------------------------------------------------------------------------
# Public per-symbol / per-cycle entry points.
# ---------------------------------------------------------------------------

_default_source: Optional[WikipediaPageviewsSource] = None


def get_attention_source() -> WikipediaPageviewsSource:
    """Return the module-level ``WikipediaPageviewsSource`` singleton.

    Constructing on first call so import-time side effects are avoided
    (mirrors ``data.market_data.get_provider()`` /
    ``data.sentiment_sources.get_sentiment_source()``).
    """
    global _default_source
    if _default_source is None:
        _default_source = WikipediaPageviewsSource()
    return _default_source


def reset_attention_source() -> None:
    """Force-reset the singleton (useful in tests)."""
    global _default_source
    _default_source = None


def compute_attention_score(
    symbol: str,
    company_name: Optional[str] = None,
    *,
    source: Optional[AttentionSource] = None,
    lookback_days: Optional[int] = None,
    as_of: Optional[datetime] = None,
) -> float:
    """Compute one symbol's ``Attention_Score`` for this cycle.

    Tries Wikipedia first (``resolve_article_title()``'s candidate titles,
    in order); falls back to the optional pytrends overlay ONLY if
    Wikipedia yielded nothing AND ``settings.PYTRENDS_ENABLED`` is True.
    Returns NaN (never fabricated -- CONSTRAINT #4) if neither path
    produces a usable score.

    This function does NOT itself check ``settings.WIKIPEDIA_ATTENTION_ENABLED``
    -- it always attempts a fetch when called directly (useful for tests
    and for callers that already know they want a fetch). Production call
    sites MUST go through ``compute_attention_scores_for_universe()``,
    which enforces the master gate before this is ever reached.
    """
    from settings import settings as _settings

    if lookback_days is None:
        lookback_days = int(_settings.WIKIPEDIA_ATTENTION_LOOKBACK_DAYS)
    src = source or get_attention_source()

    for title in resolve_article_title(symbol, company_name):
        series = src.fetch_daily_series(title, lookback_days)
        if not series:
            continue
        score = _abnormal_attention_score(series, as_of=as_of)
        if not math.isnan(score):
            return score

    if _settings.PYTRENDS_ENABLED:
        pytrends_score = _fetch_pytrends_attention_score(symbol, lookback_days)
        if pytrends_score is not None and not math.isnan(pytrends_score):
            return pytrends_score

    return float("nan")


def compute_attention_scores_for_universe(
    symbols: Sequence[str],
    company_names: Optional[Dict[str, str]] = None,
) -> Dict[str, float]:
    """Batch entry point for ``pipeline/production_steps.py``.

    Complete no-op -- returns ``{}``, makes ZERO network calls -- when
    ``settings.WIKIPEDIA_ATTENTION_ENABLED`` is False. This is the single
    master gate for the whole attention feature (Wikipedia AND the optional
    pytrends overlay both live behind it -- see
    ``settings.WIKIPEDIA_ATTENTION_ENABLED``'s docstring: "nothing activates
    unless explicitly set").

    Per-symbol failures are caught individually so one bad symbol can never
    abort the batch (CONSTRAINT #6, matching this codebase's per-ticker
    try/except convention in ``data_engine.py``/orchestrator loops); a
    failed symbol's score is NaN, never fabricated.
    """
    from settings import settings as _settings

    if not _settings.WIKIPEDIA_ATTENTION_ENABLED:
        return {}

    company_names = company_names or {}
    source = get_attention_source()
    lookback_days = int(_settings.WIKIPEDIA_ATTENTION_LOOKBACK_DAYS)

    scores: Dict[str, float] = {}
    for sym in symbols:
        try:
            scores[sym] = compute_attention_score(
                sym, company_names.get(sym), source=source, lookback_days=lookback_days,
            )
        except Exception as exc:
            logger.warning(
                "compute_attention_scores_for_universe: %s failed: %s", sym, exc,
            )
            scores[sym] = float("nan")
    return scores
