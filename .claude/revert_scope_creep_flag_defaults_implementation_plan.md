# Revert scope-creep `default=True` flips on 7 data-source/diagnostic flags

## Context

Commit `30d136ae8` ("Implement Feature Flags UI and update default-on admin
gates", 2026-08-07) was meant to flip a named list of 16 admin/write/execution
API gates to `default=True` (per CLAUDE.md's "Convention change (2026-08-03)"
carve-out, which explicitly does **not** extend to "feature flags that change
trading behavior... new data sources"). Diffing that commit directly shows it
also flipped 7 flags that are not admin/write/execution gates at all —
`SECTOR_HEAT_ENABLED`, `WIKIPEDIA_ATTENTION_ENABLED`, `SENTIMENT_INDEX_ENABLED`,
`ETF_TRANSMISSION_ENABLED`, `EDGAR_FULLTEXT_ENABLED`, `ETF_HOLDINGS_ENABLED`,
`MARKET_DATA_LATENCY_TRACKING_ENABLED` — each of which gates a live network
call (GDELT / Wikipedia Pageviews / SEC EDGAR) or new
signal/measurement behavior, with zero commit-message or CLAUDE.md rationale
specific to any of them (unlike the named 16, which trace to an explicit,
dated operator decision).

Confirmed unintentional (not a deliberate decision), independently, three ways:
1. Every one of the 7 fields' own `description=` docstring still reads
   "False (the default) is a complete no-op" — never updated to match the
   flip.
2. CLAUDE.md/AGENTS.md (byte-identical, auto-synced) still document at least
   3 of these as defaulting `False` (the Sector-Heat/Wikipedia-Attention/
   pytrends bullet, the ETF Holdings Ingestion bullet, and the Mission
   Control bullet for `MARKET_DATA_LATENCY_TRACKING_ENABLED`).
3. `tests/test_settings.py::test_sentiment_attention_scaffolding_defaults`'s
   own header comment says "every default must preserve today's exact
   behavior (nothing new enabled)" directly above assertions that check
   `is True` for 3 of these — a self-contradictory test, the fingerprint of
   a mechanical sed-style flip rather than a per-flag decision.

Confirmed impact: a real `main_orchestrator.run_pipeline()` call now makes an
unconditional live HTTPS request to `api.gdeltproject.org` every cycle with no
`.env` override, which made `tests/test_quantitative_models.py::
test_main_orchestrator_pipeline` network-dependent (already patched locally in
that one test as a stopgap; this plan fixes the actual root cause).

User confirmed (via AskUserQuestion) fixing all 7, not just the 2 originally
named. `BROKERAGE_CONNECT_ENABLED` (also flipped `True` in the same commit) is
**excluded** — its docstring was correctly updated to "On by default" and it
gates a genuine write/command endpoint, so it is not part of this pattern.

## Changes

### 1. `settings.py` — flip 7 fields back to `default=False`

No docstring text changes needed on these 7 fields — every one already reads
"False (the default) is a complete no-op", so fixing the `default=` value
alone makes field and docstring agree again:

- `MARKET_DATA_LATENCY_TRACKING_ENABLED` (~line 648)
- `SENTIMENT_INDEX_ENABLED` (~line 2672)
- `EDGAR_FULLTEXT_ENABLED` (~line 2961)
- `SECTOR_HEAT_ENABLED` (~line 2991)
- `WIKIPEDIA_ATTENTION_ENABLED` (~line 3133)
- `ETF_TRANSMISSION_ENABLED` (~line 3201)
- `ETF_HOLDINGS_ENABLED` (~line 4396)

One adjacent one-line text fix in a *different* field's docstring
(`SENTIMENT_INGESTION_ENABLED`, ~line 2591), also broken by the same mass
flip: it currently reads "...matching this codebase's convention for opt-in
networked features (ORCHESTRATOR_DAEMON_ENABLED, GRAVITY_AI_RUNNER_ENABLED,
PILOTS_API_ENABLED all default True the same way)" — self-contradictory
(opt-in features defaulting *True* isn't an example of opt-in) and factually
wrong for 2 of the 3 names (only `PILOTS_API_ENABLED` actually defaults
`True`; the other two still default `False`, unaffected by this plan). Fix:
drop `PILOTS_API_ENABLED` from the comparison list and restore "...default
False the same way" for the remaining two, which are still accurate.

### 2. `tests/test_settings.py` — fix + extend the scaffolding-defaults test

In `test_sentiment_attention_scaffolding_defaults` (~line 111):
- Flip the 3 existing wrong assertions: `EDGAR_FULLTEXT_ENABLED`,
  `SECTOR_HEAT_ENABLED`, `WIKIPEDIA_ATTENTION_ENABLED` → `is False`.
- Add `monkeypatch.delenv(...)` + `assert ... is False` coverage for the 4
  flags this test never covered (`SENTIMENT_INDEX_ENABLED`,
  `ETF_TRANSMISSION_ENABLED`, `ETF_HOLDINGS_ENABLED`,
  `MARKET_DATA_LATENCY_TRACKING_ENABLED`) — closing the exact test gap that
  let those 4 slip through unnoticed. This keeps the fix from being a
  do-over of the same drift later.

No other test file references these 7 flags' *default* value (verified via
repo-wide grep) — `tests/test_pilots_api_tunables.py`'s `ETF_TRANSMISSION_ENABLED`
hits are PUT-endpoint tests that set the value explicitly in the request body,
unrelated to the default.

### 3. `webapp/src/api/mock.ts` — fix the Feature Flags screen mock (mock/live parity)

`api/pilots_api.py`'s real `GET /settings/feature-flags` reads
`settings.settings.X` live via `_settings_editor_payload`, so it self-corrects
automatically once settings.py is fixed — no backend endpoint code changes
needed. But the **mock** fixture backing the webapp's offline Feature Flags
screen hardcodes stale values: `FEATURE_FLAGS_TUNABLE_DEFS`'s "Diagnostic &
Data Features" group (~lines 4852-4909) has `value: true, default: true` for
all 7 flags, which would silently reintroduce a mock/live parity gap (the
exact class of bug this codebase's CLAUDE.md repeatedly documents fixing
elsewhere in the options desk). Flip all 7 entries to `value: false, default:
false`. (The *other*, per-feature settings-editor mocks for these same flags
— e.g. `SECTOR_HEAT_ENABLED` inside `SENTIMENT_TUNABLE_DEFS`, `ETF_HOLDINGS_ENABLED`/
`ETF_TRANSMISSION_ENABLED` inside `ETF_TRANSMISSION_TUNABLE_DEFS` — already
correctly say `false`; only the Feature Flags screen's own block drifted.)

`pilots/feature_flags.py`'s `DIAGNOSTIC_FLAG_REASONS` registry (what makes
these 7 keys visible on the Feature Flags screen at all) is a separate,
legitimate, dated 2026-08-07 decision about *screen visibility*, not about
their default *value* — it is correct as-is and needs no change.

### 4. Documentation-update step (per CLAUDE.md's Implementation Plan requirement)

Checked and no edit needed: CLAUDE.md/AGENTS.md (byte-identical) already
state `default False` for every one of these 7 flags wherever they're
documented (the Sector-Heat/Wikipedia-Attention/pytrends bullet, the ETF
Holdings Ingestion bullet, the Mission Control bullet). `SENTIMENT_INDEX_ENABLED`
and `ETF_TRANSMISSION_ENABLED`'s own CLAUDE.md-level descriptions don't state a
default value at all, so nothing there to correct either. No `docs/architecture/*.md`
or `docs/signals/*.md` file was found asserting `default True` for any of the
7. This step is being stated explicitly, not skipped, per CLAUDE.md's
requirement that every Implementation Plan include it even when the
conclusion is "no doc edit required."

## PR Artifacts

Per CLAUDE.md's "PR Artifacts & Unique Naming" rule, commit uniquely-scoped
copies of this plan + a task tracker + a walkthrough to `.claude/` on the
branch, e.g.:
- `.claude/revert_scope_creep_flag_defaults_implementation_plan.md`
- `.claude/revert_scope_creep_flag_defaults_task.md`
- `.claude/revert_scope_creep_flag_defaults_walkthrough.md`

## Workflow

This is a data-source/trading-behavior change (CLAUDE.md's "Everything else"
tier) — branch + PR required, no direct commit to `main`.
- `git checkout -b revert-diagnostic-flag-default-flips`
- Make the 3 code/test/mock edits above
- Run the targeted tests (below), commit, push, open a PR with the
  before/after table and the 3-way confirmation evidence from this
  investigation in the PR body

## Verification

- `pytest tests/test_settings.py -q` — the fixed/extended
  `test_sentiment_attention_scaffolding_defaults` must pass, asserting all 7
  flags default `False`.
- `pytest tests/test_quantitative_models.py::test_main_orchestrator_pipeline -q`
  — must still pass (the existing explicit `mock.patch` stays in place as
  defense-in-depth; this confirms the test no longer *needs* to rely on that
  patch alone to stay offline, since `SECTOR_HEAT_ENABLED` now defaults off
  too).
- `pytest tests/test_feature_flags_registry.py tests/test_settings_keysets.py -q`
  — confirm no regression in the Feature Flags registry tests (these don't
  assert on the 7 flags' default values today, so should be unaffected).
- `npm run --prefix webapp typecheck` — confirm the `mock.ts` edit doesn't
  break typing (plain literal value changes, no shape change expected).
- `npm run --prefix webapp test -- FeatureFlagsScreen` — confirm the Feature
  Flags screen's own tests still pass against the corrected mock defaults.
- `make ci` (or `pytest` full suite) before opening the PR, per CLAUDE.md's
  "Verification is mandatory" rule.
