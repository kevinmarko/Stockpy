# Walkthrough: validation.harness bulk mode (mirroring refresh_validations.py)

## What changed and why

`scripts/refresh_validations.py` already has a real bulk mode — `--strategies
NAME[,NAME]` (default: everything in `STRATEGY_REGISTRY`) plus `--workers` —
wired to the webapp Commands screen's "🧪 Bulk Validate All Strategies"
button. `validation/harness.py`'s own CLI (`python -m validation.harness`)
had no bulk capability at all: only a single `--strategy`.

The two CLIs are NOT equivalent under the hood: for a non-options
`--strategy` name, `validation.harness` never touches `STRATEGY_REGISTRY` —
it always runs a placeholder Buy-and-Hold-SPY strategy. Only names in
`validation.options_harness.STANDARD_OPTIONS_STRATEGIES` (`Put Credit
Spread`, `Call Credit Spread`, `Iron Condor`, `Bull Call Spread`, `Bear Put
Spread`, `Long Straddle`) get real, strategy-specific results. Confirmed with
the operator: bulk mode is deliberately **options-strategies only** — a
non-options name is a hard `SystemExit(2)` error, not a silent re-run of the
same placeholder under N different labels.

## Python: `validation/harness.py`

- `--strategy` and the new `--strategies` are now a `required=True` mutually
  exclusive group — every existing documented single-strategy invocation
  (`docs/HOW_TO_GUIDE.md`, `GO_LIVE_CHECKLIST.md`, `RUNBOOK.md`) is
  byte-identical to before.
- New `--strategies` (comma-separated), `--workers`/`-w` (mirrors
  `refresh_validations.py`'s flag exactly), `--json` (same one-final-line
  convention).
- Bulk-mode flow: parse names → reject any name outside
  `STANDARD_OPTIONS_STRATEGIES` with a clear error listing the valid set
  (before running anything) → run each through the existing
  `StrategyValidationHarness.run_options_validation()` (sequential or via
  `ThreadPoolExecutor` on `--workers > 1`) → print a pass/fail summary table
  → optional JSON line → exit `0` iff every strategy is deployable, else `1`.
- A per-strategy exception is caught and shown as `ERROR` in the table/JSON
  rather than crashing the whole batch.

## A real, pre-existing bug found and fixed along the way

While smoke-testing the new bulk mode against real (synthetic-price, still
offline) data, `run_options_validation()` raised `TypeError:
StrategyValidationHarness.__init__() missing 2 required positional
arguments: 'universe_fn' and 'cost_model'` on **every single invocation**.
Confirmed via `git stash` that this bug already existed on `main` before any
of this PR's changes — the throwaway `harness_inst = cls(strategy_fn=...,
reports_dir=...)` used only to reuse three persistence methods
(`_write_json_summary`/`_append_validation_history`/
`_record_validation_run_to_db`) had simply forgotten two other
required-positional constructor args. Neither is actually read by those three
methods (they only touch `self.reports_dir` and the report object), so the
fix is harmless placeholders. This means the ONLY code path that ever gives a
real per-strategy options result had never actually completed via the CLI —
fixing it was necessary for the bulk-mode feature to produce anything but
errors, so it's included in this PR rather than filed as a separate follow-up.
A dedicated regression test class (`TestRunOptionsValidationConstructorRegression`
in `tests/test_harness_bulk_cli.py`) exercises the real function end-to-end;
verified it actually catches the bug by temporarily reverting the fix and
confirming both new tests go red, then restoring it.

## Manifest / backend plumbing

`scripts/build_command_manifest.py` gained a sibling of the existing
`_fetch_strategy_registry` — `_fetch_options_strategy_registry` (both now
share a `_fetch_registry_via_subprocess` helper) — sourcing
`sorted(STANDARD_OPTIONS_STRATEGIES.keys())` via the same isolated-subprocess,
dead-letter-to-`[]` pattern. The manifest gained a new top-level
`options_strategy_registry` field; regenerated
`cli_introspect/command_manifest.json` and diffed it (only the expected new
CLI options + field appeared). `pilots/commands.py::command_manifest()`
passes the new field through on both the success and degraded/empty paths.

## Webapp

- `CommandManifest` (types.ts) gained `options_strategy_registry?: string[]`;
  `mock.ts` got the matching fixture data + extended the mock
  `validation.harness` entry's options.
- `commandParse.ts` gained a `REGISTERED_OPTIONS_STRATEGIES` fallback constant
  (sibling of `REGISTERED_STRATEGIES`) — **deliberately not wired into the
  free-text Command Bar's autocomplete** (`valueSuggestions`), which still
  sources any `*strategy*`-named option from the equity registry regardless
  of command. This is a disclosed, out-of-scope gap (documented in both
  updated architecture docs), not an oversight — fixing it needs threading a
  second registry through `parseCommandLine` and was judged separate scope
  from wiring up the Form-Mode builder + bulk button.
- `CommandFormBuilder.tsx`: a key constraint discovered while reading the
  existing test suite — a pinned test asserts `validation.harness`'s
  **singular** `--strategy` control still shows the equity
  `strategyRegistry`/`REGISTERED_STRATEGIES` list (today's existing,
  admittedly-imperfect behavior, explicitly out of scope here). So the new
  options list is threaded in as a genuinely separate value
  (`effectiveOptionsStrategies`), picked only for `validation.harness`'s
  plural `--strategies` via a `registryForOption(optName)` helper — never
  touching the singular control. A regression test confirms this explicitly.
- `Commands.tsx` gained a second "🧪 Bulk Validate Options Strategies" button,
  guarded the same way the existing one is (only rendered when
  `validation.harness` is present in the manifest).

## Verification

- Full offline suite (`pytest -m "not network and not slow" -n auto --dist
  loadgroup`): 11745 passed, 31 skipped, 23 failed — every one of the 23
  confirmed pre-existing on `main` (unrelated: chat-provider environment
  deps, `test_run_once.py`/`test_pipeline_smoke.py`/
  `test_main_body_engine_injection.py`). Zero new failures from this change.
- `python -m ruff check . --select=F821,F822,F823,E9` — clean (the repo's
  actual CI lint gate, not the full default ruleset which has ~1200
  pre-existing style violations this repo doesn't enforce).
- Real end-to-end CLI smoke test against genuine backtest logic (synthetic
  price data, no network): two different options strategies produced two
  genuinely different Sharpe/PBO/DSR/MaxDD results, proving real per-strategy
  dispatch rather than a repeated placeholder.
- `npm run --prefix webapp typecheck` clean; full `npx vitest run` — 167 test
  files / 1817 tests passed.
