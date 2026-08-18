# Walkthrough — Offline CI Suite Performance Follow-On
**Branch:** `claude/e-offline-suite-perf-97yalj`  
**Date:** 2026-08-18

## Background

A prior plan (`docs/plans/PYTEST_XDIST_SPEEDUP_PLAN.md`) already shipped
xdist parallelisation and the `test`/`test-slow` job split (224 s → ~112 s
wall-clock on the `test` job). This PR is the targeted follow-on pass
described in that plan's "further improvements" notes.

## Changes made

### 1. `pip-audit` moved to its own concurrent `security` job

**File:** `.github/workflows/ci.yml`

Previously `pip-audit` ran sequentially inside the `test` job (install
pip-audit → PyPA advisory DB network call) before pytest ever started.
That added 30–60 s to the test job's critical path every PR.

The new `security` job runs concurrently with `test`, `test-slow`, `webapp`,
and `bandit`, removing pip-audit from the critical path entirely.  The
audit logic (install the actual pinned venv first, then audit against it) is
unchanged.

### 2. `--cov-report=term-missing` removed from the test command

**File:** `.github/workflows/ci.yml`

The terminal coverage table (`--cov-report=term-missing`) iterated over all
300+ source files to emit a per-file missing-line report.  The CI summary
step only reads `coverage.json`, and the `fail_under = 58` floor in
`.coveragerc` is enforced by the coverage *run*, not the terminal report.
Dropping `term-missing` (keeping only `--cov-report=json`) eliminates the
expensive table without weakening any gate.

### 3. `--durations` trimmed from 50 → 10

**File:** `pytest.ini`

Each worker sorts and reports the slowest tests at the end of the run.
Reducing from 50 to 10 entries cuts the sort overhead and scrollback noise
with no coverage impact.  The CI comment was updated to reflect the new value.

### 4. `xdist_group` marker added to `test_settings_keysets.py`

**File:** `tests/test_settings_keysets.py`

This file has two `scope="class"` fixtures (found at lines 344 and 388).
Without an `xdist_group` marker, `--dist loadgroup` falls back to `load`
distribution for this file, potentially splitting its tests across workers
and causing each worker to re-run the expensive class-scope fixture setup
(which imports `api.pilots_api` and constructs `Settings`).

The new marker follows the established pattern already used by three other
files:
- `test_train_lgbm.py` → `xdist_group("train_lgbm")`
- `test_settings_liveness.py` → `xdist_group("settings_liveness")`
- `test_backtest_sector_configs_cli.py` → `xdist_group("backtest_sector_configs_offline_cli")`

The CI comment block was also updated to list `test_settings_keysets.py`
as a fourth member of the group.

## What was NOT changed

- `--dist loadgroup` was kept (not switched to `loadfile`).  The ci.yml
  comment already explained why `loadgroup` was chosen, and three files
  already carry `xdist_group` markers following that convention.  Adding the
  missing marker to `test_settings_keysets.py` is the targeted fix, not a
  strategy switch.
- The `Makefile` `ci`/`test` targets were not changed.  They use `loadgroup`
  for local runs, which is correct.
- `.coveragerc` `show_missing = True` / `skip_covered = True` were kept —
  those affect `term-missing` output, which no longer runs, but the settings
  are harmless and removing them would change the local `pytest --cov` output
  for developers.

## Expected impact

| Change | Estimated saving |
|--------|-----------------|
| pip-audit off critical path | 30–60 s off test job wall-clock |
| Drop `--cov-report=term-missing` | 5–15 s (300+ file table) |
| `--durations=50` → `--durations=10` | ~1 s (minor) |
| `xdist_group` on `test_settings_keysets.py` | Prevents duplicate class-fixture setup across workers |
