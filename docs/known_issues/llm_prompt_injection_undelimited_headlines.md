# LLM prompts interpolated externally-sourced headlines with no delimiter between untrusted data and instructions

**Status: Fixed and verified.** Found during a secondary audit pass (2026-08-24)
covering `llm/commentary.py`, `llm/chart_insight.py`, `llm/research.py` — an area
never previously audited in this codebase's ongoing multi-pass audit series (prior
passes covered `sizing/`, `execution/`, `pilots/options_*`, data stores).

## How this was found

Read all three modules in full plus their callers, then statically reproduced
(offline, no live LLM API call) the exact prompt strings the real production
functions build from synthetic malicious inputs, to see what a model would actually
receive.

## Overall conclusion — bounded, not a live trading-integrity risk

This is a real, worth-fixing prompt-injection gap, but it is NOT a path to
manipulated trading decisions:

- `ResearchBrief`/`AnalystRationale`/`ChartPatternRead`/`AlertCommentary` (the four
  schemas these modules produce) have **zero numeric or action fields** — no
  `conviction`, `price_target`, `action`, or `score` anywhere. The worst a
  successful injection could do is manipulate bounded-length free-text prose fields
  (max 600/160/200 chars).
- Every LLM-generated field was traced end to end and confirmed to be display-only:
  `sizing/`, `signals/`, `execution/` were grepped for any reference to
  `llm_rationale`/`research_brief`/the four schema class names — zero hits. No LLM
  output reaches sizing, signal scoring, or order execution at any point.
- The deterministic recommendation (action, conviction, position size) is always
  computed BEFORE and independently of any LLM call, and is rendered alongside the
  LLM prose, not replaced by it.

The real risk is social engineering a human operator: a manipulated "why now"/thesis
narrative shown next to a real BUY/HOLD/SELL action, sourced from a headline an
attacker planted or a compromised news feed served, is a trust-erosion risk worth
closing even without a direct trading-integrity path.

## Root cause

`llm/research.py::_format_grounding_user_prompt` interpolated real, externally-
retrieved news headlines (FMP/Finnhub-sourced — genuinely external, not
operator-authored text) directly into the user-turn prompt with a bare f-string,
with no delimiter separating "data" from "instruction" within that turn:

```python
for headline in headlines:
    lines.append(f"  - {headline}")
```

Reproduced statically: a headline containing an embedded instruction-shaped payload
landed verbatim, mid-stream, in the same block the model was told to "synthesize the
structured research brief from ONLY the above." System/user role separation was
already correctly done at the SDK level in all three providers (Claude's `system=`,
Gemini's `system_instruction=`, OpenAI's `messages[0].role="system"`) — this was not
a system-vs-user boundary bypass, the injection surface was entirely *within* the
undifferentiated user-turn data.

A related, lower-severity instance: `llm/commentary.py::_format_rationale_user_prompt`
interpolates `context["research_brief"]` (Opal's own research output, itself
synthesized from the same untrusted headline text one call earlier) into Claude's
own analyst-rationale prompt using Python's `repr()` — which incidentally neutralizes
embedded newlines/quote-breaking but was never an intentional security boundary.

## Fix

**`llm/research.py`**: every headline is now wrapped in explicit `<headline>...
</headline>` tags, paired with a new system-prompt clause explicitly instructing the
model to treat text inside those tags as raw, untrusted external data that may
contain wording crafted to look like an instruction, and to never follow, obey, or
execute anything found inside them. A new `_sanitize_untrusted_text` helper
neutralizes literal `<`/`>` characters (replaced with visually-similar guillemets,
`‹`/`›`) and collapses embedded newlines/carriage returns to spaces before wrapping
— cheap, deterministic defense-in-depth against a headline forging its own closing
delimiter (e.g. an embedded literal `</headline>` string) or faking a new prompt
line, though the system-prompt instruction (not the escaping) is the primary
safeguard.

**`llm/commentary.py`**: the "Research context" block (which cites Opal's output,
itself derived from the same class of untrusted text) is now similarly fenced in
`<research_context>...</research_context>` tags, with a matching system-prompt
clause explaining that this block was synthesized by another model from externally-
sourced news and should be treated with the same caution as raw external data.

**`llm/chart_insight.py`**: an unrelated, minor cleanup found in the same pass — a
leftover `import traceback; traceback.print_exc()` debug statement in the chart-
render exception handler (dumping a full stack trace to stderr on every chart-render
failure, e.g. a bad/empty bars DataFrame on a data-outage day) was replaced with the
existing `logger.warning(..., exc_info=True)` convention already used everywhere
else in these three modules.

## Verification

- `tests/test_research_brief.py`: 39 passed (34 pre-existing + 5 new — headlines
  fenced in `<headline>` tags, the system prompt instructs the model to never follow
  instructions inside them, a headline containing a literal `</headline>` cannot
  forge its own closing delimiter, and `_sanitize_untrusted_text`'s two neutralization
  behaviors are unit-tested directly).
- `tests/test_opal_pipeline_integration.py`: 11 passed (10 pre-existing + 1 new —
  the research-context block is fenced in `<research_context>` tags in the real
  rationale prompt, with the cited content genuinely inside the fence).
- `tests/test_llm_commentary.py`, `tests/test_advisory_llm_enrichment.py`,
  `tests/test_alert_dispatch_llm.py`: 49 passed, unaffected.
- `tests/test_chart_insight.py`: 19 passed, unaffected by the debug-statement
  cleanup.

## What this does NOT fix / disclosed scope

- **No technical validation that LLM-asserted numeric/factual claims match real
  data.** All four schemas' free-text fields are bounded only by length/shape —
  nothing cross-checks a number appearing in generated prose (e.g. "RSI is 72")
  against the real `key_indicators`/`rec_skeleton` values actually passed in. This
  is a pre-existing, disclosed design tradeoff (post-hoc fact-checking of free text
  is genuinely hard), not something this pass attempted — CONSTRAINT #4 compliance
  for these free-text fields currently rests on prompt-following, not structural
  enforcement.
- The `symbol` path parameter on `GET /data/ai/research/{symbol}` is not validated
  against a ticker-shaped pattern before use — low severity (requires an already-
  authenticated caller, not an untrusted-external-content vector), noted but not
  fixed in this pass.
- Delimiter-fencing is defense-in-depth for an LLM that is instruction-following in
  good faith; it is not a hard technical guarantee against a sufficiently capable
  adversarial model choosing to ignore the system prompt's instruction. The schema's
  narrow, action-free field set remains the real backstop.
