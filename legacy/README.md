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
