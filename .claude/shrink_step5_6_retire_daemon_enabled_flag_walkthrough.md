# Step 5.6 walkthrough: retire `ORCHESTRATOR_DAEMON_ENABLED`

Plan: `.claude/shrink_step5_6_retire_daemon_enabled_flag_implementation_plan.md` (operator-approved 2026-10-12).
Parent: `.claude/shrink_step5_retire_main_py_implementation_plan.md` §4 (5.6). **Stacked on 5.5 (#1124);
merges after it.**

## Live impact
None for this deployment. The live `.env` already had `ORCHESTRATOR_DAEMON_ENABLED=true` and the
runtime-flags store has no override for it. The leftover `.env` line is ignored (`Settings` uses
`extra="ignore"`); checked read-only by loading `Settings(_env_file=<live .env>)`, which loads and has
no such attribute.

## What changed
- `shared/orchestrator_runner.py::launch_orchestrator`: the daemon fast path runs for every non-dry run;
  the subprocess fallback is unchanged (daemon unreachable, trigger rejected, exception, or `dry_run`).
  `launch_daemon_engine` and `DAEMON_LOG_PATH` removed (no callers since the desktop shell was deleted).
- `launch_webapp.command`: when neither `:8601` nor `:8602` answers and no daemon PID exists, it always
  starts the real daemon (the flag-off "two bare stub APIs" branch is gone). The reuse branches above it
  are unchanged; live, `com.investyo.stack` already holds both ports.
- Field removed from `settings.py`, `shared/env_io.py::ALLOWED_KEYS`, the pilots_api "Advanced / Config"
  tunables group, `.env.example`, and both webapp mock lists. Passing mentions in other field
  descriptions (`PILOTS_API_ENABLED`, `SENTIMENT_INGESTION_ENABLED`, `WEEKLY_DIGEST_ENABLED`, a sizing
  comment) reworded.
- **New root-`conftest.py` fixture `_isolate_daemon_control_api_in_tests`** sets
  `settings.ORCHESTRATOR_API_PORT = 1`. Without it, any test reaching an unpatched `launch_orchestrator()`
  would now `POST /run` to whatever listens on 8601, which on the operator's machine is the live daemon
  (confirmed: `/health` answered 200 during this work). A throwaway probe test showed
  `daemon_available()` is False under the fixture.
- Tests: cutover tests retargeted (flag cases → fallback cases; `launch_daemon_engine` tests deleted),
  a guard that the field and allowlist entry can't return, `test_settings_keysets` size pins
  (`_TUNABLE_INDEX` 90 → 89, editor keys 162 → 161), `SettingsReference.test.tsx` uses
  `ORCHESTRATOR_EXTENDED_HOURS_ONLY` as its sample writable boolean.
- Docs: CLAUDE.md/AGENTS.md status bullet, HOW_TO_GUIDE, RUNBOOK §0, `docs/architecture/webapp-and-gui.md`,
  `docs/architecture/data-layer.md`, FEATURE_TIER_HISTORY, parent plan status. Dated
  `docs/known_issues/*` write-ups and older `.claude/*` plans keep their mentions as history.

## Verification
- `make ci`: 11,892 passed, 24 skipped, 0 failed (first run caught the `test_settings_keysets` size pin).
- Targeted runner/control/daemon-client/env_io/runtime-flags/tunables tests: 542 passed.
- Census/liveness/keysets/agent-docs/archive-smoke: 86 passed.
- Webapp: `typecheck` clean; vitest 153 files / 1,814 tests passed; `/settings/tunables` and
  `/settings/reference` rendered in mock mode on a scratch port, key absent. Console showed only CORS
  errors from components calling live `:8602` from the non-standard origin (unrelated).
- `bash -n launch_webapp.command` clean.

## Not verified / disclosed
- `launch_webapp.command` was not run end to end (it would start or reuse live backends).
- Pre-existing, not changed: when the daemon answers `409 already_running`, `launch_orchestrator` falls
  back to spawning a `main_orchestrator.py` subprocess rather than attaching to the running cycle, so two
  cycles can run at once. It behaved this way with the flag on, which was the live setting.
