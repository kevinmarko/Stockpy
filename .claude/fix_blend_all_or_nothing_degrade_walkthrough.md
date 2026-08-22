# Walkthrough: Fix all-or-nothing readiness gates in N-way blends

Branch: `fix-blend-all-or-nothing-degrade` (off `origin/main`)

## Summary

Fixed three independent sites where an `any()`/`all()` readiness gate let
one immature/missing component in an N-way blend silently collapse the
whole blend to a uniform/dropped fallback, discarding every other
component's real, already-computed signal. Generalized the fix into a new
codified CLAUDE.md convention ("Graduated-degrade convention for N-way
blends").

## What changed, by file

### Fix 1 — forecast-skill weighting
- `forecasting/forecast_tracker.py`: added `compute_skill_weights_from_stats(model_stats, min_obs)`
  right after `_MIN_RMSE`, a pure function replacing the inline
  `any(n < min_obs) -> equal weights for everyone` logic. Full cold-start
  (no model mature) still returns equal weights across every model present
  (unchanged). Partial maturity now computes inverse-RMSE weights over the
  mature subset only — an immature model is absent from the result, not
  zeroed. `ForecastTracker.get_skill_weights` now just calls this function.
- `pilots/observability.py`: `_portfolio_forecast_stats` and
  `_forecast_stats_by_symbol` had each independently re-implemented the
  same buggy formula inline. Both now call the shared function instead.
  Removed the now-dead `_MIN_RMSE_FALLBACK` constant (confirmed unused
  elsewhere in the file). Both functions, and their callers
  (`portfolio_forecast_skill`, `forecast_skill_by_symbol_summary`), now
  also surface a new `n_by_model: {model_name: n}` field.

### Fix 2 — `risk/etf_transmission.py::build_etf_return_composite`
Replaced the strict all-or-nothing weighting-basis selection (every
contributing wrapper must have a usable `shares_held`, else every wrapper
must have a usable NAV `weight`, else drop the constituent) with per-entry
filtering: each basis (`shares_held`, `weight`) is filtered independently
to its own usable (finite, positive) survivors, and whichever basis has
MORE survivors wins outright, computed over those survivors only. A tie
breaks to `shares_held`. The single-wrapper (`len(entries) == 1`) fast
path is untouched. Docstring's "Weighting basis" section rewritten to
describe the new algorithm.

### Fix 3 — `signals/registry.py`
`compute_all`/`compute_all_vectorized` now log a WARNING and `continue`
(skip, absent from `outputs`) for a module whose `required_features`
aren't present this cycle, instead of `raise ValueError(...)` — which
previously aborted every OTHER already-registered module's computation
for that cycle too.

## Documentation

- New CLAUDE.md bullet: "Graduated-degrade convention for N-way blends",
  placed next to the existing "Degenerate-std guard convention" bullet.
  Auto-mirrored onto AGENTS.md by the repo's `sync_agent_docs.sh`
  PostToolUse hook (verified: both files are byte-identical in size after
  the edit — no manual AGENTS.md edit was made).
- `docs/architecture/signal-engines.md`: extended the existing
  `forecasting_engine.py` bullet with the skill-weighting fix, and the
  existing `risk/etf_transmission.py` bullet with the composite-weighting
  fix.
- `docs/architecture/validation-and-signals.md`: extended the existing
  `signals/registry.py` bullet with the per-cycle skip fix.
- `docs/architecture/observability-and-apis.md`: added a new dedicated
  `pilots/observability.py` bullet (confirmed the file had none before).
- `docs/signals/etf_transmission.md`: rewrote the "Composite weighting
  basis" section to describe majority-coverage-wins + shares_held
  tie-break instead of the old all-or-nothing algorithm.
- New `docs/known_issues/graduated_degrade_all_or_nothing_blends.md`,
  documenting the live evidence (29/30 symbols pinned at a uniform 0.2
  skill weight despite ~2,800 completed observations each, due to one
  newly-added `cnn_lstm` model with only 7 observations tripping the old
  `any(n < min_obs)` gate), root cause, the three fix sites, and why the
  bug class is easy to introduce/miss. Added an index row to
  `docs/known_issues/README.md`.

## Tests

- `tests/test_forecast_tracker.py`: added `TestComputeSkillWeightsFromStats`
  (5 new tests) covering empty input, full cold-start, graduated degrade
  (the bug-fix case), multiple mature models, and the `_MIN_RMSE` guard
  over the mature subset.
- `tests/test_pilots_observability.py`: rewrote
  `test_cold_start_within_window_uses_equal_weights` (portfolio-wide) and
  `test_cold_start_within_window_uses_equal_weights_per_symbol`
  (per-symbol) — both had encoded the OLD all-or-nothing behavior as the
  expected/correct outcome, so fixing the bug required rewriting their
  assertions, not just adding new tests. Renamed to
  `test_graduated_degrade_excludes_immature_model[_per_symbol]` and
  updated to assert the immature model is excluded, not just diluted.
  Added `n_by_model` to the exact-dict-equality assertion for the NVDA
  zero-history row.
- `tests/test_etf_transmission.py`: added two tests to
  `TestBuildETFReturnComposite` — a 4-wrapper majority-coverage-wins case
  (2 shares-held survivors beat 1 NAV-weight survivor outright) and a
  3-wrapper tie case (1-vs-1, tie breaks to shares_held). Traced every
  pre-existing test in that class against the new logic by hand before
  running — all use either the single-wrapper fast path or full coverage
  in one basis, so none needed changes; confirmed by the test run.
- `tests/test_signal_registry.py`: replaced
  `test_signal_registry_missing_features` (previously asserted a raise)
  with an assertion that the module is silently absent from `outputs` and
  no exception is raised. Added
  `test_signal_registry_missing_features_skip_is_per_module_not_global`
  (two registered modules, only one satisfied) proving the skip is
  per-module, not a whole-registry abort — the fully-satisfied module's
  output is present and correct.

## Verification

**Targeted tests** (`pytest tests/test_forecast_tracker.py
tests/test_pilots_observability.py tests/test_etf_transmission.py
tests/test_signal_registry.py -v`): **193 passed, 0 failed.**

**Broader offline gate** (`pytest -m "not network and not slow" -q`,
run against the exact committed state, post-rebase onto the latest
`origin/main`): **11,870 passed, 13 skipped, 92 deselected, 2 failed**
(393.45s). Both failures are pre-existing and unrelated to this diff:

- `tests/test_settings_liveness.py::TestCommittedArtifactIsFresh::test_committed_json_matches_a_fresh_run`
  — fails identically on `origin/main` itself; `tests/test_settings_liveness.py`,
  `docs/settings_liveness.json`, and `settings.py` are byte-identical
  between this branch and `origin/main` (confirmed: none of them appear
  in this PR's file diff at all), so nothing in this change could have
  caused or fixed this staleness.
- `tests/test_reports_library.py::TestInlineViewToggle::test_hide_button_closes_the_report`
  — passes cleanly when run in isolation; an order-dependent flake
  surfaced only under the full-suite run, in a file this PR does not
  touch.

**Genuine-bug lint gate** (`python -m ruff check . --select=F821,F822,F823,E9`
on every changed file): all clean.

## Branch hygiene note

While this PR was in progress, `origin/main` advanced by one unrelated
commit (PR #852, `technical_options_engine.py`'s VRP NaN-guard fix). The
branch was rebased cleanly onto the new `origin/main` tip with zero
conflicts; the final diff against `origin/main` covers exactly the 18
files this PR intends to touch — confirmed via `git diff origin/main --stat`
immediately before pushing.

Separately, this shared worktree directory carries roughly two dozen old
`git stash` entries left behind by unrelated past sessions going back
months. A diagnostic `git stash`/`git stash pop` pair run during
verification (checking whether a test failure pre-dated this branch)
accidentally popped one of those old stashes (`stash@{0}`, unrelated WIP
docs/webapp changes from a different session) into the working tree,
producing merge conflicts. This was caught immediately via `git status`;
`git reset --hard HEAD` cleanly discarded the accidental pop without
touching the stash itself (git does not auto-drop a stash on a conflicted
pop, so it remained safely in the stash list throughout — confirmed via
`git stash list` before and after). The working tree was verified clean
and identical to this PR's intended 18-file diff before the branch was
pushed.

## PR

Opened against `main`. Not merged (per task scope — review only).
