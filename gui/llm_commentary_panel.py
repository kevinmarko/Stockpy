"""
gui/llm_commentary_panel.py — helpers for the Reports-tab commentary button.
=============================================================================

The Streamlit-facing wiring lives in :func:`gui.panels.render_report_viewer`.
This module hosts the pure helpers it depends on so they remain unit-testable
WITHOUT installing or stubbing Streamlit.  Mirrors the pattern used by
:mod:`gui.circuit_breakers`, :mod:`gui.observability_telemetry`, and
:mod:`gui.market_data_diagnostics`.

What lives here
---------------
* :func:`commentary_state_key` — session-state key derivation that mirrors the
  on-disk cache bucket (provider + symbol + UTC date + score bucket + action).
  Two clicks of the same button on the same UTC day re-use the cached payload.
* :func:`commentary_status` — three-state classifier the Streamlit panel uses
  to decide whether to render a button, a "key missing" hint, or a hard-disabled
  notice.
* :func:`format_rationale_markdown` — turns a validated ``AnalystRationale``
  dump (or ``None``) into the Markdown shown under the button.
* :func:`generate_for_symbol_row` — orchestrates the click:
  ``Recommendation`` reconstruction → ``enrich_with_llm_rationale`` → dict
  payload.  Returns ``None`` on any failure — the panel falls back to the
  "commentary unavailable" message.

Design constraints
------------------
* Streamlit is NOT imported at module level so headless tests can exercise
  every helper.
* Nothing here calls a provider directly — the heavy lifting goes through
  :func:`engine.advisory.enrich_with_llm_rationale`, which already wraps the
  provider in try/except (CONSTRAINT #6).
* No fabricated metrics (CONSTRAINT #4) — the helper passes the deterministic
  ``Recommendation`` straight through to the enricher and renders whatever the
  enricher returns, never inventing a fallback narrative.
"""

from __future__ import annotations

import hashlib
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Mapping, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Type definitions
# ---------------------------------------------------------------------------

CommentaryStatus = Literal[
    "disabled",       # LLM_COMMENTARY_ENABLED is False — button is hidden / hard-disabled
    "missing_key",    # master switch on but ANTHROPIC_API_KEY unset — button shown w/ warning
    "ready",          # master switch on AND key set — button shown enabled
]


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def commentary_status(settings_obj: Any) -> CommentaryStatus:
    """Decide what the panel should render for the given settings object.

    ``settings_obj`` is duck-typed on two attributes:
    :attr:`LLM_COMMENTARY_ENABLED` and :attr:`ANTHROPIC_API_KEY`.  Both default
    to "missing" via ``getattr`` so the helper works with stub objects in tests.

    Returns one of ``"disabled"``, ``"missing_key"``, or ``"ready"``.
    Pure function — no side effects.
    """
    if not getattr(settings_obj, "LLM_COMMENTARY_ENABLED", False):
        return "disabled"
    key = getattr(settings_obj, "ANTHROPIC_API_KEY", None)
    if not key:
        return "missing_key"
    return "ready"


def commentary_state_key(
    *,
    symbol: str,
    score: float,
    action: str,
    date_iso: Optional[str] = None,
    provider: str = "claude",
) -> str:
    """Return a stable session-state key for the rendered commentary payload.

    Mirrors the on-disk cache key derivation in :mod:`llm.cache` so the GUI
    cache and the disk cache agree on bucketing: same UTC day + same score
    bucket (``floor(score / 5)``) + same action → same key.  Two clicks of
    the button on the same trading day are free; a meaningful score move
    (47 → 52, crossing a 5-point bucket) invalidates and re-fetches.
    """
    try:
        bucket = int(math.floor(float(score) / 5.0))
    except Exception:
        bucket = 0
    parts = "|".join([
        "llm_commentary",
        (provider or "claude").lower(),
        (symbol or "").upper(),
        date_iso or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        str(bucket),
        (action or "").upper(),
    ])
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()[:24]


def format_rationale_markdown(rationale: Optional[Mapping[str, Any]]) -> str:
    """Render an ``AnalystRationale.model_dump()`` payload as Markdown.

    Returns a sentinel string when the payload is ``None`` or empty so the
    caller never has to special-case that path (CONSTRAINT #6 — caller still
    sees something, never an exception or a blank box).
    """
    if not rationale:
        return (
            "_LLM commentary unavailable._ The provider returned no payload "
            "(soft-fail). The deterministic rationale above is the source of truth."
        )

    headline = str(rationale.get("headline", "") or "").strip()
    why_now = str(rationale.get("why_now", "") or "").strip()
    invalidation = str(rationale.get("invalidation", "") or "").strip()
    risks: List[str] = []
    for r in rationale.get("key_risks") or []:
        try:
            text = str(r).strip()
        except Exception:
            continue
        if text:
            risks.append(text)

    parts: List[str] = []
    if headline:
        parts.append(f"### {headline}")
    if why_now:
        parts.append(f"**Why now.** {why_now}")
    if risks:
        risk_md = "\n".join(f"- {r}" for r in risks)
        parts.append(f"**Key risks:**\n{risk_md}")
    if invalidation:
        parts.append(f"**Invalidation.** {invalidation}")

    return "\n\n".join(parts) if parts else format_rationale_markdown(None)


def signal_row_to_rec_skeleton(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Turn a single ``signals`` row from ``state_snapshot.json`` into the
    minimum-viable dict that :func:`generate_for_symbol_row` consumes.

    The state-snapshot schema varies by entry point — older rows use spaces
    (``"Action Signal"``) and newer ones use underscores (``"advisory_action"``).
    This helper picks whichever variant is present and never fabricates a
    field that's missing (CONSTRAINT #4 — missing → ``None``).
    """
    def _pick(*keys: str, default: Any = None) -> Any:
        for k in keys:
            if k in row and row[k] is not None:
                return row[k]
        return default

    return {
        "symbol": (str(_pick("symbol", "Symbol", default="")) or "").upper(),
        "action": (str(_pick("action", "advisory_action", "Action Signal", default="HOLD")) or "HOLD").upper(),
        "strategy": str(_pick("strategy", "Strategy", default="multi-signal composite")),
        "conviction": _pick("advisory_conviction", "conviction", "Conviction", default=0.0),
        "rationale": str(_pick("advisory_rationale", "rationale", "Rationale", default="")),
        "suggested_position_pct": _pick("kelly_target", "Kelly Target", default=0.0),
        "forecast": _pick("forecast_30", "Forecast_30", "forecast", default=None),
        "key_indicators": {
            "score": _pick("score", "Score", default=0.0),
        },
        "data_quality": str(_pick("data_quality", "Data Quality", default="OK")),
    }


def generate_for_symbol_row(
    row: Mapping[str, Any],
    *,
    enricher: Optional[Any] = None,
    research_brief: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Run the on-demand enrichment for a single signal row.

    Parameters
    ----------
    row :
        The signal row (a dict produced by ``signal_row_to_rec_skeleton`` or
        a raw row from ``state_snapshot.json``).
    enricher :
        Optional override (test seam).  Defaults to
        :func:`engine.advisory.enrich_with_llm_rationale`.
    research_brief :
        Optional already-generated Opal research brief
        (``ResearchBrief.model_dump()``) to thread into Claude's prompt for
        free — no new OpenAI call.  Tier 9 Scope 4 reuse path: the GUI's
        dedicated "Opal research brief" button caches its payload in session
        state; the "Claude analyst note" button forwards that cached payload
        here so Claude's synthesis benefits WITHOUT the Claude button ever
        triggering a fresh Opal call (``run_opal`` stays ``False``).

    Returns
    -------
    Optional[Dict[str, Any]]
        The ``rec.llm_rationale`` payload on success, ``None`` on any failure.
        Soft-fail contract — the caller renders the "unavailable" message
        without re-trying.  CONSTRAINT #6.
    """
    try:
        skeleton = signal_row_to_rec_skeleton(row)
        if not skeleton["symbol"]:
            return None

        # Thread a caller-supplied Opal brief into the enrichment context so
        # Claude can cite it — never triggers a new Opal call (run_opal=False).
        _context: Optional[Dict[str, Any]] = None
        if isinstance(research_brief, Mapping) and research_brief:
            _context = {"research_brief": dict(research_brief)}

        if enricher is None:
            # Lazy import keeps this module Streamlit-free AND keeps the
            # advisory pipeline's lazy SDK reach intact.
            from engine.advisory import Recommendation, enrich_with_llm_rationale  # noqa: PLC0415

            rec = Recommendation(
                symbol=skeleton["symbol"],
                action=skeleton["action"] if skeleton["action"] in ("BUY", "SELL", "HOLD") else "HOLD",
                strategy=skeleton["strategy"],
                conviction=float(skeleton["conviction"] or 0.0),
                rationale=skeleton["rationale"],
                suggested_position_pct=float(skeleton["suggested_position_pct"] or 0.0),
                forecast=skeleton["forecast"],
                key_indicators={k: float(v or 0.0) for k, v in skeleton["key_indicators"].items()},
                data_quality=skeleton["data_quality"] if skeleton["data_quality"] in ("OK", "STALE", "PARTIAL") else "OK",
            )
            enriched = enrich_with_llm_rationale(rec, _context)
        else:
            # Test seam — enricher receives the skeleton dict directly.
            enriched = enricher(skeleton)

        # enrich_with_llm_rationale returns a Recommendation; the dict shape
        # from a test enricher returns whatever the test wants — normalise.
        if enriched is None:
            return None
        if isinstance(enriched, dict):
            return enriched.get("llm_rationale")
        return getattr(enriched, "llm_rationale", None)
    except Exception as exc:
        logger.debug("generate_for_symbol_row soft-failed: %s", exc)
        return None
