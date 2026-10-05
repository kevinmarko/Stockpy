# ADVISORY_ONLY co-write with SECTOR_HEAT_ENABLED: implementation plan

Status: Phase 1 (root cause and plan). No code has been changed yet.
Tier: "Everything else" (it touches the safety-critical settings write path and the test
harness that guards the live store), so this goes through a branch and a PR
(`fix-runtime-flags-test-contamination`). It is a bug fix, so the feature freeze allows it.

## 1. Symptom

`~/.stockpy_local/output/runtime_flags_audit.jsonl` holds 119 `ADVISORY_ONLY` writes and 134
`SECTOR_HEAT_ENABLED` writes, all by actor `pilots_api`. Since 2026-09-27T19:11Z every
`ADVISORY_ONLY` write sits 1 to 110 ms from a `SECTOR_HEAT_ENABLED` write. The order of the
pair varies, and once (2026-09-28T20:01:24) the timestamps run out of append order. Pairs
arrive every 2 to 10 minutes at all hours, including 00:00 to 09:00 UTC. The operator says
they did not change execution modes.

## 2. Root cause: the pytest suite writes to the live runtime-flags store

The webapp did not cause this. Two tests in `tests/test_settings_reference.py` do it whenever
the suite runs in any worktree:

| Test | Request | What reaches the live store |
|---|---|---|
| `TestSettingsReferenceWrite::test_ordinary_boolean_write_succeeds` (line 293) | `PUT /settings/reference {"SECTOR_HEAT_ENABLED": true}` | `SECTOR_HEAT_ENABLED = true` |
| `TestSettingsReferenceWrite::test_dangerous_field_succeeds_with_correct_confirmation` (line 352) | `PUT /settings/reference {"ADVISORY_ONLY": false}` with `confirm={"ADVISORY_ONLY": "ADVISORY_ONLY"}` | `ADVISORY_ONLY = false` |

How the write gets through:

1. The helper `_put_reference` in `tests/test_settings_reference.py` (lines 62-77) mocks only
   `pilots_api.env_io.write_many_atomic`, which is the `.env` write.
2. `api/pilots_api.py::_validate_and_write_payload` (line 4344, default `actor="pilots_api"`)
   then calls `_apply_live_overrides` (line 4496). For every `live_safe` key that function calls
   the real `runtime_flags_writer.write_override(key, value, actor=actor)` (line 4590) with no
   `path=`.
3. `runtime_flags.store_path()` (line 229) resolves to `DEFAULT_STORE_PATH`
   (`LOCAL_DATA_ROOT/output/runtime_flags.json`). Every worktree shares that machine-wide path.
   Only `INVESTYO_RUNTIME_FLAGS_PATH` redirects it.
4. `tests/test_pilots_api_tunables.py` already has a file-local autouse fixture
   (`_isolated_runtime_flags_store`, line 58) that sets that env var. Its docstring describes
   this exact hazard. `tests/test_settings_reference.py` has no such fixture, and the root
   `conftest.py` has no session-wide store isolation, unlike the DB-store fixtures
   (`_isolate_validation_runs_db_in_tests`, ...) that CLAUDE.md requires.

Why the writes became a pair on 2026-09-27: PR #1057 (`7106d5ef`, "delete the retired Streamlit
desktop app") regenerated `docs/settings_liveness.json`. That moved `ADVISORY_ONLY` from
`restart_required` to `live_safe`, because its only restart-required capture site was the
deleted app. Before #1057, `_apply_live_overrides` returned `next_daemon_restart` for
`ADVISORY_ONLY` without calling the writer, so only `SECTOR_HEAT_ENABLED` leaked. Every audit
entry from 2026-09-07 to 2026-09-27 is `SECTOR_HEAT_ENABLED` alone. The tests themselves arrived
in #1014 on 2026-09-07, the date of the first leaked write. The first paired write
(19:11:57Z on 09-27) came about 20 minutes before #1057 merged (19:34Z), which fits that
branch's own pre-merge test runs, since the store is shared.

The varying order and the out-of-order timestamps come from `make ci` running
`pytest -n auto`: the two tests land on different xdist workers and race.

**Reproduced.** I ran `INVESTYO_RUNTIME_FLAGS_PATH=<scratch>/runtime_flags.json pytest
tests/test_settings_reference.py -k TestSettingsReferenceWrite` (9 passed). It left exactly the
production signature in the scratch audit: `SECTOR_HEAT_ENABLED` then `ADVISORY_ONLY` 13 ms
apart, `actor: pilots_api`, `applies: immediately`. The scratch store held
`ADVISORY_ONLY=false` and `SECTOR_HEAT_ENABLED=true`, matching the live store byte for byte in
shape.

## 3. Was a value ever flipped?

- The tests always write `ADVISORY_ONLY=false` and `SECTOR_HEAT_ENABLED=true`. The live `.env`
  holds `ADVISORY_ONLY=false` and `SECTOR_HEAT_ENABLED=true`, and the live store holds the same
  two values (last `updated_by: pilots_api`, 2026-10-05T09:04:34Z). So the effective value has
  not differed from the operator's intent, and nothing flipped in practice.
- **Friday 2026-10-02 is unrelated.** The log line `fmp_paper: skipping broker reconciliation`
  was deleted from `main_orchestrator._execute_broker_orders` by #1096 (`bb6ca803`, merged Thu
  2026-10-01 11:57 EDT; see `git show bb6ca803`, line 6053 of that diff). The daemon kept the
  old code until it restarted Fri 09:29:32, which is why Thursday still logged the line hourly.
  On Friday no cycle logged `ADVISORY_ONLY=True — broker execution surface is quarantined` or
  `📋 ADVISORY_ONLY=True`, both of which the quarantine always emits
  (`main_orchestrator.py:534`, `pipeline/production_steps.py:3245`). So `ADVISORY_ONLY` was
  `False` during those cycles, and the missing line comes from the code change.
- **Latent safety bug (the real risk).** Precedence is shell env > store > `.env`
  (`runtime_flags.apply_overrides`, line 351 onward; only a real shell export beats the store).
  `PUT /automation/execution-mode` (`api/pilots_api.py:3800-3850`), behind the Settings ->
  Execution Mode "Advisory Only" button, writes `ADVISORY_ONLY` to `.env` only, through
  `env_io.write_setting`. It never touches the store. Because every test run re-plants
  `ADVISORY_ONLY=false` in the store, an operator who presses "Advisory Only" to quarantine
  execution gets `.env=true`, which the store's `false` overrides on the next daemon start or
  runtime-flags refresh. **The quarantine would silently fail to engage.** The endpoint also
  reports `"Execution mode updated."` and `applies: next_daemon_restart`, both false here.
  This fails open on the highest-consequence switch in the settings surface. It is also
  reachable without the tests, because any earlier Feature Flags or Reference save of
  `ADVISORY_ONLY` leaves a store entry that the mode button then cannot override.

## 4. The client is not at fault (checked)

- `webapp/src/components/GenericSettingsEditor.tsx`, used by FeatureFlags, Sentiment, Reference
  and the others, sends only dirty keys. `dirtyKeys` (lines 329-335) feeds `buildPayload`
  (lines 349-359), and a dangerous key forces `DangerousConfirmDialog`, which requires typing
  the name.
- `ExecutionModeSection` (`webapp/src/screens/SettingsGeneral.tsx:120+`) writes only on
  explicit button, typed confirmation and submit. Nothing writes on mount.
- `SECTOR_HEAT_ENABLED` (`_SENTIMENT_GROUPS`, line 4687) and `ADVISORY_ONLY` (`_TUNABLE_GROUPS`
  "Runtime & Ops", line 4043) share only the Feature Flags and Reference scopes, and a UI save
  there still needs typed confirmation. Unattended pairs every few minutes at 02:00 UTC do not
  fit a human. The reproduction settles it.

No webapp code change is needed. A vitest regression pin is still worth adding (section 6).

## 5. Proposed fix

### 5.1 Stop the leak: session-wide store isolation (primary fix)
- Root `conftest.py`: add an autouse fixture `_isolate_runtime_flags_store_in_tests(monkeypatch,
  tmp_path)` that sets `runtime_flags.PATH_OVERRIDE_ENV_VAR` to a per-test tmp file. This
  follows the existing `_isolate_*_in_tests` pattern. The audit log is a sibling of the store
  (`runtime_flags_writer.audit_path`), so it is redirected too.
- Remove the now-redundant file-local `_isolated_runtime_flags_store` from
  `tests/test_pilots_api_tunables.py`, or keep it as a harmless duplicate with a pointer comment.
  Removing it is preferred.
- `test_runtime_flags*.py` tests that pass explicit `path=` keep working unchanged.

### 5.2 Defense in depth: the writer refuses the default store under pytest
In `runtime_flags_writer._write_override_inner` and `_delete_override_inner`: if the resolved
path equals `runtime_flags.DEFAULT_STORE_PATH` and `"PYTEST_CURRENT_TEST" in os.environ`, return
a `refused` result with reason `default_store_under_test`, write nothing, and log at ERROR. This
mirrors the `.env` hard-deny hook for the live store and catches the next new test file that
forgets isolation, even if someone disables the conftest fixture.
- `runtime_flags_writer.py` already reads `os.environ` (via `runtime_flags.store_path`). Confirm
  whether `tests/test_measure_settings_census.py::TestFormDOsEnvironIsFullyAllowlisted` scans
  this module. If it does, add `PYTEST_CURRENT_TEST` to its allowlist with a comment explaining
  why. The fallback is `"pytest" in sys.modules`, which needs no env read; that is the preferred
  choice if the census test objects.

### 5.3 Make the execution-mode button authoritative over the store (the safety bug)
In `update_execution_mode` (`api/pilots_api.py`), after the `.env` write, also call
`runtime_flags_writer.write_override(...)` for each written key (`ADVISORY_ONLY`, plus
`DRY_RUN`/`PAPER_TRADING` when written) that currently has a store entry, or simply for
`ADVISORY_ONLY` always. Use `actor="pilots_api:execution_mode"`, so the store can never hold a
stale value that defeats the mode the operator just picked. Report the per-key `applies`
honestly, as `_apply_live_overrides` does, instead of the fixed `"next_daemon_restart"`. If the
store write is refused or fails while the store still holds a differing value, return the
response with an explicit `store_conflict` warning rather than `"Execution mode updated."`
(CONSTRAINT #4: never claim a state that is not in force).
- Alternative, smaller and equally safe: `delete_override(key)` for those keys, so `.env`
  becomes the sole layer. Recommend `write_override`, because it also applies live with no
  restart, which is what an operator pressing "Advisory Only" expects.
- `shared/strategy_registry.set_active_mode` (writes `DRY_RUN`/`PAPER_TRADING`) gets the same
  treatment through the endpoint, not inside the registry.

### 5.4 Make the audit able to answer "did it flip?" without logging values
`runtime_flags_writer._append_audit`: add
- `changed`: `true`/`false`, computed by comparing the validated new value to the store's prior
  entry, or `null` when there was no prior entry. It is a boolean, so no value leaks; the
  writer already refuses `SECRET_KEYS`.
- `previous_present`: bool.
- `pid` and `process` (basename of `sys.argv[0]`, for example `pytest`, `uvicorn`,
  `orchestrator_daemon`). This would have pointed at pytest on day one.

The "audit never contains the value" guarantee and its test stay intact. Add one assertion that
the new fields are booleans or a pid/basename only.

### 5.5 One-time live remediation (operator's call; not done by the agent)
The live store holds test-planted `ADVISORY_ONLY=false` and `SECTOR_HEAT_ENABLED=true`. Both
equal `.env` today, so deleting them changes nothing now, and removes the trap described in
section 3. The plan is to give the operator the exact command
(`python -c "from runtime_flags_writer import delete_override; delete_override('ADVISORY_ONLY',
actor='operator cleanup: test-planted'); ..."`) and run it only on the operator's explicit OK.
Remove it from `docs/RUNBOOK.md`-style steps afterwards.

## 6. Tests to add

pytest:
- `tests/test_runtime_flags_test_isolation.py` (new, project-scoped name):
  - `runtime_flags.store_path()` inside any test is not `DEFAULT_STORE_PATH` (canary for the
    conftest fixture).
  - Regression: drive `PUT /settings/reference {"ADVISORY_ONLY": false}` with confirmation
    through the real endpoint and assert the live `DEFAULT_STORE_PATH` file's mtime and content
    are untouched (read-only stat, never written), and that the tmp store received the write.
  - Writer guard: with `PYTEST_CURRENT_TEST` set and `path=DEFAULT_STORE_PATH`,
    `write_override`/`delete_override` return `refused` / `default_store_under_test` and do not
    create or modify the file. Use a monkeypatched `DEFAULT_STORE_PATH` pointing at tmp so the
    test itself never touches the real one.
- `tests/test_runtime_flags_writer.py`: the audit gains `changed`/`previous_present`/`pid`/
  `process`, and `changed` is correct for same-value, different-value and first-write cases. The
  existing "value never in audit" test is extended to the new fields.
- `tests/test_pilots_api.py` (execution-mode block):
  - With a tmp store pre-seeded `ADVISORY_ONLY=false`, `PUT /automation/execution-mode`
    `mode=advisory` (confirmed) leaves the store at `ADVISORY_ONLY=true` and the live settings
    singleton at `True`. This is the fail-open regression.
  - A writer refusal surfaces `store_conflict` and does not claim success.
  - The unconfirmed path still writes nothing, to neither `.env` nor the store.
- `tests/test_settings_reference.py`: no behavior change; it is now isolated by conftest. Add a
  module docstring note.

vitest (`webapp/src/screens/FeatureFlagsScreen.test.tsx`):
- Pin the exonerated path: with `ADVISORY_ONLY` and `SECTOR_HEAT_ENABLED` both rendered,
  toggling only `SECTOR_HEAT_ENABLED` and saving calls `updateFeatureFlags` with exactly
  `{SECTOR_HEAT_ENABLED: <v>}` and `confirm={}`, with no confirm dialog.
- `SettingsGeneral.test.tsx`: if 5.3 changes the response shape (`store_conflict`, per-key
  `applies`), update `types.ts`/`client.ts`/`mock.ts` in parity, add an honest mock fixture for
  the conflict shape, and assert the warning renders instead of the success toast. Then
  `npm run --prefix webapp typecheck` and `/verify-webapp` (UI-visible), plus the
  `api-parity-reviewer` agent.

Gates: targeted pytest files above, then `make ci`.

## 7. Documentation updates (part of the deliverable)
- New `docs/known_issues/runtime_flags_store_test_contamination_2026_10.md`: symptom, the audit
  signature, root cause (the unisolated test plus the #1057 liveness flip), the latent
  fail-open on the Advisory Only button, why Friday 10-02 is unrelated (#1096 log removal),
  fix, status, and remediation. Add it to `docs/known_issues/README.md`, and cross-link from
  `docs/known_issues/pr872_live_db_test_contamination_2026.md` (same class).
- `CLAUDE.md` (the `AGENTS.md` mirror syncs through the hook): add
  `_isolate_runtime_flags_store_in_tests` to the "Test isolation for implicit-default stores"
  fixture list, and add one sentence to the Runtime flags store bullet: "any write path that
  writes a key to `.env` must also update or clear that key's store override, or the store
  silently wins."
- `docs/architecture/testing.md`: the new conftest fixture and the new test file.
- `docs/architecture/observability-and-apis.md`: the execution-mode endpoint now writes the
  store, and its response shape changes.
- `runtime_flags_writer.py` module docstring ("The audit log" section): the new audit fields and
  the pytest guard.
- `docs/RUNBOOK.md` (§3.x settings/runtime flags): how to inspect the store for overrides that
  shadow `.env`, and the one-time cleanup command.
- PR artifacts: `.claude/advisory_only_cowrite_fix_task.md` and `..._walkthrough.md`.

## 8. Risks
- 5.3 changes behavior of a DANGEROUS_KEYS write path. It moves toward the operator's explicit
  choice, but it is execution-adjacent, so it needs the operator's sign-off before merge.
- 5.2's pytest detection must not fire in production. `PYTEST_CURRENT_TEST` is set only by
  pytest; `sys.modules` detection could misfire if a production process ever imported pytest,
  which nothing does today. Pick one and test it.
- The conftest fixture must not break tests that intentionally read a seeded store through the
  env var. Those already set it per test, and the per-test monkeypatch layering keeps the later
  set winning.
- Until 5.1 merges, every `make ci` run in any worktree keeps rewriting the live store. The
  values are benign today, but the Advisory Only button is unreliable until 5.3 lands or the
  store entries are removed.
