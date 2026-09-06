# Implementation Plan: forecast-fallback disclosure

## §0 — Dependency / prior-state check

- **Starting point**: `forecasting_engine.py::ForecastingEngine._blend_with_skill()` deliberately
  returns `current_price` (never `NaN`) when every contributing model fails to produce a
  usable forecast for a horizon. This is documented in-code ("never return `float('nan')`")
  and pinned by an existing test,
  `tests/test_forecasting_engine.py::TestBlendWithSkill::test_no_models_returns_current_price_never_zero`.
- **The ask under review**: a prior proposal wanted this changed to return `NaN` instead, on
  the reasoning that `current_price` is indistinguishable from a genuine forecast.
- **No other in-flight PR/branch touches `_blend_with_skill`, `generate_forecast`,
  `ForecastingStep`, `engine/advisory.py`'s Step 6, or either `state_snapshot.json` writer** —
  confirmed by fetching `origin/main` fresh (`36a7d296…`) and reading each file in full before
  editing. No merge-conflict risk identified.
- **This is a judgment call, not a mechanical change.** §1 below is the investigation that
  determined the outcome; §2 is what was actually implemented as a result.

## §1 — Investigation (decision: keep `current_price`, add disclosure — "path (a)")

1. Read `_blend_with_skill()` and `generate_forecast()` in full off `origin/main`. Confirmed the
   original reasoning: `current_price` was chosen specifically to avoid `0.0`
   (CONSTRAINT #4's literal concern), not with `NaN` explicitly ruled out for other reasons —
   but the "never return float('nan')" comment shows it WAS a deliberate choice, not an
   oversight.
2. Traced whether `current_price` is distinguishable downstream from a real forecast. It is
   **not** — no flag, no `model_used`, no confidence field exists anywhere in
   `generate_forecast()`'s return dict, `pipeline/production_steps.py::ForecastingStep`'s
   dashboard columns, `engine/advisory.py`'s `key_indicators`, or either
   `state_snapshot.json` writer.
3. Determined how narrow/real the all-models-failed scenario actually is: Monte Carlo
   (unconditional, no settings flag) succeeds under every normal condition given a positive
   `current_price` (GBM's `exp()` term is always positive) — the ONLY way `model_forecasts`
   ends up genuinely empty is `mu`/`sigma` themselves being `NaN`, which requires
   `history_series` to have ≤1 valid observation after `.dropna()`. Reproduced this directly:
   `tests/test_forecasting_engine.py::TestGenerateForecast::test_thin_history_falls_back_to_current_price_and_discloses_it`.
4. Determined what `NaN` would actually do to the two real consumers of `Forecast_30`:
   - `pipeline/production_steps.py`'s vectorized signal path:
     `vec_df['forecast_price'] = dashboard_df['Forecast_30'].fillna(0.0)` — `NaN` becomes a
     literal fabricated **$0 price target**, strictly worse than reusing a real observed price.
   - The per-ticker path: `se.evaluate_security(forecast_price=row.get('Forecast_30', 0.0))` —
     `row.get(...)` with an existing `NaN` key returns `NaN` verbatim (the default arg is
     irrelevant), passing `NaN` straight into `StrategyEngine.evaluate_security()`'s scoring
     arithmetic with no established NaN-handling contract for that parameter.
   - **Conclusion: changing the return value to `NaN` would introduce a new, more severe
     CONSTRAINT #4 violation without additional (out-of-scope) work at both consumer sites.
     Keep `current_price`.**
5. Decision: **path (a)** — the design is correct; add a disclosure flag rather than change the
   value.

## §2 — What was implemented

1. **`forecasting_engine.py::generate_forecast()`**: compute
   `results[f'Forecast_{h}_Is_Fallback'] = not bool(model_forecasts)` for each horizon,
   right where `results[f'Forecast_{h}'] = blended` is set. Always set (True/False), never
   conditionally omitted. `_blend_with_skill()` itself is UNTOUCHED — no signature change, no
   behavior change, the existing test stays valid as-is.
2. **`pipeline/production_steps.py::ForecastingStep`**: add the four new keys to
   `forecast_cols` (an extra `dashboard_df` column set, deliberately NOT added to
   `config.COLUMN_SCHEMA` — Pandera's `DashboardSchema` is non-strict, and registering it
   would force the advisory orchestrator / Sheets publisher / HTML report templates to also
   populate it, out of scope here). The `except`-branch Monte-Carlo recovery (an entire
   `ForecastingEngine` exception) also marks all four horizons `True`.
3. **`engine/advisory.py::evaluate()`**: thread `Forecast_30_Is_Fallback` from a fresh
   `generate_forecast()` call onto `key_indicators["forecast_is_fallback"]`
   (`1.0`/`0.0`/`NaN`, matching the existing `kelly_raw_was_capped` float-encoding
   convention). New `precomputed_forecast_is_fallback` keyword lets a caller reusing
   `settings.ADVISORY_REUSE_PIPELINE_COMPUTE`'s precomputed forecast also supply its fallback
   status; `pipeline/production_steps.py`'s `_eval_one` closure passes this through, trusting
   only an actual `bool` from the row (a `NaN` cell or missing column both degrade to
   `None`/unknown).
4. **Both `state_snapshot.json` writers** (`reporting/state_snapshot.py` for advisory,
   `main_orchestrator.py::_write_state_snapshot` for the orchestrator) emit a shared
   `"forecast_is_fallback"` per-signal key, added to
   `tests/test_state_snapshot_parity.py`'s `SHARED_SIGNAL_FIELDS` set.
5. **Documentation**: `docs/known_issues/forecast_fallback_current_price_disclosure.md` (new,
   full write-up of the investigation and decision), `docs/known_issues/README.md` (index row),
   `docs/architecture/signal-engines.md`'s `forecasting_engine.py` bullet (addendum).

## §3 — Explicit non-goals (disclosed, not silently dropped)

- `config.COLUMN_SCHEMA` / Google Sheets / HTML report template / webapp UI surfacing. The
  flag reaches `dashboard_df` and `state_snapshot.json` (this repo's two primary
  observability surfaces) but not the Sheet, the HTML report, or a webapp component. A
  follow-up once there's a concrete UI need.
- A separate, adjacent bug found while reading `_blend_with_skill`'s static-blend `elif`
  chain: a `model_forecasts` dict containing ONLY `holt_winters` (no arima/mc/lstm) with
  `preferred_model != "HW"` falls through to `current_price` despite having a real forecast
  available. Disclosed in the known-issues doc; not fixed here (different bug, narrower
  reproduction requirement, deserves its own investigation).

## §4 — Verification

- `python -m ruff check . --select=F821,F822,F823,E9` — clean.
- Full offline suite (`pytest -m "not network and not slow"`) — see
  `.claude/fix-forecast-fallback-disclosure_walkthrough.md` for the actual recorded output.
- New/updated tests: `tests/test_forecasting_engine.py`,
  `tests/test_forecasting_step_fallback_disclosure.py` (new),
  `tests/test_advisory.py::TestForecastIsFallbackKeyIndicator` (new),
  `tests/test_advisory_dedup_wiring.py`, `tests/test_state_snapshot_parity.py`.
