# Fix CPCV Mean OOS Max Drawdown Fabrication (Meta-Labelers)

## Problem
`get_model_registry_status` (and `ml/registry.yaml` directly) showed BOTH
meta-labeler models pinned at `cpcv_mean_oos_max_dd: 1.0` (a literal 100%
average out-of-sample max drawdown across CPCV folds):

- `meta_labeler_timeseries_momentum`: mean OOS Sharpe -0.73, max DD 1.0
- `meta_labeler_cross_sectional_momentum`: mean OOS Sharpe +0.30, max DD 1.0

A mean max-drawdown of exactly 1.0 coexisting with a POSITIVE mean OOS
Sharpe is internally inconsistent for a well-formed backtest and warranted
investigation before trusting either number.

## Investigation plan
1. Locate where `cpcv_mean_oos_max_dd` is computed for the meta-labelers:
   `scripts/train_meta_labelers.py::compute_cpcv_metrics` →
   `validation/metrics.py::run_cpcv_evaluation` →
   `validation/stress_scenarios.py::compute_max_drawdown`.
2. Reproduce offline using `trainer.train_signal(..., force_synthetic=True)`
   (no live market data needed) and instrument `run_cpcv_evaluation` to
   inspect the actual per-fold returns/drawdowns feeding the mean.
3. Determine whether this is a real calculation bug or a genuine, if
   surprising, result.
4. If a real bug: fix the root cause without silently coercing bad data
   into a plausible-looking number — an unmeasurable/inapplicable fold or
   statistic must be excluded/reported as `None`, never coerced.
5. Add a synthetic regression test that would have caught the bug.
6. Sweep the rest of `ml/registry.yaml` for other suspiciously-exact
   drawdown values.

## Root cause found
`scripts/train_meta_labelers.py::_meta_gated_returns()` produces a
discrete per-event outcome in `{-1, 0, +1}` — an R-multiple of the
triple-barrier width (win / flat / loss on the primary signal's gated
event), NOT a periodic fractional-of-capital return. `run_cpcv_evaluation`'s
`mean_oos_max_dd` is a COMPOUNDED equity-curve drawdown
(`(1 + returns).cumprod()`), a statistic that is only meaningful for
genuine capital-fraction returns (e.g. `scripts/train_lgbm.py`'s daily
long-short spread). Compounding a bare `±1` outcome series permanently
zeroes "equity" the instant any single gated event loses (a `-1` reads as
-100% of capital; once compounded equity hits exactly 0 under
multiplication it can never recover). Confirmed via a real offline CPCV
re-run: every one of 15/15 OOS paths for BOTH signals contained enough
directionally-wrong gated events (roughly a coin-flip win rate, consistent
with these being experimental, non-deployable models) to trip this on
every single fold — pinning the mean at exactly 1.0 regardless of the
model's true OOS quality (and regardless of Sharpe's sign).

This is a call-site unit mismatch, not a bug in `compute_max_drawdown` /
`run_cpcv_evaluation` themselves — both remain correct and unchanged for
`scripts/train_lgbm.py`'s genuine daily returns and for
`validation/harness.py`'s real pilot-strategy backtests, where a true
single-period -100% blowup is real, meaningful information that must stay
in the mean.

## Solution
- `scripts/train_meta_labelers.py::compute_cpcv_metrics` no longer passes
  `mean_oos_max_dd` through from `run_cpcv_evaluation`'s result — it
  reports `None` (CONSTRAINT #4: honest "not computable from this returns
  representation," never a coerced/misleading number). `dsr`/`pbo`/
  `mean_oos_sharpe` are unaffected (Sharpe is scale/compounding-independent
  and remains a valid statistic on this returns series).
- Corrected the two already-committed, now-provably-stale
  `ml/registry.yaml` entries (`cpcv_mean_oos_max_dd: 1.0` → `null`) to
  match what a real re-run under the fix now produces, and expanded the
  registry's field-doc comment to note the meta-labeler exception.
- `cpcv_mean_oos_max_dd` is a provenance-only field per the registry's own
  header docs (never read by the `deployable` gate) — this fix has zero
  effect on model deployability.

## Verification
- `.venv/bin/python3 -m pytest tests/test_train_meta_labelers.py tests/test_metrics_cpcv_oos_aggregates.py tests/test_train_lgbm.py -q`
