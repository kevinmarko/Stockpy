# Task Tracker: FMP Pipeline Optimization (6-Agent Phased Build)

- [x] **Phase 1: Foundation, Dead Code & Documentation Cleanup (Agent 1: Cleanup & Hygiene Specialist)**
  - [x] Workstream C: Delete unused `earnings_calendar` from `data/fmp_client.py` <!-- id: 1.1 -->
  - [x] Workstream C: Correct `FMP_ECON_INDICATORS` docstring in `settings.py` <!-- id: 1.2 -->
  - [x] Workstream D: Document `CNN_LSTM_PROCESS_POOL_WORKERS=3` in `settings.py`, `docs/known_issues/cnn_lstm_tf_deadlock.md`, `docs/architecture/signal-engines.md` <!-- id: 1.3 -->
  - [x] Workstream D: Correct `docs/FMP_INTEGRATION.md`:24 `/income-statement-ttm` statement <!-- id: 1.4 -->
  - [x] Workstream D: Add comment above `fetch_volatility_benchmarks()` in `data/fmp_feeds_market.py` <!-- id: 1.5 -->

- [x] **Phase 2: Pipeline Shared Deadline Optimization (Agent 2: Pipeline Runtime Engine Specialist)**
  - [x] Workstream A: Refactor `_apply_fmp_analyst`, `_apply_fmp_earnings`, and `_apply_fmp_insider` in `pipeline/production_steps.py` with `deadline: Optional[float] = None` <!-- id: 2.1 -->
  - [x] Workstream A: Refactor `StrategyEvalStep.run` to instantiate and pass shared cycle deadline <!-- id: 2.2 -->
  - [x] Workstream A: Add `TestSharedDeadline` in `tests/test_production_steps_fmp_stubs.py` <!-- id: 2.3 -->

- [x] **Phase 3: Economics Calendar Diagnostic Feed (Agent 3: Macro & Economics Feed Specialist)**
  - [x] Workstream B: Add `FMP_ECON_CALENDAR_ENABLED: bool = Field(default=True, ...)` to `settings.py` <!-- id: 3.1 -->
  - [x] Workstream B: Add `Next_Macro_Event` and `Next_Macro_Event_Date` to `config.py` `COLUMN_SCHEMA` <!-- id: 3.2 -->
  - [x] Workstream B: Implement `_apply_fmp_econ_calendar` and wire into `StrategyEvalStep.run` in `pipeline/production_steps.py` <!-- id: 3.3 -->
  - [x] Workstream B: Expose `FMP_ECON_CALENDAR_ENABLED` in `_FMP_GROUPS` (`api/pilots_api.py`) and `FMP_LABEL_MAP` (`webapp/src/screens/FmpSettings.tsx`) <!-- id: 3.4 -->
  - [x] Workstream B: Add unit tests in `tests/test_production_steps_fmp_stubs.py` and `tests/test_fmp_feeds_market.py` <!-- id: 3.5 -->

- [x] **Phase 4: Provider Selection, Default Flips & UI Exposure (Agent 4: Settings, Provider & UI Specialist)**
  - [x] Workstream E: Flip 14 `FMP_*` capability booleans and `FMP_QUOTES_REALTIME` to `default=True` in `settings.py` with risk annotations <!-- id: 4.1 -->
  - [x] Workstream E: Change `MARKET_DATA_PROVIDER` default to `"fmp"` and `FUNDAMENTALS_SOURCE` default to `"fmp"` in `settings.py` <!-- id: 4.2 -->
  - [x] Workstream E: Populate all 31 FMP settings across `_FMP_GROUPS` in `api/pilots_api.py` and `ALLOWED_KEYS` in `gui/env_io.py` <!-- id: 4.3 -->
  - [x] Workstream E: Complete `FMP_LABEL_MAP` in `webapp/src/screens/FmpSettings.tsx` <!-- id: 4.4 -->

- [x] **Phase 5: Test Suite Parity & Regression Audit (Agent 5: Test Suite & Regression Auditor)**
  - [x] Workstream E: Update `tests/test_market_data.py` for new provider and capability defaults <!-- id: 5.1 -->
  - [x] Workstream E: Verify `tests/test_production_steps_fmp_stubs.py` and `tests/test_fmp_feeds_market.py` pass without regressions <!-- id: 5.2 -->
  - [x] Workstream E: Ensure `TestFMPSettingsDefaults` in `tests/test_settings.py` tests fresh `Settings(_env_file=None)` with all flags on <!-- id: 5.3 -->
  - [x] Workstream E: Run full targeted test suite across all FMP-related tests (377 passed) <!-- id: 5.4 -->

- [x] **Phase 6: QA Gate, Documentation & Release Artifacts (Agent 6: QA, Docs & Gatekeeper Agent)**
  - [x] Update `docs/FMP_INTEGRATION.md`, `CLAUDE.md`/`AGENTS.md`, `docs/architecture/data-layer.md`, `.env.example` <!-- id: 6.1 -->
  - [x] Commit PR artifacts (`implementation_plan.md`, `task.md`, `walkthrough.md`) to `.claude/` for multi-agent handoff <!-- id: 6.2 -->
  - [x] Run full CI verification gate (`pytest` suite + `npm run typecheck`) <!-- id: 6.3 -->
  - [x] Compile release notes and walkthrough summary <!-- id: 6.4 -->
