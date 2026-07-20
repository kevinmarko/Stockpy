"""
gui/panels/sentiment_dynamics.py
--------------------------------
💬 Sentiment Dynamics tab — a per-symbol, on-demand view combining two
INDEPENDENT real sentiment signals plus the real GJR-GARCH asymmetric-
volatility computation. Every number rendered here is either a genuine
computed/fetched value or an honest "—" / explanatory note; the previous
draft of this panel rendered several fabricated random-noise demo charts and
a hardcoded metric value (see the WIP baseline commit for the full list of
violations) — none of that remains.

Section 1 — News Catalyst Sentiment: the REAL per-symbol ``news_sentiment``
field already computed by the always-on pipeline's ``NewsCatalystSignal`` and
persisted into ``output/state_snapshot.json``. Mirrors the honest-null-
skipping pattern in ``gui/panels/analytics_signals.py::render_news_sentiment()``.

Section 2 — Antigravity Agent + GJR-GARCH: calls the SAME
``sentiment_risk_engine.SentimentRiskEngine.get_live_sentiment()`` /
``compute_asymmetric_volatility()`` that the FastAPI ``GET
/metrics/sentiment/{symbol}`` endpoint (``api/metrics_api.py``) uses — one
honesty contract enforced centrally, not two divergent implementations.
``st.metric`` shows "—" for any ``None`` field; a clear note explains WHY
when ``source == "unavailable"`` rather than rendering a silent wall of
dashes.

These two sections are DISTINCT signals — the News Catalyst score comes from
the always-on pipeline (FinBERT / keyword lexicon over Finnhub headlines);
the Antigravity section is an on-demand LLM agent call plus a real
per-request GARCH fit over price history. Labeled separately so they are
never conflated. Explainer prose lives in ``gui/help_content.py``
(``TAB_HELP["sentiment_dynamics"]``, ``SECTION_HELP``, ``METRIC_HELP``) per
this repo's convention — this module holds no hard-coded educational text.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pandas as pd
import streamlit as st

from gui import help_widgets
from gui.panels import load_state_snapshot
from sentiment_risk_engine import SentimentRiskEngine, SentimentResult

logger = logging.getLogger(__name__)


def _news_catalyst_row(snap: Dict[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
    """Extract the single ``news_sentiment`` value for *symbol* from the state
    snapshot (pure — mirrors ``gui.panels.analytics_signals._sentiment_rows``,
    scoped to one symbol instead of every symbol).

    Returns ``None`` when the snapshot has no signals, *symbol* isn't in it,
    or its ``news_sentiment`` is null/absent/NaN — CONSTRAINT #4: a symbol
    with no scored news is skipped honestly, never rendered as a fabricated
    neutral 0.0.
    """
    if not isinstance(snap, dict):
        return None
    try:
        for sig in snap.get("signals") or []:
            if not isinstance(sig, dict):
                continue
            if str(sig.get("symbol", "")).upper() != symbol.upper():
                continue
            val = sig.get("news_sentiment")
            if val is None:
                return None
            try:
                f = float(val)
            except (TypeError, ValueError):
                return None
            if f != f:  # NaN
                return None
            return {"symbol": symbol, "news_sentiment": f}
        return None
    except Exception as exc:  # noqa: BLE001 - dead-letter, never raise into UI
        logger.debug("news-catalyst row extraction failed for %s: %s", symbol, exc)
        return None


def _run_live_sentiment(
    engine: SentimentRiskEngine, ticker: str, date: datetime, returns: pd.Series
) -> SentimentResult:
    """Run the async ``get_live_sentiment`` coroutine to completion.

    Streamlit reruns the whole script on every interaction on a fresh
    thread, so — mirroring ``gui/panels/live_inventory.py``'s async-sync
    pattern — a new event loop is created explicitly rather than relying on
    ``asyncio.run()``.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(engine.get_live_sentiment(ticker, date, returns))
    finally:
        loop.close()


def _fmt(v: Optional[float]) -> str:
    """Honest numeric formatting — ``None`` renders as "—", never a guessed number."""
    return "—" if v is None else f"{v:.3f}"


def render_sentiment_dynamics() -> None:
    help_widgets.explain("sentiment_dynamics")
    st.header("💬 Social Media Sentiment Dynamics")
    st.info(
        "📋 **Read-only, advisory-only.** Every metric below is either a real "
        "computed/fetched value or an honest \"—\" — nothing on this tab is "
        "simulated.",
        icon="📋",
    )

    snap = load_state_snapshot()
    sig_list = snap.get("signals", []) if isinstance(snap, dict) else []
    if not sig_list:
        st.caption(
            "No `state_snapshot.json` yet — run the orchestrator (Launcher tab) "
            "to populate the symbol universe this tab reads from."
        )
        return
    sig_df = pd.DataFrame(sig_list)
    symbols = sorted(sig_df["symbol"].astype(str).unique()) if "symbol" in sig_df.columns else []
    if not symbols:
        st.caption("Signals frame has no `symbol` column to iterate over.")
        return

    symbol = st.selectbox("Symbol", options=symbols, key="sentiment_dynamics_symbol")

    st.divider()

    # ── Section 1 — News Catalyst Sentiment (real, always-on pipeline) ──────
    st.subheader("1. News Catalyst Sentiment")
    help_widgets.section_caption("sentiment_dynamics.news_catalyst")
    row = _news_catalyst_row(snap, symbol)
    if row is None:
        st.info(
            f"No news-sentiment data for {symbol} in the latest snapshot "
            "(news_catalyst may not have scored it, or hasn't run)."
        )
    else:
        help_widgets.metric_with_help(
            "News Sentiment (FinBERT / keyword lexicon)",
            f"{row['news_sentiment']:+.3f}",
            "sentiment_dynamics.news_sentiment",
        )

    st.divider()

    # ── Section 2 — Antigravity Agent + GJR-GARCH (on-demand, real) ─────────
    st.subheader("2. Antigravity Agent Sentiment + GJR-GARCH Asymmetric Volatility")
    help_widgets.section_caption("sentiment_dynamics.antigravity_agent")

    try:
        from data.historical_store import HistoricalStore  # lazy import (CLAUDE.md convention)

        price_df = HistoricalStore(readonly=True).get_bars(symbol, lookback_days=504)
        returns = (
            price_df["Close"].pct_change().dropna()
            if price_df is not None and not price_df.empty
            else pd.Series(dtype=float)
        )
    except Exception as exc:  # noqa: BLE001 - dead-letter, never raise into UI
        logger.warning("sentiment_dynamics: bars fetch failed for %s: %s", symbol, exc)
        returns = pd.Series(dtype=float)

    if returns.empty:
        st.info(
            f"No price history available for {symbol} — cannot compute "
            "Antigravity sentiment or GJR-GARCH volatility."
        )
        return

    try:
        result = _run_live_sentiment(
            SentimentRiskEngine(), symbol, datetime.now(timezone.utc), returns
        )
    except Exception as exc:  # noqa: BLE001 - dead-letter, never raise into UI
        logger.warning("sentiment_dynamics: get_live_sentiment failed for %s: %s", symbol, exc)
        st.error(f"Sentiment lookup failed for {symbol}: {exc}")
        return

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        help_widgets.metric_with_help(
            "Sentiment Score", _fmt(result.sentiment_score), "sentiment_dynamics.sentiment_score"
        )
    with c2:
        help_widgets.metric_with_help(
            "Sentiment Intensity",
            _fmt(result.sentiment_intensity),
            "sentiment_dynamics.sentiment_intensity",
        )
    with c3:
        help_widgets.metric_with_help(
            "Credibility Score",
            _fmt(result.credibility_score),
            "sentiment_dynamics.credibility_score",
        )
    with c4:
        help_widgets.metric_with_help(
            "Volatility Persistence (α+β+γ/2)",
            _fmt(result.volatility_persistence),
            "sentiment_dynamics.volatility_persistence",
        )

    if result.source == "unavailable":
        st.info(
            "🔌 **Antigravity agent unavailable for this request** — the "
            "`google.antigravity` SDK isn't installed, `GEMINI_API_KEY` isn't "
            "set, or the live call failed. Sentiment Score / Intensity / "
            "Credibility above are honestly blank rather than guessed. "
            "Volatility Persistence is unaffected — it comes from an "
            "independent GJR-GARCH fit over price history, not the agent."
        )
    elif result.volatility_persistence is None:
        st.caption(
            "Volatility Persistence is \"—\": fewer than 100 daily return "
            f"observations were available for {symbol} to fit the GJR-GARCH model."
        )
