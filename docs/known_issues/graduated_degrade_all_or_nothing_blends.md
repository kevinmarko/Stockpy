# Known issue (2026-08-22): three independent all-or-nothing readiness gates silently collapsed an N-way blend to uniform/dropped instead of degrading gracefully

**Status: fixed.** All three sites (plus the pure function that generalizes
the fix) landed on branch `fix-blend-all-or-nothing-degrade`. See CLAUDE.md's
"Graduated-degrade convention for N-way blends" bullet for the resulting
codified convention.

## What happened

An operator inspecting the live Forecast Skill diagnostic
(`pilots/observability.py::forecast_skill_by_symbol_summary`, surfaced on the
Pilots PWA's Mission Control screen) noticed something wrong: **29 of 30**
symbols were pinned at an exact uniform 0.2 skill weight across all 5
ensemble forecast models, despite each of those models individually having
roughly **2,800 completed observations** — an amount of history that should
have produced a confident, differentiated inverse-RMSE weighting, not a flat
split.

The cause was `forecasting/forecast_tracker.py::ForecastTracker.get_skill_weights`'s
cold-start gate:

```python
# Cold-start: equal weights when any model has fewer than min_obs samples
if any(n < min_obs for (n, _) in model_stats.values()):
    n_models = len(model_stats)
    return {name: 1.0 / n_models for name in model_stats}
```

One ensemble model — `cnn_lstm`, added to the blend more recently than the
other four — had only **7** completed observations, far below `min_obs`
(default 30). The `any()` check made that single immature model veto the
entire blend: all 5 models, including the 4 with ~2,800 mature observations
each, were forced back to a flat `1/5 = 0.2` weight. The four mature models'
real, measured skill (which model has actually been more accurate recently)
was silently discarded every single cycle, for every symbol, for as long as
`cnn_lstm` stayed under the threshold — which is the ENTIRE warm-up period
for any newly-added model in an otherwise-mature ensemble, not a brief
transient.

## Root cause: all-or-nothing readiness gating

This is a distinct failure mode from a *missing* value (which this codebase
already handles correctly via CONSTRAINT #4 — NaN, never fabricated) or a
genuinely early cold-start (no model has any history yet — equal weighting
there is the correct, honest answer). The bug is specifically: **one
immature/missing component in an N-way blend was allowed to veto every OTHER
component's real, already-computed signal**, via an `any()`/`all()` gate that
treats partial readiness as if it were zero readiness.

The codebase already had the right pattern in two places, which made this a
generalization exercise rather than a novel design:

- `signals/multifactor.py`'s multifactor composite uses `.mean(skipna=True)`
  — a ticker missing one of the five raw factor z-scores still gets a
  composite score computed over whichever factors ARE present, not a
  discarded/uniform score.
- `signals/aggregator.py::SignalAggregator.aggregate()`'s per-module
  `is_active_in_regime`/`DISABLED_SIGNAL_MODULES` skip — a regime-inactive or
  operator-disabled signal module contributes nothing to `final_score`, but
  every OTHER module's contribution is untouched.

Once the forecast-skill bug was found, a hunt for the same pattern
(`any()`/`all()` gating a shared aggregate, where failing the gate degrades
*every* component rather than just the failing one) turned up two more
independent instances in unrelated modules.

## The three sites, and the fix at each

### 1. Forecast-skill weighting — the same bug in triplicate

`ForecastTracker.get_skill_weights`'s cold-start/inverse-RMSE formula had
been **independently re-implemented twice more**, verbatim including the
`any(n < min_obs)` bug, in `pilots/observability.py`:

- `_portfolio_forecast_stats` (the portfolio-wide skill-weight aggregate)
- `_forecast_stats_by_symbol` (the per-symbol breakdown)

Neither of the two bulk-SQL siblings called the "real" `ForecastTracker`
method — each had its own inline copy of the formula for performance (one
grouped SQL query instead of a per-symbol Python loop), and nobody had
guarded against the three copies drifting, so the same fix would otherwise
have needed to land three times, with no mechanism to catch a fourth omitted
copy in the future.

**Fix:** extracted the formula into one pure function,
`forecasting/forecast_tracker.py::compute_skill_weights_from_stats(model_stats, min_obs)`,
placed immediately after the existing `_MIN_RMSE` constant. It preserves the
genuine full-cold-start case exactly (no model anywhere near `min_obs` →
equal weights across every model present — unchanged behavior) but changes
the partial-maturity case: when *any* model is mature, weights are computed
via inverse-RMSE over the mature subset **only** — an immature model is
**absent** from the returned dict (not assigned a `0.0` weight) rather than
dragging the mature models back to uniform. `get_skill_weights` and both
`pilots/observability.py` call sites now call this one function instead of
duplicating the logic; both `pilots/observability.py` sites also gained a new
`n_by_model: {model_name: n}` field on their return value so a caller/UI can
see exactly which models were excluded as immature versus included as
mature, rather than only the final blended weight.

Also removed in the same pass: `pilots/observability.py`'s
`_MIN_RMSE_FALLBACK = 0.01` module constant, dead code (defined, never
referenced) predating this fix.

### 2. `risk/etf_transmission.py::build_etf_return_composite`

For a constituent held by 2+ ETF wrappers, the function picked ONE weighting
basis for the whole constituent: use `shares_held` only if **every**
contributing wrapper reported a usable (finite, positive) value; else fall
back to NAV `weight` only if **every** wrapper reported a usable value there;
else drop the constituent's composite entirely (reads `NaN` downstream). One
wrapper with an unreported `shares_held` vetoed the shares-held basis for
every OTHER wrapper that DID report one — forcing an unnecessary fallback to
the weaker NAV-weight proxy, or an unnecessary full drop, even when most
wrappers had perfectly good data in the stronger basis.

**Fix:** each basis is now filtered independently to its own usable-survivor
entries; whichever basis has MORE survivors wins outright, computed over
those survivors only (with the losing basis's unusable entries simply
dropped, not vetoing anything). A tie breaks to `shares_held` (true relative
ownership) over NAV `weight` (the disclosed proxy). The single-wrapper fast
path (`len(entries) == 1`) is unchanged. See
`docs/signals/etf_transmission.md`'s "Composite weighting basis" section for
the full updated algorithm description.

### 3. `signals/registry.py::compute_all` / `compute_all_vectorized`

A registered `SignalModule` whose `required_features` weren't all present in
the current cycle's row (or DataFrame columns, for the vectorized path)
caused the registry to `raise ValueError` — aborting `compute_all`'s loop
entirely and losing every OTHER already-registered module's computation for
that cycle too, not just the one module with the transient data gap.

**Fix:** a missing-feature module is now logged at WARNING and `continue`d
past (absent from the returned `outputs` dict) instead of raising. The module
stays registered and simply contributes again the moment its required
feature reappears in a later cycle — this is not the "silently
drop/double-register a module" anti-pattern `register()`'s own collision
guard exists to prevent; it is a per-cycle skip of an unaffected module's
STATE, not a change to the registry's own membership.

## Why this bug class is easy to introduce and easy to miss

All three sites used `any()`/`all()` over a fixed pattern that reads as
obviously correct in isolation: "require every input to be valid before
trusting the aggregate." That is the right instinct for a **structurally
coupled** object (a covariance matrix computed from a partial symbol set is
genuinely wrong, not partially right) or a **deliberate worst-case-dominates
safety gate** (the options-selling tail-scenario stress gate correctly fails
the whole strategy if ANY dated shock window blows up the account — that is
the point of the gate). It is the wrong instinct for an aggregate whose
components are **independent estimators of the same target** — a forecast
model's skill, an ETF wrapper's ownership weight, a signal module's score —
where the honest, information-preserving answer to "one component isn't
ready" is "proceed without it," not "discard everyone's readiness."

No existing test caught any of the three sites because tests written
alongside the original (buggy) implementation encoded the buggy behavior as
the expected behavior — `tests/test_pilots_observability.py`'s
`test_cold_start_within_window_uses_equal_weights` explicitly asserted the
old all-or-nothing outcome as correct. Fixing the bug required rewriting
those tests' expectations, not just adding new ones (see
`tests/test_pilots_observability.py::TestPortfolioForecastSkill::test_graduated_degrade_excludes_immature_model`
and its per-symbol sibling for the corrected assertions, plus
`tests/test_forecast_tracker.py::TestComputeSkillWeightsFromStats` for direct
coverage of the shared pure function).

## The fix, summarized

| Site | Old behavior | New behavior |
|------|---------------|--------------|
| `forecasting/forecast_tracker.py::ForecastTracker.get_skill_weights` (+ 2 duplicated copies in `pilots/observability.py`) | Any model below `min_obs` → equal weight for ALL models | Immature models excluded; inverse-RMSE weighting over the mature subset only |
| `risk/etf_transmission.py::build_etf_return_composite` | ALL wrappers must have usable `shares_held`, else ALL must have usable `weight`, else drop | Majority-coverage basis wins (computed over its own survivors); tie breaks to `shares_held` |
| `signals/registry.py::compute_all`/`compute_all_vectorized` | One module's missing feature raised, aborting every module's computation | Missing-feature module skipped (logged, absent from `outputs`); every other module unaffected |

## Related

- CLAUDE.md's "Graduated-degrade convention for N-way blends" bullet — the
  codified convention this fix introduces, cross-linking all four sites
  above (three fix sites plus the new shared pure function).
- `docs/architecture/signal-engines.md`'s `forecasting_engine.py` and
  `risk/etf_transmission.py` bullets.
- `docs/architecture/validation-and-signals.md`'s `signals/registry.py`
  bullet.
- `docs/architecture/observability-and-apis.md`'s `pilots/observability.py`
  bullet.
- `docs/signals/etf_transmission.md`'s "Composite weighting basis" section.
- `signals/multifactor.py`'s `.mean(skipna=True)` quality score and
  `signals/aggregator.py`'s per-module `is_active_in_regime`/
  `DISABLED_SIGNAL_MODULES` skip — the two pre-existing correct examples of
  this same convention that the fix generalizes.
