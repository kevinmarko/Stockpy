# ADVISORY_ONLY co-write fix: task tracker

Plan: `.claude/advisory_only_cowrite_fix_implementation_plan.md`. The coordinator approved
points 1, 2 and 4 for Phase 2. Points 3 and 5 are on HOLD for operator sign-off.

- [x] 1. Root `conftest.py` autouse fixture `_isolate_runtime_flags_store_in_tests`. Removed
      the duplicate file-local fixture from `tests/test_pilots_api_tunables.py`.
- [x] 2. Writer backstop in `runtime_flags_writer`: refuse the live default store when
      `"pytest" in sys.modules`. No `os.environ` read (census allowlist intact). Inert-outside-
      pytest test, both in process and in a fresh interpreter.
- [x] 4. Value-free audit fields: `pid`, `process`, `previous_present`, `changed`.
- [x] Tests: `tests/test_runtime_flags_test_isolation.py` (new) and
      `tests/test_runtime_flags_writer.py::TestAuditChangeTracking`; closed-key-set audit test
      updated; `tests/test_runtime_flags.py` default-branch test clears the conftest override.
- [x] Docs: `docs/known_issues/runtime_flags_store_test_contamination_2026_10.md`, README
      index, pr872 cross-link, `docs/architecture/testing.md`, writer module docstring, fixture
      name in the CLAUDE.md/AGENTS.md test-isolation list.
- [x] Regenerated `docs/settings_field_census.*` and `docs/settings_liveness.json`. Only line
      numbers changed.
- [ ] HOLD 3. `PUT /automation/execution-mode` updates the store (the fail-open fix). Needs
      operator sign-off.
- [ ] HOLD 5. One-time removal of the test-planted `ADVISORY_ONLY`/`SECTOR_HEAT_ENABLED` live
      store entries. Needs operator sign-off.
