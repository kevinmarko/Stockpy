# Walkthrough — revert scope-creep default=True flips on 7 flags

## What changed

Commit `30d136ae8` ("Implement Feature Flags UI and update default-on admin
gates", 2026-08-07) flipped a named list of 16 admin/write/execution API
gates to `default=True` (a deliberate, documented decision — see CLAUDE.md's
"Convention change (2026-08-03)" bullet). In the same commit, 7 unrelated
flags were also flipped to `default=True` with no docstring update, no
commit-message rationale, and no CLAUDE.md entry naming them:

| Flag | Gates |
|---|---|
| `SECTOR_HEAT_ENABLED` | live GDELT article-volume query |
| `WIKIPEDIA_ATTENTION_ENABLED` | live Wikipedia Pageviews API call |
| `SENTIMENT_INDEX_ENABLED` | composite sentiment index computation |
| `ETF_TRANSMISSION_ENABLED` | ETF ownership/comovement measurement (live fetch) |
| `EDGAR_FULLTEXT_ENABLED` | live SEC EDGAR full-text search (10-K/10-Q) |
| `ETF_HOLDINGS_ENABLED` | live SEC N-PORT ETF holdings ingestion |
| `MARKET_DATA_LATENCY_TRACKING_ENABLED` | in-process latency instrumentation |

None of these are admin/write/execution gates — they're data-source,
signal-module, or diagnostic-instrumentation flags, squarely inside CLAUDE.md's
own carve-out: the 2026-08-03 default-on convention "does **not** extend to
feature flags that change trading behavior (new signal modules, sizing
changes, new data sources, forecasting changes)."

## Evidence this was unintentional (not a deliberate decision)

1. Every one of the 7 fields' own `description=` docstring in `settings.py`
   still read "False (the default) is a complete no-op" — contradicting the
   `default=True` on the same field.
2. CLAUDE.md/AGENTS.md (byte-identical, auto-synced) still documented at
   least 3 of the 7 as defaulting `False`.
3. `tests/test_settings.py::test_sentiment_attention_scaffolding_defaults`'s
   own header comment read "every default must preserve today's exact
   behavior (nothing new enabled)" directly above 3 assertions checking
   `is True` — a self-contradictory test.

Confirmed live impact: a real `main_orchestrator.run_pipeline()` call made an
unconditional outbound HTTPS request to `api.gdeltproject.org` every cycle
with no `.env` override, which made
`tests/test_quantitative_models.py::test_main_orchestrator_pipeline`
network-dependent (patched locally as a stopgap before this PR; this PR fixes
the actual root cause so that patch is now defense-in-depth rather than
load-bearing).

`BROKERAGE_CONNECT_ENABLED` (also flipped `True` by the same commit) was
checked and excluded from this fix — its docstring was correctly updated to
"On by default" and it gates a genuine write/command endpoint (brokerage
credential connect/disconnect), so it's not part of this pattern.

## The fix

1. **`settings.py`** — flipped all 7 fields' `default=True` back to
   `default=False`. No docstring text changes were needed on these 7 fields
   themselves (they already correctly describe `False` as the default).
   Also fixed one adjacent, unrelated one-line breakage from the same
   commit: `SENTIMENT_INGESTION_ENABLED`'s docstring referenced
   `PILOTS_API_ENABLED` as an example of an "opt-in networked feature
   default False" — but `PILOTS_API_ENABLED` legitimately flipped to `True`
   in the same commit (it's a real admin gate), so the sentence had become
   self-contradictory ("opt-in... default True") and factually wrong.
   Dropped `PILOTS_API_ENABLED` from that comparison list.

2. **`tests/test_settings.py`** — fixed the 3 wrong `is True` assertions
   back to `is False`, and extended the same test with `delenv` +
   assertion coverage for the 4 flags it never covered
   (`SENTIMENT_INDEX_ENABLED`, `ETF_TRANSMISSION_ENABLED`,
   `ETF_HOLDINGS_ENABLED`, `MARKET_DATA_LATENCY_TRACKING_ENABLED`) — closing
   the exact test gap that let those 4 drift undetected.

3. **`webapp/src/api/mock.ts`** — the live `GET /settings/feature-flags`
   endpoint reads `settings.settings.X` dynamically, so it self-corrects
   with no backend code change. But the offline mock fixture backing the
   Feature Flags screen (`FEATURE_FLAGS_TUNABLE_DEFS`'s "Diagnostic & Data
   Features" group) hardcoded `value: true, default: true` for all 7 flags
   — a mock/live parity gap in the making. Fixed all 7 entries to `false`.
   (The *other* per-feature settings-editor mocks for these same flags, in
   `SENTIMENT_TUNABLE_DEFS` and `ETF_TRANSMISSION_TUNABLE_DEFS`, already
   correctly said `false` — only the Feature Flags screen's own block had
   drifted.)

## Documentation-update step

Checked: CLAUDE.md/AGENTS.md already state `default False` for every one of
these 7 flags wherever documented — no text change needed there, since the
code was wrong, not the docs. No `docs/architecture/*.md` or
`docs/signals/*.md` file was found asserting `default True` for any of the 7.

## Verification

- `pytest tests/test_settings.py -q` → 45 passed.
- `pytest tests/test_quantitative_models.py::test_main_orchestrator_pipeline
  tests/test_feature_flags_registry.py tests/test_settings_keysets.py -q`
  → 28 passed.
- `npm run --prefix webapp typecheck` → clean.
- `npx vitest run src/screens/FeatureFlagsScreen.test.tsx` → 1 passed.
- Full offline suite: `pytest -q -m "not network" -p no:randomly` →
  **11627 passed, 31 skipped, 88 deselected**, 5 failures — all 5 confirmed
  **pre-existing and unrelated** by reproducing identically on unmodified
  `HEAD` (an `ImportError` in `test_data_api_chat.py`/
  `test_gemini_live_chat.py`'s AI-chat-provider routing tests; nothing to do
  with the 7 flags touched here).

## Scope note

This PR intentionally does not touch the `mock.patch` stopgap already added
to `tests/test_quantitative_models.py::test_main_orchestrator_pipeline` for
the originally-reported GDELT network-timeout symptom — it's now
defense-in-depth (the flag defaulting off makes the underlying call never
happen either way), and removing a working safety net wasn't asked for or
necessary to close this bug.
