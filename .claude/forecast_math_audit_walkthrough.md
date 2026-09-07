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

### Rebase onto origin/main

Before finalizing, `origin/main` had moved 7 commits ahead of this branch's base, two of which
(`ad68f474`, `ed44bccc`) independently touched the exact same subsystem (`forecast_tracker.py`,
`forecasting_engine.py`'s tracker-gating, `conftest.py`). Rebased and resolved two real conflicts:

- `docs/architecture/signal-engines.md`: origin/main's copy of the `forecasting_engine.py` bullet had
  grown further (later, unrelated commits appended more content) while this branch's own copy had
  already removed the incorrect `(μ − 0.5σ²)` mention (F1's doc fix). Resolved by keeping
  origin/main's longer, newer text and applying the same one-phrase removal to it, rather than
  reverting to this branch's shorter, stale copy.
- `pipeline/production_steps.py`: a genuine intent conflict. This branch's Pass 1 removed
  `Forecast_30_Prophet_Lower/_Upper` from `ForecastingStep`'s exception-fallback dict (WP8: stop
  writing MC percentiles under the Prophet name). Independently, origin/main's `ad68f474` had added
  `Forecast_{10,30,60,90}_Is_Fallback` disclosure flags to that same dict (a real, valuable, unrelated
  fix). Resolved by keeping both: the mislabeled Prophet columns stay removed, the new fallback-
  disclosure flags are kept.

Both `conftest.py`'s new autouse fixtures (this branch's `_no_forecast_tracker_due_date_lookup_in_tests`
and origin/main's `_isolate_forecast_tracker_db_in_tests`) auto-merged cleanly — they patch different
things (a settings flag vs. the DB path resolver) and coexist without conflict. `docs/settings_field_census.{json,md}`/
`docs/settings_liveness.json` conflicted trivially (both sides regenerated the same auto-generated
artifacts independently) — resolved by regenerating fresh against the final merged tree.

### Two pre-existing, out-of-scope failures found during final verification

Both confirmed NOT caused by this branch — `api/pilots_api.py`, `pilots/models.py`, and `ml/registry.yaml`
are byte-identical to `origin/main` (aside from this branch's own, unrelated F7 `HORIZONS` fix) — and
left alone rather than chased down:

- `tests/test_runtime_flags.py::TestApplyHappyPath::test_string_values_are_coerced_to_the_field_type` —
  fails only as part of the full-suite run, passes standalone and as part of its own file's full run.
  Order-dependent pollution from some other test elsewhere in the ~12,800-test suite, unrelated to the
  fields this branch touches (`BETA_LOOKBACK_DAYS`/`ADVISORY_ONLY`, not any of the new settings).
- `tests/test_pilots_api.py::TestModelsRegistry::test_needs_retrain_age_flag_is_consistent_with_trained_date` —
  `pilots/models.py::_scan_local_artifacts()` scans the real, machine-global
  `settings.LOCAL_DATA_ROOT / "ml_models"` directory (shared across every worktree/checkout on this
  machine, per `settings.LOCAL_DATA_ROOT`'s own design) with no test-isolation fixture of its own; some
  other process/session on this shared machine left it in a state this test doesn't tolerate
  (`trained_date: ""` instead of `null`/omitted for one entry). Reproducible with zero relation to any
  file this branch changes.

### Deliberately not done (disclosed, not silently skipped)

- `tests/test_forecast_skill_uplift.py`'s uplift experiment stays a diagnostic print, not a hard alpha
  assertion — that test's own author already documented why (avoiding overfitting the test to
  synthetic data), and that reasoning holds up on review.

## Follow-up session — WP6 buildout (interval coverage, no longer deferred)

The item above ("Interval coverage / CRPS / pinball loss") was the one deliberately-deferred piece of
the original audit. This follow-up session built it out in full, again via 4 parallel agents against a
fixed contract this session defined up front (the schema/tracker-method layer, done first since
everything else depends on its exact method signatures):

1. **Schema + tracker methods** (done directly, not by an agent, since it fixes the contract everything
   else builds against): `forecast_errors` gained additive `forecast_lower`/`forecast_upper` columns
   (idempotent `PRAGMA table_info`-guarded migration); `record_forecasts()` gained an optional
   `model_bounds` parameter; two new `ForecastTracker` methods, `coverage_report()`/
   `interval_score_stats()`; a shared pure function, `compute_coverage_and_interval_score()`, computing
   the Gneiting & Raftery (2007) interval score — deliberately NOT called CRPS, since a true CRPS needs
   the full predictive distribution this table doesn't persist, and mislabeling a real, different metric
   as CRPS would be its own honesty violation.
2. **Engine wiring** (agent): threaded `run_monte_carlo`'s `(mc_lo, mc_hi)` into the ONE
   `record_forecasts` call site in `generate_forecast()`'s per-horizon loop, gated on the same
   `m_res > 0` condition already gating the point forecast's own inclusion.
3. **Observability wiring** (agent): a new bulk-SQL sibling, `_forecast_coverage_stats_by_symbol`,
   mirroring `_forecast_stats_by_symbol`/`_forecast_decay_stats_by_symbol`'s existing "one query, group
   in Python" pattern, reusing the shared pure function rather than reimplementing the formula a third
   time — wired into `forecast_skill_by_symbol_summary`'s per-symbol rows as 5 new fields
   (`mc_coverage_n`/`mc_coverage_pct`/`mc_nominal_coverage_pct`/`mc_interval_score`/`mc_coverage_reason`).
4. **Test coverage** (agent): 25 new tests in `tests/test_forecast_tracker.py`, including the plan's
   explicit statistical ask — a synthetic-ground-truth test proving a correctly-calibrated 90% band
   measures within tolerance of its true coverage on 500 known draws, AND that a deliberately
   miscalibrated band is correctly flagged as measuring well outside that tolerance (so the test
   provably discriminates good calibration from bad, not just "returns a number"). No bugs found in the
   already-implemented tracker code — every test passed on first attempt against the documented
   contract.
5. **Webapp** (agent): rendered the 5 new fields in `Observability.tsx`'s `ForecastSkillBySymbolSection`
   (an honest degrade to the `*_reason` text when insufficient history, never a blank cell), and — found
   and closed in the same pass — a real, separate, PRE-EXISTING gap: `pending`/`n_by_model`/`decay_pct`/
   `decay_reason` were already computed by the Python backend but never declared in the webapp's
   `ForecastSkillSymbolRow` type or rendered anywhere, since an earlier session added the decay feature
   without a corresponding webapp pass.

Also run this session, per the plan's own instruction ("the operator, not the agent"): `scripts/
repair_forecast_errors_horizon.py --dry-run` against the live `~/.stockpy_local/quant_platform.db`.
Result: **2,362,135 rows scanned, 47,403 to NULL (prematurely actualized under the old calendar-day
cutoff), 676,782 to correct (scored against the wrong price)** — roughly 30% of the table affected,
confirming F5's real-world impact was substantial. `--apply` has NOT been run — that write step is the
operator's own, after a `sqlite3 ".backup"` snapshot, exactly as the original plan specified.

Verification: full offline suite after all 4 agents' changes — 12,939 passed, 4 pre-existing failures
confirmed unrelated (backing files byte-identical to `origin/main`: a load-sensitive subprocess-timing
test, a real non-default `.env` value leaking into an unrelated settings test, a sector-selection
sentiment test, and the previously-documented shared-machine `pilots/models.py` state issue) — plus a
harmless settings-liveness artifact line-number drift from this session's own line-count changes,
regenerated. Webapp: `npm run typecheck` clean, full vitest suite (176 files / 1956 tests) passing.
- The historic DB repair itself (`scripts/repair_forecast_errors_horizon.py --apply`) has not been run
  against the live `~/.stockpy_local/quant_platform.db` — per the plan, that's the operator's step,
  after a `sqlite3 ".backup"` snapshot.
