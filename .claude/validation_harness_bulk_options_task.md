# Task tracker: validation.harness bulk mode (mirroring refresh_validations.py)

Branch: `claude/validation-harness-bulk-menu-48c980`

## Scope decisions (confirmed with operator)
- [x] Bulk mode (`--strategies`) validates `STANDARD_OPTIONS_STRATEGIES` only; a
      non-options name is rejected with a clear error (fail closed), not silently
      re-running the Buy-and-Hold placeholder under a fake label.
- [x] Webapp Commands screen wired up too: a real multi-select + second bulk
      entry point for `validation.harness`, alongside the existing
      `refresh_validations.py` one.

## Implementation checklist

- [x] `validation/harness.py`: `--strategy`/`--strategies` mutually exclusive
      group (`required=True`); new `--workers`/`-w`, `--json`; bulk-mode branch
      (`_run_options_bulk`, `_print_options_bulk_summary`, `_fail_reason`);
      exit code 0/1 on deployability; per-strategy exceptions dead-lettered,
      never abort the batch.
- [x] **Pre-existing bug found + fixed**: `run_options_validation()`'s
      `cls(strategy_fn=..., reports_dir=...)` omitted required `universe_fn`/
      `cost_model` — `TypeError` on every invocation (confirmed via `git stash`
      against `main`, unrelated to this PR's own changes but directly blocking
      the feature). Fixed with harmless placeholders (neither arg is read by
      the 3 persistence methods called on that throwaway instance).
- [x] `scripts/build_command_manifest.py`: `_fetch_registry_via_subprocess`
      shared helper; `_fetch_options_strategy_registry` sibling of
      `_fetch_strategy_registry`; `options_strategy_registry` manifest field.
- [x] Regenerated `cli_introspect/command_manifest.json` (diff reviewed — only
      expected new options/field, no accidental drift).
- [x] `pilots/commands.py`: `options_strategy_registry` passthrough (success +
      degraded-empty paths).
- [x] `webapp/src/api/types.ts` + `mock.ts`: new field + mock manifest entries.
- [x] `webapp/src/commandParse.ts`: `REGISTERED_OPTIONS_STRATEGIES` fallback
      constant (NOT wired into the free-text Command Bar's `valueSuggestions` —
      disclosed, out-of-scope gap, documented in both doc bullets below).
- [x] `webapp/src/components/CommandFormBuilder.tsx`: new
      `optionsStrategyRegistry` prop, `effectiveOptionsStrategies`,
      `registryForOption()` helper — singular `--strategy` on
      `validation.harness` deliberately untouched (regression-tested).
- [x] `webapp/src/screens/Commands.tsx`: second "🧪 Bulk Validate Options
      Strategies" button + prop threading.
- [x] Tests: `tests/test_harness_bulk_cli.py` (new, 12 tests incl. a
      constructor-bug regression class verified to actually catch the bug via
      a manual revert-and-confirm-red check), extended
      `tests/test_build_command_manifest.py` (+7), `tests/test_command_manifest_freshness.py`
      (+1), `tests/test_pilots_commands.py` (+3 assertions/1 new test), extended
      `webapp/src/components/CommandFormBuilder.test.tsx` (+4 tests),
      `webapp/src/screens/Commands.test.tsx` (+2 tests).
- [x] Docs: `docs/architecture/validation-and-signals.md` (`validation/harness.py`
      bullet extended), `docs/architecture/webapp-and-gui.md` (new dated
      follow-up bullet after the "Commands screen strategy-registry sync"
      bullet it closes part of).

## Verification (all run, all green except pre-existing/unrelated)

- [x] `python -m ruff check . --select=F821,F822,F823,E9` — clean.
- [x] `python -m pytest -m "not network and not slow" -n auto --dist loadgroup`
      — 11745 passed, 31 skipped, 23 failed. All 23 failures confirmed
      pre-existing on `main` via `git stash` (chat-provider env deps, and
      `test_run_once.py`/`test_pipeline_smoke.py`/`test_main_body_engine_injection.py`
      — none touch anything this PR changed). Zero new failures.
- [x] Real (non-mocked, non-network — synthetic price_df) end-to-end CLI smoke:
      `python -m validation.harness --strategies "Put Credit Spread,Iron Condor" --workers 2 --start 2023-01-01 --end 2023-06-01`
      against REAL price/backtest logic — produced two genuinely different
      per-strategy results (proving real per-strategy dispatch, not the
      placeholder), correct table + exit-code behavior.
- [x] `python -m validation.harness --strategies "Not A Real Strategy"` →
      exit 2, no run.
- [x] `python scripts/build_command_manifest.py` re-run, diff reviewed.
- [x] `npm run --prefix webapp typecheck` — clean.
- [x] `npx vitest run` (full webapp suite) — 167 files / 1817 tests passed.
