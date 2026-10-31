# Forecasting rebuild F5 + F6: implementation plan (for operator review, no code yet)

Parent plan: `.claude/forecasting_rebuild_implementation_plan.md` (F5, F6). Written 2026-10-29 against `main` @ `6e79c997`.
Tier: "Everything else" (F5 changes how decisions are derived from forecasts). **No code until the operator has reviewed
this plan.** Nothing below was run against the live DB or `.env`; anything that depends on them is marked UNKNOWN.
Citations are `path:line` at that commit. Numbers marked "arithmetic" are derived from rules in the code, not measured.

## 1. Where F1-F4 actually stand (verified in code and docs)

| Phase | State | Evidence |
|---|---|---|
| F1 #1066 | Merged. `blend` and `naive` are recorded at all horizons; one row per symbol x model x horizon x US/Eastern day (upsert). | `forecasting/forecast_tracker.py:767-885`, `skill_vs_naive` `:1640`, `scripts/forecast_skill_report.py`, `scripts/clean_forecast_ledger.py` (dry-run default, categories a-d) |
| F1 gap | **Not done:** skill-vs-naive on `/symbols/{t}/forecast` and the observability sections (F1 walkthrough, "Not done in F1"). Still true: outside `tests/`, `git grep skill_vs_naive` finds only the tracker and scripts, no `pilots/` or `api/` caller. | `.claude/forecasting_rebuild_f1_walkthrough.md`, `pilots/forecast_skill.py:58-164` |
| F2 #1073 | Merged: clamp + input-price guard, Prophet `freq="B"`, missing/fallback forecast scores 0 in `forecast_alignment`. | `forecasting_engine.py:107`, `signals/forecast_alignment.py:52-60,98-100` |
| F3 #1076 | **The shadow IS implemented.** `compute_naive_gate` (`forecast_tracker.py:425`), `naive_gate_stats` (`:1716`), `gated_blend` recorded every cycle next to `blend` with `gate_fallback`/`gate_admitted` (`forecasting_engine.py:1726-1730`), side-by-side report `forecast_skill_report.py --gate`. | `.claude/forecasting_f3_naive_gate_shadow_walkthrough.md` |
| F3 flag | `FORECAST_NAIVE_GATE_ENABLED` **defaults False** (`settings.py:2013-2014`). No commit or doc in the repo records a flip, so the live value is UNKNOWN. Also merged: #1079 (cleanup category d + `scripts/backfill_naive_forecasts.py`). | `git log`, `git grep` of docs |
| F4 part 1 #1127 | Merged: BERT-LLA, LSTM-attention endpoint, `FORECAST_DRIFT_SHRINKAGE`, sector-config loader archived; sector map frozen as `SECTOR_FORECAST_CONFIGS_FROZEN` (`forecasting_engine.py:84`). | F4 walkthrough, `docs/FEATURE_TIER_HISTORY.md` 2026-10-12 |
| F4 part 2a #1130 | Merged: `Forecast_{10,30,60,90}_{Lower,Upper}` reach `dashboard_df` and `state_snapshot.json` `signals[]` as `forecast_{h}_lower/upper` (`main_orchestrator.py:998-1005`). These are **Monte Carlo's own p5/p95** (`forecasting_engine.py:393-397`, `:1790-1794`), not a band on the blend. | part-2a walkthrough |
| F4 part 2b | **Not done.** (i) CNN-LSTM and Prophet still run (`forecasting_engine.py:1537-1551`); their archive is gated on F3 data. (ii) Sector static-preference blend still present (`:1198-1201`; goes with the F3 flip). (iii) **One code path not done:** `GET /metrics/forecast/{symbol}` still refits `ForecastingEngine()` with no tracker (`api/metrics_api.py:186`) and the Forecast Viewer reads it (`webapp/src/api/client.ts:630`); the advisory overlay refits a second time unless `ADVISORY_REUSE_PIPELINE_COMPUTE` is on (`settings.py:2300`, default False; `pipeline/production_steps.py:2780-2799,2852`). (iv) `cnn_lstm_worker.py:249,311` attention functions (F4 decision D2). `main.py`'s forecasting is gone with its archive (`legacy/main.py`). | code reads |

**UNKNOWN, the operator must tell us (or run the query in section 3):**
- U1 Live values of `FORECAST_NAIVE_GATE_ENABLED`, `FORECAST_SKILL_WEIGHTING_ENABLED`, `FORECAST_SKILL_WINDOW_DAYS` (F1 walkthrough says 365 on your install), `ADVISORY_REUSE_PIPELINE_COMPUTE`. They live in `.env` / `output/runtime_flags.json`, which the repo does not contain.
- U2 Whether you ran `clean_forecast_ledger.py --apply` (a,b,d) and `backfill_naive_forecasts.py --apply`. They change the `naive` history the gate and F5 read.
- U3 Whether the F3 shadow review happened (output of `python scripts/forecast_skill_report.py --gate`). No review artifact is in the repo.
- U4 Date of the first real post-F1 `blend` row. The daemon was stopped 2026-09-27 15:27 (F3 walkthrough) and ran `primary` from 2026-10-12 (CLAUDE.md), so it is between those; query A below gives the answer.

## 2. F5 design: decisions in units of the forecast's own measured error

### 2.1 What reads the forecast today (so what can change)
Only the **30-day** forecast reaches decisions: `vec_df['forecast_price']` from `Forecast_30` (`pipeline/production_steps.py:2212`, per-row `:2363`) and `raw_f30` in advisory (`engine/advisory.py:877`). So F5 acts on h=30 only; z for h=10/60/90 is display (F6).

| Consumer | Today | Under F5 flag | How a decision can change |
|---|---|---|---|
| `forecast_alignment` (`signals/forecast_alignment.py:61-78`, `:103-111`; weight 10.0, `settings.py:2646`) | gain >= 1.5% -> +10; 0 < gain < 1.5% -> +5; forecast <= price -> -10; missing/fallback -> 0 | z ladder (2.4) | Score is 50 + weighted sum; tiers cut at 75/55/35 (`strategy_engine.py:378-394`). A swing up to 20 points (-10 to +10) can flip BUY/HOLD/RISK REDUCE, which gates the execution queue. A -10 for a tiny negative forecast becomes 0 inside the noise band. |
| Advisory direction (`engine/advisory.py:324-325`, `:1098-1104`) | forecast change < -3% bearish, > +3% bullish; **ignores `forecast_is_fallback`** | z <= -Z_T / z >= +Z_T on the 30-day z | Case A (`:1142`: loss <= -10% and bearish -> SELL, conviction 0.80, `suggested_exit_pct` = 1.0 at `:1206-1209`); Case C (`:1178-1190`: gain >= 10%, BUY, "flat" -> HOLD); bullish BUY conviction boost (`:1196`). Case A can stop or start firing; "flat" widens or narrows. |
| `sellRange` upper leg (`strategy_engine.py:145-148`) | `max(price + 3*ATR, Forecast_30)` | `max(price + 3*ATR, Forecast_30_Upper)` (2.5) | **Display only.** `sell_range` is passed through to the API/UI (`engine/advisory.py:1477`, `api/data_api.py:1176`, `main_orchestrator.py:1010`); `git grep` finds no use in `execution/`. |
| Webapp Forecast Viewer label | hard-coded +-1.5% BULLISH/BEARISH (`webapp/src/screens/ForecastViewer.tsx:212-214`) | server-provided classification (F6) | A third copy of the fixed threshold; removed in F6. |
| **Not changed** | Kelly sizing (`strategy_engine.py` `_calculate_kelly_sizing_detailed` reads GARCH vol and the trade ledger, not the forecast), `buyRange`, limit prices, the published `Forecast_*` values, the meta-labeler's `forecast_price` feature (`ml/meta_bootstrap.py:120`). F5 reinterprets the forecast; it does not change it. | | |

### 2.2 Definition
For symbol s, horizon h (h=30 for decisions), spot P (the dashboard `Price`, the value `naive` is recorded at):
- `r = ln(Forecast_h / P)`, `E = median |ln(F_d / A_d)|` over the published model's matured days d, `z = r / E`.
- `E` is `median_abs_log_error` from `compute_skill_vs_naive` (`forecast_tracker.py:328-411`) for the **published** model: row `blend` while `FORECAST_NAIVE_GATE_ENABLED` is off; with it on, `gated_blend` restricted to `gate_fallback = 0` days (the admitted-blend days; `gate_fallback`/`gate_admitted` exist, `forecast_tracker.py:759-762`). This is the plan's "score exactly what you publish". When the gate falls back to naive, `r = 0` and the row is already `Is_Fallback` (neutral), so nothing new happens.
- Source, exactly: table `forecast_errors`; columns `symbol, model_name, horizon_days, forecast_ts, forecast_day, forecast_price, actual_price, gate_fallback`.
- Read through a new `ForecastTracker.published_error_stats(symbol, horizons, window_days, as_of, model_name, admitted_only)`. It reuses `_latest_row_per_day` and `_pairs_with_naive`, **the F3 no-lookahead cutoff** (`forecast_day + BDay(h) < eastern_day(as_of)`, `forecast_tracker.py:1778-1788`), the $1 price floor, and the window `FORECAST_SKILL_WINDOW_DAYS`.
- `naive_gate_stats` already returns `blend`/`gated_blend` stats (it selects all models for the symbol), so the gate-off case needs no extra query; the admitted-only case adds `gate_fallback` to the same SELECT (additive). One DB read per symbol per cycle, as today.
- The engine computes it where the tracker, `as_of` and `Forecast_h` already exist (`generate_forecast`, after `:1576-1585`) and emits `Forecast_{h}_Err`, `Forecast_{h}_Err_N`, `Forecast_{h}_Z`, `Forecast_{h}_Z_Reason`. `ForecastingStep` adds them as NaN-filled extra columns, like the F4 bands (`production_steps.py:520-553`), kept out of `config.COLUMN_SCHEMA`.

### 2.3 Insufficient data (CONSTRAINT #4 / #6): unavailable, never a fabricated error
`z = None` with a reason string when: `n < FORECAST_Z_MIN_OBS`; `E` not finite or `< 1e-12` (repo degenerate-std rule; no floor-and-divide); the forecast is `Is_Fallback`; price < $1; the ledger read failed (`reason == "error"`, as `naive_gate_stats`). Effects:
- alignment 0 pts, explanation "0pts: forecast z unavailable (<reason>)";
- advisory neither bullish nor bearish (same as a missing forecast today);
- `sellRange` uses the ATR leg only; the webapp shows "—" plus the reason.

This is **not** a fallback to the legacy +-1.5%/+-3%: that would put an unmeasured default back in charge for exactly the new symbols (Q4). It also closes today's gap where advisory classifies a fallback forecast without checking the flag (`engine/advisory.py:1101`).

### 2.4 Thresholds and where they live
New settings (all `live_safe`, read per call, F3 precedent: `shared/env_io.py` `ALLOWED_KEYS`, not in the typed-confirmation set):
- `FORECAST_Z_DECISIONS_ENABLED: bool = False` (default = today's exact behaviour, per the default-value policy; flipped only with the operator's OK).
- `FORECAST_Z_THRESHOLD: float` (Z_T). Value is **not chosen here**: the proposed primary is 1.0 ("the forecast move exceeds the model's own typical miss"), pre-registered with 0.5 and 1.5 as the only neighbours, fixed before any backtest is read (section 4). Operator confirms.
- `FORECAST_Z_MIN_OBS: int = 60` (the gate's own floor `FORECAST_NAIVE_GATE_MIN_OBS`, kept as a separate key so it can be tuned; see section 3 on overlap).
- Both numbers are also returned by `GET /thresholds` (`api/pilots_api.py:2016`) and `Thresholds` in `webapp/src/api/types.ts`, so help text reads them live and the viewer literals go.

Ladder (point magnitudes unchanged so `SIGNAL_WEIGHTS` calibration is untouched): `z >= Z_T` -> +10; `z <= -Z_T` -> -10; `|z| < Z_T` -> 0 (inside its own error, no evidence); unavailable -> 0. This drops the +5 tier and the "any forecast <= price is -10" asymmetry; whether to keep a +5 tier is Q3. Advisory: bullish `z >= Z_T`, bearish `z <= -Z_T`.

### 2.5 sellRange from the upper quantile
Flag on: `take_profit_hi = max(price + 3*ATR, Forecast_30_Upper)` (new `forecast_upper` field on `ExecutionRangeParameters`, `dto_models.py:466-478`; `evaluate_security` gets one kwarg). `Forecast_30_Upper` is already persisted (F4 2a). Caveats the operator should weigh:
1. It is Monte Carlo's 95th percentile, not a band on the blend. If the blend sits above MC's p95, HI goes **down** vs today; the diff lists both directions.
2. Only `monte_carlo` rows carry bounds (`forecasting_engine.py:1738`), so coverage is measurable only for MC (`coverage_report`, `forecast_tracker.py:1455`); a blend-centred band would need new recording (Q5).
3. A band suppressed by an F2 guard, or a fallback row, gives the ATR leg only, never the point forecast.
4. p95 is a stretch target by construction (5% of paths, by MC's own claim).

### 2.6 One producer is a precondition
With `ADVISORY_REUSE_PIPELINE_COMPUTE` off, the advisory overlay calls `generate_forecast` a second time with its own tracker (`engine/advisory.py:869`; `_build_forecasting_engine()` always attaches one).
- Both calls upsert the same pending `(symbol, model, horizon, ET day)` row, so the ledger's `blend` for the day is whichever call ran last. It can differ from the `Forecast_30` that fed `forecast_alignment` (CNN-LSTM and GARCH are not bit-reproducible, F2 walkthrough).
- That already weakens "score what you publish", and it would make `E` and `z` disagree with the number that drove the score.
- Before F5-3 flips: either turn on the existing `ADVISORY_REUSE_PIPELINE_COMPUTE` after a one-cycle decision diff (no code), or make advisory read the pipeline's persisted forecast (F4 part 2b iii). Q7.

## 3. Data readiness

### 3.1 What "enough" means
A scored observation = one (symbol, US/Eastern forecast day) row of the published model at h=30 with `actual_price` set, deduped to the last row per day, `forecast_day + BDay(30) < today's ET day` (strict, as the gate), price >= $1. Per symbol, `z` is available when **n >= `FORECAST_Z_MIN_OBS` (60)**, the gate's evidentiary floor.
- Flip criterion proposed: **every currently held symbol has n >= 60 at h=30** (Case A only fires for holdings, so a held symbol without z silently loses its loss-cutting rule), plus the universe coverage `k of M` printed for the operator. No other universal percentage is invented.
- Two caveats for the operator: (a) consecutive-day 30-day forecasts share 29/30 of their window, so 60 daily rows are about two independent windows (`n_indep ~ n / h`; the report prints both); (b) the window must cover `h + N - 1` business days: `(30+60-1)*7/5` = about 125 calendar days at h=30 (fine at 180/365); at h=90 `(90+59)*7/5` = about 209 calendar days, so the 180-day default can never reach n=60 at h=90 (arithmetic; irrelevant to decisions, relevant to F6 display).

### 3.2 When (arithmetic, not a forecast)
`blend` cannot be back-filled (it depends on the models of the day), unlike `naive`. If the first live `blend` day is S, the first usable day for n=60 at h=30 is `S + BDay(30+59) + 1 BDay`. Examples: S = 2026-09-28 -> 2027-02-01; S = 2026-10-12 -> 2027-02-15 (pandas `BDay`, holidays ignored as the code does). So the parent plan's "about mid-October" is only when the **first** 30-day rows mature (S + 30 BDay = 2026-11-09 or 2026-11-23 in the two examples); n=60 is roughly three months later. Options are Q2.

### 3.3 Measure it (read-only; I could not see your DB)
SQL below was syntax-checked only against a synthetic ledger built with the real DDL, not your data. Run with `sqlite3 "file:$HOME/.stockpy_local/quant_platform.db?mode=ro"` (uri) so nothing is written.
```sql
-- A. when did real blend rows start, how many?
SELECT model_name, MIN(forecast_day) first_day, MAX(forecast_day) last_day, COUNT(*) n_rows, COUNT(DISTINCT symbol) symbols
FROM forecast_errors WHERE model_name IN ('blend','gated_blend') AND forecast_day IS NOT NULL GROUP BY model_name;
-- B. matured scored rows per symbol, summarised per model x horizon (gated_blend: admitted days only)
WITH per AS (
  SELECT symbol, model_name, horizon_days h, COUNT(*) n, MIN(forecast_day) first_day
  FROM forecast_errors
  WHERE model_name IN ('blend','gated_blend') AND forecast_day IS NOT NULL
    AND actual_price IS NOT NULL AND forecast_price >= 1.0
    AND (model_name <> 'gated_blend' OR gate_fallback = 0)
  GROUP BY symbol, model_name, horizon_days)
SELECT model_name, h, COUNT(*) symbols, SUM(n>=30) ge30, SUM(n>=60) ge60, MAX(n) max_n, ROUND(AVG(n),1) mean_n, MIN(first_day) first_day
FROM per GROUP BY model_name, h ORDER BY h, model_name;
-- C. pending (not yet matured) rows and the earliest forecast day still pending
SELECT model_name, horizon_days h, COUNT(*) pending, MIN(forecast_day) earliest_pending_day
FROM forecast_errors WHERE model_name IN ('blend','gated_blend') AND forecast_day IS NOT NULL AND actual_price IS NULL
GROUP BY model_name, horizon_days;
```
Query B approximates the $1 filter with the blend's own price and the cutoff with "has an actual"; the exact count is what the gate sees.

**Spec for `scripts/forecast_f5_readiness.py`** (PR F5-1; read-only, `--json`, exit 0/2 like `feature_freeze_status.py`):
- For each symbol with rows, call `ForecastTracker(readonly=True).published_error_stats` (the code path F5 will use) at h=30.
- Print per symbol `n`, `n_indep`, `E`, would-be `z` and its reason; a summary `k of M` and the held symbols still missing; per-horizon pending count and first-maturity date; the projected first-ready date from the formula in 3.2.
- Contamination: also run `python scripts/clean_forecast_ledger.py` (dry run, categories a,b,d). The F3 walkthrough found 16 test-contaminated `blend` rows (AAPL/SPY at about $10); they must be gone before `E` is trusted.

## 4. Validation (before the flag can be flipped)

1. **Harness backtest, proxy only.** New adapter `_build_forecast_direction_z_adapter` and `STRATEGY_REGISTRY` entry `forecast_direction_arima_hw_z` in `scripts/refresh_validations.py`.
   - Identical to `forecast_direction_arima_hw` (`:1743-1872`: universe, 5-year window, weekly rebalance, SPY SMA-200 overlay, `.shift(1)`, `TieredCostModel`, declared turnover) except the classification.
   - Per ticker, a walk-forward buffer of the proxy's own ARIMA+HW 30-day errors: a forecast issued at t' enters `E` only when `t' + 30 trading days < t`, scored against the close at `t' + 30`. `n_min` = `FORECAST_Z_MIN_OBS` in weekly issue dates (60 trading days = 12 weekly). `z = ln(F/P)/E`, scored through the **real** `ForecastAlignmentSignal` z path (not a reimplementation, as today).
   - Honest scope, written into the docstring: it validates the construction on a proxy, **not the live blend** (no ledger exists that far back; `signal_replay_balanced_blend` excludes `forecast_alignment`, `:1903`).
   - The run needs FMP network access, which this sandbox lacks, so an operator machine or a networked agent runs it. Do not claim a re-verification that did not happen.
2. **Gate** (thresholds read from `validation/thresholds.py`, never copied): PBO < 0.5, DSR > 0.95, net-of-cost Sharpe > 0.5, MaxDD < 30%.
   - Z_T is pre-registered (2.4); its three values are emitted as variants so PBO/DSR see the selection. No tuning on the window, no date-snooping, no loosened gate.
   - **Baseline warning:** the fixed-threshold proxy is itself `deployable=False` (Sharpe -0.175, PBO 0.000, DSR 0.338, MaxDD 21.7% on the 2026-09-07 widened universe; curated-only 0.424 / 0.000 / 0.841 / 29.8%; `docs/signals/forecast_alignment.md`). An honest FAIL for both is a likely outcome. The flag then stays off unless the operator explicitly accepts a **relative A/B** (z vs fixed on identical inputs: Sharpe, MaxDD, turnover) as the bar (Q9).
   - Either outcome is written into `docs/signals/forecast_alignment.md` (Backtest Validation) **and** `docs/VALIDATION_STRATEGY_FIX_LOG.md` (`strategy-validation` skill, section 4).
3. **No-lookahead (perturbation) tests.** (a) Live `z`: two ledgers with the same matured rows, future rows (outcome after `as_of`) made great in one and awful in the other; `E`, `n`, `z` bit-identical; non-vacuous companion (judged later the awful rows change it); model on `tests/test_forecast_rebuild_f3.py::TestGateHasNoLookahead` (`:327`). (b) Adapter: triple every close after a cutoff date; scores at/before it bit-identical (as F2's Prophet test).
4. **Flag-off byte-identity.** Results dict and every ledger row equal pre-F5 output with the flag off (model: `TestFlagOffIsByteIdentical`, `:534`); new keys appear only as NaN-filled extras.
5. **Live-cycle before/after diff.** New read-only `scripts/forecast_decision_diff.py` (F2 method: `state_snapshot.json`, `price_bars` via sqlite `mode=ro`, `ForecastTracker(readonly=True)`).
   - Better than F2: F5 does not change the forecast, so the tool uses the snapshot's persisted `Forecast_30`/`Forecast_30_Upper` **frozen**, with no refit and no CNN-LSTM run-to-run noise. Only `E` comes from the ledger.
   - Output per symbol: z, E, n, reason; alignment points before/after; score and action tier; advisory bearish/bullish before/after and whether Case A/C/boost fires (and `suggested_exit_pct`); `sellRange` HI before/after (both directions); count of symbols with z unavailable.
   - Any symbol whose action or exit changes is listed with its cause and is the operator's to approve.
6. **Live shadow (recommended, operator may strike).** Mirror F3: F5-1 records the would-be z classification in the snapshot with the flag off; once enough days mature, compare realised direction hit rate of the z rule vs the fixed rule on live data. This is the only evidence about the live blend rather than the proxy.

## 5. F6 design: one Forecasts screen

**Screen** (`/forecast` route kept for bookmarks, `ResearchHub`, Marketplace; label "Forecasts"; `ForecastViewer.tsx` reworked in place to keep the diff reviewable):
- Universe table, one row per tracked symbol from the **pipeline's persisted forecast**: price; per horizon the published forecast and band (`forecast_{h}_lower/upper`), "published = naive" and "fallback" badges, z, E and n (F5, nullable until F5-1).
- Symbol panel: price chart with the band cone (existing `ForecastCandleChart`); **gate**: models admitted (latest `gated_blend.gate_admitted`) and rejected with the reason from `compute_naive_gate(...)["rejected"]` (`forecast_tracker.py:419-422`) plus the gate settings; **skill vs naive** per horizon and model (`n`, median |log error|, naive's, % beating naive, sign-test p, direction hit rate; `skill_vs_naive(..., symbol=...)` already accepts a symbol, `forecast_tracker.py:1645`); MC band coverage (`coverage_report`).
- **What the forecast changed this cycle:** `forecast_alignment` points = `score_components["forecast_alignment"]` (already in the snapshot, `main_orchestrator.py:1094`); action tier without that contribution, from `score` minus the contribution at the same 75/55/35 cuts, labelled approximate because the kill-switch and choppy-market overlays are not in the snapshot; advisory effects (below); whether `sellRange` HI came from the forecast (derivable from the string: `LO = P + 1.5*ATR`, so HI > `2*LO - P` beyond rounding means the forecast leg won; pinned by a test).
- Replaces the hard-coded +-1.5% label and the per-request refit data source (`ForecastViewer.tsx:212-214`, `client.ts:630`).

**Backend** (all `GET`, fail-open read tier `require_read_token`, no writes, so no new `*_ENABLED` flag; per the `pilots-endpoint` skill):
- `pilots/forecasts.py`: new dependency-light reader (stdlib + `settings` + `forecasting.forecast_tracker` + the snapshot file; no `forecasting_engine`, `strategy_engine`, `signals`, `desktop` imports), added to `tests/test_pilots_strategy_matrix.py::test_pilots_read_helpers_stay_dependency_light` (`:433`). Never raises: missing/corrupt snapshot or ledger -> empty shape + `reason`.
- `GET /forecasts` -> `{as_of, source, gate:{enabled,min_improvement,min_obs}, z:{enabled,threshold,min_obs}|null, rows:[{symbol, price, horizons:{"10"|"30"|"60"|"90":{forecast,lower,upper,is_fallback,gated_naive,z,z_reason,err,err_n}}, impact:{alignment_points, action, action_without_forecast, advisory_effects:[...], sell_hi_from_forecast}}], reason}`.
- `GET /symbols/{ticker}/forecast-summary` -> `{symbol, horizons:{h:{published_model, skill:[{model,n,median_abs_log_error,naive_median_abs_log_error,pct_beating_naive,sign_test_p,direction_hit_rate}], gate:{admitted,rejected:{model:reason},fallback_to_naive}, mc_coverage:{n,empirical,nominal}}}, reason}`. A new route instead of widening `GET /symbols/{t}/forecast` (single horizon, `api/pilots_api.py:1504`, consumed by `ModelHealthPanel` and `SymbolDetail`); that route is unchanged.
- `GET /thresholds` gains `forecast_gate_min_improvement` and `forecast_gate_min_obs` in F6a (existing settings); `forecast_z_threshold` and `forecast_z_min_obs` arrive with F5-1, when those settings exist (`z` is `null` until then).
- Additive, decision-neutral change in `engine/advisory.py`: a trailing `Recommendation.forecast_effects: tuple[str, ...]` (`case_a_exit`, `case_c_hold`, `bullish_conviction_boost`), persisted as `advisory_forecast_effects` in `_write_state_snapshot` and pinned in `tests/test_state_snapshot_parity.py`. Today the case is only free text in the rationale (`holding_override_reason`, `advisory.py:1135-1190`). Proved neutral by a byte-identical decision test.

**Webapp** (`new-pwa-screen` order: `types.ts` with every field explicit and `| null` where honest -> `client.ts` with explicit `http<T>` generics + `mock.ts` -> screen -> route/nav -> tests; `Thresholds` extended):
- Honest mock fixtures, one per branch: cold start (no ledger, `reason`); snapshot missing (404 -> honest empty state); gate admits nothing (published = naive banner, `gated_naive` true); `Is_Fallback` forecast; band null for one horizon; z unavailable with `n < 60`; pre-F3 ledger; ledger read error; a gate that admitted a model, with a rejected-reason list; F5 flag off vs on.
- Reachability: `/forecast` already has a desktop `NAV_ITEMS` entry (`navigation.tsx:98`), a Marketplace "Explore" card (`Marketplace.tsx:442`), `ResearchHub.tsx:46` and the command palette (`CommandPaletteModal.tsx:47`). Only the label and copy change; no `NAV_ITEMS` slot moves.
- Links in: Symbol Detail's "Forecast skill" card and `ModelHealthPanel` (rendered in `AgenticTrading.tsx:271`) link to `/forecast?symbol=...` (the screen reading a `symbol` query param is new).
- Help: `TAB_HELP.forecast` and new `GLOSSARY` terms (naive gate, skill vs naive, forecast z) in `webapp/src/help/helpContent.ts`, with numbers from `/thresholds` only. `shared/help_content.py` entries ("Sell Zone" `:1461,1493`, Case A text `:915,1654`) carry `guide_anchor`s that must resolve in `docs/HOW_TO_GUIDE.md` (`tests/test_help_content.py::TestAnchorValidity`).

**Verification (F6):** targeted pytest for the reader, routes (200 shape, fail-open with no token, 401 on wrong token, cold-start `reason`, never 500 on a corrupt file), parity and advisory-neutral tests; `npm run --prefix webapp typecheck`; `npm run --prefix webapp test` (vitest); `npm run build`; then a real browser pass per the `verify-webapp` command, against the mock **and** `VITE_USE_MOCK=false` on a live backend (a typecheck alone has missed live field mismatches before), console clean, every honesty fixture seen.

## 6. PR breakdown and ordering

Per the parent plan: one PR each, each merged before the next (merge order is 1 to 5; development of independent lanes can overlap). **Recommended order differs from the parent plan** (Q1): F5 is blocked on data until roughly February 2027 (3.2) while F6 only reads, so run F6 first; its nullable F5 fields show "unavailable" until F5-1 lands.

1. **F6a, read contract + backend** (M, ~2 agent-days). Starts **now**.
   - Lane A: `pilots/forecasts.py`, `tests/test_pilots_forecasts.py`, the allowlist line in `tests/test_pilots_strategy_matrix.py`.
   - Lane B: `api/pilots_api.py` (two routes, `/thresholds`) and route tests. B imports A, so agree the two function signatures first.
   - Lane C: `engine/advisory.py`, `main_orchestrator.py::_write_state_snapshot`, `tests/test_state_snapshot_parity.py`, the decision-neutral test.
2. **F6b, webapp** (L, ~3-4 agent-days). Lanes D, E, G start now; **merge after F6a** so the live parity check is real.
   - Lane D (single owner of the shared contract files): `webapp/src/api/{types,client,mock}.ts` and their tests. Can start from the contract in section 5.
   - Lane E: `webapp/src/screens/ForecastViewer.tsx` and its test.
   - Lane F (doors and links): `App.tsx`, `navigation.tsx`, `CommandPaletteModal.tsx`, `Marketplace.tsx`, `ResearchHub.tsx`, `SymbolDetail.tsx`, `ModelHealthPanel.tsx` and their tests.
   - Lane G (help): `webapp/src/help/helpContent.ts`, `shared/help_content.py`, `docs/HOW_TO_GUIDE.md`.
3. **F5-1, measure + shadow, no decision change** (M, ~2-3 agent-days). Can be developed now (it is measurement; flag off is byte-identical) but **needs Q10 answered first**, because it adds the three `FORECAST_Z_*` settings during the freeze.
   - Lane H: `forecasting/forecast_tracker.py` (`published_error_stats`).
   - Lane I: `forecasting_engine.py`, `pipeline/production_steps.py` (ForecastingStep columns only), snapshot keys and the parity test.
   - Lane J: `settings.py`, `shared/env_io.py`, `api/pilots_api.py` (`/thresholds` z keys), `webapp/src/api/types.ts` (`Thresholds`), census/liveness regenerated with the repo scripts.
   - Lane K: `scripts/forecast_f5_readiness.py` and its test.
4. **F5-2, validation vehicle + diff tool** (M, ~2-3 agent-days plus run time). Code can start now; **the run is blocked** on FMP network access and on the operator confirming Z_T and Q9.
   - Lane L: `scripts/refresh_validations.py` adapter, registry entry, perturbation test.
   - Lane M: `scripts/forecast_decision_diff.py` and tests.
   - Docs lane after the run.
5. **F5-3, decision change behind the flag** (M, ~2-3 agent-days). **Blocked** on data readiness (3.1), the F5-2 verdict, and Q2/Q3/Q7/Q9. Merged flag-off; **flipping is a settings change made only on the operator's OK.**
   - Lane N: `signals/forecast_alignment.py`.
   - Lane O: `engine/advisory.py` (z classification; reads `forecast_effects` from F6a).
   - Lane P: `strategy_engine.py`, `dto_models.py` (`forecast_upper`), `pipeline/production_steps.py` (`vec_df['forecast_z']`, per-row kwarg), `ml/meta_bootstrap.py` whitelist, `tests/test_train_meta_labelers.py`.

Efforts are my estimates from diff size and the F1-F4 PRs, not measurements.

Not in this plan, still open under F4: part 2b (CNN-LSTM/Prophet archive, sector blend removal) waits on the F3 flip decision; `/metrics/forecast/{symbol}` becoming a persisted read can ride in #1 (lane B) if you want it there.

## 7. Documentation, risks, open questions

### Documentation per PR (exact files)
- **F6a:** `docs/architecture/observability-and-apis.md` (new reader and routes), `docs/architecture/testing.md`, `docs/FEATURE_TIER_HISTORY.md` (dated entry), `.claude/forecasting_f6a_*_walkthrough.md`.
- **F6b:** `docs/architecture/webapp-and-gui.md`, `docs/HOW_TO_GUIDE.md` (Forecasts section/anchors), `webapp/src/help/helpContent.ts`, `shared/help_content.py`, `.claude/forecasting_rebuild_implementation_plan.md` (F6 status note), `docs/FEATURE_TIER_HISTORY.md`.
- **F5-1:** `docs/architecture/signal-engines.md` (`forecasting_engine.py` entry), `docs/settings_field_census.{json,md}` + `docs/settings_liveness.json` (regenerated with `scripts/measure_settings_census.py --write` and `scripts/settings_liveness.py --write`, never by hand; `settings.py` line numbers shift), `docs/FEATURE_TIER_HISTORY.md`, `docs/RUNBOOK.md` (readiness query).
- **F5-2:** `docs/signals/forecast_alignment.md` (Backtest Validation, pass or honest FAIL), `docs/VALIDATION_STRATEGY_FIX_LOG.md`, `docs/architecture/validation-and-signals.md`.
- **F5-3:** `docs/signals/forecast_alignment.md` (Signal Logic: z ladder, unavailable rule), `docs/architecture/signal-engines.md`, `CLAUDE.md` + `AGENTS.md` mirror (a forecasting-decisions bullet and the new flag), `shared/help_content.py` + `docs/HOW_TO_GUIDE.md` (Sell Zone, Case A copy), `docs/FEATURE_TIER_HISTORY.md`.
- Every PR also commits its task-scoped plan/task/walkthrough under `.claude/` (CLAUDE.md rule 6; no bare filenames).

### Risks
1. **Statistical power.** Overlapping windows: 60 daily rows at h=30 are about two independent windows, so `E` is a noisy scale and z near the threshold flips on noise. Mitigation: print `n_indep`; Q2.
2. **Bad models inflate E.** `blend` currently contains models that do not beat naive (F1 walkthrough, 2026-09-27: CNN-LSTM median |log error| 0.32 vs naive 0.044 at h=10, n=27). A larger `E` shrinks z and quietly mutes the forecast until F3/F4 prune them. Intended, but it couples F5's effect to the F3 flip.
3. **F3/F5 coupling.** After the F3 flip the error source is `gated_blend` admitted days, which cannot exist before the gate admits something plus 30 business days to mature plus the n floor. Recommended: validate and flip F5 on the currently published `blend` first (Q6).
4. **Two producers** (2.6) break "score what you publish" and can make z disagree with the score.
5. **Ledger contamination** (test leaks, 2026-08-14 seed rows, $100 inputs) feeds `E`; cleanup status UNKNOWN (U2).
6. **Decision impact on a live account:** up to 20 score points, Case A exit changes, `sellRange` movement in both directions; mitigated by the flag, the frozen-input diff, and no flip without the operator.
7. **Proxy backtest cannot speak for the live blend**, and the baseline already fails the gate (section 4).
8. **Hot path:** one more ledger read per symbol per cycle with 8 worker threads sharing one tracker lock; reuse the existing single read (2.2).
9. `.env` vs runtime-flags-store precedence: a flag flip must update or clear both (`CLAUDE.md`, runtime flags store).
10. Freeze: F5/F6 are under the already-approved rebuild plan, but three new settings (`FORECAST_Z_*`) are flagged here for explicit OK (Q10).

### Open questions for the operator
- **Q1** Run F6 before F5 (recommended; F5 is data-blocked)? The parent plan says F5 then F6.
- **Q2** `FORECAST_Z_MIN_OBS`: keep 60 (about Feb 2027 at the earliest by 3.2), lower it, or pool a vol-normalised error across symbols (needs a new ledger column for forecast-time sigma, so it only accrues from now)?
- **Q3** Z_T value (proposed primary 1.0, neighbours 0.5 and 1.5), and ladder shape: symmetric dead zone (proposed) vs keep a +5 tier?
- **Q4** `z` unavailable -> neutral (proposed) vs fall back to the legacy +-1.5%/+-3%?
- **Q5** sellRange: Monte Carlo p95 as persisted (proposed), a different quantile, or record a blend-centred band first? Is a display-only change with higher targets acceptable?
- **Q6** Validate/flip F5 on `blend` before the F3 flip (proposed), or after?
- **Q7** Live value of `ADVISORY_REUSE_PIPELINE_COMPUTE`, and OK to turn it on after a one-cycle diff (or have advisory read the persisted forecast)?
- **Q8** Answers to U1-U4 (live flags; whether cleanup/backfill applies ran; the `--gate` output; query A/B/C output).
- **Q9** Flip bar given the proxy baseline already fails the gate: absolute gate only, or accept a relative A/B (non-inferiority) with the honest FAIL documented?
- **Q10** OK to add `FORECAST_Z_DECISIONS_ENABLED`, `FORECAST_Z_THRESHOLD`, `FORECAST_Z_MIN_OBS` during the freeze?
