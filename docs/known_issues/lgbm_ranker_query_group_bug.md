# `LGBMCrossSectionalRanker` trained with one giant LambdaRank query instead of per-date groups

**Status: Fixed and verified.** The crash itself is gone (confirmed via a full real
re-run: all 1365 CPCV paths complete with zero `LightGBMError`s). The strategy's
reported Sharpe/DSR from that same run (`24.886`/`0.696`) is a SEPARATE, distinct
problem — not this bug recurring — traced to a harness-wide annualization-frequency
gap (`validation/metrics.py::sharpe_ratio` always assumes daily observations) compounding
with the already-documented in-sample-vs-OOS gate being off by default. See
`docs/VALIDATION_STRATEGY_FIX_LOG.md`'s 2026-08-21 follow-up entry and
`docs/signals/lgbm_ranker.md`'s own follow-up section for the full detail — that
harness-wide gap is being fixed as its own dedicated follow-up, not folded into this
document.

## How this was found

Discovered as a side effect of the 2026-08-21 `scripts/refresh_validations.py`
universe-widening change (see `docs/VALIDATION_STRATEGY_FIX_LOG.md`'s
"Tiered universe widening for 7 cross-sectional strategies" entry): widening
`lgbm_ranker`'s validation universe from 30 to a capped 100 tickers made every
single CPCV fold start hard-crashing with:

```
[LightGBM] [Fatal] Number of rows <N> exceeds upper limit of 10000 for a query
LightGBMError: Number of rows <N> exceeds upper limit of 10000 for a query
```

(`N` observed ranging 11,666–29,398, scaling with each fold's training-panel
size.) The harness's own all-folds-failed dead-letter path then reported
sentinel metrics (`PBO=1.0`, `DSR=0.0`, `Sharpe=None`) — not a real backtest
result — which could easily have been misread as "no edge" rather than
"training crashed."

## Root cause

`ml/lgbm_ranker.py::LGBMCrossSectionalRanker.train()` already computed a
correct per-date query-group array for a `(date, ticker)` MultiIndex panel —

```python
# LambdaRank needs a group array: # tickers per date (query).
# If MultiIndex, group by first level (date); else treat all as one group.
if is_multi:
    groups = X.index.get_level_values(0).value_counts().sort_index().values
```

— but never actually passed it to LightGBM. Both the inner purged-k-fold
sub-CV and the final full-data fit instead used:

```python
fold_model.fit(X_tr, y_tr, group=[len(y_tr)], eval_group=[[len(y_te)]], ...)
...
final_model.fit(X_arr, y_arr, group=[len(y_arr)])
```

— i.e. **the entire fold (or the entire panel) was treated as ONE LambdaRank
query**, not one query per date. `groups` was computed and then never read
anywhere — dead code.

This is wrong in two distinct ways, one silent and one loud:

1. **Silently wrong (the real issue): the model was trained to rank tickers
   against tickers from OTHER dates, not just same-date peers.** LambdaRank's
   pairwise loss only makes sense within a query — cross-sectional ranking
   requires "which ticker had the better forward return on THIS date," not
   "which (date, ticker) row had the better rank-scaled label across the
   whole training set." A single giant query silently defeats the entire
   point of a per-date cross-sectional ranker.
2. **Loudly wrong (what actually got noticed): a single query's row count is
   capped internally by LightGBM at ~10,000 rows.** At a small universe
   (30 tickers × ~300–1000 dates per fold, historically under a few thousand
   rows) this stayed under the limit by luck, not by design. Widening to 100
   tickers pushed it over, and every fold started raising `LightGBMError`.

## Production impact — not just this validation script

`scripts/train_lgbm.py` (the actual production training entry point —
"updates the registry row," pickles `ml/models/lgbm_<YYYYMMDD>.pkl`) calls
the exact same `LGBMCrossSectionalRanker.train()` method, including the same
buggy final-fit call (`ranker.train(panel.X, panel.y, panel.t1)`,
`scripts/train_lgbm.py:472-473`). **The deployed LGBM ranker signal
(`ml-cross-sectional-rank` Pilot, weight 0.10 ensemble input) has likely been
trained with this same cross-date-query defect for as long as this code has
existed** — production's own training universe apparently never happened to
cross the 10,000-row threshold, so it never crashed there; it just silently
trained with the wrong LambdaRank objective the whole time. This fix corrects
both the validation harness AND the next real production retrain.

## Fix

`ml/lgbm_ranker.py` gained a small module-level helper,
`_positional_query_groups(keys, positions)`, which run-length-encodes
consecutive identical date labels at the given (already row-ordered)
positions into LightGBM `group` sizes. This is correct even across a
purge/embargo gap that removes rows from the *middle* of one date's block —
the surviving rows for that date are still contiguous *within the filtered
subset*, because `CombinatorialPurgedCV.split()` (`validation/purged_cv.py`)
partitions an already date-sorted array into positional blocks and returns
train/test indices as filters over that array — filtering preserves relative
row order and never interleaves two different original blocks' rows.

Both the per-fold sub-CV fits and the final full-data fit now pass real
per-date groups instead of `[len(y)]`. The non-MultiIndex ("no real per-date
structure") path is untouched — it still treats the whole array as one group,
exactly as before, since there's no date information to recover a real
grouping from in that case.

## Verification

- `tests/test_lgbm_ranker_native_cv.py::TestPerDateQueryGroups` (5 new tests):
  pure unit coverage of `_positional_query_groups` (contiguous dates, a
  purged middle-chunk edge case, the `keys=None` fallback, empty positions),
  plus a real (non-mocked) end-to-end reproduction — `250 dates × 45 tickers
  = 11,250 rows`, comfortably over LightGBM's real ~10,000-row single-query
  limit (independently confirmed against the installed `lightgbm` directly:
  `fit(..., group=[10500])` raises the exact same error text) — which raised
  before this fix and now trains cleanly.
- Full existing `ml/lgbm_ranker.py`-adjacent suite (`test_lgbm_feature_pit.py`,
  `test_lgbm_no_leakage.py`, `test_lgbm_purged_integration.py`,
  `test_lgbm_ranker_native_cv.py`, `test_lgbm_ranker_signal.py`,
  `test_train_lgbm.py`, `test_validation_lgbm.py`,
  `test_validation_lgbm_ranker_registry.py`): 58 passed, 3 deselected
  (network-marked), 0 failed, 0 regressions.
- Live re-run of `python -m scripts.refresh_validations --strategies
  lgbm_ranker --start 2005-01-01 --n-cpcv-splits 15 --n-test-splits 4` against
  the real widened universe: **completed all 1365 combinatorial CPCV paths
  with zero crashes** (`LGBMCrossSectionalRanker trained on N samples`, real
  varying NDCG@1 CV scores per fold). `deployable=False` (DSR 0.696 < 0.95,
  unchanged conclusion either way). The reported Sharpe (24.886) is a
  separate, already-tracked measurement issue (see the Status line above),
  not this bug recurring — the crash itself is confirmed gone.

## What this does NOT fix / disclosed scope

- No attempt was made to re-verify or retrain the currently-deployed
  `ml/models/lgbm_<date>.pkl` pickle against this fix — that's a live
  production retrain, out of scope for a validation-harness bug fix, and
  should happen through the normal `scripts/train_lgbm.py` retrain cadence.
- The magnitude of the pre-fix objective's real-world impact on the deployed
  model's actual score quality is not measured here (would require training
  both versions on identical historical data and comparing downstream
  performance) — this write-up establishes the mechanism and root cause, not
  a quantified before/after production impact.
