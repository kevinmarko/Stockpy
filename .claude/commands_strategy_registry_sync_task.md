# Task tracker — Commands screen strategy-registry sync + bulk-validate UI

Branch: `claude/bulk-run-new-strategies-5c6512`
Plan: `.claude/commands_strategy_registry_sync_implementation_plan.md`

## Backend
- [x] `scripts/build_command_manifest.py` — `_fetch_strategy_registry()` (isolated subprocess,
      dead-letters to `[]` on any failure), wired into `build_manifest()`'s returned dict.
- [x] `pilots/commands.py::command_manifest()` — surfaces `strategy_registry` on both the
      success path and the degraded `_empty()` path.
- [x] Regenerated `cli_introspect/command_manifest.json` (29 strategies, `copula_stat_arb`/
      `vol_mispricing` no longer missing from the `--strategies` help text).
- [x] `tests/test_command_manifest_freshness.py` — exact-set-equality CI guard.
- [x] `tests/test_build_command_manifest.py` — dead-letter behavior coverage for the new helper.

## Frontend
- [x] `webapp/src/api/types.ts` — `CommandManifest.strategy_registry?: string[]`.
- [x] `webapp/src/commandParse.ts` — corrected 29-name fallback list; `strategyRegistry` param
      threaded through `parseCommandLine`/`valueSuggestions`; fixed the dead autocomplete
      branch (`prevOption.choices` guard never true for `--strategy`/`--strategies`/date opts).
- [x] `webapp/src/components/CommandFormBuilder.tsx` — `strategyRegistry` prop; exact-match
      split of `--strategy` (unchanged single-select) vs. `--strategies` (new inline
      multi-select, default all-selected, Select All/Clear).
- [x] `webapp/src/screens/Commands.tsx` — threads `strategyRegistry`; new "🧪 Bulk Validate
      All Strategies" button (conditional on `refresh_validations.py` existing in the manifest).
- [x] `webapp/src/api/mock.ts` — added `refresh_validations.py` to the mock manifest +
      `strategy_registry` field (mock/live parity).
- [x] Extended `commandParse.test.ts`, `CommandFormBuilder.test.tsx`, `Commands.test.tsx`.

## Docs
- [x] `docs/architecture/webapp-and-gui.md` — addendum to the existing `COMMAND_EXECUTION_ENABLED`
      paragraph.
- [x] `CLAUDE.md` — new bullet (auto-mirrored to `AGENTS.md` via `sync_agent_docs.sh`, confirmed).

## Verification
- [x] `pytest tests/test_command_manifest_freshness.py tests/test_build_command_manifest.py
      tests/test_pilots_commands.py tests/test_command_execution.py -q` — 48/48 passed.
- [x] `python3 -m ruff check . --select=F821,F822,F823,E9` — clean (the repo's actual CI lint
      gate; a broader default `ruff check` surfaces pre-existing style-only findings in
      untouched lines of `pilots/commands.py`, out of scope for this change).
- [x] `npm run --prefix webapp typecheck` — clean.
- [x] Full webapp vitest suite — 1810/1810 passed (no regressions).
- [x] Live mock-mode browser walkthrough: bulk button renders → opens builder with 29/29
      pre-selected → Clear → toggle one → Select All restores 29/29, compiled command updates
      live throughout → `validation.harness`'s singular `--strategy` dropdown unaffected
      (still starts unselected, now lists all 29 names).

## Explicitly out of scope
- `STANDARD_OPTIONS_STRATEGIES` (Title-Case options-strategy namespace in
  `validation/options_harness.py`) is a second, separate set of valid `--strategy` values,
  not covered by this fix.
