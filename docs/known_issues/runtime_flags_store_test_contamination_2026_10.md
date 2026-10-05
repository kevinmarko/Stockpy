# The test suite wrote the live runtime-flags store (ADVISORY_ONLY co-writes), 2026-09 to 2026-10

**Status:** Fixed for the test-suite leak: the root `conftest.py` fixture, the writer backstop,
and value-free audit provenance. **Open:** the execution-mode endpoint does not update the
store (see "Latent fail-open" below). That fix waits on operator sign-off, and so does the
one-time cleanup of the two test-planted store entries.

## Symptom

`~/.stockpy_local/output/runtime_flags_audit.jsonl` showed 119 `ADVISORY_ONLY` writes and 134
`SECTOR_HEAT_ENABLED` writes by actor `pilots_api`. `ADVISORY_ONLY` is a `DANGEROUS_KEYS`
member, the execution quarantine. Since 2026-09-27T19:11Z every `ADVISORY_ONLY` write sat
1 to 110 ms from a `SECTOR_HEAT_ENABLED` write. The order varied, and the timestamps were
occasionally out of append order. Pairs arrived every few minutes at all hours. The operator
had not changed execution modes. The audit records no values by design, so it could not show
whether anything flipped.

## Root cause

`tests/test_settings_reference.py` drove `PUT /settings/reference` through the real endpoint:

- `test_ordinary_boolean_write_succeeds` sent `SECTOR_HEAT_ENABLED=true`.
- `test_dangerous_field_succeeds_with_correct_confirmation` sent `ADVISORY_ONLY=false` with
  the correct confirmation.

Its helper mocked only `env_io.write_many_atomic`, the `.env` write.
`api/pilots_api.py::_validate_and_write_payload` (default `actor="pilots_api"`) then called
`_apply_live_overrides`. That calls the real `runtime_flags_writer.write_override` for every
`live_safe` key, with no `path=`. The path resolved to `runtime_flags.DEFAULT_STORE_PATH`,
which is the machine-global store the live daemon reads and every worktree shares.

`tests/test_pilots_api_tunables.py` had a file-local isolation fixture for exactly this.
`test_settings_reference.py` did not, and the root `conftest.py` had no store isolation.

`make ci`'s `pytest -n auto` put the two tests on different xdist workers. That explains the
racing pair and the out-of-order timestamps.

Timeline:

- **2026-09-07** — the tests arrive in #1014. From then, only `SECTOR_HEAT_ENABLED` leaked.
- **2026-09-27** — #1057 (`7106d5ef`) deleted the Streamlit app and regenerated
  `docs/settings_liveness.json`. `ADVISORY_ONLY`'s only restart-required capture site was in
  the deleted app, so it became `live_safe`, and `_apply_live_overrides` started writing it to
  the store too.

Reproduced 2026-10-05: running the two tests with `INVESTYO_RUNTIME_FLAGS_PATH` pointed at a
scratch file produced the exact production audit signature in the scratch audit log.

## Did anything flip?

No. The tests always wrote `ADVISORY_ONLY=false` and `SECTOR_HEAT_ENABLED=true`, which match
the operator's `.env`, so the effective value never changed.

Friday 2026-10-02's missing `fmp_paper: skipping broker reconciliation` log lines are
unrelated. #1096 (`bb6ca803`) deleted that log line, and the daemon picked up the new code
when it restarted at 09:29 that morning. No Friday cycle logged the quarantine messages
(`ADVISORY_ONLY=True — broker execution surface is quarantined` /
`📋 ADVISORY_ONLY=True`), which the quarantine always emits.

## Latent fail-open (open, follow-up)

Precedence is shell env, then the store, then `.env`. `PUT /automation/execution-mode`,
behind the Settings > Execution Mode "Advisory Only" button, writes `.env` only. With a stored
`ADVISORY_ONLY=false`, which every test run re-planted, pressing "Advisory Only" sets `.env`
to `true`. The store's `false` overrides it on the next daemon start or runtime-flags refresh,
so **the quarantine would silently not engage**. The fix is for that endpoint to also update
the store, plus a one-time removal of the two test-planted entries. Both need operator
sign-off and are not in this change.

## Fix

1. The root `conftest.py` autouse fixture `_isolate_runtime_flags_store_in_tests` points
   `INVESTYO_RUNTIME_FLAGS_PATH` at a per-test temp file. The audit log is a sibling of the
   store, so it follows. The duplicate file-local fixture in `test_pilots_api_tunables.py` was
   removed.
2. Backstop in `runtime_flags_writer`: `write_override`/`delete_override` refuse when
   `"pytest" in sys.modules` and the resolved target is `DEFAULT_STORE_PATH`. Nothing is
   written, not even an audit line, and the refusal is logged at ERROR. The check uses
   `sys.modules`, not the environment, so the module stays inside the `os.environ` allowlist
   that `tests/test_measure_settings_census.py` enforces. It is inert outside pytest, and a
   fresh-interpreter test proves that.
3. Value-free audit provenance. Every record now has `pid` and `process` (basename of
   `sys.argv[0]`) and `previous_present`. A successful write also records `changed`: true or
   false, or null when there was no prior entry. The audit still never contains a value.

Tests: `tests/test_runtime_flags_test_isolation.py` (new) and
`tests/test_runtime_flags_writer.py::TestAuditChangeTracking`.

## Lesson

Same class as [`pr872_live_db_test_contamination_2026.md`](pr872_live_db_test_contamination_2026.md):
an implicit-default store that production code reaches through a widely called path needs
suite-wide isolation at its own boundary. Leaving it to each test file to remember fails. A
file-local fixture in one test file protected that file only.
