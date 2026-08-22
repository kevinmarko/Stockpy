# Implementation Plan: Fix all-or-nothing readiness gates in N-way blends

**Branch:** `fix-blend-all-or-nothing-degrade` (off `origin/main`)
**Date:** 2026-08-22

## Bug class

An aggregate/blend of independent estimators (signal modules, ensemble
forecast models, ETF-wrapper composites), gated by `any()`/`all()` readiness
checks, where ONE immature/missing component silently collapses the WHOLE
blend to a degraded fallback — discarding every other component's real
signal. This codebase already gets this right elsewhere
(`signals/multifactor.py`'s `.mean(skipna=True)` quality score,
`signals/aggregator.py`'s per-module `is_active_in_regime`/
`DISABLED_SIGNAL_MODULES` skip) — the fix generalizes that pattern: exclude
the immature/missing component and renormalize/proceed over the survivors,
instead of reverting everything to uniform/aborting.

## Fix 1 — forecast-skill weighting (3 literal copies of the same bug)

Add `compute_skill_weights_from_stats(model_stats, min_obs)` to
`forecasting/forecast_tracker.py`, right after `_MIN_RMSE`. Pure function:
full cold-start (no model mature) → equal weights across every model
present (unchanged); any model mature → inverse-RMSE weights over the
mature subset only, immature models absent from the result (not `0.0`).

Rewire three call sites to use it instead of duplicating the formula:

- `ForecastTracker.get_skill_weights` (`forecasting/forecast_tracker.py`)
- `pilots/observability.py::_portfolio_forecast_stats`
- `pilots/observability.py::_forecast_stats_by_symbol`

Remove the now-dead `_MIN_RMSE_FALLBACK` constant in `pilots/observability.py`
(confirmed unused). Add a new `n_by_model: {model_name: n}` field threaded
through `_portfolio_forecast_stats` → `portfolio_forecast_skill()`, and
`_forecast_stats_by_symbol` → `forecast_skill_by_symbol_summary()`.

## Fix 2 — `risk/etf_transmission.py::build_etf_return_composite`

Replace the all-or-nothing basis selection (require ALL contributing
wrappers to have usable `shares_held`, else ALL to have usable NAV
`weight`, else drop) with per-entry filtering: each basis filtered
independently to its own usable survivors; whichever basis has more
survivors wins, computed over those survivors only; a tie breaks to
`shares_held`. The `len(entries) == 1` fast path is untouched.

## Fix 3 — `signals/registry.py::compute_all` / `compute_all_vectorized`

Change `raise ValueError(...)` on a missing `required_features` entry into
a `logger.warning(...)` + `continue` (skip that module for this cycle,
absent from `outputs`) — do not abort every other module's computation.

## Documentation

1. New CLAUDE.md bullet: "Graduated-degrade convention for N-way blends"
   (auto-mirrored to AGENTS.md by `sync_agent_docs.sh`).
2. Update `docs/architecture/signal-engines.md`'s `forecasting_engine.py`
   and `risk/etf_transmission.py` bullets.
3. Update `docs/architecture/validation-and-signals.md`'s
   `signals/registry.py` bullet.
4. Add a new `pilots/observability.py` bullet to
   `docs/architecture/observability-and-apis.md`.
5. Update `docs/signals/etf_transmission.md`'s "Composite weighting basis"
   section to describe majority-coverage-wins + tie-break.
6. New `docs/known_issues/graduated_degrade_all_or_nothing_blends.md` +
   index row in `docs/known_issues/README.md`.

## Verification

- `pytest tests/test_forecast_tracker.py tests/test_pilots_observability.py tests/test_etf_transmission.py tests/test_signal_registry.py -v`
- `pytest -m "not network and not slow" -q` (broader offline gate; only
  failures caused by this diff block completion)

## PR mechanics

Per CLAUDE.md: copy this plan + a task tracker + a walkthrough into
`.claude/`, commit, push, open a PR against `main`. Do not merge.
