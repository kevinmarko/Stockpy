# Forecasting rebuild F6 (part a) — walkthrough

Plan: `.claude/forecasting_rebuild_f5_f6_implementation_plan.md` §5-7. Parent: `.claude/forecasting_rebuild_implementation_plan.md` (F6).
Task tracker: `.claude/forecasting_rebuild_f6_task.md`. Branch `forecasting-rebuild-f6`, from `origin/main` @ `d8e53dfc`.

## Operator decisions applied (2026-11-13: "go with your recommendations")

| Question | Answer applied | Effect here |
|---|---|---|
| Q1 F6 before F5 | Yes | This PR is F6 only. F5's data matures around Feb 2027 (plan §3.2). |
| F6 open choices | The plan's "(proposed)" options | New routes instead of widening `GET /symbols/{t}/forecast`; read tier, no `_ENABLED` flag; `forecast_effects` as a trailing field; z fields nullable until F5-1. |
| Q10 new flags | OK, default off | Not used: F6a needed no new setting. |
| Q2, Q3, Q4, Q5, Q7, Q8; Q6/Q9 flips | Not decided here | All belong to F5 or need live operator data. Still open (below). |

The plan splits F6 into two PRs, F6a (read contract + backend) and F6b (the webapp screen), with F6b merged after F6a so
its live-backend parity check is real. **This PR is F6a.** F6b is the next PR.

## What changed

1. **`pilots/forecasts.py` (new).** Dependency-light reader (stdlib + `settings` + lazy `forecasting.forecast_tracker`).
   - `forecasts_view(snapshot)`: one row per `state_snapshot.json` signal. Per horizon 10/30/60/90: published
     forecast, MC band, `is_fallback`, `gated_naive`, and `z`/`err`/`err_n` = null with `z_reason` (F5 not built).
     `impact`: `forecast_alignment` points (from `score_components`), the StrategyEngine tier with and without
     those points (labelled approximate: the snapshot has the rounded score, not the choppy-market input or the
     kill-switch overlay), `advisory_effects`, and `sell_hi_from_forecast` (derived from `sellRange`:
     `LO = P + 1.5*ATR`, ATR leg `= 2*LO - P`, forecast leg won if `HI` exceeds it by more than 0.02).
   - `forecast_summary(symbol)`: per horizon, skill vs naive per model (`skill_vs_naive`, window
     `FORECAST_SKILL_WINDOW_DAYS`), the last recorded gate decision, the gate's evaluation now
     (`compute_naive_gate` on `naive_gate_stats` at `as_of`, every model with stats as a candidate, disclosed in
     `evaluation_note`), and MC band coverage. Published model is `gated_blend` when `FORECAST_NAIVE_GATE_ENABLED`
     else `blend`.
   - Never raises; every missing value is null with a `reason`.
2. **`forecasting/forecast_tracker.py`**: new read-only `ForecastTracker.latest_gate_decisions(symbol, horizons)`
   (newest `gated_blend` row per horizon; honest reason on a pre-F3 ledger or a read error).
3. **`api/pilots_api.py`**: `GET /forecasts`, `GET /symbols/{ticker}/forecast-summary` (both `require_read_token`,
   no writes); `GET /thresholds` gains `forecast_gate_min_improvement`, `forecast_gate_min_obs` (live settings).
4. **`engine/advisory.py`**: trailing `Recommendation.forecast_effects: Tuple[str, ...]` with `case_a_exit`,
   `case_c_hold`, `bullish_conviction_boost` (only when the boost actually raised conviction). Built from the same
   flags; the two new flags are read only inside the `forecast_effects=` expression.
5. **`main_orchestrator.py::_write_state_snapshot`**: `signals[]` gains `forecast_{h}`, `forecast_{h}_is_fallback`,
   `forecast_{h}_gated_naive` (all modes), and `advisory_forecast_effects` (primary mode only, with `garch_vol` /
   `suggested_exit_pct`; `[]` = no rule fired, null = no recommendation).
   **Deviation from the plan:** the plan's `GET /forecasts` reads "the pipeline's persisted forecast", but on
   current `main` the snapshot carried only the bands and the 30-day fallback flag, not the point forecasts, so
   they are added here. Read-only telemetry; nothing downstream decides on them.
6. **Webapp**: `Thresholds` in `webapp/src/api/types.ts`, `mock.ts` and five test fixtures get the two new keys
   (parity for the `/thresholds` change). No screen change (that is F6b).

7. **`docs/settings_liveness.json`, `docs/settings_field_census.{json,md}`**: regenerated with
   `scripts/settings_liveness.py --write` and `scripts/measure_settings_census.py --write` (line numbers and
   read-site counts only; no field changed classification).

No new setting, no `.env` key, no `ALLOWED_KEYS`/`DANGEROUS_KEYS` change, no decision change.

## Citations re-checked against `origin/main` @ `d8e53dfc`
All still hold, with small line drift: `forecast_tracker.py` `compute_skill_vs_naive:328`, `compute_naive_gate:425`,
`coverage_report:1455`, `skill_vs_naive:1640`, `naive_gate_stats:1716`; `api/pilots_api.py`
`/symbols/{t}/forecast` 1504 -> 1506, `/thresholds` 2016 -> 2018; `main_orchestrator.py` bands 998-1005,
`score_components` 1094; `strategy_engine.py` sell range 145-148, tier cuts 378-394; `engine/advisory.py` Cases A/C
~1135-1190; `tests/test_pilots_strategy_matrix.py:433`; F6b's `ForecastViewer.tsx:212-214` (+-1.5% label),
`client.ts` 630 -> 632, `navigation.tsx:98`, `Marketplace.tsx:442`, `ResearchHub.tsx:46`,
`CommandPaletteModal.tsx:47`, `AgenticTrading.tsx:278` (was 271), `api/metrics_api.py:186` (still refits).

## Tests and checks
- `tests/test_pilots_forecasts.py` (new, 34 tests): view shape, null-not-zero, pre-F6a snapshot, garbage rows,
  ledger summary on a synthetic `forecast_errors`, failing tracker, routes (cold start 200, corrupt snapshot 200,
  401 with no/wrong token), `latest_gate_decisions`, tier cuts + sell-zone formula pinned to `strategy_engine.py`.
- **No-lookahead perturbation** (`TestSummaryGateHasNoLookahead`): rows not matured by `as_of` made great (0.001)
  in one ledger and awful (5.0) in the other leave the gate evaluation identical; companion test: judged 120 days
  later the awful rows flip it to fallback, so the test is not vacuous. F6a adds no new feature or forecaster;
  this covers the one place it reads the ledger "as of" a time.
- **Default/neutral behaviour** (no flag exists, so the equivalent of the flag-off test is decision-neutrality):
  AST guard `TestForecastEffectsAreDecisionNeutral`, plus a one-off run of 180 scenarios (5 positions x 9 forecasts
  x 4 signals) through `origin/main`'s `engine/advisory.py` and this branch's: **0 differing fields besides
  `forecast_effects`**; effects seen: none 152, `bullish_conviction_boost` 10, `case_a_exit` 8, `case_c_hold` 10.
  The snapshot without recommendations still has no primary-mode keys (existing parity test, extended).
- **Mutation checks** (each mutation applied, the named tests run, then restored):
  1. gate evaluated at `datetime.now()` instead of `as_of` -> `NoLookahead` 1 failed;
  2. ATR leg `2*LO - P` replaced by `LO` -> `sell_hi` tests 3 failed;
  3. `_positive_or_none` returns `0.0` instead of `None` -> null/cold-start tests 1 failed;
  4. conviction raise gated on `_bullish_boost_fired` -> decision-neutral tests 2 failed;
  5. snapshot `forecast_30` fabricated as `0.0` when absent -> `TestOrchestratorPublishedForecasts` 1 failed.

Verification output is in the PR body.

## Still open for the operator
- **F6b** (next PR): webapp Forecasts screen (`types.ts`/`client.ts`/`mock.ts` for the two routes with honest
  fixtures, `ForecastViewer.tsx` rework dropping the +-1.5% literal and the refit source, `?symbol=` param, links
  from Symbol Detail and `ModelHealthPanel`, `TAB_HELP.forecast` + glossary, `docs/HOW_TO_GUIDE.md`), verified in a
  browser against the mock and a live backend.
- **F5** (data-blocked to about Feb 2027): F5-1 measure + shadow (adds `FORECAST_Z_*`, default off, Q10 OK), F5-2
  validation vehicle + diff tool (needs FMP network), F5-3 decision change behind the flag.
- **Q2** `FORECAST_Z_MIN_OBS` (keep 60 / lower / pooled). **Q3** Z_T value and ladder shape. **Q4** z unavailable ->
  neutral (proposed). **Q5** sellRange quantile. **Q6** validate/flip F5 before or after the F3 flip. **Q7** live
  `ADVISORY_REUSE_PIPELINE_COMPUTE` and the one-producer precondition. **Q8** U1-U4 (live flags, cleanup/backfill
  applies, `forecast_skill_report.py --gate` output, query A/B/C). **Q9** flip bar given the proxy baseline fails.
- `GET /metrics/forecast/{symbol}` still refits `ForecastingEngine()` (F4 part 2b iii); F6b stops the Forecast
  Viewer from using it.
