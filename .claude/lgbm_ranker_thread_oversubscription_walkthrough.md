# `lgbm_ranker` / `--workers` CPU-oversubscription profiling — walkthrough

Follow-up to a PR #740 code-review finding that was deliberately left unfixed pending
profiling: does `scripts/refresh_validations.py --workers N > 1` risk CPU oversubscription
when the strategy batch includes `lgbm_ranker` (the one `STRATEGY_REGISTRY` adapter that
genuinely retrains a real LightGBM model per CPCV fold) alongside the other, cheap,
mostly-I/O-bound adapters?

## What was inspected first

- `scripts/refresh_validations.py::_build_lgbm_ranker_adapter` — confirmed its docstring's
  claim: it builds a real `(date, ticker)` feature panel and its closed-over `strategy_fn`
  calls `LGBMCrossSectionalRanker.train()` once per CPCV fold (~45 folds at CLI defaults:
  `n_cpcv_splits=10`, `n_test_splits=2` → `C(10,2)=45`).
- `ml/lgbm_ranker.py::LGBMCrossSectionalRanker.train()` — each call does
  `purged_kfold_splits` (3, as constructed by the adapter) inner `lgb.LGBMRanker().fit()`
  calls plus one final full-data fit = 4 real LightGBM fits per `.train()` call. None of
  `_DEFAULT_PARAMS` sets `n_jobs`/`num_threads`, so LightGBM uses its library default
  (auto = all physical cores).
- `scripts/refresh_validations.py::run_validations` — `ThreadPoolExecutor(max_workers=min(workers,
  len(strategies)))`, one thread per strategy, `executor.map` in submission order. Confirmed
  only `lgbm_ranker` in `STRATEGY_REGISTRY` is built by `_build_lgbm_ranker_adapter` — i.e.
  only ONE thread can ever be running real LightGBM training at a time regardless of
  `--workers`.

## Profiling methodology

Machine: 10 physical cores (`os.cpu_count() == 10`), LightGBM 4.6.0.

1. **Real end-to-end CLI run**, `lgbm_ranker` + 2 cheap SPY-only adapters
   (`rsi2_mean_reversion`, `timeseries_momentum`), `--n-cpcv-splits 5` (fewer folds, for
   runtime), live yfinance/Wikipedia network calls:
   - `--workers 1`: 59.23s wall (44.46s user + 33.57s sys CPU-time, 131% avg CPU).
   - `--workers 3`: 60.31s wall (47.59s user + 34.63s sys CPU-time, 136% avg CPU).
   - Result: within measurement noise of each other (network-call latency variance
     dominates at this scale); no regression, no clear win either.

2. **Synthetic-panel micro-benchmark** (isolates LightGBM's own concurrency behavior from
   network/CPCV-harness overhead): built a `(date, ticker)` panel matching the real
   adapter's shape (30 tickers × 300 dates, ~20 features) and called
   `LGBMCrossSectionalRanker.train()` at increasing concurrency via `threading.Thread`:
   - 1x (baseline): 3.90–3.97s
   - 3x sequential (ceiling): 9.18s (2.36x baseline)
   - 3x **concurrent**: 5.68s (1.46x baseline, **0.62x of the sequential ceiling**)
   - 6x concurrent: 10.04s (2.53x baseline; sequential ceiling would be ~23.8s)
   - 10x concurrent: 17.58s (4.43x baseline; sequential ceiling would be ~39.6s)
   - Result: concurrency was **always faster than sequential**, even at 10-way
     concurrency — no oversubscription regression observed at any tested concurrency
     level on this hardware, though per-job slowdown does grow sub-linearly at high N
     (expected — cores are finite).

## Conclusion

**No code fix applied.** The risk flagged in review does not reproduce:
- Structurally, only one `STRATEGY_REGISTRY` adapter (`lgbm_ranker`) is CPU-bound, so
  `run_validations()` can never create more than one concurrently-running real-training
  thread today, regardless of `--workers`.
- Even stress-tested well beyond that (up to 10-way concurrent LightGBM training, a
  scenario this codebase cannot currently produce), wall-clock time never regressed vs.
  sequential execution on the profiling machine.

Capping LightGBM's thread count or excluding `lgbm_ranker` from the worker pool would
trade away real, measured concurrency benefit to guard against a regression that isn't
reproducible here — not a minimal, evidence-backed change.

## What shipped instead

- `run_validations()`'s docstring (`scripts/refresh_validations.py`) — full caveat with
  methodology/numbers and the explicit "re-profile if a second CPU-bound adapter is ever
  added, or on a much smaller machine" condition.
- `--workers` CLI help text — one-line pointer to the docstring.
- `docs/architecture/validation-and-signals.md`'s `scripts/refresh_validations.py` entry —
  same summary, repo-wide-visible.
- `tests/test_refresh_validations.py::TestRunValidations::test_only_one_cpu_bound_adapter_in_registry`
  — regression guard: fails (forcing a re-profile) if a second `STRATEGY_REGISTRY` entry
  is ever built from `_build_lgbm_ranker_adapter`, since that's the precondition this
  whole conclusion rests on.

## Verification

- `pytest tests/test_refresh_validations.py -q` → **99 passed** (98 pre-existing + 1 new).
- `ruff check scripts/refresh_validations.py tests/test_refresh_validations.py` → 234
  pre-existing findings, identical count on `main` before this change (confirmed via
  `git stash` diff) — nothing new introduced.
