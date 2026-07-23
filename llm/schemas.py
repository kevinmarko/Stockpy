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

Length bounds are validated at the schema-validation step so a runaway
provider response is rejected before it reaches a notification or the
cache.  For plain ``str`` fields this is ``Field(max_length=...)`` (a
character cap); for ``List[str]`` fields the ``Field(min_length/max_length)``
caps the NUMBER OF ITEMS, and each item's character length is capped by an
inner ``Annotated[str, StringConstraints(max_length=...)]`` element type
(a bare ``max_length`` on a ``List[str]`` would only bound the list length,
never the per-string length).
"""

from __future__ import annotations

from typing import Annotated, List, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Per-item length-bounded string aliases for the ``List[str]`` schema fields.
# A bare ``Field(max_length=N)`` on a ``List[str]`` bounds the list length,
# NOT the length of each string element — the inner ``StringConstraints`` is
# what actually caps each bullet's character count.
_Catalyst = Annotated[str, StringConstraints(max_length=160)]
_RiskFactor = Annotated[str, StringConstraints(max_length=160)]
_Development = Annotated[str, StringConstraints(max_length=200)]


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
        0-4 short bullets naming specific upcoming or recent catalysts
        drawn from the grounding packet (e.g. "Q3 earnings call scheduled
        Nov 4"). Each ≤160 chars. May be empty when the grounding packet
        yields none — never fabricated to fill the list. Never a number the
        model invented.
    risk_factors :
        0-4 short bullets naming risks visible in the grounding packet.
        Each ≤160 chars. May be empty when none are visible in the packet
        — never fabricated.
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
    catalysts: List[_Catalyst] = Field(
        default_factory=list,
        max_length=4,
        description="0-4 short bullets naming specific catalysts from the grounding packet; each ≤160 chars. May be empty.",
    )
    risk_factors: List[_RiskFactor] = Field(
        default_factory=list,
        max_length=4,
        description="0-4 short bullets naming risks visible in the grounding packet; each ≤160 chars. May be empty.",
    )
    recent_developments: List[_Development] = Field(
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


class SentimentDocumentVerification(BaseModel):
    """LLM verification verdict for a single borderline-credibility sentiment
    document (Sentiment Pipeline Phase 2 PR2, AI-Assisted Credibility
    Filtering -- :mod:`signals.credibility`).

    Provider-agnostic by design (any of Claude/Gemini/OpenAI may serve this
    job per ``settings.SENTIMENT_LLM_VERIFICATION_PROVIDER`` -- same
    flexible-routing shape as :class:`GravityAuditStepResult`). The prompt
    the caller builds includes ONLY the document's own ``source_name``,
    ``symbol``, and ``text_content`` -- never anything computed from data
    after the document's own ``as_of`` timestamp, preserving point-in-time
    safety.

    Fields
    ------
    verifiable :
        Whether the document reads as genuine, plausible commentary rather
        than spam, bot-generated filler, or obviously fabricated/manipulative
        text. Advisory input to ``S_verification`` only -- never itself a
        trading signal.
    confidence :
        [0, 1] confidence in the ``verifiable`` verdict. Mapped to
        ``S_verification`` as ``confidence`` when ``verifiable`` is True, or
        ``1 - confidence`` when False -- so a low-confidence call in either
        direction lands near the neutral middle of the score range rather
        than at an extreme.
    rationale :
        One-sentence justification, capped short so a runaway response is
        rejected at schema validation (CONSTRAINT #4 + #6) rather than
        reaching the cache or the audit table.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    verifiable: bool = Field(
        description=(
            "True if the document reads as genuine, plausible commentary; "
            "False if it reads as spam, bot-generated, or fabricated."
        ),
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in the verifiable/not-verifiable verdict, 0=none, 1=full.",
    )
    rationale: str = Field(
        min_length=1,
        max_length=300,
        description="One-sentence justification for the verdict.",
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
