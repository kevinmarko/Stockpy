# Agentic Trading: Multi-Horizon Forecast Backfill & Meta-Labeling Guide

## Overview

The Multi-Horizon Forecast Backfill and Meta-Labeling engine (`ml/forecast_backfill.py`) implements Marcos Lopez de Prado's Meta-Labeling methodology across **10, 30, 60, and 90-day horizons** for primary momentum models:

1. **Time-Series Momentum (TSMOM)**
2. **Cross-Sectional Momentum (CSMOM)**

The pipeline trains confidence classifiers that evaluate market environment conditions (volatility, RSI, MACD, volume ratio) to output out-of-sample $P(\text{success})$ probabilities for primary signals, per horizon.

**Current scope: Research/diagnostic with an opt-in live gate bridge.** The trained
per-horizon models (`ml/models/backfill_meta_{signal_id}_{horizon}d_{stamp}.pkl`) are plain pickled
classifiers, wrapped as `ml.meta_labeling.MetaLabeler` instances, and can be registered into
`ml.meta_labeling.global_meta_registry` if they pass the deployability gate (PBO/DSR) and an explicit
feature compatibility check. The operator must explicitly opt a signal in via `settings.META_LABELING_BACKFILL_ELIGIBLE_SIGNALS`
and enable the bridge via `settings.META_LABELING_BACKFILL_BRIDGE_ENABLED`. When both exist, AFML models are
prioritized over backfill-screen models as a tie-break.

**2026-09 update: the feature-compatibility gate no longer refuses all 6 eligible signals by construction.**
The gate (`ml.meta_bootstrap.LIVE_ROW_FEATURE_WHITELIST`/`check_feature_compatibility`) previously refused
to register any model whose declared training features weren't fully present in the live per-ticker feature
row `strategy_engine.py::evaluate_security()` actually queries a meta-labeler with — and none of the 6
backfill-eligible signals' declared `meta_label_features` were fully covered by that row. That row has now
been widened with the 9 previously-missing feature names (`Vol_20`, `Vol_50`, `Vol_Ratio`, `RSI_14`, `MACD`,
`MACD_Signal`, `ROC_6M`, `ROC_5`, `ROC_20`), each genuinely computed — not fabricated or zero-filled — and
`tests/test_train_meta_labelers.py::TestSixEligibleSignalsFeatureCompatibility` proves all 6 signals' real
`meta_label_features` now pass `check_feature_compatibility()`. Be precise about scope: this closes the
*compatibility* gate specifically. A signal still separately needs to pass the DSR/PBO deployability gate
(unchanged, untouched by this fix) to actually register live. Do not read this update as "all 6 signals are
now live" — only the compatibility gate itself was fixed. See each signal's own `docs/signals/<name>.md`
"Backfill-Screen Live Meta-Labeler Bridge" section for the per-signal registry key, chosen live horizon, and
this same caveat.

**Correction, same doc, 2026-09 follow-up: the `cross_sectional_momentum` "Signal column unconditionally
NaN" claim above is stale.** The "KNOWN GAP" docstring it referenced was removed from
`ml/forecast_backfill.py` by an earlier fix (commit `8e000117`, "wire cross-sectional pre_compute into the
forecast backfill pipeline") — `step_3_generate_primary_signals` now detects this module's overridden
`pre_compute` and routes it through `_run_cross_sectional_module`'s real two-phase replay (via
`compute_batch_xsec`'s vectorized fast path, see the "Step 3 vectorization" section below). Verified
directly: a synthetic-panel run trains all 4 horizons with a genuinely two-sided (+1/-1) Signal column.
`sector_quality_rank` was, at the time this paragraph was originally written, also genuinely blocked from
training (no accrual_ratio/gross_profitability/sector data anywhere in this pipeline). It no longer is — see
this doc's own WP3 section below, which unblocked it via a real, opt-in SEC EDGAR fetch
(`settings.FORECAST_BACKFILL_SNEQR_QUALITY_FACTS_ENABLED`, verified live). `vrp_premium_selling` remains
genuinely blocked — real per-ticker historical implied volatility is structurally unavailable from this
repo's permitted data sources (FMP/Yahoo); see `docs/known_issues/vrp_premium_selling_no_historical_iv.md`
and this doc's WP4 section below.

---

## Technical Features & Signal Formulations

### Primary Signals
- **TSMOM**: 252-day absolute return sign ($+1$ if $R_{252} > 0$, else $-1$).
- **CSMOM**: 252-day cross-sectional percentile rank across the stock universe ($+1$ if percentile rank $> 0.5$, else $-1$).

### Contextual Features
- **Vol_20 & Vol_50**: 20-day and 50-day rolling standard deviations of daily returns, annualized by $\sqrt{252}$.
- **RSI_14**: 14-day Relative Strength Index.
- **MACD**: Moving Average Convergence Divergence ($EMA_{12} - EMA_{26}$).
- **Vol_Ratio**: Ratio of daily volume to 20-day moving average volume.
- **ROC_5 / ROC_20**: 5-day and 20-day rate-of-change, now genuinely computed in `step_2_calculate_technical_features()` (previously declared by `options_flow_sentiment`'s `meta_label_features` but silently dropped every training run since they were never computed here — a real, now-fixed gap, not merely a doc omission).
- **ROC_6M**: 6-month rate-of-change — already computed here, but only threaded into the live per-ticker row as part of the 2026-09 feature-compatibility fix described above.

### Meta-Target Formulation
For horizon $h \in \{10, 30, 60, 90\}$ days:
- Actual forward return: $R_{t, t+h} = \frac{P_{t+h}}{P_t} - 1$.
- Meta-Label target ($y$): $1$ if $\text{sign}(\text{Primary Signal}) == \text{sign}(R_{t, t+h})$, else $0$.

---

## Performance, Timeout, and Partial-Result Checkpointing

A full-universe run (~515 tickers × 4 horizons) is dominated by two CPU-bound stages —
step 3's per-date/per-ticker cross-sectional signal replay and step 5's per-(model,
horizon) meta-labeler training — and can genuinely take well over 30 minutes. Three
complementary fixes address this:

**Step 3 vectorization.** `_run_cross_sectional_module` (`ml/forecast_backfill.py`) first
tries a module's optional `SignalModule.compute_batch_xsec(ranks_wide)` hook
(`signals/base.py`, default `None`) before falling back to the original per-date
`pre_compute()` + per-ticker `compute()` replay. `CrossSectionalMomentumSignal`
(`signals/cross_sectional_momentum.py`) implements it as `2.0 * (ranks_wide - 0.5)` — the
same formula `pre_compute`/`compute` already use, verified numerically identical
(`test_cross_sectional_fast_path_matches_slow_path_parity`) — computed once, vectorized,
over the entire historical panel instead of once per (date, ticker). `SignalModule`
subclasses that don't implement the hook keep using the original, correctness-preserving
loop. Separately, `step_3_generate_primary_signals` now skips any registered module with
no `meta_label_features` before running it at all, since such a module can never be
trained or exported by this engine regardless of what step 3 computes for it.

**Step 5/6 checkpointing.** Training (step 5) and inference (step 6) are interleaved
per (model_type, horizon) combo — `_infer_one()` runs immediately after each combo's
classifier is trained and its `.pkl` is saved, instead of waiting for every combo across
every strategy to finish first. After each combo, `_write_partial_export()` writes a
running snapshot to `output/agentic_forecast_backfill.partial.csv` /
`output/agentic_forecast_summary.partial.json` — **separate files** from the canonical
`agentic_forecast_backfill.csv` / `agentic_forecast_summary.json`, which remain
exclusively the product of a genuinely *completed* run via `export_results()`. The
worker process (`ml/forecast_backfill_worker.py`) also emits a `{"event": "progress", ...}`
NDJSON event after each combo; the parent job (`ml/forecast_backfill_job.py`) records this
onto `BackfillJobState.partial_summary` (`{"trained": [...], "metrics_so_far": {...}}`),
included in `serialize_job()`'s payload and therefore in every
`GET /pilots/forecast_backfill/status/{job_id}` poll response. `_enforce_deadline` never
touches this field, so whichever combo last completed before a timeout SIGKILLs the worker
is preserved. The Pilots PWA reads it: `backfillFailureMessage()`
(`webapp/src/forecastBackfillCopy.ts`) reports *"The backfill timed out after training N
models — partial results were saved"* instead of the old unconditional "Nothing was
saved" whenever `partial_summary.trained` is non-empty, and `ForecastBackfillScreen.tsx`
renders a "Partial Results Saved Before Timeout" table listing the checkpointed models.

**Deadline.** `settings.FORECAST_BACKFILL_DEADLINE_SECONDS` (see table below) is a hard
wall-clock backstop independent of the above — it still exists even after the
vectorization/checkpointing fixes land, and was raised from its prior 1800s (30 min)
default because 30 minutes was measured to be genuinely too short for a full-universe run
even before accounting for the perf work above.

---

## Zero Hardcoded Configuration Reference

All hyperparameters are centralized in `settings.py` and configurable via `.env`:

| Setting | Default | Description |
|---------|---------|-------------|
| `FORECAST_BACKFILL_HORIZONS` | `[10, 30, 60, 90]` | List of forecast horizons in days. |
| `FORECAST_BACKFILL_LOOKBACK_YEARS` | `4` | Default backfill window in years, used when no explicit `start_date` is given (e.g. the webapp's "Run Forecast Backfill" button always omits it). Computed relative to `end_date` at run time — rolls forward on every re-run rather than growing unbounded. |
| `FORECAST_BACKFILL_MOMENTUM_WINDOW` | `252` | Lookback window in trading days for primary TSMOM/CSMOM signals. |
| `FORECAST_BACKFILL_VOL_SHORT_WINDOW` | `20` | Short rolling volatility lookback window in days. |
| `FORECAST_BACKFILL_VOL_LONG_WINDOW` | `50` | Long rolling volatility lookback window in days. |
| `FORECAST_BACKFILL_RSI_WINDOW` | `14` | RSI calculation window in days. |
| `FORECAST_BACKFILL_MACD_FAST` | `12` | MACD fast EMA span. |
| `FORECAST_BACKFILL_MACD_SLOW` | `26` | MACD slow EMA span. |
| `FORECAST_BACKFILL_VOL_RATIO_WINDOW` | `20` | Volume ratio moving average window in days. |
| `FORECAST_BACKFILL_TRAIN_SPLIT` | `0.80` | Chronological train/test split fraction (no lookahead bias). |
| `FORECAST_BACKFILL_N_ESTIMATORS` | `100` | Tree count for RandomForest / LightGBM classifier. |
| `FORECAST_BACKFILL_MAX_DEPTH` | `5` | Maximum tree depth for classifier. |
| `FORECAST_BACKFILL_RANDOM_STATE` | `42` | Random seed for reproducibility. |
| `FORECAST_BACKFILL_CLASSIFIER_TYPE` | `"random_forest"` | Algorithm (`"random_forest"` or `"lightgbm"`). |
| `FORECAST_BACKFILL_DEADLINE_SECONDS` | `5400` (90 min) | Hard wall-clock deadline for one run, from worker start to a terminal result — the process group is SIGKILLed if it hasn't produced a result by then. Raised from a prior `1800` (30 min) default, which was too short for a full ~500-ticker operator universe. GUI-writable. |
| `FORECAST_BACKFILL_SNEQR_QUALITY_FACTS_ENABLED` | `False` | Opt-in real SEC EDGAR fetch (accrual_ratio/gross_profitability/sector) per ticker in step 2 — the raw inputs `sector_quality_rank` needs. Off preserves byte-identical pre-2026-09 behavior; see the "Real Accrual/Gross-Profitability/Sector Data (WP3)" section below. GUI-writable. |
| `FORECAST_BACKFILL_VRP_PROXY_ENABLED` | `False` | Opt-in, OHLCV-only, no-network computation of `IVR_Proxy`/`VRP_Proxy` (realized-vol-derived stand-ins for the options-chain-derived `True_IVR`/`VRP`) and training of a SEPARATE, quarantined `vrp_premium_selling_proxy` model_type against them. Never touches `signals/vrp_premium_selling.py` or its live behavior, and structurally excluded from `ml/forecast_backfill_registry_bridge.py::BACKFILL_ELIGIBLE_SIGNAL_IDS` so it can never reach live inference. Off preserves byte-identical pre-2026-09 behavior; see the "Quarantined Realized-Vol Proxy for vrp_premium_selling (WP4)" section below. GUI-writable. |

---

## Real Accrual/Gross-Profitability/Sector Data (WP3, 2026-09)

`sector_quality_rank` declares `meta_label_features` but, before this, could never train a
single model regardless of universe size or history length — `signals/sector_quality_rank.py`'s
own "Data Availability Gap" docstring documents why: neither raw input (`accrual_ratio`,
`gross_profitability`) was computed anywhere in this pipeline. This is now fixed FOR THE
BACKFILL/RESEARCH PATH ONLY, gated behind `FORECAST_BACKFILL_SNEQR_QUALITY_FACTS_ENABLED`
(opt-in, default `False` — no network call, no new columns, byte-identical to before this
change whenever the flag is off).

**Reused, not reimplemented.** `data/sneqr_quality_facts.py::fetch_sneqr_quality_facts()` is
the same, already live-EDGAR-verified computation `scripts/refresh_validations.py`'s
`sector-quality-rank` Pilot validation has used since 2026-08 (extracted verbatim from that
script's `_fetch_sneqr_quality_facts`, which now delegates to the shared module) — real SEC
EDGAR XBRL `NetIncomeLoss`/`NetCashProvidedByUsedInOperatingActivities`/`Assets`/`GrossProfit`
tags, PIT-correct via `data/edgar_fundamentals.py::extract_latest_fact(..., max_date=filed)`.
`data/sneqr_quality_facts.py::load_ticker_sectors()` reads the same
`forecasting/data/ticker_sectors.csv` current-snapshot-applied-across-history approximation
already accepted for `macro_regime_pit`/`signal_replay_balanced_blend`.

**Wiring, when enabled**: `ml/forecast_backfill.py::step_2_calculate_technical_features()`
fetches and `merge_asof`-joins both ratios plus `sector` onto each ticker's panel;
`_run_cross_sectional_module()`'s per-date `universe_df` slice (previously hardcoded to only
`Symbol`+`XSec_12_1M`) is widened to carry these three columns through to
`sector_quality_rank.pre_compute()` — without this second step, `pre_compute()` would still see
nothing regardless of what step 2 computed, since it reads its raw inputs from the `universe_df`
it's handed, never from `self.data` directly.

**Verified live, not assumed**: a real 6-name Technology-sector universe
(`AAPL`/`MSFT`/`AMD`/`ADBE`/`ADI`/`AVGO` — `forecasting/data/ticker_sectors.csv` carries 84
Technology names, comfortably clearing `MIN_SECTOR_SIZE=5`) genuinely trains
`sector_quality_rank` across all 4 horizons —
`tests/test_forecast_backfill.py::test_sneqr_quality_facts_enabled_unblocks_sector_quality_rank_end_to_end`.

**Deliberately, disclosed scope trim — NOT done in this pass**:
1. **No DB persistence/caching.** Every backfill run re-fetches full EDGAR company-facts JSON
   per ticker (throttled ~10 req/s by `data/edgar_fundamentals.py`'s existing cross-process
   throttle). Acceptable for an occasional research run; a real cost for a wide universe run
   repeated often. A follow-up could persist both ratios into `HistoricalStore.fundamentals_history`
   (additive `ALTER TABLE ... ADD COLUMN`, the same idempotent-probe convention that table's
   `report_date` column already uses) so repeat runs read cache instead of re-fetching.
2. **The LIVE per-cycle trading pipeline is untouched.** `processing_engine.calculate_fundamental_metrics()`
   does not compute either ratio for production scoring — `sector_quality_rank` remains dormant
   there (`0.0` contribution to `final_score` every cycle) exactly as its own docstring already
   documented. Wiring the live path safely needs the DB-caching layer above first (a live cycle
   re-fetching full EDGAR JSON per ticker every few minutes, unthrottled-by-caching, is not
   something to ship). See `signals/sector_quality_rank.md`'s own "2026-09 update" note.
3. **No point-in-time sector history.** `load_ticker_sectors()` is a current snapshot applied
   across all history (unfixed, already-accepted approximation) and
   `universe_engine.get_sp500_constituents` returns the current roster for every historical
   date, so a wide backfill panel carries membership survivorship bias independent of this fix.

Tests: `tests/test_forecast_backfill.py`'s `test_sneqr_quality_facts_disabled_by_default_adds_no_columns`
(flag off is a true no-op, zero network calls) and
`test_sneqr_quality_facts_enabled_unblocks_sector_quality_rank_end_to_end` (live-EDGAR verification
above), both `@pytest.mark.network`.

---

## Quarantined Realized-Vol Proxy for vrp_premium_selling (WP4, 2026-09)

`vrp_premium_selling` also declares `meta_label_features` but, unlike `sector_quality_rank`
(WP3, above), it **cannot** be fixed the same way. `signals/vrp_premium_selling.py::VRPPremiumSellingSignal`
scores off `True_IVR`/`VRP` — real, per-ticker, options-chain-derived historical implied
volatility — and this repo's permitted data sources (FMP, Yahoo) structurally cannot
reconstruct that history. This is a verified, disclosed, unfixable data-availability gap, not
a bug: see `docs/known_issues/vrp_premium_selling_no_historical_iv.md` for the full root-cause
writeup (why no FMP/Yahoo endpoint on this account, or any tier, exposes historical per-ticker
IV). `signals/vrp_premium_selling.py` is completely untouched by this WP — the real signal's
own eligibility stays exactly as broken as it already was: `_build_training_set` always sees 0
rows (its `_Signal` column is all-NaN, since `True_IVR`/`VRP` are never populated by this
OHLCV-only offline engine), flag on or off.

**Instead, a QUARANTINED proxy.** `ml/vrp_premium_selling_proxy_signal.py::VrpPremiumSellingProxySignal`
is a SEPARATE `SignalModule` implementation, gated behind `FORECAST_BACKFILL_VRP_PROXY_ENABLED`
(opt-in, default `False` — no new columns, no new model_type, no proxy signal instantiated when
off). When enabled:

- `ml/forecast_backfill.py::step_2_calculate_technical_features()` computes two new,
  OHLCV-only, no-network columns per ticker:
  - **`IVR_Proxy`**: `df["GARCH_Vol"].rolling(252).rank(pct=True) * 100.0` — the trailing-252-day
    percentile rank of this pipeline's own vectorized EWMA-GARCH volatility estimate, the same
    percentile-rank semantics `volatility/iv_engine.calculate_true_ivr` computes against a real
    dated IV history, substituting `GARCH_Vol` for IV.
  - **`VRP_Proxy`**: a trailing 60-day realized-vol reading minus `GARCH_Vol`
    (`rv60 - GARCH_Vol`), matching `volatility/iv_engine.get_vrp`'s exact
    `current_iv - garch_vol` formula and the identical realized-vol-proxy convention
    `validation/options_selling_backtest.py` already uses (`LONG_TERM_VOL_WINDOW=60`).
- `step_3_generate_primary_signals()` instantiates `VrpPremiumSellingProxySignal()` directly and
  registers it into `AgenticForecastBackfiller._proxy_modules` (a NEW, backfill-engine-local
  dict, separate from `signals.registry.global_registry`) — every downstream module lookup in
  this file (`_get_module()`) checks both, so the proxy is transparently trainable/inferable
  alongside every real registry-backed signal without a special case at each call site.
- Gate logic is a byte-for-byte port of `VRPPremiumSellingSignal.compute_vectorized`/`.compute`
  (both the vectorized fast path and the per-row `@abstractmethod` implementation every
  `SignalModule` must provide), substituting `IVR_Proxy`/`VRP_Proxy` for `True_IVR`/`VRP` —
  same `IVR_SELL_THRESHOLD`/`VRP_MIN_THRESHOLD`/`VRP_SATURATION` constants (imported from
  `signals.vrp_premium_selling`, never re-duplicated, so the two gates can never silently drift
  apart), same score/confidence/explanation shape, same `meta_label_features`/`meta_label_horizons`
  (the same technical context columns the real signal already uses — neither references
  `True_IVR`/`VRP`/`IVR_Proxy`/`VRP_Proxy`, so the meta-labeler's own input features are
  identical either way).

**This is a REALIZED-VOL-REGIME model, not a volatility-risk-premium model.** It contains no
options-market information and supports no claim about the actual volatility risk premium the
real `vrp_premium_selling` thesis rests on — it can only ever say "realized vol looks
elevated/decaying in a way that resembles the shape of a rich VRP regime," never "the options
market is actually pricing a rich premium." Its `model_type` name
(`"vrp_premium_selling_proxy"`) is a research/diagnostic artifact only.

**Structural exclusion from live inference.** `ml/vrp_premium_selling_proxy_signal.py` has no
`global_registry.register(...)` call at module scope (every real `SignalModule` file has one —
see `signals/vrp_premium_selling.py`'s own bottom line) and its `model_type` is deliberately
absent from `ml/forecast_backfill_registry_bridge.py::BACKFILL_ELIGIBLE_SIGNAL_IDS`. The latter
is the load-bearing guarantee: `step_7_register_live_meta_labelers()`'s
`active_and_eligible = set(self.active_strategies) & set(BACKFILL_ELIGIBLE_SIGNAL_IDS) & set(eligible)`
is a three-way AND, so even an operator who explicitly opts a running instance into
`META_LABELING_BACKFILL_BRIDGE_ENABLED=True` and names `"vrp_premium_selling_proxy"` in
`META_LABELING_BACKFILL_ELIGIBLE_SIGNALS` can never get it promoted into
`ml.meta_labeling.global_meta_registry` — the intersection with `BACKFILL_ELIGIBLE_SIGNAL_IDS`
is guaranteed empty regardless of the other two sets.

**Verified offline — no network needed.** Unlike WP3's SNEQR facts (real SEC EDGAR), both
`IVR_Proxy` and `VRP_Proxy` are pure OHLCV derivations, so this whole WP is testable with fully
synthetic price data. A flat-vol random walk (this file's usual `_synthetic_engine` fixture)
gates on far too few rows to ever clear `_build_training_set`'s 30-sample floor — a
regime-switching synthetic universe (alternating low/high daily-vol blocks, so the slow
60-day realized-vol leg of `VRP_Proxy` stays elevated for a while after each high-vol block
ends while the fast EWMA `GARCH_Vol` leg has already decayed back down) reliably produces
enough genuinely-gated rows to train all 4 horizons.

Tests: `tests/test_forecast_backfill.py`'s
`test_vrp_proxy_disabled_by_default_adds_no_columns_and_leaves_real_signal_unaffected` (flag off
is a true no-op; the real `vrp_premium_selling` signal's pre-existing, unrelated
`insufficient_samples:0_*` eligibility is unchanged either way),
`test_vrp_proxy_enabled_trains_at_least_one_horizon_offline` (genuine offline training success),
`test_vrp_proxy_never_reaches_live_meta_labeler_registry_even_with_bridge_and_eligible_signals_set`
(the `BACKFILL_ELIGIBLE_SIGNAL_IDS` exclusion holds even when every other gate is deliberately
opened), and `test_vrp_proxy_never_registered_in_global_signal_registry` (the
`global_registry`-registration exclusion) — none `@pytest.mark.network`.

---

## Suppressing the options_flow_sentiment Momentum Proxy on the Backfill Path (WP5, 2026-09)

**The bug**: `signals/options_flow_sentiment.py::OptionsFlowSentimentSignal` declares
`meta_label_features` (one of the six Forecast-Backfill-eligible signals) and has a legitimate
fallback — when no real UOA (unusual-options-activity) flow data exists for a symbol, both
`compute_vectorized()` and its scalar sibling `compute()` fall back to a price-momentum proxy
built from `ROC_5`/`ROC_20`. That fallback is a correct, intentional feature of the live
production signal (a real UOA-data-outage degrade path). The problem is specific to the Forecast
Backfill screen: `ml/forecast_backfill.py`'s dummy `SignalContext`/synthetic `pre_compute()`
replay never carries real UOA flow data, so the fallback's trigger condition is always satisfied
here, and it fires on every row. A meta-labeler trained on `options_flow_sentiment_{h}d` would
therefore be meta-labeling pure price momentum wearing an "options flow sentiment" label — not a
fabrication of a *number*, but a fabrication of what the model actually *represents*. Until a
prior, unrelated fix, `ROC_5`/`ROC_20` were never computed anywhere in this engine's DataFrame, so
this fallback silently never fired and the signal never trained; once those two columns were
added (feeding the six-signal feature-compatibility widening), the fallback started firing
unconditionally.

**The operator's explicit decision (not up for reconsideration by a future pass)**: do not train
on this proxy. Suppress the fallback on the backfill path only, so this signal reports "no
historical UOA flow data" via the existing `eligibility`/`_mark_eligibility` mechanism instead of
silently training a mislabelled model. Deliberately unconditional — no new `settings.*` flag. This
is a bug fix removing an already-wrong default behavior (a model that would train on the wrong
signal), not new opt-in capability; there is no reasonable "keep the old broken behavior" mode
worth preserving behind a flag, unlike WP3/WP4's genuinely new, opt-in capabilities.

**A design assumption corrected mid-implementation**: the original plan for this fix assumed
`options_flow_sentiment` is dispatched through `step_3_generate_primary_signals()`'s plain
`else: out_df = module.compute_vectorized(self.data, context)` branch, and proposed suppressing
the fallback there by feeding a column-dropped copy of `self.data` into that one call. That
assumption is wrong: `OptionsFlowSentimentSignal` overrides `pre_compute` (it loads/synchronizes
persisted UOA records once per date), so `type(module).pre_compute is not SignalModule.pre_compute`
evaluates `True` for it, exactly like `cross_sectional_momentum`/`sector_quality_rank` — meaning
`needs_precompute` is `True` and it is dispatched through `_run_cross_sectional_module()`'s
per-date/per-ticker replay instead, which calls the **scalar** `compute()` method, never
`compute_vectorized()`. (`_run_cross_sectional_module`'s own pre-existing comment already lists
`options_flow_sentiment` alongside `cross_sectional_momentum` as a cross-sectional module for this
exact reason.) `compute()` carries its own, separately-written copy of the identical fallback
check (`if raw_val is None and "ROC_5" in row and "ROC_20" in row:` — a column-presence test on
the `pd.Series` row it's handed, not on a DataFrame's `.columns`).

**The actual mechanism implemented**: `ml/forecast_backfill.py::_run_cross_sectional_module()`'s
per-date loop, immediately before the `for idx, row in group.iterrows():` line that calls
`module.compute(row_with_symbol, context)`, now selects a `row_source` that is `group` for every
module EXCEPT `options_flow_sentiment`, for which it is
`group.drop(columns=["ROC_5", "ROC_20"], errors="ignore")` — a local copy with the two trigger
columns dropped, computed once per date (not once per row). With those two columns absent from
the row `compute()` receives, its fallback's presence check never passes, `raw_val` stays `None`,
and the module returns its own honest neutral degrade
(`score=0.0, confidence=0.0, explanation="Options flow sentiment: neutral/no flow data this cycle"`).
Back in `step_3_generate_primary_signals()`, `np.sign(0.0)` is `0.0`, and the existing
`.replace(0, np.nan)` step (already there, unrelated to this fix) turns that into `NaN` — so
`options_flow_sentiment_Signal` comes out entirely `NaN` on the backfill path. `self.data` itself
— every other module's view of it, and this signal's own `meta_label_features` resolution in step
5's `_resolve_meta_features` (which reads `self.data` directly, never the per-date `row_source`
copy) — is completely untouched; only the local `row_source` fed into this one module's dispatch
is modified. `signals/options_flow_sentiment.py` itself was not edited at all (verified: zero
diff).

**Downstream effect**: with `options_flow_sentiment_Signal` all-`NaN`, step 4's
`_Target_{h}d` columns for this signal are all-`NaN` too (`isna_mask` includes
`self.data[f"{name}_Signal"].isna()`), so `_build_training_set`'s `clean_df.dropna(...)` always
drops every row, `len(clean_df) < 30` for every horizon, and
`self.eligibility["options_flow_sentiment"]` honestly reports `trained: False` with
`reason == "insufficient_samples:0_for_<h>d"` — never a fabricated metrics row (CONSTRAINT #4).

**Live behavior is unaffected**: `signals/options_flow_sentiment.py`'s own `compute()` and
`compute_vectorized()` are byte-identical to before this change, so the fallback still fires
exactly as designed whenever a live cycle genuinely has no UOA flow data for a symbol. This is
purely a training-data-integrity decision scoped to the backfill/research engine, not a change to
production scoring.

Tests, appended to `tests/test_forecast_backfill.py`:
- `test_options_flow_sentiment_live_compute_vectorized_fallback_still_fires_unchanged` — calls
  `OptionsFlowSentimentSignal().compute_vectorized()` directly (never through
  `ml/forecast_backfill.py`) and confirms the momentum-proxy fallback still produces a real,
  non-neutral, correctly-signed score.
- `test_options_flow_sentiment_live_compute_scalar_fallback_still_fires_unchanged` — the same
  proof against the scalar `compute()` method, which is the ACTUAL method
  `_run_cross_sectional_module` dispatches to for this signal.
- `test_forecast_backfill_suppresses_options_flow_sentiment_momentum_proxy_on_backfill_path` — an
  offline, synthetic-panel, end-to-end run through steps 2-5 confirming: the signal still runs and
  gets a `_Signal` column, that column is entirely `NaN`, `self.data`'s shared `ROC_5`/`ROC_20`
  columns are byte-identical before and after step 3, no fabricated metrics row is produced, the
  eligibility reason starts with `insufficient_samples:`, and — as a regression check — the other
  three signal modules on the exact same synthetic panel (`timeseries_momentum`,
  `cross_sectional_momentum`, `rsi2_mean_reversion`) are unaffected and still train normally.

---

## API Endpoints & Web App UI

### REST API Endpoints (`api/pilots_api.py`)
- `GET /pilots/forecast_backfill`: Returns backfill status, trained 8-model metrics (Accuracy, ROC-AUC, sample counts), and metadata — always the last *completed* run (never in-progress/partial data).
- `POST /pilots/forecast_backfill/run`: Triggers an on-demand, asynchronous forecast backfill cycle (202 + job id; runs in an isolated subprocess).
- `GET /pilots/forecast_backfill/status/{job_id}`: Polled every 2s by the webapp. Includes `phase`/`step`/`state` and, once at least one (model, horizon) combo has finished training, `partial_summary: {"trained": [...], "metrics_so_far": {...}} | null` — survives into a `state: "timeout"` response so a killed run still reports what it managed to checkpoint.
- `POST /pilots/forecast_backfill/cancel/{job_id}`: Cancels an in-flight run.

### Web App (Pilots PWA)
- **Screen**: `<ForecastBackfillScreen />` (`webapp/src/screens/ForecastBackfillScreen.tsx`)
- **Route**: `/forecast/backfill`
- Displays pipeline status, trained model performance table, data sourcing badges (FMP), and on-demand trigger control.
- On a timed-out run with a non-empty `partial_summary`, renders a "Partial Results Saved Before Timeout" table listing the checkpointed (model, horizon) combos and their metrics, and the failure banner reads *"The backfill timed out after training N models — partial results were saved"* instead of an unconditional "Nothing was saved" (`webapp/src/forecastBackfillCopy.ts::backfillFailureMessage`).

---

## CLI Usage

Run a backfill cycle directly from the command line:

```bash
python scripts/run_forecast_backfill.py --use-fmp
python scripts/run_forecast_backfill.py --tickers AAPL,MSFT,NVDA,JPM --horizons 10,30,60,90
```

---

## Automated Tests

Run the unit test suite:

```bash
pytest tests/test_forecast_backfill.py tests/test_forecast_backfill_job.py -v
```

Coverage added for the performance/checkpointing work above:
- `test_step_3_skips_cross_sectional_modules_with_no_meta_label_features` — confirms only
  `cross_sectional_momentum` reaches the expensive per-date replay path.
- `test_cross_sectional_fast_path_matches_slow_path_parity` — proves `compute_batch_xsec`'s
  vectorized output is numerically identical (within this codebase's 1e-5 drift convention)
  to the original per-row loop's output.
- `test_cross_sectional_fast_path_is_lookahead_free` — the standard perturbation test,
  re-verified against the new fast path specifically.
- `test_kill_mid_step_5_leaves_partial_export_with_completed_combos` /
  `test_kill_before_any_combo_finishes_produces_no_partial_files` — SIGKILL a real child
  subprocess mid-run and inspect the filesystem, proving the partial CSV/JSON checkpoint
  survives a genuine hard kill (not just a clean early return), and that a kill before any
  combo finishes still honestly produces no partial files.
- `tests/test_forecast_backfill_job.py::TestDeadlineEnforcement` — extended to assert
  `partial_summary` survives into the serialized `"timeout"` state.
- Webapp: `webapp/src/forecastBackfillCopy.test.ts` (new — full branch coverage for
  `backfillFailureMessage`, including the singular/plural and honest-empty cases),
  `webapp/src/screens/ForecastBackfillScreen.test.tsx` (partial-results table
  shown/not-shown), `webapp/src/api/mock.test.ts` (end-to-end
  `stockpy.mock.forecast_backfill_timeout` marker + a shape-parity assertion against
  `ForecastBackfillModelMetrics`).
