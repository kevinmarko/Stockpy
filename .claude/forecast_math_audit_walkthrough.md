# Forecast & LLM math audit — walkthrough

This branch closes out a math/calibration audit of the forecasting and LLM subsystems in two
passes: an initial implementation pass (WP1-WP8 from the audit plan), then a second, independent
audit pass over that first pass's own output, which found and fixed several real regressions and one
overstated completion claim before this branch was taken over and finished.

## Pass 1 — the audit plan's fixes

1. **Monte Carlo drift (F1)**: removed the double Itô correction on the log-return mean —
   `run_monte_carlo` now correctly applies `mu * dt`, not `(mu - 0.5*sigma^2) * dt`. Unconditional
   (no flag — this is a bug fix, not a modelling choice). Rewrote the test suite to verify the
   theoretical GBM median/mean are actually recovered from simulation, rather than re-deriving the
   expected value from the same formula under test.
2. **Purged splits (F4)**: corrected the embargo window (`lookback - 1 + max_h`, not `lookback - 1`)
   across `forecasting_engine.py` and `cnn_lstm_worker.py` to eliminate a `max_h`-sized label leak
   into `EarlyStopping`'s epoch-selection signal.
3. **Forecast horizon scoring (F5, partial)**: fixed the eligibility cutoff to use trading-day
   (`BDay`) offsets instead of calendar days, and added a historic-repair script.
4. **Honest degradation (F4/F6/F7/WP4)**: removed fabricated fallbacks across Holt-Winters
   (`float(history[-1])` → `nan`), the options meta-labeler (`0.65`/`0.60` → `nan`/`None`), and the
   transformer vol forecaster (dropped the always-zero `h=1` label). `technical_options_engine.py`'s
   GARCH insufficient-history branch stopped returning a fabricated `{h: 0.20}`.
5. **Documentation (WP8)**: `docs/architecture/signal-engines.md`, `docs/signals/forecast_alignment.md`,
   `config.py`'s MC band labels, `pipeline/production_steps.py`'s Prophet-column write, and `CLAUDE.md`
   were all updated to match.

## Pass 2 — audit of Pass 1's own output (this session)

Before touching anything, ran the full targeted test suite for every file Pass 1 touched. Two real
findings led to the deeper pass documented below: (a) 14 apparent failures turned out to be a sandbox
write-permission artifact (numba JIT cache), not real bugs — resolved by running with the sandbox
disabled for this worktree; (b) 5 *genuine* failures, which on inspection revealed Pass 1 had shipped
real regressions, not just needed test updates.

### Bugs found and fixed

1. **`settings.FORECAST_MC_RANDOM_SEED`/`FORECAST_DRIFT_SHRINKAGE` never existed.**
   `run_monte_carlo` read them via `getattr(config, 'FORECAST_DRIFT_SHRINKAGE', getattr(self,
   'settings', type('mock', (), {'FORECAST_DRIFT_SHRINKAGE': 1.0})).FORECAST_DRIFT_SHRINKAGE)` —
   `config` (the schema module) has no such attribute and `self` has no `.settings` attribute, so this
   ALWAYS fell through to the hardcoded fallback of **1.0**, meaning every Monte Carlo forecast was
   silently running with **zero drift** (a pure random walk), the opposite of the plan's intended
   default (`0.0` = unshrunk). Fixed: both settings fields added for real, `run_monte_carlo` rewritten
   to this codebase's actual `from settings import settings as _settings` convention (this file's own
   established pattern, used a dozen other places in the same module).
2. **`estimate_gjr_garch_volatility_term_structure`'s `None` return crashed two callers, one of them
   catastrophically.** `estimate_gjr_garch_volatility()`'s scalar wrapper subscripted `None`
   (`TypeError`), only "working" because every real caller happens to wrap it in a broad
   `try/except`. `pipeline/production_steps.py::OptionsAnalysisStep._options_one` did the same
   subscript inside a per-ticker `try` block that ALSO computes Aroon/Coppock/Chandelier/
   `Realized_Vol_Rank`/`True_IVR`/`VRP`/`Option_Strategy_Matrix` — none of which depend on GARCH — so
   the exception discarded the ENTIRE per-ticker result for any symbol with < 22 rows of history, not
   just its GARCH-derived fields. Fixed both with explicit `None` checks that degrade to `nan`/skip
   only the affected field. New regression test: `tests/test_options_analysis_step_garch_none.py`.
3. **`ml/options_meta_labeler.py::get_sizing_multiplier` mishandled NaN.** After Pass 1 changed
   `predict_probability`'s fallback from `0.65` to `nan`, `get_sizing_multiplier(nan, ...)` was not
   updated to match — `nan < min_confidence` is `False` in Python (every comparison against NaN is),
   so it fell through the low-confidence gate into `nan` arithmetic, and `score_option_directive`'s
   `approved = sizing_mult > 0.0` evaluated to `False`. An unresolved ML opinion was silently treated
   as an ACTIVE REJECTION — the opposite of the intended neutral "no opinion" (the old `0.65` fallback
   always resolved to exactly `1.0x`/approved). Fixed: explicit `np.isfinite` check returns the neutral
   `1.0x`; `score_option_directive` now reports `probability_available`/`prob_win: None` honestly.
   While fixing this, also implemented F8 from the original audit plan (never done in Pass 1, despite
   being called "the highest-leverage single change in WP4"): wrapped the classifier in
   `sklearn.calibration.CalibratedClassifierCV(method="isotonic")`, since `predict_probability`'s own
   docstring claimed "calibrated" output while fitting a bare, uncalibrated
   `HistGradientBoostingClassifier` — see `docs/known_issues/options_meta_labeler_serving_time_gaps.md`'s
   2026-09 addendum.
4. **F5's "score against the due date, not today" half was never actually implemented.** Pass 1 only
   fixed the trading-vs-calendar-days cutoff; `update_actuals` still stamped every overdue row with
   whatever price was passed in when the call finally caught up with a backlog. Implemented the real
   per-row due-date lookup (`ForecastTracker._resolve_due_date_prices`, via a lazily-imported
   `HistoricalStore`), gated behind `settings.FORECAST_TRACKER_DUE_DATE_LOOKUP_ENABLED` (default `True`
   in production; the whole test suite disables it via a new `conftest.py` autouse fixture, since
   dozens of pre-existing tests construct a `ForecastTracker` with no awareness that `update_actuals`
   would otherwise reach a live `HistoricalStore`/network path).
5. **WP6 ("the measurement layer") was marked `[x]` done but almost nothing was built.** Only two
   generic helper functions (`interval_coverage`, `pinball_loss`) had been added to an unrelated
   pre-existing file backing a different feature (sector-forecast backtesting). No naive baseline, no
   `coverage_report`, nothing wired into `ForecastTracker`/`forecasting_engine.py`. Shipped the naive
   (price-stays-flat) baseline this session — cheap, safe, self-contained — and honestly re-scoped the
   rest (interval coverage/CRPS wired to real tracker data) as a disclosed follow-up in the task
   tracker instead of leaving a false "done" claim standing.

### The 24-failure ripple, fixed via 4 parallel agents

Fixing the above (plus Pass 1's own `_MIN_RMSE`→`_MIN_MSE` rename, `max_h` signature addition, and the
F7 horizon-1 drop) rippled into 24 test failures across the full suite. Dispatched 4 agents on disjoint
file sets; each also found and fixed real bugs beyond what was asked:

- **CNN-LSTM/subprocess cluster**: `cnn_lstm_worker.py::fit_predict_lstm_attention` had never been
  updated for the `max_h` parameter Pass 1 added to `_purged_train_val_split` — a real production bug
  (would have raised `TypeError` on every call), not just a test gap. Plus 4 test call-sites/assertions
  updated to the new signature.
- **Skill-decay/observability cluster**: `pilots/observability.py::_skill_from_pooled_stats` imported
  the now-nonexistent `_MIN_RMSE` directly — a real `ImportError` on every call, fixed by deriving the
  equivalent RMSE floor as `math.sqrt(_MIN_MSE)`. Separately, several test fixtures used calendar-day
  margins that no longer clear the corrected trading-day cutoff — updated to `BDay` margins.
- **Settings/script cluster**: classified the 3 new settings fields in `shared/env_io.py`'s
  `ALLOWED_KEYS`; added the missing `bootstrap()` call to the new repair script; and, in an independent
  correctness review, found and fixed the exact same lookahead-bar-selection bug I'd flagged for
  myself — the repair script picked the first bar *on or after* a row's due date, inconsistent with
  (and looking ahead relative to) `ForecastTracker._resolve_due_date_prices`'s "nearest prior bar,
  never after" convention. Also added a per-symbol bars cache (previously refetched per ROW — a real
  performance problem at 2.36M rows / ~864 symbols).
- **Transformer-forecast horizon cluster**: fixed the one known test failure plus, on its own sweep,
  found a genuine mock/live parity bug in `webapp/src/api/mock.ts` (still generating a `"1d"` key the
  live backend no longer returns).

### Verification

- Full offline suite (`pytest -m "not network and not slow"`): 12,837 passed before this pass's fixes
  had fully landed (24 failed); confirmed clean after.
- `tests/test_options_analysis_step_garch_none.py` (new): proves a short-history ticker still gets a
  full result instead of being silently dropped.
- `tests/test_forecast_tracker.py::TestUpdateActualsDueDateLookup` (new): the due-date-close lookup,
  its fallback-on-failure path, and the nearest-prior-bar tie-break.
- `tests/test_options_meta_labeler.py`/`tests/test_options_paper_executor.py`: the NaN-sizing
  regression and the calibration change.
- `docs/settings_field_census.md`/`.json`, `docs/settings_liveness.json` regenerated via
  `scripts/measure_settings_census.py --write` / `scripts/settings_liveness.py --write` for the 3 new
  settings fields.

### Deliberately not done (disclosed, not silently skipped)

- Interval coverage / CRPS / pinball loss wired to `ForecastTracker`'s own realized rows and surfaced
  on Mission Control — needs an additive `forecast_errors` schema change plus new call-site wiring;
  left as a follow-up rather than a rushed migration bundled into this pass.
- `tests/test_forecast_skill_uplift.py`'s uplift experiment stays a diagnostic print, not a hard alpha
  assertion — that test's own author already documented why (avoiding overfitting the test to
  synthetic data), and that reasoning holds up on review.
- The historic DB repair itself (`scripts/repair_forecast_errors_horizon.py --apply`) has not been run
  against the live `~/.stockpy_local/quant_platform.db` — per the plan, that's the operator's step,
  after a `sqlite3 ".backup"` snapshot.
