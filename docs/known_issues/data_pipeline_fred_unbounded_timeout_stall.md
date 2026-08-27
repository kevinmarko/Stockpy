# Pipeline wedged for 2.5 days — FRED calls had no timeout (2026-08-27)

## Status
**Fixed** (this PR). Three parts: (A) each of `fetch_all_data_async()`'s three
concurrent sub-fetches is now individually bounded; (B) the actual unbounded
call — `fredapi.Fred.get_series()` — is now bounded at its own layer, which
also protects a second, independent call path Fix A does not cover; (C) a
read-only stall alert now surfaces a wedged cycle within
`PIPELINE_STALL_ALERT_SECONDS` instead of relying on an operator noticing.

## Relationship to the prior "WATCHLIST inline comment" incident
This is the **same symptom class** as
[`watchlist_env_inline_comment_hang.md`](watchlist_env_inline_comment_hang.md)
— a cycle stuck in `state: "running"` forever, restarting the daemon the only
recovery, and the operator report ("it looks like the pipeline isn't
working... I wasn't able to run it") is nearly identical — but a **different
root cause**: that incident was one malformed ticker string hitting an
unbounded network read; this one is a structurally unbounded call in the
data layer itself, reachable on any cycle regardless of ticker universe
contents.

## Symptom
Operator: "Is the data pipeline able to refresh -- it looks like it's not
working." Live investigation found:
- `output/progress.json` frozen at `state: "running", stage: "data",
  stage_index: 0/6, symbols_done: 0/30` for 15+ minutes and still climbing.
- `output/last_data_refresh.txt` last updated **2.5 days earlier** — no
  pipeline cycle had completed successfully since then.
- The daemon process itself was fully alive and its Control/Pilots APIs
  (:8601/:8602) answered normally throughout — `_run_one_cycle` runs on a
  thread separate from the APIs and the timer loop, so nothing else looked
  broken. This is exactly why it went unnoticed for 2.5 days.
- Near-zero CPU usage and **no open outbound network socket** on the daemon
  process (checked via `lsof`) — ruling out an active retry loop and
  pointing at a blocked synchronous call whose underlying TCP connection had
  already gone stale at the OS level, with nothing watching for that.
- A zombie/defunct child process was present but its relationship to the
  hang was not conclusively established and is not the fix target here.

Restarting the daemon (`kill` + relaunch `python -m desktop.orchestrator_daemon`)
immediately unstuck it — the very next cycle completed end-to-end.

## Root cause
`fredapi.Fred.get_series()` (used throughout `data_engine.py` to pull
VIXCLS/T10Y2Y/BAMLH0A0HYM2/UNRATE/etc. from FRED) calls a bare `urlopen(url)`
with **no timeout parameter and no session-injection hook anywhere in the
class** — confirmed by reading the full installed library source
(`fredapi/fred.py`), not assumed:

```python
def __fetch_data(self, url):
    url += '&api_key=' + self.api_key
    try:
        response = urlopen(url)              # <-- no timeout=, ever
        root = ET.fromstring(response.read())
    except HTTPError as exc:
        root = ET.fromstring(exc.read())
        raise ValueError(root.get('message'))
    return root
```

`urlopen()`'s default timeout is `socket.getdefaulttimeout()`, which is
`None` (block forever) unless something else in the process has changed it.
If FRED's connection stalls after connecting, this call never returns.

**Two independent, currently-live paths reached this with zero timeout
protection**, which is why bounding only one of them would not have been a
complete fix:

1. `main_orchestrator.py::fetch_all_data_async()` awaited
   `asyncio.gather(macro_task, fund_task, tech_task, return_exceptions=True)`
   with **no overall timeout**. `return_exceptions=True` isolates one task's
   *exception* from the others (already covered by
   `tests/test_main_orchestrator.py::TestFetchAllDataAsyncDeadLetter`), but
   does nothing for a *hang* — `macro_task` calls `DataEngine.fetch_macro_raw()`
   → `fetch_macro_raw_detailed()` → the unbounded FRED calls above.
2. Independently, `pipeline/runner.py::AsyncPipelineRunner.run()` dispatches
   every synchronous `PipelineStep` via `await asyncio.to_thread(step.run,
   ctx)` with no timeout either. `RunPipelineStep.run()` calls
   `main_orchestrator.run_pipeline(...)`, which builds `MacroEngine` and
   calls `compute_hmm_risk_on_probability()` → `HistoricalStore.get_macro()`
   → `DataEngine.fetch_macro_history()` → the *same* unbounded FRED calls, in
   a completely different pipeline stage than (1).

Every other external data client in this codebase (FMP: `FMP_TIMEOUT_SECONDS`
+ retries + a cooldown circuit breaker; GDELT: documented explicit read
timeouts + circuit breaker) already has a bounded timeout. FRED via
`fredapi` was the one unguarded outlier.

Separately, `desktop/daemon_runtime.py::_timer_loop` had (and, for anything
outside the two paths above, still has) no mechanism to notice or alert on a
cycle that's been silently wedged for hours/days — its own comment states
"previous cycle still in flight is expected and fine."

## Fix (this PR)
- **`main_orchestrator.py::fetch_all_data_async()`** — each of the three
  `asyncio.to_thread(...)` sub-fetches is now wrapped in
  `asyncio.wait_for(..., timeout=settings.DATA_FETCH_TASK_TIMEOUT_SECONDS)`
  (default 180s). A timeout is a `TimeoutError` (an `Exception` subclass),
  caught by the SAME existing per-task dead-letter `isinstance(x, Exception)`
  handling already used for a raised exception — no new fallback logic
  needed.
- **`data_engine.py`** — new `_bounded_fred_timeout(seconds)` context manager
  scopes `socket.setdefaulttimeout()` narrowly around every
  `self.fred.get_series(...)` call in both `fetch_macro_raw_detailed()` and
  `fetch_macro_history()` (new setting `FRED_REQUEST_TIMEOUT_SECONDS`,
  default 10.0, mirroring `FMP_TIMEOUT_SECONDS`'s per-request scope). This is
  the *mandatory* companion to the fix above, not an optional hardening —
  path (2) above is untouched by the `fetch_all_data_async` fix and would
  still hang without it. `fredapi.Fred` has no timeout or session-injection
  hook anywhere in its API (verified against the full installed source), so
  a narrowly-scoped, always-restored `socket.setdefaulttimeout()` is the only
  lever available short of vendoring the library. No new exception handling
  is needed at either call site — both already have a broad
  `except Exception` that catches `socket.timeout`/`TimeoutError`/`URLError`.
  A secondary, disclosed benefit: `asyncio.wait_for` cannot actually
  interrupt a thread already blocked inside a hung synchronous call
  (`concurrent.futures.Future.cancel()` is a no-op once running), so without
  this fix, repeated timeouts from Fix A alone would slowly leak permanently
  -blocked worker threads in the process-wide default thread pool
  (bounded at `min(32, cpu_count+4)`) until it's exhausted — bounding FRED
  itself closes this off at the source.
- **`desktop/daemon_runtime.py`** — new `OrchestratorDaemon.maybe_alert_on_pipeline_stall()`,
  gated on `settings.PIPELINE_STALL_ALERT_ENABLED` (default **True** — a
  deliberate exception to this repo's usual "new instrumentation defaults
  off" convention, justified because it's pure read-only alerting with zero
  external blast radius when no webhook/email channel is configured). Reads
  `output/progress.json` via the existing `reporting.progress.read_progress()`
  and fires a `WARNING` (`observability.alerts.send_alert`, `dedup_key="pipeline_stall"`)
  when `state == "running"` and `age_seconds() > settings.PIPELINE_STALL_ALERT_SECONDS`
  (default 1800s / 30 min). Called from both of `_timer_loop`'s per-wake
  spots AND from `trigger_run()` directly — `settings.ORCHESTRATOR_INTERVAL_SECONDS`
  actually defaults to **0** (on-demand only, not the 3600s one specific
  deployment's `.env` has it set to), and at `interval<=0` `_timer_loop`
  parks on an untimed wait, so it would otherwise never get a periodic
  chance to check. Deliberately alert-only — it never cancels the wedged
  cycle or restarts the daemon process, since this same process also hosts
  the Control/Pilots APIs the webapp depends on, and forcibly killing a
  mid-flight cycle risks corrupting partial state.

Tests: `tests/test_main_orchestrator.py::TestFetchAllDataAsyncDeadLetter::test_macro_fetch_hang_isolated_dict_fallback_within_bounded_time`;
`tests/test_data_engine_macro_history.py`'s new `TestBoundedFredTimeout` and
`TestFetchMacroCallsBoundedByRequestTimeout` classes (the latter proves the
bound against a REAL blocking socket read, not a mocked timeout);
`tests/test_daemon_runtime.py::TestMaybeAlertOnPipelineStall`.

## Scope boundary — disclosed, not silently assumed complete
This PR closes the two proven FRED-dependent hang paths and adds a generic,
read-only stall alert as a safety net for whatever else isn't covered. It
does **not** attempt a full line-by-line audit of every one of the six
pipeline stages (`data`/`macro_options`/`processing`/`forecasting`/`strategy`/
`execution`) for other similarly-unbounded blocking calls — `PIPELINE_STALL_ALERT_ENABLED`
exists specifically because that broader audit was not performed, and a
future stall from a different, still-unbounded call would be caught by the
alert (within 30 minutes) rather than by a targeted fix. A genuinely
comprehensive audit is a reasonable, separate follow-up.

`HistoricalStore.get_macro()` is also called directly from live HTTP request
handlers in `api/pilots_api.py`/`api/data_api.py` — Fix B protects those for
free (it's scoped at the `data_engine.py` layer), no separate change needed.
