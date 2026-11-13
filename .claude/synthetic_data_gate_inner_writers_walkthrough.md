# Synthetic-data gate, part 2: the inner writers (walkthrough)

Branch `synthetic-data-gate-inner-writers`. Follow-up to PR #1142 (`952cde40`, the keyless gate),
plan Q2 of `.claude/synthetic_data_gate_keyless_implementation_plan.md`, approved by the operator on
2026-11-13. Known issue: `docs/known_issues/synthetic_data_gate_keyless_cycle_2026_10.md`. Tracker:
`.claude/synthetic_data_gate_inner_writers_task.md`.

## The problem

`main_orchestrator.run_pipeline` builds its own `RunContext` for the inner steps (`MacroStep`,
`TrendVolatilityStep`, `ProcessingStep`, `ForecastingStep`, `StrategyEvalStep`). The flag that
`AsyncDataFetchStep` sets on the outer context never reached it, so every durable writer inside
`RunPipelineStep` still wrote on a `MockDataEngine` cycle. `AdvisoryOverlayStep` has the outer
context but did not pass the flag to `engine.advisory.evaluate`.

## How the flag gets there now

- `RunPipelineStep` calls `run_pipeline(..., data_is_synthetic=is_cycle_synthetic(ctx),
  synthetic_reason=synthetic_reason(ctx))`. Both are keyword-only with today's defaults, so a direct
  caller of `run_pipeline` is unchanged.
- `run_pipeline` copies them onto the inner context with the new
  `pipeline/synthetic_gate.py::propagate_synthetic_flag`. It does not log or alert: the outer cycle
  already did (`mark_cycle_synthetic`).
- Steps that hold a `ctx` check `is_cycle_synthetic(ctx)`. Code with no `ctx` (the forecasting
  engine, the macro engine, `engine.advisory.evaluate`) gets a keyword from its caller: `persist=` /
  `persist_forecast=`. This is the least invasive correct option. Those engines are shared warm
  objects, so a flag on the instance would leak across cycles. A context variable would be a global
  and would not cross the `ThreadPoolExecutor` that `ForecastingStep` uses. Defaults keep every
  other caller (the MCP tools, the metrics API, scripts) byte-identical.

## Every writer reachable from RunPipelineStep and AdvisoryOverlayStep

Found by reading each step end to end and grepping each engine, signal module and helper it calls
for file, DB, alert and cache writes.

### Gated (skipped on a synthetic cycle)

| # | Writer | Where | What it did on a Mock cycle | Gate |
|---|---|---|---|---|
| 1 | `ForecastTracker.update_actuals` | `ForecastingStep` -> `generate_forecast` | Stamped the Mock price (~$10) onto REAL matured past forecasts as their realized actual. This corrupts skill weights and the naive gate. Not listed in the first known-issue doc; the worst one. | `generate_forecast(persist=False)` |
| 2 | `ForecastTracker.record_forecasts` | same | $10 forecast rows in `forecast_errors` | same |
| 3 | Forecast model cache (Prophet `.pkl`, CNN-LSTM `.keras` + scalers, incl. the subprocess worker's `save_path`) | `run_prophet_forecast` / `run_cnn_lstm_forecast` | With `FORECAST_MODEL_PERSISTENCE_ENABLED` (default off) a model fitted on Mock bars was saved, and real cycles reloaded it for `FORECAST_MODEL_RETRAIN_DAYS` | `persist=False`: the save is skipped; the LOAD of a fresh cached model still runs |
| 4 | `macro_history` upsert | `MacroStep` -> `MacroEngine.compute_hmm_risk_on_probability` -> `HistoricalStore.get_macro()` top-up | Upserted `MockDataEngine.fetch_macro_history()`'s fabricated VIX/T10Y2Y series under `source="fred"`. Real cycles read it for the HMM until the next refresh | `persist=False`: the store is bypassed and the history comes straight from the engine (the existing `HISTORICAL_STORE_ENABLED=False` path) |
| 5 | Warm HMM fit (in memory, but outlives the cycle) | same | A refit on Mock SPY bars stayed in the daemon's warm detector for `retrain_freq_days` (7) of real cycles. This matters most on the empty-fetch fallback, where the warm engines are real | `persist=False`: fit and predict on `copy.deepcopy(detector)` |
| 6 | PIT feature snapshot (`PITFeatureStore.write`) | `StrategyEvalStep` | Overwrote today's real `features_<YYYYMMDD>.parquet` (one file per day) that ML retraining reads | skipped + INFO log |
| 7 | `dead_letter.json` | `StrategyEvalStep` | Replaced the last real report, or "cleared" it when nothing failed | skipped; the failure count is still logged |
| 8 | `sizing_cap_events` (`CapAuditStore.record_cap_events`) | `StrategyEvalStep` | Fake capped/not-capped history feeding `StrategyEngine`'s consecutive-capped-cycles read | skipped |
| 9 | `symbol_rating_events` (`_record_symbol_ratings`), plus the cadence check's write-mode store | `StrategyEvalStep` | Fake GOOD/BAD rows: reset real BAD streaks and drove universe auto-drops | skipped (the cadence check too) |
| 10 | Sizing-cap threshold alert (`send_alert`, `SIZING_CAP_ALERT_ENABLED`) | `StrategyEvalStep` | An outward alert about sizing on fabricated data | skipped |
| 11 | Advisory forecast-ledger rows (and model cache) | `AdvisoryOverlayStep` -> `evaluate` -> `generate_forecast` | Rows from the advisory's own refit | `evaluate(persist_forecast=False)` |

Row 11 note: the advisory refits on bars from `data.market_data.get_provider()` (the real provider),
not on the Mock engine, so those rows are not fabricated prices. It is gated anyway, so the invariant
stays simple ("a synthetic cycle adds no forecast-ledger rows"), and because a synthetic cycle often
coincides with a data outage. The cost is one cycle of advisory coverage.

### Kept, and why

| Writer | Why it is safe to keep |
|---|---|
| `CREATE TABLE IF NOT EXISTS` / schema stamps when a store is constructed (`ForecastTracker()` in `ForecastingStep`, `HistoricalStore()`) | Idempotent schema DDL, no data rows |
| `ProcessingEngine.calculate_fundamental_metrics` -> `HistoricalStore.get_fundamentals(provider=get_provider())` | Fetches REAL fundamentals from the composite provider (FMP/yfinance), not from `ctx.market`. A real cycle writes the same row |
| `_apply_fmp_analyst/_earnings/_insider/_sector` (`upsert_analyst_snapshot`, `upsert_earnings_events`, `mark_earnings_fetched`, `upsert_insider_stats`, `mark_insider_fetched`, `upsert_sector_snapshots`) | Real FMP vendor archives keyed by symbol/date, independent of the synthetic inputs. The columns are also read back from the store, so skipping the upsert would change computation |
| `NewsCatalystSignal.pre_compute`: `save_finbert_scores`, `fetch_and_archive` (social documents), `save_news_sentiment` (`NEWS_HISTORY_CAPTURE_ENABLED`); `credibility.save_verification` | Real headlines and social documents, plus content-hash caches of scoring them. None of it uses Mock prices, fundamentals or macro |
| `_apply_sector_selection` -> `record_correlations` (default off) | Computed from the store's real-provider bars and sentiment, once per trading day |
| Advisory inputs and `evaluate`'s `HistoricalStore.get_bars` write-through (`fetch_bars_for_universe`, `build_context_extras`) | Real-provider bars |
| In-memory per-cycle state: signal-module scores (recomputed every `pre_compute`), `ForecastingEngine` guard stats (popped each cycle), GARCH estimator (stateless), advisory singletons | Not durable, and nothing carries a synthetic fit forward (unlike the HMM, row 5) |
| The gate's own CRITICAL log + deduplicated alert, the `SYNTHETIC DATA` progress message, the daily-summary warning line | Explicitly about the synthetic state (PR #1142) |
| Read-only stores (`TrendsStore(readonly)`, `SymbolRatingStore(readonly)` for the rating columns, `TransactionsStore(readonly)` / `BrokerFillsStore(readonly)` in `evaluate_portfolio`, `PaperAccountStore(readonly)`) | Reads |
| Telemetry log lines | Logs |

## Computation is unchanged, with three deliberate exceptions

`tests/test_synthetic_data_gate_inner_writers.py::TestComputationUnchanged` runs the same bars once
as a Mock cycle and once through a non-Mock engine. Price, GARCH_Vol, ARIMA, Forecast_10/30/60/90,
Action Signal, Kelly Target and Score match to 1e-9. Prophet is off in both runs because it makes
`Forecast_30` differ between two identical REAL cycles here (measured with a temporary probe; not
caused by this change). Monte Carlo is seeded. The exceptions, all on purpose:

1. With `update_actuals` skipped, that call's later ledger reads (naive-gate stats, skill weights) do
   not see the synthetic actuals. Seeing them was the bug.
2. The HMM's macro history comes from the engine directly instead of DB-cache plus engine top-up. On
   the empty-fetch fallback (warm REAL macro engine) that is a live FRED fetch instead of the cache.
3. The symbol-rating cadence check is not run. It only decides whether to write.

## Tests

`tests/test_synthetic_data_gate_inner_writers.py` (15 tests):

- A full `_main_body` cycle in the daemon shape (warm `EngineContext.build(data_engine=...)` + an
  injected engine), offline, with every inner writer switched on. On `MockDataEngine`:
  - one seeded forecast row and no new ones; the seeded matured REAL forecast stays un-actualized;
  - the previous `dead_letter.json` is kept byte-for-byte;
  - 0 rating, cap-audit and `macro_history` rows; no PIT file and no model artifact;
  - the warm HMM stays unfitted; no sizing alert;
  - the advisory got `persist_forecast=False`.
- Control: the identical bars through `_RealEngineOverMockData` (not a `MockDataEngine` subclass, so
  the gate calls it real) write every one of those.
- Unit tests:
  - `propagate_synthetic_flag` (no log, no alert);
  - the `RunPipelineStep` -> `run_pipeline` hand-off and the inner-context marking;
  - `generate_forecast(persist=)` against a real `ForecastTracker`;
  - the Prophet cache: save skipped, and the load proven by making a refit raise;
  - `compute_hmm_risk_on_probability(persist=)`, including equality with the store-disabled path;
  - `evaluate(persist_forecast=)` forwarding.

Every store is the root `conftest.py` per-test copy. Two new autouse fixtures were needed:

- `_isolate_cap_audit_db_in_tests`: before it, every test driving `StrategyEvalStep` wrote
  `sizing_cap_events` into the shared `quant_platform.db`. This sandbox's copy held 267 such rows.
- `_isolate_pit_feature_store_in_tests`: before it, tests rewrote today's
  `ml_feature_cache/features_<date>.parquet`. This sandbox's copy was modified today by test runs.

Rows already leaked on a real machine are not cleaned up here.

Mutation check: 17 mutants, one per gate and seam, each applied to a copy, run against the new file,
and restored from a backup (hash-verified, no `git stash`). All 17 were killed. Script and log are
in the agent scratchpad (`gate2_agent/mutate.py`, `mutation.log`).

Not covered: the CNN-LSTM save gates (in-process save and the subprocess `save_path`). TensorFlow is
not installed in this sandbox, so `run_cnn_lstm_forecast` returns before reaching them. They are the
same one-condition change as the Prophet gate, which is tested.

Test-only signature updates: fakes that enumerated parameters now accept `**_kw`:

- `tests/advisory_frozen_inputs.py` (`compute_hmm_risk_on_probability`);
- `tests/test_forecasting_step_fallback_disclosure.py`;
- `tests/test_forecast_model_persistence.py`;
- `tests/test_forecasting_improvements.py`.

The symbol-rating gate is written as `if synthetic: pass / elif _daemon_should_record_ratings(...)`.
That keeps `tests/test_symbol_rating_wiring.py`'s AST guard satisfied. The settings census
artifacts (`docs/settings_liveness.json`, `docs/settings_field_census.{json,md}`) were regenerated
for the three new `settings.X` reads.

## Still unverified

- Not run against the live daemon. Whether it was ever keyless is still the open question from the
  first PR.
- No cleanup tool for `macro_history` rows a past keyless cycle may have written (see the
  known-issue doc). `scripts/clean_forecast_ledger.py` rule (d) still covers $10 forecast rows, but
  not real rows whose `actual_price` was overwritten by `update_actuals` with a Mock price; those
  cannot be told apart from the ledger alone.
