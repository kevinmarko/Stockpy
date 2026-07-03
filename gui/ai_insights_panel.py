"""
gui/ai_insights_panel.py — Streamlit-free helpers for the AI Insights tab.
===========================================================================

The Streamlit-facing wiring lives in :func:`gui.panels.render_ai_insights`.
This module hosts the pure helpers it depends on so they remain unit-
testable headlessly — mirrors the pattern used by
:mod:`gui.llm_commentary_panel` and :mod:`gui.gravity_ai_panel`.

What lives here
---------------
* :func:`insights_status` — three-state classifier reused for the tab
  (disabled / missing_key / ready), keyed on
  ``LLM_COMMENTARY_ENABLED`` + ``GEMINI_API_KEY``.  The chart pattern
  surface and the analyst-note surface share the same master switch so
  the operator can enable both at once.
* :func:`format_chart_pattern_markdown` — render a ``ChartPatternRead``
  dump as Markdown for the panel.  Returns an "unavailable" sentinel on
  ``None`` (CONSTRAINT #6 — caller never sees a blank box).
* :func:`format_research_brief_markdown` — same contract for Opal's
  ``ResearchBrief`` (Tier 9 Scope 4), rendered at the TOP of the tab
  (front-of-pipeline).
* :func:`derive_disagreement_overview` — aggregate disagreement table:
  takes the current ``state_snapshot.json`` ``signals`` list + a cached
  Claude-vs-Gemini directional map and emits one row per symbol with
  the deterministic action, the Claude verdict, the Gemini verdict, and
  a disagreement boolean.  Missing sides render as ``"—"`` not
  fabricated PASSED (CONSTRAINT #4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional

logger = logging.getLogger(__name__)


InsightsStatus = Literal[
    "disabled",       # LLM_COMMENTARY_ENABLED is False — section hidden
    "missing_key",    # master switch on but GEMINI_API_KEY unset
    "ready",          # master switch on AND key set
]


def insights_status(settings_obj: Any) -> InsightsStatus:
    """Return the three-state status for the AI Insights tab.

    Duck-typed on ``LLM_COMMENTARY_ENABLED`` + ``GEMINI_API_KEY`` (the
    multimodal path uses Gemini; the analyst-note section reuses the
    Claude commentary helper which keys on its own ``ANTHROPIC_API_KEY``
    so the operator can mix-and-match).
    """
    if not getattr(settings_obj, "LLM_COMMENTARY_ENABLED", False):
        return "disabled"
    if not (getattr(settings_obj, "GEMINI_API_KEY", None) or ""):
        return "missing_key"
    return "ready"


def format_chart_pattern_markdown(payload: Optional[Mapping[str, Any]]) -> str:
    """Render a :class:`ChartPatternRead` model_dump as Markdown.

    Returns the "unavailable" sentinel when ``payload`` is ``None`` or
    empty so the caller never has to special-case that path.  All sections
    are rendered only when their field is present; missing fields produce
    no output — never a fabricated placeholder.
    """
    if not payload:
        return (
            "_Chart pattern interpretation unavailable._  The provider returned no "
            "payload (soft-fail).  The chart above is the source of truth."
        )

    pattern = str(payload.get("pattern_name", "") or "").strip()
    trend = str(payload.get("trend_direction", "") or "").strip()
    narrative = str(payload.get("narrative", "") or "").strip()
    confidence = str(payload.get("confidence", "") or "").strip()
    supports: List[str] = [
        str(s).strip() for s in (payload.get("support_levels") or []) if str(s).strip()
    ]
    resistances: List[str] = [
        str(s).strip() for s in (payload.get("resistance_levels") or []) if str(s).strip()
    ]

    parts: List[str] = []
    if pattern:
        parts.append(f"### {pattern}")
    if trend:
        trend_arrow = {"bullish": "▲", "bearish": "▼", "neutral": "→"}.get(trend.lower(), "")
        suffix = f" ({confidence} confidence)" if confidence else ""
        parts.append(f"**Trend:** {trend_arrow} {trend}{suffix}")
    if narrative:
        parts.append(narrative)
    if supports:
        parts.append("**Support:**\n" + "\n".join(f"- {s}" for s in supports))
    if resistances:
        parts.append("**Resistance:**\n" + "\n".join(f"- {r}" for r in resistances))

    return "\n\n".join(parts) if parts else format_chart_pattern_markdown(None)


def format_research_brief_markdown(payload: Optional[Mapping[str, Any]]) -> str:
    """Render a :class:`llm.schemas.ResearchBrief` model_dump as Markdown.

    Tier 9 Scope 4 (Opal) — mirrors :func:`format_chart_pattern_markdown`'s
    contract exactly: returns the "unavailable" sentinel when ``payload`` is
    ``None``/empty so the caller never special-cases that path, and renders
    each section only when its field is present/non-empty — partial-safe,
    never a fabricated placeholder (CONSTRAINT #4).
    """
    if not payload:
        return (
            "_Opal research brief unavailable._  The provider returned no "
            "payload (soft-fail), Opal is disabled, or no API key is "
            "configured.  The deterministic recommendation above is the "
            "source of truth."
        )

    thesis = str(payload.get("thesis_context", "") or "").strip()
    confidence = str(payload.get("data_confidence", "") or "").strip()
    sources_note = str(payload.get("sources_note", "") or "").strip()
    catalysts: List[str] = [
        str(c).strip() for c in (payload.get("catalysts") or []) if str(c).strip()
    ]
    risks: List[str] = [
        str(r).strip() for r in (payload.get("risk_factors") or []) if str(r).strip()
    ]
    developments: List[str] = [
        str(d).strip() for d in (payload.get("recent_developments") or []) if str(d).strip()
    ]

    parts: List[str] = []
    if thesis:
        suffix = f" ({confidence} confidence)" if confidence else ""
        parts.append(f"**Thesis:** {thesis}{suffix}")
    if catalysts:
        parts.append("**Catalysts:**\n" + "\n".join(f"- {c}" for c in catalysts))
    if risks:
        parts.append("**Risk factors:**\n" + "\n".join(f"- {r}" for r in risks))
    if developments:
        parts.append("**Recent developments:**\n" + "\n".join(f"- {d}" for d in developments))
    if sources_note:
        parts.append(f"_{sources_note}_")

    return "\n\n".join(parts) if parts else format_research_brief_markdown(None)


# ---------------------------------------------------------------------------
# Aggregate disagreement view
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DisagreementRow:
    """One row in the aggregate disagreement table."""

    symbol: str
    advisory_action: str             # the deterministic action from the pipeline
    claude_verdict: Optional[str]    # AnalystRationale.trend_direction or "—"
    gemini_verdict: Optional[str]    # ChartPatternRead.trend_direction or "—"
    disagreement: bool


def _pick_action_str(row: Mapping[str, Any]) -> str:
    """Lift the deterministic action label from a state-snapshot signal row."""
    for key in ("action", "advisory_action", "Action Signal"):
        val = row.get(key)
        if val:
            return str(val).upper()
    return "HOLD"


def _normalise_direction(value: Any) -> Optional[str]:
    """Lower-case + whitespace-strip a trend-direction string.

    Returns ``None`` for empty / missing values so the table renders
    ``"—"`` rather than a fabricated entry (CONSTRAINT #4).
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    return text


def derive_disagreement_overview(
    signals: Iterable[Mapping[str, Any]],
    claude_map: Optional[Mapping[str, Mapping[str, Any]]] = None,
    gemini_map: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> List[DisagreementRow]:
    """Build the per-symbol agreement / disagreement view.

    Parameters
    ----------
    signals :
        Iterable of state-snapshot signal rows (from
        ``output/state_snapshot.json``).  Must include at minimum a
        ``symbol`` field; ``action`` is also picked up if present.
    claude_map :
        Optional ``{symbol: AnalystRationale.model_dump()}``.  The cell
        in the disagreement row is sourced from a heuristic mapping of
        the rationale headline → a trend label; an explicit
        ``trend_direction`` field is also honoured if the caller adds one.
        Missing entries render as ``"—"``.
    gemini_map :
        Optional ``{symbol: ChartPatternRead.model_dump()}``.  The cell
        comes directly from ``trend_direction``.

    Returns
    -------
    list[DisagreementRow]
        One row per signal row, in the order they were given.  A row
        flags ``disagreement=True`` ONLY when BOTH sides have a non-None
        direction AND they differ — partial coverage never flags
        (CONSTRAINT #4: never fabricate "disagreement" against a missing
        side).
    """
    claude_map = claude_map or {}
    gemini_map = gemini_map or {}
    out: List[DisagreementRow] = []
    for sig in signals:
        if not isinstance(sig, Mapping):
            continue
        symbol = str(sig.get("symbol") or sig.get("Symbol") or "").upper().strip()
        if not symbol:
            continue
        action = _pick_action_str(sig)

        claude_entry = claude_map.get(symbol) or {}
        gemini_entry = gemini_map.get(symbol) or {}
        claude_dir = _normalise_direction(
            claude_entry.get("trend_direction")
            or _heuristic_direction_from_rationale(claude_entry)
        )
        gemini_dir = _normalise_direction(gemini_entry.get("trend_direction"))

        disagreement = (
            claude_dir is not None
            and gemini_dir is not None
            and claude_dir != gemini_dir
        )
        out.append(DisagreementRow(
            symbol=symbol,
            advisory_action=action,
            claude_verdict=claude_dir,
            gemini_verdict=gemini_dir,
            disagreement=bool(disagreement),
        ))
    return out


def _heuristic_direction_from_rationale(payload: Mapping[str, Any]) -> Optional[str]:
    """Best-effort lift of a direction from an AnalystRationale headline.

    The :class:`llm.schemas.AnalystRationale` schema does NOT include a
    direction field — Claude's rationale is prose, not a verdict.  The
    aggregate view still wants a comparable token, so we cheaply scan the
    headline for the same three words Gemini's schema uses.  Missing /
    ambiguous → ``None`` (no fabrication).
    """
    if not isinstance(payload, Mapping):
        return None
    headline = str(payload.get("headline") or "").lower()
    if not headline:
        return None
    if any(tok in headline for tok in ("bullish", "uptrend", "rally", "breakout above")):
        return "bullish"
    if any(tok in headline for tok in ("bearish", "downtrend", "breakdown", "sell-off")):
        return "bearish"
    if any(tok in headline for tok in ("neutral", "sideways", "consolidation", "range-bound")):
        return "neutral"
    return None


def disagreement_summary(rows: Iterable[DisagreementRow]) -> Dict[str, int]:
    """Roll up a list of :class:`DisagreementRow` into KPI counters."""
    rows = list(rows)
    total = len(rows)
    both = sum(
        1 for r in rows if r.claude_verdict is not None and r.gemini_verdict is not None
    )
    disagree = sum(1 for r in rows if r.disagreement)
    return {
        "total_symbols": total,
        "both_present": both,
        "agreements": both - disagree if both else 0,
        "disagreements": disagree,
    }
