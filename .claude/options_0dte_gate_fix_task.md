# Task Tracker: `OPTIONS_0DTE_ENABLED` outer-gate fix

- [x] Confirm the bug by reading `main.py`'s outer gate (`main.py`, was
      lines 1494-1499 pre-fix).
- [x] Extract the inline block into `_run_automated_options_lifecycle(macro_dto=None)`,
      mirroring `_run_automated_delta_hedge_cycle`'s existing extraction
      pattern, and add `OPTIONS_0DTE_ENABLED` to the outer OR.
- [x] Rewire `_run_cycle()` to call the new function.
- [x] Add regression tests in `tests/test_main.py` covering the full
      four-flag OR-gate truth table (the 0DTE-only case is the key one).
- [x] Manually verify the key test fails against the pre-fix gate condition.
- [x] Investigate the daemon-path gap (`main_orchestrator.py`/
      `desktop/daemon_runtime.py` have no equivalent wiring for exit
      management/strategy auto-execution/delta hedging, only 0DTE).
- [x] Decide scope: document (not code-fix) the daemon gap, given the
      main.py venv-reexec-guard blocker and the cadence/macro_dto design
      decisions a real fix would need.
- [x] Write `docs/known_issues/options_lifecycle_daemon_gate_gap_2026_08_22.md`.
- [x] Add index row to `docs/known_issues/README.md`.
- [x] Extend `desktop/daemon_runtime.py`'s `_run_one_cycle`/`_timer_loop`
      comments.
- [x] Extend `docs/architecture/execution.md`'s `OptionsPaperExecutor` bullet.
- [x] Extend `CLAUDE.md`'s "Multi-Leg Option Paper Trading" bullet; confirm
      `AGENTS.md` auto-mirrored.
- [x] Run `pytest tests/test_main.py tests/test_run_once.py -q`.
- [x] Run `pytest tests/test_daemon_runtime.py -q` (docs-only changes there,
      sanity check).
- [x] Run `pytest tests/test_orchestrator_daemon.py tests/test_options_paper_executor.py tests/test_zero_dte_engine.py -q`.
- [x] `ruff check` diff against baseline — no new genuine issues.
- [x] Write PR artifacts (`.claude/options_0dte_gate_fix_implementation_plan.md`,
      `_task.md`, `_walkthrough.md`).
- [ ] Open PR from `fix-options-0dte-gate-missing` -> `main`.
