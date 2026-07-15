"""InvestYo Command Center — Help tab. Renders a searchable glossary, an onboarding tour, and per-tab descriptions sourced from gui/help_content.py."""

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


@st.cache_data(ttl=300)
def _load_guide_section(anchor: str) -> str:
    """Extract the markdown body of the section whose heading slug == ``anchor``.

    Returns "" when the file is missing or no section matches (CONSTRAINT #6
    — never raises). The returned string excludes the heading line itself and
    stops at the next heading of equal or higher level.
    """
    import re as _re
    if not anchor or not anchor.startswith("#"):
        return ""
    target = anchor.lstrip("#")
    guide = _REPO_ROOT / "docs" / "HOW_TO_GUIDE.md"
    try:
        lines = guide.read_text(encoding="utf-8").splitlines()
    except Exception:
        return ""

    def _slug(text: str) -> str:
        t = text.lower()
        t = _re.sub(r"[^\w\s-]", "", t)
        return t.replace(" ", "-")

    in_section = False
    section_level = 0
    body: list[str] = []
    for line in lines:
        heading_match = _re.match(r"^(#+)\s+(.*)$", line)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            if in_section and level <= section_level:
                break
            if not in_section and _slug(heading_text) == target:
                in_section = True
                section_level = level
                continue
        if in_section:
            body.append(line)
    return "\n".join(body).strip()



def render_help() -> None:
    """❓ Help tab — searchable glossary, onboarding tour, and tab descriptions."""
    from gui.onboarding import read_onboarding_state, mark_onboarded, DEFAULT_MARKER
    from gui.help_content import GLOSSARY, search_glossary

    _ob_state = read_onboarding_state(st.session_state, DEFAULT_MARKER)

    if _ob_state.should_show:
        st.info(
            "👋 **Welcome to InvestYo Command Center!** This is an advisory-only "
            "platform — it generates signals and recommendations but **never submits "
            "orders to any broker** while `ADVISORY_ONLY=true`.",
            icon="📋",
        )
        with st.expander("✅ Start here — 4-step checklist", expanded=True):
            st.markdown(
                "1. Set `FRED_API_KEY` in `.env` (free key from "
                "[fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html)).\n"
                "2. Click **🔄 Refresh Data (Advisory)** in the Launcher tab.\n"
                "3. Open the HTML report (`output/daily_report_*.html`).\n"
                "4. Review the Conviction Calibration chart (Reports tab) once "
                "closed trades accumulate."
            )
            if st.button("✅ Got it — don't show again"):
                mark_onboarded(DEFAULT_MARKER)
                st.session_state[__import__("gui.onboarding", fromlist=["SESSION_KEY"]).SESSION_KEY] = True
                st.rerun()
        st.divider()

    st.subheader("❓ In-App Help & Glossary")
    st.caption(
        "Plain-English definitions for every concept the platform uses. "
        "All information here is **informational only** — no orders are sent."
    )

    query = st.text_input("🔍 Search glossary", placeholder="e.g. Kelly, PBO, HMM …")
    if query.strip():
        results = search_glossary(query)
    else:
        results = list(GLOSSARY.values())

    if not results:
        st.info("No matching terms found.")
    else:
        for entry in results:
            with st.expander(f"**{entry.term}**"):
                st.markdown(entry.plain_english)
                if entry.guide_anchor:
                    _section = _load_guide_section(entry.guide_anchor)
                    if _section:
                        st.divider()
                        st.markdown("**📖 From the How-To Guide:**")
                        st.markdown(_section)
                    else:
                        st.caption(
                            f"_(Guide section `{entry.guide_anchor}` not found — "
                            f"see `docs/HOW_TO_GUIDE.md` in the repo.)_"
                        )

    st.divider()
    st.subheader("Tab descriptions")
    tab_ids = [
        "launcher", "reports", "settings", "strategy_matrix",
        "paper_monitor", "gravity", "options", "market_data",
        "observability", "live_inventory",
    ]
    for tab_id in tab_ids:
        help_widgets.explain(tab_id, expanded=False)




# ===========================================================================
# Tab 11 — Prompt Registry
# ===========================================================================


