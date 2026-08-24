# Fix 4 confirmed core-statistics/signal-scoring audit findings + 1 doc drift

## Context

A prior audit pass ("this session's core-statistics and signal-scoring audits")
produced four independently-confirmed, low/moderate-severity findings plus one
bonus doc-drift item, all already root-caused with exact file/line references
in the user's report. Every one was re-verified directly against the current
worktree before any fix was made (line numbers below are confirmed, not
assumed). None are live bugs that produce a false PASS today — they're a
mishandled edge case, a CONSTRAINT #4 gap on an unreachable-but-directly-callable
code path, a convention mismatch that could mislead a future reader, and a
duplication/doc drift that risks silent divergence later. This plan fixes all
five, following CLAUDE.md's branch/PR workflow (this touches `validation/`,
orchestrator code, and docs).

## Findings → Fixes

### 1. `compute_max_drawdown` first-day-wipeout → NaN instead of 1.0
**File:** `validation/stress_scenarios.py:163-175`

When `returns[0] == -1.0`, `equity` and `running_max` are both 0 for the whole
series, so `(equity - running_max) / running_max` is a `0/0` → NaN at every
point, and `.min()` over an all-NaN series is NaN. The docstring already
*claims* "a single -100% day produces a 1.0 (total) drawdown" — this fix makes
that true in the one case it currently isn't (day-1 wipeout; a mid-series
wipeout already works correctly because `running_max` was nonzero before it).

**Fix:** guard the `running_max == 0.0` case explicitly and report a full
drawdown for those points instead of letting the division produce NaN. No
caller-visible behavior change except this one edge case; `account_survived`
is untouched (it already treats any `<= -1.0` day as non-survival).

### 2. `probability_of_backtest_overfitting` empty-input guard fabricates `0.0`
**File:** `validation/metrics.py:339-341`

The empty-input early return (`n_paths == 0 or n_strategies == 0: return 0.0`)
is a "no overfitting" PASS sentinel on literally no data, inconsistent with the
same function's own later convention (`measurable_paths == 0: return
float("nan")`). Currently unreachable via `run_cpcv_evaluation()` (its own
upstream `_empty_result` guard returns fail-closed `pbo: 1.0` first), but the
function is directly importable/callable, so a future/test caller passing
empty arrays would silently get a false PASS.

**Fix:** change the early return to `float("nan")`, matching the function's
own bottom-of-function convention. One-line change; grep-audited to confirm no
other production caller relies on the old `0.0` empty-input behavior
(`run_cpcv_evaluation`, `autonomous_backtest_runner.py`, and
`options_harness.py` all guard against calling this function with empty
arrays in the first place).

### 3. Calmar ratio convention mismatch — document, don't unify
**Files:** `validation/harness.py:917-922` (full-sample) and `:947-959`
(OOS-gate branch); `evaluation_engine.py:892-899`

`harness.py` computes Calmar as `full_returns.mean() * inferred_freq /
max_dd` — an **arithmetic-mean annualization**. `evaluation_engine.py`
computes real geometric CAGR / `abs(max_drawdown)` — the textbook CAGR-based
definition. Both are internally correct and already apply the repo's `1e-12`
degenerate-std/dd guard convention correctly. Unifying the two would change
`STRATEGY_REGISTRY`-recorded Calmar values for every currently-validated
strategy with no live-market network access available in this sandbox to
re-verify the registry — too risky for this fix. This fix documents the
divergence instead of unifying (comments in both `.py` files plus a
cross-referencing sentence in each of the two owning architecture docs).

### 4. Cross-sectional 12-1m momentum duplicated 3x, only 2 of 3 have a parity test
**Files:** `main_orchestrator.py::compute_xsec_momentum_ranks` (defined, not
called in production), `pipeline/production_steps.py::_compute_xsec_momentum`
(the real orchestrator path), `main.py::_build_context_extras`'s inline copy
(the advisory path's own third copy — not mentioned in the "keep in lockstep"
comment and with no existing parity test).

`tests/test_xsec_momentum.py` already parity-tests the first two. Nothing
covered the third. Full extraction into one shared helper would touch the
advisory hot path with no test today proving byte-identical output first —
riskier than the finding's stated severity warrants. Fix: close the CI gap
with a new parity test (`tests/test_xsec_momentum_advisory_parity.py`), and
update the stale/incomplete "keep in lockstep" docstrings/comments in both
production files so a future maintainer knows all three copies exist.

### 5. (Bonus, doc-only) stale Quality-factor formula in `docs/signals/multifactor.md`
Confirmed against the real code (`processing_engine.py:513-534`): Quality is
the mean of whichever of `{returnOnEquity, operatingMargins, grossMargins}`
are actually present (falling back to `-debt_to_equity` when none are), not a
fixed `0.5 * roe + 0.5 * operating_margin`. Fixed the Rationale table row and
the Factor Construction code block to match.

## Execution note

All five items landed on one branch/PR (`fix-stats-audit-findings`) rather
than splitting the doc-only item (#5) to a direct-to-`main` commit as
originally scoped — the four parallel implementation agents used to execute
this plan all worked in the same worktree/branch, and #5 is small enough
(9 lines) that bundling it added negligible review surface versus the
overhead of a separate commit/push. Every other file-level and
verification detail matches the plan as approved.

## Verification (all executed, see walkthrough for results)

- Targeted regression sweep across every touched-area test file.
- Genuine-bug-only ruff gate (`ruff check . --select=F821,F822,F823,E9`).
- Full offline CI suite (`pytest -m "not network and not slow"`, mirroring
  `.github/workflows/ci.yml`'s `test` job / `make ci`).
- Manual diff review of every changed file before commit.
