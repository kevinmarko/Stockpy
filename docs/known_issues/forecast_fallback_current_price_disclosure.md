# `_blend_with_skill`'s all-models-failed fallback: kept `current_price`, added disclosure

**Status (2026-09): by design — this is NOT a bug fix. The original `current_price`
fallback (never `NaN`) was investigated and confirmed correct; what was genuinely
missing was a way for a downstream reader to tell it apart from a real forecast. That
disclosure gap is now closed.**

## Background

`forecasting_engine.py::ForecastingEngine._blend_with_skill()` is the per-horizon
ensemble blend behind `Forecast_10`/`Forecast_30`/`Forecast_60`/`Forecast_90`. When
**every** contributing model (ARIMA, Holt-Winters, CNN-LSTM, Monte Carlo, Prophet,
BERT-LLA) fails to produce a usable price for a given horizon, it has nothing left to
blend and returns `current_price` verbatim, with an explicit in-code comment ("never
return `float('nan')`") and a dedicated regression test
(`tests/test_forecasting_engine.py::TestBlendWithSkill::test_no_models_returns_current_price_never_zero`).

A prior review proposed changing this to return `NaN` instead, on the reasoning that
`current_price` is numerically indistinguishable from a genuine model prediction and
so risks being read as a real forecast when it is not. This document records the
investigation into that proposal and the reasoning for the outcome: **the value stays
`current_price`; a new boolean disclosure flag was added instead.**

## Why `current_price`, not `NaN` — with evidence, not assumption

1. **The scenario is real but narrow.** Tracing `generate_forecast()`'s per-horizon
   loop: Monte Carlo (`run_monte_carlo`) is unconditional (no settings flag disables
   it) and, given a positive `current_price` (already gated by an early return),
   produces a **positive** terminal price under every normal condition — GBM's
   `exp()` term is always positive. So `model_forecasts` is essentially never
   genuinely empty *except* when `mu`/`sigma` themselves come back `NaN`, which
   happens when `history_series` has ≤1 valid observation (`log_returns.dropna()`
   leaves zero rows) — e.g. a brand-new listing, or a severely degraded historical
   fetch for one symbol this cycle. Verified directly:
   `tests/test_forecasting_engine.py::TestGenerateForecast::test_thin_history_falls_back_to_current_price_and_discloses_it`
   reproduces this exact condition and confirms `_blend_with_skill`'s fallback branch
   is the one that fires.

2. **Downstream, `NaN` would be actively worse, not neutral.**
   `pipeline/production_steps.py`'s vectorized signal-aggregation path builds its
   input frame with:
   ```python
   vec_df['forecast_price'] = ctx.dashboard_df.get('Forecast_30', ...).fillna(0.0).values
   ```
   A `NaN` `Forecast_30` would silently become a literal **`$0` price target** fed
   into cross-sectional scoring — read as "this stock is forecast to go to zero,"
   a far more misleading fabrication than reusing today's real, observed price. The
   per-ticker (non-vectorized) path
   (`se.evaluate_security(forecast_price=row.get('Forecast_30', 0.0))`) would instead
   pass a raw `NaN` straight into `StrategyEngine.evaluate_security()`'s scoring
   arithmetic, an unaudited-for-this-change code path with no guarantee it degrades
   gracefully rather than propagating `NaN` into `Score`/`Kelly Target`. Neither of
   these was part of the original proposal's scope, and fixing both would have been
   required to make `NaN` even neutral, let alone an improvement.
   `current_price` sidesteps both failure modes: it is a real, non-fabricated number
   (CONSTRAINT #4 is about never fabricating a *value*, not about avoiding an
   informative real one), and every existing numeric consumer already treats it
   exactly like a computed forecast without needing new NaN-handling.

3. **The real gap was disclosure, not the value.** Nothing downstream could tell
   "a real model produced this price" apart from "every model failed and this is
   just today's price relabeled." That is the actual problem CONSTRAINT #4 cares
   about here — an operator or an automated consumer (skill-weighting, a future
   webapp forecast-confidence indicator) being misled about what kind of number
   they're looking at, not the number's own magnitude.

## What changed instead

A new disclosure flag, `Forecast_{h}_Is_Fallback` (`h` in `{10, 30, 60, 90}`), is
computed alongside each horizon's blended price in
`forecasting_engine.py::generate_forecast()` — `not bool(model_forecasts)`, i.e.
`True` exactly when `_blend_with_skill` had to fall back to `current_price` for that
horizon. It is threaded end-to-end:

- **`pipeline/production_steps.py::ForecastingStep`** writes it as a genuine extra
  `dashboard_df` column (`Forecast_10_Is_Fallback` … `Forecast_90_Is_Fallback`),
  alongside the existing `Forecast_*` columns. The step's own `except` branch (a
  `ForecastingEngine` exception, recovered via a single coarse Monte-Carlo call) also
  marks all four horizons `True` — that recovery is itself not the real ensemble.
  **Deliberately not added to `config.COLUMN_SCHEMA`** — Pandera's `DashboardSchema`
  is non-strict (extra columns are fine), and registering it there would force every
  dashboard-building code path (the advisory orchestrator, Sheets publisher, HTML
  report templates) to also populate it, which is out of scope for this change. A
  future task that wants this in the Sheet/HTML report should register it there
  explicitly and update both orchestrators together, per this repo's usual
  `COLUMN_SCHEMA` convention.
- **`engine/advisory.py::evaluate()`** (the `main.py` advisory path, which calls
  `generate_forecast()` independently) threads the same signal onto
  `key_indicators["forecast_is_fallback"]`, encoded `1.0`/`0.0`/`NaN` — the same
  convention as the existing `kelly_raw_was_capped` field, since every
  `key_indicators` value is passed through `math.isnan()`. A new
  `precomputed_forecast_is_fallback` keyword lets a caller reusing
  `settings.ADVISORY_REUSE_PIPELINE_COMPUTE`'s precomputed `Forecast_30` also supply
  its fallback status instead of leaving it unknown; `pipeline/production_steps.py`'s
  `_eval_one` closure passes this through from the same cycle's dashboard column,
  trusting only an actual `bool` (a `NaN` cell or a missing column both degrade to
  `None`/unknown, never a fabricated `False`).
- **Both `state_snapshot.json` writers** (`reporting/state_snapshot.py` for the
  advisory path, `main_orchestrator.py::_write_state_snapshot` for the orchestrator
  path) now emit a shared `"forecast_is_fallback"` per-signal key —
  `1.0`/`0.0`/`null`, never a fabricated default — enforced by
  `tests/test_state_snapshot_parity.py`'s `SHARED_SIGNAL_FIELDS` set and a dedicated
  `TestForecastIsFallbackParity` class.

## Explicitly out of scope for this change

- **`config.COLUMN_SCHEMA` / Sheets / HTML report / GUI surfacing.** The flag reaches
  `dashboard_df` and `state_snapshot.json` (the two surfaces this repo's own
  observability conventions read from first) but not the Google Sheet, the HTML
  report template, or a GUI/webapp UI element. Any of those is a legitimate,
  separate follow-up once there's a concrete UI need (e.g. an operator wanting to
  see "this forecast is degraded" on a chart).
- **The static-blend elif-chain's own, unrelated gap.** While tracing
  `_blend_with_skill`'s static fallback branch, a separate, adjacent oddity was
  found: if `model_forecasts` contains **only** `holt_winters` (no ARIMA, no Monte
  Carlo, no CNN-LSTM) and `preferred_model != "HW"`, the static blend's `elif`
  chain has no branch that reads `hw_price`, and falls through to
  `arima_price if arima_price > 0 else (mc_price if mc_price > 0 else current_price)`
  — silently discarding a real Holt-Winters forecast in favor of `current_price`,
  even though `model_forecasts` is non-empty. This is a genuinely different bug (a
  gap in the static blend's model-selection logic, not the empty-`model_forecasts`
  case this document is about) and is disclosed here rather than fixed, since given
  Monte Carlo's near-universal success (point 1 above) this combination requires
  Monte Carlo to fail while Holt-Winters succeeds — a narrow, not-yet-reproduced
  edge case that deserves its own investigation rather than a speculative fix bundled
  into this change.

## Tests

- `tests/test_forecasting_engine.py::TestGenerateForecast` — `Forecast_{h}_Is_Fallback`
  is `False` with real history, `True` with single-point (thin) history, and every
  `Forecast_{h}` in the thin-history case equals `current_price` exactly.
- `tests/test_forecasting_step_fallback_disclosure.py` — `ForecastingStep`'s per-row
  writeback (happy path, dead-lettered/zero-price row stays `NaN` not `False`, and
  the exception-recovery path marks every horizon `True`).
- `tests/test_advisory.py::TestForecastIsFallbackKeyIndicator` —
  `key_indicators["forecast_is_fallback"]` round-trips `True`/`False`/absent as
  `1.0`/`0.0`/`NaN` from both the fresh-fit and precomputed-reuse paths.
- `tests/test_advisory_dedup_wiring.py` — the `_eval_one` precompute-selection
  reproduction now covers the new third value, including the NaN-cell-is-not-`True`
  guard.
- `tests/test_state_snapshot_parity.py::TestForecastIsFallbackParity` — both
  `state_snapshot.json` writers round-trip the flag identically.

The pre-existing
`tests/test_forecasting_engine.py::TestBlendWithSkill::test_no_models_returns_current_price_never_zero`
was left unchanged — `_blend_with_skill`'s own return value and contract are
untouched by this change.
