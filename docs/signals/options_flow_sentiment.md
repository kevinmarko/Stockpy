# Signal: `options_flow_sentiment`

* **File:** `signals/options_flow_sentiment.py`
* **Default weight:** 10.0 (`settings.SIGNAL_WEIGHTS["options_flow_sentiment"]`)
* **Score range:** `[-1.0, +1.0]`
* **Regime gate:** Always active
* **Pilot:** Options Flow Sentiment (`options-flow-sentiment`)

---

## Rationale

Institutional options order flow provides valuable predictive information regarding informed positioning and directional alpha. Academic literature (e.g., Pan & Poteshman 2006, "The Information in Option Volume for Future Stock Prices"; Johnson & So 2012, "The Option to Stock Volume Ratio") demonstrates that non-market-maker options volume—specifically aggressive order flow (ask sweeps vs. bid sweeps)—contains leading information about future asset returns and price momentum.

Large block trades executed aggressively at or above the Ask price indicate urgency to acquire upside (for Calls) or downside protection/speculation (for Puts). Conversely, trades executed aggressively at or below the Bid represent aggressive premium selling or position liquidations.

---

## Signal Logic & Feature Scoring

The signal evaluates unusual options activity (UOA) records, order flow velocity, institutional accumulation/distribution, and earnings/news blackout windows:

1. **Net Directional Flow Sentiment**:
$$\text{Net Flow Sentiment} = \frac{\text{Bullish Notional} - \text{Bearish Notional}}{\text{Total Notional}} \in [-1.0, +1.0]$$

Where:
* **Bullish Notional** = Call Ask Sweeps ($\text{Price} \ge \text{Ask}$) + Put Bid Sweeps ($\text{Price} \le \text{Bid}$) + explicit BULLISH records
* **Bearish Notional** = Put Ask Sweeps ($\text{Price} \ge \text{Ask}$) + Call Bid Sweeps ($\text{Price} \le \text{Bid}$) + explicit BEARISH records
* **Total Notional** = Bullish Notional + Bearish Notional + Neutral Notional

2. **Flow Velocity & Institutional Accumulation/Distribution**:
* **Fast Flow Velocity**: 5-day Rate of Change ($\text{ROC}_5 = \frac{P_t - P_{t-5}}{P_{t-5}}$).
* **Institutional Accumulation/Distribution**: 20-day Rate of Change ($\text{ROC}_{20}$) vs. 200-day trend moving average ($\text{SMA}_{200}$).
* **Regime Classifications**:
  - `HIGH_VELOCITY_BULLISH`: Rapid 5d upward order flow velocity above $\text{SMA}_{200}$ $\rightarrow$ Recommendation: `BUY`.
  - `HIGH_VELOCITY_BEARISH`: Rapid 5d downward order flow velocity below $\text{SMA}_{200}$ $\rightarrow$ Recommendation: `SELL`.
  - `ACCUMULATION`: Sustained 20d accumulation with price above $\text{SMA}_{200}$ $\rightarrow$ Recommendation: `BUY`.
  - `DISTRIBUTION`: Sustained 20d distribution with price below $\text{SMA}_{200}$ $\rightarrow$ Recommendation: `SELL`.
  - `NEUTRAL`: Balanced flow and rangebound momentum $\rightarrow$ Recommendation: `NEUTRAL`.
  - `BLACKOUT`: Active earnings or news event blackout window $\rightarrow$ Recommendation: `NEUTRAL`.

3. **Earnings / News Blackout Window Filtering (`blackout_window_days=3`)**:
* Within $\pm 3$ days of high-impact earnings/news events, directional positioning is neutralized (`position_recommendation = "NEUTRAL"`, `flow_score = 0.0`, `regime = "BLACKOUT"`).

| Condition | Points / Score | Interpretation |
|-----------|----------------|----------------|
| $\text{Net Sentiment} > +0.15$ | $+0.15 \text{ to } +1.0$ | Bullish institutional call sweep / put bid flow |
| $\text{Net Sentiment} < -0.15$ | $-0.15 \text{ to } -1.0$ | Bearish institutional put sweep / call bid flow |
| $-0.15 \le \text{Net Sentiment} \le +0.15$ | $-0.15 \text{ to } +0.15$ | Balanced / neutral options order flow |
| Earnings/News Blackout Active | $0.0$ (`confidence = 0.0`) | Directional bets neutralized during earnings window |
| No UOA Data for Symbol | $0.0$ (`confidence = 0.0`) | Neutral fallback — no unusual flow detected |

---

## Flow Regime Computation (`compute_flow_regime`)

The public helper `compute_flow_regime(closes, options_flow_records=None, news_events=None, blackout_window_days=3, lag_signals=False) -> pd.DataFrame` outputs:
- `flow_score`: Normalized directional flow score in $[-1.0, 1.0]$.
- `regime`: `"ACCUMULATION"`, `"DISTRIBUTION"`, `"HIGH_VELOCITY_BULLISH"`, `"HIGH_VELOCITY_BEARISH"`, `"NEUTRAL"`, or `"BLACKOUT"`.
- `blackout_active`: Boolean indicating if bar is inside an active blackout window.
- `position_recommendation`: `"BUY"`, `"SELL"`, or `"NEUTRAL"`.

---

## Failure Modes

| Failure Mode | Impact | Handling (CONSTRAINT #4 / #6) |
|--------------|--------|-------------------------------|
| Missing / Empty Chain | No flow detected | Returns `score = 0.0, confidence = 0.0` with `"Options flow sentiment: neutral/no flow data this cycle"` explanation. |
| Single-sided 0 OI trade | Division by zero in V/OI | Volume / 0 OI safely treated as infinite ratio ($999.99\times$) without crashing. |
| Zero Total Notional | Division by zero in Net Sentiment | Returns neutral `score = 0.0`. |
| Earnings Blackout | Elevated gap risk | Automatically neutralizes directional scores to 0.0. |

---

## Interaction with Other Modules

* **`news_catalyst`**: Complements headline and social sentiment with real-time institutional money flow.
* **`timeseries_momentum` / `cross_sectional_momentum`**: Acts as an alpha overlay confirming whether institutional options positioning aligns with price trend.
* **`vrp_premium_selling`**: Differentiates between directional options flow vs. volatility-selling regimes.

---

## Backtest Validation (`options_flow_sentiment`, 2026-08)

The `options_flow_sentiment` strategy is formally joined to the validation harness via `_build_options_flow_sentiment_adapter` in `scripts/refresh_validations.py` (`validation_strategy_id="options_flow_sentiment"` in `pilots/catalog.py`).

The proxy evaluates multi-horizon flow velocity (5d/20d rate-of-change) and institutional regime pressure against macro trend filters (`SMA_200`) with strict 1-day signal lagging (zero lookahead bias). 

Walk-forward CPCV validation results:
- **Net Sharpe**: 0.231
- **PBO**: 0.111 (< 0.50)
- **DSR**: 0.906 (gated, target > 0.95)
- **Max Drawdown**: 27.7% (< 30%)
- **Status**: `deployable=False` (honestly documented baseline)

To enable machine learning and confidence gating, `meta_label_features` (`ROC_12M`, `ROC_6M`, `RSI_14`, `Vol_20`, `GARCH_Vol`, `SMA_5`, `SMA_200`) and multi-horizon targets (10d, 30d, 60d, 90d) are configured in `signals/options_flow_sentiment.py` for integration into `ml/forecast_backfill.py`'s `AgenticForecastBackfiller` and the webapp Forecasting Backfill tab.

See [`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) for strategy registry fix history.


### 2026-08-18 Full Validation Run (`options_flow_sentiment`, rebased onto `main`)

| Metric | Result |
|---|---|
| **Sharpe Ratio (net)** | 0.2132 |
| **PBO** | 0.2000 |
| **DSR** | 0.7497 |
| **Max Drawdown** | 14.17% |
| **Deployable** | ❌ False |


*Note: The 2026-08-17 run verifies stability following a systemic parser fix. The `Deployable: False` outcome and its underlying causal reasoning remain exactly as previously documented.*

---

## Defects found while analysing `pilots/unusual_options_flow.py`

A follow-up audit of the UOA scan engine this signal's `signals/options_flow_sentiment.py`
consumes (`pilots/unusual_options_flow.py`, not the signal module itself) turned up six
issues, all closed in a combined follow-up effort landed across two branches. Full detail,
root cause, and verification for every item lives in
[`docs/known_issues/earnings_crush_uoa_followup_audit_findings.md`](../known_issues/earnings_crush_uoa_followup_audit_findings.md)
(findings #3–#8); summarized briefly here:

1. **IV-burst HV30 wiring (finding #3).** The IV-burst score's historical-volatility
   denominator was not actually wired to a live 30-day realized-vol computation, so the
   burst score was silently comparing implied vol against a stale/placeholder baseline
   rather than genuine current HV30 — fixed to source HV30 live.
2. **Mid-block sentiment deadband (finding #4).** A block trade printed at the exact
   midpoint of the bid/ask spread was being force-classified as bullish or bearish rather
   than left neutral, overstating directional conviction on genuinely ambiguous prints —
   fixed with an explicit deadband around the midpoint.
3. **`price_is_estimated`/`spot_price_is_estimated` honesty flags (finding #5).** Trades
   whose fill price or underlying spot had to be estimated (rather than sourced from a
   real quote) were indistinguishable from trades with a fully real price — fixed by
   adding two new boolean fields (now present on `webapp/src/api/types.ts`'s
   `UnusualOptionTrade`) so a consumer can honestly tell an estimated print apart from a
   real one (CONSTRAINT #4).
4. **Per-contract isolation in `scan_unusual_options_activity` (finding #6).** One
   contract's malformed/missing chain data could previously abort processing for other,
   healthy contracts in the same scan pass — fixed to isolate a per-contract failure so
   the rest of the scan still completes.
5. **`degraded`/diagnostics fields on `GET /pilots/options/flow/unusual` (finding #7,
   this doc's own signal's data source).** `get_unusual_options_activity` gained an
   optional `diagnostics` kwarg (`symbols_fetch_failed`, `read_from_cache`) so the
   endpoint can report an honest `degraded: bool` and `symbols_fetch_failed: string[]`
   instead of an empty/short `records` list reading identically to "nothing unusual
   found this cycle." See finding #7 in the known-issues doc for the full detail,
   including the parallel fix applied to `GET /pilots/options/earnings-crush/candidates`
   in the same effort.
6. **Atomic write for `save_uoa_records` (finding #8).** The UOA persistence write was
   not atomic (temp-file + rename), so a crash mid-write could leave a corrupt/partial
   cache file behind — fixed to the same write-then-rename idiom used elsewhere in this
   codebase (e.g. `execution/kill_switch.py`, `desktop/orchestrator_daemon.py`'s
   `_write_daemon_file`).

## Backfill-Screen Live Meta-Labeler Bridge

This signal is eligible for the `ml/forecast_backfill.py` meta-labeler bridge.
- **Registry Key**: `meta_labeler_backfill_options_flow_sentiment`
- **Live Horizon**: 10 days (default)
- **Measured DSR/PBO**: Pending a successful run (requires explicit opt-in).
- **Feature-Compatibility Caveat**: RESOLVED — the live per-ticker row (`strategy_engine.py::evaluate_security()`) was widened with the 9 previously-missing feature names this signal (and its 5 siblings) declare, genuinely computed in `processing_engine.py`/`ml/forecast_backfill.py`, not merely whitelisted. `check_feature_compatibility()` now returns `True` for this signal's real `meta_label_features` (see `tests/test_train_meta_labelers.py::TestSixEligibleSignalsFeatureCompatibility`). A trained model for this signal can now actually reach `global_meta_registry` once it separately clears the DSR/PBO deployability gate (unchanged by this fix). Note also: this signal's declared `ROC_5`/`ROC_20` features were previously silently dropped every training run since `ml/forecast_backfill.py::step_2_calculate_technical_features()` never computed them — they are now genuinely computed there too, so this signal's feature compatibility fix is real at both the training-data and live-row level, not just the live-row level.
- **Momentum-proxy fallback deliberately suppressed on the backfill path (WP5, 2026-09)**: this signal's meta-labeler will never actually train via the Forecast Backfill screen, by design. `compute()`'s (and `compute_vectorized()`'s) legitimate live-production fallback — a price-momentum proxy computed from `ROC_5`/`ROC_20` when no real UOA (unusual-options-activity) flow data is available for a symbol — fires unconditionally on the backfill path, because `ml/forecast_backfill.py`'s dummy `SignalContext`/synthetic `pre_compute()` replay never carries real UOA flow data. Training a meta-labeler on that proxy would mean training it on pure price momentum mislabelled as options flow sentiment — a fabrication of what the model represents, not just of a number. The operator's explicit decision (not up for reconsideration by a future pass) is to suppress the fallback on the backfill path only rather than train on it: `ml/forecast_backfill.py::_run_cross_sectional_module` (this signal overrides `pre_compute`, so it is dispatched through the per-date/per-ticker SCALAR `compute()` replay there, never `compute_vectorized()`) drops the `ROC_5`/`ROC_20` columns from the per-date row source it hands to `compute()` specifically for `options_flow_sentiment`, so the fallback's column-presence check (`"ROC_5" in row and "ROC_20" in row`) never sees them and every score stays neutral (0.0, which `np.sign(...).replace(0, np.nan)` turns into `NaN`). As a result, this signal's `_Target_{h}d` columns are always all-NaN on the backfill path, `_build_training_set` always hits its `len(clean_df) < 30` branch, and `self.eligibility["options_flow_sentiment"]` honestly reports `trained: False` with a `reason` of `"insufficient_samples:0_for_<h>d"` for every horizon — never a fabricated metrics row (CONSTRAINT #4). **This is purely a training-data-integrity decision for the backfill screen** — `signals/options_flow_sentiment.py` itself was not touched at all by this fix (verified: zero diff), and this module's OWN live per-ticker behavior (the fallback firing for real when a symbol genuinely has no persisted UOA flow data mid-cycle) is completely unchanged. See `docs/plans/FORECAST_BACKFILL_PLAN.md`'s WP5 section for the full writeup and `tests/test_forecast_backfill.py`'s `test_options_flow_sentiment_live_compute_vectorized_fallback_still_fires_unchanged`/`test_options_flow_sentiment_live_compute_scalar_fallback_still_fires_unchanged`/`test_forecast_backfill_suppresses_options_flow_sentiment_momentum_proxy_on_backfill_path` for the regression coverage.
