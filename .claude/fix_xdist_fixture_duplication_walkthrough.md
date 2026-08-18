# Walkthrough — Fix xdist Fixture Duplication in 3 More Test Files
**Branch:** `fix-xdist-fixture-duplication`
**Date:** 2026-08-18

## Background

Follow-on to the offline-CI-suite speedup work in PR #796/#797. While pulling
real timing data during that work, the `slowest 10 durations` output from a
live CI run showed:

```
53.92s setup    tests/test_orchestrator_e2e.py::TestStateSnapshot::test_state_snapshot_file_materializes
52.64s setup    tests/test_orchestrator_e2e.py::TestStateSnapshot::test_state_snapshot_has_expected_top_level_schema
```

Two nearly-identical ~53s **setup** costs for the same `scope="module"`
fixture (`orchestrator_run`) is the exact signature of a known bug class in
this codebase: under `--dist loadgroup`, a module/class-scoped fixture with
no `@pytest.mark.xdist_group(...)` marker falls back to `load` distribution,
which can scatter its consuming tests across multiple xdist workers — and
each worker that gets any of them re-runs the expensive fixture setup from
scratch.

Four files already carry this marker (fixed in earlier PRs):
`test_train_lgbm.py`, `test_settings_liveness.py`,
`test_backtest_sector_configs_cli.py`, `test_settings_keysets.py`.

## What was checked

Grepped every file under `tests/` for `scope="module"`/`"class"`/`"session"`
fixtures (20 files found), then filtered to the ones that actually run in
CI's offline `test`/`test-slow` jobs (10 of the 20 are `@pytest.mark.network`
and deselected in CI entirely — not a concern here) and checked each
fixture's real cost and how many classes/tests consume it.

**Confirmed real duplication risk, fixed in this PR:**

| File | Fixture | Cost | Classes sharing it |
|---|---|---|---|
| `test_orchestrator_e2e.py` | `orchestrator_run` | Real end-to-end pipeline run (~53s), **confirmed 2x-duplicated in actual CI logs** | 5 classes |
| `test_etf_transmission_sensitivity_sweep.py` | `sweep_results` | Real 2-D grid sweep (`MAX_DERATE_GRID x COV_INFLATION_GRID`) | 3 classes |
| `test_sector_forecast_backtest.py` | `backtest_result` | Real ARIMA/Holt-Winters/Monte-Carlo walk-forward fits | 1 class (multiple methods) |

**Checked and skipped — fixture is cheap, not worth the churn:**

| File | Fixture | Why skipped |
|---|---|---|
| `test_pipeline_smoke.py` | `_repo_py_files` | Just `Path.rglob("*.py")` — a file-tree walk, not real computation |
| `test_options_selling_backtest_stress.py` | `golden`/`golden_spy` | JSON file read + vectorized numpy reconstruction — cheap |
| `test_measure_settings_census.py` | `fresh_census` | Single class, likely fast settings/env scan |

## Change

Added `pytestmark = pytest.mark.xdist_group("<name>")` to the top of each of
the 3 confirmed files, following the exact pattern already established in
`test_settings_keysets.py` (see that file's own comment for precedent).
Group names: `orchestrator_e2e`, `etf_transmission_sensitivity_sweep`,
`sector_forecast_backtest`.

Updated `.github/workflows/ci.yml`'s `--dist loadgroup` comment to list all
7 files now carrying this marker (was 4).

## Verification

No local `.venv`/heavy dependencies available in this sandbox to run the
full suite directly — syntax-checked with `ast.parse` (clean) and confirmed
the marker usage is byte-for-byte the same pattern already proven working
in 4 merged files. CI itself (`ruff` + the full offline pytest suite) is the
real verification gate for this PR.

## Expected impact

Removes the confirmed ~53s duplicate `orchestrator_run` fixture cost (was
happening 2x per run based on real log evidence) plus prevents the same
class of duplication for the ETF sweep and sector-forecast-backtest
fixtures, which hadn't yet been observed duplicating in a top-10 slowest
list but carry the same structural risk.
