"""
llm/chart_insight.py — Tier 9 Scope 3 Gemini Vision chart interpretation.
==========================================================================

Public entry point :func:`generate_chart_pattern_read` takes a symbol +
price DataFrame, renders a matplotlib chart to PNG bytes, sends both the
PNG and a short user prompt to Gemini Vision (via
:meth:`GeminiProvider.call_structured_with_image`), and returns a
schema-validated :class:`ChartPatternRead` instance — or ``None`` on any
failure.

Soft-fail contract (CONSTRAINT #6)
----------------------------------
Every code path that touches matplotlib, the SDK, or the cache is wrapped
in try/except.  Any failure → ``None``; the caller (the AI Insights tab)
renders an "unavailable" placeholder and the deterministic chart
underneath remains the source of truth.

No fabricated metrics (CONSTRAINT #4)
-------------------------------------
The model's output flows ONLY into the rendered Markdown block in the
AI Insights tab.  It never feeds back into ``score``, ``conviction``,
``forecast``, ``support_zone`` or any numeric pipeline field.  The
deterministic ATR-based corridors continue to drive position sizing.

Cache reuse
-----------
Reuses :func:`llm.cache.make_cache_key` / :func:`cache_get` /
:func:`cache_put` so the day-bucketed JSON cache contract is the same as
the analyst commentary path.  The cache key folds in the chart's bar
fingerprint (latest close + bar count) so the day-bucket invalidates
naturally when new data is fetched.
"""

from __future__ import annotations

import io
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from llm.cache import cache_get, cache_put, make_cache_key
from llm.schemas import ChartPatternRead
from settings import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Baseline system prompt — inlined; the Prompt Registry override path is
# identical to llm.commentary._registry_prompt.
# ---------------------------------------------------------------------------
_CHART_SYSTEM_PROMPT = (
    "You are an experienced technical-analysis chart reader.  You will receive "
    "ONE price chart for a single equity, and you must respond with a strictly "
    "schema-conforming JSON object.  Describe what is VISIBLE in the chart "
    "(pattern, trend direction, qualitative support / resistance zones, a "
    "concise 2-3 sentence narrative).  NEVER fabricate numbers that are not on "
    "the chart's axes.  This is an advisory note — do not authorise any trade."
)


def _registry_prompt(prompt_id: str, default: str) -> str:
    """Mirror :func:`llm.commentary._registry_prompt` for prompt-registry overrides."""
    if not getattr(settings, "PROMPT_REGISTRY_ENABLED", False):
        return default
    try:
        from prompt_registry import get_registry  # noqa: PLC0415

        body = get_registry().get(prompt_id, default)
        if isinstance(body, str) and body.strip():
            return body
    except Exception as exc:
        logger.debug("chart_insight: registry lookup for %s failed: %s", prompt_id, exc)
    return default


# ---------------------------------------------------------------------------
# Helpers — chart rendering + bar fingerprint
# ---------------------------------------------------------------------------


def render_price_chart_png(
    symbol: str,
    bars_df: Any,
    *,
    width: float = 8.0,
    height: float = 4.5,
    dpi: int = 110,
) -> Optional[bytes]:
    """Render the symbol's price + 50/200-day SMA to a PNG byte string.

    Returns ``None`` on any failure (matplotlib import error, empty df,
    missing ``Close`` column).  The caller's soft-fail then propagates.

    The chart is intentionally minimalist — close line + two SMAs +
    title — so the model gets a clean visual without legend / theme
    distractions.  Bars older than the last ~252 rows are dropped so
    the visible window matches what an operator would naturally watch.
    """
    try:
        # Lazy matplotlib import — keeps test-only paths fast and lets
        # environments without a GUI backend opt out via Agg backend below.
        import matplotlib  # noqa: PLC0415

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt  # noqa: PLC0415
        import pandas as pd  # noqa: PLC0415

        if bars_df is None or not hasattr(bars_df, "tail"):
            return None
        df = bars_df.tail(252).copy()
        if df.empty or "Close" not in df.columns:
            return None

        sma_50 = df["Close"].rolling(window=50, min_periods=1).mean()
        sma_200 = df["Close"].rolling(window=200, min_periods=1).mean()

        fig, ax = plt.subplots(figsize=(width, height), dpi=dpi)
        ax.plot(df.index, df["Close"], color="#1f77b4", linewidth=1.6, label=f"{symbol} Close")
        ax.plot(df.index, sma_50, color="#ff7f0e", linewidth=1.0, linestyle="--", label="SMA 50")
        ax.plot(df.index, sma_200, color="#2ca02c", linewidth=1.0, linestyle="--", label="SMA 200")
        ax.set_title(f"{symbol} — last {len(df)} bars")
        ax.legend(loc="best", fontsize=8)
        ax.grid(True, linestyle=":", linewidth=0.5, alpha=0.7)
        fig.autofmt_xdate()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        plt.close(fig)
        return buf.getvalue()
    except Exception as exc:
        import traceback; traceback.print_exc(); logger.warning("render_price_chart_png failed for %s: %s", symbol, exc)
        return None


def _bar_fingerprint(bars_df: Any) -> float:
    """Return a scalar fingerprint of the bar set for cache keying.

    The fingerprint is ``close * 1000 + bar_count`` so a new bar landing
    (count++) OR a meaningful close-price move (score bucket changes via
    ``floor(value / 5)`` in :func:`llm.cache.make_cache_key`) invalidates
    the cache the same day.  Returns ``0.0`` on any failure — the cache
    still works, it just doesn't bucket as cleanly.
    """
    try:
        if bars_df is None or not hasattr(bars_df, "tail"):
            return 0.0
        df = bars_df.tail(1)
        if df.empty or "Close" not in df.columns:
            return 0.0
        close = float(df["Close"].iloc[-1])
        n = int(len(bars_df))
        if math.isnan(close):
            return 0.0
        return close * 1000.0 + n
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Provider plumbing
# ---------------------------------------------------------------------------


def _get_vision_provider():
    """Construct the Gemini provider lazily for vision calls.

    Reuses the same opt-in master switch as the commentary surface
    (``LLM_COMMENTARY_ENABLED``) — operators who turn on Tier 9 commentary
    get the chart interpretation seam for free, and the same kill-switch
    disables both.  Returns ``None`` when the switch is off or the key is
    unset.
    """
    try:
        if not getattr(settings, "LLM_COMMENTARY_ENABLED", False):
            return None
        key = getattr(settings, "GEMINI_API_KEY", None)
        if not key:
            return None
        from llm.providers import GeminiProvider  # noqa: PLC0415

        return GeminiProvider(
            api_key=key,
            timeout_seconds=float(getattr(settings, "LLM_COMMENTARY_TIMEOUT_SECONDS", 8) or 8),
        )
    except Exception as exc:
        logger.warning("chart_insight: GeminiProvider construction failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_chart_pattern_read(
    symbol: str,
    bars_df: Any,
    *,
    provider=None,
    chart_renderer=None,
) -> Optional[ChartPatternRead]:
    """Return a Gemini Vision interpretation of the symbol's chart.

    Parameters
    ----------
    symbol :
        Uppercase ticker.
    bars_df :
        OHLCV DataFrame (must have a ``Close`` column).  Will be rendered
        to a 252-bar chart via :func:`render_price_chart_png`.
    provider :
        Optional pre-constructed provider (test seam).  Must implement
        ``call_structured_with_image(system, user, image_bytes, schema_model)``.
    chart_renderer :
        Optional override for :func:`render_price_chart_png` (test seam).
        Receives ``(symbol, bars_df)`` and must return ``bytes`` or ``None``.

    Returns
    -------
    Optional[ChartPatternRead]
        Schema-validated structured output, or ``None`` on master-switch
        off, missing key, render failure, network exception, schema
        mismatch — every failure mode is a soft-fail (CONSTRAINT #6).
    """
    try:
        sym = (symbol or "").upper().strip()
        if not sym:
            return None

        fp = _bar_fingerprint(bars_df)
        cache_key = make_cache_key(
            provider="gemini",
            schema_name=ChartPatternRead.__name__,
            symbol=sym,
            score=fp,
            action="CHART",
        )
        cached = cache_get(cache_key)
        if cached is not None:
            try:
                return ChartPatternRead.model_validate(cached)
            except Exception:
                pass

        renderer = chart_renderer if chart_renderer is not None else render_price_chart_png
        image_bytes = renderer(sym, bars_df)
        if not image_bytes:
            return None

        prov = provider if provider is not None else _get_vision_provider()
        if prov is None:
            return None
        if not hasattr(prov, "call_structured_with_image"):
            logger.warning(
                "chart_insight: provider %s lacks call_structured_with_image — skipping.",
                getattr(prov, "name", "?"),
            )
            return None

        system = _registry_prompt("llm.chart.system", _CHART_SYSTEM_PROMPT)
        user = (
            f"Interpret the {sym} chart attached as an image.  Stay within what "
            f"is visible — do not invent prices that aren't on the axes.  "
            "Respond using the structured-output schema."
        )
        result = prov.call_structured_with_image(
            system=system,
            user=user,
            image_bytes=image_bytes,
            schema_model=ChartPatternRead,
        )
        if result is None:
            return None

        try:
            cache_put(
                cache_key,
                result.model_dump(),
                meta={"provider": getattr(prov, "name", "?"), "symbol": sym, "fp": fp},
            )
        except Exception as exc:
            logger.debug("chart_insight: cache_put failed (non-fatal): %s", exc)
        return result
    except Exception as exc:
        logger.warning("generate_chart_pattern_read failed unexpectedly: %s", exc)
        return None
