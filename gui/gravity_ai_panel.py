"""
gui/gravity_ai_panel.py — helpers for the Safety-tab AI Gravity panel.
=======================================================================

The Streamlit-facing wiring lives in :func:`gui.panels.render_gravity_audit`.
This module hosts the pure helpers it depends on so they remain unit-testable
WITHOUT installing or stubbing Streamlit — mirrors the pattern used by
:mod:`gui.llm_commentary_panel`, :mod:`gui.circuit_breakers`, and
:mod:`gui.observability_telemetry`.

What lives here
---------------
* :func:`runner_status` — three-state classifier (master switch off / keys
  missing / ready), so the panel renders a button, a "configure keys" hint,
  or a hard-disabled notice consistently.
* :func:`load_audit_report` — reads ``output/gravity_ai_audit.json``,
  returning ``None`` for missing file, corrupt JSON, or wrong-shape payload
  (CONSTRAINT #6 — never raises).
* :func:`step_rows` — turn a raw report dict into a list of display rows
  (step number, title, Claude badge, Gemini badge, disagreement flag,
  notes) so the Streamlit panel just iterates and renders.
* :func:`summarise_run` — operator-facing roll-up KPIs for the metric strip
  at the top of the section.

Design constraints
------------------
* No Streamlit imports anywhere in this file so tests can exercise every
  helper headlessly.
* No live LLM calls — the on-demand button calls
  :func:`engine.gravity_ai_runner.run_all` which wraps every provider call
  in try/except (CONSTRAINT #6).
* No fabricated metrics (CONSTRAINT #4) — missing fields read as ``None``
  and render as a "—" placeholder; we never invent a PASSED verdict.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

logger = logging.getLogger(__name__)


RunnerStatus = Literal[
    "disabled",       # GRAVITY_AI_RUNNER_ENABLED is False — no button
    "missing_key",    # master switch on but neither key set — disabled button + warning
    "partial_key",    # exactly one of ANTHROPIC_API_KEY / GEMINI_API_KEY set
    "ready",          # both keys + master switch on
]


# ---------------------------------------------------------------------------
# Status classification
# ---------------------------------------------------------------------------


def runner_status(settings_obj: Any) -> RunnerStatus:
    """Decide what the Safety-tab AI runner section should render.

    ``settings_obj`` is duck-typed on three attributes (all optional so
    stub objects in tests work):
    :attr:`GRAVITY_AI_RUNNER_ENABLED`, :attr:`ANTHROPIC_API_KEY`,
    :attr:`GEMINI_API_KEY`.

    Pure function — no I/O, no side effects.
    """
    if not getattr(settings_obj, "GRAVITY_AI_RUNNER_ENABLED", False):
        return "disabled"
    ak = getattr(settings_obj, "ANTHROPIC_API_KEY", None) or ""
    gk = getattr(settings_obj, "GEMINI_API_KEY", None) or ""
    if not ak and not gk:
        return "missing_key"
    if not ak or not gk:
        return "partial_key"
    return "ready"


# ---------------------------------------------------------------------------
# Report I/O — tolerant of missing / corrupt files (CONSTRAINT #6)
# ---------------------------------------------------------------------------


def load_audit_report(path: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """Read ``output/gravity_ai_audit.json`` and return the parsed dict.

    Returns ``None`` for ANY of: missing file, unreadable, corrupt JSON,
    non-object root, missing required keys.  Never raises.
    """
    try:
        if path is None:
            try:
                from settings import settings  # noqa: PLC0415

                path = settings.GRAVITY_AI_RUNNER_OUTPUT_PATH
            except Exception:
                path = "output/gravity_ai_audit.json"
        p = Path(path)
        if not p.exists():
            return None
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        if "steps" not in data or "summary" not in data:
            return None
        if not isinstance(data["steps"], list):
            return None
        return data
    except Exception as exc:
        logger.debug("gravity_ai_panel.load_audit_report failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _badge_for_status(status: Optional[str]) -> str:
    """Map an audit-step status string to a single-cell badge for the table."""
    if status is None:
        return "—"
    s = str(status).upper()
    if s == "PASSED":
        return "✅ PASSED"
    if s == "FAILED":
        return "❌ FAILED"
    return f"⚠ {s}"


def step_rows(report: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Turn a runner report into display rows for the Streamlit table.

    Returns ``[]`` when ``report`` is ``None`` or has no ``steps`` list.
    Each row carries: ``step_number``, ``step_title``, ``claude``,
    ``gemini``, ``disagreement`` (bool), ``score_claude`` / ``score_gemini``
    (int or NaN), ``notes`` (joined string).  Missing fields are ``"—"``
    or ``NaN`` — never fabricated (CONSTRAINT #4).
    """
    if not report:
        return []
    out: List[Dict[str, Any]] = []
    for entry in report.get("steps") or []:
        if not isinstance(entry, dict):
            continue
        cv = entry.get("claude_verdict")
        gv = entry.get("gemini_verdict")
        out.append({
            "step_number": entry.get("step_number"),
            "step_title": entry.get("step_title") or "—",
            "claude": _badge_for_status(cv.get("status") if isinstance(cv, dict) else None),
            "gemini": _badge_for_status(gv.get("status") if isinstance(gv, dict) else None),
            "disagreement": bool(entry.get("disagreement", False)),
            "score_claude": (cv.get("score") if isinstance(cv, dict) else None),
            "score_gemini": (gv.get("score") if isinstance(gv, dict) else None),
            "notes": " · ".join(entry.get("notes") or []),
        })
    return out


@dataclass(frozen=True)
class RunSummary:
    """Operator-facing KPI strip values.

    Every counter is an integer; ``generated_at`` is the literal ISO
    timestamp from the report (or ``"—"`` when absent).  ``health``
    is a one-token verdict for the colour band:

    * ``"clean"`` — every step has agreement AND no Claude FAILED.
    * ``"warn"`` — disagreements present OR runner reports partial coverage.
    * ``"fail"`` — at least one Claude FAILED verdict.
    * ``"empty"`` — no report yet.
    """

    generated_at: str
    enabled: bool
    total_steps: int
    claude_passed: int
    claude_failed: int
    claude_skipped: int
    gemini_passed: int
    gemini_failed: int
    gemini_skipped: int
    disagreements: int
    health: Literal["clean", "warn", "fail", "empty"]


def summarise_run(report: Optional[Dict[str, Any]]) -> RunSummary:
    """Build a :class:`RunSummary` from a loaded runner report.

    Returns an ``empty`` summary (zeros) when ``report`` is ``None`` —
    the caller renders a "no report yet" placeholder.
    """
    if not report:
        return RunSummary(
            generated_at="—",
            enabled=False,
            total_steps=0,
            claude_passed=0,
            claude_failed=0,
            claude_skipped=0,
            gemini_passed=0,
            gemini_failed=0,
            gemini_skipped=0,
            disagreements=0,
            health="empty",
        )
    s = report.get("summary") or {}
    c = s.get("claude") or {}
    g = s.get("gemini") or {}
    total = int(s.get("total_steps") or 0)
    disagreements = int(s.get("disagreements") or 0)
    claude_failed = int(c.get("failed") or 0)
    if claude_failed > 0:
        health: Literal["clean", "warn", "fail", "empty"] = "fail"
    elif disagreements > 0 or int(c.get("skipped") or 0) or int(g.get("skipped") or 0):
        health = "warn"
    else:
        health = "clean"
    return RunSummary(
        generated_at=str(report.get("generated_at") or "—"),
        enabled=bool(report.get("enabled", False)),
        total_steps=total,
        claude_passed=int(c.get("passed") or 0),
        claude_failed=claude_failed,
        claude_skipped=int(c.get("skipped") or 0),
        gemini_passed=int(g.get("passed") or 0),
        gemini_failed=int(g.get("failed") or 0),
        gemini_skipped=int(g.get("skipped") or 0),
        disagreements=disagreements,
        health=health,
    )


def health_caption(summary: RunSummary) -> str:
    """One-line operator-facing message for the colour band at the top."""
    if summary.health == "empty":
        return "No AI Gravity audit run yet."
    if summary.health == "fail":
        return (
            f"❌ Claude flagged {summary.claude_failed} step(s) as FAILED — "
            f"{summary.disagreements} disagreement(s) with Gemini."
        )
    if summary.health == "warn":
        return (
            f"⚠ {summary.disagreements} model disagreement(s); "
            f"Claude skipped={summary.claude_skipped} / "
            f"Gemini skipped={summary.gemini_skipped}."
        )
    return f"✅ Both models cleared {summary.total_steps} step(s) with full agreement."
