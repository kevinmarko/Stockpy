# Bug-hunt follow-up: universe-cache, PBO/DSR sentinel, and preflight-DB fixes

**Branch:** `claude/sp500-universe-data-db1271`
**Date:** 2026-08-17

## Context

A prior investigation (run by another agent, in a different worktree) produced a
5-item bug-hunt report. The operator asked to implement all 5 on this branch.
Each item was independently re-verified against this worktree before any code
changed — two of the five did not hold up as originally framed, and are
documented below as skipped/narrowed rather than silently dropped.

## Fixed & verified

### 1. `scripts/preflight_check.py::check_db_exists` ignored `LOCAL_DATA_ROOT`
Hardcoded `_REPO_ROOT / "quant_platform.db"`, so the preflight gate failed in
every worktree even when the real (220MB+) database was present and healthy
at its `settings.LOCAL_DATA_ROOT`-anchored canonical location (PR #718). Now
resolves via `db_config.resolve_database_url()` — the same path every real
store uses — with the legacy repo-root path kept as a fallback for
pre-migration setups. Confirmed genuine bug; fixed.

### 2. `universe_engine.py::fetch_and_cache_universe` — Wikipedia changes-table removal
Wikipedia permanently removed its "Selected changes to the list of S&P 500
components" table (confirmed live, 2026-08). This is real and reproduces
against the live page today.

**Narrower fix than the original report proposed.** The existing test suite
(`tests/test_dead_letter_resilience.py::TestFetchAndCacheUniverseMalformedTable`)
already *deliberately* pins "raise ValueError when no stale cache exists" for
two of the three structural failure modes (too-few-tables,
missing-symbol-column) — both are genuinely unrecoverable (current_tickers
itself can't be parsed), and silently degrading there would risk masking a
much larger page-shape break. Blindly swallowing every `ValueError` from
`_parse_wikipedia_changes_table` would have also flipped that intentional,
tested contract and broken 3 passing tests.

The fix targets only the third, *actually-occurring* case: a second table
exists (`len(tables) >= 2`) but its columns no longer match any recognized
shape. In that case `current_tickers` (parsed from table[0]) is still solid,
fully-parsed data — the function now proceeds with `current_tickers` and an
empty (never fabricated) `change_records`, loudly logging that
survivorship-bias change history is incomplete for the refresh. A genuinely
missing second table stays fatal, unchanged.

Updated 2 tests whose contracts intentionally changed
(`test_missing_changes_columns_raises_value_error` →
`test_missing_changes_columns_degrades_to_current_tickers_only`;
`test_missing_changes_columns_with_cache_present_falls_back_to_stale_cache` →
`test_missing_changes_columns_prefers_fresh_current_tickers_over_stale_cache`,
since fresh partial data now correctly wins over stale full data).

**Verified against the real, live, currently-broken Wikipedia page**: all 5
previously-failing tests in `tests/test_universe.py` (network-marked) now
pass.

### 3. Fabricated `-999.0` sentinel in `validation/metrics.py::run_cpcv_evaluation`
A NaN Sharpe (degenerate/constant returns on a CPCV path) was replaced with
`-999.0` before being stored in `is_sharpe_matrix`/`oos_sharpe_matrix` — a
CONSTRAINT #4 violation, since those matrices back `mean_oos_sharpe`,
`sr_observed` (feeds DSR), `distribution`, and `paths[].sharpe`, all of which
could report a fabricated, wildly-wrong finite number instead of an honest
"unmeasurable."

Fixed by keeping the real (NaN-preserving) values in every matrix/output, and
introducing a *local-only* `_rank_key` helper (NaN → `-inf`) used solely for
`argmax`-based "best trial" selection — never stored, never leaked into a
reported metric. Also hardened `probability_of_backtest_overfitting` against
an all-NaN path row it can now legitimately receive (previously impossible,
since the sentinel guaranteed no NaN ever reached it) — such a path is now
excluded from the denominator rather than crashing on `np.nanargmax`.

One test (`tests/test_validation_lgbm.py::test_lgbm_validation_harness_runs_end_to_end`)
was asserting the *old* fabricated behavior (`PBO` non-NaN) for a fixture
whose own docstring already documents its Sharpe as "honestly NaN... not a
bug" for the exact same degenerate-zero-signal reason. Updated the PBO
assertion to match, with the same CONSTRAINT #4 framing already used for the
Sharpe assertion two lines above it.

### 4. Undeclared `NO_VENV_REEXEC` env var
Documented in `.env.example` (with an explanation of why it's read via raw
`os.environ` rather than the `settings.X` singleton — it has to run *before*
Settings can safely be constructed). Deliberately **not** added as a
`Settings` field: doing so would trigger this repo's settings-census/liveness
regeneration machinery (`tests/test_settings_keysets.py`) for a variable
Settings never actually consumes — disproportionate to a pure documentation
gap.

## Skipped — did not reproduce

### `conftest.py` settings-singleton "pollution"
The original report claimed `_defaults = Settings()` (which loads real
`.env`) at session init causes `tests/test_state_api.py` /
`tests/test_pilots_api.py` to fail with 401s. Both files were run directly:
**419/419 pass**, unchanged, in this environment. Every security-sensitive
test in them already explicitly `mock.patch.object(settings, "STATE_API_TOKEN", ...)`
per-test rather than relying on the session-init default. The proposed fix
(`Settings(_env_file=None)` at session init) would zero out real
`.env`-sourced config for the *entire* test session — a real regression risk
for the many network-marked tests that intentionally read live credentials
from `.env` — to fix a failure mode that does not reproduce here. Left
unchanged; flagged for the operator rather than silently dropped.

## Verification performed
- `tests/test_dead_letter_resilience.py`, `tests/test_validation_lgbm.py`,
  `tests/test_metrics_cpcv_oos_aggregates.py`, `tests/test_harness_oos_gate.py`,
  `tests/test_pbo.py`, `tests/test_settings_keysets.py`: all pass (81 tests).
- Full `-k "metrics or harness or pbo or dsr or cpcv"` sweep: 340 passed.
- `tests/test_universe.py` (network-marked, against the real live Wikipedia
  page): 5 passed, 1 skipped (previously 5 failed).
- `ruff check` on all changed files: zero new findings beyond one `BLE001`
  (blind-exception catch), consistent with ~20 pre-existing instances of the
  same accepted pattern already in `scripts/preflight_check.py`.
