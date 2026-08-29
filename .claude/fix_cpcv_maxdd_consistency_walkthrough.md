# Walkthrough: CPCV Mean OOS Max Drawdown Fabrication Fix

## Root cause

`scripts/train_meta_labelers.py::_meta_gated_returns()` returns, per event,
`position * sign(y_primary) * sign(y_barrier)` — a discrete outcome in
`{-1, 0, +1}` (loss / flat / win, an "R-multiple of the triple-barrier
width"), not a periodic fractional-of-capital return. This series is fed
into `validation/metrics.py::run_cpcv_evaluation`, whose
`mean_oos_max_dd` aggregate calls
`validation/stress_scenarios.py::compute_max_drawdown`, which computes a
**compounded** equity curve: `(1 + returns).cumprod()`. That compounding
is only meaningful for genuine capital-fraction returns. Applied to a bare
`±1` outcome series, a single `-1` reads as "-100% of capital in one
period" — and once the compounded equity curve hits exactly 0 under
multiplication, it can never recover (`0 * anything == 0`), permanently
pinning that fold's drawdown at 1.0 regardless of any later wins.

## Per-fold reproduction evidence

Reproduced fully offline (`force_synthetic=True`, no live market data) by
calling `trainer.train_signal()`'s own internals
(`_build_price_panel` → `_build_primary_signal_series` →
`_assemble_training_set` → `compute_cpcv_metrics`) and instrumenting
`run_cpcv_evaluation` to inspect the real per-fold OOS returns:

```
=== timeseries_momentum: n_events=497 ===
compute_cpcv_metrics result: {'dsr': 0.0046, 'pbo': 0.067,
  'mean_oos_sharpe': -0.0839, 'mean_oos_max_dd': 1.0}
  path (0, 1): sharpe=0.1373  n=164 n(-1)=40 n(+1)=41 n(0)=83  max_dd=1.0000
  path (0, 2): sharpe=2.3623  n=164 n(-1)=41 n(+1)=60 n(0)=63  max_dd=1.0000
  path (0, 3): sharpe=-2.1518 n=164 n(-1)=51 n(+1)=35 n(0)=78  max_dd=1.0000
  ... (15/15 paths, ALL max_dd == 1.0000, Sharpe ranging -3.60 to +2.76)

=== cross_sectional_momentum: n_events=514 ===
compute_cpcv_metrics result: {'dsr': 0.0088, 'pbo': 0.20,
  'mean_oos_sharpe': -0.5545, 'mean_oos_max_dd': 1.0}
  path (0, 1): sharpe=-2.5330 n=170 n(-1)=34 n(+1)=19 n(0)=117 max_dd=1.0000
  ... (15/15 paths, ALL max_dd == 1.0000, Sharpe ranging -2.79 to +2.19)
```

Every single OOS fold for both signals contains dozens of "gated in, wrong
direction" events (`n(-1)`, roughly a coin-flip share of gated trades —
consistent with these being experimental, non-deployable models). Any
fold with even one such event trips the permanent-zero-equity pathology
under naive compounding, so the mean is pinned at exactly 1.0 in 100% of
folds, for both models, independent of their true (and very different)
OOS Sharpe. This is a structural statistic/data-type mismatch, not an
occasional degenerate corner case — confirmed NOT a bug in
`compute_max_drawdown` / `run_cpcv_evaluation` themselves: those remain
correct for `scripts/train_lgbm.py`'s genuine daily long-short returns and
`validation/harness.py`'s real pilot-strategy backtests, where a true
single-period -100% return is real, meaningful information that must
stay in the mean, not be excluded.

## Changes made

- **`scripts/train_meta_labelers.py`**: `compute_cpcv_metrics()` no longer
  passes `result["mean_oos_max_dd"]` through — it always returns
  `mean_oos_max_dd: None`, with a comment explaining why (CONSTRAINT #4:
  honest "not computable," never a coerced/fabricated-looking value).
  `dsr`, `pbo`, and `mean_oos_sharpe` are untouched (Sharpe is
  scale/compounding-independent and remains valid on this returns type).
  Docstring updated to match.
- **`ml/registry.yaml`**: corrected the two now-provably-stale entries
  (`meta_labeler_timeseries_momentum`, `meta_labeler_cross_sectional_momentum`)
  from `cpcv_mean_oos_max_dd: 1.0` to `null`, matching what a real re-run
  under the fix now produces. Expanded the header's field-doc comment for
  `cpcv_mean_oos_max_dd` to note the meta-labeler exception.
  `cpcv_mean_oos_sharpe` values (-0.73 / +0.30) are untouched — they were
  never wrong. **`deployable` is unaffected**: `cpcv_mean_oos_max_dd` is
  explicitly documented in the registry's own header as a provenance-only
  field "never read by the deployable gate" (`ml/registry_io.py::compute_deployable`
  only consumes `cpcv_dsr`/`pbo`), so this fix changes no model's
  deployability status.
- **`tests/test_train_meta_labelers.py`**:
  - Updated `test_real_cpcv_populates_metrics_and_gate` to assert
    `cpcv_mean_oos_max_dd is None` post-fix (was asserting it was a
    populated float).
  - Added `test_compute_cpcv_metrics_never_passes_through_a_fabricated_max_dd`:
    stubs `run_cpcv_evaluation` with the exact degenerate `1.0` the real
    bug produced and asserts `compute_cpcv_metrics` suppresses it to
    `None` while `dsr`/`pbo`/`mean_oos_sharpe` still pass through
    unchanged — a synthetic, fast regression test that would have caught
    this bug.

## Validation

```
.venv/bin/python3 -m pytest tests/test_train_meta_labelers.py \
  tests/test_metrics_cpcv_oos_aggregates.py tests/test_train_lgbm.py -q
```
`52 passed` (re-confirmed green after rebasing onto `origin/main`, ~14
unrelated upstream commits ahead — no conflicts, diff unchanged).

Also swept the rest of `ml/registry.yaml` for other suspiciously-exact
max-drawdown values: only these two entries were affected.
`lgbm_ranker`'s own `cpcv_mean_oos_max_dd` (~0.0128), built from genuine
daily long-short returns via `train_lgbm.py::_long_short_returns`, is
correctly non-round and untouched.

## Note on `ml/registry.yaml` overlap

A parallel branch, `fix-zero-sample-pbo-dsr`, also edits `ml/registry.yaml`
but touches a different model entry (`options_meta_labeler`'s
`cpcv_dsr`/`pbo` zero-sample handling) — no overlap with the
`meta_labeler_timeseries_momentum` / `meta_labeler_cross_sectional_momentum`
blocks this PR edits, so no merge conflict is expected between the two.
