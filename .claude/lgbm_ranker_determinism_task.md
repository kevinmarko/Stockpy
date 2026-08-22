# Task tracker: `lgbm_ranker` training non-determinism fix

Branch: `fix-lgbm-ranker-nondeterminism`. Status as of 2026-08-22 10:18 ET (verified against
actual repo/process state at write time, not assumed).

- [x] Add `LGBM_RANDOM_SEED = 42` + `random_state`/`deterministic`/`force_row_wise` to
      `ml/lgbm_ranker.py::_DEFAULT_PARAMS` — confirmed present via `git diff ml/lgbm_ranker.py`.
- [x] Empirical verification (in-process offline double-run) — bit-identical
      `dsr=0.9812207805846127`, `pbo=0.14285714285714285`, `mean_oos_sharpe=2.857031446734973`,
      `deployable=True` confirmed across two runs. (`ml/registry.yaml`'s updated
      `lgbm_ranker` entry — `cpcv_dsr: 0.9812207805846127`, `pbo: 0.14285714285714285`,
      `trained_date: '2026-08-22'` — corroborates this run actually happened.)
- [x] Regression test added to `tests/test_lgbm_ranker_native_cv.py` —
      `TestReproducibility` class (3 tests: fixed-seed params present, bit-identical
      predictions on the native-MultiIndex-CV path, bit-identical predictions on the
      flatten path). Confirmed via `git diff` and a live run:
      `python3 -m pytest tests/test_lgbm_ranker_native_cv.py -q` → **19 passed** (all
      pre-existing tests plus the 3 new ones), no flakiness observed.
- [ ] Full canonical validation re-run
      (`python -m scripts.refresh_validations --strategies lgbm_ranker --start 2005-01-01
      --output-dir reports --n-cpcv-splits 15 --n-test-splits 4 --workers 1 --json`) —
      **running in background**, log at `/tmp/validation_runs/lgbm_ranker_seedfix.log`,
      PID 16419. Confirmed alive via `ps -p 16419` (elapsed ~3m31s at last check, still in
      the early per-CPCV-fold training stage per the log tail — this run has historically
      taken ~2 hours wall-clock for 1365 CPCV paths, so it is expected to still be running
      well past this session). Do not report a final Sharpe/PBO/DSR/MaxDD/`deployable`
      number until `reports/lgbm_ranker_validation_summary.json` actually lands.
- [ ] Investigation note on `cross_sectional_momentum`/`sector_quality_rank`
      non-determinism (parallel-agent task per the plan's scope) — **not yet present**;
      no matching text found in `docs/VALIDATION_STRATEGY_FIX_LOG.md` and no other
      worktree/process evidence of it in progress was found from this worktree. Still
      outstanding.
- [ ] Documentation: `docs/VALIDATION_STRATEGY_FIX_LOG.md` append to the existing
      `lgbm_ranker` thread — **not yet done**. The file's `lgbm_ranker` thread still shows
      its prior `PENDING` marker (lines ~1789, ~1816) with no new entry appended for this
      fix. Blocked on the full validation re-run above landing (or documented as an honest
      still-running status per the plan).
- [ ] Documentation: `docs/signals/lgbm_ranker.md` Backtest Validation follow-up —
      **not yet done**. Same blocker as above.
- [ ] Commit, push branch, open PR — **not yet done**. `git status` shows the three
      modified files (`ml/lgbm_ranker.py`, `ml/registry.yaml`, `tests/test_lgbm_ranker_native_cv.py`)
      still unstaged/uncommitted on `fix-lgbm-ranker-nondeterminism`.
