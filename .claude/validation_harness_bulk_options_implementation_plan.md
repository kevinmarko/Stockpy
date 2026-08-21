# `validation.harness` bulk mode (mirroring `refresh_validations.py`)

## Context

`scripts/refresh_validations.py` already has a real bulk mode: `--strategies NAME[,NAME]`
(comma-separated, defaults to *all* of `STRATEGY_REGISTRY`) + `--workers`, wired to the
webapp Commands screen's "🧪 Bulk Validate All Strategies" button. `validation/harness.py`'s
own CLI (`python -m validation.harness`) only takes a single `--strategy` and has no bulk
capability at all.

The two CLIs aren't equivalent under the hood, though: for a non-options `--strategy` name,
`validation.harness`'s `main()` never touches `STRATEGY_REGISTRY` — it always runs a fixed
placeholder Buy-and-Hold-SPY strategy. Only names in `STANDARD_OPTIONS_STRATEGIES`
(`validation/options_harness.py`: `Put Credit Spread`, `Call Credit Spread`, `Iron Condor`,
`Bull Call Spread`, `Bear Put Spread`, `Long Straddle`) get real, strategy-specific logic via
`StrategyValidationHarness.run_options_validation()`. So a naive bulk flag that accepted any
name would silently re-run the identical placeholder N times for anything non-options —
confirmed with the user this is out of scope; bulk mode is **options-strategies only**, and a
non-options name in `--strategies` is a hard, clear CLI error rather than a silent no-op.

Confirmed with user (via AskUserQuestion):
1. Bulk mode validates `STANDARD_OPTIONS_STRATEGIES` only; other names are rejected with a clear error.
2. The webapp Commands screen should also be wired up (a real multi-select + bulk entry point for `validation.harness`, alongside the existing one for `refresh_validations.py`).

This touches `validation/` (runtime/validation logic) → per `CLAUDE.md`'s Start-of-session
checklist this is "Everything else" tier: needs a feature branch + PR, not a direct commit to
`main`.

## Python: `validation/harness.py`

- Add `import concurrent.futures` at module top (mirrors `scripts/refresh_validations.py`'s
  own `ThreadPoolExecutor` usage for the same purpose).
- In `main()`, replace the single required `--strategy` argument with a **mutually exclusive
  group, `required=True`**, containing:
  - `--strategy` (unchanged: single name, existing options/placeholder branching untouched —
    this preserves every documented invocation exactly, e.g. `docs/HOW_TO_GUIDE.md`,
    `docs/GO_LIVE_CHECKLIST.md`, `docs/RUNBOOK.md`'s `python -m validation.harness --strategy
    <name> --start ... --end ...`).
  - `--strategies` (new, comma-separated, e.g. `--strategies "Iron Condor,Put Credit Spread"`)
    — bulk mode.
  - `--workers`/`-w` (new, `int`, default `1`) — mirrors `refresh_validations.py`'s flag name,
    dest, and help text convention.
  - `--json` (new, `action="store_true"`) — mirrors `refresh_validations.py`'s one
    machine-readable JSON line as the last line of stdout.
- New bulk-mode branch in `main()` (only reachable when `args.strategies` is set):
  1. Parse `args.strategies` into a list (`.split(",")`, stripped, empties dropped) —
     matches `refresh_validations.py`'s exact parsing idiom.
  2. Validate every name against `validation.options_harness.STANDARD_OPTIONS_STRATEGIES`
     (imported at the top of the branch, matching the existing single-strategy branch's own
     local import of `STANDARD_OPTIONS_STRATEGIES`). Any unknown name → print a clear error
     naming the bad value(s) and the full list of valid options names, then `raise
     SystemExit(2)` **before running anything** (fail closed, no partial runs).
  3. Run each valid name through `StrategyValidationHarness.run_options_validation(
     strategy_name=name, ticker=args.ticker, start_date=args.start, end_date=args.end)`
     (the exact same call the existing single-strategy options branch already makes).
     Sequential when `args.max_workers <= 1` or only one name; otherwise
     `concurrent.futures.ThreadPoolExecutor(max_workers=min(args.max_workers, len(names)))`
     with `executor.map` (order-preserving — same convention `refresh_validations.py` uses and
     documents). Catch per-strategy exceptions so one failing name doesn't abort the batch;
     record the exception in the results dict instead (dead-letter, don't crash — CONSTRAINT #6).
  4. Print a compact pass/fail summary table — a new small helper,
     `_print_options_bulk_summary(results: Dict[str, ValidationReport | Exception])`, local to
     this module (deliberately NOT importing `scripts.refresh_validations`'s `_print_summary_table`/
     `_fail_reason`, since `refresh_validations.py` already imports `validation.harness` at
     function scope — importing back would be circular). Reuses `ValidationReport.deployable`,
     `.sharpe`, `.pbo`, `.dsr`, `.max_dd`, `.stress_gate_passed` directly (all already public
     properties/attributes) to build the same PASS/FAIL/ERROR-with-reason table shape
     `refresh_validations.py._print_summary_table` produces, scoped to the always-options-selling
     case (stress-gate reason always applicable here, unlike the general-purpose version).
  5. If `--json`, print one final machine-readable JSON line:
     `{strategy_name: {deployable, pbo, dsr, sharpe, max_drawdown[, error]}}` — same shape/
     convention as `refresh_validations.py --json`.
  6. Exit code: `0` if every strategy is deployable, `1` otherwise (matches
     `refresh_validations.py main()`'s return convention). The pre-existing single-strategy
     branch's exit behavior is untouched (it doesn't set an exit code today; leaving it as-is
     avoids any risk to existing callers).

No changes to `run_options_validation()`, `run()`, `ValidationReport`, or the single-strategy
CLI branch's own logic — this is additive only.

## Manifest generation: `scripts/build_command_manifest.py`

- New `_fetch_options_strategy_registry()`, a straight sibling of the existing
  `_fetch_strategy_registry()` (same subprocess-isolation rationale, same dead-letter-to-`[]`
  behavior on any failure): fetches `sorted(STANDARD_OPTIONS_STRATEGIES.keys())` from
  `validation.options_harness` via an isolated `subprocess.run([sys.executable, "-c", ...])`
  call (this module is comparatively light — no pandas/numpy heavy import chain the way
  `scripts.refresh_validations` is — but subprocess isolation is kept for consistency with the
  established pattern and because `validation.options_harness` still pulls in `validation.harness`
  transitively).
- `build_manifest()` gains a new top-level `"options_strategy_registry"` key in its return dict,
  populated the same way `"strategy_registry"` already is.
- Regenerate `cli_introspect/command_manifest.json` by actually running
  `python scripts/build_command_manifest.py` once the Python CLI change above lands — this
  picks up the new `--strategies`/`--workers`/`--json` options on `validation.harness`
  automatically via real argparse introspection (`cli_introspect/capture.py`'s monkeypatch
  trick), plus the new `options_strategy_registry` field. Commit the regenerated file.

## Backend reader: `pilots/commands.py`

- `command_manifest()`'s success-path dict gains `"options_strategy_registry":
  data.get("options_strategy_registry", [])`, mirroring the existing `"strategy_registry"` line
  exactly (including the same honest empty-list degrade in `_empty()`).

## Webapp types/client: `webapp/src/api/types.ts`, `webapp/src/api/mock.ts`

- `CommandManifest` interface: add `options_strategy_registry?: string[]` right after the
  existing `strategy_registry?: string[]` field, with a docstring comment mirroring that
  field's own (sourced from `STANDARD_OPTIONS_STRATEGIES` in `validation/options_harness.py`
  now, generated by `scripts/build_command_manifest.py`).
- `MOCK_COMMAND_MANIFEST`: add `options_strategy_registry: ["Put Credit Spread", "Call Credit
  Spread", "Iron Condor", "Bull Call Spread", "Bear Put Spread", "Long Straddle"]`, and extend
  the mock `validation.harness` command entry's `options` array with `--strategies`, `--workers`
  (aliases `["--workers", "-w"]`), and `--json` entries mirroring the real manifest shape (same
  pattern already used for the mock `refresh_validations.py` entry).

## Webapp UI: `webapp/src/commandParse.ts`

- Add a new fallback constant `REGISTERED_OPTIONS_STRATEGIES` (mirrors `REGISTERED_STRATEGIES`'s
  existing role/doc-comment exactly, sourced from `STANDARD_OPTIONS_STRATEGIES`): `["Put Credit
  Spread", "Call Credit Spread", "Iron Condor", "Bull Call Spread", "Bear Put Spread", "Long
  Straddle"]`.

## Webapp UI: `webapp/src/components/CommandFormBuilder.tsx`

Key constraint found while reading the existing test suite: an existing pinned test
(`"does NOT affect the singular --strategy select on validation.harness"`,
`CommandFormBuilder.test.tsx`) asserts the **singular** `--strategy` control on
`validation.harness` keeps showing the equity `strategyRegistry`/`REGISTERED_STRATEGIES`
list (today's existing, admittedly-imperfect behavior, out of scope to fix here). So the new
options-strategy list must be threaded in as an **additional**, separate value — never by
making `effectiveStrategies` itself command-aware, which would also silently change the
singular control.

- Add a new prop `optionsStrategyRegistry?: string[]` (default `[]`) alongside the existing
  `strategyRegistry?: string[]` prop.
- Add a second computed list, `effectiveOptionsStrategies`, mirroring `effectiveStrategies`'s
  existing `useMemo` exactly but falling back to `REGISTERED_OPTIONS_STRATEGIES`.
- Add a small helper `registryForOption(optName: string)`: returns `effectiveOptionsStrategies`
  when `optName === "--strategies" && command.name === "validation.harness"`, else
  `effectiveStrategies` — i.e. the only new branching point, applied at exactly the three
  existing call sites that currently read `effectiveStrategies` unconditionally:
  1. The initial `optionValues` default-value block (`--strategies` case).
  2. The `<OptionFormControl strategyRegistry={...} />` render call.
  3. The Reset handler's `--strategies` case.
- No changes to `OptionFormControl`/`StrategyMultiSelectControl` internals — both are already
  fully generic over whatever `strategies: string[]` they're handed; the isMultiStrategy /
  isSingleStrategy branching by option name is untouched.

## Webapp UI: `webapp/src/screens/Commands.tsx`

- Pass the new `optionsStrategyRegistry={data?.options_strategy_registry ?? []}` prop through
  to `<CommandFormBuilder>` alongside the existing `strategyRegistry` prop.
- Add a second bulk entry point next to the existing "🧪 Bulk Validate All Strategies" button,
  following the exact same guarded pattern (`bulkValidateCommand`/its existence check):
  `bulkValidateOptionsCommand = data?.commands.find((c) => c.name === "validation.harness") ??
  null`, rendered as a second `<Button variant="primary" onClick={() =>
  setBuilderCommand(bulkValidateOptionsCommand)}>🧪 Bulk Validate Options Strategies</Button>`
  next to the first, only when non-null.

## Tests

- `tests/test_harness_bulk_cli.py` (new, mirrors `tests/test_command_manifest_freshness.py`'s
  and `tests/test_refresh_validations.py`'s conventions): unknown-name rejection (exit code 2,
  nothing run — assert via a monkeypatched `run_options_validation` that's never called);
  valid multi-name bulk run with a stubbed `run_options_validation` (avoid real network/backtest
  cost) confirms all names are invoked, summary/JSON output shape, and the aggregate exit code
  reflects `deployable` across all results; one-failure-doesn't-abort-batch (a raised exception
  for one name still lets the others complete and shows up as `"error"` in the JSON output).
- `tests/test_build_command_manifest.py`: extend with the `_fetch_options_strategy_registry`
  sibling-test set (mirrors every existing `_fetch_strategy_registry` test: real invocation,
  timeout, nonzero exit, empty stdout, unparseable stdout, wrong shape).
- `tests/test_command_manifest_freshness.py`: add
  `test_manifest_options_strategy_registry_matches_live_registry_exactly`, comparing the
  committed manifest's `options_strategy_registry` against
  `sorted(STANDARD_OPTIONS_STRATEGIES.keys())`, mirroring the existing STRATEGY_REGISTRY test.
- `webapp/src/commandParse.test.ts` (if a matching test file/section exists for
  `REGISTERED_STRATEGIES`): mirror for `REGISTERED_OPTIONS_STRATEGIES`.
- `webapp/src/components/CommandFormBuilder.test.tsx`: new `describe` block for
  `validation.harness`'s new `--strategies` control (parallel structure to the existing
  `refresh_validations.py` one: defaults to all options-strategies selected, Clear/Select-All,
  falls back to `REGISTERED_OPTIONS_STRATEGIES` with no prop) — plus an explicit regression
  assertion re-running the existing "singular --strategy unaffected" test to confirm it still
  passes unchanged.
- `webapp/src/screens/Commands.test.tsx`: extend the existing "Bulk Validate All Strategies
  button" describe block with the parallel case for the new "Bulk Validate Options Strategies"
  button (renders only when `validation.harness` is present in the manifest; opens Form Mode
  for that command on click).

## Documentation (CLAUDE.md-mandated step for this tier of change)

- `docs/architecture/validation-and-signals.md`: extend the existing `validation/harness.py`
  entry describing the new `--strategies`/`--workers`/`--json` bulk mode, the options-only
  scope decision and why (mirrors the reasoning already documented for
  `refresh_validations.py`'s `--strategies`), and the new `options_strategy_registry` manifest
  field.
- `docs/architecture/webapp-and-gui.md`: extend the existing "Commands screen strategy-registry
  sync (2026-08)" bullet (or add an adjacent dated bullet) noting the new
  `options_strategy_registry` field and the second bulk-validate entry point — this directly
  closes part of that bullet's own previously-disclosed gap ("the options-strategy Title-Case
  namespace... remains a separate, un-synced... gap").
- `docs/HOW_TO_GUIDE.md` / `docs/GO_LIVE_CHECKLIST.md`: no changes needed — the existing
  single-`--strategy` invocations they document remain valid and unchanged.

## Verification

- `pytest tests/test_harness_bulk_cli.py tests/test_build_command_manifest.py
  tests/test_command_manifest_freshness.py -q` plus the pre-existing `tests/test_harness_*.py`/
  `tests/test_options_harness.py` files (confirm no regression to the untouched single-strategy
  path).
- `python -m validation.harness --strategies "Iron Condor,Put Credit Spread" --workers 2 --json`
  run manually against real data (or at minimum `--strategies "Not A Real Strategy"` to confirm
  the fail-closed rejection path) as a smoke check before committing the regenerated manifest.
- `python scripts/build_command_manifest.py` re-run, diff `cli_introspect/command_manifest.json`
  to confirm only the expected new fields/options appear (no accidental drift elsewhere).
- `npm run --prefix webapp typecheck` (webapp/src changes).
- `npm run --prefix webapp test -- CommandFormBuilder Commands` (or full `npm run --prefix
  webapp test` if scoping proves awkward) plus, per CLAUDE.md, an actual `npm run dev` +
  browser check of the Commands screen: both bulk buttons render, the new multi-select shows
  the 6 options-strategy names pre-selected, Select All/Clear work, and the composed command
  string is correct.
- Branch: `validation-harness-bulk-options-mode` (or similar lowercase-kebab name), PR per
  CLAUDE.md's "Everything else" tier, with `.claude/validation_harness_bulk_*` plan/task/
  walkthrough artifacts per the PR-artifact naming convention.
