# Task Tracker: Pipeline data-fetch hang fix (FRED unbounded timeout)

- [x] Diagnose the live incident (progress.json frozen, last_data_refresh.txt
      2.5 days stale, near-zero CPU, no open outbound socket on the daemon
      process) and root-cause it by reading the installed `fredapi` library
      source directly (`Fred.get_series()` → bare `urlopen()`, no timeout).
- [x] Manually restart the wedged daemon (verified the next cycle completed
      end-to-end) to restore live service before starting the code fix.
- [x] Confirm a SECOND independent unprotected path
      (`pipeline/runner.py::AsyncPipelineRunner.run()` →
      `RunPipelineStep`/`run_pipeline()` → `MacroEngine` → `fetch_macro_history()`)
      via a Plan-agent validation pass — this is why bounding FRED itself,
      not just the outer `fetch_all_data_async` gather, is mandatory.
- [x] Fix A: bound each of `fetch_all_data_async()`'s three concurrent
      sub-fetches with `asyncio.wait_for(...)` (`main_orchestrator.py`).
      New setting `DATA_FETCH_TASK_TIMEOUT_SECONDS` (default 180.0).
- [x] Fix B: `_bounded_fred_timeout()` context manager in `data_engine.py`,
      wrapping every `self.fred.get_series(...)` call in
      `fetch_macro_raw_detailed()`/`fetch_macro_history()`. New setting
      `FRED_REQUEST_TIMEOUT_SECONDS` (default 10.0).
- [x] Fix C: `OrchestratorDaemon.maybe_alert_on_pipeline_stall()` in
      `desktop/daemon_runtime.py`, wired into both `_timer_loop` per-wake
      spots and `trigger_run()`. New settings `PIPELINE_STALL_ALERT_ENABLED`
      (default True) / `PIPELINE_STALL_ALERT_SECONDS` (default 1800).
- [x] Register the 4 new settings in `gui/env_io.py::ALLOWED_KEYS` and
      `PIPELINE_STALL_ALERT_ENABLED` in
      `pilots/feature_flags.py::DIAGNOSTIC_FLAG_REASONS`.
- [x] New test:
      `tests/test_main_orchestrator.py::TestFetchAllDataAsyncDeadLetter::test_macro_fetch_hang_isolated_dict_fallback_within_bounded_time`.
- [x] New tests: `tests/test_data_engine_macro_history.py`'s
      `TestBoundedFredTimeout` (pure unit) and
      `TestFetchMacroCallsBoundedByRequestTimeout` (proves the bound against
      a REAL blocking socket via a black-hole TCP server, not a mock).
- [x] New tests: `tests/test_daemon_runtime.py::TestMaybeAlertOnPipelineStall`
      (6 cases: disabled flag, no progress file, fresh state, completed
      state, stale-past-threshold fires once, dedup suppresses a repeat).
- [x] Full targeted suite green: `test_main_orchestrator.py` (47),
      `test_data_engine_macro_history.py` (11), `test_data_engine_fetch_concurrency.py`,
      `test_daemon_runtime.py` + `test_orchestrator_daemon.py` (123),
      `test_pipeline_defatalize.py`, `test_progress_emission.py`,
      `test_feature_flags_registry.py`, `test_gui_env_io.py` — 268 passed, 0 failed.
- [x] Docs: new `docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md`,
      `docs/known_issues/README.md` index row,
      `docs/architecture/orchestration-entrypoints.md` append,
      `docs/architecture/observability-and-apis.md` append, `CLAUDE.md` bullet
      (`AGENTS.md` auto-synced via the existing hook — verified in sync).
- [x] Ran the full offline CI-mirroring suite (`pytest -m "not network and
      not slow"`, matching `.github/workflows/ci.yml`'s `test` job) — 12379
      passed, 13 skipped, 0 failed, after regenerating the 2 committed
      settings-census artifacts the 4 new settings fields made stale
      (`scripts/settings_liveness.py --write`,
      `scripts/measure_settings_census.py --write`). `ruff check . --select=F821,F822,F823,E9` clean.
- [x] Opened PR against `main` from `claude/data-pipeline-refresh-e7ccf7` with
      these `.claude/data_pipeline_stall_fix_*` artifacts committed —
      [PR #916](https://github.com/kevinmarko/Stockpy/pull/916).
