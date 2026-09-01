# Phase 7: Daemon Integration Tasks

- [ ] Create `execution/options_lifecycle.py` and extract `_run_automated_options_lifecycle` and `_run_automated_delta_hedge_cycle` from `main.py`.
- [ ] Update `main_orchestrator.py` to return `macro_dto` from `_main_body` and `_main_body_impl`.
- [ ] Update `desktop/daemon_runtime.py`'s `_run_one_cycle` to receive `macro_dto`.
- [ ] Wire the extracted options lifecycle methods into `desktop/daemon_runtime.py`.
- [ ] Update `main.py` to use the extracted functions in `execution.options_lifecycle.py`.
- [ ] Write/update tests in `tests/test_daemon_runtime.py`.
- [ ] Update `docs/known_issues/options_lifecycle_daemon_gate_gap_2026_08_22.md` to mark Bug 2 as fixed.
- [ ] Run test suite and `make verify`.
- [ ] Copy artifacts to `.claude/phase_7_options_lifecycle_hedging_...` prefixed files.
