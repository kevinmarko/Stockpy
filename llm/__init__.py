"""
llm/ — Claude + Gemini commentary integration (Tier 9, advisory-only).
======================================================================

Two specialised commentary agents layered on top of the existing template
narrative pipeline:

* **Claude** generates analyst-grade per-symbol "why" prose (Recommendation
  rationale, surfaced via the CLI and the `Recommendation.llm_rationale` dict).
* **Gemini** generates concise alert-text bodies for ntfy push notifications
  (WatchAlert / TradeAlert dispatch sites).

Design invariants (audited by Gravity step_74):

1. **Lazy SDK import.**  No top-level `import anthropic` / `import google.genai`
   in this package's `__init__.py`.  SDKs are imported only inside provider
   `__init__` methods, gated by `settings.LLM_COMMENTARY_ENABLED=True` and a
   present API key.  When the master switch is off, ZERO network calls fire
   and ZERO SDKs are imported.
2. **Soft-fail (CONSTRAINT #6).**  Every provider call returns `Optional[Model]`;
   any exception → `None`; the caller falls back to the deterministic template.
3. **Schema-bounded structured output.**  Both providers force the response
   through a pydantic schema (`AnalystRationale` / `AlertCommentary`).  A
   schema-mismatched response is treated as a soft failure.
4. **Advisory-only (CONSTRAINT enforced in Python, not in prompt bodies).**
   LLM output flows only into the `rationale` string, the `llm_rationale`
   dict field, and ntfy `message` bodies — never into `score`, `conviction`,
   `suggested_position_pct`, `forecast`, `key_indicators`, or any
   `state_snapshot.json` numeric field.
5. **No fabricated metrics (CONSTRAINT #4).**  Numbers stay numeric-engine-
   derived.  Models can only describe; they cannot supply pipeline scalars.
6. **Operator opt-in.**  `settings.LLM_COMMENTARY_ENABLED` defaults to `False`.

Public API (the only re-exports — keep this surface intentionally small):
* :func:`generate_analyst_rationale(rec_skeleton, context)` — Claude entry
* :func:`generate_alert_commentary(alert_skeleton, context)` — Gemini entry
"""

from __future__ import annotations

from llm.commentary import generate_alert_commentary, generate_analyst_rationale

__all__ = [
    "generate_analyst_rationale",
    "generate_alert_commentary",
]
