# Execution-mode store override: implementation plan

This is point 3 of `.claude/advisory_only_cowrite_fix_implementation_plan.md`. The operator
approved it on 2026-10-05.

## Problem
Precedence is shell env, then the runtime-flags store, then `.env`. `PUT
/automation/execution-mode` wrote `.env` only, so a stored `ADVISORY_ONLY=false` silently
overrode the "Advisory Only" button and the quarantine did not engage. The endpoint still
reported "Execution mode updated."

## Change
1. `shared/strategy_registry.py`: add a pure `mode_env_values(mode)` returning
   `{DRY_RUN, PAPER_TRADING}`. `set_active_mode` uses it, so the endpoint's store values cannot
   drift from what `set_active_mode` writes to `.env`.
2. `api/pilots_api.py::update_execution_mode`: the typed confirmation and the `.env` write are
   unchanged. Then `_apply_execution_mode_to_store` calls `write_override` for every key the
   endpoint set, with actor `pilots_api:execution_mode`. The response adds:
   - `per_key_applies`: the writer's real per-key verdict.
   - `ok`.
   - `quarantine_engaged`, which fails closed.
   - `store_conflict`: `{keys, reasons, message}`.
   - An honest `note`.
   `applies` is `immediately` only when every key is live and `ok`. The endpoint never raises.
3. Webapp:
   - `types.ts`: the new response fields.
   - `mock.ts`: the in-force shape, kept in parity.
   - `SettingsGeneral.tsx`: show the success toast only when `ok`. Otherwise show an error
     toast and a persistent `execution-mode-store-conflict` warning. The last result is held
     in the parent component so it survives the post-save reload.
4. Tests:
   - `tests/test_pilots_api.py::TestExecutionModeStoreOverride`:
     - Regression: store seeded with `ADVISORY_ONLY=false`, then a confirmed "advisory" press,
       gives effective `True` after a fresh runtime-flags apply.
     - Non-advisory modes write their key pair to the store.
     - A writer refusal and a writer exception both report the quarantine NOT engaged.
     - A disagreeing env-pinned value is a conflict.
   - Update the existing happy-path test for the new response.
   - Vitest in `SettingsGeneral.test.tsx`: the warning renders and survives the reload; no
     warning when the change is in force.
5. Docs:
   - CLAUDE.md rule, with AGENTS.md identical: a `.env` write of a key must also update or
     clear that key's runtime-flags store override.
   - `docs/architecture/observability-and-apis.md` and `docs/architecture/execution.md`.
   - `docs/RUNBOOK.md` §3.17.
   - Update the known-issue write-up.
   - Regenerate the census and liveness artifacts.

## Out of scope
- The one-time cleanup of the test-planted live store entries. It still needs operator
  sign-off.
