from __future__ import annotations

from __future__ import annotations
import io
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
import streamlit as st
from settings import settings
from gui import env_io, orchestrator_runner, help_widgets
from gui.symbol_search import filter_by_symbol
from gui.orchestrator_runner import StageStatus
from gui.panels._shared import (  # noqa: E402
    GICS_SECTORS,
    _BF_EDITOR_COLUMNS,
    _REPO_ROOT,
    _active_symbols,
    _held_symbols,
    _kill_switch,
    _signal_symbols,
    _watchlist_symbols,
    load_block_log,
    logger,
)


def _render_gemini_chart_section(symbol: str) -> None:
    """Inner helper — chart render + on-demand Gemini Vision call."""
    if not symbol:
        return
    try:
        from llm.chart_insight import generate_chart_pattern_read, render_price_chart_png
    except Exception as exc:
        st.caption(f"(chart_insight helpers unavailable: {exc})")
        return

    # Fetch bars from the live market-data provider — same path the rest
    # of the platform uses.  Soft-fail to caption on any failure.
    try:
        from data.market_data import get_provider  # noqa: PLC0415

        provider = get_provider()
        bars = provider.get_intraday_bars(symbol, lookback_days=252)
    except Exception as exc:
        st.caption(f"Could not fetch bars for {symbol}: {exc}")
        return

    png = render_price_chart_png(symbol, bars)
    if png:
        st.image(png, caption=f"{symbol} — last 252 bars", width='stretch')
    else:
        st.caption("Chart render failed (insufficient bars).")
        return

    session_slot = f"ai_insights_gemini_payload_{symbol}"
    by_symbol_slot = "ai_insights_gemini_by_symbol"
    if st.button(
        "📈 Interpret chart with Gemini",
        key=f"ai_insights_gemini_btn_{symbol}",
        width="stretch",
    ):
        with st.spinner(f"Asking Gemini to read the {symbol} chart…"):
            result = generate_chart_pattern_read(symbol, bars)
        payload = result.model_dump() if result is not None else None
        st.session_state[session_slot] = payload
        # Mirror map for the aggregate view.
        mirror = st.session_state.get(by_symbol_slot, {})
        if payload is not None:
            mirror[symbol] = payload
        else:
            mirror.pop(symbol, None)
        st.session_state[by_symbol_slot] = mirror

    cached = st.session_state.get(session_slot)
    if cached is not None or session_slot in st.session_state:
        try:
            from gui.ai_insights_panel import format_chart_pattern_markdown
        except Exception:
            st.json(cached)
            return
        st.markdown(format_chart_pattern_markdown(cached))


# ===========================================================================
# Tab 14 — AI Control Center (one place for every AI option, operator-only)
# ===========================================================================


def render_ai_insights() -> None:
    """Render the AI Insights tab — Claude analyst + Gemini chart vision + aggregate view.

    Three sections, all gated by the same ``LLM_COMMENTARY_ENABLED``
    master switch:

    1.  **Per-symbol Claude analyst note** — reuses
        :mod:`gui.llm_commentary_panel` so this tab and the Reports-tab
        drill-down button share one code path AND one session-state cache.
    2.  **Gemini chart pattern interpretation** — renders a 252-bar
        matplotlib chart for the selected symbol and (on click) sends it
        to Gemini Vision via :func:`llm.chart_insight.generate_chart_pattern_read`.
    3.  **Aggregate disagreement view** — walks the cached Claude /
        Gemini outputs in ``st.session_state`` and renders one row per
        watchlist symbol with the deterministic action, the Claude verdict,
        the Gemini verdict, and a disagreement boolean.

    Every section is wrapped in try/except so a section's failure renders
    an inline error message without aborting the tab (CONSTRAINT #6).
    """
    help_widgets.explain("ai_insights")
    st.subheader("🪄 AI Insights — Claude analyst + Gemini Vision")

    try:
        from gui.ai_insights_panel import (
            derive_disagreement_overview,
            disagreement_summary,
            format_chart_pattern_markdown,
            insights_status,
        )
    except Exception as exc:  # pragma: no cover - import-time degrade
        st.error(f"AI Insights helpers unavailable: {exc}")
        return

    status = insights_status(settings)
    if status == "disabled":
        st.info(
            "AI Insights is off.  Set `LLM_COMMENTARY_ENABLED=true` and at "
            "least `GEMINI_API_KEY=…` (plus `ANTHROPIC_API_KEY=…` for the "
            "analyst notes) in `.env`, then relaunch the GUI."
        )
        return
    if status == "missing_key":
        st.warning(
            "`LLM_COMMENTARY_ENABLED=true` but `GEMINI_API_KEY` is unset.  "
            "The chart-pattern section will be a no-op; the analyst-note "
            "section still works if `ANTHROPIC_API_KEY` is set."
        )

    # ── Symbol picker (shared across the three sections) ────────────────
    snap = load_state_snapshot()
    sig_list = snap.get("signals", []) if isinstance(snap, dict) else []
    if not sig_list:
        st.caption(
            "No `state_snapshot.json` yet — run the orchestrator (Launcher tab) "
            "to populate the signal universe AI Insights iterates over."
        )
        return
    sig_df = pd.DataFrame(sig_list)
    symbols = sorted(sig_df["symbol"].astype(str).unique()) if "symbol" in sig_df.columns else []
    if not symbols:
        st.caption("Signals frame has no `symbol` column to iterate over.")
        return

    selected_symbol = st.selectbox(
        "Symbol", options=symbols, key="ai_insights_symbol"
    )
    row = (
        sig_df[sig_df["symbol"].astype(str) == selected_symbol].iloc[0].to_dict()
        if selected_symbol
        else {}
    )

    # ── Section 1 — Claude analyst note (reuses Reports-tab helper) ────
    st.markdown("---")
    st.markdown("#### 🤖 Claude analyst note")
    try:
        _render_llm_commentary_button(row, selected_symbol)
    except Exception as exc:
        st.error(f"Analyst-note section failed: {exc}")

    # ── Section 2 — Gemini chart pattern interpretation ─────────────────
    st.markdown("---")
    st.markdown("#### 📈 Gemini chart pattern interpretation")
    try:
        _render_gemini_chart_section(selected_symbol)
    except Exception as exc:
        st.error(f"Chart-pattern section failed: {exc}")

    # ── Section 3 — Aggregate disagreement view ─────────────────────────
    st.markdown("---")
    st.markdown("#### 🔍 Aggregate Claude vs Gemini disagreement")
    try:
        # Both maps are populated by symbol-keyed mirrors written at click
        # time: the Claude mirror in _render_llm_commentary_button, the
        # Gemini mirror in _render_gemini_chart_section.
        claude_by_symbol = st.session_state.get("ai_insights_claude_by_symbol", {})
        gemini_by_symbol = st.session_state.get("ai_insights_gemini_by_symbol", {})

        rows = derive_disagreement_overview(
            signals=sig_list,
            claude_map=claude_by_symbol,
            gemini_map=gemini_by_symbol,
        )
        summ = disagreement_summary(rows)
        kpi_cols = st.columns(4)
        kpi_cols[0].metric("Symbols", summ["total_symbols"])
        kpi_cols[1].metric("Both verdicts present", summ["both_present"])
        kpi_cols[2].metric("Agreements", summ["agreements"])
        kpi_cols[3].metric("Disagreements", summ["disagreements"])

        if rows:
            disp_df = pd.DataFrame([
                {
                    "Symbol": r.symbol,
                    "Action (deterministic)": r.advisory_action,
                    "Claude": r.claude_verdict or "—",
                    "Gemini": r.gemini_verdict or "—",
                    "Disagreement": "⚠" if r.disagreement else "",
                }
                for r in rows
            ])
            st.dataframe(disp_df, width="stretch", hide_index=True)
        else:
            st.caption("Run section 1 + 2 above on a few symbols to populate the table.")
    except Exception as exc:
        st.error(f"Aggregate view failed: {exc}")



