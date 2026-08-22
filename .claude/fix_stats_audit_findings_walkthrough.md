# Walkthrough — fix-stats-audit-findings

Five fixes from an audit pass over `validation/` core-statistics code and
signal-scoring documentation. Nothing here changes a live deployability
verdict for any registered strategy — see below for why each fix is safe.

## 1. `validation/stress_scenarios.py::compute_max_drawdown`

**Before:** a daily-returns series whose very first observation was exactly
`-100%` produced `NaN` instead of the correct `1.0` (total) drawdown, because
`running_max` never leaves `0` once equity is wiped out on day one, turning
the drawdown ratio into an unguarded `0/0`. A mid-series wipeout already
worked correctly (running_max was nonzero going into it).

**After:** the `running_max == 0.0` case is guarded explicitly and reports a
full (`1.0`) drawdown at those points instead of `NaN`. `StressResult.passed`
already treated NaN as a fail, so this was never a false-PASS bug — it was an
under-reported magnitude in exactly the tail-event scenario this module
exists to measure precisely.

New tests in `tests/test_stress_runner.py`: the day-1-wipeout case now
asserts `1.0`; the pre-existing mid-series-wipeout behavior is pinned as a
regression test; the empty-series → `NaN` short-circuit is confirmed
unaffected.

## 2. `validation/metrics.py::probability_of_backtest_overfitting`

**Before:** calling this function with empty `(n_paths == 0)` input arrays
returned a fabricated `0.0` ("no overfitting detected") — a real CONSTRAINT #4
gap, inconsistent with the same function's own later
`measurable_paths == 0 → NaN` convention.

**After:** the empty-input guard now also returns `NaN`. Confirmed via grep
that no production caller depends on the old behavior — `run_cpcv_evaluation`,
`autonomous_backtest_runner.py`, and `options_harness.py` all short-circuit
before ever calling this function with empty arrays, so this closes a
currently-unreachable-but-directly-callable gap without changing any live
report.

## 3. Calmar ratio convention — documented, not unified

`validation/harness.py` computes Calmar via arithmetic-mean annualization
(`mean(returns) * freq / max_dd`, matching that file's own Sharpe/Sortino
convention); `evaluation_engine.py` computes it via compounded CAGR /
`abs(max_drawdown)` (the textbook definition). Both are correct in their own
context and both already apply the repo's `1e-12` degenerate-value guard —
this was never a bug, but the two numbers are not directly comparable for the
same underlying returns, which could mislead a future reader who assumes they
are. Rather than unifying (which would silently change every
`STRATEGY_REGISTRY`-recorded Calmar value with no live-market access in this
sandbox to re-verify the fleet), this fix adds explanatory comments at both
Calmar computation sites in each `.py` file, plus one cross-referencing
sentence in each file's owning architecture doc
(`docs/architecture/validation-and-signals.md` /
`docs/architecture/simulation-eval-reporting.md`).

## 4. Cross-sectional 12-1m momentum — 3-way parity test added

The Jegadeesh-Titman 12-1m momentum formula was hand-duplicated in three
places (`main_orchestrator.py::compute_xsec_momentum_ranks`,
`pipeline/production_steps.py::_compute_xsec_momentum`, and
`main.py::_build_context_extras`'s inline copy), with an existing test
covering only the first two. All three currently agree numerically (verified,
not assumed — the new test passed on its first run with no code changes
needed to the three duplicated implementations). Added
`tests/test_xsec_momentum_advisory_parity.py`, which builds a synthetic
multi-ticker `bars_dict` and asserts `main.py`'s raw 12-1m returns and
resulting rank ordering agree with the other two implementations within the
repo's documented `1e-5` numeric-drift tolerance. Updated the stale
"keep the two in lockstep" docstring in `pipeline/production_steps.py` (which
only named two of the three copies) and added a matching comment in `main.py`
— both now name all three copies and point at the new test as the
drift-detection mechanism instead of relying solely on a hand-maintained
comment.

## 5. (Bonus) `docs/signals/multifactor.md` Quality-factor formula

Two stale spots described Quality as a fixed `0.5 * roe + 0.5 *
operating_margin` two-metric formula. The real code
(`processing_engine.py:513-534`) computes it as the mean of whichever of
`{returnOnEquity, operatingMargins, grossMargins}` are actually present that
cycle (skipping missing ones, never treating a missing one as `0.0`),
falling back to `-debt_to_equity` only when none of the three are available.
Fixed the Rationale table row and the Factor Construction code block to
match.

## Verification

- Targeted regression sweep (10 test files across all touched areas): 109 passed.
- Genuine-bug-only lint gate (`ruff check . --select=F821,F822,F823,E9`): clean.
- Full offline CI suite (`pytest -m "not network and not slow"`, the same
  gate `.github/workflows/ci.yml`'s `test` job runs): 12057 passed, 13
  skipped, 0 failed.
- Every diff was read in full before commit; the two Calmar-comment files and
  the two xsec-momentum docstring files contain zero logic/behavior changes
  (comments and docstrings only).
