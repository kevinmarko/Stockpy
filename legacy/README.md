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
