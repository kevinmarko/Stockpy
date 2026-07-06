"""
gui/styling.py
===============
Shared pandas-Styler helpers for severity-based conditional highlighting and
"as of" freshness badges — Task C5.

Extends the color-coding pattern already established by
``gui/panels/observability.py``'s ``_color_pnl`` / ``_style_holdings``-style
helpers (green/red P&L) to two more metrics that appear across several tabs:

* **Kelly Target / suggested position size** — thresholds sourced from
  ``engine.advisory.CONFIG["max_single_position_pct"]`` (the advisory-layer
  per-name ceiling, default 5%). A value AT the ceiling means the sizing
  engine has been clamped there — worth a red flag; 80% of the ceiling is
  the amber "approaching the cap" warning zone. This is deliberately the
  advisory ceiling, not ``settings.MAX_POSITION_WEIGHT`` (1.0 / 100%), which
  is the much looser live-execution ceiling and would almost never trip on
  advisory-mode numbers.
* **Conviction** — thresholds sourced from ``engine.advisory.CONFIG``'s own
  ``conviction_buy`` (0.70) and ``conviction_hold`` (0.55) bucket constants,
  matching the task's requested green(>0.7)/yellow(0.5-0.7)/red(<0.3) bands
  as closely as possible while staying anchored to real config values rather
  than re-typed literals. The low-end "red" cutoff (0.3) does not correspond
  to an existing named CONFIG constant, so it is defined locally as
  ``_CONVICTION_LOW_CUTOFF`` with a comment explaining the choice.
* **Validation Sharpe** — threshold sourced from
  ``validation.thresholds.NET_SHARPE_MIN`` (0.50), the same constant
  ``validation/harness.py`` and ``gui/strategy_health.py`` use for the
  deployability gate.

No imports from ``gui.panels.*`` (kept dependency-free so any future
top-level consumer outside the ``gui.panels`` package could use these
helpers too without pulling in Streamlit-panel machinery).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from engine.advisory import CONFIG as _ADVISORY_CONFIG
from validation.thresholds import NET_SHARPE_MIN

# ---------------------------------------------------------------------------
# Thresholds — sourced from live config, never re-typed literals.
# ---------------------------------------------------------------------------

KELLY_CEILING_PCT: float = float(_ADVISORY_CONFIG["max_single_position_pct"])
"""Advisory-layer per-name position-size ceiling (fraction, e.g. 0.05 = 5%)."""

KELLY_WARN_PCT: float = KELLY_CEILING_PCT * 0.8
"""Amber warning zone: 80% of the advisory ceiling — 'approaching the cap'."""

CONVICTION_GREEN_MIN: float = float(_ADVISORY_CONFIG["conviction_buy"])
"""Conviction at/above this is high-confidence (green)."""

CONVICTION_YELLOW_MIN: float = float(_ADVISORY_CONFIG["conviction_hold"])
"""Conviction at/above this (but below green) is medium-confidence (yellow)."""

# No CONFIG constant exists for a "low confidence" cutoff — the task's
# requested < 0.3 red band is defined here explicitly rather than reusing an
# unrelated constant for a different purpose.
_CONVICTION_LOW_CUTOFF: float = 0.3

VALIDATION_SHARPE_MIN: float = float(NET_SHARPE_MIN)
"""Net-of-cost Sharpe deployability gate — validation/thresholds.py."""


def _color_pnl(val) -> str:
    """CSS colour rule for a P&L-like cell: green if >0, red if <0.

    Shared by every ``gui/panels/*`` tab that renders P&L-coloured cells
    (e.g. the Observability tab's Account Holdings & P&L table) so the
    green/red convention is identical everywhere it appears.
    """
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ""
    if v > 0:
        return "color: #10b981; font-weight: 600;"
    if v < 0:
        return "color: #ef4444; font-weight: 600;"
    return ""


def _color_kelly_target(val) -> str:
    """Red at/above the advisory ceiling, amber in the warning zone, else default."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ""
    if v >= KELLY_CEILING_PCT:
        return "color: #ef4444; font-weight: 700;"
    if v >= KELLY_WARN_PCT:
        return "color: #f59e0b; font-weight: 600;"
    return ""


def _color_conviction(val) -> str:
    """Green > CONVICTION_GREEN_MIN, yellow in the mid band, red below the low cutoff."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ""
    if v >= CONVICTION_GREEN_MIN:
        return "color: #10b981; font-weight: 600;"
    if v < _CONVICTION_LOW_CUTOFF:
        return "color: #ef4444; font-weight: 600;"
    if v < CONVICTION_YELLOW_MIN:
        return "color: #f59e0b;"
    return ""


def _color_sharpe(val) -> str:
    """Red if below the net-Sharpe deployability gate, else default."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ""
    if v < VALIDATION_SHARPE_MIN:
        return "color: #ef4444; font-weight: 600;"
    return "color: #10b981;"


def style_severity(
    df: pd.DataFrame,
    *,
    kelly_cols: tuple[str, ...] = (),
    conviction_cols: tuple[str, ...] = (),
    sharpe_cols: tuple[str, ...] = (),
    pnl_cols: tuple[str, ...] = (),
):
    """Return a pandas ``Styler`` applying severity coloring to named columns.

    Every ``*_cols`` argument is a tuple of column names that may or may not
    be present in *df* — missing columns are silently skipped so a partially
    populated frame still renders (dead-letter-safe, no exception).

    Uses ``Styler.map`` (pandas >= 2.1), the same API the Observability tab's
    account-holdings table relies on.
    """
    styler = df.style
    for col in kelly_cols:
        if col in df.columns:
            styler = styler.map(_color_kelly_target, subset=[col])
    for col in conviction_cols:
        if col in df.columns:
            styler = styler.map(_color_conviction, subset=[col])
    for col in sharpe_cols:
        if col in df.columns:
            styler = styler.map(_color_sharpe, subset=[col])
    for col in pnl_cols:
        if col in df.columns:
            styler = styler.map(_color_pnl, subset=[col])
    return styler


def freshness_badge(
    timestamp: Optional[datetime],
    *,
    ttl_seconds: float,
    label: str = "Data",
) -> str:
    """Return a markdown "as of [timestamp]" string, red-flagged if stale.

    Parameters
    ----------
    timestamp:
        The UTC timestamp the data was last refreshed. ``None`` renders an
        "unknown freshness" caption rather than fabricating a time.
    ttl_seconds:
        The staleness threshold in seconds — pass the TTL that actually
        governs this data source (e.g. ``settings.DASHBOARD_REFRESH_SECONDS``,
        ``settings.MARKET_DATA_QUOTE_TTL_SECONDS``).
    label:
        Short description of what the timestamp refers to (e.g. "Snapshot",
        "Quote cache").

    Returns
    -------
    str
        A markdown string: green/neutral if fresh, red bold if older than
        ``ttl_seconds``.
    """
    if timestamp is None:
        return f"*{label}: freshness unknown (no timestamp available)*"

    ts = timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - ts).total_seconds()

    ts_str = ts.strftime("%Y-%m-%d %H:%M:%S UTC")
    if age_seconds > ttl_seconds:
        age_min = age_seconds / 60.0
        return f":red[**{label} as of {ts_str} — STALE ({age_min:.0f} min old)**]"
    return f"{label} as of {ts_str}"
