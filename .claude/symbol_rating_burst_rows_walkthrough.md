# symbol_rating_burst_rows walkthrough

Problem: pytest runs wrote ~26.7k constant-score (55.0 / 1.0) fixture rows into the live `symbol_rating_events`
table because `SymbolRatingStore()` had no root isolation fixture and `SYMBOL_RATING_ENABLED` defaults True.

Changes
- `conftest.py::_isolate_symbol_rating_db_in_tests` (autouse): patches `rating.symbol_rating_store.resolve_database_url`
  to a per-test tmp SQLite file.
- `tests/test_store_isolation_contract.py`: expected-set entry + `DEFAULT_TRUE_GATED_STORES` guard.
- `tests/test_symbol_rating_test_isolation.py`: behavioral regressions.
- `scripts/cleanup_symbol_rating_test_rows.py` (+ tests): dry run by default, `--apply` backs up, one transaction,
  `--max-id` bound, rollback on count mismatch. Not applied to the live DB.
- Docs: `docs/known_issues/symbol_rating_burst_rows_test_leakage.md`, index, testing.md, CLAUDE.md/AGENTS.md,
  regenerated `docs/settings_*` artifacts (new script adds a scanned file).

Verification
- Targeted tests green; `ruff --select=F821,F822,F823,E9 .` clean.
- `make ci` (with INVESTYO_RUNTIME_FLAGS_PATH exported): 11657 passed, 1 failed - `test_runtime_flags.py::TestPathAnchoring::
  test_explicit_path_beats_env_var_beats_default`, which asserts the default path and fails only because that env var is
  exported; it passes (48/48 in the file) without it.
- Live `symbol_rating_events` row count unchanged across the `make ci` run (27656 -> 27656, max id 27656).
