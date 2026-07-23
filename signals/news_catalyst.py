"""
signals/news_catalyst.py
========================
Tier 2.4 — News / Earnings Catalyst Signal

Combines headline sentiment with earnings-proximity dampening to produce a
directional score in [-1, +1].

Data sources
------------
* **Finnhub company_news** (`/api/v1/company-news`) — last
  ``NEWS_LOOKBACK_DAYS`` calendar days of headlines (free tier).
* **Finnhub earnings calendar** (`/api/v1/calendar/earnings`) — next 30
  calendar days, used to detect the 48h suppression and 7-day dampening
  windows (free tier).

Sentiment scorer
----------------
If ``transformers`` is installed (with a PyTorch or TensorFlow backend --
see ``requirements-optional.txt`` for a CPU-only torch pin), uses
`ProsusAI/finbert <https://huggingface.co/ProsusAI/finbert>`_ — a BERT
model fine-tuned on 10 000 financial news sentences.  Loaded once per
process and cached as a module-level singleton.

If ``transformers`` is unavailable or the model fails to load, falls back
to a curated 80-word financial keyword lexicon.  Set
``FINBERT_ENABLED=false`` in ``.env`` to force the lexicon even when
``transformers`` is installed.

Batched scoring + cache
------------------------
``score_headlines()`` is the batched scoring entry point: it encodes
``settings.FINBERT_BATCH_SIZE`` (default 16) headlines per forward pass
instead of one call per headline, and returns the full 3-class softmax
(``{"positive", "neutral", "negative"}``) per headline rather than a single
collapsed scalar. Results are cached by a SHA-256 hash of the headline text
in ``data/historical_store.py``'s ``finbert_score_cache`` table (gated by
``settings.FINBERT_SCORE_CACHE_ENABLED``, default ``True``), so an
unchanged headline seen again in a later cycle's lookback window is not
re-scored. ``_score_headline()`` remains a thin, cache-bypassing wrapper
around ``score_headlines()`` for a single item, preserving its exact
pre-batching signature/return contract for existing callers (e.g.
``data/sentiment_sources.py``).

Earnings-proximity adjustment
------------------------------
* Within ``NEWS_EARNINGS_SUPPRESS_HOURS`` (default 48 h): score forced to
  0.0 — signal is unreliable immediately before earnings.
* Within ``NEWS_EARNINGS_DAMPEN_DAYS`` (default 7 days): score × 0.5 —
  signal exists but carry risk is elevated.
* More than ``NEWS_EARNINGS_DAMPEN_DAYS`` out: full score.

Registration
------------
Auto-registered with ``global_registry`` at module import time (imported by
``signals/__init__.py`` for every ``SignalAggregator`` cycle).
"""

import hashlib
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from dto_models import MacroEconomicDTO
from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry

logger = logging.getLogger(__name__)

# Regimes during which social/news sentiment is suppressed entirely (RISK-OFF).
# Same thresholds as signals/rsi2_mean_reversion.py's regime gate: sentiment is
# noisiest exactly when it matters least (panics, credit events), and this is
# the platform's existing signal-level regime-gate pattern, not a new one.
_RISK_OFF_REGIMES = {"RECESSION", "CREDIT EVENT"}
_VIX_RISK_OFF_THRESHOLD = 30.0

# ---------------------------------------------------------------------------
# FinBERT pipeline — lazy process-level singleton
# ---------------------------------------------------------------------------

_FINBERT_PIPELINE: Optional[Any] = None
_FINBERT_LOAD_ATTEMPTED: bool = False


def _get_finbert_pipeline() -> Optional[Any]:
    """Load ProsusAI/finbert pipeline on first call; None on failure.

    Import is deferred so the module can be imported without ``transformers``
    installed — the signal degrades gracefully to the keyword lexicon.
    """
    global _FINBERT_PIPELINE, _FINBERT_LOAD_ATTEMPTED
    if _FINBERT_LOAD_ATTEMPTED:
        return _FINBERT_PIPELINE
    _FINBERT_LOAD_ATTEMPTED = True
    try:
        from transformers import pipeline as _hf_pipeline  # type: ignore
        _FINBERT_PIPELINE = _hf_pipeline(
            "sentiment-analysis",
            model="ProsusAI/finbert",
            tokenizer="ProsusAI/finbert",
            truncation=True,
            max_length=512,
        )
        logger.info("NewsCatalystSignal: FinBERT pipeline loaded successfully.")
    except Exception as exc:
        logger.info(
            "NewsCatalystSignal: FinBERT unavailable (%s). Falling back to "
            "keyword lexicon.",
            exc,
        )
    return _FINBERT_PIPELINE


# ---------------------------------------------------------------------------
# Keyword lexicon — finance-specific positive / negative terms
# ---------------------------------------------------------------------------

_POSITIVE_WORDS: frozenset = frozenset({
    "beat", "beats", "beating", "record", "surpass", "surge", "rally", "soar",
    "jump", "upgraded", "upgrade", "outperform", "strong", "growth", "profit",
    "gain", "gains", "bullish", "expansion", "accelerate", "recovery", "revenue",
    "buyback", "raised", "raise", "positive", "exceeds", "exceed", "upside",
    "momentum", "crushed", "crush", "top", "tops", "boosted", "acquires",
    "acquisition", "breakout", "invest", "innovation", "partnership", "deal",
    "approval", "approved", "dividend", "increase", "increased", "launch",
    "better", "above", "strong", "guidance", "raised",
})

_NEGATIVE_WORDS: frozenset = frozenset({
    "miss", "misses", "missed", "fall", "plunge", "drop", "crash", "decline",
    "cut", "cuts", "downgrade", "downgraded", "loss", "losses", "weak", "slump",
    "bearish", "contraction", "recession", "layoff", "layoffs", "debt", "default",
    "lawsuit", "investigate", "investigation", "fraud", "penalty", "fine", "warn",
    "warning", "shortfall", "disappointment", "below", "concern", "risk", "threat",
    "fail", "failed", "bankrupt", "recall", "controversy", "delay", "lower",
    "disappointing", "underperform", "negative", "withdrew", "withdraw",
    "downward", "guidance", "reduced", "reduce", "reject", "rejection",
})


def _lexicon_sentiment(headline: str) -> float:
    """Score a headline in [-1, +1] via keyword matching.

    Returns (pos − neg) / max(1, pos + neg) so pure-positive yields +1,
    pure-negative yields -1, and balanced / empty yields 0.
    """
    tokens = [w.strip(".,!?;:\"'()[]") for w in headline.lower().split()]
    pos = sum(1 for w in tokens if w in _POSITIVE_WORDS)
    neg = sum(1 for w in tokens if w in _NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / total


def _content_hash(headline: str) -> str:
    """SHA-256 hex digest of headline text — the ``finbert_score_cache`` PK.

    Content-hash, not date/cycle-keyed. Caching a FinBERT/lexicon score by
    unchanged headline TEXT is NOT a lookahead risk: the score is a pure,
    deterministic function of the text alone (neither FinBERT nor the
    lexicon has any notion of "when" they scored a string), so identical
    text always yields the identical score regardless of which trading
    cycle reads it. A lookahead bug would require a cache read to surface
    information from a cycle that hasn't happened yet; a content-hash
    lookup can only ever return a score for text THIS cycle already fetched
    from Finnhub, so there is no channel through which a future cycle's
    headline could leak into an earlier cycle's read. See
    tests/test_news_catalyst.py::TestFinbertScoreCacheLookaheadSafety.
    """
    return hashlib.sha256((headline or "").encode("utf-8")).hexdigest()


def _lexicon_softmax(headline: str) -> Dict[str, float]:
    """Represent the keyword lexicon's signed score as a softmax-shaped dict
    for API uniformity with FinBERT's genuine 3-class distribution.

    NOT a calibrated probability distribution — the keyword lexicon has no
    notion of confidence — deliberately constructed so exactly one of
    ``positive``/``negative`` is nonzero at a time (magnitude
    ``abs(_lexicon_sentiment(headline))``), with the remaining mass reported
    honestly as ``neutral``. This guarantees ``positive - negative`` (see
    ``_distribution_to_signed``) exactly reconstructs the original
    ``_lexicon_sentiment()`` scalar — the backward-compatibility property
    ``_score_headline()`` depends on.
    """
    s = _lexicon_sentiment(headline)
    positive = max(0.0, s)
    negative = max(0.0, -s)
    neutral = 1.0 - positive - negative
    return {"positive": positive, "neutral": neutral, "negative": negative}


def _distribution_to_signed(dist: Dict[str, float]) -> float:
    """Collapse a 3-class softmax distribution to a single signed score in
    [-1, +1]: the net probability mass, ``positive - negative``.

    For the lexicon fallback (see ``_lexicon_softmax``) this exactly
    reproduces the original ``_lexicon_sentiment()`` scalar, since exactly
    one of ``positive``/``negative`` is ever nonzero. For a genuine FinBERT
    distribution this is a principled account of BOTH tails of the
    distribution (unlike the pre-batching behavior, which only ever looked
    at the single argmax class's own probability and discarded the rest) —
    still bounded in [-1, 1] since ``positive``/``negative`` are each in
    [0, 1].
    """
    return float(dist.get("positive", 0.0)) - float(dist.get("negative", 0.0))


def score_headlines(
    headlines: List[str],
    pipeline: Optional[Any] = None,
    *,
    batch_size: Optional[int] = None,
    use_cache: bool = True,
) -> List[Dict[str, float]]:
    """Score a batch of headlines, returning one full-softmax dict per
    headline (same order as ``headlines``):
    ``{"positive": float, "neutral": float, "negative": float}``.

    - Encodes real FinBERT inference in batches of ``batch_size`` (default
      ``settings.FINBERT_BATCH_SIZE``, 16) headlines per forward pass
      instead of the old one-headline-at-a-time loop.
    - Truncates each headline to 512 characters before it reaches the
      pipeline — the pipeline itself was already constructed with
      ``truncation=True, max_length=512`` (see ``_get_finbert_pipeline``);
      this mirrors that same limit at the call site.
    - When ``use_cache`` and ``settings.FINBERT_SCORE_CACHE_ENABLED`` are
      both true (and ``settings.HISTORICAL_STORE_ENABLED`` is true — the
      DB-layer master switch), checks ``data/historical_store.py``'s
      ``finbert_score_cache`` (keyed by SHA-256 content hash — see
      ``_content_hash``) first; only cache MISSES are scored, and every
      freshly-scored headline is written back before returning. Any
      cache read/write failure degrades to "score it fresh" — the
      returned scores are unaffected by a broken cache (CONSTRAINT #6).
    - Falls back cleanly to the keyword lexicon (``_lexicon_sentiment`` via
      ``_lexicon_softmax``) per-headline when ``pipeline`` is ``None`` OR a
      FinBERT batch call raises / returns a malformed result — never
      raises itself.
    - Empty input → ``[]`` (no pipeline/cache calls at all).
    """
    if not headlines:
        return []

    from settings import settings as _settings

    effective_batch_size = max(1, int(batch_size or getattr(_settings, "FINBERT_BATCH_SIZE", 16) or 16))

    n = len(headlines)
    results: List[Optional[Dict[str, float]]] = [None] * n
    hashes: List[str] = [_content_hash(h or "") for h in headlines]

    # ---- 1. Cache lookup (content-hash, per headline) ----
    cache_enabled = (
        use_cache
        and bool(getattr(_settings, "HISTORICAL_STORE_ENABLED", True))
        and bool(getattr(_settings, "FINBERT_SCORE_CACHE_ENABLED", True))
    )
    store = None
    if cache_enabled:
        try:
            from data.historical_store import HistoricalStore
            store = HistoricalStore()
        except Exception as exc:
            logger.debug("score_headlines: cache store unavailable, scoring fresh: %s", exc)
            store = None

    if store is not None:
        for i in range(n):
            try:
                cached = store.get_finbert_score(hashes[i])
            except Exception as exc:
                logger.debug("score_headlines: cache read failed for one headline: %s", exc)
                cached = None
            if cached is not None:
                results[i] = cached

    # ---- 2. Score cache misses ----
    miss_indices = [i for i in range(n) if results[i] is None]
    if miss_indices:
        if pipeline is not None:
            for batch_start in range(0, len(miss_indices), effective_batch_size):
                batch_idx = miss_indices[batch_start: batch_start + effective_batch_size]
                batch_texts = [(headlines[i] or "")[:512] for i in batch_idx]
                try:
                    raw_outputs = pipeline(
                        batch_texts, batch_size=effective_batch_size, top_k=None
                    )
                except Exception as exc:
                    logger.debug("score_headlines: FinBERT batch scoring error: %s", exc)
                    raw_outputs = None
                if not raw_outputs or len(raw_outputs) != len(batch_idx):
                    for i in batch_idx:
                        results[i] = _lexicon_softmax(headlines[i] or "")
                    continue
                for pos, i in enumerate(batch_idx):
                    try:
                        item = raw_outputs[pos]
                        dist = {"positive": 0.0, "neutral": 0.0, "negative": 0.0}
                        for entry in item:
                            label = str(entry.get("label", "")).lower()
                            if label in dist:
                                dist[label] = float(entry.get("score", 0.0))
                        results[i] = dist
                    except Exception as exc:
                        logger.debug("score_headlines: per-item FinBERT parse error: %s", exc)
                        results[i] = _lexicon_softmax(headlines[i] or "")
        else:
            for i in miss_indices:
                results[i] = _lexicon_softmax(headlines[i] or "")

    # ---- 3. Write freshly-scored headlines back to the cache ----
    if store is not None and miss_indices:
        try:
            to_save = {
                hashes[i]: {**results[i], "headline_snippet": (headlines[i] or "")[:200]}
                for i in miss_indices
                if results[i] is not None
            }
            if to_save:
                store.save_finbert_scores(to_save)
        except Exception as exc:
            logger.debug("score_headlines: cache write failed: %s", exc)

    return [
        r if r is not None else {"positive": 0.0, "neutral": 1.0, "negative": 0.0}
        for r in results
    ]


def _score_headline(headline: str, pipeline: Optional[Any]) -> float:
    """Score one headline in [-1, +1] using FinBERT or lexicon fallback.

    Thin, cache-bypassing wrapper around :func:`score_headlines` for a
    single item — kept for exact backward compatibility with existing
    callers (e.g. ``data/sentiment_sources.py``'s several ``_score()``
    helpers) that expect this precise signature/return contract and no new
    DB I/O. The batched, cached path is ``score_headlines()`` itself
    (used directly by ``NewsCatalystSignal.pre_compute()``).
    """
    if not headline:
        return 0.0
    results = score_headlines([headline], pipeline=pipeline, use_cache=False)
    if not results:
        return 0.0
    return max(-1.0, min(1.0, _distribution_to_signed(results[0])))


# ---------------------------------------------------------------------------
# Earnings-proximity gating
# ---------------------------------------------------------------------------

def _earnings_proximity_multiplier(
    next_earnings: Optional[datetime],
    now: datetime,
    suppress_hours: float,
    dampen_days: float,
) -> float:
    """Return a [0, 1] multiplier based on time until next earnings.

    0.0 within suppress_hours (unreliable pre-earnings window).
    0.5 within dampen_days (elevated carry risk).
    1.0 beyond dampen_days (full signal).
    0.5 in the 24h post-earnings window (fresh noise).
    """
    if next_earnings is None:
        return 1.0
    diff_hours = (next_earnings - now).total_seconds() / 3600.0
    if diff_hours < -24:
        # More than 24h post-earnings — earnings effect faded
        return 1.0
    if diff_hours < 0:
        # Within 24h post-earnings — dampen
        return 0.5
    if diff_hours <= suppress_hours:
        return 0.0
    if diff_hours <= dampen_days * 24:
        return 0.5
    return 1.0


# ---------------------------------------------------------------------------
# Finnhub client helpers
# ---------------------------------------------------------------------------

def build_finnhub_client() -> Optional[Any]:
    """Return a finnhub.Client or None if not configured / not installed.

    Public API (promoted alongside :func:`fetch_company_news` /
    :func:`fetch_next_earnings` in Tier 9 Scope 4) so ``llm/research.py``
    can obtain a Finnhub client without reaching into a private surface.
    """
    api_key = os.environ.get("FINNHUB_API_KEY", "")
    if not api_key:
        return None
    try:
        import finnhub  # type: ignore
        return finnhub.Client(api_key=api_key)
    except ImportError:
        logger.debug(
            "NewsCatalystSignal: finnhub-python not installed "
            "(pip install finnhub-python)."
        )
        return None


def fetch_company_news(
    client: Any, symbol: str, lookback_days: int
) -> List[Dict[str, Any]]:
    """Fetch recent company news; returns [] on any error.

    Public API (promoted from ``_fetch_company_news`` in Tier 9 Scope 4) so
    ``llm/research.py`` can reuse this exact grounding call for Opal's
    research briefs without reaching into a private module surface.
    """
    try:
        now_utc = datetime.now(timezone.utc)
        start = (now_utc - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        end = now_utc.strftime("%Y-%m-%d")
        result = client.company_news(symbol, _from=start, to=end)
        return result if isinstance(result, list) else []
    except Exception as exc:
        logger.debug(
            "NewsCatalystSignal: company_news(%s) failed: %s", symbol, exc
        )
        return []


def fetch_next_earnings(client: Any, symbol: str) -> Optional[datetime]:
    """Return the soonest upcoming earnings datetime (UTC-aware) or None.

    Public API (promoted from ``_fetch_next_earnings`` in Tier 9 Scope 4) —
    see :func:`fetch_company_news`.
    """
    try:
        now_utc = datetime.now(timezone.utc)
        start = now_utc.strftime("%Y-%m-%d")
        end = (now_utc + timedelta(days=30)).strftime("%Y-%m-%d")
        data = client.earnings_calendar(_from=start, to=end, symbol=symbol) or {}
        entries = data.get("earningsCalendar", [])
        future: List[datetime] = []
        for entry in entries:
            date_str = entry.get("date", "")
            if not date_str:
                continue
            try:
                dt = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
                if dt >= now_utc - timedelta(hours=24):
                    future.append(dt)
            except ValueError:
                continue
        return min(future) if future else None
    except Exception as exc:
        logger.debug(
            "NewsCatalystSignal: earnings_calendar(%s) failed: %s", symbol, exc
        )
        return None


# ---------------------------------------------------------------------------
# Signal module
# ---------------------------------------------------------------------------

class NewsCatalystSignal(SignalModule):
    """Tier 2.4 — News sentiment + earnings-proximity catalyst signal.

    Score ∈ [-1, +1]: averaged FinBERT / lexicon sentiment over the last
    ``NEWS_LOOKBACK_DAYS`` calendar days, multiplied by an earnings-proximity
    gate (0 within 48 h of earnings, 0.5 within 7 days, 1 otherwise).

    ``pre_compute`` batch-fetches Finnhub data for the full symbol universe
    once per cycle and caches results so ``compute`` is a pure dict lookup
    (no per-symbol network calls in the hot loop).
    """

    name = "news_catalyst"
    required_features: List[str] = []  # No bar-level features required

    def __init__(self) -> None:
        # Per-cycle caches populated by pre_compute
        self._news_scores: Dict[str, float] = {}          # symbol → averaged score
        self._earnings_dt: Dict[str, Optional[datetime]] = {}  # symbol → next earnings
        # Multi-source credibility-weighted aggregate (Sentiment Pipeline Phase 4),
        # keyed by symbol -- see _read_sentiment_credibility_aggregate().
        self._sentiment_credibility: Dict[str, Dict[str, float]] = {}

    def is_active_in_regime(self, macro: MacroEconomicDTO) -> bool:
        """RISK-OFF gate: suppressed during RECESSION/CREDIT EVENT or VIX > 30.

        News/social sentiment is noisiest exactly when it matters least — during
        systemic panics, headline flow reflects fear and forced deleveraging
        rather than idiosyncratic company information, so the module is
        switched off entirely (mirrors signals/rsi2_mean_reversion.py's
        regime gate) rather than down-weighted.
        """
        if macro.market_regime in _RISK_OFF_REGIMES:
            return False
        if macro.vix > _VIX_RISK_OFF_THRESHOLD:
            return False
        return True

    def _run_multi_source_ingestion(self, symbols: List[str]) -> None:
        """Fetch, credibility-score, and archive multi-source documents
        (Sentiment Pipeline Phase 3/4: Yahoo RSS/GDELT/Reddit/EDGAR, and
        Finnhub too if an operator opts it into ``settings.SENTIMENT_SOURCES``)
        for every symbol in the universe, once per cycle.

        This is the ONLY call site that invokes
        ``data.sentiment_sources.CompositeSentimentSource`` in the live
        pipeline -- without it, ``sentiment_ingestion_audit`` never
        accumulates rows no matter how much time passes. Dead-letter
        resilient per-symbol (CONSTRAINT #6): one symbol's ingestion failure
        never blocks the others or the rest of ``pre_compute``.

        Gated behind ``settings.SENTIMENT_INGESTION_ENABLED`` (default
        ``False``) -- a complete no-op, no network call attempted, until an
        operator opts in. Two of the four sources (Yahoo RSS, GDELT) need no
        API key, so this is the only way they stay quiet by default the same
        way Finnhub/Reddit/EDGAR already do via absent credentials.
        """
        try:
            from settings import settings as _settings
            if not _settings.SENTIMENT_INGESTION_ENABLED:
                return
            if not _settings.SENTIMENT_AUDIT_ENABLED:
                return
            from data.sentiment_sources import get_sentiment_source
            source = get_sentiment_source()
            source.reset_cycle()
            for symbol in symbols:
                try:
                    source.fetch_and_archive(symbol)
                except Exception as exc:
                    logger.warning(
                        "NewsCatalystSignal: multi-source ingestion failed for %s: %s",
                        symbol, exc,
                    )
        except Exception as exc:
            logger.warning(
                "NewsCatalystSignal: multi-source ingestion setup failed: %s", exc
            )

    def _read_sentiment_credibility_aggregate(self) -> None:
        """Read this trading day's multi-source credibility-weighted
        aggregate from ``sentiment_ingestion_audit`` (Sentiment Pipeline
        Phase 2-4: ``data/sentiment_sources.py`` writes it, ``signals/
        credibility.py`` scores it). Read-only, no network I/O -- pure DB
        aggregation query. Dead-letter resilient (CONSTRAINT #6): any
        failure degrades to an empty dict, never raises.
        """
        try:
            from data.historical_store import HistoricalStore
            trading_day = HistoricalStore.resolve_trading_day(datetime.now(timezone.utc))
            self._sentiment_credibility = HistoricalStore().get_sentiment_aggregate_by_symbol(
                trading_day
            )
        except Exception as exc:
            logger.warning(
                "NewsCatalystSignal: sentiment credibility aggregate read failed: %s", exc
            )
            self._sentiment_credibility = {}

    def pre_compute(
        self,
        universe_df: pd.DataFrame,
        context: SignalContext,
    ) -> None:
        """Batch-fetch news and earnings for every symbol in the universe.

        Stores results in ``context.news_sentiment_scores`` and
        ``context.earnings_dates`` (ISO-date strings) for orchestrator
        writeback to ``dashboard_df``, AND in instance attributes for
        ``compute()`` to read.

        If ``FINNHUB_API_KEY`` is unset, all scores are 0.0 (no crash).
        """
        from settings import settings as _settings

        self._news_scores = {}
        self._earnings_dt = {}

        # Collect symbols from the universe DataFrame -- computed up front so
        # both the multi-source ingestion run below and the Finnhub-specific
        # loop can use it, regardless of whether Finnhub is configured.
        symbol_col = "Symbol" if "Symbol" in universe_df.columns else None
        if symbol_col is None and len(universe_df.columns) > 0:
            symbol_col = universe_df.columns[0]
        symbols: List[str] = (
            list(universe_df[symbol_col].dropna().astype(str).str.upper().unique())
            if symbol_col is not None else []
        )

        # Multi-source ingestion + credibility scoring + archive (Sentiment
        # Pipeline Phase 3/4) -- runs every cycle, independent of Finnhub
        # configuration, so Reddit/GDELT/EDGAR/Yahoo RSS documents accumulate
        # in sentiment_ingestion_audit even when FINNHUB_API_KEY is unset.
        # This is the write side; _read_sentiment_credibility_aggregate()
        # right after is the same-cycle read side (this cycle's own writes
        # land under TODAY's trading_day and are picked up by the read below,
        # since both resolve "now" via the same HistoricalStore.resolve_trading_day()).
        self._run_multi_source_ingestion(symbols)
        self._read_sentiment_credibility_aggregate()
        context.sentiment_credibility_scores = dict(self._sentiment_credibility)

        client = build_finnhub_client()
        if client is None:
            logger.info(
                "NewsCatalystSignal: FINNHUB_API_KEY not set — all news "
                "scores will be 0.0 (no-op)."
            )
            # Still surface empty dicts to context so orchestrator writeback
            # doesn't fail
            context.news_sentiment_scores = {}
            context.earnings_dates = {}
            return

        pipeline = _get_finbert_pipeline() if _settings.FINBERT_ENABLED else None

        lookback = int(_settings.NEWS_LOOKBACK_DAYS)
        suppress_h = float(_settings.NEWS_EARNINGS_SUPPRESS_HOURS)
        dampen_d = float(_settings.NEWS_EARNINGS_DAMPEN_DAYS)
        now = datetime.now(timezone.utc)

        for symbol in symbols:
            try:
                next_earnings = fetch_next_earnings(client, symbol)
                self._earnings_dt[symbol] = next_earnings

                news_items = fetch_company_news(client, symbol, lookback)
                headlines = [
                    item.get("headline", "") for item in news_items if item.get("headline")
                ]
                # Batched (+ content-hash cached) FinBERT/lexicon scoring --
                # replaces the old one-headline-at-a-time _score_headline loop.
                # Scoring is local (no network call), so it is never subject
                # to the Finnhub rate-limit courtesy delay below.
                distributions = score_headlines(headlines, pipeline=pipeline)
                scores = [_distribution_to_signed(d) for d in distributions]
                raw = float(sum(scores) / len(scores)) if scores else 0.0
                multiplier = _earnings_proximity_multiplier(
                    next_earnings, now, suppress_h, dampen_d
                )
                self._news_scores[symbol] = max(-1.0, min(1.0, raw * multiplier))

                # Courtesy delay to respect the Finnhub free-tier rate limit
                # for the NEXT symbol's fetch_next_earnings/fetch_company_news
                # calls -- unrelated to the (local, unthrottled) scoring above.
                time.sleep(0.12)
            except Exception as exc:
                logger.warning(
                    "NewsCatalystSignal.pre_compute: error for %s: %s", symbol, exc
                )
                self._news_scores[symbol] = 0.0

        # Populate context for orchestrator writeback to dashboard_df columns
        context.news_sentiment_scores = dict(self._news_scores)
        context.earnings_dates = {
            sym: (dt.strftime("%Y-%m-%d") if dt is not None else "")
            for sym, dt in self._earnings_dt.items()
        }

        self._archive_news_history(self._news_scores)

        logger.info(
            "NewsCatalystSignal.pre_compute: scored %d symbols "
            "(FinBERT=%s).",
            len(self._news_scores),
            pipeline is not None,
        )

    @staticmethod
    def _archive_news_history(scores: Dict[str, float]) -> None:
        """Forward-archive this cycle's news-sentiment scores (best-effort).

        No backtest reads this data yet (see HistoricalStore's news_history
        DDL comment) — this purely accumulates real point-in-time history so
        one becomes possible later. Gated by settings.NEWS_HISTORY_CAPTURE_ENABLED;
        a write failure is logged and never propagated (CONSTRAINT #6).
        """
        try:
            from settings import settings as _settings
            if not _settings.NEWS_HISTORY_CAPTURE_ENABLED or not scores:
                return
            from data.historical_store import HistoricalStore
            HistoricalStore().save_news_sentiment(scores, datetime.now(timezone.utc))
        except Exception as exc:
            logger.warning("NewsCatalystSignal: news_history archive failed: %s", exc)

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        """Return the credibility-weighted blend of the Finnhub-headline
        score and the multi-source social sentiment aggregate for this symbol.

        Gracefully degrades to headline-only (``News_Sentiment``'s own
        meaning is unchanged) when no multi-source social documents exist
        for this symbol this trading day -- never a fabricated social score
        (CONSTRAINT #4). See ``settings.SENTIMENT_SOCIAL_BLEND_WEIGHT``.
        """
        from settings import settings as _settings

        symbol = str(row.get("Symbol", row.get("Ticker", ""))).upper()
        headline_score = self._news_scores.get(symbol, 0.0)
        confidence = 0.75 if symbol in self._news_scores else 0.5

        social_entry = self._sentiment_credibility.get(symbol)
        blend_suffix = ""
        if social_entry is not None:
            social_score = social_entry.get("credibility_weighted_sentiment", 0.0)
            social_weight = max(0.0, min(1.0, float(_settings.SENTIMENT_SOCIAL_BLEND_WEIGHT)))
            headline_weight = 1.0 - social_weight
            score = headline_weight * headline_score + social_weight * social_score
            blend_suffix = f" [social blend w={social_weight:.2f}]"
        else:
            score = headline_score

        if score > 0.1:
            direction = f"positive (+{score:.2f})"
        elif score < -0.1:
            direction = f"negative ({score:.2f})"
        else:
            direction = "neutral"

        earnings = self._earnings_dt.get(symbol)
        suffix = (
            f" [earnings {earnings.strftime('%Y-%m-%d')}]"
            if earnings else ""
        )
        return SignalOutput(
            score=score,
            confidence=confidence,
            explanation=(
                f"News sentiment: {direction}{suffix}{blend_suffix}."
            ),
        )


# Auto-register with the global signal registry
global_registry.register(NewsCatalystSignal())
