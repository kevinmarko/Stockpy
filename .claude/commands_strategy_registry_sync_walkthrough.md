# Walkthrough — Commands screen strategy-registry sync + bulk-validate UI

## Problem

The Pilots PWA's Commands screen builds CLI invocations for `validation.harness --strategy`
(single) and `scripts/refresh_validations.py --strategies` (bulk). Both pickers were driven by
a hardcoded TypeScript constant, `REGISTERED_STRATEGIES` (`webapp/src/commandParse.ts`), which
had drifted to 16 names against the real `STRATEGY_REGISTRY` in `scripts/refresh_validations.py`
(29 names) — 13 strategies, including `copula_stat_arb` and `vol_mispricing`, were invisible in
the UI. Separately, the backend already fully supports validating every strategy in one run
(`--strategies` accepts a comma list, or omitting it validates the whole registry), but the
webapp rendered `--strategies` as a single-value `<select>` — the same code path as the
genuinely-single-value `--strategy` — so there was no way to pick more than one strategy from
the UI.

A related pre-existing bug was found and fixed in the same pass: the free-text Command Bar's
autocomplete for strategy-name values was dead code, because its branch guard required
`option.choices` to be non-null, and `--strategy`/`--strategies` structurally never carry a
`choices` array from argparse (`--strategy` spans two disjoint name namespaces — snake_case
`STRATEGY_REGISTRY` vs. Title-Case `STANDARD_OPTIONS_STRATEGIES` — and `--strategies` is one
comma-joined string, neither of which `argparse.choices` can express).

## Fix

**Single source of truth, surfaced through the manifest.** `scripts/build_command_manifest.py`
now fetches `sorted(STRATEGY_REGISTRY.keys())` via an isolated subprocess (mirroring
`cli_introspect/capture.py`'s own subprocess-isolation pattern — a bare in-process try/except
would only catch a clean exception, not a hang or native crash from importing a
pandas/quant-engine-heavy module) and writes it as a new top-level `strategy_registry` field in
the committed `cli_introspect/command_manifest.json`. `pilots/commands.py::command_manifest()`
passes that field through `GET /commands` (both the success path and the degraded/empty-manifest
path, always defaulting to `[]` — never fabricated).

**Webapp reads the live list.** `commandParse.ts`'s `parseCommandLine`/`valueSuggestions` and
`CommandFormBuilder.tsx` now accept the live `strategy_registry` list (threaded down from
`Commands.tsx`'s API response), falling back to the hardcoded constant (corrected to the current
29 names) only when the manifest field is absent/empty.

**Real bulk-validate UI.** `CommandFormBuilder.tsx` distinguishes `--strategy` (singular,
`validation.harness` — unchanged single `<select>`) from `--strategies` (plural,
`refresh_validations.py` — new inline multi-select, checkbox-per-strategy, "Select All"/"Clear",
defaulting to all 29 selected). `Commands.tsx` gained a one-click "🧪 Bulk Validate All
Strategies" button that opens this same builder pre-defaulted to all-selected. No new backend
job-execution wiring — this reuses the existing `JobType.COMMAND`/`COMMAND_EXECUTION_ENABLED`
job runner unchanged.

**Future-proofing.** `tests/test_command_manifest_freshness.py` asserts the committed manifest's
`strategy_registry` is in exact set-equality with the live `STRATEGY_REGISTRY` — this now fails
CI the next time a strategy is added/removed without regenerating the manifest, closing the gap
that let this drift happen once already. `tests/test_build_command_manifest.py` covers the new
fetch helper's own dead-letter behavior.

## Explicitly out of scope

`STANDARD_OPTIONS_STRATEGIES` (the Title-Case options-strategy namespace in
`validation/options_harness.py`, e.g. `"Put Credit Spread"`) is a second, pre-existing set of
valid `--strategy` values for `validation.harness` not covered by this change — it was never
covered by the old `REGISTERED_STRATEGIES` either, and would need its own manifest field to
sync honestly. A future reader should not assume this PR made `--strategy` autocomplete fully
complete.

## Verification

- `python3 -m ruff check . --select=F821,F822,F823,E9` (this repo's actual CI lint gate) — clean.
- `pytest tests/test_command_manifest_freshness.py tests/test_build_command_manifest.py tests/test_pilots_commands.py tests/test_command_execution.py -q` — 48/48 passed.
- `npm run --prefix webapp typecheck` — clean.
- Full webapp vitest suite — 1810/1810 passed, no regressions.
- Live mock-mode browser walkthrough (screenshots taken during review): the bulk button renders
  next to "Command Launcher"/"Staged Execution Queue", opens the `refresh_validations.py` Form
  Mode builder with "29 of 29 selected", Clear drops to 0/29 (compiled command updates live to
  just the toggled-back-on name), Select All restores 29/29; `validation.harness`'s singular
  `--strategy` dropdown independently confirmed to list all 29 names while still starting
  unselected (no leakage from the new all-selected-by-default logic).

## Process note

Implemented via 5 parallel subagents (backend passthrough+tests, `commandParse.ts`,
`CommandFormBuilder.tsx`, `Commands.tsx`+mock, docs), each scoped to disjoint files per an
explicit interface contract (exact prop/param names) drawn from the approved plan, then
cross-verified by re-running the full test suites together and a live browser check.
