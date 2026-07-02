# Opal Build Spec — Tier 9 Scope 4: Research / Deep-Context Agent (OpenAI/GPT)

**Status:** Build spec (prompts + doc text). No code has been built yet — this document is the
executable work order for implementing Opal, plus the ready-to-apply `CLAUDE.md` / `GEMINI.md`
section text and the Gravity-suite update spec.

---

## 1. Context

The platform already has two operator-triggered, opt-in LLM agents (Tier 9, merged to `main`):

- **Claude** — per-symbol analyst rationale (`llm/commentary.py`)
- **Gemini** — alert text + chart-pattern vision (`llm/chart_insight.py`)

Both are **off by default**, **soft-fail** to the deterministic template, and **never touch
numeric pipeline scalars**.

**Opal** adds a **third agent, running on OpenAI/GPT** (a genuinely new provider — `openai` is
already a declared-but-unused dependency). Opal is a **front-of-pipeline research / deep-context
agent**: for a given symbol it produces a structured *research brief* (context, catalysts, risk
factors, recent developments, confidence) that is threaded **into** Claude's and Gemini's prompts
as enriched input.

### Grounding — the non-negotiable design decision (CONSTRAINT #4)

A pure-LLM "researcher" with no live data would hallucinate catalysts and news. Opal MUST
synthesize **real retrieved data**, not invent it. The platform already fetches real Finnhub
`company_news` + `earnings_calendar` in `signals/news_catalyst.py` (currently as the private
helpers `_fetch_company_news` / `_fetch_next_earnings`; `FinnhubProvider` in
`data/market_data.py`) and carries a macro DTO. STEP 3a below promotes those helpers to their
public names (`fetch_company_news` / `fetch_next_earnings`) so `llm/research.py` can consume
them without reaching into another module's private surface. Opal's research module reuses
these to assemble a grounded input packet, then asks GPT to **synthesize** it into the schema.
The `data_confidence` field + a strict "qualitative only, never invent numbers" system prompt
are the backstops. (Future option: OpenAI Responses API web-search tool for live grounding —
out of scope here.)

Opal stays **ADVISORY-ONLY** and **opt-in / default-off** — consistent with the standing rule
against automatic AI invocations (the feature is operator-triggered, not background/autonomous).

---

## 2. Architecture

```
symbol ─► Opal (OpenAI, grounded on real Finnhub news+earnings+macro)
              │  produces ResearchBrief (qualitative, schema-bounded)
              ▼
        context["research_brief"] ──► Claude analyst rationale  (llm/commentary.py)
                                 └──► Gemini chart/alert prompts (optional)
              ▼
        Recommendation.research_brief (new nullable dict field, GUI-surfaced)
```

### New files (mirror existing `llm/` patterns exactly)

| File | Change |
|---|---|
| `llm/schemas.py` | add `ResearchBrief` (qualitative-only, bounded, `extra=forbid`, frozen) |
| `llm/providers.py` | add `OpenAIProvider(LLMProvider)` — lazy `import openai`, Structured Outputs, soft-fail |
| `llm/research.py` (new) | `_gather_grounding()` (real Finnhub) + `generate_research_brief()` |
| `llm/router.py` | add `get_research_provider()` |
| `engine/opal_research.py` (new) | CLI `python -m engine.opal_research SYMBOL` |
| `tests/test_openai_provider.py` (new) | mocked SDK, soft-fail matrix |
| `tests/test_research_brief.py` (new) | schema bounds, cache, opt-in, grounding injection |
| `tests/test_opal_pipeline_integration.py` (new) | thread-in + no-fabrication invariant |
| `tests/test_gui_env_io_openai_key.py` (new) | `OPENAI_API_KEY` secret-only |
| `tests/test_opal_research_panel.py` (new) | GUI helper + wiring |

### Modified files

| File | Change |
|---|---|
| `engine/advisory.py` | add `Recommendation.research_brief`; generate + inject brief in `enrich_with_llm_rationale` |
| `llm/commentary.py` | `_format_rationale_user_prompt` appends a research-context block when present |
| `settings.py` | `OPAL_RESEARCH_ENABLED/PROVIDER/MODEL/TIMEOUT_SECONDS`, `OPENAI_API_KEY` |
| `gui/env_io.py` | `OPENAI_API_KEY`→`SECRET_KEYS`; three `OPAL_*` toggles→`ALLOWED_KEYS` |
| `requirements.txt` | bump `openai>=1.12.0` → `openai>=1.40.0` (Structured Outputs) |
| `gui/ai_insights_panel.py` + `gui/panels/__init__.py` | `format_research_brief_markdown` + `_render_opal_research_section` (top of AI Insights tab) |
| `CLAUDE.md` | new Scope-4 section (text in §7 below) |
| `GEMINI.md` | three-provider update (text in §7 below) |
| `gravity/__init__.py` | `step_77_opal_research_audit()` (~10 checks) + run-sequence wire |
| `ai_verification_prompts.py` | `STEP_8_PROMPT` + `ALL_PROMPTS` |
| `engine/gravity_ai_runner.py` | `_STEP_FILE_MAP[8]` |

### Invariants

- **CONSTRAINT #3** — `OPENAI_API_KEY` secret-only; `write_setting` raises `SecretWriteError`.
- **CONSTRAINT #4** — `ResearchBrief` qualitative-only; grounded on real Finnhub data; never
  writes `score`/`conviction`/`forecast`/`suggested_position_pct`.
- **CONSTRAINT #6** — every provider/research/enrich path try/except → `None`/unchanged-rec.
- **Opt-in / default-off** — `OPAL_RESEARCH_ENABLED=False`; zero `openai` import + zero network
  when off.
- **Lazy SDK reach** — no top-level `import openai` in `engine/advisory.py` or `llm/research.py`.

---

## 3. MASTER (BUILD) PROMPT

> Persona / master rules for the implementing agent.

```
You are a senior Python engineer implementing "Opal", a third advisory-only AI agent for the
InvestYo/Stockpy quant platform. Opal is a FRONT-OF-PIPELINE research agent running on OpenAI/GPT
that produces a grounded, qualitative research brief per symbol, which is threaded into the
existing Claude (analyst) and Gemini (alert/chart) prompts as enriched context.

NON-NEGOTIABLE RULES:
1. ADVISORY-ONLY. No order-submission code of any kind. No submit_order/place_order/etc.
2. OPT-IN, DEFAULT-OFF. Gate everything on settings.OPAL_RESEARCH_ENABLED (default False). When
   off: ZERO openai import, ZERO network calls, behavior byte-identical to today.
3. SOFT-FAIL EVERYWHERE (CONSTRAINT #6). Every provider/research/enrich path is wrapped in
   try/except and returns None (or the unchanged Recommendation). Nothing propagates.
4. NO FABRICATED METRICS (CONSTRAINT #4). The ResearchBrief is QUALITATIVE ONLY — no price
   targets, no scores, no percentages as data. It is SYNTHESIZED from REAL retrieved Finnhub
   news + earnings + the macro DTO — never invented. The model is explicitly forbidden from
   stating numbers not present in the grounding packet.
5. SECRETS (CONSTRAINT #3). OPENAI_API_KEY lives in gui/env_io.SECRET_KEYS ONLY, never
   ALLOWED_KEYS; a GUI write attempt raises SecretWriteError.
6. LAZY SDK REACH. `import openai` happens ONLY inside OpenAIProvider.__init__. No top-level
   openai import in engine/advisory.py or llm/research.py.
7. MIRROR EXISTING PATTERNS. Copy the shape of llm/providers.py (ClaudeProvider/GeminiProvider),
   llm/commentary.py, llm/chart_insight.py, llm/router.py, llm/cache.py, and the Tier 9 test
   files. Do not invent new conventions where an existing one fits.
8. TEST WITH MOCKED SDKs. Every test installs a fake `openai` module in sys.modules or injects a
   fake provider. ZERO real network calls in CI. Restore sys.modules in fixture teardown.
9. Keep the diff additive. Do not modify unrelated behavior. `context` in
   generate_analyst_rationale is a reserved-but-unused param — extend it, don't break callers.
Deliver working code + passing tests + a green Gravity step_77 before considering a step done.
```

---

## 4. STEP PROMPTS (build order)

```
STEP 1 — OpenAIProvider. In llm/providers.py add OpenAIProvider(LLMProvider): lazy
`import openai` in __init__; construct client as
`openai.OpenAI(api_key=..., timeout=timeout_seconds)` — timeout goes at CLIENT INIT
(mirrors `anthropic.Anthropic(api_key=..., timeout=...)` in ClaudeProvider — do NOT reach for
`signal.alarm`). call_structured(system,user,schema_model) uses the openai>=1.40 SDK helper
`client.beta.chat.completions.parse(model=..., messages=[{"role":"system","content":system},
{"role":"user","content":user}], response_format=schema_model)` and returns
`completion.choices[0].message.parsed` (already a validated pydantic instance; may be `None` on
a refusal — check `.message.refusal` / `.message.parsed`). Do NOT hand-roll
`response_format={"type":"json_schema","strict":True,"schema":model_json_schema()}` — OpenAI's
strict mode requires `additionalProperties:false` on every object AND every field in `required`
(Optional fields must be `type:[T,"null"]`, not omitted); pydantic v2's raw `model_json_schema()`
doesn't emit these, so a hand-rolled schema 400s at runtime. The `.parse()` helper does the
schema post-processing for you. Any exception / refusal / None `.parsed` → return None.
name="openai". Missing SDK → self._client=None → calls return None.

STEP 2 — ResearchBrief schema. In llm/schemas.py add ResearchBrief (ConfigDict extra=forbid,
frozen). Fields: thesis_context (≤600), catalysts (1-4, ≤160), risk_factors (1-4, ≤160),
recent_developments (0-4, ≤200), data_confidence Literal[low|medium|high], sources_note (≤200).
NO numeric fields.

STEP 3a — Promote Finnhub helpers to public API. In signals/news_catalyst.py rename
`_fetch_company_news` → `fetch_company_news` and `_fetch_next_earnings` → `fetch_next_earnings`
(update the two internal callers in the same file — `NewsCatalystSignal.pre_compute`). Behavior
unchanged; single-diff docstring/rename commit. This lets llm/research.py consume a public API
instead of reaching into another module's private surface.

STEP 3 — llm/research.py. _RESEARCH_SYSTEM_PROMPT (synthesize supplied grounding only; never
invent numbers; advisory note). _gather_grounding(symbol, context) reuses the newly-public
signals.news_catalyst.fetch_company_news / fetch_next_earnings (degrade to {} on any failure) +
macro snippet from context. generate_research_brief(symbol, context=None, *, provider=None,
grounding_fn=None): opt-in gate → cache_get → build user prompt from grounding → provider.
call_structured → cache_put → return; soft-fail → None. Reuse llm.cache + _registry_prompt
("llm.research.system"). Cache key MUST use `llm.cache.make_cache_key(provider="openai",
schema_name="ResearchBrief", symbol=symbol, score=0.0, action="RESEARCH", date_iso=None)` — the
`score` / `action` slots aren't semantic for a research brief, so we fix them to `0.0` /
`"RESEARCH"` (mirrors how llm/chart_insight.py pins non-scored artifacts) and let the UTC-date
bucket be the natural refresh boundary.

STEP 4 — Router. In llm/router.py add get_research_provider(): None unless OPAL_RESEARCH_ENABLED
and OPAL_RESEARCH_PROVIDER=="openai" and OPENAI_API_KEY set → OpenAIProvider(...).

STEP 5 — Thread-in. engine/advisory.py: add research_brief:Optional[Dict[str,Any]]=None to
Recommendation; in enrich_with_llm_rationale, when OPAL_RESEARCH_ENABLED, generate brief, inject
into context["research_brief"] BEFORE the Claude call, and replace(rec,
research_brief=brief.model_dump()). llm/commentary.py: _format_rationale_user_prompt appends a
"Research context" block when context["research_brief"] present. No top-level openai import.

STEP 6 — Config. settings.py: OPAL_RESEARCH_ENABLED(False), OPAL_RESEARCH_PROVIDER("openai"),
OPAL_RESEARCH_MODEL("gpt-4o"), OPAL_RESEARCH_TIMEOUT_SECONDS(15), OPENAI_API_KEY(None).
gui/env_io.py: OPENAI_API_KEY→SECRET_KEYS; the three OPAL_* toggles→ALLOWED_KEYS.
requirements.txt: openai>=1.40.0.

STEP 7 — CLI. engine/opal_research.py: `python -m engine.opal_research SYMBOL` builds context,
prints ResearchBrief fields or "Opal research unavailable", exit 0 on soft-fail.

STEP 8 — GUI. gui/ai_insights_panel.py: format_research_brief_markdown(payload) (None→sentinel,
partial-safe). gui/panels/__init__.py: _render_opal_research_section(symbol) at TOP of
render_ai_insights (front-of-pipeline); session-state mirror.

STEP 9 — Docs. CLAUDE.md new Scope-4 section + llm/ bullet update (text in §7). GEMINI.md
three-provider update + Opal bullet (text in §7).

STEP 10 — Gravity. gravity/__init__.py step_77_opal_research_audit() (~10 checks) + wire into run
sequence. ai_verification_prompts.py STEP_8_PROMPT + ALL_PROMPTS. engine/gravity_ai_runner.py
_STEP_FILE_MAP[8] = ("llm/research.py","llm/providers.py","engine/advisory.py").

STEP 11 — Tests (mock SDK, no network): test_openai_provider.py (happy/schema-miss/exception/
missing-SDK/timeout → None + fixture restores sys.modules), test_research_brief.py (schema
bounds; generate happy via injected provider; cache hit; soft-fail; empty symbol → None;
default-disabled → None; grounding_fn injected, no real Finnhub), test_opal_pipeline_integration.py
(brief threads into enrich context; _format_rationale_user_prompt includes it; numeric
Recommendation fields byte-identical after enrichment — CONSTRAINT #4; disabled → no OpenAI call),
test_gui_env_io_openai_key.py (write_setting OPENAI_API_KEY → SecretWriteError; toggles allowed),
test_opal_research_panel.py (format_research_brief_markdown None/full/partial; panel wiring grep).

STEP 12 — Verify + ship. Run all Opal + adjacent tests + Gravity step_74/75/76/77 green; smoke
`python -m engine.opal_research AAPL` with OPAL off (→ "unavailable", exit 0); grep confirms no
top-level openai import in engine/advisory.py; commit on a fresh branch off main; open PR.
```

---

## 5. GRAVITY AI-RUNNER AUDIT PROMPT (`ai_verification_prompts.STEP_8_PROMPT`)

```
STEP_8_PROMPT — "Opal Research Agent & Multi-Provider Grounding". Verify:
1. PROVIDER ABSTRACTION: OpenAIProvider implements the LLMProvider ABC, lazy-imports openai, and
   soft-fails to None on every error (network/parse/schema/missing-SDK).
2. GROUNDING (no hallucinated data): generate_research_brief synthesizes REAL retrieved Finnhub
   news/earnings (via signals.news_catalyst helpers), never invents catalysts/numbers; the
   ResearchBrief schema exposes NO numeric price/score fields (CONSTRAINT #4).
3. OPT-IN: brief generation is gated on OPAL_RESEARCH_ENABLED (default False) — off ⇒ no openai
   import, no network.
4. THREADING: the brief flows into context["research_brief"] and into the Claude rationale
   user-prompt; it never writes a numeric Recommendation field.
5. SECRETS: OPENAI_API_KEY is SECRET_KEYS-only (CONSTRAINT #3).
6. ADVISORY-ONLY: no order-submission verbs in llm/research.py.
Respond in JSON: {"status":"PASSED/FAILED","score":0-100,"findings":[],"missing_elements":[]}
```

---

## 6. Gravity `step_77_opal_research_audit` — check list (~10)

Mirrors `step_74/75/76`. Each check appends `{check, passed[, detail]}`; wire
`self.step_77_opal_research_audit()` into the run sequence after `step_76`.

1. Module surface: `llm.research.generate_research_brief`, `ResearchBrief`, `OpenAIProvider` importable.
2. `OpenAIProvider.call_structured` is callable.
3. `ResearchBrief` rejects >4 catalysts AND a bad `data_confidence` value.
4. `ResearchBrief` exposes NO numeric field — type-based check: iterate
   `ResearchBrief.model_fields.items()` and assert every annotation resolves to `str`,
   `list[str]`, or `Literal[...]`. Reject anything that resolves to `int`, `float`, `Decimal`,
   or a container of those (CONSTRAINT #4 — stronger than a field-name scan, which would miss a
   `catalysts: list[str]` field whose content happens to include "$5B buyback").
5. No top-level `openai` import in `llm/research.py` OR `engine/advisory.py` (lazy only).
6. No order-submission verbs in `llm/research.py` (advisory-only).
7. Opt-in: `generate_research_brief("X")` returns `None` when `OPAL_RESEARCH_ENABLED=False`.
8. Threading: `_format_rationale_user_prompt` references `research_brief` (source grep).
9. `OPENAI_API_KEY` in `gui/env_io.SECRET_KEYS` and NOT in `ALLOWED_KEYS`.
10. All five Opal test files exist.

---

## 7. Ready-to-apply documentation text

### 7a. `CLAUDE.md` — new section

**Insertion point:** immediately after the `## Tier 9 Scope 3 — AI Insights tab (Gemini Vision, 2026-06)`
section, BEFORE the `## AI Control Center tab (gui/ai_control_center.py, 2026-07)` section.
Rationale: keeps all Tier-9 LLM-provider scopes (1/2/3/4) contiguous so an operator scanning
CLAUDE.md sees the three-provider layer in one continuous block, with the Control Center /
Prompt Registry sections following separately.

```markdown
## Tier 9 Scope 4 — Opal Research Agent (`llm/research.py`, OpenAI/GPT)

### Overview
A third advisory-only AI agent — **Opal** — running on **OpenAI/GPT** (new `OpenAIProvider`).
Opal is a FRONT-OF-PIPELINE research/deep-context agent: for a symbol it produces a structured,
qualitative `ResearchBrief` (thesis_context, catalysts, risk_factors, recent_developments,
data_confidence, sources_note) that is threaded INTO the Claude analyst and Gemini prompts as
enriched context. Grounded on REAL retrieved Finnhub `company_news` + `earnings_calendar` (reuses
`signals/news_catalyst.py` helpers) — never invents catalysts or numbers (CONSTRAINT #4).

### Surface
- `llm/schemas.py::ResearchBrief` — qualitative-only, bounded fields, NO numeric price/score.
- `llm/providers.py::OpenAIProvider` — lazy `import openai`, Structured Outputs
  (`response_format` json_schema, `strict=True`), soft-fail to `None`.
- `llm/research.py::generate_research_brief(symbol, context, *, provider=None, grounding_fn=None)`
  — `_gather_grounding()` pulls real Finnhub news/earnings, cache via `llm/cache.py`, opt-in gate,
  soft-fail. `_registry_prompt("llm.research.system", ...)` override.
- `llm/router.py::get_research_provider()` — gated on `OPAL_RESEARCH_ENABLED` + `OPAL_RESEARCH_PROVIDER`
  + `OPENAI_API_KEY`.
- `engine/advisory.py` — `Recommendation.research_brief: Optional[Dict[str,Any]] = None`;
  `enrich_with_llm_rationale` generates the brief and injects `context["research_brief"]` before
  the Claude call. `llm/commentary.py::_format_rationale_user_prompt` appends a research-context
  block when present. No top-level `openai` import.
- `engine/opal_research.py` — CLI `python -m engine.opal_research SYMBOL`.
- GUI: `gui/ai_insights_panel.format_research_brief_markdown` +
  `gui/panels._render_opal_research_section` at the top of the AI Insights tab.

### Settings / env vars
- `OPAL_RESEARCH_ENABLED: bool = False` — dedicated master switch (independent of
  `LLM_COMMENTARY_ENABLED`).
- `OPAL_RESEARCH_PROVIDER: str = "openai"` (`"openai"|"none"`).
- `OPAL_RESEARCH_MODEL: str = "gpt-4o"`.
- `OPAL_RESEARCH_TIMEOUT_SECONDS: int = 15`.
- `OPENAI_API_KEY: Optional[str] = None` — **`gui/env_io.SECRET_KEYS` ONLY, never GUI-writable
  (CONSTRAINT #3).**
- The three `OPAL_RESEARCH_*` toggles are in `gui/env_io.ALLOWED_KEYS`.

### Gravity step 77 (`step_77_opal_research_audit`)
10 checks: module surface; `OpenAIProvider.call_structured` callable; `ResearchBrief` bounds +
NO numeric fields (CONSTRAINT #4); no top-level `openai` import (lazy); no order verbs; opt-in
default-off; brief threads into the rationale prompt; `OPENAI_API_KEY` secret-only; test files
exist. Runner audit prompt: `ai_verification_prompts.STEP_8_PROMPT`; `_STEP_FILE_MAP[8]` covers
`llm/research.py` + `llm/providers.py` + `engine/advisory.py`.

### Critical invariants (must never regress)
- No fabricated metrics — brief is qualitative, grounded on real Finnhub data; never sets a
  numeric `Recommendation` field.
- Dead-letter resilience — every path soft-fails to `None`/unchanged-rec.
- Opt-in default-off — zero `openai` import + zero network when `OPAL_RESEARCH_ENABLED=False`.
- No GUI-writable secret — `OPENAI_API_KEY` in `SECRET_KEYS` only.
- No top-level LLM SDK reach in `engine/advisory.py` or `llm/research.py`.

### AI Control Center auto-activation
The AI Control Center tab (`gui/ai_control_center.py`, added in the tab-14 build) already
carries an `opal_built()` helper that soft-imports `llm.research`. Once STEP 3 lands, the
Control Center's Opal row auto-lights-up (status transitions from `not_built` → `disabled` /
`missing_key` / `ready` per the standard 4-state classifier). **No Control Center change is
needed** as part of this build — do not duplicate the wiring there.
```

### 7b. `GEMINI.md` — replace the "openai/anthropic" line

Locate via `grep -n "openai/anthropic" GEMINI.md` (line number drifts as GEMINI.md grows —
don't cite a fixed line). Current text: *"Uses openai/anthropic for LLM agent integration."*
Replace with:

```markdown
Uses a three-provider advisory LLM layer (all opt-in, default-off, soft-fail): Anthropic Claude
(analyst rationale), Google Gemini (alert text + chart-pattern vision), and OpenAI/GPT
("Opal" — front-of-pipeline research/deep-context agent grounded on real Finnhub news+earnings).
All are ADVISORY-ONLY and never write numeric pipeline scalars (CONSTRAINT #4).
```

Add an Opal bullet under the standing-rules/architecture section:

```markdown
- **Opal (OpenAI research agent):** produces a qualitative, grounded ResearchBrief per symbol
  that feeds INTO Claude/Gemini. Opt-in via OPAL_RESEARCH_ENABLED (default off). OPENAI_API_KEY
  is secret-only. No order code; no fabricated numbers.
```

---

## 8. Verification (during execution)

1. `pytest` the 5 new Opal test files + adjacent (`test_advisory_llm_enrichment`,
   `test_llm_providers`, `test_ai_insights_panel`) → green, mocked SDKs only.
2. Gravity `step_74/75/76/77/78` all PASS (run the auditor methods in isolation — step_78 is the
   AI Control Center audit; verify Opal row transitions from `not_built` → its live status).
3. Opt-in smoke: `OPAL_RESEARCH_ENABLED=false python -m engine.opal_research AAPL` → prints the
   "Opal research unavailable" sentinel, exit 0; no `openai` import occurs.
4. Grounding smoke (keys present, off-CI): `_gather_grounding` returns real Finnhub headlines;
   `sources_note` cites them; `data_confidence` downgrades to "low" when grounding is empty.
5. `grep -n "^import openai\|^from openai" engine/advisory.py llm/research.py` → empty (lazy only).
6. `Recommendation` numeric fields byte-identical before/after enrichment when Opal is on.
7. `CLAUDE.md` Scope-4 section + `GEMINI.md` three-provider update present and accurate.
8. Structured-output smoke (keys present, off-CI): a real call to `OpenAIProvider.call_structured`
   with a trivial `ResearchBrief` prompt returns a validated pydantic instance (NOT `None`) —
   confirms the `.beta.chat.completions.parse()` path is wired correctly and the schema is
   accepted by OpenAI's strict-mode post-processing.
9. `Recommendation.research_brief` respects the additive contract — every existing
   `engine.advisory.evaluate` caller in the repo (`main.py`, `main_orchestrator.py`, the CLI, the
   GUI drill-down) still constructs `Recommendation` positionally without touching the new field.

## 9. Open decision for execution time (not blocking)

**Master switch independence.** This spec uses a dedicated `OPAL_RESEARCH_ENABLED` (separate from
`LLM_COMMENTARY_ENABLED`) so research can run without commentary. If a single global LLM switch is
preferred, collapse to `LLM_COMMENTARY_ENABLED` — trivial to change.
