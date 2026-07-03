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
If ``transformers`` is installed (with a PyTorch or TensorFlow backend),
uses `ProsusAI/finbert <https://huggingface.co/ProsusAI/finbert>`_ — a
BERT model fine-tuned on 10 000 financial news sentences.  Loaded once
per process and cached as a module-level singleton.

If ``transformers`` is unavailable or the model fails to load, falls back
to a curated 80-word financial keyword lexicon.  Set
``FINBERT_ENABLED=false`` in ``.env`` to force the lexicon even when
``transformers`` is installed.

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

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry

logger = logging.getLogger(__name__)

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


def _score_headline(headline: str, pipeline: Optional[Any]) -> float:
    """Score one headline in [-1, +1] using FinBERT or lexicon fallback."""
    if not headline:
        return 0.0
    if pipeline is not None:
        try:
            result = pipeline(headline[:512])[0]  # type: ignore[index]
            label = result["label"].lower()
            prob = float(result["score"])
            if label == "positive":
                return prob
            elif label == "negative":
                return -prob
            return 0.0  # neutral
        except Exception as exc:
            logger.debug("NewsCatalystSignal: FinBERT scoring error: %s", exc)
    return _lexicon_sentiment(headline)


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

        # Collect symbols from the universe DataFrame
        symbol_col = "Symbol" if "Symbol" in universe_df.columns else None
        if symbol_col is None and len(universe_df.columns) > 0:
            symbol_col = universe_df.columns[0]
        symbols: List[str] = (
            list(universe_df[symbol_col].dropna().astype(str).str.upper().unique())
            if symbol_col is not None else []
        )

        lookback = int(_settings.NEWS_LOOKBACK_DAYS)
        suppress_h = float(_settings.NEWS_EARNINGS_SUPPRESS_HOURS)
        dampen_d = float(_settings.NEWS_EARNINGS_DAMPEN_DAYS)
        now = datetime.now(timezone.utc)

        for symbol in symbols:
            try:
                next_earnings = fetch_next_earnings(client, symbol)
                self._earnings_dt[symbol] = next_earnings

                news_items = fetch_company_news(client, symbol, lookback)
                scores = [
                    _score_headline(item.get("headline", ""), pipeline)
                    for item in news_items
                    if item.get("headline")
                ]
                raw = float(sum(scores) / len(scores)) if scores else 0.0
                multiplier = _earnings_proximity_multiplier(
                    next_earnings, now, suppress_h, dampen_d
                )
                self._news_scores[symbol] = max(-1.0, min(1.0, raw * multiplier))

                # Courtesy delay to respect Finnhub free-tier rate limit
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

        logger.info(
            "NewsCatalystSignal.pre_compute: scored %d symbols "
            "(FinBERT=%s).",
            len(self._news_scores),
            pipeline is not None,
        )

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        """Return the pre-computed sentiment score for this symbol."""
        symbol = str(row.get("Symbol", row.get("Ticker", ""))).upper()
        score = self._news_scores.get(symbol, 0.0)
        confidence = 0.75 if symbol in self._news_scores else 0.5

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
                f"News sentiment: {direction}{suffix}."
            ),
        )


# Auto-register with the global signal registry
global_registry.register(NewsCatalystSignal())
