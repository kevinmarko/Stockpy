# Modular Multi-Source Knowledge Pack (6-Agent Execution) Task Tracker

## Agent Tasks Breakdown

### Agent 1: Macro & Regime Specialist
- [x] Implement `generate_macro_regime_source()` in `scripts/export_notebooklm.py`
- [x] Extract FRED series (`VIXCLS`, `T10Y2Y`, `BAMLH0A0HYM2`) from `HistoricalStore(readonly=True)`
- [x] Extract HMM regime state, HMM risk-on probability, and macro kill-switch from `output/state_snapshot.json`
- [x] Format `01_macro_and_regime.md` with explicit degradation on missing/cold data

### Agent 2: Portfolio & Greeks Specialist
- [x] Implement `generate_portfolio_greeks_source()` in `scripts/export_notebooklm.py`
- [x] Query `HistoricalStore(readonly=True).latest_account_snapshot()` with `_serialize_portfolio`
- [x] Calculate live portfolio net Greeks via `pilots.options_risk.calculate_portfolio_greeks()`
- [x] Format `02_portfolio_and_greeks.md` with position details, cost basis, market value, and Greeks totals

### Agent 3: Strategy Signals & Picks Specialist
- [x] Implement `generate_signals_picks_source()` in `scripts/export_notebooklm.py`
- [x] Extract `signals` array from `output/state_snapshot.json`
- [x] Format top ratings, conviction, `buy_range`, `sell_range`, Kelly sizing, and multifactor z-scores
- [x] Incorporate active strategy subscriptions from `FollowsStore().list_active()`
- [x] Format `03_strategy_signals_and_picks.md`

### Agent 4: Trade Journal & Ledger Specialist
- [x] Implement `generate_trade_journal_source()` in `scripts/export_notebooklm.py`
- [x] Query `pilots.trade_history.trade_history_view()` and `BrokerFillsStore(readonly=True)`
- [x] Format performance KPI cards (win rate, profit factor, total realized PnL, avg holding days)
- [x] Format chronological recent closed trades table in `04_trade_journal_and_ledger.md`

### Agent 5: Options Directives & Matrix Specialist
- [x] Implement `generate_options_matrix_source()` in `scripts/export_notebooklm.py`
- [x] Extract active directives from `output/options_matrix.json`
- [x] Format candidate tables with spreads, target DTE, IV Rank, GARCH sigma, delta targets, Altman Z, and Piotroski scores
- [x] Format `05_options_directives_and_matrix.md`

### Agent 6: Exporter Driver, Test & Honesty Auditor
- [x] Implement modular CLI dispatcher in `scripts/export_notebooklm.py` (supporting `--modular-only`, `--consolidated-only`, `--output-dir`, `--section`)
- [x] Ensure atomic writing (`.tmp` + `replace`) for both single file and all 5 modular files
- [x] Expand test suite in `tests/test_export_notebooklm.py` covering all 5 generators, degraded paths, and CLI flags (32 tests total)
- [x] Update `docs/GOOGLE_NOTEBOOK_INTEGRATION.md` documentation
- [x] Rebuild `cli_introspect/command_manifest.json` and synchronize `docs/settings_field_census.*` / `docs/settings_liveness.json`
- [x] Verify all 61 targeted tests pass cleanly
