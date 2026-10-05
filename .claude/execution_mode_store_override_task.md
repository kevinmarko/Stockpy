# Execution-mode store override: task tracker

- [x] `strategy_registry.mode_env_values`, and `set_active_mode` uses it.
- [x] `_apply_execution_mode_to_store`: per-key `applies`, `ok`, `quarantine_engaged` (fails
      closed), `store_conflict`, honest `note`.
- [x] `types.ts` and `mock.ts` in parity. `client.ts` needed no change: it uses the same
      `http<ExecutionModeUpdateResult>` call.
- [x] `SettingsGeneral.tsx` shows the warning and only toasts success when `ok`.
- [x] pytest: `TestExecutionModeStoreOverride` (5 tests), plus the updated happy-path test.
- [x] vitest: 2 new tests, existing fixtures updated.
- [x] Docs: CLAUDE.md/AGENTS.md rule, observability and execution architecture docs, RUNBOOK
      §3.17, known-issue update, regenerated census and liveness artifacts.
- [x] Verification: targeted pytest, typecheck, vitest, ruff, `make ci`, browser check (mock).
- [ ] HOLD: one-time cleanup of the live store's test-planted entries. Needs operator sign-off.
