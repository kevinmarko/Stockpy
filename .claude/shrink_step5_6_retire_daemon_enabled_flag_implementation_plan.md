# Step 5.6: retire `ORCHESTRATOR_DAEMON_ENABLED`

## Context
Last step of shrink step 5 (parent plan `.claude/shrink_step5_retire_main_py_implementation_plan.md` §4,
5.6). Since the 2026-10-12 cutover the daemon is the only runtime, so the flag that chose "daemon vs.
bare APIs / subprocess" has one meaningful value. The live `.env` already has
`ORCHESTRATOR_DAEMON_ENABLED=true` and the runtime-flags store has no override for it, so **nothing
changes at runtime** for this deployment. `Settings` uses `extra="ignore"` and `runtime_flags.apply_overrides`
skips unknown keys with a warning, so the leftover `.env` line stays harmless (no `.env` edit needed).

**Base and merge order:** branch `step5-6-retire-daemon-enabled-flag` **stacked on
`step5-5-archive-main-py`** (#1124). Both PRs regenerate the census/liveness files and edit the same
CLAUDE.md status bullet, so branching from `main` would guarantee conflicts. The PR targets #1124's
branch, then is retargeted to `main` after #1124 merges (after 2026-10-16). Not merged before #1124.

## Readers to change (code)
- `shared/orchestrator_runner.py::launch_orchestrator` (~317): drop the `settings.ORCHESTRATOR_DAEMON_ENABLED`
  condition, so the fast path runs for every non-dry run. **The subprocess fallback stays** (daemon
  unreachable, `trigger_run` fails, or `dry_run=True`), per the parent plan. Docstring + module comment
  (~84) updated.
- `shared/orchestrator_runner.py::launch_daemon_engine`: no callers since the desktop shell was deleted;
  it exists only for the flag's old "always-on loop" mode. Remove it, `DAEMON_ENGINE_LOG_PATH`-style
  constant at ~83 if unused elsewhere, and its tests (class 1 in `tests/test_orchestrator_runner_daemon_cutover.py`).
- `launch_webapp.command` (~497-512, header ~16-23, ~551): remove the flag read; when neither port is up,
  always start the real daemon. The "port already up → reuse, don't fight" branch above it is unchanged
  (that's what happens live, since `com.investyo.stack` keeps the daemon up). The `daemon_alive:false`
  hint at ~551 now says "start one: python -m desktop.orchestrator_daemon" only.
- `api/pilots_api.py:4187`: remove the key from the "Advanced / Config" tunables group.
- `shared/env_io.py:192` (`ALLOWED_KEYS`) + comment at ~690: remove.
- `settings.py`: delete the field (~2649) and its comment block (~2640); fix the passing mentions at
  ~472, ~1799, ~2970, ~5168 (each describes the daemon; reword to "the orchestrator daemon").
- Comments only: `api/_jobs.py:95`, `desktop/daemon_runtime.py:1254`, `api/control_api.py:540`.

## Config / webapp
- `.env.example:681`: remove the line and its comment.
- `webapp/src/api/mock.ts` (~2866 tunables entry, ~4772 settings-reference entry): remove.
- `webapp/src/screens/SettingsReference.test.tsx` uses the key only as a sample writable boolean: swap in
  another `allowed`, writable boolean that survives (`ORCHESTRATOR_EXTENDED_HOURS_ONLY`).
- Regenerate `docs/settings_field_census.{json,md}` and `docs/settings_liveness.json`
  (`scripts/measure_settings_census.py --write`, `scripts/settings_liveness.py --write`).

## Tests
- `tests/test_orchestrator_runner.py:204`, `tests/test_orchestrator_runner_daemon_cutover.py`: the
  flag-off cases become "daemon unreachable → subprocess fallback"; flag-on cases drop the monkeypatch;
  `TestLaunchDaemonCycle` loses its `flag` parametrize.
- `tests/test_pilots_api_tunables.py:122`: drop from `_NEW_ADVANCED_KEYS`.
- `tests/test_control_api.py:1037`: comment only.
- New guard in an existing test file (`tests/test_orchestrator_runner_daemon_cutover.py`): assert
  `"ORCHESTRATOR_DAEMON_ENABLED" not in Settings.model_fields` and not in `env_io.ALLOWED_KEYS`, so it
  can't creep back.

## Docs (same PR)
CLAUDE.md/AGENTS.md (status bullet: 5.6 done, step 5 complete; drop "Step 5.6 … is pending"),
`docs/architecture/webapp-and-gui.md` (orchestrator_runner + launcher paragraphs),
`docs/architecture/orchestration-entrypoints.md`, RUNBOOK and HOW_TO_GUIDE mentions (grep),
`docs/FEATURE_TIER_HISTORY.md` (new dated entry; the historical 2340 entry stays as history),
`legacy/README.md` not needed (nothing moved to legacy; removed outright, git history has it).
Parent plan status line. Plan/task/walkthrough as `.claude/shrink_step5_6_retire_daemon_enabled_flag_*.md`.
Historical `.claude/*` plans/walkthroughs that mention the flag are left as-is.

## Not in scope / disclosed
- Antigravity's in-progress F4 branch also edits `api/pilots_api.py`'s "Advanced / Config" group
  (`SECTOR_FORECAST_*`) and the census files; whichever lands second rebases (small, mechanical conflict).
- Draft PR #1122 (another session's 5.4b-ii docs follow-up) overlaps #1124's docs; untouched here.

## Verification
1. `git grep -n ORCHESTRATOR_DAEMON_ENABLED -- ':!legacy' ':!.claude' ':!docs/FEATURE_TIER_HISTORY.md'` → empty.
2. Targeted: `test_orchestrator_runner*.py`, `test_pilots_api_tunables.py`, `test_control_api.py`,
   `test_env_io*.py`, `test_runtime_flags.py`, `test_measure_settings_census.py`, `test_settings_liveness.py`,
   `test_agent_docs_sync.py`, `test_main_py_archive_import_smoke.py`.
3. `npm run --prefix webapp typecheck` + `npx vitest run src/screens/SettingsReference.test.tsx`; Settings
   Reference screen opened in the browser (mock mode), no console errors, key absent.
4. `bash -n launch_webapp.command`; dry read of the edited branch logic.
5. Live check (read-only): `python -c "from settings import settings"` with the real `.env` loads without
   error despite the leftover line; `apply_overrides` unaffected (no store key).
6. `make ci` zero failures on the final tree.
