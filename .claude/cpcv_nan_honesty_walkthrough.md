# CPCV NaN Honesty Fix — Walkthrough

## Summary

`/code-review 786` found PR #786 was opened from a stale base predating the
already-merged PR #785, is reported `mergeable: CONFLICTING` by GitHub, and
duplicates/regresses most of what #785 already fixed. This PR extracts and
completes the one genuinely new, non-redundant piece of #786 —
`validation/autonomous_backtest_runner.py`'s NaN-honesty fix, which #785
never touched — and closes a real bug that fix newly exposes in the shared
`probability_of_backtest_overfitting()`.

## Changes

### 1. `validation/metrics.py::probability_of_backtest_overfitting()`
Added a guard: when the in-sample-best trial's own OOS Sharpe is
individually `NaN` (e.g. a degenerate/constant test window for just that one
trial), the path is now excluded from `measurable_paths` entirely, matching
the function's existing all-NaN-row skip. Previously `NaN < median`
silently evaluated `False`, counting such a path as "not overfit" instead of
excluding it — biasing PBO downward.

### 2. `validation/autonomous_backtest_runner.py::run_cpcv()`
- Removed the `-999.0` sentinel for the IN-SAMPLE Sharpe (`path_is_sharpes`),
  matching the OOS side which #786 had already fixed correctly.
- Replaced `mean_is_sharpes = np.mean(np.where(is_arr > -900, is_arr, 0.0), axis=0)`
  (a fabricated `0.0` substitution for degenerate trials) with a NaN-aware
  per-strategy mean, filtering NaN columns before computing `sr_var` —
  mirroring `validation/metrics.py`'s existing `finite_mean_is_sharpes`
  pattern.

### 3. Module docstrings
Reapplied 5 of #786's 6 new docstrings verbatim (confirmed accurate against
each file's actual contents). Corrected the 6th
(`ml/transformer_vol_forecaster.py`): the claimed "quantile uncertainty
estimation" doesn't exist anywhere in that 327-line file (confirmed via
grep — zero hits for "quantile"); the corrected docstring says "point
volatility prediction" instead.

### 4. Dropped (not carried forward from #786)
`conftest.py`, `settings.py` (`NO_VENV_REEXEC`), `.env.example`,
`db_config.py`, `scripts/preflight_check.py`, `universe_engine.py`,
`tests/test_preflight.py` — all either fully superseded by #785's already-
merged, more complete fix, or independently regressive (see the
code-review findings this PR is based on: `conftest.py`'s change reintroduces
a fix #785 explicitly investigated and rejected; the new `NO_VENV_REEXEC`
settings field is dead code that also breaks the settings-census/liveness
freshness tests; `db_config.py`'s dynamic `resolve_database_url()` diverges
from the still-frozen `DEFAULT_DATABASE_URL` constant, empirically
reproduced to break `tests/test_investyo_mcp_server.py`).

### 5. Tests
- `tests/test_pbo.py`: 2 new cases —
  `test_pbo_all_nan_is_row_excluded_from_measurable_paths` (backfills
  coverage for #785's pre-existing all-NaN-row guard, which had no dedicated
  test) and `test_pbo_nan_oos_for_is_winner_excluded_not_miscounted_as_not_overfit`
  (pins the new fix — hand-computed expected PBO of `1.0`, vs. `0.5` under
  the pre-fix bug).
- `tests/test_autonomous_backtest_runner.py`: 1 new case —
  `test_degenerate_flat_price_series_never_leaks_fabricated_sentinel` (a
  zero-volatility synthetic OHLCV series drives every Sharpe to NaN; asserts
  no `-999.0` leaks into either matrix and `pbo`/`dsr` stay honest floats).

### 6. Docs
Updated `docs/architecture/ml-and-reports.md` (`validation/autonomous_backtest_runner.py`
bullet) and `docs/architecture/validation-and-signals.md`
(`validation/metrics.py` bullet) per CLAUDE.md's mandatory documentation-update
step.

## Verification

- `pytest tests/test_pbo.py tests/test_autonomous_backtest_runner.py tests/test_dsr.py -v`: **36 passed** (3 new)
- `pytest tests/test_metrics_cpcv_oos_aggregates.py tests/test_harness_oos_gate.py tests/test_metrics_sharpe_ratio.py tests/test_institutional_metrics.py tests/test_multiple_testing.py -q`: **60 passed**
- `python3 -m ruff check . --select=F821,F822,F823,E9`: **all checks passed**
- Full offline suite, `python3 -m pytest -m "not network and not slow" -q -n auto --dist loadgroup`:
  **11348 passed, 31 skipped, 6 failed**. All 6 failures confirmed
  **pre-existing on `main`, unrelated to this branch** (reproduced identically
  with this branch's changes stashed out): 3 in `test_data_api_chat.py` +
  2 in `test_gemini_live_chat.py` fail on this machine due to a missing/
  misconfigured `google-genai` package import; 1 in
  `test_preflight.py::TestDbExists::test_fails_when_missing` fails because
  it doesn't patch `settings.LOCAL_DATA_ROOT` and this machine's real
  `~/.stockpy_local/quant_platform.db` genuinely exists. None of the three
  touch any file this PR changes.

No settings/census regeneration needed. No `STRATEGY_REGISTRY`/
`docs/VALIDATION_STRATEGY_FIX_LOG.md` entry needed — this fixes shared
PBO/DSR measurement honesty, not a specific strategy's deployability lever.
