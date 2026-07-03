"""
gui/help_widgets.py
===================
Thin Streamlit wrappers over ``gui.help_content``.  Each widget degrades
gracefully to a **no-op + DEBUG log** when a key is missing (CONSTRAINT #6),
so a typo in a panel call never blanks the UI and never raises.

Every function is ≤ 15 lines of logic so panels stay declarative.

Public API
----------
``explain(tab_id, *, expanded=False)``
    Renders the full :class:`~gui.help_content.TabHelp` for a tab as a
    collapsible ``st.expander``.

``help_expander(title, body_md)``
    Generic titled expander for section-level help blocks.

``metric_with_help(label, value, metric_key, **kw)``
    ``st.metric(…, help=metric_help(metric_key))`` — ``help=None`` for
    unknown keys (never raises).

``glossary_chip(term)``
    Renders a term with its ``plain_english`` as an inline tooltip via
    ``st.popover`` (fallback: ``st.caption`` on older Streamlit builds).

``why_callout(text)``
    Small ``st.info`` box answering "why am I seeing this?".  No-op when
    *text* is falsy.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import streamlit as st

from gui.help_content import get_tab_help, metric_help

logger = logging.getLogger(__name__)


def explain(tab_id: str, *, expanded: bool = False) -> None:
    """Render the :class:`~gui.help_content.TabHelp` for *tab_id* as a
    collapsible ``st.expander("❓ What is this & how do I use it?")``.

    No-ops with a DEBUG log when *tab_id* has no entry in ``TAB_HELP``,
    so a typo never blanks a panel.
    """
    tab = get_tab_help(tab_id)
    if tab is None:
        logger.debug("explain: no TabHelp found for tab_id=%r", tab_id)
        return
    with st.expander("❓ What is this & how do I use it?", expanded=expanded):
        st.markdown(tab.description)


def help_expander(title: str, body_md: Optional[str]) -> None:
    """Generic titled expander for section-level help.

    No-ops when *body_md* is empty or ``None``.
    """
    if not body_md:
        return
    with st.expander(title, expanded=False):
        st.markdown(body_md)


def metric_with_help(label: str, value: Any, metric_key: str, **kw: Any) -> None:
    """Wrap ``st.metric`` with a ``help=`` tooltip pulled from ``METRIC_HELP``.

    Falls back to ``help=None`` (no tooltip) when *metric_key* is unknown —
    never raises (CONSTRAINT #6).
    """
    tip = metric_help(metric_key)
    st.metric(label, value, help=tip or None, **kw)


def glossary_chip(term: str) -> None:
    """Render *term* with its ``plain_english`` as an inline tooltip.

    Uses ``st.popover`` when available (Streamlit ≥ 1.31); falls back to
    ``st.caption``.  No-ops (DEBUG log) when *term* is not in ``GLOSSARY``.
    """
    from gui.help_content import get_glossary  # local to avoid any circular-import risk

    entry = get_glossary(term)
    if entry is None:
        logger.debug("glossary_chip: no GLOSSARY entry for term=%r", term)
        return
    _popover = getattr(st, "popover", None)
    if _popover is not None:
        with _popover(entry.term):
            st.caption(entry.plain_english)
    else:
        st.caption(f"**{entry.term}** — {entry.plain_english}")


def why_callout(text: Optional[str]) -> None:
    """Render a small ``st.info`` box for contextual "why is this here?" notes.

    No-ops when *text* is empty or ``None``.
    """
    if not text:
        return
    st.info(text)
