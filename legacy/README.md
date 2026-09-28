# legacy/

Archived code: working code moved out of the active codebase so it can be
restored if needed. Nothing in the active platform imports from here, and
pytest does not collect it (`pytest.ini` sets `testpaths = tests`). Files keep
their original relative path under `legacy/`, and the archived tests still use
the original import paths (`from risk.etf_transmission import ...`), so to run
one you need to put the moved module back first.

Add to this directory only when archiving, and record each move below.

## Step 4d: ETF volatility transmission (2026-09)

Why: the operator decided to archive it. Every ETF flag
(`ETF_TRANSMISSION_ENABLED`, `ETF_TRANSMISSION_SIZING_ENABLED`,
`ETF_TRANSMISSION_PORTFOLIO_ENABLED`, `ETF_HOLDINGS_ENABLED`,
`ETF_HOLDINGS_ISSUER_CSV_ENABLED`) was off in the live config, so the feature
never ran and removing it did not change a pipeline cycle.

Moved:
- `risk/__init__.py`, `risk/etf_transmission.py` (the package held nothing else)
- `data/etf_holdings.py`
- `tests/test_etf_holdings.py`, `tests/test_etf_transmission.py`,
  `tests/test_etf_transmission_lookahead.py`, `tests/test_etf_transmission_portfolio.py`,
  `tests/test_etf_transmission_sensitivity_sweep.py`,
  `tests/test_production_steps_etf_transmission.py`,
  `tests/test_production_steps_etf_transmission_multiplier.py`,
  `tests/test_production_steps_etf_transmission_portfolio.py`
- `tests/fixtures/nport_sample.xml` (used only by `test_etf_holdings.py`)
- `tests/test_etf_transmission_multiplier_shape.py`: new file holding the former
  section 8 of `tests/test_position_sizer.py`, which tested
  `transmission_multiplier` directly
- `webapp/src/screens/EtfTransmissionSettings.tsx` and its `.test.tsx`

The pipeline functions that called these (`_apply_etf_transmission`,
`_apply_etf_transmission_multiplier`, `_build_etf_transmission_cov_matrix` in
`pipeline/production_steps.py`) were deleted rather than moved; git history has
them. Still in the active tree until step 4f: the four `ETF_*`
`config.COLUMN_SCHEMA` columns (written as NaN), the `ETF_*` settings fields,
`size_position()`'s `etf_transmission_multiplier` kwarg, and the
`etf_holdings` table in `data/historical_store.py`.

## Step 4e: Google Sheet publisher (2026-09)

The operator decided to retire the Google Sheet output sink — the Pilots PWA
(`webapp/`) is the platform's only frontend, and nothing reads the Sheet
anymore.

- `reporting/sheet_publisher.py` → `legacy/reporting/sheet_publisher.py`
  (the `RunResult` → Sheet row mapping, `write_recommendations()`, and the
  conditional-formatting rules).
- `reporting/sheets_client.py` → `legacy/reporting/sheets_client.py` (the
  `gspread` service-account client + `SHEET_NAME`/`TAB_NAME_OUTPUT`
  constants).
- The Sheets half of `tests/test_reporting_package.py`
  (`TestSheetsClient`, `TestSheetPublisher`) → `legacy/tests/test_sheet_publisher.py`.
  The HTML-publisher tests in that file (`TestHtmlPublisher`) are unaffected
  and stayed in `tests/`.
- `tests/test_config.py`'s `TestAdvisoryColumnCoverage` (exercised
  `rec_to_sheet_row`) → `legacy/tests/test_advisory_column_coverage.py`.

`main.py` no longer imports `reporting.sheet_publisher`/`reporting.sheets_client`,
no longer calls the Sheet sink, and no longer has a Sheet2-column-A universe
fallback (`_load_tickers_from_sheet2`) — an empty `held ∪ watchlist ∪
discovered` universe now falls straight through to
`compute_tracked_universe()`'s existing `DEFAULT_TICKERS` fallback, and stays
empty if that's empty too. `pipeline/steps.py` no longer imports `SHEET_NAME`.
`requirements.txt` no longer lists `gspread`/`gspread-dataframe` (nor
`google-auth-oauthlib`, which only `gspread` needed); `google-auth` itself is
kept — it's still a transitive dependency of `google-genai` (Gemini) and
`google-cloud-language`.

**`credentials.json` is no longer used by any active code.** It was the
Google Sheets service-account key; the real-vs-mock `DataEngine` gate that
used to key off its presence was already switched to
`data_engine.live_data_configured()` (a FRED-key check) in PR #1065, before
this Sheets retirement landed. The file itself was never tracked by git and
is left alone — the operator manages it and may still hold a copy on disk,
but nothing reads it anymore.

## Step 4b: options desk (2026-09)

Per `.claude/shrink_step4_archive_implementation_plan.md`'s operator
decision to archive the options desk, 0DTE, ETF volatility transmission, the
Google Sheet publisher, and Follow-a-Pilot (PRs 4a-4f). This PR (4b) moved
the 50 modules and 50 dedicated test files below — the orphans, the
cluster-only modules, and the "still wired" modules 4a (`#1067`) already
unwired — plus deleted `conftest.py`'s `_isolate_execution_audit_db_in_tests`
fixture (its subject, `data/execution_audit_store.py`, moved with the rest).
`data/option_symbols.py` (the OCC parser + Black-Scholes pricer 4a extracted
for paper option marking) and `volatility/garch.py`/`trend_indicators.py`
(GJR-GARCH and Aroon/Coppock/Chandelier, extracted from
`technical_options_engine.py` back in step 3d) are **not** archived — both
are core, actively used by the kept equity pipeline and paper broker.

Modules (with their `legacy/`-relative path unchanged from their original
repo-root-relative path):

- `data/execution_audit_store.py`
- `execution/almgren_chriss_router.py`, `execution/dynamic_circuit_breaker.py`,
  `execution/fix_gateway.py`, `execution/multi_broker_gateway.py`,
  `execution/options_analytics.py`, `execution/options_lifecycle.py`,
  `execution/options_paper_executor.py`, `execution/options_queue_builder.py`,
  `execution/sec_rule_606_reporter.py`
- `llm/research_copilot.py`
- `ml/drl_market_maker.py`, `ml/drl_market_maker_ppo.py`,
  `ml/options_meta_labeler.py`, `ml/transformer_vol_forecaster.py`,
  `ml/vrp_premium_selling_proxy_signal.py`
- `options_ondemand.py`, `technical_options_engine.py` (both repo-root)
- `pilots/copula_stat_arb.py`, `pilots/dispersion_trading.py`,
  `pilots/earnings_crush.py`, `pilots/gamma_scalper.py`,
  `pilots/har_volatility.py`, `pilots/lob_simulator.py`,
  `pilots/multi_leg_pricing.py`, `pilots/options.py`,
  `pilots/options_alerts.py`, `pilots/options_gex.py`,
  `pilots/options_hedging.py`, `pilots/options_risk.py`,
  `pilots/options_sor.py`, `pilots/options_vpin.py`,
  `pilots/paper_broker_options_order.py`, `pilots/realtime_risk_streamer.py`,
  `pilots/scenario_matrix.py`, `pilots/unusual_options_flow.py`,
  `pilots/vol_mispricing.py`, `pilots/volatility_surface.py`,
  `pilots/zero_dte_engine.py`
- `reporting/options_snapshot.py`
- `scripts/purge_corrupt_paper_options.py`
- `signals/options_flow_sentiment.py`, `signals/vrp_premium_selling.py`
- `sizing/hrp_cvar_optimizer.py`
- `validation/autonomous_backtest_runner.py`, `validation/options_harness.py`,
  `validation/options_selling_backtest.py`,
  `validation/synthetic_diffusion_engine.py`
- `volatility/bootstrap_iv_history.py`, `volatility/iv_engine.py`

Their 50 dedicated test files moved to `legacy/tests/` alongside them (same
filenames, e.g. `legacy/tests/test_options_risk.py`). Two of those weren't
pure 1:1 moves:

- `legacy/tests/test_dynamic_circuit_breaker.py` had one test,
  `test_soft_halt_alert_dispatch`, extracted out FIRST (it exercises
  `GlobalKillSwitch`/`observability.alerts`, not the archived circuit
  breaker) — it now lives in the active `tests/test_kill_switch.py`.
- `legacy/tests/test_options_selling_backtest_margin.py` is a new file, split
  out of the active `tests/test_walk_forward.py` (which mixed kept
  `validation/walk_forward.py` coverage with two
  `validation/options_selling_backtest.py`-only tests).

`tests/test_options_archive_import_smoke.py` (kept, active) is the
regression guard proving no active entry point (`main.py`,
`main_orchestrator.py`, `investyo_mcp_server.py`,
`broker_live_execution_mcp.py`, the three `api/*.py` services,
`execution/fmp_paper_broker.py`, `data/paper_account_store.py`,
`pipeline/production_steps.py`) needs any of the 50 modules above.
