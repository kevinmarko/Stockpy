"""
llm/schemas.py — Pydantic v2 schemas for LLM structured output.
================================================================

Two schemas, one per provider role:

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
