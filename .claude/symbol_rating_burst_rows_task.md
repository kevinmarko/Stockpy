# symbol_rating_burst_rows task tracker

- [x] Phase 1 investigation + plan (`symbol_rating_burst_rows_implementation_plan.md`)
- [x] Root conftest autouse `_isolate_symbol_rating_db_in_tests`
- [x] Readonly-store-on-missing-file case verified (degrades to empty; fixture does not pre-create the file because a suite test asserts tmp_path stays empty)
- [x] Structural guard `DEFAULT_TRUE_GATED_STORES` + expected-set entry in `tests/test_store_isolation_contract.py`
- [x] `tests/test_symbol_rating_test_isolation.py`
- [x] `scripts/cleanup_symbol_rating_test_rows.py` + `tests/test_cleanup_symbol_rating_test_rows.py` (dry-run default; NOT applied to the live DB)
- [x] Docs: known issue + index, testing.md, CLAUDE.md/AGENTS.md fixture list, regenerated settings census/liveness artifacts
- [ ] Tripwire (skipped: not cheap/reliable; the post-merge zero-growth row-count check covers it)
- [ ] Operator-approved live cleanup run
