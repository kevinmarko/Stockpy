"""Pure data-shaping helpers for the Observability panel.

Everything here is ``streamlit``-free: plain inputs → plain outputs, so the
threshold-badge / regime / heat / heartbeat / forecast-skill formatting logic
can be unit-tested without a Streamlit runtime. ``gui/panels/observability.py``
imports these and does the actual ``st.*`` rendering, keeping the render path
byte-identical while the decision logic becomes independently verifiable.

This module is deliberately named distinctly from the pre-existing
``gui/observability_telemetry.py`` (heartbeat ring-buffer, system telemetry,
latency store, log parsing) — that module owns stateful/session-scoped
telemetry; this one owns stateless per-value formatting for the Mission Control
tab's badges and metrics.

NaN-honesty (CONSTRAINT #4): where a denominator or metric is genuinely
unavailable, helpers return ``float("nan")`` / ``"—"`` rather than fabricating a
zero or a placeholder number the operator would misread as a real value.
"""

from __future__ import annotations

import math
from typing import Any, NamedTuple, Optional

__all__ = [
    "IndicatorBadge",
    "sahm_badge",
    "hy_oas_badge",
    "yield_curve_badge",
    "vix_badge",
    "regime_emoji",
    "compute_portfolio_heat",
    "portfolio_heat_badge",
    "heartbeat_status",
    "format_rmse",
    "format_skill_weight",
]


class IndicatorBadge(NamedTuple):
    """A recession/risk-indicator status badge.

    ``level`` is a Streamlit callable name (``"error"`` / ``"warning"`` /
    ``"success"``) so the render side can do ``getattr(st, badge.level)(badge.message)``
    — keeping the rendered output byte-identical to the previous inline
    ``st.error(...)`` / ``st.warning(...)`` / ``st.success(...)`` calls.
    ``message`` is the full emoji-prefixed string.
    """

    level: str
    message: str


# ── Recession-indicator threshold badges ─────────────────────────────────────
# Each takes the *already-known-non-None* indicator value and returns the badge
# to render underneath the ``st.metric`` tile. The caller keeps the
# ``if value is not None:`` guard (so the "—" fallback tile is unchanged).


def sahm_badge(value: float) -> IndicatorBadge:
    """Sahm Rule recession indicator. Thresholds: 0.50 (killSwitch) / 0.30
    (HMM-agreement fast-trigger)."""
    if value >= 0.5:
        return IndicatorBadge("error", "🔴 ≥ 0.50 — kill-switch threshold breached")
    if value >= 0.3:
        return IndicatorBadge(
            "warning", "🟡 ≥ 0.30 — fast-trigger zone (HMM agreement needed)"
        )
    return IndicatorBadge("success", "🟢 < 0.30 — below fast-trigger zone")


def hy_oas_badge(value: float) -> IndicatorBadge:
    """High-Yield Option-Adjusted Spread (%). Thresholds: 6.0 (RECESSION) /
    4.5 (CREDIT EVENT)."""
    if value >= 6.0:
        return IndicatorBadge("error", "🔴 ≥ 6.0% — RECESSION regime trigger")
    if value >= 4.5:
        return IndicatorBadge("warning", "🟡 ≥ 4.5% — CREDIT EVENT zone")
    return IndicatorBadge("success", "🟢 < 4.5% — below credit-stress threshold")


def yield_curve_badge(value: float) -> IndicatorBadge:
    """10Y-2Y spread (%). Inversion below -0.25 is part of the RECESSION gate."""
    if value < -0.25:
        return IndicatorBadge("warning", "🟡 Inverted (< -0.25%)")
    return IndicatorBadge("success", "🟢 Not inverted")


def vix_badge(value: float) -> IndicatorBadge:
    """CBOE VIX. Thresholds: 30 (killSwitch) / 25 (lowered HMM-agreement zone)."""
    if value > 30:
        return IndicatorBadge("error", "🔴 > 30 — kill-switch VIX threshold breached")
    if value > 25:
        return IndicatorBadge(
            "warning", "🟡 > 25 — lowered-threshold zone (HMM-agreement)"
        )
    return IndicatorBadge("success", "🟢 ≤ 25")


# ── Regime / heat / heartbeat status glyphs ──────────────────────────────────


def regime_emoji(regime: Any) -> str:
    """Traffic-light glyph for a market-regime label.

    Green for RISK ON, red for RECESSION, amber for everything else (NEUTRAL,
    CREDIT EVENT, UNKNOWN, …) — matching the system-health-bar convention.
    """
    s = str(regime)
    if "RISK ON" in s:
        return "🟢"
    if "RECESSION" in s:
        return "🔴"
    return "🟡"


def compute_portfolio_heat(
    adverse_abs: float, total_equity: Optional[float]
) -> float:
    """Portfolio heat = |adverse open-position P&L| / total account equity.

    ``total_equity`` is sourced from the cached Robinhood account snapshot
    (``cache/account_snapshot.json``). Returns ``float("nan")`` — never a
    fabricated denominator (CONSTRAINT #4) — when equity is missing, unparseable,
    non-finite, or ≤ 0, so the caller can degrade the tile to "—" honestly
    instead of dividing by a hard-coded placeholder.
    """
    if total_equity is None:
        return float("nan")
    try:
        eq = float(total_equity)
    except (TypeError, ValueError):
        return float("nan")
    if not math.isfinite(eq) or eq <= 0:
        return float("nan")
    try:
        return float(adverse_abs) / eq
    except (TypeError, ValueError):
        return float("nan")


def portfolio_heat_badge(heat_pct: float) -> str:
    """Traffic-light glyph for portfolio heat: 🔴 > 5%, 🟡 > 3%, else 🟢."""
    return "🔴" if heat_pct > 0.05 else ("🟡" if heat_pct > 0.03 else "🟢")


def heartbeat_status(latest_age: float) -> str:
    """Orchestrator heartbeat freshness label from the latest age (seconds).

    NaN age → "⚪ No heartbeat"; > 120 s → "🔴 Stale"; > 60 s → "🟡 Slow";
    otherwise "🟢 Fresh".
    """
    if latest_age != latest_age:  # NaN
        return "⚪ No heartbeat"
    if latest_age > 120:
        return "🔴 Stale"
    if latest_age > 60:
        return "🟡 Slow"
    return "🟢 Fresh"


# ── Forecast-skill table cell formatters ─────────────────────────────────────


def format_rmse(r: Optional[float]) -> str:
    """RMSE cell: 4-decimal string, or "—" when ``None`` / NaN (``r == r`` is the
    NaN guard — no fabricated 0.0000)."""
    return f"{r:.4f}" if r is not None and r == r else "—"


def format_skill_weight(w: Optional[float]) -> str:
    """Skill-weight cell: percent string, or "—" when ``None`` (cold start)."""
    return f"{w:.1%}" if w is not None else "—"
