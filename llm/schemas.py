"""
llm/schemas.py — Pydantic v2 schemas for LLM structured output.
================================================================

One schema per provider role:

* :class:`AnalystRationale` — Claude (analyst narrative).  Carries the four
  fields that a human reading a sell-side note would expect: a one-sentence
  thesis ("headline"), a 2-3 sentence "why now" catalyst paragraph, 1-3
  key-risk bullets, and a one-sentence "invalidation" condition that voids
  the thesis.  Maps cleanly onto the existing rationale verbose-mode
  sections without conflicting with the deterministic [A]/[B] blocks.

* :class:`AlertCommentary` — Gemini (push-notification body).  Bounded
  ≤280 chars so it fits ntfy's per-message limit and a typical Android
  notification preview.  ``urgency_hint`` is an advisory channel ONLY —
  the deterministic ``WatchAlert.priority`` / ``TradeAlert.priority`` is
  the single source of truth for ntfy priority and is never overridden by
  an LLM response (enforced at the call site).

* :class:`ChartPatternRead` — Gemini Vision (chart-pattern interpretation,
  Tier 9 Scope 3).

* :class:`ResearchBrief` — OpenAI/Opal (front-of-pipeline grounded research
  brief, Tier 9 Scope 4).  Qualitative-only by construction — no numeric
  field exists to fabricate a price target or score into.

Length bounds are validated via ``Field(max_length=...)`` so a runaway
provider response is rejected at the schema-validation step before it
reaches a notification or the cache.
"""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field


class AnalystRationale(BaseModel):
    """Claude analyst-grade narrative for a single advisory recommendation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    headline: str = Field(
        min_length=1,
        max_length=120,
        description="One-sentence thesis. e.g. 'Mean-reversion entry on a healthy uptrend.'",
    )
    why_now: str = Field(
        min_length=1,
        max_length=800,
        description=(
            "2-3 sentence paragraph naming the catalyst / timing rationale. "
            "References the deterministic numbers in the rec, never invents new ones."
        ),
    )
    key_risks: List[str] = Field(
        min_length=1,
        max_length=3,
        description=(
            "1-3 short bullets describing the main downside risks the operator "
            "should weigh against the thesis. Each ≤140 chars."
        ),
    )
    invalidation: str = Field(
        min_length=1,
        max_length=240,
        description=(
            "One sentence describing the condition that would void the thesis "
            "(e.g. 'A close below the 200-day SMA invalidates the trend setup.')."
        ),
    )


class AlertCommentary(BaseModel):
    """Gemini short-form body for an ntfy push notification.

    ``urgency_hint`` is advisory only — the deterministic alert ``priority``
    is the source of truth for ntfy dispatch.  Provided here so callers
    that want to log a model's perceived urgency vs. the rule-based
    priority can do so without overloading the message body.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    body: str = Field(
        min_length=1,
        max_length=280,
        description=(
            "Push-notification body, 1-2 sentences, ≤280 chars to fit ntfy + "
            "typical Android notification preview limits."
        ),
    )
    urgency_hint: Literal["low", "normal", "high"] = Field(
        default="normal",
        description=(
            "Advisory urgency. Does NOT override WatchAlert.priority / "
            "TradeAlert.priority — those remain the deterministic SoT."
        ),
    )


class ChartPatternRead(BaseModel):
    """Gemini Vision interpretation of a single price chart (Tier 9 Scope 3).

    The schema is bounded so a runaway provider response is rejected at
    validation (same CONSTRAINT #4 + #6 contract as the other Tier 9
    schemas).  All numeric fields are described *qualitatively* — the
    model never returns prices it would have invented; it can only refer
    to support / resistance levels visible in the chart it was given.

    Fields
    ------
    pattern_name :
        Short label for the dominant pattern (e.g. ``"ascending triangle"``,
        ``"head-and-shoulders"``, ``"sideways consolidation"``).  Free-form
        string capped at 60 chars so it fits a single dashboard cell.
    trend_direction :
        Ordinal verdict: ``"bullish" | "bearish" | "neutral"``.  Advisory
        only — does NOT feed back into the numeric pipeline.
    support_levels / resistance_levels :
        Lists of qualitative descriptions of levels visible in the chart
        (e.g. ``"recent low near $170"``, ``"prior breakout zone"``).
        Bounded to 3 each, each ≤120 chars.  Never numeric — see
        CONSTRAINT #4.
    narrative :
        2-3 sentence paragraph the GUI renders verbatim.  Capped at 800
        chars so the Streamlit ``st.markdown`` block never blows out the
        page width.
    confidence :
        Ordinal: ``"low" | "medium" | "high"``.  Advisory hint only.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    pattern_name: str = Field(
        min_length=1,
        max_length=60,
        description="Short label for the dominant pattern visible in the chart.",
    )
    trend_direction: Literal["bullish", "bearish", "neutral"] = Field(
        description="Ordinal trend read — advisory only, never feeds the pipeline.",
    )
    support_levels: List[str] = Field(
        default_factory=list,
        max_length=3,
        description="≤3 qualitative descriptions of support; each ≤120 chars.",
    )
    resistance_levels: List[str] = Field(
        default_factory=list,
        max_length=3,
        description="≤3 qualitative descriptions of resistance; each ≤120 chars.",
    )
    narrative: str = Field(
        min_length=1,
        max_length=800,
        description="2-3 sentence operator-facing chart interpretation.",
    )
    confidence: Literal["low", "medium", "high"] = Field(
        default="medium",
        description="Advisory confidence hint — does NOT override deterministic conviction.",
    )


class ResearchBrief(BaseModel):
    """OpenAI/Opal grounded research brief for a single symbol (Tier 9 Scope 4).

    A FRONT-OF-PIPELINE artifact: synthesized from REAL retrieved Finnhub
    news + earnings + a macro snippet (never invented — CONSTRAINT #4), then
    threaded INTO the Claude analyst-rationale prompt as enriched context.

    Qualitative-only by construction: every field resolves to ``str``,
    ``list[str]``, or a ``Literal`` — there is no numeric field to fabricate
    a price target or score into. This is enforced structurally (not just by
    convention) so Gravity step_77 can type-check `model_fields` rather than
    scan field names.

    Fields
    ------
    thesis_context :
        2-4 sentence synthesis of the symbol's current setup, grounded in
        the supplied news/earnings/macro packet. ≤600 chars.
    catalysts :
        1-4 short bullets naming specific upcoming or recent catalysts
        drawn from the grounding packet (e.g. "Q3 earnings call scheduled
        Nov 4"). Each ≤160 chars. Never a number the model invented.
    risk_factors :
        1-4 short bullets naming risks visible in the grounding packet.
        Each ≤160 chars.
    recent_developments :
        0-4 short bullets summarizing recent real news headlines supplied
        in the grounding packet. Each ≤200 chars. Empty when no news was
        retrieved — never fabricated to fill the list.
    data_confidence :
        Ordinal: ``"low" | "medium" | "high"`` — reflects how much real
        grounding data was available (e.g. "low" when Finnhub returned no
        news and no earnings date). Advisory only.
    sources_note :
        One-sentence attribution of what was actually retrieved (e.g.
        "Based on 3 Finnhub headlines from the past 7 days; no earnings
        date available."). ≤200 chars.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    thesis_context: str = Field(
        min_length=1,
        max_length=600,
        description="2-4 sentence synthesis grounded in the supplied news/earnings/macro packet.",
    )
    catalysts: List[str] = Field(
        min_length=1,
        max_length=4,
        description="1-4 short bullets naming specific catalysts from the grounding packet; each ≤160 chars.",
    )
    risk_factors: List[str] = Field(
        min_length=1,
        max_length=4,
        description="1-4 short bullets naming risks visible in the grounding packet; each ≤160 chars.",
    )
    recent_developments: List[str] = Field(
        default_factory=list,
        max_length=4,
        description="0-4 short bullets summarizing real retrieved headlines; each ≤200 chars.",
    )
    data_confidence: Literal["low", "medium", "high"] = Field(
        default="medium",
        description="How much real grounding data was available. Advisory only.",
    )
    sources_note: str = Field(
        min_length=1,
        max_length=200,
        description="One-sentence attribution of what was actually retrieved.",
    )


class GravityAuditStepResult(BaseModel):
    """One AI-rendered Gravity audit step verdict.

    Mirrors the JSON shape that the SYSTEM_PROMPT in
    ``ai_verification_prompts.py`` already forces on every step prompt
    (``{"status": "PASSED/FAILED", "score": 0-100, "findings": [],
    "missing_elements": []}``).  Using a strict schema means a Claude or
    Gemini response that drifts off-shape gets rejected at schema-validation
    time, the provider returns ``None``, and the runner records a soft
    failure for that step rather than fabricating a verdict
    (CONSTRAINT #4 + #6).

    Both Claude (primary auditor) and Gemini (cross-checker) return the
    same shape.  The runner records both verdicts side-by-side and flags
    a disagreement when their ``status`` fields differ — the operator
    sees the conflict explicitly instead of being asked to trust one
    model's verdict over the other.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["PASSED", "FAILED"] = Field(
        description="Verdict for this audit step. PASSED/FAILED only — no in-between.",
    )
    score: int = Field(
        ge=0,
        le=100,
        description="Integer score 0-100; 0 = total failure, 100 = perfect adherence.",
    )
    findings: List[str] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "Short bullets describing what the model observed in the code. "
            "Each ≤500 chars; max 20 bullets to keep the response bounded."
        ),
    )
    missing_elements: List[str] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "Short bullets naming criteria the code FAILED to meet. Each "
            "≤500 chars; an empty list is valid (PASSED with nothing missing)."
        ),
    )
