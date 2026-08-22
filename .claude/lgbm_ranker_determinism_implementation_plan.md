# Fix `lgbm_ranker` training non-determinism (missing LightGBM seed)

## Context

`ml/lgbm_ranker.py::_DEFAULT_PARAMS` sets `feature_fraction=0.8`/`bagging_fraction=0.8`
(refreshed every iteration, `bagging_freq=1`) with **no `random_state`/`seed` anywhere** in
the dict, `LGBMCrossSectionalRanker.__init__`, or `scripts/train_lgbm.py` — every
`lgb.LGBMRanker(**params)` construction is non-deterministic run-to-run. This is the last
unaddressed contributor to the `lgbm_ranker` Sharpe/DSR/`deployable` instability that
`docs/VALIDATION_STRATEGY_FIX_LOG.md`'s 2026-08-21 entries already partially chased down
(a real query-group crash, fixed; a real annualization-frequency bug, fixed) — and that
entry's own re-run to get the *real* post-fix number was started but **never completed**
(left `PENDING`, PID from a background process in a different, now-gone session). This
task closes both gaps: fixes the seed and finally fills in the long-outstanding PENDING
numbers.

**Direct evidence gathered from the live, durable `validation_runs` SQLite table**
(`~/.stockpy_local/quant_platform.db`, shared across every worktree on this machine) before
writing this plan:

- `lgbm_ranker`: two runs at `18:48:20`/`18:48:05` on 2026-08-21 with the **identical**
  `start_date`/`end_date` (2005-01-01 → 2026-08-21) differ at the 6th significant digit
  (`sharpe=0.6752608019782934` vs `0.6752606339208121`) — a small but real, non-zero,
  non-reproducible residual consistent with *unseeded LightGBM bagging/feature-fraction
  subsampling averaged across 1365 CPCV paths* (large-N averaging mostly cancels the
  per-fold randomness but doesn't zero it) — i.e. this is real, direct proof of the bug,
  not merely theoretical.
- **However**, most of the *dramatic* swings (Sharpe -0.57 to +24.9) the user's own DB
  query surfaced are NOT same-window reruns at all: `start_date`/`end_date` differ wildly
  between rows for the same strategy (e.g. `lgbm_ranker` rows range from
  `2005-01-01→2026-08-21` to `2025-08-28→2026-07-16`, a <1yr window vs a 20yr window) —
  this table is a shared pool written by many *concurrent, independent* worktree sessions
  each invoking `refresh_validations.py`/`train_lgbm.py` with different `--start`/`--end`
  args (or hitting a different subset of tickers due to FMP's shared rate limiter). That's
  a confound, not evidence of the seed bug by itself — the seed bug is real but smaller in
  magnitude than the DB's raw scatter suggests.
- **`sector_quality_rank`** (not LightGBM-based) shows real Sharpe swings (0.457 → 0.964)
  across FIVE runs sharing the *exact same* `start_date`/`end_date`
  (`2005-01-03→2026-08-20`) — a genuine same-window instability, most plausibly explained
  by concurrent-worktree contention on the shared FMP fetch/circuit-breaker or
  `HistoricalStore` cache (a different ticker subset succeeding per run shifts sector
  composition), not a code determinism bug in that adapter itself. `cross_sectional_momentum`'s
  swings are dominated by differing `start_date`/`end_date` across rows (same confound as
  `lgbm_ranker`). Both get a documented *finding*, not a fix, per the user's scope.

## Approach

### 1. Environment setup (no code change)

This worktree has no `.env` (checked: `test -f .env` → false), so `FMP_API_KEY` is unset
and the real full-registry validation run cannot fetch price data at all
(`scripts/refresh_validations.py::_download_closes` raises `RuntimeError` with zero FMP
key). The main checkout (`/Users/kevinlee/Stockpy-live/.env`) already has a real,
non-empty `FMP_API_KEY` — copy that `.env` into this worktree (a local file copy on the
user's own machine, not a network credential entry) so the canonical full validation run
in step 5 can actually hit real data, matching how every other worktree that has produced
real recorded runs in the shared DB got its key.

### 2. Branch

Currently on `claude/unruffled-matsumoto-8bd8b0`, which is byte-identical to `main` (no
divergent commits). Create `fix-lgbm-ranker-nondeterminism` off current HEAD and work
there — this touches `ml/lgbm_ranker.py` (production training path + validation gate),
squarely in CLAUDE.md's "Everything else" tier requiring a branch + PR, not a direct
`main` commit.

### 3. Code fix — `ml/lgbm_ranker.py`

Add a fixed seed constant (matching the `CNN_LSTM_RANDOM_SEED = 42` convention in
`cnn_lstm_worker.py`) and LightGBM's own documented determinism knobs to
`_DEFAULT_PARAMS`:

```python
LGBM_RANDOM_SEED = 42

_DEFAULT_PARAMS: dict = {
    ...,  # unchanged existing keys
    "random_state": LGBM_RANDOM_SEED,   # seeds bagging/feature_fraction/drop/data seeds
    "deterministic": True,              # forces LightGBM's reproducible (slower) code path
    "force_row_wise": True,             # pins the row/col-wise auto-choice LightGBM's
                                         # "Reproducing results" FAQ calls out as a second
                                         # non-determinism source alongside `deterministic`
}
```

This dict is the single base every `LGBMRanker(**self.params)` construction merges from
(`__init__`: `{**_DEFAULT_PARAMS, **(params or {})}`), so the fix automatically covers:
`ml/lgbm_ranker.py`'s own per-fold CV fits + final fit, `scripts/train_lgbm.py`'s
`_CANDIDATE_PARAMS` trials (they only override `num_leaves`/`learning_rate`/
`n_estimators`), and `scripts/refresh_validations.py::_build_lgbm_ranker_adapter`'s
per-CPCV-fold retrains (constructs with no `params` override at all). No new
`settings.py` field — this mirrors `CNN_LSTM_RANDOM_SEED`'s precedent of an
unconditional module constant, not an operator-tunable knob.

I'll empirically confirm this exact param combination is sufficient (not just trust the
docs) via the reproducibility test in step 4 below; if bit-identical results aren't
achieved, I'll add a pinned `num_threads` next (LightGBM's histogram-reduction order can
still vary by thread count in rare cases) and note that in the writeup.

### 4. Empirical verification + regression test

- **Fast in-process check** (not committed, reported in the summary): call
  `scripts/train_lgbm.py::run_training(tickers, offline=True, ...)` twice in-process
  (the offline `_SyntheticDataEngine` is itself seeded/deterministic) and diff
  `dsr`/`pbo`/`mean_oos_sharpe`/`deployable` — isolates LightGBM's own determinism from
  network/data variability entirely.
- **Permanent regression test** — new `TestReproducibility` class in
  `tests/test_lgbm_ranker_native_cv.py` (reuses that file's existing
  `_make_multiindex_panel` helper):
  - `_DEFAULT_PARAMS` contains the fixed seed / `deterministic=True`.
  - Two independently-constructed `LGBMCrossSectionalRanker()` instances trained on the
    *same* synthetic (date, ticker) panel + t1 (via `use_native_multiindex_cv=True`, the
    path production/validation actually exercises) produce bit-identical `.predict()`
    output on a held-out frame (`np.testing.assert_array_equal`, not `allclose` — the
    whole point is exact reproducibility).

### 5. Full canonical validation re-run (fills the outstanding PENDING entry)

Start, in the background, the *exact* command the 2026-08-21 entry already established
for comparability (and never got to finish):

```
python -m scripts.refresh_validations --strategies lgbm_ranker --start 2005-01-01 \
  --output-dir reports --n-cpcv-splits 15 --n-test-splits 4 --workers 1 --json
```

Historically ~2 hours wall-clock (1365 CPCV paths, the only adapter that genuinely
retrains a fresh model per fold). Launch this as early as possible (right after steps 1-2)
so it runs concurrently with steps 3-4 and the docs/investigation work below. If it
finishes within this session, record the real Sharpe/PBO/DSR/MaxDD/`deployable` in both
docs (step 6). If it's genuinely still running when everything else is done, I will not
fabricate a number — I'll report its live/PID status to the user and keep it running,
finishing the doc updates once it lands rather than leaving the PENDING marker stale
again (the exact thing the prior entry warned against).

### 6. Documentation (per CLAUDE.md's mandatory doc-update step + the strategy-validation
skill's two-place rule)

- **`docs/VALIDATION_STRATEGY_FIX_LOG.md`**: append to the *existing* `lgbm_ranker` thread
  (do not start a new top-level entry) — the seed/determinism fix, the DB evidence above
  (both the real small-magnitude proof AND the honest correction that most of the DB's
  raw scatter is a concurrent-worktree/different-window confound, not the seed bug alone),
  the reproducibility test result, and the real full-run numbers (finally resolving the
  outstanding `PENDING` marker) — or an honest still-running status if step 5 hasn't
  finished.
- **`docs/signals/lgbm_ranker.md`**: append to its own matching "PENDING" Backtest
  Validation follow-up section with the same resolved numbers.
- **New finding, not a fix**: a short note (in the fix log, near the `lgbm_ranker` entry)
  on `cross_sectional_momentum`/`sector_quality_rank`: not LightGBM-based, so a different
  mechanism — `sector_quality_rank` shows genuine same-window swings best explained by
  concurrent-worktree contention on the shared FMP/HistoricalStore cache (different ticker
  subsets succeeding per run); `cross_sectional_momentum`'s swings are dominated by
  differing `--start`/`--end` windows across the shared DB's concurrent writers. Flagged
  for future investigation, explicitly out of scope to fix here.

### 7. Tests + PR

Run the targeted LGBM/validation test files
(`test_lgbm_ranker_native_cv.py`, `test_train_lgbm.py`, `test_lgbm_no_leakage.py`,
`test_lgbm_purged_integration.py`, `test_lgbm_feature_pit.py`,
`test_validation_lgbm_ranker_registry.py`) plus the new test class. Commit, push the
branch, open a PR with the required `.claude/lgbm_ranker_determinism_*` plan/task/
walkthrough artifacts (unique-named per CLAUDE.md's collision-avoidance rule).

## Verification

- New pytest class passes deterministically (run it 2-3x locally to make sure it isn't
  itself flaky).
- Full targeted test files above: zero failures.
- The in-process offline double-run diff reported in the final summary.
- Real `reports/lgbm_ranker_validation_summary.json` from step 5 (or an honest
  still-running status) quoted verbatim into both docs — never estimated.
