# Sync the Commands screen's strategy list + add a real bulk-validate-all-strategies UI

## Context

The Pilots PWA's Commands screen (`webapp/src/screens/Commands.tsx`) builds CLI invocations for
`validation.harness` (single `--strategy`) and `scripts/refresh_validations.py` (bulk `--strategies`).
Both dropdowns are driven by a hardcoded TypeScript constant, `REGISTERED_STRATEGIES`
(`webapp/src/commandParse.ts:167-184`), which lists 16 strategy names. The real source of truth,
`STRATEGY_REGISTRY` in `scripts/refresh_validations.py:3460-3621`, now has **29** entries — 13 newer
strategies (`copula_stat_arb`, `vol_mispricing`, `pairs_trading`, `lgbm_ranker`,
`vrp_premium_selling`, `covered_call`, the four credit/debit spread strategies, `aroon_trend`,
`sector_quality_rank`, `options_flow_sentiment`) are invisible in the UI. The committed
`cli_introspect/command_manifest.json` is separately two strategies stale in its own embedded help
text (`copula_stat_arb`, `vol_mispricing`) because nobody re-ran `scripts/build_command_manifest.py`
after they were added.

Separately, the backend already fully supports a bulk "validate every strategy" run —
`refresh_validations.py --strategies` accepts a comma-separated list, or the flag can be omitted
entirely to validate the whole registry — but the webapp's Form Mode builder renders `--strategies`
as a single-value `<select>` (the same code path as `validation.harness`'s single-strategy
`--strategy`), so there is no way to pick more than one strategy from the UI today. The user wants
(a) the strategy list corrected and (b) a real bulk-validate-all affordance, and — since this list has
now silently drifted out of sync once already — a mechanism so it can't drift again.

Investigation (two rounds, files read in full: `pilots/commands.py`, `scripts/build_command_manifest.py`,
`webapp/src/components/CommandFormBuilder.tsx`, `webapp/src/commandParse.ts`, relevant sections of
`webapp/src/screens/Commands.tsx`, `webapp/src/api/{types,mock}.ts`, `cli_introspect/command_manifest.json`,
`gui/panels/validation_lab.py`, `webapp/src/components/options/OptionsMetricSelector.tsx`) also turned up a
genuine pre-existing bug: `commandParse.ts:507`'s branch guard (`prevOption.choices` truthy) means the
free-text Command Bar's strategy-name autocomplete has been dead code all along — `--strategy`/`--strategies`
never carry a `choices` array from argparse (and structurally can't: `--strategy` spans two disjoint name
namespaces — snake_case `STRATEGY_REGISTRY` vs. Title-Case `STANDARD_OPTIONS_STRATEGIES` in
`validation/options_harness.py:128` — and `--strategies` is one comma-joined string, not a
`argparse.choices`-compatible single value). Fixing this is in scope alongside the rest.

## Approach

**1. Single source of truth, surfaced through the manifest (not argparse `choices=`).**

`scripts/build_command_manifest.py`'s `build_manifest()` gains a new best-effort step that fetches
`sorted(STRATEGY_REGISTRY.keys())` and writes it as a new top-level `strategy_registry: list[str]` key
in the JSON. Fetch it via a subprocess-isolated helper (mirroring `cli_introspect/capture.py`'s existing
isolation pattern — a target module that heavy-imports pandas/quant engines has no business running
un-isolated in the manifest-builder's own process, even inside a try/except, since that only catches a
clean exception, not a hang or native crash). On any failure (import error, timeout, bad output),
degrade to `[]` and log a WARNING — "dead-letter, don't crash," matching this file's existing philosophy
for the 12 other introspection targets. Do NOT add `choices=` to the `--strategy`/`--strategies`
argparse definitions themselves — it's structurally wrong for both (see Context above).

`pilots/commands.py::command_manifest()` currently constructs a closed 5-key dict by hand from the
parsed JSON (it does not pass through arbitrary top-level keys) — add `"strategy_registry":
data.get("strategy_registry", [])` to both the success-path return and `_empty()`'s degraded-shape
return, so the field is always present with a consistent `[]`-on-failure default (CONSTRAINT #6).

Regenerate the real `cli_introspect/command_manifest.json` (`python scripts/build_command_manifest.py`)
as part of this change — this both adds the new field and fixes the pre-existing 2-strategy staleness
in the `--strategies` help text.

**Future-proofing test**: new `tests/test_command_manifest_freshness.py` imports
`STRATEGY_REGISTRY` from `scripts.refresh_validations`, reads the committed manifest's
`strategy_registry` field, and asserts the two sets are exactly equal — catching both a missing-new-
strategy regression and a stale-removed-strategy one, with a failure message pointing at
`python scripts/build_command_manifest.py`. Also add a small `tests/test_build_command_manifest.py`
covering the new helper's own dead-letter behavior (import/subprocess failure → `[]` + warning, never
raises).

**2. Webapp: read the live list, keep the hardcoded constant only as a last-resort fallback.**

- `webapp/src/api/types.ts`: `CommandManifest` gains `strategy_registry?: string[]` (optional, matching
  the existing `dead_letters?` precedent for backward compat with older mocks/manifests).
- `webapp/src/commandParse.ts`: correct `REGISTERED_STRATEGIES` to the current full 29 names (comment
  updated to say it's now a fallback-of-last-resort, real source is the manifest field); give
  `parseCommandLine`/`valueSuggestions` a new `strategyRegistry: string[] = []` parameter (default value,
  not `?`, so none of the ~15 existing call sites need updating); resolve effective list as
  `strategyRegistry.length > 0 ? strategyRegistry : REGISTERED_STRATEGIES` wherever it's consumed.
- Fix the dead-code branch at `commandParse.ts:507` — widen the guard so `valueSuggestions` actually
  fires for strategy options (and, since it's the same root cause, the date options too) despite
  `choices` being `null`.
- `webapp/src/components/CommandFormBuilder.tsx`: add an optional `strategyRegistry: string[] = []`
  prop, threaded into `OptionFormControl`. Split the current loose `option.name.includes("strategy")`
  check into two **exact-match** cases — `option.name === "--strategy"` (singular, `validation.harness`
  — stays a single `<select>`, just fed the live/fallback list) vs. `option.name === "--strategies"`
  (plural, `refresh_validations.py` — gets the new multi-select, below). Using exact match here is
  load-bearing: `CommandFormBuilder.test.tsx`'s existing `HARNESS_COMMAND` tests assert the singular
  `--strategy` select starts on the empty `""` option, so the new "pre-select everything" behavior for
  `--strategies` must never leak onto `--strategy` via a loose substring match.
- New inline multi-select for `--strategies`, built directly inside `OptionFormControl`'s existing
  per-option card (not a second nested modal — `OptionsMetricSelector.tsx` is a raw `position: fixed`
  overlay predating `Modal.tsx`'s a11y fixes, and `CommandFormBuilder` already renders inside a `Modal`;
  stacking a second overlay inside it would reintroduce the exact bug `Modal.tsx` was built to avoid).
  Borrow only the toggle-per-row shape (`Toggle` per strategy name, `checked={selected.has(name)}`),
  add "Select All" / "Clear" buttons and a live "`X of 29 selected`" counter, in a `maxHeight`/`overflowY:
  auto` sub-container (29 rows shouldn't dominate the modal's own scroll area). Serializes to/from the
  comma-joined string the CLI already expects — no other plumbing changes needed since
  `RunCommandControl.tsx` already passes `argTokens` straight through to `createJob(...)` unmodified,
  and `launch_manifest_command`/`JobManager` have no length limit on `args` (confirmed).
- Default value: both `CommandFormBuilder`'s initial `optionValues` state build and its `Reset` handler
  special-case `option.name === "--strategies"` to start with **all strategies selected** (comma-joined
  full list) instead of empty string — mirrors `gui/panels/validation_lab.py:52-83`'s
  `st.multiselect(..., default=options)` precedent for the equivalent Streamlit tab. This makes opening
  the builder and hitting Run an immediate, genuine "validate everything" action.

**3. One-click discoverability.**

`Commands.tsx` gets a "🧪 Bulk Validate All Strategies" button near the existing header controls
("💻 Command Launcher" / "📋 Staged Execution Queue"), rendered only if `refresh_validations.py` exists
in `data.commands` (never fabricate an affordance the manifest doesn't back — same honesty convention
as the rest of this screen). Clicking it calls the existing `setBuilderCommand(...)` directly with that
`CommandSpec` — same modal, same job-runner path (`COMMAND_EXECUTION_ENABLED` → `resolve_command` →
`launch_manifest_command`), now pre-defaulted to all-selected per above. No new backend wiring: this
reuses the exact `JobType.COMMAND` path already documented in `docs/architecture/webapp-and-gui.md`.

**4. Mock/live parity.**

`webapp/src/api/mock.ts`'s `MOCK_COMMAND_MANIFEST` currently has no `refresh_validations.py` entry at
all (5 commands total) and no `strategy_registry` field — without fixing this, the new button and
multi-select would be invisible in the default `VITE_USE_MOCK=true` dev mode. Add a `refresh_validations.py`
`CommandSpec` mirroring the real manifest's shape (`cli_introspect/command_manifest.json:574-676`:
`--strategies`, `--start`, `--end`, `--output-dir`, `--n-cpcv-splits`, `--n-test-splits`, `--workers`,
`--json`), bump `command_count` to 6, and add the full `strategy_registry` list.

**5. Explicitly out of scope** (call this out in a code comment where relevant, so a future reader
doesn't assume this PR made `--strategy` autocomplete fully complete): the options-strategy Title-Case
namespace (`STANDARD_OPTIONS_STRATEGIES` in `validation/options_harness.py`, e.g. `"Put Credit Spread"`)
is a second, pre-existing set of valid `--strategy` values for `validation.harness` that neither the old
`REGISTERED_STRATEGIES` nor the new `strategy_registry` manifest field covers. Not fixing this now —
it's a separate, smaller registry with a different (Title Case) naming convention and would need its
own manifest field to do honestly.

## Documentation

- `docs/architecture/webapp-and-gui.md`: extend the existing `COMMAND_EXECUTION_ENABLED`/`Commands.tsx`
  paragraph with the `strategy_registry` manifest field, the new freshness test, and the bulk
  multi-select UX (append to that paragraph, matching its existing style; list the new test files at
  its end the way it already does).
- `CLAUDE.md` (auto-mirrors to `AGENTS.md` via `sync_agent_docs.sh`): a short bullet near the existing
  "Command execution from the webapp Commands screen" entry noting the strategy-registry sync + bulk
  multi-select fix and the new freshness test — keep it concise, this doesn't need its own paragraph.

## Branch / PR

Already on `claude/bulk-run-new-strategies-5c6512` with zero commits ahead of `main` (a harness-created
branch for this exact task) — use it as-is; no engines/signals/execution/sizing/orchestrator code is
touched, but this is real new behavior (not docs/tests-only), so it still goes through this branch + a PR
per the repo's Branch Workflow, rather than direct-to-main.

## Files

**Backend:**
- `scripts/build_command_manifest.py` — new subprocess-isolated `strategy_registry` fetch helper, wired
  into `build_manifest()`.
- `pilots/commands.py` — surface `strategy_registry` in `command_manifest()`'s return (success + `_empty`).
- `cli_introspect/command_manifest.json` — regenerated.
- New `tests/test_command_manifest_freshness.py`, new `tests/test_build_command_manifest.py`.

**Frontend:**
- `webapp/src/api/types.ts` — `CommandManifest.strategy_registry?: string[]`.
- `webapp/src/commandParse.ts` — full 29-name fallback list + doc comment update; `strategyRegistry`
  param threaded through `parseCommandLine`/`valueSuggestions`; fix the dead `choices`-gated branch.
- `webapp/src/components/CommandFormBuilder.tsx` — `strategyRegistry` prop; exact-match split of
  `--strategy` vs `--strategies`; new inline multi-select for `--strategies`; all-selected default/reset.
- `webapp/src/screens/Commands.tsx` — thread `strategyRegistry` down; add the bulk-validate quick button.
- `webapp/src/api/mock.ts` — add `refresh_validations.py` to `MOCK_COMMAND_MANIFEST`, bump
  `command_count`, add `strategy_registry`.
- Extend `webapp/src/components/CommandFormBuilder.test.tsx`, `webapp/src/commandParse.test.ts`,
  `webapp/src/screens/Commands.test.tsx` with the new-behavior cases (multi-select defaults/select-all/
  clear, `--strategy` regression guard, autocomplete-now-works, bulk button visibility/click).

**Docs:** `docs/architecture/webapp-and-gui.md`, `CLAUDE.md`.

## Verification

1. `python scripts/build_command_manifest.py --json` — confirm `strategy_registry` has 29 entries and
   the `refresh_validations.py` command's `--strategies` description now lists all 29.
2. `pytest tests/test_command_manifest_freshness.py tests/test_build_command_manifest.py tests/test_pilots_commands.py tests/test_command_execution.py -q`
   — all green.
3. `npm run --prefix webapp typecheck` — clean.
4. `npm run --prefix webapp test -- commandParse CommandFormBuilder Commands` (or the repo's equivalent
   vitest invocation) — extended test cases pass.
5. Browser check (`npm run dev`, mock mode): open Commands screen → confirm the "🧪 Bulk Validate All
   Strategies" button appears → click it → Form Mode opens for `refresh_validations.py` with all 29
   strategies pre-checked → uncheck a few, confirm the Compiled Execution Target preview updates its
   comma-joined `--strategies` list live → Select All restores the full set. Separately open
   `validation.harness`'s Form Mode and confirm its `--strategy` dropdown now lists all 29 names and
   still starts unselected.
6. `make ci` (or `pytest` + `ruff`) for the full offline gate before considering this done, per this
   repo's mandatory verification convention.
