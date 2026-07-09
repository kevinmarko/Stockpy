# Feature & Tier History (detailed)

This file holds the detailed, dated changelog of every Tier/Task/Scope feature shipped
since 2026-06, moved out of `CLAUDE.md` on 2026-07-05 to keep that file under the
context-budget char limit. `CLAUDE.md` still carries the **current, load-bearing**
architecture reference (flat module list, standing operator rules, domain split,
conventions); this file is the **archival "why does X exist and what shipped in the
PR that added it" record** — read it when you need the full backstory, test surface,
or Gravity-audit-step details for a specific subsystem named below.

Sections in this file (search for the `##` heading):
- Dead-code resolution — reuse grossMargins/currentRatio, delete orphans, activate ForecastTracker (opt-in) (2026-07-09)
- Advisory multifactor-Z threading — close the PR #192 follow-up (2026-07-09)
- Forecasting fit-once + GARCH reuse, 4 settings to GUI, advisory-snapshot telemetry parity (2026-07-09)
- Forecasting — GARCH volatility into Monte Carlo + Prophet into the ensemble (2026-07-08)
- Fundamentals — Finnhub → Yahoo statement-computed engine (2026-07-08)
- ML Package Architecture (Stage 4 — Triple Barrier + Meta-Labeling)
- Lookback & Vectorization Enhancements (Bug Fixes)
- GUI tab build-outs: Reports/Brinson-Fachler, Launcher, Market Data, Observability,
  Safety/Analytics/Control, Enhanced Observability & Error Handling, GUI Operational
  Efficiency/UX
- Tier 1 Decision Support (snapshot diff), Tier 1.2 (calibration), Tier 1.3 (decision
  journal), Tier 1.4 (watch alerts), Tier 1.5 (rationale verbosity)
- Tier 2.1 (regime weights), Tier 2.2 (forecast skill ensemble), Tier 2.4 (news
  catalyst signal), Tier 2.5 (correlation clusters)
- Task 3 — Operator Ergonomics (daily briefing, mobile report, key-rotation reminder,
  quick-add watchlist)
- Tier 4 — Validation & Honesty (recommendation tracking, walk-forward cadence)
- Tier 5.1/5.2/5.3 — ADVISORY_ONLY quarantine, RUNBOOK rewrite, kill-switch pause gate
- Tier 6 / 6.1 — Autonomous Advisory Agent, trade-signal abilities
- Tier 7 — Robinhood Realized-P&L Engine
- Tier 8 — Robinhood Execution Bridge
- Tier 9 / Scope 2/3/4 — Claude+Gemini commentary, AI Gravity Audit Runner, AI
  Insights tab (Gemini Vision), Opal Research Agent (OpenAI)
- AI Control Center tab
- Prompt Registry

**Critical invariants that must never regress are still summarized in `CLAUDE.md`
where they're load-bearing for every session** (e.g. ADVISORY_ONLY quarantine,
CONSTRAINT #3/#4/#6, no-fabricated-metrics, dead-letter resilience). This file adds
the full detail, test surface, and Gravity step numbers behind each of those.

---

## Dead-code resolution — reuse grossMargins/currentRatio, delete orphans, activate ForecastTracker (opt-in) (2026-07-09)

**Why.** A repo-wide dead-code sweep found three classes of cruft: (a) computed-but-unconsumed
fundamentals (`grossMargins`, `currentRatio` were emitted by `data/yahoo_fundamentals.py` but never
reached a factor or the advisory display); (b) genuinely orphaned code with no production caller
(`revenueGrowth` + its `_prior_annual` helper, six dead momentum ROC intermediates, the legacy
`reporting_engine.py` + `daily_report_template.html` report path, and two Google-Cloud-NLP macro
sentiment functions); and (c) fully-built-but-dark machinery (the Tier 2.2 `ForecastTracker`
inverse-RMSE skill-weighted blend, wired but never activated). Resolve each by reuse, deletion, or
opt-in activation — no silent behavior change on a fresh checkout.

**What shipped.**
- **REUSE — `grossMargins` → Quality factor.** `processing_engine.py::calculate_fundamental_metrics`
  now sets `quality_factor_score = mean(available among {returnOnEquity, operatingMargins,
  grossMargins})` (all fractions), falling back to `-debt_to_equity` only when none of the three is
  present. Was `roe + operating_margin` (a two-metric SUM requiring both). **Mean, not sum**, so a
  ticker with 1, 2, or 3 available metrics stays on one cross-sectional z-score scale; `Quality_Z`
  is identical to the old sum when every ticker carries the same two metrics. `grossMargins` (already
  emitted by `data/yahoo_fundamentals.py`, previously unconsumed) now feeds the multifactor `Quality_Z`.
  NaN discipline preserved (CONSTRAINT #4) — a missing input never fabricates a `0.0`.
- **REUSE — `currentRatio` → DTO + advisory display.** `dto_models.py::FundamentalDataDTO` gained a
  `current_ratio` field (`__init__` default `NaN`; `from_raw_dict` reads `info.get("currentRatio", NaN)`);
  `engine/advisory.py` surfaces it as `key_indicators["current_ratio"]`. `currentRatio` was already
  emitted by `yahoo_fundamentals` but never carried into the DTO — now the liquidity ratio rides
  through to the advisory `Recommendation`.
- **DELETE — `revenueGrowth`.** Removed from `data/yahoo_fundamentals.py` (+ its `_prior_annual`
  helper), the Finnhub `_METRIC_MAP` entry in `data/market_data.py`, and its tests. No DTO field,
  schema column, or factor consumed it (the emitted-fundamentals count drops 15 → 14).
- **DELETE — dead momentum ROC intermediates.** Removed `ROC_3M`, `ROC_1M`, `ROC_6M_skip`,
  `ROC_3M_skip`, `ROC_1M_skip`, `ROC_12M_skip` from `processing_engine.py::calculate_momentum_metrics`
  (kept `ROC_12M`/`ROC_6M`, which are the only ones consumed downstream — e.g. the StrategyEngine
  option-overlay trend filter's `ROC_12M > 0`).
- **DELETE — `reporting_engine.py` + `daily_report_template.html`.** Superseded by
  `diagnostics_and_visuals.generate_html_report` (the only report path, called by both `main.py` and
  `main_orchestrator.py`); the legacy pair was never wired into either entry point. `.github` and
  `docs/architecture.md` references were scrubbed alongside.
- **DELETE — macro sentiment `analyze_sentiment` + `fetch_and_compile_macro`; RETAIN
  `_fallback_sentiment`.** The Google-Cloud-NLP `analyze_sentiment` and the orphaned
  `fetch_and_compile_macro` had no production caller and no DTO sentiment field — sentiment is owned
  by `signals/news_catalyst.py` (FinBERT). **Critical nuance:** `_fallback_sentiment` was initially
  removed but RESTORED and is retained *solely* as the load-bearing reference for the BUG-1 regression
  guard (`tests/test_bug_fixes.py` + the Gravity BUG-1 audit assert `main_orchestrator` uses
  `calculate_sahm_rule`, NOT `_fallback_sentiment`, for the Sahm rule) — do not delete it thinking it
  is dead.
- **ACTIVATE (opt-in) — `ForecastTracker`.** New `settings.FORECAST_SKILL_WEIGHTING_ENABLED`
  (default **False**, mirroring the `FORECAST_USE_GARCH_SIGMA` opt-in convention); when `True`, a
  persistent `forecasting.forecast_tracker.ForecastTracker` (self-provisioning its `forecast_errors`
  table in `quant_platform.db`) is injected into every `ForecastingEngine` construction —
  `main_orchestrator.py` (both `EngineContext.build` and the `run_pipeline` fallback) and
  `engine/advisory.py` — so the multi-model blend weights ARIMA/Monte Carlo/Holt-Winters/CNN-LSTM by
  inverse recent RMSE instead of fixed fractions. Default-off ⇒ `tracker=None` ⇒ **byte-identical**
  static blend as today. `FORECAST_SKILL_WINDOW_DAYS` default raised **60 → 180** (it MUST exceed the
  90-day max horizon: a 'completed' h=90 row needs `forecast_ts ≤ now-85d` while the window only counts
  `forecast_ts ≥ now-WINDOW`; at 60 those bands are mutually exclusive so h=60/h=90 could never warm
  up). Both keys added to `gui/env_io.py` `ALLOWED_KEYS` (non-secret, GUI-writable).

**Test surface.**
- `tests/test_processing_engine.py` — `quality_factor_score` mean-of-3 (1/2/3-metric parity, none-present
  `-debt_to_equity` fallback, NaN discipline); dead-ROC-intermediate removal.
- `tests/test_yahoo_fundamentals.py` — `revenueGrowth` + `_prior_annual` removed from the emitted-key
  contract; the remaining 14 metrics' scale rules and NaN discipline unchanged.
- `tests/test_advisory.py` — `current_ratio` surfaced onto `Recommendation.key_indicators`.
- `tests/test_macro_engine.py` — `analyze_sentiment`/`fetch_and_compile_macro` gone; `_fallback_sentiment`
  retained; Sahm rule still sourced from `calculate_sahm_rule` (BUG-1 guard intact).
- **NEW `tests/test_forecast_skill_uplift.py`** — opt-in uplift backtest for the skill-weighted blend
  (slow-marked; the default-off path stays byte-identical and is covered by the existing forecasting tests).

---

## Advisory multifactor-Z threading — close the PR #192 follow-up (2026-07-09)

**Why.** PR #192 (below) unified the advisory-path `state_snapshot.json` writer with the
orchestrator's, but the five per-signal multifactor keys (`value_z`/`quality_z`/`lowvol_z`/
`size_z`/`multifactor_composite`) always serialized as JSON `null` on the advisory path — the
writer read them from `engine.advisory.Recommendation.key_indicators`, but `evaluate()` never put
them there in the first place. This closes that gap.

**What shipped.**
- `engine/advisory.py::evaluate()` already receives `context_extras` (the universe-wide dict
  `main._build_context_extras()` builds once per cycle via `global_registry.run_pre_compute()`,
  keyed `{"multifactor_scores": {ticker: {"Value_Z":..., "Quality_Z":..., "LowVol_Z":...,
  "Size_Z":..., "Multifactor_Composite":...}}}`) and already threads it into
  `StrategyEngine.evaluate_security()` for scoring — it just never copied the values onto the
  returned `Recommendation`. Added a lookup, `_mf_scores = (context_extras or {}).get
  ("multifactor_scores", {}).get(symbol, {})`, right before the `key_indicators` dict literal
  (Step 12), and five new entries in that dict — `value_z`/`quality_z`/`lowvol_z`/`size_z`/
  `multifactor_composite` — each `_safe_float(_mf_scores.get("Value_Z"), nan)`-style, so an
  absent `context_extras`, a failed pre-compute, or a symbol missing from this cycle's universe
  (e.g. microcap-excluded per `signals/multifactor.py`) all degrade to `NaN` — never a fabricated
  `0.0` (CONSTRAINT #4). Verified `context.multifactor_scores` never stores `None` as a per-ticker
  value (always a dict, NaN-filled when excluded) — see `signals/multifactor.py`'s `pre_compute()`
  — so the `.get(symbol, {})` chain never raises.
- No changes to `reporting/state_snapshot.py` — it already read the correct snake_case keys from
  `key_indicators`; they were just always absent. No changes to `main._build_context_extras()` —
  the universe-wide pre-compute was already correct and already threaded through for scoring.
- **Cost.** Zero — a dict lookup already-computed data was sitting next to; no new fetch, no new
  fit, no new network call.

**Test surface.**
- `tests/test_advisory.py::TestContextExtrasThreading` — two new tests:
  `test_multifactor_scores_populate_key_indicators` (a supplied `context_extras` entry for the
  symbol round-trips through `key_indicators` under the exact snake_case keys the snapshot writer
  reads) and `test_multifactor_scores_absent_degrade_to_nan` (both "context_extras omitted" and
  "context_extras present but no entry for this symbol" degrade every one of the five keys to
  `NaN`, never `0.0` or a missing key).
- `tests/test_advisory.py` full file: 31 passed.
- End-to-end spot check (evaluate() → write_state_snapshot()): a synthetic `context_extras` with
  `Value_Z=1.23` etc. flows through `Recommendation.key_indicators` into the written
  `state_snapshot.json`'s per-signal `value_z`/`multifactor_composite` fields with the real values,
  confirmed by direct inspection of the written JSON.

---

## Forecasting fit-once + GARCH reuse, 4 settings surfaced to GUI, advisory-snapshot telemetry parity (2026-07-09)

**Why.** A code-review efficiency + UI-connect pass over the 2026-07-08 forecasting/fundamentals
work, targeting three findings:
1. **Redundant statsmodels fits in the forecasting hot loop.** `generate_forecast` re-ran a full
   ARIMA fit and a full Holt-Winters grid search *per horizon* (and again for the target-days
   forecast), even though both fits are horizon-independent — the horizon only changes the
   `.forecast(h)` step. That is ~5 ARIMA + ~12-15 HW statsmodels optimizations per ticker per
   cycle where 1 + 3 suffice.
2. **GJR-GARCH fit twice per ticker per cycle.** `main_orchestrator.py` already fits GJR-GARCH once
   (~line 300, populating `dashboard_df['GARCH_Vol']`), then `generate_forecast` → `_estimate_daily_sigma`
   fit GJR-GARCH a SECOND time on the same DataFrame to source the Monte Carlo σ.
3. **Four forecasting/data tunables were `.env`-only**, invisible to the GUI operator; and the
   **advisory** state-snapshot writer blanked telemetry (macro recession indicators, per-signal
   GARCH/multifactor-Z) that the `main_orchestrator.py` path already surfaced, so switching to the
   advisory orchestrator silently emptied the GUI Observability / Report-Viewer tabs.

**What shipped.**
- **Forecasting fit-once refactor (`forecasting_engine.py`).** `run_arima`/`run_holt_winters_grid_search`
  are split into fit-once + forecast helpers: `run_arima_fit`/`forecast_from_arima_fit` and
  `run_holt_winters_fit`/`forecast_from_hw_fit`. `generate_forecast` fits ARIMA and Holt-Winters
  exactly ONCE per ticker (guarded by the same `> 30` row condition as before) and reuses each
  fitted model across the target-days forecast AND every horizon in `[10,30,60,90]` — collapsing
  ~5 ARIMA + ~12-15 HW fits/ticker down to **1 + 3**. Output is **byte-identical** to the
  pre-refactor per-horizon path. The old `run_arima`/`run_holt_winters_grid_search` names are
  **retained as back-compat shims** (fit-once + forecast-once internally) so external callers are
  unaffected. CNN-LSTM (direct multi-step, trained once) and Prophet (h=30, run once) were already
  single-fit and are untouched.
- **GARCH double-fit elimination (`forecasting_engine.py` + `main_orchestrator.py`).**
  `generate_forecast` gained `precomputed_garch_annual_vol: Optional[float] = None`;
  `_estimate_daily_sigma` uses `precomputed_garch_annual_vol / sqrt(252)` (same SCALE RULE — GARCH
  returns ANNUALIZED, MC needs DAILY) when supplied, skipping the second internal GJR-GARCH fit;
  else its behavior is unchanged. `main_orchestrator.py` (~line 410) passes the `dashboard_df['GARCH_Vol']`
  it already computed (~line 300) so that ticker isn't GARCH-fit twice per cycle. `main.py` and
  `engine.advisory` callers pass nothing (default `None` → unchanged internal fit path).
- **Four settings surfaced to the GUI (`gui/env_io.py` + `gui/panels/settings_manager.py`).**
  Added to `ALLOWED_KEYS` (non-secret tunables) and `_SETTINGS_LAYOUT`: `FORECAST_USE_GARCH_SIGMA`
  (bool — GARCH→MC σ rollback lever), `FORECAST_PROPHET_WEIGHT` (float [0,1] — Prophet overlay
  weight), `FUNDAMENTALS_SOURCE` (`"yahoo"` | `"yfinance_info"`), `BETA_LOOKBACK_DAYS` (int). All
  write through the existing allowlist-bounded `env_io` path; no credential is ever added.
- **Advisory state-snapshot telemetry parity (`reporting/state_snapshot.py`).** `write_state_snapshot`
  (the advisory `main.py` path) now emits top-level `sahm_rule`/`high_yield_oas`/`yield_curve`/
  `hmm_risk_on_probability` (from the injected `macro_dto`) plus per-signal `garch_vol`/`hmm_risk_on`
  and the five multifactor keys (`value_z`/`quality_z`/`lowvol_z`/`size_z`/`multifactor_composite`) —
  matching what the `main_orchestrator.py` `_write_state_snapshot()` path already surfaced.
  `garch_vol` reads from `engine.advisory` `Recommendation.key_indicators`; at ship time the five
  multifactor keys emitted JSON `null` (no fabricated values — CONSTRAINT #4) because
  `Recommendation.key_indicators` didn't carry them yet — closed the same day, see "Advisory
  multifactor-Z threading" above.
- **Cost.** Net efficiency win: ARIMA/HW fits drop ~4-5× per ticker, and one GJR-GARCH fit per
  ticker per cycle is eliminated on the orchestrator path. No new dependencies.

**Test surface.**
- `tests/test_forecasting_engine.py` — fit-once shim-equality (back-compat `run_arima`/
  `run_holt_winters_grid_search` produce identical output to the split helpers), fit-count
  assertions (ARIMA/HW fit invoked once per `generate_forecast`), and the `precomputed_garch_annual_vol`
  reuse path (supplying a precomputed annual vol skips the internal GARCH fit and yields a sane
  forecast dict).
- `tests/test_gui_env_io_forecast_keys.py` — the four new keys are in `ALLOWED_KEYS`, are
  writable/round-trip through `env_io`, and are non-secret.
- `tests/test_state_snapshot_advisory.py` — the advisory writer emits the macro recession telemetry
  + per-signal `garch_vol`/multifactor-Z keys (writer-level contract; at ship time the multifactor
  values were `null` in practice since `Recommendation.key_indicators` didn't carry them yet — see
  "Advisory multifactor-Z threading" above for the same-day fix).
- `tests/test_forecasting_lookahead.py` — re-run to confirm the fit-once refactor preserves the
  train-only scaler / no-lookahead invariants.
- Full run: 72 passed across the four files on the Python 3.11 sandbox (CI on 3.12 authoritative).

---

## Forecasting — GARCH volatility into Monte Carlo + Prophet into the ensemble (2026-07-08)

**Why.** Two long-standing weaknesses in `forecasting_engine.py`:
1. The Monte Carlo GBM simulation used a **naive historical log-return stdev** for σ. That
   backward-looking, thin-tailed estimate makes the MC confidence band (`MC_Lower`/`MC_Upper`,
   the 5th/95th terminal-price percentiles) roughly constant across volatility regimes — it
   does not widen when the market is turbulent nor tighten when it is calm, so the band
   understates tail risk exactly when it matters most. The platform already computes a
   forward-looking, fat-tailed **GJR-GARCH(1,1)** volatility elsewhere
   (`technical_options_engine.estimate_gjr_garch_volatility`, Student-t innovations); the MC
   simulation should consume it.
2. **Prophet was dead weight.** `run_prophet_forecast` was already being *invoked* at h=30 and
   its result surfaced into the `Forecast_30_Prophet*` report columns — but the yhat was
   **discarded from the actual `Forecast_30` blend**, so it influenced nothing the strategy
   layer consumes. It cost a fit and produced no ensemble value.

**What shipped.**
- **GARCH → Monte Carlo σ.** New helper `ForecastingEngine._estimate_daily_sigma(history_df,
  fallback_daily_sigma) -> float`. When `settings.FORECAST_USE_GARCH_SIGMA` is `True` (default)
  and `history_df` has ≥ 22 rows, it calls `TechnicalOptionsEngine().estimate_gjr_garch_volatility(history_df)`
  and returns a **DAILY** sigma for the GBM. `generate_forecast` computes `mc_sigma` once and
  threads it into **every** `run_monte_carlo` call site (the target-days band AND each per-horizon
  point forecast), so the whole MC surface is regime-responsive.
- **THE SCALE RULE (documented so nobody "fixes" it).** `estimate_gjr_garch_volatility` returns
  an **ANNUALIZED** vol; Monte Carlo's GBM needs a **DAILY** σ. `_estimate_daily_sigma` therefore
  divides by **`sqrt(252)`**. This conversion is *mandatory and cannot be delegated to
  `run_monte_carlo`*: that function has an auto-normalize guard, but it keys on **`mu` only**
  (`if abs(mu) > 0.05: mu/=252; sigma/=sqrt(252)`) — it inspects the drift, not σ, so a stealth
  annualized σ (e.g. 0.40 ≈ 40% annual) would sail through undivided and inflate the band ~16×.
  Guarded: a non-finite or non-positive daily σ falls back to the historical stdev; a floor of
  `1e-6` prevents a degenerate zero.
- **Degradation (never raises).** Flag off → fallback; `history_df is None` or `< 22` rows →
  fallback; estimator raises → logged at DEBUG + fallback. Pre-GARCH behavior is restored exactly
  by `FORECAST_USE_GARCH_SIGMA=false`.
- **Prophet → ensemble overlay.** Prophet still runs **at most once per call, at h=30 only** (it
  is expensive). Its yhat now enters `model_forecasts["prophet"]` for the h=30 horizon and is
  folded into `_blend_with_skill`'s **static branch** as a backward-compatible overlay applied
  *after* the existing base blend is computed: `final = base*(1-w) + prophet*w`, with
  `w = settings.FORECAST_PROPHET_WEIGHT` (default 0.25, clamped to [0,1]). **Scope is deliberately
  narrow:** only the h=30 static-blend path changes; the skill-weighted branch, all other
  horizons, and the `Forecast_30_Prophet*` report columns are untouched. **Prophet-absent behavior
  is byte-identical** to before — when `prophet_price` is missing/≤ 0 the overlay is skipped and
  `base` is returned unchanged.
- **Two new settings** (both `.env`-tunable, in `settings.py`): `FORECAST_USE_GARCH_SIGMA`
  (bool, default `True`) and `FORECAST_PROPHET_WEIGHT` (float, default `0.25`).
- **Cost.** One extra GJR-GARCH fit per ticker per cycle (Prophet already ran once); the GARCH
  fit is a small, bounded arch-library optimization and adds only a few ms per symbol.

**Test surface.**
- `tests/` (owned by the test agent) cover: the `/sqrt(252)` scale conversion (a df with a
  known/patched GARCH annualized vol yields `daily ≈ annual/sqrt(252)` at the MC call site, NOT
  the raw annual value); regime-responsiveness (a turbulent synthetic history yields a wider
  `MC_Upper - MC_Lower` band than a calm one under real GARCH); the Prophet overlay shift
  (`PROPHET_AVAILABLE` patched `True` + a `run_prophet_forecast` returning a far-above yhat moves
  `Forecast_30` toward that yhat vs a baseline with `FORECAST_PROPHET_WEIGHT=0`); and the
  degradation paths (flag off, `None`/short `history_df`, estimator raising → historical fallback).

**Gravity audit.** `run_forecast_skill_audit` (Step 59) gained two checks: **(11)**
`_estimate_daily_sigma` applies the `/sqrt(252)` annualized→daily conversion (patched GARCH
annual → returned daily ≈ annual/sqrt(252)), and **(12)** `_blend_with_skill` is prophet-absent
byte-identical while prophet-present applies the `base*(1-w)+prophet*w` overlay.

---

## Fundamentals — Finnhub → Yahoo statement-computed engine (2026-07-08)

**Why.** Fundamentals had been sourced from Finnhub's `company_basic_financials`
endpoint, wired as the primary path in `CompositeProvider` with a raw yfinance `.info`
fallback. Finnhub is a paid-tier API whose free plan (60 calls/min) was chronically
rate-limited (429) across a large watchlist sync — an entire 2026-06 mitigation layer
(sliding-window rate limiter + 6 h positive/negative fundamentals cache + one-shot 429
backoff) existed only to paper over that flakiness, and coverage gaps still forced
degraded metrics. The goal: drop the paid/flaky dependency for fundamentals entirely and
compute the same metric set from Yahoo Finance's FREE financial statements, keeping the
downstream `.info`-style key contract byte-for-byte so no consumer changes.

**What shipped.**
- **NEW `data/yahoo_fundamentals.py`** — a pure, I/O-free `compute_fundamentals(...)` that
  derives **14 equity fundamentals** from statement frames the caller has already fetched
  via yfinance (`income_stmt` + quarterly, `balance_sheet`, `cashflow` + quarterly,
  `dividends`, `institutional_holders`) plus `price`/`shares`. It never imports yfinance,
  never touches the network, never reads a file — so the math core is fully offline-testable
  and deterministic. Returns a `dict` keyed by yfinance `.info` names (`trailingEps`,
  `trailingPE`, `bookValue`, `priceToBook`, `dividendYield`, `payoutRatio`, `marketCap`,
  `beta`, `returnOnEquity`, `debtToEquity`, `grossMargins`,
  `operatingMargins`, `currentRatio`, `heldPercentInstitutions`, + `currentPrice`/
  `shortName`/`sector` straight-through) so `FundamentalDataDTO.from_raw_dict()` is
  unchanged. **NaN-degrading (CONSTRAINT #4)**: every metric is computed in its own
  try/except and independently degrades to `float("nan")` on a missing/bad input — a
  missing statement row never fabricates a `0.0` and never nukes the other 13 metrics.
  **Version-drift tolerance**: module-level alias tables (`EQUITY`, `NET_INCOME`,
  `TOTAL_REVENUE`, `TOTAL_DEBT`, …) resolved by `_row_latest` / `_ttm` / `_match_row`
  case-insensitively, so a yfinance statement-label rename is a one-line data edit here.
- **Formula notes.** TTM flows (`_ttm`) sum the trailing 4 quarterly columns, falling back
  to the latest annual column; `trailingPE`/`priceToBook` are NaN when EPS/bookValue ≤ 0
  (mirrors Yahoo); `currentRatio` = current assets / current liabilities;
  `heldPercentInstitutions` sums the
  top-N institutional `% Out` column (auto-detecting percent-vs-fraction).
- **Two SCALE-CRITICAL emission rules** (documented so nobody "fixes" them): `dividendYield`
  is emitted **as a FRACTION** (e.g. `0.0257`, not `2.57`) and is NOT routed through
  `normalize_yfinance_dividend_yield` — the platform consumes it as-is; `debtToEquity` is
  emitted **×100** (e.g. `150.0`, not `1.5`) because two downstream consumers divide by 100.
  `payoutRatio` takes `abs()` of the (negative) "Cash Dividends Paid" cash-flow outflow over
  TTM net income, so the sign is always positive.
- **Beta** = `Cov(stock, SPY) / Var(SPY)` over the trailing `BETA_LOOKBACK_DAYS` (new
  setting, default **504** ≈ 2 years) of daily returns, requiring ≥ 60 overlapping
  observations; the SPY return series is cached inside the provider to avoid refetching per
  symbol.
- **`data/market_data.py` rewire** — NEW `YahooFundamentalsProvider` (`SOURCE="yahoo_computed"`)
  is now the **PRIMARY** fundamentals source in `CompositeProvider`; raw yfinance `.info`
  (`YFinanceProvider`) is the **emergency fallback** (used when the primary yields an empty
  dict). `FinnhubProvider` still exists but is **UNWIRED from fundamentals** (deprecated as a
  fundamentals source; its rate-limiter/cache machinery is retained but dormant on that path).
  `CompositeProvider` gained a **`source_name`** property reporting the active fundamentals
  backend. NEW setting **`FUNDAMENTALS_SOURCE`** (`"yahoo"` default | `"yfinance_info"`).
- **`FINNHUB_API_KEY` is NO LONGER a fundamentals dependency.** It remains ONLY for
  `signals/news_catalyst.py` (company news / earnings headlines). `finnhub-python` stays in
  `requirements.txt` for that signal.

**Test surface.**
- **NEW `tests/test_yahoo_fundamentals.py`** — 35 fully offline unit tests: `TestScaleRules`
  (the two scale rules), `TestValuationMath` (bookValue/priceToBook/trailingPE/ROE/marketCap),
  `TestPayoutSign` (`payoutRatio` `abs()` sign), `TestBeta`, `TestNaNDiscipline` (NaN-not-zero
  discipline), `TestAliasResolver` (`_row_latest`/`_ttm` label-drift tolerance), `TestContract`
  (emitted-key set + `.info`-style names).
- **`tests/test_market_data.py`** composite fundamentals-routing tests rewired to the
  Yahoo-primary path: NEW `TestYahooFundamentalsProvider`; `TestCompositeProviderCache` now
  asserts **Yahoo-computed → yfinance `.info` fallback when primary is empty**, and primary
  used when non-empty.

**Gravity audit.** `run_market_data_provider_audit` (Step 26) gained checks **(p)/(q)/(r)**:
`source_name == "yahoo_computed"`, the two scale rules (dividendYield fraction, debtToEquity
×100), and empty-safe degradation.

## ML Package Architecture (Stage 4 — Triple Barrier + Meta-Labeling)

**qlib-style three-layer architecture (ml/ package)**:
- `ml/data/` — PIT feature store (PITFeatureStore, Parquet cache), label construction (`build_meta_label_target`, `build_meta_features`). Re-exports `build_pit_feature_matrix` / `build_forward_return_ranks` from `ml/feature_engineering.py`.
- `ml/models/` — `Model` ABC (`fit/predict/save/load`) that ALL ML models must implement. `LGBMCrossSectionalRanker` and `MetaLabeler` both inherit from it.
- `ml/strategies/` — `StrategySpec` links a Model to a SignalModule by `signal_id`. Used by Gravity audits.
- `ml/registry.yaml` — Human-readable model registry with `cpcv_dsr`, `pbo`, `deployable` fields. Parse with PyYAML.

**Triple-barrier no-lookahead invariants**:
- `get_volatility(close, span)` uses `ewm(adjust=False)` (causal) — vol at t uses only returns ≤ t.
- `apply_triple_barrier` pre-computes vol on the FULL series then indexes at event time — this is correct (vol[t] IS the prefix vol because ewm is causal). The perturbation test in `test_triple_barrier_lookahead.py` proves this empirically.
- `cusum_filter` is inherently sequential (scalar loop over dates, not iterrows). This is intentional and correct.

**Meta-labeling hard gate**:
- `MetaLabelerRegistry.global_meta_registry` is a module-level singleton in `ml/meta_labeling.py`.
- `SignalAggregator.aggregate()` imports it lazily via `_get_meta_registry()` to avoid circular imports.
- When a MetaLabeler is registered for a signal AND `predict_proba_scalar` returns P < `settings.META_LABEL_MIN_CONFIDENCE` (0.4), `meta_label_composite` is set to EXACTLY 0.0 (not near-zero via log-space — a hard flag `meta_hard_gate` ensures this).
- Hard gate affects position sizing ONLY (Kelly Target × composite = 0), not the signal score/recommendation (BUY/HOLD/etc.).
- Default state (empty registry) is identical to pre-Stage-4 behavior: composite = 1.0.

**PyYAML** added to `requirements.txt` (needed for `ml/registry.yaml` round-trip tests).

## Lookback & Vectorization Enhancements (Bug Fixes)
- **Lookback pricing history**: Fetch lookback changed from `"1y"` to `"2y"` (~504 trading days) in `data_engine.py` and `data_ingestion.py` to ensure all cross-sectional and momentum engines have sufficient history (requires at least 275 trading days). In `data/market_data.py`, yfinance lookback threshold mapping is adjusted so that `lookback_days <= 500` maps to `"2y"`.
- **DataFrame vectorization**: All mutations in `main_orchestrator.py` and `evaluation_engine.py` that occurred inside `.iterrows()` loops have been refactored to use dictionary collection and vectorized `.map()` operations, satisfying Constraint #3.
- **Gravity AI Review Suite extensions**:
  - **Step 1 dynamic schema validation**: covered dynamic `DashboardSchema` validation in `run_schema_audit()`.
  - **Step 8 multi-indicator perturbation**: added lookahead perturbation check for all technical indicators (`RSI`, `RSI_2`, `MACD`, `ATR`, `Aroon`, `Coppock`, `Chandelier_Exit`) in `run_lookahead_audit()`, verifying the actual `ProcessingEngine` calculations.
  - **Step 35 portfolio heat limit audit**: added `run_risk_gates_portfolio_heat_audit()` verifying that `PreTradeRiskGate` with 6% limit blocks BUY orders and allows SELL orders in mock mode.
  - **Step 37 six-bug regression audit** (`run_six_bug_regression_audit()`): enforces the six production bugs found in the 2026-06 bug-hunt session cannot regress. Checks: (1) `_fallback_sentiment("")` is an NLP scorer not a Sahm proxy; (2) `calculate_sahm_rule()` is called in `run_pipeline`; (3) `sahm_rule_indicator=` keyword wired to `MacroEconomicDTO` in `main_orchestrator.py`; (4) `MacroEconomicDTO.killSwitch` fires at `sahm_rule_indicator >= 0.5`; (5) Gordon Growth Model uses the same capped g in both numerator and denominator; (6) `calculate_momentum_metrics` returns NaN (not 0.0) for `ROC_12M`/`Realized_Vol_60D` when `len(df) < 253`; (7) `evaluate_portfolio`'s `benchmark_df` default is `None`; (8) fallback forecast path in `main_orchestrator` uses `run_monte_carlo`, not `price*(1+mu*N)`.
- **Six-bug session invariants** (2026-06 bug-hunt — must never regress):
  - `main_orchestrator.run_pipeline` MUST call `me.calculate_sahm_rule()` (not `me._fallback_sentiment("")`) and forward the result as `sahm_rule_indicator=sahm_val` to `MacroEconomicDTO`. Violating either half silently disables the Sahm Rule recession kill-switch.
  - `calculate_gordon_fair_value()` MUST cap `g` before computing `D1 = D0 * (1 + g_capped)`. Both numerator and denominator must use the same capped rate.
  - `calculate_momentum_metrics()` MUST return `float('nan')` for all ROC/vol columns in the `len(df) < 253` early-return path. `0.0` is fabricated data (Constraint #4).
  - `evaluate_portfolio()`'s `benchmark_df` parameter MUST default to `None`; create a fresh `pd.DataFrame()` inside the function body.
  - The fallback-forecast exception path in `run_pipeline` MUST call `fe.run_monte_carlo(price, mu, sigma, N)` for every forecast horizon — not `price * (1 + mu * N)`.
- **Zero-position-size crash fix (2026-06-26 — must never regress):** Production CRITICAL crash `"Platform execution pipeline crashed: float division by zero"` was caused by `evaluate_portfolio`'s Brinson-Fachler block computing `df.groupby('sector')['position_size'].sum() / df['position_size'].sum()` when every ticker is a watchlist-only ticker (zero shares → `Shares × Price = 0` → `position_size.sum() == 0`). Three invariants enforced:
  1. `EvaluationEngine.evaluate_portfolio` MUST guard `total_position_size = df['position_size'].sum()` and skip the BF division (default `BF_Allocation`/`BF_Selection` to `0.0`) when `total_position_size <= 0`. Never divide by a zero position total.
  2. `main_orchestrator.run_pipeline` MUST replace zero `position_size` values (after `Shares × Price`) with the `$10 000` notional default via `zero_mask = position_size <= 0.0; dashboard_df.loc[zero_mask, 'position_size'] = 10000.0`.
  3. The pipeline crash handler in `_main_body` MUST use `telemetry.critical(..., exc_info=True)` so the full traceback appears in `logs/investyo.log` for future diagnosis. Covered by `tests/test_evaluate_portfolio_zero_positions.py` (5 tests) and Gravity Step 45.




## Reports tab — Brinson-Fachler Attribution Analysis (2026-06 UI task)
- `gui/panels.py` now exposes a full **Brinson-Fachler Attribution Analysis** section inside `render_report_viewer` (replacing the prior placeholder expander). Five pure helpers (kept module-level so they are testable without Streamlit) form the API boundary between the UI and `EvaluationEngine.calculate_brinson_fachler`:
  - `default_brinson_fachler_frame() -> pd.DataFrame` — seeds the `st.data_editor` with the canonical GICS-11 sector list (`GICS_SECTORS` constant) and the five-column header `(Sector, Portfolio Weight (%), Portfolio Return (%), Benchmark Weight (%), Benchmark Return (%))`.
  - `parse_pasted_sector_matrix(text: str) -> pd.DataFrame` — accepts TSV or CSV pasted from a spreadsheet (delimiter auto-detected from the first line) and supports BOTH header-bearing and header-less 5-column blocks. The header-less branch is detected by sniffing whether columns 2–5 of the first row parse as floats — without this guard, `pd.read_csv` would promote a real data row to the header and silently drop a sector.
  - `build_brinson_fachler_inputs(editor_df) -> (portfolio_df, benchmark_df)` — splits the editor frame into the two-DataFrame shape `EvaluationEngine._calculate_brinson_fachler_compat` consumes. **Unit-consistency invariant (must never regress):** the editor stores percents but the engine multiplies `weight × return` directly, so this helper divides every numeric column by 100 to convert percent → fraction. A regression here would not crash anything — the result dict would just be off by a factor of 100.
  - `validate_brinson_fachler_weights(editor_df, *, tolerance_pct=1.0) -> list[str]` — pre-flight checker called on every render: warns if portfolio or benchmark weights deviate from 100 % by more than 1 % or if any weight is negative (long-only attribution convention).
  - `compute_brinson_fachler(editor_df) -> dict` — orchestrates the above and returns the engine's canonical 8-key result dict (`Portfolio Return`, `Benchmark Return`, `Active Return`, `Allocation Effect`, `Selection Effect`, `Interaction Effect`, `Attribution Sum`, `Sector Details`). The `Sector Details` map of dicts uses the engine's documented per-row schema (`weight_p`, `weight_b`, `return_p`, `return_b`, `allocation_effect`, `selection_effect`, `interaction_effect`, `total_attribution`).
- The rendered UI persists editor + result state under `st.session_state["bf_editor_df"]` and `st.session_state["bf_result"]` so swapping Command Center tabs doesn't lose work. Per-sector breakdown and editor-input CSV downloads are both wired (`bf_download_sector`, `bf_download_input`).
- **Engine path:** `EvaluationEngine.calculate_brinson_fachler` dispatches DataFrame inputs to `_calculate_brinson_fachler_compat` (already present, unchanged). The UI ALWAYS goes through the DataFrame-compat path so the engine's name-mapping branch is exercised deterministically.
- **Test surface:** `tests/test_brinson_fachler_ui.py` (11 tests) pins the default frame shape, all paste-parser branches (TSV/CSV, header/header-less, percent-sign stripping, malformed input), the percent→fraction unit conversion, the validation warnings, and the attribution-sum ≈ active-return identity end-to-end through the engine.
- **Gravity:** `Gravity AI Review Suite.py` step `step_40_brinson_fachler_attribution_audit` verifies the same five invariants in the production code path so a refactor that breaks the wiring fails the audit.

## Launcher tab — dual entry points + telemetry feedback (2026-06 UI task)
- `gui/panels.py::render_launcher` now exposes **two launch paths** as distinct buttons, both spawned via `gui/orchestrator_runner.py`:
  - **▶️ Launch Pipeline** — `orchestrator_runner.launch_orchestrator(dry_run, refresh_account)` → `python main_orchestrator.py` (async, full pipeline, broker, HTML report).
  - **🔄 Refresh Data (Advisory)** — `orchestrator_runner.launch_advisory_main(refresh_account)` → `python main.py` (synchronous advisory loop). This is the project's canonical `.env`-loading entry point (the `load_dotenv()` call lives inside `main.main()`); using it from the GUI gives a fast, broker-free refresh that still hydrates `output/state_snapshot.json` for every observability panel.
- **`RunHandle`** now carries a `mode: str` field (`"orchestrator"` | `"advisory"`) and an explicit `log_path: Path` so callers know which stream to tail. `compute_stage_status()` follows `handle.log_path` rather than the hard-coded `RUN_LOG_PATH`, so the orchestrator stage markers don't false-positive on advisory output.
- **Two log files:** `output/gui_run.log` (orchestrator) and `output/gui_advisory.log` (advisory). Kept distinct so a stage-marker scan on one log never sees the other's text.
- **Pre-launch env readiness check:** `orchestrator_runner.validate_required_env(required=REQUIRED_ENV_VARS) -> dict[str, bool]` is called on every render. Missing variables are surfaced as an inline `st.error` BEFORE the buttons are clicked — eliminating the "Refresh Data does not produce observable results" failure mode where the subprocess silently degraded to neutral defaults. Default `REQUIRED_ENV_VARS = ("FRED_API_KEY",)` — only the minimum needed for non-trivial output; optional integrations (Robinhood, alerts, broker) are not required.
- **Telemetry tail:** the new helper `orchestrator_runner.read_telemetry_tail(max_lines=120)` reads `logs/investyo.log` (written by `alerting.setup_logging()`, rotated at 10 MB × 5 backups) and is rendered as a separate expander under the active run log. This is the entry-point-agnostic structured-logging stream — surfacing it gives the operator a single window into platform-wide diagnostics whether the orchestrator or main.py was launched.
- **Auto-refresh ticker:** opt-in checkbox (`launcher_auto_refresh`); while a run is active it sleeps 5 s then calls `st.rerun()` so the log tail keeps scrolling without manual clicks.
- **Status display:** finished-run banner now distinguishes exit code 0 (✅ green) from any non-zero code (❌ red) and labels the run with the mode (`Orchestrator` / `Advisory`).
- **Test surface:** `tests/test_orchestrator_runner.py` (12 tests) covers the env-validation truth table (missing / present / whitespace), the log-routing contract (`read_log_tail(handle=...)` follows `handle.log_path`), the telemetry-tail idle hint, and that `launch_advisory_main` / `launch_orchestrator` emit handles tagged with the correct `mode` and pointing at distinct log files. Subprocess is monkeypatched so no real child is spawned.
- **Gravity:** `step_41_launcher_telemetry_audit` pins the same wiring in the production code path (validate_required_env truth table, `launch_advisory_main` mode tag, distinct log paths, telemetry idle hint).

## Market Data tab — diagnostics, throttling, connectivity health (2026-06 UI task)
- **gui/market_data_diagnostics.py** — operator-facing helpers for the Market Data Provider tab. Keeps the UI decoupled from provider internals and unit-testable headlessly (no Streamlit imports). Four public surfaces:
  - `classify_market_error(exc) -> ErrorCategory` — five-way classification (`RATE_LIMIT` / `NOT_FOUND` / `NETWORK_TIMEOUT` / `MALFORMED` / `UNKNOWN`). Walks `__cause__`/`__context__` chains and inspects `getattr(exc, "status_code", None)` so wrapped exceptions (e.g. `MarketDataError` over `TimeoutError`; `FinnhubAPIException` with `status_code=429`) still resolve to the specific category instead of falling through to `UNKNOWN`. `category_label(cat)` returns the operator-facing string (`"API Rate Limited"`, `"Symbol Not Found"`, etc.).
  - `validate_quote(quote) -> QuoteValidation` — flags malformed quotes BEFORE they enter the rest of the pipeline (CONSTRAINT #4): NaN/non-positive price, missing timestamp, or inverted bid/ask. **Asymmetric tolerance:** a missing-only-bid OR missing-only-ask is NOT flagged (some providers legitimately omit one side outside RTH); a missing PRICE always flags. `QuoteValidation.label` is the GUI string (`"OK"` or `"⚠ <issues>"`).
  - `FetchHealthTracker` — sliding-window success/failure ledger feeding the connectivity badge. Default `window=20`, `healthy_threshold=0.9`, `degraded_threshold=0.5`. Three-tier `HealthStatus`: HEALTHY / DEGRADED / DOWN. Empty state is HEALTHY (neutral) so the first-paint badge isn't a red scare. Persisted across Streamlit reruns under `st.session_state["md_health_tracker"]`; reset button is exposed in the panel.
  - `BatchQuoteFetcher(fetch_fn, spacing_seconds=0.1, health_tracker=None, sleep_fn=time.sleep)` — generator-based throttled fetcher. `iter_fetch(symbols)` yields one `BatchResult(index, symbol, quote, validation, error, category)` per symbol so the Streamlit panel can stream progress-bar updates. Spacing is enforced via a rolling `_last_call_ts`, so back-to-back batches share one monotonic clock. The `sleep_fn` is pluggable for tests. Default 100 ms spacing (10 calls/s) is safely under yfinance's known rate-limit threshold and trivially within Alpaca's 200 calls/min ceiling. `summarise_categories(results) -> dict[str, int]` rolls the result list up into the `{"ok": …, "rate_limit": …, …}` toast/caption summary.
- **gui/panels.py::render_market_data** — rewritten on top of those helpers. Surface changes:
  - Four KPI columns: **Provider**, **Mode** (real-time vs. delayed), **Quote TTL**, **Connection** (the new health badge).
  - A persistent **yfinance-delayed info banner** when the active provider is not Alpaca, citing the env-var swap needed to upgrade.
  - **Throttle slider** (0–1000 ms, default 100 ms) so the operator can dial spacing per-batch.
  - **Streaming progress bar** (`i/N — SYMBOL`) driven by the `BatchQuoteFetcher` generator.
  - Per-symbol **error category** rendered in a new `Error` column (e.g. `"API Rate Limited: 429 …"`), never a bare `None`.
  - Per-symbol **validation Status** column (`"OK"` or `"⚠ price missing/NaN; bid > ask"`) so malformed quotes are visible at a glance and never silently feed the quant pipeline.
  - Two reset buttons: **♻ Reset provider singleton** (existing) and **🩺 Reset connection health** (new — clears the sliding window).
  - Last-batch results live in `st.session_state["md_last_results"]` so the table survives tab switches.
- **Test surface:** `tests/test_market_data_diagnostics.py` (33 tests). Class coverage: `TestClassifyMarketError` (12 parametrised exception-message cases + `status_code` attribute + chained `__cause__` walk + label round-trip), `TestValidateQuote` (happy path + NaN/zero price + inverted bid-ask + missing-one-side OK), `TestFetchHealthTracker` (empty=HEALTHY-neutral, all-success=HEALTHY, mixed=DEGRADED, all-fail=DOWN, window roll-off, invalid-threshold rejection), `TestBatchQuoteFetcher` (one-result-per-symbol, error classification + health update, success tracking, throttle spacing observed via injected `sleep_fn`, invalid-spacing rejected, malformed quote flagged not-ok, `summarise_categories`).
- **Gravity:** `step_42_market_data_diagnostics_audit` pins the four-surface contract (error classification matrix, validate_quote invariants, sliding-window thresholds, BatchQuoteFetcher throttle + classification) so a refactor that breaks any of them fails the audit.

## Observability tab — System Telemetry + Latency Heatmap + Error Log (2026-06 UI task)
- **gui/observability_telemetry.py** — headless helpers backing three new sections of `render_observability` in `gui/panels.py`. No Streamlit imports so the module is unit-testable cold. Three public surfaces:
  - **System telemetry** — `collect_system_telemetry(disk_path='/') -> SystemTelemetry` samples CPU%, logical-core count, 1-min load avg, memory% + bytes, disk% + bytes for `disk_path`, plus the current Python process's RSS / CPU% / thread count. Frozen `SystemTelemetry` dataclass; psutil unavailability is reported via `psutil_available=False` + NaN floats + `-1` byte counts (CONSTRAINT #4 — never zero-fabricated). Sampling failure is caught and degraded the same way. CPU% is sampled with `interval=None` (delta since last call) so it never blocks the Streamlit reactivity loop — first paint is therefore meaningless and the panel auto-refreshes. `format_bytes(n)` returns the human-readable B/KiB/MiB/GiB/TiB string (`"—"` for `n<0`).
  - **Latency heatmap** — `LatencySampleStore(max_samples=500)` is a bounded ring buffer of `LatencySample(symbol, source, quote_timestamp, ingested_at, latency_seconds, is_stale)`. `record(symbol, source, quote_timestamp, ingested_at=None, is_stale=False)` computes `(ingested - quote_ts).total_seconds()` (both promoted to UTC if naive); negative samples are preserved (forensic value) and clamped to 0 only at render time. **Cross-tab wiring (must not regress):** the store lives in `st.session_state["obs_latency_store"]` and is populated by `render_market_data` on every successful fetch, so one click of "Fetch quotes" on the Market Data tab feeds the heatmap rendered on the Observability tab. `summarise_latency(samples) -> {count, p50, p95, worst_symbol, worst_p95}` powers the KPI strip; empty input returns NaN-shaped output. Heatmap rendering uses pandas `Styler.background_gradient(cmap='RdYlGn_r')` over the `Latency (s)` column with a fall-through to a plain table when the Styler fails.
  - **Error aggregation / log viewer** — `parse_log_lines(lines) -> list[LogEntry]` parses `alerting.setup_logging()`'s formatter `"%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"` (UTF-8, comma- or dot-millisecond). Unparseable lines (multi-line traceback continuations) are RETAINED as `LogEntry(level="", parsed=False, raw=…)` so context survives. `filter_log_entries(entries, *, min_level='INFO', contains=None)` filters ordinally over `VALID_LEVELS = ('DEBUG','INFO','WARNING','ERROR','CRITICAL')` AND a case-insensitive substring; unparsed lines are KEPT regardless of level so traceback continuations are never dropped by a level filter. `tally_levels(entries)` drives the KPI metrics row. `read_log_tail(path, max_lines=500)` is the file IO (missing file → `[]`, never raises). The panel points at `gui.orchestrator_runner.TELEMETRY_LOG_PATH = logs/investyo.log`.
- **gui/panels.py::render_observability** — extended with three new sections after Strategy P&L, each a private helper to keep `render_observability` declarative:
  - `_render_observability_system_telemetry()` — two-column "Host" / "Process" KPI strip with red-saturation warnings at CPU ≥ 90% / memory ≥ 90% and a yellow caution at CPU ≥ 75%.
  - `_render_observability_latency_heatmap()` — KPI strip (Samples, p50, p95, worst symbol) + colour-graded table + "🧹 Clear latency samples" button. Empty-state shows an info hint pointing the operator to the Market Data tab.
  - `_render_observability_error_log()` — KPI strip per level (CRITICAL/ERROR/WARNING/INFO + Total), level dropdown defaulting to INFO, substring filter, and a `st.code(..., language='log')` block capped at 300 most-recent matching lines so a runaway run can't freeze the browser.
- **gui/panels.py::render_market_data** — now also feeds `st.session_state["obs_latency_store"]` (`LatencySampleStore`) on every successful fetch so the Observability heatmap stays in sync without a separate "Sample latency" button.
- **Test surface:** `tests/test_observability_telemetry.py` (26 tests). Classes: `TestSystemTelemetry` (happy shape, forced psutil ImportError → NaN-shaped output, `format_bytes` unit ladder + negative-dash), `TestLatencySampleStore` (record + compute, naive→UTC promotion, ring-buffer roll-off, clear, invalid capacity, empty/non-empty summary), `TestParseLogLines` + `TestFilterLogEntries` + `TestTallyAndIO` (canonical-line round-trip, every level parses, traceback-continuation kept unparsed, blank-line skip, ordinal threshold, case-insensitive substring, unparsed kept under filter, invalid level rejected, tally counts including UNPARSED bucket, missing-file → `[]`, `max_lines` tail).
- **Gravity:** `step_43_observability_telemetry_audit` pins the three-surface contract — telemetry NaN-fallback shape, latency store roll-off + summary, log parser preserves unparseable lines under a level filter.

## Safety / Analytics / Control tabs — circuit breakers, dependency map, version registry, mode toggle, drill-down (2026-06 UI task)
- **gui/circuit_breakers.py** — derivation layer over the file-backed state the platform already writes (`output/KILL_SWITCH`, `output/risk_gate_blocks.jsonl`). Five public surfaces:
  - `read_block_log(path, max_lines=500)` — tolerant JSON-lines reader; corrupt lines are dropped + logged at DEBUG (never raised). Newest first.
  - `derive_kill_switch_trip(path, reason=None)` — emits a CRITICAL `CircuitBreakerTrip` when the sentinel exists; sentinel text and mtime are surfaced as `triggered_at` + detail.
  - `derive_block_log_trips(blocks, *, window=24h, now=…)` — projects risk-gate blocks into typed trips. Keeps the newest per `(check_name, strategy_id)` and drops anything outside the window. Unknown `check_name` values still bubble through (tagged `WARNING`) so a future risk-gate addition surfaces immediately — operator's signal to add a row to the local `_KNOWN_CHECKS` mapping.
  - `collect_circuit_breaker_trips(...)` — top-level helper; kill-switch trip first, then newest-first block-derived trips.
  - `summarise_trips(trips) -> {CRITICAL, WARNING, TOTAL}` — KPI strip rollup.
  - **Architectural rule:** adding a new breaker means adding a check inside `execution/risk_gate.py` and tagging the emitted block; the panel auto-picks it up via `_KNOWN_CHECKS`. NEVER re-implement risk logic in `gui/circuit_breakers.py`.
- **gui/dependency_map.py** — declarative `DataSource` (enum) → `Consumer` (frozen dataclass) graph. The map (`CONSUMERS: Dict[DataSource, tuple[Consumer, ...]]`) is hand-curated by design: inferring it from imports would over-couple to call sites that gate sources on config flags. Public helpers: `impacted_consumers(degraded) -> list[ImpactRecord]` (string inputs that don't match a known `DataSource` resolve to `DataSource.UNKNOWN` with empty impact — never fabricated, CONSTRAINT #5), `all_consumers()`, `render_edges()`. **Extension rule:** add a new consumer of an existing source → append to the right `_*_CONSUMERS` tuple. Add a new source → add a new `DataSource` enum value AND a `_LABELS` entry AND a `CONSUMERS` row. Always carry the data-source change through to Gravity.
- **gui/strategy_registry.py** — strategy file versioning + global execution-mode toggle.
  - `StrategyVersion` dataclass: `(name, file_path, version_hash, last_modified, enabled, weight)`. `version_hash` = sha256(file)[:12] hex; `None` when the file is missing or unreadable (CONSTRAINT #5).
  - `list_strategy_versions(*, module_names=None, weights=None, disabled=None, signals_dir=None)` — joins the live `signals.registry.global_registry` (when available) with `settings.SIGNAL_WEIGHTS` / `settings.DISABLED_SIGNAL_MODULES`. All four kwargs are injectable for tests. Operationally: "version" here means *was the file touched since last orchestrator run*, not semver.
  - `ExecutionMode` enum: `SIMULATION` (DRY_RUN=true), `PAPER` (ALPACA_PAPER=true), `LIVE` (ALPACA_PAPER=false). `read_active_mode() -> ModeState` synthesises mode from the two env vars with `DRY_RUN` winning over `ALPACA_PAPER` (OrderManager intercepts before broker contact regardless of `ALPACA_PAPER`).
  - `set_active_mode(mode)` writes BOTH `DRY_RUN` and `ALPACA_PAPER` together (no half-flips) via the allowlist-bounded `gui/env_io.write_setting`. Effect on **next** launch only — never patches a running `settings.Settings`.
  - **New `ALLOWED_KEYS` entry**: `ALPACA_PAPER`. The Alpaca *secret* keys remain in `SECRET_KEYS` and are still write-protected.
- **gui/panels.py** changes:
  - `render_gravity_audit` is now the **Safety** tab (renamed in the docstring): adds Circuit Breaker Dashboard (`_render_circuit_breaker_dashboard`) + Dependency Map (`_render_dependency_map`) above the existing Gravity audit launcher.
  - `render_strategy_matrix` gains a top **Global Execution Mode** selector (`_render_strategy_mode_toggle`) and a **Strategy Version Registry** table (`_render_strategy_version_registry`) above the existing module enable/weights form. The Live confirm button is labelled `🔴 CONFIRM LIVE PRODUCTION` to force a deliberate click.
  - `render_report_viewer` shows a provenance banner (`_render_report_provenance_banner`): `🔵 Live data` (blue `st.info`) when an orchestrator snapshot exists AND mode ∈ {PAPER, LIVE}; `⚪ Backtested / simulated` (grey Markdown blockquote) otherwise. A new **🔬 Drill down by symbol** expander surfaces the full signal row + recent closed trades for that symbol from `transactions_store.TransactionsStore` — integrating, not reinventing (CONSTRAINT #7).
- **Test surface:** `tests/test_circuit_breakers.py` (12 tests), `tests/test_strategy_registry.py` (12 tests), `tests/test_dependency_map.py` (10 tests). 34 tests total. Coverage: block-log corrupt-line tolerance, kill-switch trip with reason, known-vs-unknown check classification, window filter + per-(name, strategy) dedup; strategy-version happy path + missing-file degradation + hash-changes-on-edit; mode resolution truth table + invalid-mode rejection + two-flag write atomicity; dependency-map registry sanity + UNKNOWN-source no-fabrication + edge-count symmetry.
- **Gravity:** `step_44_safety_analytics_control_audit` pins the contract — kill-switch derivation, block-log dedup, unknown-source no-fabrication, strategy-version hashing, mode resolution truth table.
- **Docs:** `docs/HOW_TO_GUIDE.md` and `docs/RUNBOOK.md` carry a new section on the Safety tab + global mode toggle so a fresh operator can find the kill-switch override and the Live confirm button without spelunking the source.

## Enhanced Observability & Error Handling (2026-06, GUI)

### Dead-Letter Queue (gui/dead_letter.py + main_orchestrator.py)
- **`gui/dead_letter.py`** — read-side consumer of `output/dead_letter.json`. Public API: `DeadLetterEntry` (frozen dataclass: symbol, stage, error, timestamp), `DeadLetterReport` (frozen: run_id, generated_at, entries; `.is_clean`, `.symbols`), `read_dead_letter(path) -> Optional[DeadLetterReport]`. Missing/corrupt file → `None` (CONSTRAINT #4 — no fabricated success). Write side lives in `main_orchestrator.run_pipeline` inline (no `gui.*` import from pipeline layer).
- **`main_orchestrator.run_pipeline`** — per-ticker eval loop now wrapped in try/except with `_stage` tracker (`"dto_construction"` → `"strategy"` → `"edge_ratio"` → `"results"`). Failures append `{symbol, stage, error, timestamp}` to `dead_letter_entries`; after the loop, `output/dead_letter.json` is written atomically (write-then-rename). Empty entries = clean run (file still written so GUI always has a current timestamp). This implements CONSTRAINT #6 for the eval loop — previously, any single-ticker exception would crash the entire pipeline.
- **`gui/orchestrator_runner.py`** — new `RETRY_LOG_PATH = output/gui_retry.log` and `launch_symbol_retry(symbol, refresh_account=False) -> RunHandle`. Spawns `main.py` with `env["WATCHLIST"] = symbol.upper()` so only that ticker (plus held positions) is evaluated. Returns `RunHandle(mode="retry")`. No changes to `main.py` needed — it already reads `WATCHLIST` via `_build_universe()`.
- **`gui/panels.py`** — new `_render_dead_letter_queue()` inserted in `render_launcher()` (after telemetry expander, before auto-refresh). Shows run timestamp, failed symbol + stage + error, and per-symbol **🔄 Retry** buttons that call `launch_symbol_retry()` and display the retry log inline.

### Contextual Error Classification (gui/observability_telemetry.py)
- **`extract_symbol_from_message(message) -> Optional[str]`** — ordered regex patterns (Dead-lettered, "for TICKER", `symbol=`, `ticker=`, `[TICKER]`, prefix colon). Excludes single-letter candidates and common false positives (`AT`, `IN`, `OR`, etc.).
- **`classify_log_entry(entry) -> Literal["systemic", "symbol_specific", "unknown"]`** — symbol-specific is checked FIRST (a dead-lettered ticker message logged by `main_orchestrator` is NOT systemic even though the logger name contains "orchestrat"). Systemic keywords: pipeline, orchestrat, crash, fatal, DataEngine, MacroEngine, fred, sheet, database, schema, etc.
- **`_render_observability_error_log()`** enhanced: a **Contextual Error Summary** expander appears above the raw log when WARNING/ERROR/CRITICAL lines are present, grouping entries into systemic / symbol-specific (deduplicated per ticker) / unclassified buckets. Symbol-specific errors link the operator to the Launcher's Dead-Letter Queue.

### Heartbeat Trend Sparkline (gui/observability_telemetry.py)
- **`HeartbeatSample`** — frozen dataclass: `sampled_at` (UTC datetime), `age_seconds` (float, NaN preserved for gaps).
- **`HeartbeatTrendStore(max_samples=60)`** — bounded ring buffer (deque); `.record(age_seconds) -> HeartbeatSample`; `.to_dataframe()` returns a pandas DataFrame indexed by `sampled_at` with `age_seconds` column for `st.line_chart`. `.clear()` for operator reset.
- **`_render_observability_heartbeat_trend()`** in `gui/panels.py` — wired into `render_observability()` before the system telemetry section. Samples `orchestrator_runner.heartbeat_age_seconds()` on each render; persists store in `st.session_state["obs_heartbeat_trend"]`. KPI strip: Current age / Peak age / Samples / Status (🟢/🟡/🔴). A rising trend over 60 samples ≈ 30 minutes signals a memory leak or hanging thread.

### Test surface
- **`tests/test_dead_letter.py`** (16 tests) — DeadLetterEntry frozen, DeadLetterReport.is_clean/.symbols, read_dead_letter (missing, corrupt, partial entry, empty file, valid payload, run_id preservation).
- **`tests/test_heartbeat_trend.py`** (33 tests) — HeartbeatSample frozen/NaN, HeartbeatTrendStore ring-buffer roll-off/clear/invalid-capacity/to_dataframe, extract_symbol_from_message (7 positive patterns + false-positive exclusions), classify_log_entry (systemic/symbol_specific/unknown, priority ordering, unparsed continuation).
- **Gravity:** `step_46_enhanced_observability_audit` — 10 checks covering all three features: dead-letter read API, contextual classification priority, ring-buffer roll-off, `launch_symbol_retry` callable, `run_pipeline` dead-letter write and `_stage` tracker.

## GUI Operational Efficiency, UX & Architectural Integration (2026-06)

### Pipeline StageStatus enum (gui/orchestrator_runner.py)
- **`StageStatus(str, enum.Enum)`** — five members: `SUCCESS/"success"`, `ACTIVE/"active"`, `ERROR/"error"`, `PENDING/"pending"`, `SKIPPED/"skipped"`. Inherits from `str` so legacy callers doing `if status == "active"` continue to work without modification.
- **`compute_stage_status(handle) -> Dict[str, StageStatus]`** now returns typed `StageStatus` values. New behaviour: `DRY_RUN=true` on an orchestrator run forces the `"Execution"` stage to `SKIPPED`; a non-zero exit code on the last-active stage emits `ERROR`; prior stages on an error run stay `SUCCESS`.
- **`STAGES`** list has exactly 4 pipeline stages: Data Acquisition, Processing, Forecasting, Execution.
- Launcher stage indicator rendering updated: uses `StageStatus`-aware icon map (`✅`/`🟡`/`🔴`/`⚪`/`⏭️`) and displays `.value` for enum instances.
- **Test surface:** `tests/test_pipeline_stage_status.py` (12 tests) — enum str-subclass, all 5 members, string equality, 4-stage count, compute_stage_status variants (None handle, no log, finished-clean, dry-run-skipped, error-path).

### Preflight Runner (gui/preflight_runner.py)
- **`PreflightCheck`** (frozen dataclass: `name, passed, reason, warning`). **`PreflightReport`** (frozen: `all_passed, checks, error, returncode`).
- **`run_preflight(timeout, skip) -> PreflightReport`** — subprocess wrapper around `scripts/preflight_check.py --json`. **CONSTRAINT #4**: timeout/missing-script/corrupt-JSON/empty-stdout → `all_passed=False` — never fabricates success.
- **`gui/panels._render_preflight_panel()`** — on-demand gate button in the Launcher tab; renders per-check pass/fail table; uses `st.session_state["preflight_report"]` for persistence across reruns.
- **Test surface:** `tests/test_preflight_runner.py` (16 tests) — import, frozen fields, good path, non-zero exit, timeout, missing script, corrupt JSON, empty stdout, subprocess exception, wiring checks.

### Launcher Safety Controls (gui/panels._render_launcher_safety_controls)
- **`_render_launcher_safety_controls()`** — kill-switch toggle + DRY_RUN checkbox + Safe Mode composite indicator in the Launcher tab. Safe Mode is **DERIVED** (no new env var): `ks.is_active() AND settings.DRY_RUN`. Writes `DRY_RUN` via the allowlist-bounded `gui.env_io.write_setting`. Wired into `render_launcher()` between stage indicators and the log expanders.
- **Test surface:** `tests/test_launcher_safety_controls.py` (12 tests) — helper exists/callable, SAFE_MODE not in ALLOWED_KEYS/SECRET_KEYS, DRY_RUN in ALLOWED_KEYS, write round-trips, kill-switch activate/deactivate, safe-mode derivation logic.

### Persistent Run-Mode Header (gui/run_mode.py + gui/app.py)
- **`RunModeState`** (frozen dataclass: `mode, process, dry_run, alpaca_paper, icon, color, pid, run_mode_label`). **`read_active_run_mode(session_state={}) -> RunModeState`** — Streamlit-free derivation (testable headlessly). Mode truth table: `(DRY_RUN=T,*) → Simulation`; `(False,PAPER=T) → Paper`; `(False,False) → Live`.
- **`gui/app.py`** renders a persistent colored banner above the tab bar on every Streamlit render: `st.error` (red) for Live, `st.warning` (amber) for Paper, `st.info` (blue) for Simulation.
- **Test surface:** `tests/test_run_mode.py` (15 tests) — import, frozen, idle/running/finished process derivation, mode truth table, icon/color/label non-empty, app.py references run_mode.

### Symbol Search (gui/symbol_search.py)
- **`filter_by_symbol(df, query, *, column="Symbol") -> pd.DataFrame`** — Streamlit-free, case-insensitive contains match on the symbol column. Empty/None/whitespace query returns the full DataFrame unchanged. NaN symbol rows always pass through (never silently dropped for EQUITY_ONLY sentinels). Falls back to first column when `"Symbol"` absent.
- Wired into **`render_report_viewer`** (🔍 Filter by symbol above the signals table) and **`render_live_inventory`** (🔍 Filter by symbol above the inventory table).
- **Test surface:** `tests/test_symbol_search.py` (15 tests) — passthrough, exact/partial/case-insensitive match, no-match empty, NaN pass-through, custom column, fallback column, empty DataFrame, returns-same-object.

### Strategy Health View (gui/strategy_health.py + gui/panels._render_strategy_health)
- **`DeployabilityGate`** (frozen: `metric, value, threshold, direction, passed`). **`StrategyHealth`** (frozen: `strategy_id, deployable, gates, is_options_selling, stress_passed, last_audited_at`).
- **`read_gravity_report(path) -> list[dict]`** — tolerant reader of `output/gravity_verification_report.json`. Missing → `[]`, corrupt JSON → `[]`, wrong schema → `[]` (CONSTRAINT #4 — never fabricate).
- **`evaluate_gate(strategy_dict) -> StrategyHealth`** — evaluates one strategy dict against thresholds from `validation.thresholds` (single source of truth). Missing/NaN metric → `gate.passed=None`. `deployable` mirrors the report field rather than re-deriving.
- **`gui/panels._render_strategy_health()`** — top section of `render_gravity_audit` showing per-strategy gate table with PASS/FAIL/N/A indicators. Wired first (before circuit breakers / dependency map).
- **`Gravity AI Review Suite._write_gravity_verification_report()`** — writes `output/gravity_verification_report.json` atomically (write-then-rename) at the end of every audit run.
- **Test surface:** `tests/test_strategy_health.py` (20 tests) — import, frozen fields, read_gravity_report failure modes (missing/corrupt/non-dict/non-list), valid-file happy path, evaluate_gate all-pass/individual-gate-fail/missing-metric-None/NaN-metric-None/deployable-mirrors-report/options-selling-stress.

### Gravity Audit steps 47-50
- **`step_47_launcher_safety_bundle_audit`** — verifies `_render_launcher_safety_controls` exists, touches DRY_RUN + kill-switch together, SAFE_MODE not in ALLOWED_KEYS.
- **`step_48_preflight_runner_audit`** — verifies `run_preflight` returns typed report; timeout → `all_passed=False`; `_render_preflight_panel` wired into `render_launcher`.
- **`step_49_dual_mode_header_audit`** — verifies `gui.run_mode` importable, `read_active_run_mode({})` returns `process="idle"`, `gui/app.py` references `run_mode`.
- **`step_50_strategy_health_audit`** — verifies `validation.thresholds` exports 5 constants; `validation.harness` imports from it; `read_gravity_report` → `[]` on missing/corrupt file; `tests/test_strategy_health.py` exists.
- **`_extend_launcher_telemetry_audit_stage_status`** — appends StageStatus enum checks to step_41: `StageStatus` is `str`-subclassed, 5 members, string equality, `STAGES` has 4 elements.
- **`_extend_safety_control_audit_launcher`** — appends Launcher-tab safety-control checks to step_44: `_render_launcher_safety_controls` exists and `render_launcher` calls it.

## Tier 1 Decision Support — "Δ Since Last Run" snapshot diff (2026-06)

### scripts/snapshot_diff.py — rotation + diff engine
- **`scripts/snapshot_diff.py`** — single source of truth for snapshot rotation AND the "what changed since yesterday" diff. Public surface: `SnapshotDiff` (frozen dataclass: `prev_ts`, `curr_ts`, `regime_change`, `new_buys`, `action_flips`, `conviction_deltas`, `added_holdings`, `dropped_holdings`, `notes`, `.is_empty`, `.to_dict()`), `load_snapshot(path)`, `list_rotated_snapshots(output_dir)`, `rotate_snapshot(snapshot, output_dir, *, max_age_days, now=None)`, `compute_diff(prev, curr, *, conviction_delta_threshold)`, `compute_diff_from_history(output_dir, *, conviction_delta_threshold)`, `format_markdown(diff)`. CLI: `python -m scripts.snapshot_diff prev.json curr.json [--format markdown|json] [--conviction-threshold 0.2]`; with no positional args, defaults to the two most-recent rotated snapshots under `--output-dir`.
- **Tolerance contract (CONSTRAINT #4 + #6):** every loader/diff path is wrapped — missing file → `None`, corrupt JSON → `None`, non-object JSON → `None`, write/prune failures → logged at WARNING/DEBUG and skipped; the diff engine NEVER raises so the daily HTML report renders even with a degraded history dir. First-run case (`prev is None`): all current BUYs become `new_buys`, all current holdings become `added_holdings`, `regime_change` stays `None` (a first-run regime is not a "change"). Identical snapshots → `SnapshotDiff.is_empty == True`.
- **Classification rules (pinned by `tests/test_snapshot_diff.py` + Gravity step 51):**
  - *New BUY* = (no prior signal OR prior action did not contain `"BUY"`) AND current action contains `"BUY"`. Takes precedence over `action_flips` (a HOLD→BUY is reported once, as a new_buy).
  - *Action flip* = both sides present, both non-empty, different, AND NOT already in `new_buys`. Each entry: `{symbol, before, after}`.
  - *Conviction delta* = `advisory_conviction` (or fallback `conviction`) present on both sides AND `|after − before| ≥ conviction_delta_threshold`. Default threshold = 0.2.
  - *Holdings added/dropped* = set diff over the snapshot's `holdings` list (or, if absent, backfilled from `signals[].shares > 0`).
  - *Regime change* = `prev.market_regime` and `curr.market_regime` both non-empty AND different.
- **Rotation contract:** `rotate_snapshot()` writes `output/history/state_snapshot_<UTC>.json` via atomic write-then-rename. Filename encodes the snapshot's own ISO `timestamp` field when parseable (else wall-clock UTC), formatted `state_snapshot_YYYYMMDDTHHMMSSZ.json` (colon-free, FAT/NTFS-safe). Files older than `max_age_days` are pruned in the same call; `max_age_days=0` disables pruning. Non-matching filenames in `history/` are ignored.

### Wiring
- **`main_orchestrator._write_state_snapshot()`** now writes a `holdings: list[str]` field (sorted symbols where `Shares > 0`) and calls `rotate_snapshot(snapshot, settings.OUTPUT_DIR, max_age_days=settings.SNAPSHOT_HISTORY_DAYS)`. The snapshot is rotated BEFORE the HTML report renders (moved out of the tail of `_main_body()`) so the Δ-band diff sees `curr = this run / prev = previous run`. The HTML-report block then calls `compute_diff_from_history()` and passes `snapshot_diff_payload` to `generate_html_report(snapshot_diff=...)`.
- **`main._write_state_snapshot(result, macro_dto)`** — NEW helper in the advisory entry point. Emits the same JSON schema (timestamp, holdings, signals, market_regime, vix, kill_switch_active, macro_regime_gate_enabled) so the diff engine sees a consistent shape across both entry points. Called from `_write_html_report()` BEFORE the HTML render. `_load_snapshot_diff_for_report()` then returns the `.to_dict()` payload (or `None` on first ever run / any failure).
- **`diagnostics_and_visuals.generate_html_report(..., snapshot_diff=None)`** — new keyword-only kwarg. When non-`None`, the template renders a top-of-report "Δ Since Last Run" band (CSS class `.delta-band`) above the macro/regime cards, with grid cells for `new_buys`, `action_flips`, `conviction_deltas`, `added_holdings`, `dropped_holdings` and a banner for `regime_change`. When `None` (no prior snapshot or any rotation/diff failure) the band is hidden entirely — the report is unchanged.

### New env vars / settings
- **`SNAPSHOT_HISTORY_DAYS: int = 30`** — snapshots in `output/history/` older than this are pruned each run; `0` disables pruning.
- **`SNAPSHOT_CONVICTION_DELTA_THRESHOLD: float = 0.2`** — `|Δ advisory_conviction|` at or above this surfaces in the Δ band; smaller moves are noise-suppressed. Both pinned by Gravity step 51.

### Test surface
- **`tests/test_snapshot_diff.py`** (24 tests). Classes: `TestLoadSnapshot` (missing/empty/corrupt/non-object/valid round-trip), `TestRotation` (rotation-writes-history-file, filename encodes timestamp, prune drops >max_age, prune disabled when max_age=0, ignores unrelated files), `TestComputeDiff` (first-run lists buys/holdings, identical-snapshots-yield-empty, action-flip BUY→HOLD, new-buy precedence over flip, conviction threshold filters 0.19 but surfaces 0.21, regime change detected, no regime change when equal, holdings added/dropped, holdings backfilled from `shares > 0`), `TestHistoryIntegration` (two rotations → real diff, single rotation → first-run shape, no history → empty with note), `TestModuleSurface` (default threshold = 0.2, `SnapshotDiff.to_dict()` is JSON-serialisable).

### Gravity step 51
- **`step_51_snapshot_diff_audit`** — 10 checks: module surface (`SnapshotDiff`, `compute_diff`, `rotate_snapshot`, `compute_diff_from_history`, `load_snapshot`, `list_rotated_snapshots`, `DEFAULT_CONVICTION_DELTA_THRESHOLD`); default threshold = 0.2; `settings.SNAPSHOT_HISTORY_DAYS == 30` AND `settings.SNAPSHOT_CONVICTION_DELTA_THRESHOLD == 0.2`; `generate_html_report` accepts `snapshot_diff` kwarg (signature inspection); `main_orchestrator.py` references `rotate_snapshot` + `"holdings"` + `compute_diff_from_history`; `main.py` defines `_write_state_snapshot` AND references `rotate_snapshot` AND passes `snapshot_diff=`; `rotate_snapshot()` round-trip via tempdir; first-run BUYs land in `new_buys` not `action_flips`; `load_snapshot(corrupt_file)` returns `None` (CONSTRAINT #4 + #6); `tests/test_snapshot_diff.py` exists.

## Tier 1 / 1.2 — Conviction Calibration Tracker (2026-06)

### Overview
"When the system says 0.80, does it actually win 80% of the time?" Bins closed trades with recorded conviction scores into equal-width buckets, computes actual win rate per bucket, and renders a reliability diagram in the GUI Reports tab.

### Schema migration (`transactions_store.py`)
- `Trade.conviction = Column(Float, nullable=True)` — advisory signal conviction [0, 1] at entry time.
- `TransactionsStore._ensure_conviction_column()` runs on every `__init__`: inspects existing columns via SQLAlchemy `inspect`, issues `ALTER TABLE trades ADD COLUMN conviction REAL` only when missing. Safe to call on new or legacy databases.
- `record_trade()` gains `conviction: Optional[float] = None` kwarg (backward-compatible; existing callers unaffected).

### `evaluation_engine.calibration_curve()` (module-level function)
```
calibration_curve(transactions_store, n_bins=10, min_trades_per_bin=5) -> pd.DataFrame
```
- Reads `closed_trades_df()`, drops rows missing `conviction`/`entry_price`/`exit_price` (CONSTRAINT #4).
- Win definition (side-aware): long → `exit_price > entry_price`; short → `exit_price < entry_price`.
- Bins by conviction using `pd.cut` over `np.linspace(0, 1, n_bins+1)`.
- `win_rate=NaN` for bins with fewer than `min_trades_per_bin` trades (never fabricated).
- Returns empty DataFrame with correct 7-column schema (`bin_low`, `bin_high`, `bin_center`, `conviction_mean`, `win_rate`, `count`, `perfect_calibration`) on any failure — dead-letter tolerant (CONSTRAINT #6).

### GUI: Reports tab (`gui/panels.py`)
- `_render_calibration_section()` — inserted after Brinson-Fachler, before report exports.
- KPI strip: Trades w/ Conviction / Overall Win Rate / Calibration Error (MAE) / Bins w/ Data.
- Reliability diagram via matplotlib (`st.pyplot`): bars = actual win rate per bin, dashed diagonal = perfect calibration.
- "No conviction data yet" info box when no conviction-annotated closed trades exist.

### Test surface
- **`tests/test_calibration.py`** (24 tests, 5 classes): `TestSchema` (empty store, no conviction column, all-null, store read failure, count dtype); `TestWinRateLogic` (long win/loss, short win/loss, mixed, exit==entry is not a win); `TestBinning` (n_bins, bin bounds, center=midpoint, perfect_calibration=center, trades in correct bins); `TestMinTradesGate` (below threshold→NaN, at threshold OK, empty bin mean→NaN); `TestRecordTradeConviction` (kwarg accepted, persisted, None→null, column in open trades).

### Gravity step 52
- **`step_52_calibration_audit`** — 10 checks: import, schema constant, empty store, no conviction column, all-null, long win logic, short win logic, min_trades gate, dead-letter read failure, record_trade persistence.

## Tier 1 / 1.3 — Manual Execution Decision Journal (2026-06)

### Overview
Operator logs whether each advisory signal was acted on, passed, or modified. Records accumulate in `output/decision_log.jsonl` (JSON-Lines, append-only). An optional join step links "acted" entries back to the nearest `TransactionsStore` trade record within ±24 h so the calibration tracker (1.2) can filter to decisions that were actually executed — turning the calibration from "all signals" to "signals the operator endorsed."

### New module: `gui/decision_log.py`
Headlessly testable (no streamlit imports). Public API:
- `DecisionEntry` — frozen dataclass: `symbol`, `action_taken` (`"acted"|"passed"|"modified"`), `signal_action`, `conviction`, `notes`, `timestamp`, `signal_ts`, `trade_id`.
- `append_decision(entry, log_path)` — atomic JSONL line append; creates parent dirs.
- `read_decisions(log_path)` — tolerant reader; corrupt/blank lines skipped (CONSTRAINT #6).
- `decisions_df(log_path)` — typed DataFrame (Int64 nullable `trade_id`); empty schema when log absent (CONSTRAINT #4).
- `join_to_store(entry, transactions_store, window_hours=24.0)` — finds closest matching trade by symbol within `±window_hours` of `entry.timestamp`; returns `None` on no-match or any failure.
- `log_decision(...)` — orchestrates: build → join (if acted) → append; injectable `now_fn` for tests.

### GUI: Reports tab (`gui/panels.py`)
- `_render_decision_journal_section(signals)` — inserted between the drill-down expander and Brinson-Fachler.
- Symbol selectbox + signal-context KPI strip (system action, conviction) + notes textarea.
- Three buttons: **✅ Acted** / **⏭ Passed** / **🔁 Modified** (Modified requires non-empty notes).
- Success banner after click shows join result (`linked to trade #N` or `no match within 24h`).
- Past-decisions collapsible log with CSV download.

### Log file
`output/decision_log.jsonl` — append-only, never read by the signal pipeline. Written to `settings.OUTPUT_DIR / "decision_log.jsonl"` from the GUI. Never committed to git (add to `.gitignore` if not already excluded via `output/`).

### Join convention (must never regress)
- `join_to_store` is called ONLY when `action_taken == "acted"`. "passed" and "modified" decisions never set `trade_id`.
- The join window default is 24 h. Never fabricate a `trade_id` — if no match, set `None` (CONSTRAINT #4).
- Symbol matching is case-insensitive (normalized to uppercase).

### Test surface
- **`tests/test_decision_log.py`** (30 tests, 5 classes): `TestDecisionEntry` (frozen, fields, action_taken values); `TestAppendAndRead` (round-trip, multiple entries, missing file, corrupt/blank lines, trade_id/None-conviction round-trips, parent-dir creation); `TestDecisionsDf` (empty schema, Int64 dtype, nullable NaN, row count); `TestJoinToStore` (within window, outside window, symbol not found, closest pick, store failure, case-insensitive); `TestLogDecision` (field wiring, log append, passed/modified skip join, acted joins, acted no match → None, None conviction).

### Gravity step 53
- **`step_53_decision_log_audit`** — 10 checks: import, frozen dataclass + fields, round-trip, empty schema, corrupt line skip, join within window, join returns None outside, passed skips join, acted joins, test file exists.

## Tier 5.1 — ADVISORY_ONLY Mode Quarantine (2026-06)

### Summary
- New flag `settings.ADVISORY_ONLY: bool = True` (project default). When `True`, the entire broker-execution surface is quarantined: `main_orchestrator._execute_broker_orders` returns immediately (no broker imports), the GUI Strategy Matrix mode toggle is suppressed, and `scripts/preflight_check.py` drops the broker-readiness checks (`alpaca_configured` / `alpaca_paper_mode` / `dry_run_disabled` / `paper_trading_duration`) in favour of a new `advisory_only_active` check.
- ADVISORY_ONLY is a HARDER gate than `DRY_RUN`: `DRY_RUN` is enforced inside `OrderManager` (one method, future callers could bypass), while ADVISORY_ONLY is enforced at the `_execute_broker_orders` boundary AND surfaced in every GUI tab as a persistent banner, so an operator cannot click into Live by mistake. Both flags must agree (`ADVISORY_ONLY=false` AND `DRY_RUN=false` AND `ALPACA_PAPER=false`) to reach a live submission.

### Wiring
- **`settings.py`** — adds `ADVISORY_ONLY: bool = Field(default=True, ...)`.
- **`main_orchestrator._execute_broker_orders`** — adds the early-return guard at the very top of the function body (BEFORE the broker-stack imports), emitting an INFO log: `"ADVISORY_ONLY=True — broker execution surface is quarantined; skipping all order submission, reconciliation, and broker imports."`
- **`main_orchestrator._main_body`** (call site of `_execute_broker_orders`) — when `ADVISORY_ONLY=True`, logs `"📋 ADVISORY_ONLY=True — pipeline produced N signals; broker execution is disabled for this run."` and does NOT check `ALPACA_API_KEY`/`SECRET_KEY` — so an operator who happens to have keys in `.env` from an earlier paper-trading phase does NOT trigger any broker import.
- **`gui/app.py`** — the persistent run-mode header now branches on ADVISORY_ONLY first; when `True`, renders a single `st.info` "📋 **ADVISORY MODE** — no orders will be submitted to any broker." banner above the tab bar instead of the Simulation/Paper/Live badge (which would be misleading while the broker is quarantined).
- **`gui/panels._render_strategy_mode_toggle`** — when `ADVISORY_ONLY=True` does NOT render the radio + confirm button; renders an `st.warning` "📋 **Advisory mode — broker execution disabled.**" placeholder + a read-only caption showing the underlying `DRY_RUN` / `ALPACA_PAPER` flags. Set `ADVISORY_ONLY=false` in `.env` to restore the live mode-switcher.
- **`scripts/preflight_check.py`** —
  - New `check_advisory_only_active()` function. PASS (loud) when `ADVISORY_ONLY=True`; PASS with `warning=True` when False so the operator confirms they deliberately lifted the quarantine.
  - Module-level constant `_ADVISORY_AUTO_SKIP` (8 entries): broker-dependent checks (`alpaca_configured`, `alpaca_paper_mode`, `dry_run_disabled`, `paper_trading_duration`, `alpaca_key_rotation_recent`) + advisory false-positive checks (`heartbeat_fresh`, `validation_reports`, `no_unexpected_risk_blocks`). **Stage 2 expanded from 4 → 7 entries** to eliminate false-positive failures on a correctly-running advisory deployment: `heartbeat_fresh` fails because only `main_orchestrator.py` writes `heartbeat.txt` (not `main.py`); `validation_reports` gates live deployment, not advisory operation; `no_unexpected_risk_blocks` requires order submissions which never occur in advisory mode. **Stage 3 expanded from 7 → 8 entries** by adding `alpaca_key_rotation_recent` (Alpaca keys have no blast-radius risk while the broker surface is quarantined). `state_snapshot_fresh` is deliberately NOT in `_ADVISORY_AUTO_SKIP` — it is the advisory liveness indicator (both entry points write `state_snapshot.json`).
  - `run_checks()` reads `settings.ADVISORY_ONLY` once; when True, each check in `_ADVISORY_AUTO_SKIP` is recorded as PASS with reason `"(skipped: ADVISORY_ONLY=True — broker check not applicable)"`. The `--skip` flag still takes precedence (operator-explicit skip wins over auto-skip).
  - `ALL_CHECKS` order (16 total): `check_fred_key_configured` → `check_key_rotation_recent` → `check_alpaca_key_rotation_recent` → `check_advisory_only_active` → broker checks → `check_env_not_committed` → `check_kill_switch_inactive` → **`check_state_snapshot_fresh`** → `check_heartbeat_fresh` → `check_db_exists` → `check_paper_trading_duration` → `check_validation_reports` → `check_no_unexpected_risk_blocks`.

### New env vars / settings (Tier 5.1 + Stage 2)
- **`ADVISORY_ONLY: bool = True`** — the project default; set `ADVISORY_ONLY=false` in `.env` to re-enable broker execution.
- **`FRED_KEY_ROTATED_DATE`** — ISO date (YYYY-MM-DD) in `.env.example` (added Stage 2); `check_key_rotation_recent` uses it to warn when > 90 days since last rotation.

### Test surface
- **`tests/test_advisory_only.py`** (9 tests): orchestrator early-return + INFO log under `ADVISORY_ONLY=True`; no early-return log under `False`; AST/source guards that `gui/panels.py` and `gui/app.py` reference the flag and the banner strings; preflight auto-skip under True; preflight broker checks run under False; `advisory_only_active` row appears in results; warning flag when ADVISORY_ONLY is disabled; default `settings.ADVISORY_ONLY is True`.
- **`tests/test_preflight.py`** — extended in Stage 2 with `TestStateSnapshotFresh` (fresh/stale/missing/mtime-fallback/not-in-auto-skip) and `TestAdvisoryAutoSkip` (all 8 entries present, state_snapshot_fresh excluded, run_checks applies auto-skip; Stage 3 updated count from 7 → 8 for `alpaca_key_rotation_recent`). Stage 3 also extended `TestKeyRotationChecks` with `test_alpaca_rotation_fresh_passes` and `test_alpaca_rotation_invalid_iso_warns` to give symmetric coverage for FRED and Alpaca rotation checks. Total: 55 tests.

### Gravity step 54
- **`step_54_advisory_only_audit`** — 9 checks (updated in Stages 2+3): default ADVISORY_ONLY=True; `main_orchestrator.py` source references ADVISORY_ONLY + the quarantine log message; `gui/panels.py` source has the ADVISORY_ONLY guard + "Advisory mode — broker execution disabled" banner string; `gui/app.py` source renders the "ADVISORY MODE" banner; `preflight_check.check_advisory_only_active` exists; **`_ADVISORY_AUTO_SKIP` is a `dict[str, str]` with all 8 advisory-mode auto-skip entries** (5 broker-dependent including `alpaca_key_rotation_recent`, plus 3 advisory false-positives; check 6 uses superset test not exact equality so future additions don't break it); functional skip path under `ADVISORY_ONLY=True`; `check_advisory_only_active` warns under `ADVISORY_ONLY=False`; `tests/test_advisory_only.py` exists.

### Gravity step 66 (Stage 2)
- **`step_66_advisory_false_positive_audit`** — 10 checks: `check_state_snapshot_fresh` exists + in `ALL_CHECKS`; `_ADVISORY_AUTO_SKIP` contains all 8 expected entries (including `alpaca_key_rotation_recent` from Stage 3); `state_snapshot_fresh` NOT in `_ADVISORY_AUTO_SKIP`; fresh snapshot → PASS + missing snapshot → FAIL (fail-closed); stale snapshot (>2h) fails via timestamp field; `heartbeat_fresh` auto-skipped; `validation_reports` + `no_unexpected_risk_blocks` auto-skipped; `ALL_CHECKS` count == 16; `tests/test_preflight.py` contains both new test classes.

### Gravity step 67 (Stage 3)
- **`step_67_key_rotation_audit`** — 10 checks: `check_alpaca_key_rotation_recent` exists + callable; `settings.ALPACA_KEY_ROTATED_DATE` field exists; unset date → warning PASS; fresh date → clean PASS; stale date → warning PASS (never `passed=False`); invalid ISO → warning PASS; `alpaca_key_rotation_recent` in `_ADVISORY_AUTO_SKIP`; auto-skip fires under `ADVISORY_ONLY=True`; both rotation checks in `ALL_CHECKS` in order (FRED first, Alpaca second); `tests/test_preflight.py` contains `TestKeyRotationChecks`.

### Operator notes
- The kill-switch sentinel (`output/KILL_SWITCH`) and `MACRO_REGIME_GATE_ENABLED` flag are NOT changed by this tier. They remain in place and continue to gate `OrderManager` behaviour when ADVISORY_ONLY is lifted in the future.
- Re-enabling broker execution is a deliberate two-step operation: (1) flip `ADVISORY_ONLY=false` in `.env`; (2) launch the orchestrator; (3) verify the preflight check now runs the broker-readiness gate. The GUI Strategy Matrix mode toggle reappears automatically once ADVISORY_ONLY is False.

## Tier 5.2 — RUNBOOK.md Advisory-Platform Rewrite (2026-06)

Pure docs change. No new code, no new module, no schema change.

### What changed
- `docs/RUNBOOK.md` fully rewritten for advisory-mode operation (see the `docs/RUNBOOK.md` entry in the Architecture section above for the full table of contents).
- `docs/HOW_TO_GUIDE.md` updated: §11 advisory caveat, §13 preflight table updated to show auto-skip under `ADVISORY_ONLY=true`, §15 kill-switch repurposed as pause-recommendations gate, new **Advisory-Only Mode** section added before the Strategy Matrix tab section.

### Incident playbooks added (§3.1–3.3 of RUNBOOK.md)
1. **Stale account snapshot** — `python3 main.py --refresh-account`, root-causes table, held-symbol safety rule.
2. **Missing recommendation for held symbol** — Dead-Letter Queue workflow, stage/cause/fix table, EQUITY_ONLY escalation.
3. **Calibration score dropping below threshold** — `evaluation_engine.calibration_curve()` diagnostic, MAE severity table (< 0.10 monitor / 0.10–0.15 harness re-run / > 0.15 disable module), minimum 30-trade data requirement.

### Advisory pause procedure (§6 of RUNBOOK.md)
The kill-switch sentinel (`output/KILL_SWITCH`) repurposes in advisory mode as a pause-recommendations gate. `main.run_once()` already checks `GlobalKillSwitch.is_active()` and logs "advisory paused by kill-switch sentinel" when the file exists — this is enforced by **Tier 5.1** code, not docs. The runbook documents the operator flow:

```bash
python -m execution.kill_switch --activate --reason "advisory pause — investigating anomaly"
# Expected next run: INFO — advisory paused by kill-switch sentinel; skipping evaluation cycle
python -m execution.kill_switch --deactivate
```

### Gravity step
No Gravity step needed — this is a docs-only change. No new functions, no new schema, no audit criteria were added.

### No new env vars / dependencies
This task introduced no new environment variables and no new Python dependencies.

## Tier 5.3 — Kill Switch as Pause Recommendations Gate (2026-06)

### File-Based Sentinel Protocol
- **`main.run_once()`** — after Stage B (universe build), before Stage C (macro compute): checks `GlobalKillSwitch().is_active()`. When active, logs `"Advisory paused by kill-switch sentinel — skipping evaluation cycle"` (with reason + universe preview) and returns an early `RunResult` with empty `recommendations` and one error entry at `stage="kill_switch_gate"`. The account snapshot is still populated so the observability dashboard continues displaying holdings.
- **`main_orchestrator._main_body()`** — after data fetch, before `run_pipeline()`: same sentinel check. When active, logs the canonical pause message and `return`s immediately. The last written `state_snapshot.json` is untouched so the GUI shows the last known state.
- Both checks import `GlobalKillSwitch` at call time (inside the function, not at module top) so tests that monkeypatch the class resolve correctly.

### Macro-Triggered Advisory Gating (`engine/advisory.py`)
New **Step 8b** block between the StrategyEngine call and the holding-aware overlay:

| Condition | Effect |
|---|---|
| `market_regime in ("RECESSION", "CREDIT EVENT")` | Hard gate: raw STRONG BUY / BUY → HOLD; `raw_signal` and `adjusted_score` both overridden |
| `vix_value > 30.0` OR `sahm_rule_indicator ≥ 0.5` | Soft gate: `adjusted_score = max(0, score - 25)` |
| Finance/Financial Services/Real Estate sector AND (`yield_curve_10y_2y < 0` OR `high_yield_oas > 6`) | Sector veto: BUY → HOLD for structurally-exposed sectors |

`macro_gate_reason` string is assembled and:
- Passed to `_build_rationale()` (new kwarg, default `""`).
- Prepended as "Driver 0" in the rationale when non-empty so it is the first thing the operator reads.
- The holding-aware overlay Case B threshold now uses `adjusted_score` (post-penalty) instead of the raw `score`.

All six CONFIG keys added (see table in architecture section above). No magic numbers in decision logic — every threshold lives in `CONFIG`.

### New CONFIG entries (`engine/advisory.py`)
| Key | Default | Description |
|---|---|---|
| `macro_vix_gate_threshold` | `30.0` | VIX above this → soft gate fires |
| `macro_sahm_gate_threshold` | `0.5` | Sahm Rule at/above this → soft gate fires |
| `macro_score_penalty` | `25` | Points subtracted under soft gate |
| `macro_veto_sectors` | `["Financials","Financial Services","Real Estate"]` | Sectors blocked from fresh buys under adverse conditions |
| `macro_veto_yield_curve_threshold` | `0.0` | Yield curve below this → sector veto applies |
| `macro_veto_oas_threshold` | `6.0` | HY OAS above this → sector veto applies |

### Test surface
- **`tests/test_advisory_pause_gate.py`** (new, 3 test classes, ≈22 tests):
  - `TestKillSwitchPauseGate` — `run_once()` with active/inactive sentinel; pause reason in `errors`; inactive → pipeline runs
  - `TestOrchestratorKillSwitchGate` — `_main_body` skips `run_pipeline`; source-grep check
  - `TestMacroTriggeredGating` — RECESSION, CREDIT EVENT, RISK ON, NEUTRAL, VIX > 30, Sahm ≥ 0.5, sector veto Finance, sector veto Real Estate, non-vetoed Tech, macro_gate_reason in rationale, no gate noise in clean runs
  - `TestMacroGateConfig` — all six CONFIG keys present, correct types, canonical defaults, veto sector membership

### Gravity step 55
`run_advisory_pause_gate_audit()` — 10 checks: CONFIG keys, threshold defaults (30.0 / 0.5 / 25), veto sectors, Step 8b + `macro_gate_reason` in source, `main.py` pause strings, `main_orchestrator.py` pause string, test file exists, `_build_rationale` signature, functional RECESSION→HOLD via minimal mock.

### Operational flow (unchanged CLI)
```bash
python -m execution.kill_switch --activate --reason "advisory pause — investigating anomaly"
# Expected next run: INFO — advisory paused by kill-switch sentinel; skipping evaluation cycle
python -m execution.kill_switch --deactivate
```

### No new env vars / dependencies
This task introduced no new environment variables and no new Python dependencies.

## Tier 1.4 — Symbol Watch with Threshold Alerts (2026-06)

### Overview
Fills the critical visibility gap between manual system runs: `watch_engine.py` evaluates `watch_rules.yaml` rule definitions against advisory pipeline output at the end of every `run_once()` cycle, then dispatches ntfy push notifications for matched rules. Operators who monitor the platform via their phone now receive proactive, intraday alerts without polling the dashboard.

### New module: `watch_engine.py`
Headlessly testable (no Streamlit imports). Public API:
- `WatchRule` — frozen dataclass: `symbol`, `alert_on`, `threshold`, `priority`, `label`.
- `WatchAlert` — frozen dataclass: `symbol`, `rule_type`, `priority`, `title`, `message`, `trigger_detail`.
- `SymbolWatchState` — mutable dataclass: `action`, `conviction`, `alerted_conviction_above`, `alerted_conviction_below`, `timestamp`. Serialises via `.to_dict()` / `.from_dict()`.
- `load_watch_rules(path) -> list[WatchRule]` — parses YAML; returns `[]` on missing/malformed file, never raises. Validates symbol, alert_on, threshold, priority; skips invalid rules with WARNING.
- `load_watch_state(path) -> dict[str, SymbolWatchState]` — reads `output/watch_state.json`; returns `{}` on missing/corrupt, never raises (CONSTRAINT #6).
- `save_watch_state(state, path) -> None` — atomic write-then-rename; swallows failures (CONSTRAINT #6).
- `evaluate_watch_rules(rules, recommendations, prev_state) -> (list[WatchAlert], dict[str, SymbolWatchState])` — pure comparison logic; never fetches market data (no-lookahead invariant).
- `dispatch_watch_alerts(alerts, *, dashboard_url=None) -> None` — calls `alerting.notify()` per alert; per-alert try/except; silent when `NTFY_TOPIC` is unset.

### Alert types
| `alert_on` | Semantics |
|---|---|
| `action_change` | Fires once per action flip (HOLD→BUY, BUY→SELL, etc.). Never fires on first run (no prior action). |
| `conviction_above` | Edge-triggered: fires on the first run where `conviction ≥ threshold`. Silent while condition persists. Resets when conviction drops back below threshold. |
| `conviction_below` | Mirror edge-trigger: fires on the first run where `conviction < threshold`. |

### No-lookahead invariant (Gravity Step 56)
`evaluate_watch_rules` compares `prev_state` (data from the END of the previous run) against `recommendations` (advisory output from the just-completed run). No market-data fetching, forecasting, or model inference occurs inside this function. Verified by Gravity step 56 via monkeypatching `data.market_data.get_provider`.

### State file
`output/watch_state.json` — per-symbol JSON record written atomically. Tracks `action`, `conviction`, `alerted_conviction_above` (dict of threshold → bool), `alerted_conviction_below`, and `timestamp`. Missing file = first run (empty state). Symbols that leave the universe are dropped from state on the next run so stale state cannot produce phantom alerts on re-entry.

### watch_rules.yaml
Default config file at the project root. Two active rules out of the box:
1. Universe-wide conviction siren (`"*"`, `conviction_above`, threshold 0.85, high priority).
2. Universe-wide action-change tracker (`"*"`, `action_change`, default priority).

### Integration in `main.py`
Added inside `run_once()`, immediately after the advisory evaluation loop completes (before Sheet/HTML sinks), wrapped in an outer try/except (CONSTRAINT #6). Always saves state even on quiet runs.

### New settings (`settings.py`)
- `WATCH_RULES_FILE: str = "watch_rules.yaml"` — path to the YAML rule file.

### New env vars
- `WATCH_RULES_FILE` — override the rule-file path (default `"watch_rules.yaml"`).
- `NTFY_DASHBOARD_URL` — optional deep-link URL appended to every watch notification body (e.g. `http://localhost:8501`). Read directly from `os.environ` in `dispatch_watch_alerts`, consistent with the `NTFY_TOPIC` pattern in `alerting.py`.

### Test surface
- **`tests/test_watch_alerts.py`** (60 tests, 7 classes): `TestWatchRule` (frozen, defaults, all fields); `TestWatchAlert` (frozen, fields); `TestSymbolWatchState` (round-trip, from_dict defaults); `TestLoadWatchRules` (missing/malformed/empty/valid/threshold-missing/unknown-alert_on/out-of-range/invalid-priority/uppercase-normalised/multiple/bad-rule-doesnt-block); `TestLoadSaveWatchState` (missing/corrupt/non-object/round-trip/atomic/uppercase-on-load/parent-dir-creation); `TestEvaluateWatchRules` (no-rules/no-recs/action_change-flip/same-action/first-run/conviction-rising-edge/no-spam/reset+refire/first-run-above/first-run-below/conviction-below-falling/no-spam/reset+refire/wildcard-all/wildcard-skip-absent/specific-symbol/bad-rule-resilience/PARTIAL-quality/no-lookahead-structural/multi-rule-independent); `TestDispatchWatchAlerts` (empty-noop/one-per-alert/title/dashboard-url/failure-doesnt-raise/priority); `TestMainPyIntegration` (source guards + settings field + yaml exists).

### Gravity step 56 (`run_watch_alerts_audit`)
14 checks: module importable; frozen dataclasses with required fields; SymbolWatchState round-trip; `load_watch_rules` → `[]` for missing/malformed; valid conviction_above rule parsed; `load_watch_state` → `{}` for missing; action_change fires on HOLD→BUY; conviction_above edge-trigger (no spam); no-lookahead structural verify (market-data monkeypatched); `settings.WATCH_RULES_FILE`; `main.py` source guards; `watch_rules.yaml` exists; `tests/test_watch_alerts.py` exists.

## Tier 1.5 — Plain-English "Why" for Every Recommendation (Expanded) (2026-06)

### Overview
Extends `engine/advisory._build_rationale()` with four institutional-grade narrative sections, gated behind a new `RATIONALE_VERBOSITY` env var. Standard mode (`"standard"`, the default) is a single terse paragraph — unchanged from pre-1.5 behavior. Verbose mode (`"verbose"`) appends four labelled sections immediately after the standard paragraph, separated by a blank line.

### `RATIONALE_VERBOSITY` setting (`settings.py`)
```
RATIONALE_VERBOSITY: str = Field(default="standard", ...)
```
Valid values: `"standard"` (default) | `"verbose"`. Any other value is treated as standard.

### `engine/advisory.py` changes
**`_build_rationale()` extended signature** — all new params are keyword-only with safe defaults so existing call sites are unaffected:
- `hmm_risk_on_probability: Optional[float] = None` — from `macro_dto.hmm_risk_on_probability`
- `vix_value: float = 18.0`, `sahm_rule_indicator: float = 0.0`, `yield_curve: float = 0.50` — macro snapshot for section [A]
- `win_rate_data: Optional[tuple] = None` — `(p, b, n_trades)` pre-computed in `evaluate()` from `TransactionsStore.closed_trades_df()`
- `active_module_docs: Optional[Dict[str, str]] = None` — `{module_name: first_doc_line}` pre-fetched from `signals.registry.global_registry`
- `strategy_explainer_notes: str = ""` — from `strategy_out.get("Strategy Explainer Notes", "")`
- `rsi_2: Optional[float] = None`, `sma_200: Optional[float] = None`, `sector: str = ""` — for section [C] invalidation conditions

**Two new CONFIG entries** (prevent literal magic numbers in logic):
- `"rsi_mean_reversion_exit_level": 35` — RSI(14) flip point for oversold mean-reversion entry
- `"rsi_2_mean_reversion_exit_level": 35` — RSI(2) flip point for ultra-short mean-reversion entry

**`evaluate()` Step 10b** — verbose pre-computation block (inside `if settings.RATIONALE_VERBOSITY == "verbose":`):
1. Calls `estimate_win_rate_and_payoff(closed_trades_df, lookback_trades=100)` on the already-bound `transactions_store`. Sets `_verbose_win_rate = (p, b, n)` when not NaN, else `None`.
2. Lazy-imports `signals.registry.global_registry`, filters by `module.is_active_in_regime(macro_dto)`, extracts first non-boilerplate line of each module's `type(mod).__doc__` into `_verbose_module_docs`. Both steps wrapped in bare `except Exception: pass` per CONSTRAINT #6.

**Four verbose sections (emitted only when `settings.RATIONALE_VERBOSITY == "verbose"`):**

| Label | Content |
|---|---|
| `[A] Regime context` | `{macro_regime} — HMM {confirms/uncertain/risk-off pressure} (p=X.XX). VIX=X, Sahm Rule=X, 10y-2y spread=±X.` |
| `[B] Calibration` | `{p*100:.0f}% win rate over N closed trades (payoff X:1; Kelly edge X — positive/negative).` OR fallback when win_rate_data is None. |
| `[C] Invalidation` | Score flip point, RSI/RSI(2) mean-reversion voids (conditional), VIX/Sahm macro gate tripwires (always), sector-veto conditions (when applicable), SMA-200 trend break (when provided). |
| `[D] Indicator notes` | First-line `__doc__` of ≤4 regime-active signal modules from `global_registry`, title-cased. Omitted entirely when `active_module_docs` is empty. |

**No-lookahead invariant**: `_build_rationale()` contains no I/O. All data (`win_rate_data`, `active_module_docs`, `strategy_explainer_notes`) is gathered by `evaluate()` and passed as arguments.

### New env var
- `RATIONALE_VERBOSITY` — `"standard"` (default) | `"verbose"`. Set in `.env`.

### Test surface
- **`tests/test_rationale_verbosity.py`** (49 tests, 7 classes): `TestSettingsField` (field exists, default "standard"); `TestStandardMode` (no verbose markers, single paragraph, backward-compat for score/regime/macro-gate/dividend text); `TestVerboseModePresence` (standard para still present, [A]/[B]/[C] markers, double-newline separator); `TestRegimeContextSection` (high/mid/low/None HMM → correct prose, VIX and Sahm appear); `TestCalibrationSection` (win% / trade count / payoff ratio / positive/negative edge label / None fallback); `TestInvalidationSection` (BUY score flip, SELL recovery, VIX/Sahm always present, RSI oversold void conditional, RSI-2 void conditional, sector veto for Financials not Technology, SMA-200 void); `TestIndicatorTheorySection` ([D] present with docs, absent with empty/None, title-casing, doc text appears, cap at 4 modules); `TestGracefulDegradation` (all-None does not raise, extreme values don't crash, unknown verbosity string falls back to standard); `TestEndToEndIntegration` (5 end-to-end tests through evaluate() with patched engines: standard no markers, verbose [A/B/C] present, HMM probability in section A, action/conviction unchanged across modes).

### Gravity step 57 (`run_rationale_verbosity_audit`)
10 checks: `settings.RATIONALE_VERBOSITY` exists and defaults to `"standard"`; CONFIG contains both RSI invalidation-level keys; `_build_rationale` signature includes all four verbose-mode kwargs; standard mode produces no [A/B/C/D] markers; verbose mode produces [A/B/C]; HMM ≥ 0.70 → "strongly confirms"; HMM < 0.30 → "risk-off"; `win_rate_data=None` → calibration fallback; sector veto for Financials but not Technology; `tests/test_rationale_verbosity.py` exists.

## Tier 2.1 — Regime-Conditional Signal Weights (2026-06)

### Overview
`SIGNAL_WEIGHTS` is now regime-keyed: per-macro-regime override dicts are merged onto the flat default weights each aggregation cycle. Mean-reversion signals (RSI(2)) can be suppressed in RECESSION/CREDIT EVENT and momentum signals boosted in RISK ON — without any behavioral change when no overrides are configured (fully backward-compatible).

### `resolve_regime_weights()` (`signals/aggregator.py`, module-level)
```python
def resolve_regime_weights(
    market_regime: str,
    regime_weights: Dict[str, Dict[str, float]],
    default_weights: Dict[str, float],
) -> Dict[str, float]:
```
- When `regime_weights` is empty (default): returns `default_weights` unchanged (same object, zero-overhead no-op).
- Exact `market_regime` match → `{**default_weights, **override}` (merge; unlisted keys inherit default).
- Falls back to `regime_weights["_default"]` when no exact match.
- Unknown regime with no `_default` → returns `default_weights` unchanged.
- `SignalAggregator.aggregate()` calls this once per cycle; effective weights replace `self.weights.get(name)` in the module loop.

### `settings.REGIME_SIGNAL_WEIGHTS: dict[str, dict[str, float]]`
Default `{}` (empty — flat weights, identical to pre-Tier-2.1). Configure in `.env` as JSON:
```
REGIME_SIGNAL_WEIGHTS={"RECESSION": {"rsi2_mean_reversion": 0.0, "macro_regime": 60.0}, "RISK ON": {"timeseries_momentum": 40.0}}
```
The `_default` key acts as a catch-all for unmapped regimes.

### Tests
- **`tests/test_regime_weights.py`** (33 tests): empty override returns same object, exact match applies overrides + inherits defaults, `_default` fallback, unknown-regime-no-default returns defaults, merge does not mutate inputs, new keys can be added, RECESSION suppresses rsi2, RISK ON boosts momentum.

### Gravity step 58 (`run_regime_weights_audit`)
8 checks: `resolve_regime_weights` importable; empty dict returns defaults unchanged; RECESSION override applies + inherits uninvolved keys; `_default` fires for unmapped regime; no-match-no-default returns defaults; `settings.REGIME_SIGNAL_WEIGHTS` defaults to `{}`; `SignalAggregator.aggregate` docstring references regime weights; `tests/test_regime_weights.py` exists.

## Tier 2.2 — Forecast Ensemble Weighted by Recent Skill (2026-06)

### Overview
Replaces the static hardcoded blend ratios (`lstm*0.4 + arima*0.2 + mc*0.4`) in `ForecastingEngine.generate_forecast()` with inverse-RMSE weighting from a SQLite-backed skill tracker. Forecast prices are recorded each run and compared to actual prices once the horizon elapses; the model with the lowest recent RMSE gets the highest ensemble weight. Cold-start (< 30 completed observations per model) falls back to equal weights, and the entire tracker is optional — passing no `tracker` to `ForecastingEngine.__init__` reproduces the original static blending exactly.

### New package: `forecasting/`
- **`forecasting/__init__.py`** — package marker; re-exports `ForecastTracker`.
- **`forecasting/forecast_tracker.py`** — `ForecastTracker` class. SQLite table `forecast_errors` (columns: `id`, `symbol`, `model_name`, `horizon_days`, `forecast_ts`, `forecast_price`, `actual_price`, `squared_error`, `recorded_at`). Public API:
  - `record_forecasts(symbol, horizon_days, model_prices: dict[str, float], forecast_ts)` — inserts per-model prices (skips 0/negative).
  - `update_actuals(symbol, horizon_days, actual_price, as_of, tolerance_days=5) -> int` — matches past forecasts with realized prices. The 5-day tolerance absorbs weekends/holidays.
  - `get_skill_weights(symbol, horizon_days, window_days=60, min_obs=30) -> dict[str, float]` — returns normalized inverse-RMSE weights. Empty dict when no history. Cold-start equal weights when any model has < `min_obs` completed rows.
  - `pending_count(symbol, horizon_days) -> int` / `completed_count(symbol, horizon_days, window_days=60) -> int` — monitoring helpers.
  - All methods wrapped in try/except; DB failure → returns `{}` / `0` / `None`, never raises (CONSTRAINT #6).
  - `_MIN_RMSE = 0.01` — floor to prevent infinite weight on a perfect model.
  - WAL journal mode for concurrent read-write safety.

### `ForecastingEngine` changes (`forecasting_engine.py`)
- `__init__(self, tracker=None)` — accepts optional `ForecastTracker`; stores as `self._tracker`. Non-`ForecastTracker` values silently ignored (sets `_tracker=None`).
- `_blend_with_skill(model_forecasts, skill_weights, preferred_model, current_price) -> float` — static method. When `skill_weights` is non-empty, computes weighted average over the intersection of `model_forecasts` and `skill_weights` keys (renormalized). Falls back to original static sector-preference blending when `skill_weights={}`.
- `generate_forecast()` — tracker lifecycle integrated into the `for h in horizons:` loop:
  1. Before the loop: `update_actuals()` for all horizons (fills in past errors).
  2. Per horizon: `get_skill_weights()` → `_blend_with_skill()` → `record_forecasts()`.

### New settings
- **`FORECAST_SKILL_WINDOW_DAYS: int = 60`** — rolling window for RMSE computation.
- **`FORECAST_SKILL_MIN_OBS: int = 30`** — minimum completed rows per model before skill weighting activates (cold-start below this).

### Tests
- **`tests/test_forecast_tracker.py`** (56 tests): table/index creation, DDL column coverage, `record_forecasts` (positive/zero/negative/uppercase), `update_actuals` (past-due, recent, tolerance boundary, idempotency, squared_error value), `get_skill_weights` (empty history, cold-start, warm-path ordering, weights sum to 1, `_MIN_RMSE` guard, old-row window exclusion, DB error), `pending_count`/`completed_count`, `ForecastingEngine` init/tracker kwarg, `_blend_with_skill` (skill path, normalization, static fallback, empty forecasts, single-model restriction, HW preferred).

### Gravity step 59 (`run_forecast_skill_audit`)
10 checks: `ForecastTracker` importable; DDL contains all required columns; cold-start returns equal weights; warm-path inverse-RMSE makes better model higher-weight; `_MIN_RMSE > 0`; `ForecastingEngine.__init__` accepts `tracker` kwarg; `_blend_with_skill` is callable; `settings.FORECAST_SKILL_WINDOW_DAYS` and `FORECAST_SKILL_MIN_OBS` exist and are > 0; `forecasting/__init__.py` re-exports `ForecastTracker`; `tests/test_forecast_tracker.py` exists.

## Tier 2.4 — Sentiment / News Catalyst Signal (2026-06)

### Overview
`signals/news_catalyst.py` adds a `NewsCatalystSignal` that scores headlines via FinBERT (optional) or a built-in keyword lexicon, then gates the signal by earnings proximity. Runs as a standard pluggable `SignalModule` with weight 10.0 and uses the two-phase `pre_compute` / `compute` hook pattern.

### `signals/news_catalyst.py` (new)
- **`NewsCatalystSignal`** — `SignalModule` subclass. `name = "news_catalyst"`. Auto-registers via `global_registry.register(NewsCatalystSignal())` at module end; triggered by `import signals.news_catalyst` added to `signals/__init__.py`.
- **`pre_compute(universe_df, context)`** — batch-fetches Finnhub `company_news` + `earnings_calendar` for every symbol (short-circuits when `FINNHUB_API_KEY` is absent). Populates `self._news_scores`, `self._earnings_dt` (instance cache) AND `context.news_sentiment_scores`, `context.earnings_dates` (new `SignalContext` fields). Courtesy `time.sleep(0.12)` per symbol ≈ 8 calls/s, under the 60/min free-tier ceiling.
- **`compute(row, context)`** — reads `self._news_scores`; returns `SignalOutput(score=0.0)` when no data (dead-letter resilient, CONSTRAINT #6).
- **FinBERT path**: lazy process-level singleton `_FINBERT_PIPELINE` (loaded once via `transformers.pipeline("sentiment-analysis", model="ProsusAI/finbert")`; `_FINBERT_LOAD_ATTEMPTED` flag prevents repeated failures). Maps `positive/negative/neutral` labels to `+confidence/-confidence/0`. Disabled via `settings.FINBERT_ENABLED=False`.
- **Lexicon fallback**: `_POSITIVE_WORDS` and `_NEGATIVE_WORDS` frozensets (~80 words). `_lexicon_sentiment(headline)` = `(pos − neg) / max(1, pos + neg)` ∈ [-1, 1]; tokenises by `.lower().split()` and strips `".,!?;:\"'()[]"`.
- **`_earnings_proximity_multiplier(next_earnings, now, suppress_hours, dampen_days)`**: 0.0 within `suppress_hours` (default 48 h — zero weight near earnings), 0.5 within `dampen_days` (default 7 d — half weight approaching earnings), 0.5 for 24 h post-earnings (fresh noise), 1.0 beyond.

### `signals/base.py` changes
Two new `SignalContext` fields:
- `news_sentiment_scores: Dict[str, float] = field(default_factory=dict)`
- `earnings_dates: Dict[str, str] = field(default_factory=dict)`

### `config.py` changes
Three new `COLUMN_SCHEMA` entries (advisory signals section):
```python
{"header": "News Sentiment", "key": "News_Sentiment",       "format": "number"},
{"header": "Earnings Date",  "key": "Earnings_Date",        "format": "string"},
{"header": "Cluster",        "key": "Correlation_Cluster",  "format": "number"},
```

### `main_orchestrator.py` writeback
After the multifactor writeback block, reads `shared_context.news_sentiment_scores` / `context.earnings_dates` and writes `dashboard_df['News_Sentiment']` / `dashboard_df['Earnings_Date']` via `.map()`. Always initialises `dashboard_df['Correlation_Cluster'] = float('nan')` (on-demand GUI only).

### New settings / env vars
- **`NEWS_LOOKBACK_DAYS: int = 7`** — Finnhub `company_news` fetch window.
- **`FINBERT_ENABLED: bool = True`** — toggles neural vs. lexicon path.
- **`NEWS_EARNINGS_SUPPRESS_HOURS: float = 48.0`** — zero-weight window near earnings.
- **`NEWS_EARNINGS_DAMPEN_DAYS: float = 7.0`** — half-weight window before earnings.
- Env vars `FINNHUB_API_KEY` and `NTFY_DASHBOARD_URL` already existed; no new secrets.
- Optional dep: `transformers>=4.35.0` in `requirements.txt` (PyTorch/TF backend required); `ImportError` → lexicon fallback automatically, never a crash.

### Tests
- **`tests/test_news_catalyst.py`** (46 tests, 8 classes): `TestLexiconSentiment`, `TestEarningsProximity`, `TestScoreHeadline`, `TestSignalCompute`, `TestPreCompute`, `TestRegistration`, `TestFetchHelpers`, `TestSettings`. All Finnhub/transformers calls monkeypatched.

### Gravity step 60 (`run_news_catalyst_audit`)
10 checks: importable; `name == "news_catalyst"`; FinBERT helper returns `None` safely when unavailable; lexicon positive headline > 0 and negative < 0; suppress within 48 h → 0.0; dampen within 7 d → 0.5; `pre_compute` populates `context.news_sentiment_scores` and `context.earnings_dates`; no `FINNHUB_API_KEY` → no crash; `"news_catalyst"` in `settings.SIGNAL_WEIGHTS`; `tests/test_news_catalyst.py` exists.

## Tier 2.5 — Correlation Cluster Awareness (2026-06)

### Overview
`research_engine.compute_correlation_clusters()` uses hierarchical Ward-linkage clustering on the Lopez de Prado distance metric `d = sqrt(0.5 * (1 − ρ))` to label every symbol with a cluster ID. Computed **on-demand** in the GUI Reports tab (not in the main pipeline) because clustering requires simultaneous returns for all symbols at once, which is incompatible with the orchestrator's per-symbol loop.

### `research_engine.py` additions (module-level)
- **`compute_correlation_clusters(returns_df, distance_threshold=0.4, min_obs=20) -> Tuple[Dict[str, int], pd.DataFrame]`**
  - `returns_df`: columns = symbols, index = dates. Symbols with < `min_obs` valid rows get `cluster_id = 0` (excluded; CONSTRAINT #4 — never fabricates a cluster label).
  - Converts correlation matrix to distance via `d = sqrt(0.5 * (1 − ρ))`, then calls `scipy.cluster.hierarchy.linkage(method='ward')` + `fcluster(criterion='distance', t=distance_threshold)`.
  - Returns `(labels: Dict[str, int], cluster_summary: pd.DataFrame)`. `cluster_summary` columns: `cluster_id`, `symbols` (list), `n_symbols`, `avg_intra_corr` (NaN for singletons — never fabricated).
  - Returns `({}, empty DataFrame with correct schema)` on any fatal error (CONSTRAINT #6).
- **`fetch_returns_for_clustering(symbols, lookback_days=60) -> pd.DataFrame`** — fetches yfinance daily closes (lazy `import yfinance as yf` inside body so tests patch `yfinance.download` directly), returns `pct_change()`. Returns empty DataFrame on error or empty symbol list.

### GUI: Reports tab (`gui/panels.py`)
- **`_render_correlation_cluster_section(signals)`** — inserted in `render_report_viewer()` before `_render_decision_journal_section`. UI: lookback slider (30–250 d), threshold slider (0.05–1.5), "Compute Clusters" on-demand button (CONSTRAINT #5). Results stored in `st.session_state["cluster_labels"]` and `st.session_state["cluster_summary"]`; renders a symbol–cluster assignment table and a per-cluster aggregate position % concentration table with `>30%` warning.

### New settings / env vars
- **`CORRELATION_CLUSTER_LOOKBACK_DAYS: int = 60`**
- **`CORRELATION_CLUSTER_THRESHOLD: float = 0.4`**

### Tests
- **`tests/test_correlation_clusters.py`** (27 tests, 7 classes): `TestComputeCorrelationClusters` (7 tests — known correlated A/B share cluster; uncorrelated C separate; all symbols assigned; IDs positive int; all-correlated single cluster; empty/None → empty), `TestDistanceThreshold` (3), `TestSummaryDataFrame` (6), `TestEdgeCases` (4 — single symbol; all-NaN col; insufficient obs; two symbols), `TestFetchReturnsHelper` (3 — patches `yfinance.download` directly), `TestSettings` (7 — defaults, COLUMN_SCHEMA entries). `compute_correlation_clusters` is a pure function; no mocking required.

### Gravity step 61 (`run_correlation_cluster_audit`)
10 checks: `compute_correlation_clusters` importable; `fetch_returns_for_clustering` importable; known-correlated A/B share cluster; uncorrelated C in different cluster; empty DataFrame → empty labels+summary; `Correlation_Cluster` in `COLUMN_SCHEMA`; `CORRELATION_CLUSTER_LOOKBACK_DAYS == 60`; `CORRELATION_CLUSTER_THRESHOLD == 0.4`; insufficient obs symbol gets `cluster_id = 0`; `tests/test_correlation_clusters.py` exists.

## Task 3 — Operator Ergonomics (2026-06)

### 3.1 Daily Briefing Digest (`scripts/daily_briefing.py`)
- **`generate_briefing(output_dir) -> str`** — assembles a full Markdown briefing with five sections: Macro Regime, Top 3 Actions, Δ Since Last Run, Dead-Lettered Symbols, and 30-Day Calibration. Each section wraps in try/except — CONSTRAINT #6. No live network calls.
- **`write_briefing(output_dir) -> Path`** — writes to `output/briefing_YYYY-MM-DD.md` via atomic create + mkdir. Returns the path; never raises.
- **`main(argv)`** — CLI entry point: `python -m scripts.daily_briefing [--print] [--output-dir DIR]`. `--print` echoes the briefing to stdout after writing.
- **Wire-up in `launch.command`**: appended `python -m scripts.daily_briefing --print || true` as the final step so every launch (single-run and interval-mode) ends with a briefing printed to the Terminal window.
- **Section helpers (all `_section_*`)**: `_section_regime` reads `state_snapshot.json` for regime/VIX/HMM; `_section_top_actions` sorts signals by action priority (BUY > HOLD > SELL) then conviction; `_section_delta` calls `scripts.snapshot_diff.compute_diff_from_history`; `_section_dead_letters` reads `output/dead_letter.json` via `gui.dead_letter.read_dead_letter`; `_section_calibration` calls `evaluation_engine.calibration_curve` + `TransactionsStore` (both imported lazily inside the function — CONSTRAINT #7).
- **Dead-letter tolerant**: every section degrades gracefully to a "No data yet" placeholder. First-ever run (no history, no dead_letter.json) still produces a valid briefing.

### 3.2 Mobile-Friendly Daily Report
- Added a `@media (max-width: 600px)` responsive CSS block to `HTML_REPORT_TEMPLATE` in `diagnostics_and_visuals.py`, just before the closing `</style>` tag.
- **No new dependency** — pure CSS addition to the embedded template.
- Behaviour: single-column `exec-grid`; two-column `summary-band`; `overflow-x: auto` on the signals table so it scrolls horizontally rather than overflowing; `min-height: 44px` on `tr.data-row td` and `th` for WCAG 2.5.5 touch-target compliance; reduced font sizes and padding for narrow viewports.

### 3.3 Secrets-Rotation Reminder
- **`settings.FRED_KEY_ROTATED_DATE: Optional[str]`** (default `None`) — ISO date (YYYY-MM-DD) recording when `FRED_API_KEY` was last rotated. Set after generating a new key at https://fred.stlouisfed.org/docs/api/api_key.html.
- **`scripts/preflight_check.check_key_rotation_recent(max_age_days=90) -> CheckResult`** — **warning-only, never blocking**. Three outcomes: (a) `FRED_KEY_ROTATED_DATE` unset → warning to start tracking; (b) key rotated within `max_age_days` → clean PASS; (c) key older → warning citing age + rotation URL. `ALPACA_KEY_ROTATED_DATE` is intentionally **not** checked (paper keys have no blast radius in advisory mode).
- Wired into `ALL_CHECKS` as check #2, immediately after `check_fred_key_configured` and before `check_advisory_only_active`.
- **New env var**: `FRED_KEY_ROTATED_DATE` (ISO date, optional). Add to `.env.example`.

### 3.4 "Quick Add to Watchlist" GUI
- `render_live_inventory()` in `gui/panels.py` now includes a text input + "➕ Add to watchlist" button between the Robinhood snapshot fetch and the Sync Now buttons.
- **File-only**: writes to `watchlist.txt` (repo root), never to `.env` or via `gui.env_io.write_setting`. This avoids GUI-induced env churn and keeps the operator's watchlist editable in a plain text file.
- **Deduplication**: reads existing non-comment lines from `watchlist.txt` before appending — silently skips if the ticker is already present, showing an `st.info` instead.
- **Validation**: ticker is normalized to uppercase; rejects empty input or symbols that don't match `[A-Z0-9.-]` after normalization.
- **Picked up automatically** by `main.py._load_watchlist()` on the next `run_once()` call (no restart needed).
- **No new settings / env vars** (file path is hardcoded to `watchlist.txt` at the repo root, consistent with `main.py`'s `WATCHLIST_FILE` constant).

### Tests
- **`tests/test_operator_ergonomics.py`** (45 tests, 5 classes):
  - `TestDailyBriefingImport` (4) — module importable, callables exist.
  - `TestBriefingSections` (11) — regime/VIX/kill-switch text, top-actions ordering, dead-letter read, calibration MAE rendering.
  - `TestGenerateBriefing` (5) — never raises, required headers, snapshot wired, file creation, date in filename.
  - `TestMobileResponsiveCSS` (5) — `@media` present, 600px breakpoint, 44px tap targets, 1fr grid collapse, `overflow-x:auto`.
  - `TestKeyRotationCheck` (11) — unset/fresh/stale/invalid/boundary, warning-only invariant, check ordering, never-False, Alpaca keys not touched, Settings field, not in SECRET_KEYS.
  - `TestWatchlistQuickAdd` (7) — append, dedup, comment skip, uppercase, file creation, source guards.

### Gravity step 63 (`step_63_operator_ergonomics_audit`)
10 checks: `scripts.daily_briefing` importable + `generate_briefing` callable; `generate_briefing` returns Markdown with regime section; `write_briefing` produces `briefing_YYYY-MM-DD.md`; `HTML_REPORT_TEMPLATE` has `@media (max-width: 600px)` block; mobile CSS has 44px tap target + `overflow-x:auto`; `check_key_rotation_recent` in `ALL_CHECKS`; `check_key_rotation_recent` is always warning-only (never `passed=False`); `Settings.FRED_KEY_ROTATED_DATE` declared; `render_live_inventory` references `watchlist.txt` quick-add; `tests/test_operator_ergonomics.py` exists.

## Tier 4 — Validation & Honesty (2026-06)

### 4.1 Live-vs-Recommendation Tracking

"If you'd taken every BUY at the published conviction-weighted size, the paper-equivalent return over 30 days would be X%; actual decisions returned Y%." Measures whether operator judgement adds or subtracts alpha relative to the raw model signal.

#### `evaluation_engine.recommendation_tracking_report()` (module-level function)
```python
recommendation_tracking_report(
    log_path: Optional[Path] = None,
    transactions_store=None,
    horizon_days: int = 30,
    *,
    historical_store=None,
    _today=None,          # injectable for tests
) -> Dict[str, Any]
```
- Reads the 1.3 decision log (`output/decision_log.jsonl`) via `gui.decision_log.read_decisions()` (lazy import — no circular import).
- Filters to entries where `signal_action` contains `"BUY"` (catches `"STRONG BUY"` too).
- For each BUY entry computes:
  - **Model price**: `HistoricalStore.get_bars(symbol, lookback_days=756)` → `_price_at_or_before(bars, signal_dt)` for entry price and `_price_at_or_before(bars, exit_dt)` for exit price (where `exit_dt = signal_date + timedelta(days=horizon_days)`).
  - **Model return**: `(exit − entry) / entry` when `completed` (i.e. `exit_date <= today`) and both prices are available; `NaN` otherwise — **CONSTRAINT #4, never fabricated**.
  - **Actual return**: for `action_taken=="acted"` entries with a `trade_id`, reads `TransactionsStore.get_trade_history(symbol)` for `entry_price` and `exit_price`. When `exit_ts` is `None` (still open), uses the latest available bar close as a surrogate (still reported as `n_with_exit++`).
- Aggregation:
  - `model_return_30d` = conviction-weighted mean of all completed BUY signals that have a model return.
  - `operator_return_30d` = simple mean of actual returns for `action_taken=="acted"` with a closed or surrogate exit.
  - `delta` = `operator_return_30d − model_return_30d` (positive = operator adds value). `NaN` when either is unavailable.
- Returns a dict with keys: `rows` (per-signal breakdown), `model_return_30d`, `operator_return_30d`, `delta`, `n_signals`, `n_acted`, `n_completed`, `n_with_exit`, `horizon_days`.
- Module-level constants exported for tests: `_TRACKING_EMPTY` (sentinel), `_DEFAULT_DECISION_LOG_PATH`.
- Helper function `_price_at_or_before(bars: pd.DataFrame, target: datetime) -> float` — slices bars to last Close ≤ target date; `NaN` on empty/no-match.
- All I/O in try/except; dead-letter tolerant (CONSTRAINT #6). HistoricalStore is imported lazily (inside function body) to avoid circular imports.

#### GUI: Reports tab (`gui/panels.py`)
- **`_render_recommendation_tracking_section()`** — inserted in `render_report_viewer()` between `_render_decision_journal_section()` and `_render_brinson_fachler_section()`.
- Horizon slider (5–90 days, default 30, session-key `rec_tracking_horizon`).
- `@st.cache_data(ttl=300)` wraps the `recommendation_tracking_report()` call.
- Four KPI columns: BUY Signals Logged / Model {N}d Return / Operator Return / Delta (Δ).
- Plain-English narrative block summarizing whether the operator added or subtracted alpha.
- Expandable per-signal breakdown table.
- Fully wrapped in try/except (CONSTRAINT #6).

#### Tests
- **`tests/test_recommendation_tracking.py`** (≥ 30 tests, 8 classes):
  - `TestEmptyLog` — missing/empty/corrupt log, horizon preserved in result.
  - `TestNoBuySignals` — HOLD/SELL entries not counted; STRONG BUY counted.
  - `TestModelReturn` — correct model return from synthetic bars; NaN when no bars.
  - `TestActualReturn` — closed trade → correct actual_return; open trade → surrogate exit; missing trade_id → NaN.
  - `TestPassedSignal` — "passed" counted in n_signals but not n_acted; still included in model return.
  - `TestHorizonNotElapsed` — recent signal → n_completed=0; completed flag per-row.
  - `TestConvictionWeighting` — high-conviction winner + low-conviction loser → positive weighted result.
  - `TestDelta` — delta = operator − model; NaN when only model available.
  - `TestDeadLetterResilience` — HistoricalStore/TransactionsStore failures degrade gracefully.
  - `TestModuleSurface` — importable; sentinel structure; Path type; `_price_at_or_before` corner cases.

#### Gravity step 64 (`step_64_recommendation_tracking_audit`)
10 checks: `recommendation_tracking_report` importable; `_TRACKING_EMPTY` has all 9 required keys; `_DEFAULT_DECISION_LOG_PATH` is `pathlib.Path`; `_price_at_or_before(empty, now)` returns NaN (CONSTRAINT #4); missing log → n_signals=0 and all returns NaN; passed BUY → n_signals=1, n_acted=0; recent signal (5 days, horizon=30) → n_completed=0; HistoricalStore failure degrades gracefully (CONSTRAINT #6); `gui/panels.py` references `recommendation_tracking_report`; `tests/test_recommendation_tracking.py` exists.

---

### 4.2 Walk-Forward Validation Cadence

Monthly runner that re-validates every registered strategy against recent history to ensure validation reports never go stale.

#### `scripts/refresh_validations.py` (new module, runnable as `python -m scripts.refresh_validations`)
- **Strategy adapters** — pure functions `adapter_fn(spy_close: pd.Series) -> (X, y, precomputed)`:
  - `_build_rsi2_adapter(spy_close)` — mirrors `tests/test_validation_rsi2.py`; RSI(2) + SMA-200 long-only trend filter + crash/recession RISK-OFF gate; returns `X[RSI_2, SMA_200]`, `y=daily_ret`, `precomputed={RSI2_Gated, RSI2_Ungated}`.
  - `_build_tsmom_adapter(spy_close)` — mirrors `tests/test_validation_ts_momentum.py`; four variants (12M/6M × 10%/20% vol target); returns `X[ROC_12M, ROC_6M, Vol]`, `y=daily_ret`, `precomputed` dict with 4 series.
- **`_make_strategy_fn(precomputed, turnover)`** — closure returning a `StrategyValidationHarness`-compatible `strategy_fn(X_train, y_train, X_test, y_test) -> list[dict]` where each dict has `params`, `train_returns`, `test_returns`, `turnover`.
- **`STRATEGY_REGISTRY: Dict[str, Tuple[Callable, float]]`** — maps `strategy_id → (adapter_fn, turnover)`; currently contains `"rsi2_mean_reversion"` and `"timeseries_momentum"`.
- **`_download_spy(start_date, end_date)`** — downloads via `yfinance` (same library as existing test harnesses); raises `RuntimeError` on empty result.
- **`run_validations(strategies, start_date, end_date, output_dir, n_cpcv_splits, n_test_splits)`** — downloads SPY once, loops over strategies, runs `StrategyValidationHarness`, saves JSON summaries. Per-strategy failure → dead-letter entry with `deployable=False` and `error` key (CONSTRAINT #6). Returns `Dict[strategy_id, summary_dict]`.
- **`_print_summary_table(results)`** — ASCII pass/fail table to stdout.
- **`main(argv)`** — argparse CLI; exit code 0 = all pass, 1 = any failure. Flags: `--strategies`, `--start`, `--end`, `--output-dir`, `--n-cpcv-splits`, `--n-test-splits`.

#### `scripts/refresh_validations.sh` (new, executable)
Bash wrapper that verifies `.venv` exists and Python is 3.12.x, then runs `python3 -m scripts.refresh_validations "$@"`. Designed for cron scheduling:
```
0 6 1 * * cd /path/to/stockpy && ./scripts/refresh_validations.sh >> logs/validations.log 2>&1
```

#### Design constraints (CONSTRAINT #4, #6, #7)
- No fabricated synthetic returns passed to the harness — if an adapter cannot build valid X/y (insufficient history), the strategy is dead-lettered with an error.
- Data fetching uses yfinance — no new data providers.
- Each strategy wrapped in try/except so one failure never aborts the run.

#### Tests
- **`tests/test_refresh_validations.py`** (≥ 40 tests, 7 classes):
  - `TestModuleSurface` — importable, public callables exist.
  - `TestRegistryStructure` — STRATEGY_REGISTRY shape, entries are (callable, positive float), turnover in range.
  - `TestBuildRsi2Adapter` — returns 3-tuple; X has RSI_2/SMA_200; X and y share index; precomputed keys; SMA-200 warmup trimmed; RSI bounded [0, 100].
  - `TestBuildTsmomAdapter` — returns 3-tuple; X has ROC_12M/ROC_6M/Vol; 4 precomputed variants; variant names contain "TSMOM\_".
  - `TestMakeStrategyFn` — returns callable; result is list; required keys present; turnover propagated; one result per precomputed series.
  - `TestRunValidations` — returns dict; unknown strategy dead-lettered; SPY download failure marks all as failed; adapter exception dead-lettered; single-strategy filter.
  - `TestMainCLI` — all-pass → exit 0; any-fail → exit 1; error entry → exit 1; `--strategies` forwarded; `--start`/`--end` forwarded; `--n-cpcv-splits` forwarded.

#### Gravity step 65 (`step_65_refresh_validations_audit`)
10 checks: `scripts.refresh_validations` importable; `STRATEGY_REGISTRY` contains both strategies; entries are (callable, positive turnover); RSI(2) adapter returns (X with RSI_2/SMA_200, y, precomputed); TSMOM adapter returns 4 variants; `_make_strategy_fn` closure returns list with required harness keys; unknown strategy dead-lettered (CONSTRAINT #6); main exit-code 0 on all-pass / 1 on any-fail; `scripts/refresh_validations.sh` exists and is executable; `tests/test_refresh_validations.py` exists.

## Tier 6 — Autonomous Advisory Agent (2026-06)

### Overview
"Robinhood agent trader" option 2: a self-pacing loop that wraps `main.run_once()` with adaptive cadence, actionable-backlog reminders, and persistent state. **ADVISORY ONLY** — no order-submission code, no broker imports. Composes on top of the existing `engine/advisory.evaluate()` per-symbol path, `alerting.notify()` ntfy channel, `watch_engine` per-cycle alerts, and `gui/decision_log.py` operator decision tracking.

### New module: `engine/advisory_agent.py`
Headless, dependency-free policy layer (stdlib + `zoneinfo` only). Public API:
- **`AgentState`** — mutable dataclass: `cycle_count`, `last_cycle_iso`, `last_error_count`, `consecutive_error_cycles`, `backlog: dict[str, BacklogEntry]`, `last_summary_iso`. Round-trips via `.to_dict()` / `.from_dict()`.
- **`BacklogEntry`** — frozen dataclass: `symbol`, `action` (BUY/SELL only — HOLD never enters), `conviction`, `first_seen_iso`, `last_pinged_iso`, `reminders_sent`.
- **`BacklogReminder`** — frozen dataclass: one reminder ready for `alerting.notify()`. Carries `tier` (1/2/3), `age_hours`, `priority`, `title`, `message`.
- **`is_us_market_open(now_utc) -> bool`** — NYSE RTH 09:30–16:00 ET Mon–Fri. Holiday calendar NOT applied (would require `pandas_market_calendars`); operator owns the half-day judgement.
- **`is_extended_hours(now_utc) -> bool`** — 04:00–20:00 ET weekday window (RTH is a strict subset).
- **`compute_next_run_delay(now_utc, *, state, vix, market_regime, config=None) -> int`** — adaptive cadence policy. Decision tree, first match wins:
  1. **Error back-off** — `consecutive_error_cycles > 0` → `min(base * N, max)`.
  2. **Open/close 30-min boost** — inside RTH AND within `rth_open_close_window_minutes` of either boundary → `rth_open_close_delay_s` (default 60 s).
  3. **High-vol RTH** — inside RTH AND (`vix ≥ vol_spike_vix_threshold` OR `market_regime in high_vol_regimes`) → `rth_high_vol_delay_s` (default 120 s).
  4. **Normal RTH** — `rth_normal_delay_s` (default 300 s).
  5. **Extended hours** — `extended_hours_delay_s` (default 1 h).
  6. **Off-hours / weekend** — `off_hours_delay_s` (default 4 h).
  Always clamped ≥ `min_delay_s` (default 60 s) to prevent hot-looping the yfinance API.
- **`update_backlog(state, recommendations, decision_log_entries, now_utc) -> AgentState`** — three-stage update (in place): INSERT high-conviction BUY/SELL recommendations; CLEAR entries whose symbol has a matching "acted" `decision_log` record dated after `first_seen_iso`; EXPIRE entries older than `backlog_expiry_hours` (default 72 h). Conviction threshold = `backlog_conviction_threshold` (default 0.85, mirrors `watch_rules.yaml`'s default siren).
- **`compute_backlog_reminders(state, now_utc) -> list[BacklogReminder]`** — walks each backlog entry against `backlog_tier_hours` ladder (default 1 h / 4 h / 24 h); emits AT MOST one reminder per entry per call (the highest tier crossed since the last dispatch). Capped at `backlog_max_reminders` (default 3) per `(symbol, action)`.
- **`apply_reminder_dispatch(state, reminders, now_utc) -> AgentState`** — advances `last_pinged_iso` + `reminders_sent` for every reminder that was dispatched. Call AFTER `dispatch_backlog_reminders`.
- **`process_run_result(state, run_result, now_utc) -> AgentState`** — bumps `cycle_count`, sets `last_cycle_iso`, advances or resets `consecutive_error_cycles` based on `run_result.errors`. Pure with respect to wall-clock.
- **`dispatch_backlog_reminders(reminders, *, dashboard_url=None) -> None`** — mirrors `watch_engine.dispatch_watch_alerts` contract: per-reminder try/except, no-op when `NTFY_TOPIC` unset, optional dashboard URL appended to message body. Imports `alerting.notify` inline.
- **`load_agent_state(path) / save_agent_state(state, path)`** — atomic write-then-rename (same pattern as `watch_engine.save_watch_state`). Missing / corrupt / empty file → fresh `AgentState()` (CONSTRAINT #6 — never raises). Save failures logged at WARNING and swallowed.

### CONFIG (`engine.advisory_agent.CONFIG`)
Single source of truth for every threshold. No magic numbers in the logic functions. Keys:
| Key | Default | Purpose |
|---|---|---|
| `rth_normal_delay_s` | 300 | Midday RTH refresh cadence |
| `rth_high_vol_delay_s` | 120 | RTH under VIX spike / risk-off regime |
| `rth_open_close_delay_s` | 60 | RTH inside open/close 30-min window |
| `rth_open_close_window_minutes` | 30 | Half-width of the boost windows |
| `extended_hours_delay_s` | 3600 | Premarket / aftermarket weekday |
| `off_hours_delay_s` | 14400 | Overnight / weekend heartbeat |
| `error_backoff_base_s` | 60 | Linear back-off step |
| `error_backoff_max_s` | 900 | Back-off ceiling |
| `vol_spike_vix_threshold` | 25.0 | VIX threshold for high-vol cadence |
| `high_vol_regimes` | `("RISK OFF", "RECESSION", "CREDIT EVENT")` | Regimes that also trigger high-vol cadence |
| `min_delay_s` | 60 | Cadence floor — never ping faster |
| `backlog_conviction_threshold` | 0.85 | Min conviction to enter backlog |
| `backlog_tier_hours` | `(1.0, 4.0, 24.0)` | Reminder escalation ladder |
| `backlog_tier_priorities` | `("default", "high", "high")` | Per-tier ntfy priority |
| `backlog_max_reminders` | 3 | Per-`(symbol, action)` reminder cap |
| `backlog_expiry_hours` | 72.0 | Silent drop after this |
| `decision_log_match_window_hours` | 24.0 | Window for matching `decision_log` "acted" entries |

### `main.py --agent` flag
New CLI flag that replaces `--interval N`'s fixed timer with the agent policy. Takes precedence over `--interval`. Loop (per cycle):
1. Run `run_once()` (same code path as `--interval`; preserves all existing watch_engine + ntfy + sheet + HTML report behaviour — the agent layer adds, never replaces).
2. `process_run_result` → update cycle count and error streak.
3. Read fresh `gui/decision_log.read_decisions()` entries.
4. `update_backlog` — insert new high-conviction signals, drop actioned/expired entries.
5. `compute_backlog_reminders` + `dispatch_backlog_reminders` + `apply_reminder_dispatch`.
6. `save_agent_state` (always — even on a failed cycle).
7. `compute_next_run_delay` with `vix` + `market_regime` sourced from the just-written `output/state_snapshot.json` (via `_read_macro_snapshot_hint` — never re-hits FRED).
8. Sleep in 1 s slices so SIGINT/SIGTERM are caught promptly.

`_run_agent_loop` is a module-level helper in `main.py`; the agent module is imported lazily inside it so test imports of `main.py` stay cheap.

### Persistent state
`output/agent_state.json` — atomic write-then-rename. Schema:
```json
{
  "cycle_count": 42,
  "last_cycle_iso": "2026-06-30T18:30:00+00:00",
  "last_error_count": 0,
  "consecutive_error_cycles": 0,
  "backlog": {
    "AAPL:BUY": {
      "symbol": "AAPL", "action": "BUY", "conviction": 0.91,
      "first_seen_iso": "2026-06-30T14:00:00+00:00",
      "last_pinged_iso": "2026-06-30T15:00:00+00:00",
      "reminders_sent": 1
    }
  },
  "last_summary_iso": ""
}
```
Corrupt or missing file → fresh `AgentState()` on next launch (CONSTRAINT #6).

### Composition rules
- The agent layer **never** mutates `engine.advisory.evaluate()`'s output — it observes recommendations + decision log, derives a backlog, and emits ntfy reminders. The advisory pipeline is unchanged.
- The agent layer **never** imports `execution/*` or any broker module — `ADVISORY_ONLY=True` is the project default and the agent stays inside that quarantine (CONSTRAINT enforced by Gravity step 69 check 13, source-grepping for `submit_order` / `place_order` / etc.).
- The watch_engine and the agent fire INDEPENDENTLY each cycle: watch_engine is edge-triggered (action flips, conviction crossings); the agent is time-based (escalating reminders). They are complementary, not redundant.

### Tests
**`tests/test_advisory_agent.py`** (52 tests, 8 classes): `TestMarketHours` (RTH bounds + weekend + extended-hours window + naive→UTC promotion); `TestCadence` (RTH normal / open boost / close boost / high-vol VIX / high-vol regime / extended / off-hours / weekend / error back-off / cap / floor); `TestBacklog` (insert above threshold / skip below threshold / skip HOLD / preserve first_seen on resurface / acted clears / passed does NOT clear / expired drops / BUY+SELL separate keys); `TestReminders` (no-fire-too-young / tier-1@1h / tier-2@4h / cap behavior / dispatch advances counter); `TestStateIO` (round-trip / missing→fresh / corrupt→fresh / empty→fresh / unwritable-dir tolerated / no stray .tmp); `TestProcessRunResult` (cycle bump / error streak / reset / naive-now); `TestDispatch` (empty no-op / one-per-reminder / failure-doesn't-block / dashboard-url appended); `TestModuleSurface` (dataclass round-trips / corrupt backlog dropped / CONFIG keys complete).

### Gravity step 69
**`step_69_advisory_agent_audit`** — 15 checks: importable; CONFIG has all required keys; dataclass field sets; RTH-normal cadence; high-VIX tightens cadence; weekend → off-hours; error back-off short-circuits; `update_backlog` inserts high-conviction BUY; `update_backlog` clears after "acted"; tier-1 reminder fires after 1 h + counter advances; state save/load round-trip; corrupt JSON → fresh state; ADVISORY-ONLY source check (no `submit_order`/`place_order`/etc. keywords in `engine/advisory_agent.py`); `main.py` registers `--agent` flag + `_run_agent_loop`; `tests/test_advisory_agent.py` exists.

### Operational notes
- `--agent` shares the `output/state_snapshot.json` write path with `--interval` and one-shot mode, so the Streamlit dashboard, daily briefing, and snapshot-diff Δ band all light up identically — no separate observability work needed.
- `NTFY_TOPIC` unset → reminder dispatch is silently inert (operator can run the agent purely as a logger).
- `NTFY_DASHBOARD_URL` (unchanged from Tier 1.4) — when set, every reminder message ends with a deep-link to the GUI for one-click context.
- Stopping the agent: SIGINT (Ctrl-C) or SIGTERM — the loop catches the signal, finishes the current cycle + reminder dispatch + state save, then exits cleanly.
- Backlog tuning: drop `backlog_tier_hours` to `(0.5, 2.0, 8.0)` to be more aggressive intraday; raise `backlog_conviction_threshold` to 0.90 to reduce reminder volume.

## Tier 6.1 — Trade-Signal Abilities (Conviction Momentum + Stop/Target Proximity) (2026-06)

### Overview
Two advisory trading abilities layered on top of the Tier 6 autonomous agent, both derived **purely** from the per-cycle `RunResult` the agent already produces (`recommendations` + Robinhood `AccountSnapshot`). **ADVISORY ONLY** — no order code, no broker import; every output is a `TradeAlert` pushed through the existing `alerting.notify()` ntfy channel (no-op when `NTFY_TOPIC` unset). Pinned by Gravity step 70 check 10 (source-grep for `submit_order`/`place_order`/etc.).

### New module: `engine/trade_signals.py`
Headless, dependency-free (stdlib only). Public API:
- **`TradeAlert`** — frozen dataclass: `symbol`, `kind` (`"momentum_building"`|`"momentum_fading"`|`"approaching_stop"`|`"approaching_target"`), `priority` (`"default"`|`"high"`), `title`, `message`, `detail: dict[str,float]` (numeric context; NaN where unavailable, never fabricated).
- **`update_conviction_history(history, recommendations, *, config) -> dict`** — pure; appends each symbol's current conviction, trims to `momentum_lookback_cycles`, prunes symbols absent from the current universe (history can't grow unbounded), returns a NEW dict (input not mutated).
- **`detect_conviction_momentum(history, recommendations, alerted, *, config) -> (alerts, new_alerted)`** — Ability A. Edge-triggered per symbol via the `alerted` debounce map (`symbol -> "building"|"fading"`).
- **`detect_price_triggers(snapshot, recommendations, alerted, *, config) -> (alerts, new_alerted)`** — Ability B. Edge-triggered via `alerted` (`symbol -> "stop"|"target"`).
- **`dispatch_trade_alerts(alerts, *, dashboard_url=None)`** — mirrors `advisory_agent.dispatch_backlog_reminders` (inline `alerting.notify` import, per-alert try/except, dashboard deep-link append).

### Ability A — Conviction Momentum
The autonomous agent uniquely holds cross-cycle state; the static backlog only fires at the 0.85 siren. This watches each symbol's conviction *trajectory*:
- **"building"** (`default` priority) — conviction climbed ≥ `momentum_rising_delta` (0.10) monotonically over the last `momentum_min_cycles` (3), with the latest value in `[momentum_building_floor=0.60, momentum_building_ceiling=0.85)` and action not SELL. An EARLY entry heads-up *below* the backlog siren (so it never double-alerts with the backlog).
- **"fading"** (`high` priority) — conviction fell ≥ `momentum_falling_delta` (0.15) monotonically on a name whose action is no longer BUY. An EARLY exit warning.
- A sustained trend pings ONCE; the debounce flag clears when the trend breaks (choppy window) so a later move re-alerts; a direction flip (building→fading) re-fires immediately.

### Ability B — Stop / Target Proximity
For HELD positions only (`quantity > 0`, `market_value ≥ min_position_value_usd=100`):
- **Stop** (`high`) — volatility-scaled level `average_cost − stop_atr_multiple*ATR` (ATR from `rec.key_indicators["atr"]`), fallback `average_cost*(1 − stop_fallback_pct=0.08)` when ATR missing. Fires when `price ≤ stop*(1 + stop_proximity_pct=0.02)` — within the band above the stop OR already breached (title says "breached" vs "approaching").
- **Target** (`default`) — the 30-day forecast price (`rec.forecast`) when usable, fallback `average_cost + target_atr_multiple*ATR`. Fires when `price ≥ target*(1 − target_proximity_pct=0.02)` — at/near the target, including price already past the forecast.
- Stop is checked before target. No fabricated levels (CONSTRAINT #4): a position with neither a usable ATR nor forecast yields no target alert; dust positions and bad-data rows (price/cost ≤ 0) are skipped.

### `AgentState` additions (`engine/advisory_agent.py`)
Three new serialized fields (tolerant rehydration — corrupt entries dropped, never raise, CONSTRAINT #6):
- `conviction_history: Dict[str, List[float]]` — rolling per-symbol conviction window.
- `momentum_alerted: Dict[str, str]` — Ability A debounce.
- `price_trigger_alerted: Dict[str, str]` — Ability B debounce.

### Wiring in `main._run_agent_loop`
New step (4b), after the backlog-reminder step and before state persistence: lazily imports the four `engine.trade_signals` callables; updates `state.conviction_history`; runs both detectors (Ability B reads `result.snapshot`); concatenates and dispatches the alerts; advances the debounce maps on `state`. Wrapped in its own try/except so a failure degrades the cycle gracefully without affecting cadence or backlog.

### `engine/advisory_agent.py` refinements (same task)
- Removed the dead `seen_now` set in `update_backlog` (computed, never read).
- Fixed a doubled match-window in the "actioned" backlog clear: the upper bound was `first_seen + 2*match_window_h` (48 h) instead of the intended `+ match_window_h` (24 h).
- Replaced three hand-rolled `BacklogEntry` reconstructions with `dataclasses.replace`.

### CONFIG (`engine.trade_signals.CONFIG`)
| Key | Default | Purpose |
|---|---|---|
| `momentum_lookback_cycles` | 5 | Rolling conviction window length |
| `momentum_min_cycles` | 3 | Min points before a trend is judged |
| `momentum_rising_delta` | 0.10 | Rise across window to flag "building" |
| `momentum_building_floor` | 0.60 | Min conviction for a "building" flag |
| `momentum_building_ceiling` | 0.85 | Upper bound (= backlog siren; avoids double-alert) |
| `momentum_falling_delta` | 0.15 | Drop across window to flag "fading" |
| `stop_atr_multiple` | 2.5 | ATR stop distance below cost |
| `stop_fallback_pct` | 0.08 | Stop distance when ATR missing |
| `stop_proximity_pct` | 0.02 | Band above stop that triggers |
| `target_atr_multiple` | 3.0 | ATR target distance above cost (no-forecast fallback) |
| `target_proximity_pct` | 0.02 | Band below target that triggers |
| `min_position_value_usd` | 100.0 | Dust-position floor |

### Test surface
- **`tests/test_trade_signals.py`** (41 tests, 5 classes): `TestConvictionHistory` (append/trim/prune/immutability/NaN-skip), `TestConvictionMomentum` (building once+debounce, ceiling/floor/min-rise suppression, SELL block, not-enough-history, fading HIGH, BUY block, trend-reset clears debounce, direction flip re-fires, immutability), `TestPriceTriggers` (ATR stop, breach, % fallback, forecast target, already-exceeded, ATR target fallback, midrange no-trigger, debounce, dust/zero-qty/bad-data filtering, no-rec % stop, empty/missing positions, immutability), `TestDispatch` (empty no-op, one-per-alert, dashboard URL, broken-notify swallowed, priority forwarded), `TestModuleSurface` (CONFIG keys, frozen dataclass, no order keywords).
- **`tests/test_advisory_agent.py`** — extended with `test_agent_state_roundtrips_trade_signal_fields` and `test_agent_state_from_dict_drops_corrupt_history`.

### Gravity step 70 (`step_70_trade_signals_audit`)
10 checks: module importable; CONFIG keys; history append/trim/prune/immutability; building once+debounce; ceiling suppression; fading HIGH; ATR stop HIGH; forecast target + debounce; dust ignored (CONSTRAINT #4); ADVISORY-ONLY source + main.py wiring + test file exists.

## Tier 7 — Robinhood Realized-P&L Engine (2026-06)

### Overview
`data/robinhood_orders.py` is a **READ-ONLY, ADVISORY-ONLY** engine that fetches the account's *filled* equity orders and reconstructs closed round-trip trades via FIFO lot-matching — producing realized P&L, win rate, profit factor, and holding-period stats. It is the live, repeatable source for the *closed-trade population* that the calibration tracker (Tier 1.2), fractional-Kelly sizing (`sizing/kelly.py`), and the GUI consume (the same FIFO reconstruction that originally seeded the `trades` table, now a first-class tested module). It contains **NO order-submission/modification/cancellation code** (pinned by Gravity step 71 check 10 + the repo-wide `TestNoOrderFunctions` AST guard).

### Public API
- **`OrderFill`** — frozen dataclass: `symbol`, `side` (`buy`/`sell`), `quantity`, `price` (avg execution), `timestamp` (UTC-aware), `order_id`. JSON round-trips via `to_dict`/`from_dict`.
- **`ClosedTrade`** — frozen dataclass: `symbol`, `quantity`, `entry_ts`, `exit_ts`, `entry_price`, `exit_price`, `realized_pnl`, `return_pct`, `holding_days`.
- **`reconstruct_closed_trades(fills) -> list[ClosedTrade]`** — PURE FIFO per symbol: buys push open lots, sells consume oldest-first; partial lots are retained; a sell exceeding open lots matches what exists and DROPS the unmatched excess (CONSTRAINT #4 — never a fabricated zero-cost entry). Output sorted by `exit_ts` ascending (matches `TransactionsStore.closed_trades_df()`).
- **`realized_pnl_summary(trades) -> dict`** — PURE aggregation: `n_trades`, `total_realized_pnl`, `win_rate`, `avg_win`, `avg_loss`, `profit_factor`, `avg_return_pct`, `avg_holding_days`, `best_/worst_trade_pnl`, `gross_profit/loss`. Empty input → NaN-shaped (win rate/averages NaN, never 0.0); `profit_factor` is NaN when there are no losing trades (ratio undefined, not infinite).
- **`parse_orders(raw_orders, symbol_resolver) -> list[OrderFill]`** — normalises raw `get_all_stock_orders()` dicts; keeps only `state == "filled"` with positive qty/price; resolves the instrument URL → ticker via the injected `symbol_resolver`; timestamp fallback chain `last_transaction_at → updated_at → created_at`; malformed records logged at DEBUG and skipped.
- **`fetch_filled_orders(*, force=False, cache_max_age_hours=20.0, orders_fetcher=None, symbol_resolver=None) -> list[OrderFill]`** — network fetch with a daily JSON cache (`cache/robinhood_orders.json`, atomic write-then-rename). Reuses the shared read-only TOTP login from `data.robinhood_portfolio._login`. `orders_fetcher`/`symbol_resolver` are injectable for tests. Dead-letter resilient: a fetch/auth failure degrades to the (stale) cache, else `[]` — never raises (CONSTRAINT #6). The default `symbol_resolver` memoises `get_symbol_by_url` so each instrument URL hits the network at most once per process.
- **`realized_performance(*, force=False, …) -> dict`** — convenience: fetch → reconstruct → summarise, returning `{"summary": {...}, "trades": [...], "n_fills": int}`.

### Design constraints
- **READ ONLY / ADVISORY ONLY** — only `get_all_stock_orders` / `get_symbol_by_url` (reads) are called; no execution surface.
- **No fabricated metrics (CONSTRAINT #4)** — unmatched sells dropped (not invented); empty summaries are NaN-shaped.
- **No auto-persist** — the module is analytics-only; it deliberately does NOT write into the production `trades` table (avoids double-counting the Kelly population). Persistence, if ever wanted, is a separate explicit step via `TransactionsStore.record_trade`/`close_trade`.

### Test surface
- **`tests/test_robinhood_orders.py`** (29 tests, 5 classes): `TestFifoReconstruction` (simple/partial/retained-lot/loss/excess-drop/sell-without-buy/open-position/multi-symbol/sort/zero-qty filtering), `TestSummary` (NaN-empty, win-rate+profit-factor, NaN-PF-no-losses, avg-holding-days), `TestParseOrders` (filled-only, resolver skip, timestamp fallback, zero qty/price, price fallback, malformed skip, empty), `TestFetchAndCache` (injected fetcher, write-then-read cache, failure→empty, failure→stale-cache, end-to-end performance, OrderFill round-trip), `TestModuleSurface` (frozen ClosedTrade, no order-submission keywords). All offline.

### Gravity step 71 (`step_71_robinhood_orders_audit`)
10 checks: importable + full surface; FIFO two-lot split; realized P&L/return%; excess-sell drop (CONSTRAINT #4); exit_ts sort; NaN-empty summary; win-rate+profit-factor (NaN PF no losses); parse_orders filled-only+resolver; fetch dead-letter resilience (CONSTRAINT #6); ADVISORY-ONLY source + test file exists.

### No new env vars / dependencies
Reuses the existing `RH_USERNAME`/`RH_PASSWORD`/`RH_MFA_SECRET` credentials and `robin_stocks` dependency. New cache file: `cache/robinhood_orders.json` (never committed; under the existing `cache/` ignore).

## Tier 8 — Robinhood Execution Bridge (2026-06)

### Overview
Integrates the **Robinhood Trading MCP** (`https://agent.robinhood.com/mcp/trading`) so the platform can act on its advisory output — **paper/dry-run first**. The MCP is an **LLM-agent tool surface** (consumed by Claude Code: `review_equity_order`, `place_equity_order`, `cancel_equity_order`, plus read tools), NOT a Python SDK, so the headless pipeline **cannot** call it. Integration is therefore a **seam**: the Python pipeline emits a gated, dry-run order queue; a Claude Code agent is the only actor that calls the MCP. Robinhood's blast-radius control is a dedicated, separately-funded **Agentic account**; its dry-run primitive is `review_equity_order` (simulate, no execution).

**Relationship to ADVISORY_ONLY:** independent. `ADVISORY_ONLY` (default `True`) stays the master quarantine of the **Alpaca** surface (`main_orchestrator._execute_broker_orders`). Robinhood gets its own `ROBINHOOD_EXECUTION_MODE` so one flag never arms two brokers. Robinhood-live does **not** require lifting `ADVISORY_ONLY`.

### Two-phase ledger invariant
*Python writes intents; the human-driven Claude agent writes outcomes.* No component both decides and executes. Headless Python has no MCP access and defines no `place_*`/`submit_order` function (enforced by `TestNoOrderFunctions` + Gravity).

### `execution/queue_builder.py` (NEW — inside the AST-excluded `execution/` zone)
Reuses the existing decision stack (`OrderIntent`, `PreTradeRiskGate.run_all`, `GlobalKillSwitch`, `make_client_order_id`) to translate actionable advisory `Recommendation`s into a gated, dry-run queue — it **never contacts a broker or the MCP**. Public API (AST-safe names — no `place_*`/`submit_order`/`*_order`):
- `build_execution_queue(run_result, *, mode, config, now) -> dict` — gated payload.
- `gate_intent(intent, context, gate=None) -> (allowed, reasons)` — runs `PreTradeRiskGate`; **fails CLOSED** on exception (never marks allowed).
- `emit_execution_queue(run_result, *, mode=None, output_dir=None) -> Optional[Path]` — atomically writes `output/execution_queue.json`; returns `None` and writes nothing when mode is `off`; never raises (CONSTRAINT #6).
- `CONFIG` (`min_conviction=0.85`, `strategy_id="advisory"`), `VALID_MODES=("off","review","live")`.

**Intent mapping:** BUY → `qty=null` + capped `target_notional` (equity × `suggested_position_pct`, capped by `ROBINHOOD_MAX_NOTIONAL_PER_ORDER`); the agent computes shares from a live MCP quote. SELL → only for HELD symbols, `qty` = held quantity (a SELL of an unheld symbol is dropped — no fabricated position). HOLD / below-`min_conviction` / not-held-SELL are dropped.

**Safety invariant:** `allow_place = (mode=="live") AND gate_allowed AND (not kill_switch_active) AND (notional cap configured)` — structurally `False` in every non-live posture and whenever the cap is unset or the kill switch is active.

### Staged execution mode (`settings.ROBINHOOD_EXECUTION_MODE`, default `off`)
| Mode | Behavior |
|------|----------|
| `off` (default) | `emit_execution_queue` returns `None`; nothing written; zero behavior change. |
| `review` (paper/dry-run) | Queue emitted; the agent calls **only** `review_equity_order`; every intent `allow_place=False`. |
| `live` | `allow_place=True` only when gate passed + kill switch clear + cap set; the agent still requires per-trade human confirmation. |

Rollout is strictly `off → review → live`. A `field_validator` coerces any unknown value → `off` (fail-safe). New setting `ROBINHOOD_MAX_NOTIONAL_PER_ORDER` (default `0.0`; `live` requires it `> 0`).

### Wiring
`main.py::_run_cycle` calls `emit_execution_queue(result)` in a best-effort, non-fatal block (next to the Tier 1.4 watch-engine block). It only writes a file — never contacts a broker. The kill-switch advisory-pause gate already short-circuits `run_once()`, so a paused cycle emits nothing. `output/` is gitignored, so the queue + receipts are never committed.

### Claude Code execution surface (the only MCP caller)
- `.claude/skills/robinhood-execution/SKILL.md` — the runbook: verify MCP connected + queue fresh; honor hard stops (kill switch, `mode: off`, stale queue, no confirmed Agentic account); `get_accounts` → confirm Agentic account; **always `review_equity_order` first**; in `review` stop after preview; in `live` place only `allow_place=true` intents, one at a time, with explicit per-order human confirmation, re-checking the kill switch before each placement; append outcomes to `output/execution_receipts.jsonl`.
- `.claude/commands/rh-execute.md` — `/rh-execute` entry point.

### Setup (operator, local — cannot be done headless)
`claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading`, then `/mcp` → authenticate (OAuth). Fund a dedicated Agentic account. This is interactive and must run in the operator's own Claude Code.

### Guards / audits
- `tests/test_pipeline_smoke.py::TestNoOrderFunctions` — set unchanged (bridge is in `execution/`); added a positive assertion that `queue_builder` defines no order-submission function.
- `scripts/preflight_check.py` — new `check_robinhood_execution_mode` (PASS for off/review; **FAIL** for `live` without a notional cap; warning-only PASS for `live` with a cap). NOT in `_ADVISORY_AUTO_SKIP`. `ALL_CHECKS` is now 17 (Gravity step_66 count updated).
- `gravity/__init__.py` — `step_72_robinhood_execution_bridge_audit` (10 checks): off emits nothing; review never placeable; live+cap+clear-KS allows; kill-switch blocks; cap-unset blocks; drop rules + held-SELL qty; settings default+fail-safe; preflight live-without-cap fails + not auto-skipped; no order defs + main.py wiring + agent skill/command exist.

### Test surface
- `tests/test_queue_builder.py` (19 tests): mode staging (off→no file, review→file+never-placeable), allow_place gating (live+cap allows, no-cap blocks, kill-switch blocks all, gate-failure fails closed), intent construction + drop rules + held-SELL qty + capped notional + deterministic client_order_id, payload schema + atomic write + emit-failure swallowed, `gate_intent` unit.

### GUI banner (`gui/robinhood_mode.py`, 2026-07)
Informational only — the actual guards live in `execution/queue_builder.py`. A persistent banner is rendered above the tab bar in `gui/app.py`, driven by the headless `gui.robinhood_mode.read_robinhood_execution_mode(settings)` helper (frozen `RobinhoodModeState` dataclass; pure function; never raises — CONSTRAINT #6). Variants: `off` → hidden (default; renders nothing); `review` → amber `st.warning` explaining every intent is `allow_place=False`; `live` → red `st.error` naming the per-order cap (if set) or flagging `ROBINHOOD_MAX_NOTIONAL_PER_ORDER` as UNSET (if not). Rendered AFTER the ADVISORY_ONLY / Alpaca run-mode banner so both are visible when both apply. Wrapped in a try/except so a broken banner never crashes the app. Tests: `tests/test_robinhood_mode.py` (48 tests — mode/cap coercion, off/review/live variants, cap-set vs cap-unset copy, degradation contract, app-wiring source guards).

### New env vars / settings
- `ROBINHOOD_EXECUTION_MODE` — `off` (default) | `review` | `live`.
- `ROBINHOOD_MAX_NOTIONAL_PER_ORDER` — USD per-order ceiling (default `0.0`; required `> 0` for live).
New output artifacts (gitignored): `output/execution_queue.json` (Python-written intents), `output/execution_receipts.jsonl` (agent-written outcomes).

## Tier 9 — Claude + Gemini Commentary Integration (`llm/`, 2026-06)

### Overview
Two jobs — analyst rationale and alert commentary — that ENRICH (never replace) the deterministic template narrative. **Claude** (Anthropic) and **Gemini** (Google) are both available for **either** job: the operator independently chooses which provider serves rationale (`LLM_COMMENTARY_RATIONALE_PROVIDER`, default `"claude"`) and which serves alerts (`LLM_COMMENTARY_ALERT_PROVIDER`, default `"gemini"`) — e.g. Gemini-only, Claude-only, or mix-and-match (Gemini for rationale + Claude for alerts). Chart-pattern vision (Tier 9 Scope 3) remains Gemini-only (Claude vision is not wired). ADVISORY-ONLY: LLM output flows into the `rationale` string, the new `Recommendation.llm_rationale` dict, and ntfy `message` bodies — never into numeric pipeline scalars (`score`, `conviction`, `suggested_position_pct`, `forecast`, `key_indicators`). Flexible per-job routing, but still no cross-check — each job calls exactly one provider. On-demand cadence only — `evaluate()` never calls an LLM in-cycle.

### Package layout (`llm/`)
- **`llm/schemas.py`** — Pydantic v2 schemas. `AnalystRationale` (headline≤120 chars, why_now 2-3 sentences ≤800 chars, 1-3 key_risks ≤140 chars each, invalidation ≤240 chars). `AlertCommentary` (body ≤280 chars to fit ntfy push limits, urgency_hint `low|normal|high` advisory-only — never overrides `WatchAlert.priority` / `TradeAlert.priority`).
- **`llm/providers.py`** — `LLMProvider` ABC + `ClaudeProvider` (Anthropic Messages API with `tool_use` structured-output forcing; single emitter tool whose `input_schema` is `schema_model.model_json_schema()`; `tool_choice={"type":"tool","name":"emit_structured_output"}` for strict JSON) + `GeminiProvider` (Google `google.genai` with `response_mime_type='application/json'` + `response_schema=schema_model`). **Both SDKs lazy-imported inside provider `__init__`** so the package costs zero when the master switch is off; missing SDK → `_client=None` → all calls return `None` silently. Hard 8 s timeout via `settings.LLM_COMMENTARY_TIMEOUT_SECONDS`. Every method wraps the call+parse in try/except; any exception → `None` (CONSTRAINT #6).
- **`llm/router.py`** — `get_rationale_provider()` / `get_alert_provider()`. Flexible per-job routing via a shared `_construct_provider(choice, timeout)` dispatcher — either `"claude"` or `"gemini"` is valid for either job (`LLM_COMMENTARY_RATIONALE_PROVIDER` / `LLM_COMMENTARY_ALERT_PROVIDER` are independent operator choices). Returns `None` when the master switch is off, when the relevant API key is unset, when the operator pinned the provider to `"none"`, or on any construction error (soft-fail, CONSTRAINT #6).
- **`llm/commentary.py`** — public entry points `generate_analyst_rationale(rec_skeleton, context) -> Optional[AnalystRationale]` and `generate_alert_commentary(alert_skeleton, context) -> Optional[AlertCommentary]`. Each: derive cache key → check cache → on miss call provider → validate via schema → store cache → return. Baseline system prompts inlined; when `PROMPT_REGISTRY_ENABLED=True`, `_registry_prompt()` pulls `llm.rationale.system` / `llm.alert.system` from the signed registry (falls back to the baseline on any registry failure).
- **`llm/cache.py`** — JSON-file day-bucketed cache at `output/llm_commentary_cache.json` (gitignored). Key = `sha256(provider + schema_name + symbol + iso_date_utc + score_bucket + action)` where `score_bucket = floor(score/5)` so small numeric jitter doesn't invalidate but a meaningful move (47→52) does. TTL = end of UTC trading day. Atomic write via temp-rename. Corrupt JSON / missing file → empty dict silently (CONSTRAINT #6). **`provider` in the cache key is read from the LIVE-configured `LLM_COMMENTARY_RATIONALE_PROVIDER` / `LLM_COMMENTARY_ALERT_PROVIDER` setting** (not a hardcoded string) so switching a job's provider naturally segregates/invalidates cache entries rather than serving a payload generated by a different model.

### `engine/advisory.py` integration
- **`Recommendation.llm_rationale: Optional[Dict[str, Any]] = None`** — new field at the end of the frozen dataclass. Typed as `Dict[str, Any]` (not the pydantic model) so `engine/advisory.py` never imports `llm.schemas` — keeps the SDK reach lazy. Default `None` keeps positional construction stable.
- **`_build_rationale()` (line 904) stays template-pure.** No LLM call from `evaluate()`. Per-cycle behavior is byte-identical to pre-Tier-9.
- **New sibling `enrich_with_llm_rationale(rec, context=None) -> Recommendation`** at the end of `engine/advisory.py`. Calls `llm.commentary.generate_analyst_rationale(asdict(rec), ...)` and on success returns `dataclasses.replace(rec, llm_rationale=result.model_dump())`. On `None` (or any exception) returns `rec` unchanged — the deterministic `rec.rationale` template text is ALWAYS preserved. Used by the CLI and any future GUI button.

### Alert dispatch integration (`watch_engine.dispatch_watch_alerts`, `engine/trade_signals.dispatch_trade_alerts`)
- Both sites APPEND-NEVER-REPLACE: after `msg = alert.message` (line 736 in `watch_engine`) / `msg = a.message` (line 449 in `trade_signals`), a `if getattr(settings, "LLM_COMMENTARY_ENABLED", False):` guard lazily imports `llm.commentary.generate_alert_commentary` and `msg = f"{msg}\n\n📝 {enrich.body}"` on success. Soft-fail = template message unchanged. The deterministic `alert.priority` is the source of truth for ntfy dispatch and is never overridden by `AlertCommentary.urgency_hint`.

### CLI: `engine/llm_commentary.py`
`python -m engine.llm_commentary SYMBOL [--alert]`:
- Default: builds a `Recommendation` via `engine.advisory.evaluate(...)`, calls `enrich_with_llm_rationale`, pretty-prints both the deterministic template paragraph and the `AnalystRationale` fields (or "LLM commentary unavailable" on `None`). Exit 0 on soft-fail.
- `--alert`: constructs a synthetic `WatchAlert` and runs the real `dispatch_watch_alerts` path with `alerting.notify` stubbed to capture (no ntfy traffic), so the operator can preview the augmented body.

### New env vars / settings
- **`LLM_COMMENTARY_ENABLED: bool = False`** — master switch, default `False`. When off, ZERO SDK imports and ZERO network calls.
- **`LLM_COMMENTARY_RATIONALE_PROVIDER: str = "claude"`** — `"claude"` | `"gemini"` | `"none"`. Independent of the alert-provider choice.
- **`LLM_COMMENTARY_ALERT_PROVIDER: str = "gemini"`** — `"gemini"` | `"claude"` | `"none"`. Independent of the rationale-provider choice.
- **`LLM_COMMENTARY_CACHE_PATH: str = "output/llm_commentary_cache.json"`** — JSON cache path (gitignored).
- **`LLM_COMMENTARY_TIMEOUT_SECONDS: int = 8`** — hard wall-clock per provider call.
- **`ANTHROPIC_API_KEY: Optional[str] = None`** — Claude key. **In `gui/env_io.SECRET_KEYS` only — NEVER GUI-writable (CONSTRAINT #3).**
- **`GEMINI_API_KEY: Optional[str] = None`** — Gemini key. **In `gui/env_io.SECRET_KEYS` only — NEVER GUI-writable (CONSTRAINT #3).**
- The three `LLM_COMMENTARY_*` toggles (master switch + two provider names) are in `gui/env_io.ALLOWED_KEYS` so the Strategy Matrix tab can flip them without touching credentials.

### Test surface
- **`tests/test_llm_providers.py`** (25 tests) — fake `anthropic` / `google.genai` modules in `sys.modules`; tool_use happy path; missing tool_use block; schema-mismatched payload; network exception; timeout; missing SDK → `_client=None` → call returns `None`. Soft-fail contract: every provider's `call_structured` NEVER raises.
- **`tests/test_llm_router.py`** — flexible per-job routing: `get_rationale_provider()` dispatches to `ClaudeProvider` OR `GeminiProvider` per `LLM_COMMENTARY_RATIONALE_PROVIDER`; `get_alert_provider()` likewise per `LLM_COMMENTARY_ALERT_PROVIDER` (including the non-default direction — Gemini serving rationale, Claude serving alerts); master-switch-off → `None` regardless of provider choice; `"none"` / unknown provider string / missing key all soft-fail to `None`; provider-choice string is case-insensitive; construction exception never raises.
- **`tests/test_llm_commentary.py`** (26 tests) — cache key determinism (same UTC day + same score bucket → same key; bucket boundary at 47/52 invalidates; provider/action/date changes invalidate); cache put/get round-trip; corrupt JSON → empty; provider returns valid → schema-validated; second call hits cache (`call_count == 1`); `LLM_COMMENTARY_ENABLED=False` → provider NEVER instantiated; provider returns `None`/raises → returns `None`; corrupt cached payload → re-fetched; **switching a job's configured provider mid-session does NOT reuse the other provider's cache entry** (regression guard for the flexible-routing cache-key fix).
- **`tests/test_advisory_llm_enrichment.py`** (10 tests) — `Recommendation.llm_rationale` default `None` and field accepts dict; dataclass remains frozen; master switch off → `enrich_with_llm_rationale` returns rec unchanged (identity); valid provider → `llm_rationale` populated AND deterministic `rationale` PRESERVED; provider returns `None` or raises → rec unchanged; numeric-fields invariant (every numeric field byte-identical after enrichment, only `llm_rationale` changed); `engine/advisory.py` top-of-file has no `import anthropic` / `import google`.
- **`tests/test_alert_dispatch_llm.py`** (8 tests) — both `dispatch_watch_alerts` and `dispatch_trade_alerts`: master switch off → template msg unchanged; LLM success → msg starts with template AND contains `\n\n📝 ...`; LLM `None` → msg unchanged; commentary raises → dispatch still fires with template msg; priority always comes from the alert, never from `AlertCommentary.urgency_hint`.
- **`tests/test_gui_env_io_secret_llm_keys.py`** (8 tests) — `ANTHROPIC_API_KEY` and `GEMINI_API_KEY` are in `SECRET_KEYS` AND NOT in `ALLOWED_KEYS`; three toggles are in `ALLOWED_KEYS`; `write_setting("ANTHROPIC_API_KEY", "x")` raises `SecretWriteError`.

### Gravity step 74 (`step_74_llm_commentary_audit`)
8 checks: (1) `engine/advisory.py` top-level imports have no anthropic/google reach (lazy only); (2) `settings.LLM_COMMENTARY_ENABLED` default is `False`; (3) both API keys are `SECRET_KEYS` only (CONSTRAINT #3); (4) `llm/commentary.py` contains `try:` + `return None` (CONSTRAINT #6); (5) both dispatch sites preserve the template `msg` base then APPEND with the 📝 marker (append-never-replace); (6) `Recommendation.llm_rationale` field exists with default `None`; (7) `llm/commentary.py` never assigns LLM output to a numeric pipeline scalar (regex source-grep over forbidden patterns — CONSTRAINT #4); (8) all five Tier-9 test files exist.

### Critical invariants (must never regress)
- **No fabricated metrics (CONSTRAINT #4)** — LLM output flows ONLY into `rationale` strings, `llm_rationale` dict, and ntfy `message` bodies. Never into `score`, `conviction`, `suggested_position_pct`, `forecast`, `key_indicators`, or any `state_snapshot.json` numeric field. Enforced by Gravity step 74 check 7.
- **Dead-letter resilience (CONSTRAINT #6)** — every provider/commentary/enrich path is wrapped in try/except. Any failure (network, auth, timeout, ValidationError, parse error, missing SDK) returns `None`/unchanged-rec. Zero exceptions propagate past `llm/commentary.py`. Enforced by Gravity step 74 check 4.
- **ADVISORY_ONLY preserved** — LLM cannot cause an order. The broker quarantine in `main_orchestrator._execute_broker_orders` is untouched. The new commentary surface adds no order-submission code.
- **Operator opt-in** — `LLM_COMMENTARY_ENABLED=False` by default. When False, ZERO SDK imports and ZERO network calls (providers' `__init__` is lazy). Enforced by Gravity step 74 check 2.
- **No GUI-writable secrets (CONSTRAINT #3)** — both API keys live in `gui/env_io.SECRET_KEYS` only. `write_setting("ANTHROPIC_API_KEY", ...)` raises `SecretWriteError`. Enforced by Gravity step 74 check 3.
- **No top-level LLM SDK reach** in `engine/advisory.py` — all `anthropic` / `google.genai` imports are lazy (inside provider `__init__` or function bodies). Enforced by Gravity step 74 check 1 AND `tests/test_advisory_llm_enrichment.py::TestNoTopLevelLLMImport`.
- **Append-never-replace alert dispatch** — both `watch_engine.dispatch_watch_alerts` and `engine/trade_signals.dispatch_trade_alerts` keep `msg = alert.message` (or `a.message`) as the base, then append `\n\n📝 {enrich.body}` only on LLM success. Enforced by Gravity step 74 check 5.

### Operator notes
- Setting `LLM_COMMENTARY_ENABLED=true` in `.env` without setting `ANTHROPIC_API_KEY` AND `GEMINI_API_KEY` is harmless: the providers' constructors return early (no key → `_client=None`), the commentary functions return `None`, and the template fallback kicks in transparently.
- The cache key is bucketed by UTC date AND `floor(score/5)`. To force a re-fetch on the same day, delete `output/llm_commentary_cache.json` (it's gitignored).
- For ntfy push limits, `AlertCommentary.body` is hard-capped at 280 chars by the schema. A provider response longer than that fails validation → soft-fail → template message goes out unchanged.
- The Reports tab "🤖 Generate analyst commentary" button (`_render_llm_commentary_button` in `gui/panels/report_viewer.py`) has since shipped, alongside the CLI (`python -m engine.llm_commentary SYMBOL`) as the scriptable entry point.
- New optional dependency: `google-genai>=0.3.0` (added to `requirements.txt`). `anthropic>=0.25.0` was already present.

## Tier 9 Scope 2 — AI Gravity Audit Runner (`engine/gravity_ai_runner.py`, 2026-06)

### Overview
The 7 step prompts in `ai_verification_prompts.py` were designed for an external LLM to consume but never had a runner. Scope 2 adds **`engine/gravity_ai_runner.py`** — an on-demand AI audit runner that uses Tier 9's `llm.providers` to send each step to **Claude (primary auditor)** AND **Gemini (independent cross-checker)** in parallel. Both responses are validated through the new `llm.schemas.GravityAuditStepResult` schema (`status: PASSED|FAILED`, `score: int 0-100`, `findings: list[str]`, `missing_elements: list[str]`); when both verdicts are present and the `status` fields differ, the runner flags `disagreement=true` — but it NEVER picks a winner. The operator sees both verdicts and decides.

### Public API
- **`run_step(step_number, *, claude=None, gemini=None, target_code=None) -> StepRunResult`** — run a single audit step (1-7). Provider injection is for tests; the auto-construction path is lazy and respects the master switch.
- **`run_all(*, claude=None, gemini=None) -> RunReport`** — sweep all 7 steps, return an aggregate report with per-step verdicts + a roll-up `summary` dict (`{total_steps, claude:{passed,failed,skipped}, gemini:{passed,failed,skipped}, disagreements}`).
- **`write_report(report, *, path=None) -> Optional[Path]`** — atomic write-then-rename JSON persist to `settings.GRAVITY_AI_RUNNER_OUTPUT_PATH` (default `output/gravity_ai_audit.json`, gitignored). Soft-fails to `None` on any IO error.
- **`StepRunResult`** (frozen dataclass) — `step_number, step_title, claude_verdict: Optional[dict], gemini_verdict: Optional[dict], disagreement: bool, notes: list[str], timestamp: str`.
- **`RunReport`** (frozen dataclass) — `generated_at, enabled, steps: list[StepRunResult], summary: dict`.

### Step → file map (`_STEP_FILE_MAP`)
Hand-curated mapping of audit-step number to the per-layer file(s) the model reads. Each file's read is capped at 32 KB so the multi-file steps stay within model context windows. Refactoring the audit scope is a single-diff edit to this map:
| Step | Files audited |
|---|---|
| 1 | `config.py`, `database_setup.py`, `processing_engine.py` |
| 2 | `strategy_engine.py`, `processing_engine.py` |
| 3 | `technical_options_engine.py` |
| 4 | `forecasting_engine.py` |
| 5 | `macro_engine.py` |
| 6 | `evaluation_engine.py`, `research_engine.py`, `sizing/kelly.py`, `sizing/vol_target.py` |
| 7 | `execution/risk_gate.py`, `execution/kill_switch.py`, `execution/order_manager.py`, `main_orchestrator.py` |

### CLI
`python -m engine.gravity_ai_runner [STEP] [--json] [--output PATH]`:
- No `STEP`: runs all 7.
- Integer 1-7: runs that single step.
- `--json`: emit JSON only (suitable for piping); otherwise a human summary plus the report-file path.

### Safety contract (audited by Gravity step_75 — 9 checks)
1. **Opt-in master switch** — `settings.GRAVITY_AI_RUNNER_ENABLED=False` by default. Independent of `LLM_COMMENTARY_ENABLED` so the operator can enable AI audits without enabling per-symbol rationale commentary (or vice versa). When False, ZERO provider instantiation, ZERO network calls — verified by step_75 check 6.
2. **No order code** — module source has no `submit_order`/`place_order`/`buy_order`/`sell_order`/`place_equity_order`/`place_option_order` verbs (step_75 check 9). The runner is a pure verdict aggregator.
3. **No top-level SDK reach** in `engine/gravity_ai_runner.py` — all `anthropic`/`google.genai` imports are lazy inside provider factories. Step_75 check 3 source-greps the module's top-level lines.
4. **Soft-fail end-to-end** — every provider/parse/schema failure → that model's verdict is `None` and the step is recorded with operator-facing notes. The runner never raises. Step_75 check 8 verifies via a `RuntimeError`-raising fake provider.
5. **No fabricated metrics (CONSTRAINT #4)** — numeric pipeline scalars (`score`, `conviction`, `forecast`, `suggested_position_pct`, `key_indicators`, ATR levels, …) are never written from a runner output. The runner only writes audit verdicts to its own JSON file. Step_75 check 1 verifies the public surface contains no setters into pipeline scalars.
6. **Schema-bounded** — `GravityAuditStepResult` rejects out-of-bounds `score` (>100 or <0) and any `status` outside `{PASSED, FAILED}` (step_75 check 4). A schema-mismatched provider response is treated as a soft failure, never a fabricated verdict.

### New settings
- **`GRAVITY_AI_RUNNER_ENABLED: bool = False`** — runner master switch (independent of `LLM_COMMENTARY_ENABLED`).
- **`GRAVITY_AI_RUNNER_OUTPUT_PATH: str = "output/gravity_ai_audit.json"`** — where `write_report()` persists the run.

### Test surface
**`tests/test_gravity_ai_runner.py`** (19 tests, 9 classes): `TestSchemaSurface` (canonical payload accepted, bad status rejected, score bounds), `TestStepFileMap` (7 steps mapped + compose_target_code reads each successfully), `TestRunStepDisabled` (master switch off → no provider instantiated), `TestRunStepAgreement` (both PASSED → no disagreement), `TestRunStepDisagreement` (PASSED vs FAILED → disagreement=True), `TestRunStepProviderRaises` (Claude raises → Gemini survives, Gemini raises → Claude survives, both raise → no crash), `TestRunStepUnknownStep` (step 999 → notes record it, never raises), `TestRunAll` (sweeps every step + summary counts add up + disabled-by-default → all-None verdicts), `TestSummarise` (mixed PASSED/FAILED/None counts roll up correctly), `TestWriteReport` (round-trip through JSON + parent dir creation + write-failure soft-fail), `TestSourceGuards` (no top-level anthropic/google import + no order-submission verbs).

### Gravity step 75 (`step_75_gravity_ai_runner_audit`)
9 checks: module surface (run_step/run_all/write_report/RunReport/StepRunResult/_STEP_FILE_MAP); `GRAVITY_AI_RUNNER_ENABLED` default False; no top-level SDK reach; `GravityAuditStepResult` has required 4 fields + enforces score bounds; `_STEP_FILE_MAP` covers all 7 prompts; `run_all()` disabled → all-None verdicts; disagreement flag triggers on status mismatch; provider exception → that side None (CONSTRAINT #6); no order-submission verbs in source (advisory-only invariant).

### Operator notes
- Setting `GRAVITY_AI_RUNNER_ENABLED=true` without `ANTHROPIC_API_KEY` AND `GEMINI_API_KEY` produces a useful — but degraded — report: each step has `claude_verdict=None` / `gemini_verdict=None` with `notes` explaining which side was unavailable. There is no failure path that aborts the run.
- The runner reads files from disk fresh on every call — no caching across runs. To re-audit after a code change, just re-run the CLI; the report file is atomically replaced.
- The Safety tab integration (rendering the runner JSON with per-step verdicts) has since shipped as `_render_gravity_ai_runner_section` in `gui/panels/gravity_audit.py`.

## Tier 9 Scope 3 — AI Insights tab (Gemini Vision, 2026-06)

### Overview
A new "🪄 AI Insights" Streamlit tab that combines, per symbol from the current `state_snapshot.json` universe:

1. **Claude analyst note** — reuses `gui/llm_commentary_panel.py` so this tab and the Reports-tab drill-down share one code path AND one session-state cache.
2. **Gemini chart pattern interpretation** — renders a 252-bar matplotlib chart, sends the PNG to Gemini Vision via `GeminiProvider.call_structured_with_image`, returns a schema-validated `ChartPatternRead` (pattern_name / trend_direction / qualitative support+resistance / narrative / confidence).
3. **Aggregate Claude vs Gemini disagreement view** — walks the cached per-symbol outputs in `st.session_state` and emits one row per watchlist symbol (deterministic action + Claude verdict + Gemini verdict + disagreement boolean). Never flags disagreement against a missing side (CONSTRAINT #4).

### New module surface
- **`llm/schemas.py::ChartPatternRead`** — pydantic v2 schema with bounded fields (`pattern_name` ≤60, `trend_direction` Literal["bullish","bearish","neutral"], 1-3 `support_levels`/`resistance_levels` each ≤120 chars, `narrative` ≤800, `confidence` Literal["low","medium","high"]).
- **`llm/providers.py::GeminiProvider.call_structured_with_image`** — multimodal extension. Builds `Part.from_bytes(data=image_bytes, mime_type='image/png')` + `GenerateContentConfig(response_schema=...)` and returns `Optional[BaseModel]`. Same soft-fail contract as `call_structured`.
- **`llm/chart_insight.py`** — `render_price_chart_png(symbol, bars)` (matplotlib Agg, lazy import) + `generate_chart_pattern_read(symbol, bars, *, provider=None, chart_renderer=None)`. Day-bucketed JSON cache keyed by `(provider="gemini", schema="ChartPatternRead", symbol, score=close*1000+bar_count, action="CHART")`. Reuses `llm/cache.py` infrastructure.
- **`gui/ai_insights_panel.py`** — Streamlit-free helpers: `insights_status` (three-state classifier on `LLM_COMMENTARY_ENABLED`+`GEMINI_API_KEY`), `format_chart_pattern_markdown`, `DisagreementRow` (frozen dataclass), `derive_disagreement_overview`, `disagreement_summary`. Headlessly testable.
- **`gui/panels/__init__.py::render_ai_insights`** + `_render_gemini_chart_section`. Wired as tab 12 in `gui/app.py`.

### Safety contract (audited by Gravity step_76 — 9 checks)
1. Module surface exists (`generate_chart_pattern_read`, `render_price_chart_png`, `ChartPatternRead`).
2. `GeminiProvider.call_structured_with_image` is callable.
3. `ChartPatternRead` rejects bad `trend_direction` AND caps `support_levels`/`resistance_levels` at 3.
4. No top-level `anthropic`/`google` imports in `llm/chart_insight.py` (lazy SDK reach only).
5. Zero order-submission verbs in `llm/chart_insight.py` AND `gui/ai_insights_panel.py` (advisory-only).
6. Opt-in: `generate_chart_pattern_read("X", df)` returns `None` when `LLM_COMMENTARY_ENABLED=False` (default).
7. `derive_disagreement_overview` NEVER flags `disagreement=True` against a missing side (CONSTRAINT #4).
8. `gui/app.py` registers the `🪄 AI Insights` tab AND wires `panels.render_ai_insights`.
9. All three Scope 3 test files exist.

### Cadence + cost
On-demand only — every section is button-gated. The chart-render + Gemini-vision call fires only when the operator clicks **📈 Interpret chart with Gemini** for a specific symbol; results are cached in `st.session_state` (and the JSON disk cache) so re-renders inside the same UTC day are free. The aggregate disagreement view reads only what's already in session state; it never fans out provider calls.

### Test surface
- **`tests/test_chart_insight.py`** (19 tests): schema bounds, chart render happy/edge paths, bar fingerprint, end-to-end generate (cache hit, soft-fail on render/provider/exception, empty symbol, missing image method, default disabled).
- **`tests/test_ai_insights_panel.py`** (~25 tests): status truth table, markdown rendering (full/partial/empty), disagreement view (agreement/disagreement/missing-side guard/heuristic direction), tab wiring (gui/app.py + helper imports).
- **`tests/test_gemini_multimodal.py`** (9 tests): Part.from_bytes constructed, response parsed, schema mismatch / empty / network exception / missing SDK all return None.

### Operator notes
- The Reports-tab "🤖 Generate analyst commentary" button (Scope 1) and the AI Insights "🤖 Claude analyst note" section share the same `_render_llm_commentary_button` helper and the same session-state cache slot — clicking either populates both views.
- Gemini Vision requires `GEMINI_API_KEY` AND `LLM_COMMENTARY_ENABLED=true`. The status banner at the top of the tab spells out which knob is missing.
- The new tab adds matplotlib as a runtime dependency. It's lazy-imported inside `render_price_chart_png`, so an environment without matplotlib still loads the tab — the chart section just shows "Chart render failed".

## Tier 9 Scope 4 — Opal Research Agent (`llm/research.py`, OpenAI/GPT, 2026-07)

### Overview
A third advisory-only AI agent — **Opal** — running on **OpenAI/GPT** (new `OpenAIProvider`). Opal is a FRONT-OF-PIPELINE research/deep-context agent: for a symbol it produces a structured, qualitative `ResearchBrief` (thesis_context, catalysts, risk_factors, recent_developments, data_confidence, sources_note) that is threaded INTO the Claude analyst-rationale prompt as enriched context (Gemini's alert/chart-vision jobs are unaffected). Grounded on REAL retrieved Finnhub `company_news` + `earnings_calendar` (reuses `signals/news_catalyst.py`'s now-public `fetch_company_news` / `fetch_next_earnings` / `build_finnhub_client` helpers, promoted from private names specifically so Opal never reaches into another module's private surface) — never invents catalysts or numbers (CONSTRAINT #4). Independent master switch (`OPAL_RESEARCH_ENABLED`, default `False`) — Opal can run without Claude/Gemini commentary enabled, and vice versa.

### Surface
- `llm/schemas.py::ResearchBrief` — qualitative-only, bounded fields (`thesis_context`≤600, `catalysts` **0-4**×≤160, `risk_factors` **0-4**×≤160, `recent_developments` 0-4×≤200, `data_confidence` Literal[low|medium|high], `sources_note`≤200). **Per-item length is enforced by an inner `Annotated[str, StringConstraints(max_length=…)]` element type** (aliases `_Catalyst`/`_RiskFactor`/`_Development`) — a bare `Field(max_length=4)` on a `List[str]` only bounds the LIST length, never the per-string length. `catalysts`/`risk_factors` have NO `min_length` (both `default_factory=list`) so the model can honestly return an empty list on sparse grounding rather than being forced to fabricate one (CONSTRAINT #4) — symmetric with `recent_developments`. Structurally NO numeric field — every field resolves to `str`/`list[str]`/`Literal[...]`, so there is nothing to fabricate a price target or score into (stronger than a field-name scan; Gravity step_77 check 4 type-checks `model_fields`, unwrapping `Annotated` to reach the base `str`).
- `llm/providers.py::OpenAIProvider` — lazy `import openai` inside `__init__`; client constructed as `openai.OpenAI(api_key=..., timeout=timeout_seconds)` (timeout at CLIENT INIT, mirroring `ClaudeProvider` — never `signal.alarm`, which breaks under threads/Streamlit). `call_structured()` uses the `openai>=1.40` SDK helper `client.beta.chat.completions.parse(response_format=schema_model)`, which returns an already-validated pydantic instance (or `None` on a refusal) via `completion.choices[0].message.parsed`. **Deliberately does NOT hand-roll** `response_format={"type":"json_schema","strict":True,"schema":model_json_schema()}` — OpenAI's strict mode requires `additionalProperties:false` on every object and every field folded into `required` with nullable typing for Optionals; pydantic v2's raw `model_json_schema()` doesn't emit these, so a hand-rolled schema 400s at runtime. The `.parse()` helper does that post-processing for you.
- `llm/research.py::generate_research_brief(symbol, context=None, *, provider=None, grounding_fn=None) -> Optional[ResearchBrief]` — opt-in gate (`OPAL_RESEARCH_ENABLED`) → `cache_get` → `_gather_grounding()` (real Finnhub news/earnings + optional `context["macro_snippet"]`/`context["market_regime"]`) → `provider.call_structured` → `cache_put` → return; soft-fail → `None` at every step (CONSTRAINT #6). `provider`/`grounding_fn` are test seams (mirrors `llm/chart_insight.py`'s dual-seam pattern) — production calls resolve both via `llm.router.get_research_provider()` and the real Finnhub client respectively. `_registry_prompt("llm.research.system", ...)` overrides the baseline system prompt from the Prompt Registry when enabled. Cache key pins `score=0.0, action="RESEARCH"` (mirrors how `llm/chart_insight.py` pins non-scored artifacts) — the UTC-date bucket is the natural daily refresh boundary.
- `llm/router.py::get_research_provider()` — gated on `OPAL_RESEARCH_ENABLED` + `OPAL_RESEARCH_PROVIDER` (`"openai"` or `"gemini"` — Opal is now flexibly routed, same as the Claude/Gemini rationale/alert jobs) + the matching key (`OPENAI_API_KEY` / `GEMINI_API_KEY`). When `"gemini"` is chosen, `OPAL_RESEARCH_MODEL` is only forwarded to `GeminiProvider` if it's been changed away from the OpenAI-flavored default (`"gpt-4o"`) — otherwise `GeminiProvider`'s own model default applies.
- `engine/advisory.py` — `Recommendation.research_brief: Optional[Dict[str,Any]] = None` (additive trailing field; existing positional `Recommendation(...)` constructions elsewhere are unaffected). **`enrich_with_llm_rationale(rec, context=None, *, run_opal=False)`** — Opal is DECOUPLED from the Claude path: a FRESH Opal/OpenAI call fires ONLY when `run_opal=True` AND `OPAL_RESEARCH_ENABLED`. The default `run_opal=False` means the on-demand "Claude analyst note" button / `engine.llm_commentary` CLI never incur a surprise OpenAI cost. Those surfaces instead REUSE a caller-supplied `context["research_brief"]` (an already-generated brief cached by the GUI's dedicated Opal button) — it is threaded into Claude's prompt AND surfaced on the returned rec with NO new call. Builds a local `working_context` copy (never mutates the caller's `context` dict). Opal's failure and Claude's failure are each independently soft-fail; every mutation of `rec` — including the two final `dataclasses.replace()` calls — is individually guarded so the function NEVER raises (the bare `engine.llm_commentary` CLI call site relies on this "exit 0 on soft-fail" guarantee — CONSTRAINT #6). No top-level `openai` import.
- `llm/commentary.py::_format_rationale_user_prompt(rec_skeleton, context=None)` — appends a "Research context" block (thesis_context/catalysts/risk_factors/recent_developments/data_confidence/sources_note) when `context["research_brief"]` is present; purely additive — absent/empty `context` produces byte-identical output to pre-Opal behavior. **`generate_analyst_rationale`'s cache key folds in a research-brief fingerprint** (`_research_brief_cache_variant()` = `"rb"+sha256(brief)[:8]`, passed as `make_cache_key(variant=…)`) so a brief-augmented rationale never serves — or is served by — a brief-less cached entry, and a changed brief invalidates; the variant is appended to the key ONLY when non-empty, so the no-brief path is byte-identical to the pre-Opal key.
- `gui/llm_commentary_panel.generate_for_symbol_row(row, *, enricher=None, research_brief=None)` — the `research_brief` kwarg threads a cached Opal brief into `enrich_with_llm_rationale(rec, {"research_brief": …})` (still `run_opal=False`). `gui/panels._render_llm_commentary_button` reads `st.session_state["ai_insights_opal_payload_{symbol}"]` and forwards it — free reuse, no fresh Opal call.
- `engine/opal_research.py` — CLI `python -m engine.opal_research SYMBOL`; calls `generate_research_brief` directly and prints its fields or an "Opal research unavailable" sentinel; always exits 0 (preview tool, not a gate).
- GUI: `gui/ai_insights_panel.format_research_brief_markdown` (mirrors `format_chart_pattern_markdown`'s contract exactly — "unavailable" sentinel on `None`, partial-safe section rendering) + `gui/panels._render_opal_research_section` rendered as **Section 0, at the TOP of the AI Insights tab** (front-of-pipeline), gated on Opal's OWN independent master switch so it can be toggled without touching `LLM_COMMENTARY_ENABLED`. **`render_ai_insights` renders the symbol picker + Section 0 (Opal) BEFORE applying the `insights_status` (`LLM_COMMENTARY_ENABLED`) gate** — that gate now scopes to the Claude/Gemini Sections 1–3 only, instead of early-returning the whole tab, so an operator with ONLY `OPAL_RESEARCH_ENABLED` on still sees the Opal section. The pre-existing AI Control Center's Opal button (`gui/panels.py` Section B) needed **zero changes** — it was already gated on `gui.ai_control_center.opal_built()`, which now returns `True` since `llm/research.py` genuinely exists (see "AI Control Center auto-activation" below).

### Settings / env vars
- `OPAL_RESEARCH_ENABLED: bool = False` — dedicated master switch (independent of `LLM_COMMENTARY_ENABLED`).
- `OPAL_RESEARCH_PROVIDER: str = "openai"` (`"openai"|"gemini"|"none"`).
- `OPAL_RESEARCH_MODEL: str = "gpt-4o"` — interpreted per the active provider (OpenAI model name for `"openai"`, Gemini model name for `"gemini"`; left at the OpenAI default, a `"gemini"` choice falls back to `GeminiProvider`'s own default instead).
- `OPAL_RESEARCH_TIMEOUT_SECONDS: int = 15`.
- `OPENAI_API_KEY: Optional[str] = None` — **`gui/env_io.SECRET_KEYS` ONLY, never GUI-writable (CONSTRAINT #3).**
- The three `OPAL_RESEARCH_*` toggles are in `gui/env_io.ALLOWED_KEYS` (already wired by the AI Control Center build, PR #85 — no change needed here).
- `requirements.txt`: `openai>=1.12.0` bumped to `openai>=1.40.0` (needed for the `.beta.chat.completions.parse()` Structured Outputs helper).

### `signals/news_catalyst.py` public API promotion
`_fetch_company_news` → `fetch_company_news`, `_fetch_next_earnings` → `fetch_next_earnings`, `_build_finnhub_client` → `build_finnhub_client` (all three; behavior unchanged, single-diff rename + internal caller update in `NewsCatalystSignal.pre_compute`). Promoted so `llm/research.py` consumes a public API instead of reaching into another module's private surface — the same principle already established, extended consistently to the client-builder helper.

### Gravity step 77 (`step_77_opal_research_audit`)
10 checks: module surface (`llm.research.generate_research_brief`, `ResearchBrief`, `OpenAIProvider` importable); `OpenAIProvider.call_structured` callable; `ResearchBrief` rejects >4 catalysts AND a bad `data_confidence` value; `ResearchBrief` exposes NO numeric field (type-based check over `model_fields.items()` — stronger than a field-name scan); no top-level `openai` import in `llm/research.py` OR `engine/advisory.py` (lazy only); no order-submission verbs in `llm/research.py` (advisory-only); opt-in default-off (`generate_research_brief("X")` → `None` when `OPAL_RESEARCH_ENABLED=False`); threading (`_format_rationale_user_prompt` references `research_brief` — source grep); `OPENAI_API_KEY` secret-only; all five Opal test files exist. Runner audit prompt: `ai_verification_prompts.STEP_8_PROMPT`; `engine/gravity_ai_runner._STEP_FILE_MAP[8]` covers `llm/research.py` + `llm/providers.py` + `llm/schemas.py` (holds `ResearchBrief` — criterion 8.2) + `engine/advisory.py` + `gui/env_io.py` (holds `SECRET_KEYS` — criterion 8.5), so the AI auditor can actually see the source its criteria reference.

### AI Control Center auto-activation (confirmed)
The AI Control Center tab's `opal_built()` helper (a soft `import llm.research` probe) now returns `True`. Its Opal row transitioned from `not_built` → `disabled` (the default, since `OPAL_RESEARCH_ENABLED=False` out of the box) with **zero Control Center code changes** — exactly as designed when the Control Center was built (PR #85). Gravity step_78 check 9 was updated to assert this transition (`opal_built() is True` AND the real `opal_research` capability resolves `disabled` by default, never `not_built`); step_78 check 4's `not_built` ranking case now uses a synthetic capability pointing at a nonexistent module, since the real Opal capability can no longer produce that state.

### Critical invariants (must never regress)
- No fabricated metrics — brief is qualitative, grounded on real Finnhub data; structurally cannot carry a numeric field; never sets a numeric `Recommendation` field.
- Dead-letter resilience — every path (grounding, provider construction, provider call, cache) soft-fails to `None`/unchanged-rec; Opal's failure never blocks Claude's call and vice versa.
- Opt-in default-off — zero `openai` import + zero network when `OPAL_RESEARCH_ENABLED=False` (verified: `python -m engine.opal_research AAPL` with Opal off never touches `sys.modules["openai"]`).
- No GUI-writable secret — `OPENAI_API_KEY` in `SECRET_KEYS` only.
- No top-level LLM SDK reach in `engine/advisory.py` or `llm/research.py`.
- `context` passed to `enrich_with_llm_rationale` / `generate_research_brief` is never mutated in place — a local copy carries the injected `"research_brief"` key so the caller's dict is untouched.

### Test surface
- **`tests/test_openai_provider.py`**: fake `openai` module in `sys.modules`; `.beta.chat.completions.parse()` happy path returns `.message.parsed`; refusal → `None`; exception → `None`; missing SDK → `_client=None` → call returns `None`; timeout passed at client init. Soft-fail contract: `call_structured` NEVER raises.
- **`tests/test_research_brief.py`**: `ResearchBrief` schema bounds (catalysts/risk_factors 1-4, recent_developments 0-4, no numeric field type-check); `generate_research_brief` happy path via injected `provider`/`grounding_fn`; cache hit skips provider; opt-in default-disabled → `None`; empty symbol → `None`; `_gather_grounding` real-shape degradation (no Finnhub key → empty packet, never invents headlines).
- **`tests/test_opal_pipeline_integration.py`**: brief threads into `working_context["research_brief"]`; `_format_rationale_user_prompt` includes the Research context block when present, omits it when absent (byte-identical to pre-Opal); `Recommendation` numeric fields byte-identical before/after enrichment (CONSTRAINT #4); Opal disabled → no `OpenAIProvider` construction; Opal succeeds + Claude fails (and vice versa) → only the succeeding field populates.
- **`tests/test_gui_env_io_openai_key.py`**: `write_setting("OPENAI_API_KEY", …)` raises `SecretWriteError`; the three `OPAL_RESEARCH_*` toggles are GUI-writable.
- **`tests/test_opal_research_panel.py`**: `format_research_brief_markdown` None/full/partial rendering; `_render_opal_research_section` wiring (source grep for the Section-0 placement + independent master-switch gate).

## AI Control Center tab (`gui/ai_control_center.py`, 2026-07)

### Overview
A single **"🎛️ AI Control Center"** Streamlit tab (tab 14 in `gui/app.py`) — the one operator-facing surface for **every** AI option on the platform. It **consolidates** (does not duplicate) the AI actions previously scattered across the Reports, Safety, and AI Insights tabs, plus the master switches (`GRAVITY_AI_RUNNER_ENABLED`, `OPAL_RESEARCH_*`) that weren't GUI-writable before. **Operator-only — nothing autonomous.** Every action is a button click or an operator-started `--interval` / `--agent` run they can stop (honors the standing "no automatic AI invocation" rule).

### Headless helper module: `gui/ai_control_center.py`
Streamlit-free, unit-testable (mirrors `gui/ai_insights_panel.py`). Public surface:
- **`AICapability`** — frozen dataclass describing one AI option (`key`, `label`, `enable_settings`, `provider_key_settings`, `module`, `trigger`, `toggle_key`, `help`, `provider_selector_setting`). The last field is optional and marks a capability as **flexibly routed** — its required provider key is resolved dynamically (see below) rather than statically.
- **`CAPABILITIES`** — registry of all five options in display order: `claude_commentary` (label: "Analyst rationale commentary"), `gemini_alerts` (label: "Alert commentary"), `gemini_vision`, `gravity_ai_runner`, `opal_research`. Three rows are flexibly routed — `claude_commentary` sets `provider_selector_setting="LLM_COMMENTARY_RATIONALE_PROVIDER"`, `gemini_alerts` sets `provider_selector_setting="LLM_COMMENTARY_ALERT_PROVIDER"` (their historical key names are kept for backward compatibility with existing tests/Gravity checks even though either job may now run on either provider), and `opal_research` sets `provider_selector_setting="OPAL_RESEARCH_PROVIDER"` (`"openai"` or `"gemini"`). `gemini_vision` / `gravity_ai_runner` are NOT flexibly routed (`provider_selector_setting=None`) — each has a fixed provider.
- **`_PROVIDER_KEY_MAP`** — `{"claude": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}`. Used to resolve the LIVE required key for a flexibly-routed capability from its `provider_selector_setting`'s current value.
- **`capability_status(settings, cap) -> {enabled, key_present, built, status, active_provider}`** — four-state classifier. Verdict ordering (most-blocking first): `not_built` (backing module absent — e.g. Opal before its build) > `disabled` (master switch off, or provider selector == `"none"`) > `missing_key` (enabled but the ACTIVE provider's key is unset — resolved dynamically for flexible capabilities, or via the static `provider_key_settings` tuple otherwise) > `ready`. `active_provider` is the live provider choice (e.g. `"gemini"`) for a flexibly-routed capability, else `None`.
- **`control_center_overview(settings) -> list[dict]`** — one status row per capability; `provider_keys` narrows to the single ACTIVE required key when `active_provider` is set (e.g. `["GEMINI_API_KEY"]`, not both possible keys).
- **`status_badge(status) -> str`** — maps a status token to `🟢 ready` / `⚪ disabled` / `🟡 key missing` / `🚧 not built`.
- **`validate_toggle_write(key)`** — pre-flight guard: raises `SecretWriteError` for a secret key (CONSTRAINT #3), `DisallowedKeyError` for a non-allowlisted key.
- **`opal_built() -> bool`** — soft `import llm.research` probe; the Opal row auto-activates once its backend ships (`docs/OPAL_BUILD_SPEC.md`).

### GUI panel: `gui/panels/__init__.py::render_ai_control_center`
Wrapped in `safe_panel` (CONSTRAINT #6). Four sections:
- **A — Capability grid + toggles.** One row per option: status badge + masked key-present badge (narrowed to the active provider's key for flexibly-routed rows, plus a `via: **{provider}**` caption) + an enable/disable `st.toggle` written via `gui.env_io.write_setting` (takes effect **next launch**; reads the CURRENT value from `.env` via `env_io.get_value`, not the import-frozen `settings` singleton, so a write settles after one rerun instead of re-firing on every unrelated rerun). Provider API keys stay secret-only — set by hand in `.env`.
- **B — On-demand per-symbol actions.** Symbol picker (from `state_snapshot.json`) + buttons that **REUSE the exact existing helpers** — `_render_llm_commentary_button(row, sym)` (Claude note), `_render_gemini_chart_section(sym)` (Gemini chart read), and a gated Opal button (`llm.research.generate_research_brief` when `opal_built()`, else a "requires build" caption). No logic duplication.
- **C — Gravity AI audit.** Reuses `_render_gravity_ai_runner_section()`.
- **D — Operator-launched scheduled run.** Interval input + "▶️ Start scheduled run (`--interval`)" / "🤖 Start agent loop (`--agent`)" → `orchestrator_runner.launch_scheduled_advisory(mode, interval_seconds)`; live "⏹ Stop" → `orchestrator_runner.stop_run(handle)`. Handle persisted in `st.session_state["acc_scheduled_handle"]`. You start it, you stop it — nothing runs on its own.

### Scheduling launcher: `gui/orchestrator_runner.py`
- **`launch_scheduled_advisory(mode="interval", interval_seconds=300, *, refresh_account=False) -> RunHandle`** — spawns `python main.py --interval N` (clamped `>= 30 s`) or `--agent` via `subprocess.Popen`, logging to `output/gui_scheduled.log` (`SCHEDULED_LOG_PATH`). Returns `RunHandle(mode="scheduled")`. **The ONLY scheduling mechanism the platform exposes** — strictly operator-initiated/stoppable; no cron, no daemon, no `threading.Timer`.
- **`stop_run(handle, *, timeout=5.0) -> bool`** — SIGTERM → SIGKILL escalation (`Popen.terminate`/`kill`, or `os.kill` for a handle reconstructed across a Streamlit rerun). Never raises (CONSTRAINT #6).

### env_io allowlist (`gui/env_io.py`)
- `ALLOWED_KEYS` gained `GRAVITY_AI_RUNNER_ENABLED`, `OPAL_RESEARCH_ENABLED`, `OPAL_RESEARCH_PROVIDER`, `OPAL_RESEARCH_MODEL` (non-secret toggles).
- `SECRET_KEYS` gained `OPENAI_API_KEY` (forward-compatible Opal enabler; never GUI-writable — CONSTRAINT #3).

### Opal relationship (phased)
- **Phase 1 (shipped):** Control Center works fully for the four shipped options + scheduling + toggles. The Opal row renders gated `not_built` ("requires build — see `docs/OPAL_BUILD_SPEC.md`").
- **Phase 2 (shipped, see Tier 9 Scope 4 below):** the Opal backend (`llm/research.py`) has since been built; its Control Center row auto-activated via `opal_built()` with no Control Center code changes needed, confirming the design.

### Help content (`gui/help_content.py`)
`TAB_HELP["ai_control_center"]` added (anchor `#advisory-only-mode`). `tests/test_help_content.py::test_exactly_10_tabs` bumped to 11 (ai_insights and prompts intentionally carry no TAB_HELP entry).

### Test surface
- **`tests/test_ai_control_center.py`**: CAPABILITIES completeness; `capability_status` truth table (ready/disabled/missing_key/provider-none/not_built) **including flexible-routing cases** — Gemini serving rationale is `ready` off `GEMINI_API_KEY`, Claude serving alerts is `ready` off `ANTHROPIC_API_KEY`, and the wrong key present (e.g. only `ANTHROPIC_API_KEY` set while rationale is routed to Gemini) correctly resolves `missing_key`; overview row shape including `active_provider` + narrowed `provider_keys`; toggle-write guard (rejects secret + non-allowlisted, allows Control-Center toggles); Opal-gated-when-absent; `launch_scheduled_advisory` with `subprocess.Popen` monkeypatched (interval flag, agent flag, 30 s clamp) + `stop_run` terminate/None-safe; tab-wiring + no-autonomous-scheduler + no-order-verb source greps.
- **`tests/test_gui_env_io_control_center_keys.py`** (8 tests): the four new toggles in `ALLOWED_KEYS`; `OPENAI_API_KEY` in `SECRET_KEYS` and not in `ALLOWED_KEYS`; `write_setting("OPENAI_API_KEY", …)` raises `SecretWriteError`.

### Gravity step 78 (`step_78_ai_control_center_audit`)
11 checks: module surface + CAPABILITIES covers 5 options; new toggles in `ALLOWED_KEYS`; `OPENAI_API_KEY` secret-only; `capability_status` truth table; `launch_scheduled_advisory` + `stop_run` callable; launcher spawns via subprocess with no autonomous scheduler; tab registered in `gui/app.py`; `validate_toggle_write` rejects secret + non-allowlisted keys; Opal gated `not_built` while `llm.research` absent; both test files exist; **flexible per-job routing** — the `claude_commentary` row resolves `ready` off `GEMINI_API_KEY` when routed to Gemini, and `gemini_alerts` resolves `ready` off `ANTHROPIC_API_KEY` when routed to Claude.

### Critical invariants (must never regress)
- **Operator-only / no autonomous AI** — every action is a button or an operator-started `--interval`/`--agent` run they can stop. No background scheduler, no self-call. Enforced by step 78 check 6.
- **CONSTRAINT #3** — `OPENAI_API_KEY` (and all provider keys) stay `SECRET_KEYS`-only; `validate_toggle_write` and `write_setting` refuse any secret key. Enforced by step 78 checks 3 + 8.
- **No logic duplication** — Section B/C buttons call the exact existing helpers (`_render_llm_commentary_button`, `_render_gemini_chart_section`, `_render_gravity_ai_runner_section`, `orchestrator_runner`).
- **CONSTRAINT #6** — every section wrapped; a not-built capability (Opal) degrades to a caption, never a crash.
- **Flexible routing resolves the ACTIVE provider's key, never a stale static one** — a capability's readiness must reflect the LIVE value of its `provider_selector_setting`, not a fixed `provider_key_settings` tuple. Enforced by step 78 check 11.

## Prompt Registry (`prompt_registry/`, 2026-06)

Versioned, cryptographically-signed, remotely-updatable store for every AI-facing instruction
(master pre-prompt, Gravity step bodies, etc.).

### Security boundary — MUST NEVER BE VIOLATED

**Fetched prompts are advisory text only.** The registry can change what an AI is *told*; it
cannot change what the platform is *permitted to do*. Order submission, advisory quarantine,
risk gates, and the kill switch are enforced in Python code — never in prompt bodies.
This invariant is audited on every Gravity run (step 69, check 7).

### Package structure

| Module | Role |
|---|---|
| `prompt_registry/models.py` | `PromptRecord`, `PromptVersion`, `RegistryManifest` frozen dataclasses |
| `prompt_registry/signing.py` | HMAC-SHA256 `sign()` / `verify()` + `compute_sha256()` |
| `prompt_registry/guardrails.py` | `validate_prompt()` — rejects `ADVISORY_ONLY=false`, `submit_order`, size overflow |
| `prompt_registry/cache.py` | `CacheManager` — signed-version disk cache + `read_baseline()` / `list_baseline_ids()` |
| `prompt_registry/store.py` | `PromptStore` ABC + `LocalJSONStore` + `HTTPStore` (stdlib `urllib`, no new dep) |
| `prompt_registry/registry.py` | `PromptRegistry` — resolution chain, `sync()`, `rollback()`, `get_registry()` singleton |
| `prompt_registry/__main__.py` | CLI: `list`, `get`, `sync`, `pin`, `rollback`, `diff`, `verify`, `publish` |
| `prompt_registry/baseline/` | Git-committed fallback bodies (always available, zero network) |

### Resolution chain (CONSTRAINT #4 — never `""`)

```
Pin (PROMPT_REGISTRY_PINS) → Remote latest (verified) → Disk cache (verified) → Baseline → sentinel
```

### Secret keys (CONSTRAINT #3)

Four `PROMPT_REGISTRY_*` credentials live in `gui/env_io.SECRET_KEYS` and are **never**
GUI-writable. Set them by hand in `.env` only:

| Key | Role |
|---|---|
| `PROMPT_REGISTRY_URL` | Protected HTTPS URL of the signed manifest |
| `PROMPT_REGISTRY_TOKEN` | Bearer read-token (runtime platform) |
| `PROMPT_REGISTRY_PUBLISH_TOKEN` | Higher-privilege publish credential (author machine only) |
| `PROMPT_REGISTRY_SIGNING_KEY` | HMAC-SHA256 verification key |

Three non-secret tunables (`PROMPT_REGISTRY_ENABLED`, `PROMPT_REGISTRY_BACKEND`,
`PROMPT_REGISTRY_PINS`) are in `gui/env_io.ALLOWED_KEYS` and writable from the GUI Prompts tab.

### Constraints enforced by this codebase

- **CONSTRAINT #3** — 4 secret keys masked + raise `SecretWriteError` on GUI write attempt.
- **CONSTRAINT #4** — `get()` never returns `""`; fails closed to baseline then sentinel.
- **CONSTRAINT #5** — `PROMPT_REGISTRY_REFRESH_SECONDS` defaults to `0`; sync is explicit only
  (CLI or GUI "🔄 Sync" button, never on a timer or at table render).
- **CONSTRAINT #6** — every fetch/verify/parse path in `registry.py` and `__main__.py` degrades
  gracefully; no exception propagates past the GUI boundary.

### Gravity step 69 (`step_69_prompt_registry_audit`)

10 checks:
1. `prompt_registry` importable; `get_registry`, `PromptRegistry`, `PromptRecord` exist.
2. Fail-closed: with no URL/cache, `get("gravity.system")` returns the baseline (non-empty).
3. `verify(tampered_body)` is `False`; `verify(signed_body)` is `True`.
4. Guardrail rejects `ADVISORY_ONLY=false` body and `submit_order` body.
5. Four `PROMPT_REGISTRY_*` secret keys in `SECRET_KEYS` AND not in `ALLOWED_KEYS`.
6. Disabling registry leaves Gravity prompts byte-identical to baseline.
7. No `eval`/`exec`/`import` in `prompt_registry/` source or `ai_verification_prompts.py`.
8. `PROMPT_REGISTRY_REFRESH_SECONDS` default is `0` (CONSTRAINT #5).
9. CLI `verify` exits non-zero on a corrupt cache fixture.
10. `tests/test_prompt_registry_resolution.py` exists.

### Operational notes

- `python -m prompt_registry get master_preprompt` — fetch and print the resolved body.
- `python -m prompt_registry sync` — explicit pull from remote manifest.
- `python -m prompt_registry rollback <id>` — pin to previous cached version.
- Publishing v1.1.0 and moving the "latest" pointer is the "update over the internet" mechanism.
- See `docs/HOW_TO_GUIDE.md §16` for the full operator workflow.
- See `docs/RUNBOOK.md §7` for the publish/rollback incident playbooks.
