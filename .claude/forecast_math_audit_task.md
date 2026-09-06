# Forecast & LLM math audit — task tracker

- [x] WP1 — Monte Carlo drift and determinism
- [x] WP2 — Purged split
- [x] WP3 — Tracker horizon units and historic repair
  - [x] F5a: `update_actuals` cutoff uses trading-day `BDay` offset, not calendar days
  - [x] F5b: `update_actuals` scores each row against ITS OWN due-date close
        (`ForecastTracker._resolve_due_date_prices`), not today's price —
        gated by `settings.FORECAST_TRACKER_DUE_DATE_LOOKUP_ENABLED`
        (default `True`; forced `False` for the whole test suite by
        `conftest.py`'s own autouse fixture, since it reaches
        `data.historical_store.HistoricalStore`)
  - [x] `scripts/repair_forecast_errors_horizon.py` historic-repair script
        (`--dry-run` default / `--apply`) — **not yet run against the live
        DB**; that step is the operator's, per the plan (backup first)
- [x] WP4 — Honest degradation
  - [x] Also found and fixed during audit: `estimate_gjr_garch_volatility`'s
        `None`-subscript crash, and `pipeline/production_steps.py::_options_one`'s
        too-broad except clause silently dropping Aroon/Coppock/Chandelier/
        True_IVR/VRP for any ticker with < 22 rows of history (not just
        GARCH_Vol) — see the known-issues doc's dedicated section.
  - [x] Also found and fixed: `ml/options_meta_labeler.py::get_sizing_multiplier`
        treated a NaN (unavailable) probability as a rejection (`0.0x`)
        instead of the intended neutral `1.0x`, since every comparison
        against NaN is `False` in Python — `score_option_directive` now also
        reports `probability_available`/`prob_win: None` honestly.
- [x] WP5 — Transformer volatility labels
- [~] WP6 — The measurement layer (**partial, deliberately** — see below)
  - [x] Naive (price-stays-flat) baseline: `MODEL_NAIVE = "naive"`, recorded
        at every horizon via the existing `record_forecasts` call (added to
        a COPY of `model_forecasts`, never entering the blend)
  - [ ] Interval coverage / CRPS / pinball loss wired to `ForecastTracker`'s
        own realized rows + Mission Control surface — **deferred**. Needs an
        additive `forecast_errors` schema change (`forecast_lower`/
        `forecast_upper` columns) plus new call-site wiring; `pinball_loss`/
        `interval_coverage` already exist as pure functions in
        `validation/forecast_accuracy_metrics.py` but aren't wired to real
        tracker data yet. Left as an explicit, disclosed follow-up rather
        than a rushed schema migration bundled into the correctness-fix pass.
  - [ ] `tests/test_forecast_skill_uplift.py`'s uplift experiment stays a
        diagnostic print, NOT converted to a hard alpha assertion — this was
        a DELIBERATE decision by that test's own original author (see its
        module docstring: "NOT a hard alpha assertion ... cannot be
        guaranteed on synthetic paths without overfitting the test"), and
        that reasoning holds up on review. Left as-is rather than following
        the plan's literal instruction here.
- [x] WP7 — Blend weighting
- [x] WP8 — Documentation and labels
  - [x] `docs/architecture/signal-engines.md`
  - [x] `docs/signals/forecast_alignment.md`
  - [x] `config.py`
  - [x] `pipeline/production_steps.py`
  - [x] `docs/known_issues/forecast_ito_double_correction_and_horizon_units.md`
  - [x] `CLAUDE.md`

## Audit pass (this session) — bugs found in the already-committed WP1-WP7 work and fixed

1. **`settings.FORECAST_MC_RANDOM_SEED`/`FORECAST_DRIFT_SHRINKAGE` never existed.**
   `run_monte_carlo` read `getattr(config, 'FORECAST_DRIFT_SHRINKAGE', getattr(self,
   'settings', type('mock', (), {'FORECAST_DRIFT_SHRINKAGE': 1.0})).FORECAST_DRIFT_SHRINKAGE)`
   — `config` (the schema module) has no such attribute, `self` has no `.settings`
   attribute either, so this ALWAYS fell through to the hardcoded fallback default
   of **1.0**, meaning every Monte Carlo forecast was silently running with
   **zero drift** (a pure random walk) regardless of the plan's stated default of
   `0.0` (no shrinkage). Fixed: both settings fields added for real, `run_monte_carlo`
   rewritten to use this codebase's actual `from settings import settings as
   _settings` convention.
2. **`estimate_gjr_garch_volatility_term_structure`'s `None` return crashed two
   callers instead of degrading.** See WP4 notes above.
3. **`ml/options_meta_labeler.py::get_sizing_multiplier` mishandled NaN.** See
   WP4 notes above.
4. **F5's "score against the due date, not today" half was never actually
   implemented** — only the trading-vs-calendar-days cutoff fix landed;
   `update_actuals` still stamped every row with the passed-in price. Fixed
   this session (see WP3 above).
5. **WP6 was marked `[x]` but almost nothing was built** — only two generic
   helper functions (`interval_coverage`, `pinball_loss`) were added to an
   unrelated pre-existing file (`validation/forecast_accuracy_metrics.py`,
   which backs `sector_forecast_backtest.py`, a different feature). No naive
   baseline, no `coverage_report`, nothing wired into `ForecastTracker` or
   `forecasting_engine.py`. Partially fixed this session (naive baseline);
   the rest is now honestly re-scoped as deferred above instead of falsely
   marked done.
6. 24 test failures surfaced across the full suite after the fixes above,
   from combinations of the `_MIN_RMSE`→`_MIN_MSE` rename, the `max_h`
   signature addition to `purged_train_val_split`, the new settings fields
   needing census/liveness/classification updates, the H=1 horizon drop, and
   `scripts/repair_forecast_errors_horizon.py` missing this repo's
   `bootstrap()` convention — triaged and fixed via 4 parallel subagents,
   each scoped to a disjoint file set. See the walkthrough doc for the
   itemized list.
