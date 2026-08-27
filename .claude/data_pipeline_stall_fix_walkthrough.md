# Walkthrough: pipeline data-fetch hang fix (FRED unbounded timeout)

## What happened
The operator asked "is the data pipeline able to refresh -- it looks like
it's not working." Live investigation on the actual machine found the
persistent orchestrator daemon's current cycle frozen in `output/progress.json`
at `state: "running", stage: "data", stage_index: 0/6, symbols_done: 0/30`
for 15+ minutes with zero movement, and `output/last_data_refresh.txt`
showing the last SUCCESSFUL cycle had completed **2.5 days earlier**. The
daemon process itself was fully healthy — its Control/Pilots APIs kept
answering throughout, which is exactly why nothing looked broken until asked
about directly.

## Immediate fix
Killed the wedged daemon process (`SIGTERM`, which self-force-exited after
~28s per its own existing shutdown-budget behavior) and relaunched it the
same way `launch_webapp.command` would. The very next cycle completed
end-to-end (`last_data_refresh.txt` updated within seconds), confirming this
was a one-off wedge, not a structural inability to run.

## Root cause (confirmed by reading the actual installed library source)
`fredapi.Fred.get_series()` calls a bare `urlopen(url)` with **no timeout
parameter and no session-injection hook anywhere in the class**. Two
independent call paths reached this with zero timeout protection:

1. `main_orchestrator.py::fetch_all_data_async()`'s `asyncio.gather()` over
   three concurrent sub-fetches had no overall timeout.
2. `pipeline/runner.py::AsyncPipelineRunner.run()` → `RunPipelineStep` →
   `run_pipeline()` → `MacroEngine.compute_hmm_risk_on_probability()` →
   `HistoricalStore.get_macro()` → `DataEngine.fetch_macro_history()` hits
   the *same* unbounded FRED call from a completely different stage.

A Plan-agent validation pass (dispatched before writing any code, per this
repo's "Everything else" tier planning requirement) caught path (2) — the
original diagnosis only covered path (1), which would have been an
incomplete fix.

## What was changed
- **`settings.py`**: 4 new fields — `FRED_REQUEST_TIMEOUT_SECONDS` (10.0),
  `DATA_FETCH_TASK_TIMEOUT_SECONDS` (180.0), `PIPELINE_STALL_ALERT_ENABLED`
  (True), `PIPELINE_STALL_ALERT_SECONDS` (1800).
- **`main_orchestrator.py::fetch_all_data_async()`**: each of the three
  `asyncio.to_thread(...)` sub-fetches wrapped in `asyncio.wait_for(...)`.
  Reuses the existing `isinstance(x, Exception)` dead-letter handling
  unchanged (a `TimeoutError` is an `Exception` subclass).
- **`data_engine.py`**: new `_bounded_fred_timeout(seconds)` context manager
  (`socket.setdefaulttimeout()`, scoped and always restored), wrapping the
  FRED call blocks in both `fetch_macro_raw_detailed()` and
  `fetch_macro_history()`.
- **`desktop/daemon_runtime.py`**: new `OrchestratorDaemon.maybe_alert_on_pipeline_stall()`,
  called from both `_timer_loop` per-wake spots and from `trigger_run()`
  (needed because `ORCHESTRATOR_INTERVAL_SECONDS` actually defaults to 0,
  where `_timer_loop` never wakes on its own). Fires a `WARNING` via the
  existing `observability.alerts.send_alert(dedup_key=...)` mechanism.
  Deliberately alert-only — never restarts the daemon or cancels a cycle.
- **`gui/env_io.py` / `pilots/feature_flags.py`**: registered the 4 new
  settings as GUI-writable / Feature-Flags-screen-discoverable, matching
  this repo's established convention for sibling timeout/diagnostic flags.
- **Docs**: new `docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md`
  (cross-referenced from the near-identical prior `watchlist_env_inline_comment_hang.md`
  incident), a `docs/known_issues/README.md` row, append-only additions to
  `docs/architecture/orchestration-entrypoints.md` and
  `docs/architecture/observability-and-apis.md`, and a `CLAUDE.md` bullet
  (auto-synced to `AGENTS.md` by the existing hook).

## What was deliberately NOT done
- No auto-restart/auto-cancel of a wedged cycle — this daemon process also
  hosts the Control/Pilots APIs the webapp depends on, and forcibly killing
  a mid-flight cycle risks corrupting partial state. The stall alert is
  read-only by design.
- No full line-by-line audit of the other five pipeline stages
  (`macro_options`/`processing`/`forecasting`/`strategy`/`execution`) for
  other similarly-unbounded blocking calls. This is disclosed explicitly in
  the new known-issues doc as an out-of-scope follow-up — the stall alert
  exists precisely to catch whatever that audit would have found, within 30
  minutes instead of 2.5 days.

## Verification
- `tests/test_main_orchestrator.py` — 47 passed (1 new test proving a
  hanging, not merely raising, sub-fetch degrades within bounded time).
- `tests/test_data_engine_macro_history.py` — 11 passed (5 new tests,
  including two that prove the FRED-layer bound against a REAL blocking
  socket via a black-hole TCP server, not a mocked timeout).
- `tests/test_daemon_runtime.py` + `tests/test_orchestrator_daemon.py` —
  123 passed (6 new tests covering the stall-alert's every branch, including
  dedup suppression exercised for real, not mocked).
- Combined targeted run (`test_main_orchestrator.py`,
  `test_data_engine_macro_history.py`, `test_data_engine_fetch_concurrency.py`,
  `test_daemon_runtime.py`, `test_orchestrator_daemon.py`,
  `test_pipeline_defatalize.py`, `test_progress_emission.py`,
  `test_feature_flags_registry.py`, `test_gui_env_io.py`) — 268 passed, 0 failed.
- `ruff check . --select=F821,F822,F823,E9` — clean.
- Full offline CI-mirroring suite (`pytest -m "not network and not slow"`,
  the same gate `.github/workflows/ci.yml`'s `test` job runs) — **12379
  passed, 13 skipped, 0 failed** after regenerating the two committed
  settings-census artifacts (`docs/settings_liveness.json`,
  `docs/settings_field_census.{json,md}`) that the 4 new settings fields
  made stale — `python3 scripts/settings_liveness.py --write` and
  `python3 scripts/measure_settings_census.py --write`, per each failing
  test's own regeneration hint. Re-ran both previously-failing test files
  afterward to confirm.
