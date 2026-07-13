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
from typing import Dict, Optional

import pandas as pd

from engine.advisory import CONFIG as _ADVISORY_CONFIG
from validation.thresholds import NET_SHARPE_MIN

# ---------------------------------------------------------------------------
# Severity palette — single source of truth (light + dark variants)
# ---------------------------------------------------------------------------
# Every severity colour used by the Styler helpers below (and, going forward,
# by any panel that wants theme-aware chrome) is defined ONCE here, with a
# light-mode and a dark-mode hex chosen for WCAG-reasonable contrast against
# that theme's table/background surface.
#
# Why not the raw brand accents?  The bright brand greens/ambers
# (``#10b981`` / ``#f59e0b``) are eye-catching but score badly for *text*
# contrast — ``#10b981`` on white is ~2.1:1 and ``#f59e0b`` ~1.9:1, both well
# under the 4.5:1 AA threshold.  The ``light`` values below are the same hues
# darkened into an AA-reasonable range for coloured text; the ``dark`` values
# are lightened so they stay legible on a dark surface.  The bright accents are
# retained under :data:`BRAND_ACCENTS` for non-text UI chrome (bars, dots,
# borders) where luminance contrast matters far less.
SEVERITY_PALETTE: Dict[str, Dict[str, str]] = {
    "light": {
        "positive": "#047857",  # emerald-700  — ~5:1 on white
        "negative": "#dc2626",  # red-600      — ~4.5:1 on white
        "warning": "#b45309",   # amber-700    — ~4.7:1 on white (was #f59e0b)
        "neutral": "#334155",   # slate-700
    },
    "dark": {
        "positive": "#34d399",  # emerald-400  — legible on dark surfaces
        "negative": "#f87171",  # red-400
        "warning": "#fbbf24",   # amber-400
        "neutral": "#cbd5e1",   # slate-300
    },
}

#: Bright brand accents — kept for non-text chrome (progress bars, status dots,
#: borders) where the low text-contrast of these hues is irrelevant. Not used
#: for coloured *text* (see the accessible :data:`SEVERITY_PALETTE`).
BRAND_ACCENTS: Dict[str, str] = {
    "positive": "#10b981",
    "negative": "#ef4444",
    "warning": "#f59e0b",
    "neutral": "#64748b",
}


def severity_color(name: str, theme: str = "light") -> str:
    """Return the hex for a severity (``positive``/``negative``/``warning``/
    ``neutral``) in the given ``theme`` (``"light"`` | ``"dark"``).

    Falls back to the light palette for an unknown theme and to ``""`` for an
    unknown severity name — never raises (used in render paths).
    """
    palette = SEVERITY_PALETTE.get(theme, SEVERITY_PALETTE["light"])
    return palette.get(name, "")


# Convenience light-mode shortcuts used by the pandas-Styler helpers below.
# Styler emits *static* inline CSS (it cannot react to prefers-color-scheme per
# cell), so the helpers use the accessible light-mode values; the dark variants
# and the CSS custom properties below drive the theme-aware app chrome and any
# future per-panel retrofit.
_POSITIVE = SEVERITY_PALETTE["light"]["positive"]
_NEGATIVE = SEVERITY_PALETTE["light"]["negative"]
_WARNING = SEVERITY_PALETTE["light"]["warning"]

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
        return f"color: {_POSITIVE}; font-weight: 600;"
    if v < 0:
        return f"color: {_NEGATIVE}; font-weight: 600;"
    return ""


def _color_kelly_target(val) -> str:
    """Red at/above the advisory ceiling, amber in the warning zone, else default."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ""
    if v >= KELLY_CEILING_PCT:
        return f"color: {_NEGATIVE}; font-weight: 700;"
    if v >= KELLY_WARN_PCT:
        return f"color: {_WARNING}; font-weight: 600;"
    return ""


def _color_conviction(val) -> str:
    """Green > CONVICTION_GREEN_MIN, yellow in the mid band, red below the low cutoff."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ""
    if v >= CONVICTION_GREEN_MIN:
        return f"color: {_POSITIVE}; font-weight: 600;"
    if v < _CONVICTION_LOW_CUTOFF:
        return f"color: {_NEGATIVE}; font-weight: 600;"
    if v < CONVICTION_YELLOW_MIN:
        return f"color: {_WARNING};"
    return ""


def _color_sharpe(val) -> str:
    """Red if below the net-Sharpe deployability gate, else default."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ""
    if v < VALIDATION_SHARPE_MIN:
        return f"color: {_NEGATIVE}; font-weight: 600;"
    return f"color: {_POSITIVE};"


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


# ---------------------------------------------------------------------------
# Global CSS injection — theme-aware severity custom properties + tab-bar
# responsiveness.  Injected ONCE at app startup by gui/app.py.
# ---------------------------------------------------------------------------


def build_global_css() -> str:
    """Return the ``<style>`` block that defines the theme-aware severity
    palette as CSS custom properties plus light tab-bar responsiveness tweaks.

    The custom properties (``--sev-positive`` etc.) default to the accessible
    **light** values on ``:root`` and are overridden to the **dark** values via
    both a ``@media (prefers-color-scheme: dark)`` block (honors the OS/browser
    theme) and a ``[data-theme="dark"]`` selector (honors Streamlit's own theme
    toggle, which stamps ``data-theme`` on the app root).  Nothing consumes the
    variables yet beyond this injection, so app chrome renders exactly as
    before; the variables are the foundation for the documented follow-up
    per-panel palette retrofit.

    The tab-bar rules let the 18-tab row **wrap** instead of overflowing /
    horizontally scrolling on narrow viewports — all tabs stay reachable.
    """
    light = SEVERITY_PALETTE["light"]
    dark = SEVERITY_PALETTE["dark"]

    def _vars(palette: Dict[str, str], indent: str) -> str:
        return "\n".join(
            f"{indent}--sev-{name}: {palette[name]};"
            for name in ("positive", "negative", "warning", "neutral")
        )

    return f"""<style>
:root {{
{_vars(light, "  ")}
}}
@media (prefers-color-scheme: dark) {{
  :root {{
{_vars(dark, "    ")}
  }}
}}
:root[data-theme="dark"], [data-theme="dark"] {{
{_vars(dark, "  ")}
}}
/* Let the 18-tab bar wrap onto multiple rows instead of overflowing on
   narrow viewports — every tab stays reachable without horizontal scroll. */
.stTabs [data-baseweb="tab-list"] {{
  flex-wrap: wrap;
  row-gap: 0.25rem;
}}
</style>"""


def inject_global_css() -> None:
    """Inject :func:`build_global_css` into the current Streamlit app once.

    Safe to import in non-Streamlit contexts: ``streamlit`` is imported lazily
    and any failure is swallowed (a stylesheet must never crash the app).
    """
    try:
        import streamlit as st

        st.markdown(build_global_css(), unsafe_allow_html=True)
    except Exception:  # noqa: BLE001 - cosmetic; never break the app over CSS
        pass
