# ADVISORY_ONLY co-write fix: walkthrough

## What was wrong

`tests/test_settings_reference.py` PUT `SECTOR_HEAT_ENABLED=true` and a confirmed
`ADVISORY_ONLY=false` through the real `/settings/reference` endpoint with only the `.env`
write mocked. `_apply_live_overrides` then called the real `write_override`, which wrote the
machine-global `~/.stockpy_local/output/runtime_flags.json` on every suite run in every
worktree, under actor `pilots_api`. This produced the paired audit writes. I reproduced it into
a scratch store. Full analysis is in
`docs/known_issues/runtime_flags_store_test_contamination_2026_10.md`.

## What changed

- `conftest.py`: `_isolate_runtime_flags_store_in_tests` (autouse) sets
  `INVESTYO_RUNTIME_FLAGS_PATH` to a per-test temp file. The audit log follows the store.
- `runtime_flags_writer.py`:
  - Gate 0 in both write and delete refuses the resolved `DEFAULT_STORE_PATH` when pytest is
    loaded. It writes nothing, including no audit line, and logs at ERROR.
  - `_append_audit` gained `pid`, `process`, `previous_present`, and `changed` on writes.
    `changed` is compared post-validation and read under the write lock. No value is ever
    recorded.
- Tests: see the task tracker.

## Verification (run this session)

- Targeted tests: runtime_flags, writer, isolation, settings_reference, tunables, census,
  agent-docs sync. All green after regenerating the census and liveness artifacts, which had
  line-number drift only.
- `ruff check --select=F821,F822,F823,E9 .`: all checks passed.
- `make ci PYTHON=/Users/kevinlee/Stockpy-live/.venv/bin/python3` with
  `INVESTYO_RUNTIME_FLAGS_PATH` unset: `11643 passed, 24 skipped`.
- Live audit log line count: 273 before, 273 after the full `make ci` run.
  - Between my runs the live audit still grew, for example 271 -> 273 at 14:19:12Z, with
    old-format records that have no `pid` field. That is other sessions running pre-fix code;
    the leak continues from any checkout that lacks this change.
  - No record in the live log carries the new `pid` field, so none came from this branch's
    writer.

## Not done (HOLD)

- The execution-mode endpoint does not yet update the store.
- The live store still holds the test-planted entries.

Both need the operator's OK.
