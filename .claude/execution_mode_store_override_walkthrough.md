# Execution-mode store override: walkthrough

## Behavior now
A confirmed mode change writes `.env`, exactly as before. It then writes the same keys to the
runtime-flags store through `write_override`, with actor `pilots_api:execution_mode`. That
store write is also applied to the serving process.

A key counts as effective only when two things hold:
- the writer returns `ok`, and
- the live value equals the requested value.

Anything else is listed in `store_conflict`, and the response comes back with `ok: false`.
`quarantine_engaged` is `true` only when `ADVISORY_ONLY=true` is confirmed live. When an
advisory request could not be confirmed, the field is `false`, and the note says "The
ADVISORY_ONLY quarantine is NOT engaged."

The daemon picks up a store change at its next wake when `RUNTIME_FLAGS_REFRESH_ENABLED` is on,
which is between cycles. Otherwise it picks it up at restart.

## Verification (run this session)
- Targeted pytest (pilots_api, strategy_registry, runtime_flags_writer, the isolation file,
  census, liveness, agent-docs sync): 562 passed.
- `npm run --prefix webapp typecheck`: clean.
- vitest `SettingsGeneral.test.tsx`: 11 passed. Full vitest run: 147 files, 1746 tests passed.
- `ruff check --select=F821,F822,F823,E9 .`: all checks passed.
- `make ci PYTHON=/Users/kevinlee/Stockpy-live/.venv/bin/python3`: 11661 passed, 24 skipped.
  The live audit log stayed at 273 lines before and after.
- Browser check (mock mode, Vite on :5191, Settings > Execution Mode > Paper Trading):
  - The typed confirmation worked and the "Execution mode changed to paper" toast rendered.
  - The only console errors were from the dev service-worker registration, which fails under
    `vite dev`.
  - The mock always accepts the store write, so the conflict warning did not appear in the
    browser. That rendering is covered by vitest only.

## Not done
- The live store still holds the test-planted entries. Cleaning them up needs operator
  sign-off.
