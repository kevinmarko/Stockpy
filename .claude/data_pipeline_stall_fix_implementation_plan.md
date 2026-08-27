# Fix: pipeline data-fetch stage can hang forever (FRED calls have no timeout)

## Context

The persistent orchestrator daemon wedged in the "data" stage of a pipeline cycle for
15+ minutes (near-zero CPU, no open outbound socket — a blocked synchronous call, not
an active retry loop) and never recovered on its own. Nothing had refreshed in ~2.5
days until the user noticed and I manually killed and relaunched the daemon (verified:
the very next cycle completed cleanly end-to-end). This plan is the actual fix so a
human doesn't have to notice and manually intervene again.

Root cause, confirmed by reading the real installed library source, not guessed:
`fredapi.Fred.get_series()` (used by `data_engine.py` to pull VIXCLS/T10Y2Y/etc. from
FRED) calls a bare `urlopen(url)` with **no timeout parameter and no session-injection
hook anywhere in the class** (read the full 471-line installed `fredapi/fred.py`). If
FRED's connection stalls, that call blocks forever. Two independent, currently-live
paths reach it with zero timeout protection:

1. `main_orchestrator.py::fetch_all_data_async()` (~line 244) awaits
   `asyncio.gather(macro_task, fund_task, tech_task, return_exceptions=True)` with
   **no overall timeout** — `return_exceptions=True` isolates one task's *exception*
   from the others (already tested — see `tests/test_main_orchestrator.py::TestFetchAllDataAsyncDeadLetter`)
   but does nothing for a *hang*. `macro_task` calls `DataEngine.fetch_macro_raw()` →
   `fetch_macro_raw_detailed()` → the unbounded `self.fred.get_series(...)` calls.
2. Independently, `pipeline/runner.py::AsyncPipelineRunner.run()` dispatches every sync
   `PipelineStep` via `await asyncio.to_thread(step.run, ctx)` with **no timeout**
   either. `RunPipelineStep.run()` calls `main_orchestrator.run_pipeline(...)`, which
   builds `MacroEngine` and calls `compute_hmm_risk_on_probability()` →
   `HistoricalStore.get_macro()` → `DataEngine.fetch_macro_history()` → the *same*
   unbounded `self.fred.get_series(...)` calls, in a completely different pipeline
   stage than (1). **Fixing only (1) leaves this second path fully exposed to the
   identical hang** — so bounding FRED itself (not just the outer gather) is required,
   not merely nice-to-have.
3. Separately, `desktop/daemon_runtime.py::_timer_loop` has no mechanism to notice or
   alert on a cycle that's been silently wedged for hours/days — its own comment says
   "previous cycle still in flight is expected and fine." `_run_one_cycle`/`trigger_run`
   run on a thread separate from `_timer_loop`, so the daemon's Control/Pilots APIs stay
   responsive throughout (confirmed — that's why the webapp kept working while the
   pipeline itself was dead), but nothing surfaces that fact to an operator.

Every other external data client in this codebase (FMP, GDELT) already has documented
bounded timeouts/circuit breakers; FRED via `fredapi` is the one unguarded outlier.
This PR closes that gap and adds a lightweight, read-only stall alert as a generic
safety net — it does **not** attempt a full line-by-line audit of every one of the six
pipeline stages for other similarly-unbounded calls (disclosed explicitly in the new
known-issues doc as an out-of-scope follow-up, not silently assumed complete).

Branch: already on `claude/data-pipeline-refresh-e7ccf7` (correctly-scoped feature
branch off main — no new branch needed). This touches orchestrator/daemon runtime
code, so it goes through review + PR per CLAUDE.md's "Everything else" tier, never a
direct commit to main.

## Fix A — bound the per-subtask fetch in `fetch_all_data_async` (main_orchestrator.py)

Wrap each of the three `asyncio.to_thread(...)` calls (~line 251-257) in
`asyncio.wait_for(..., timeout=settings.DATA_FETCH_TASK_TIMEOUT_SECONDS)`:

```python
timeout = settings.DATA_FETCH_TASK_TIMEOUT_SECONDS
macro_task = asyncio.wait_for(asyncio.to_thread(de.fetch_macro_raw), timeout=timeout)
fund_task = asyncio.wait_for(asyncio.to_thread(de.fetch_fundamentals_raw, tickers), timeout=timeout)
tech_task = asyncio.wait_for(
    asyncio.to_thread(de.fetch_technical_raw_cached, list(set(tickers + ["SPY"]))), timeout=timeout
)
results = await asyncio.gather(macro_task, fund_task, tech_task, return_exceptions=True)
```

The existing `isinstance(x, Exception)` dead-letter blocks below are unchanged —
`TimeoutError` (what `asyncio.wait_for` raises on expiry, Python 3.11+) is an
`OSError`→`Exception` subclass, so `gather(..., return_exceptions=True)` catches it
identically to any other sub-fetch exception, and `AsyncDataFetchStep.run()`'s
existing `except Exception as fetch_err: raise PipelineFatalError(...)` one layer up
needs **zero changes** — a timeout now gets the chance to fail fast into the exact
recovery path the module's own docstring says exists for this ("a long-lived daemon
caller survives a crashed cycle"). Distinguish a timeout from a generic exception in
the three warning logs (today `str(exc)` on a bare `TimeoutError()` is an empty,
useless string) — log the configured timeout value when `isinstance(x, TimeoutError)`.

New setting `DATA_FETCH_TASK_TIMEOUT_SECONDS: float`, default **180.0** — grounded in
`settings.FMP_MAX_SECONDS_PER_CYCLE` (120.0, fundamentals/technical's own internal
FMP-path wall-clock budget), giving ~60s headroom above that plus the yfinance
fallback path, while staying two orders of magnitude below the observed hang. Add
near `DATA_FETCH_MAX_CONCURRENCY` in `settings.py`, and to `gui/env_io.py::ALLOWED_KEYS`
next to that sibling field.

## Fix B — bound the FRED calls themselves (data_engine.py) — mandatory companion to A

`fredapi.Fred` has no timeout/session hook anywhere (verified against the full
installed source), so `socket.setdefaulttimeout()` scoped narrowly is the only lever
without vendoring the library. Add a small context manager and use it at both call
sites:

```python
# data_engine.py, module level
import socket
from contextlib import contextmanager

@contextmanager
def _bounded_fred_timeout(seconds: float):
    """fredapi.Fred.get_series() calls a bare urlopen() with no timeout parameter
    and no session-injection hook (confirmed against the installed library source)
    -- this is the only lever available short of vendoring fredapi. Process-global,
    not thread-local; scoped as narrowly as possible and always restored, even on
    exception, so it can only ever ADD a bound to a call that had none -- every other
    network call in this codebase that matters already sets its own explicit
    ``timeout=`` (FMP, GDELT) rather than depending on the socket default, so the
    narrow race window (another thread opening an unrelated socket during this
    window inherits this bound too) has no observed downside.
    """
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(seconds)
    try:
        yield
    finally:
        socket.setdefaulttimeout(previous)
```

Wrap the *whole* existing FRED call block in one `with` each, in:
- `fetch_macro_raw_detailed()` (~line 158-180) — the `t10y2y`/`oas`/`unrate`/VIX block.
- `fetch_macro_history()` (~line 294-317) — all 8 `self.fred.get_series(...)` calls
  (per-call bound, not a per-function budget — worst case is `8 × FRED_REQUEST_TIMEOUT_SECONDS`,
  same per-request semantics as `FMP_TIMEOUT_SECONDS`, not `FMP_MAX_SECONDS_PER_CYCLE`;
  say so explicitly in the field description).

No new exception handling needed — both sites' existing broad `except Exception as e:`
already catch `socket.timeout`/`TimeoutError`/`URLError` (all `OSError`→`Exception`).

New setting `FRED_REQUEST_TIMEOUT_SECONDS: float`, default **10.0** (mirrors
`FMP_TIMEOUT_SECONDS`'s per-request scope), placed directly under `FRED_API_KEY`
in `settings.py`, added to `gui/env_io.py::ALLOWED_KEYS` next to the FMP timeout block.

(Disclosed, not fixed here: `HistoricalStore.get_macro()` is also called directly from
`api/pilots_api.py`/`api/data_api.py` HTTP handlers — Fix B protects those for free
since it's at the `data_engine.py` layer; no separate change needed there.)

## Fix C — daemon-level stall alert (desktop/daemon_runtime.py) — observability only

Deliberately NOT auto-restart/auto-cancel: forcibly cancelling a mid-flight cycle
risks corrupting partial state, and this same process also hosts the Control/Pilots
APIs the webapp depends on — killing it on every future stall would trade a silent
hang for a visible outage. This is a read-only "tell the operator" safety net for
whatever stage/cause isn't covered by Fix A/B.

New method, same self-gating pattern as the existing `maybe_update_circuit_breaker()`:

```python
def maybe_alert_on_pipeline_stall(self) -> None:
    if not settings.PIPELINE_STALL_ALERT_ENABLED:
        return
    try:
        state = read_progress()
        if state is None or state.state != "running":
            return
        if state.age_seconds() < settings.PIPELINE_STALL_ALERT_SECONDS:
            return
        from observability.alerts import send_alert
        send_alert(
            "WARNING",
            f"Pipeline cycle {state.run_id!r} has been stuck in stage "
            f"'{state.stage}' ({state.symbols_done}/{state.symbols_total} symbols) "
            f"for {state.age_seconds():.0f}s with no progress update.",
            dedup_key="pipeline_stall",
        )
    except Exception:
        logger.warning("maybe_alert_on_pipeline_stall: unexpected failure", exc_info=True)
```

`ProgressState.age_seconds()` (`reporting/progress.py`) already exists. `send_alert`'s
own `dedup_key`/`settings.ALERT_DEDUP_WINDOW_SECONDS` mechanism (verified: dedup state
is only written on an actual dispatch) means a persisting stall re-fires as a periodic
reminder rather than going silent forever after the first alert.

Call `self.maybe_alert_on_pipeline_stall()` unconditionally at **both** existing
per-wake spots in `_timer_loop` (right where `maybe_update_circuit_breaker()` is
called, before and after the interval wait) — matching that method's own established
"called unconditionally, self-gates internally" contract. Also call it from
`trigger_run()`'s `ALREADY_RUNNING` branch: `settings.ORCHESTRATOR_INTERVAL_SECONDS`
actually defaults to **0** (on-demand only — confirmed in settings.py, not 3600 as one
specific deployment's `.env` has it set), and at `interval<=0` `_timer_loop` parks on
`self._wake_event.wait()` with no timeout, so it would never get a periodic chance to
check without this second call site.

New settings, near `ORCHESTRATOR_INTERVAL_SECONDS` in `settings.py`:
- `PIPELINE_STALL_ALERT_ENABLED: bool`, default **True**. This deviates from this
  repo's usual "new instrumentation defaults off" convention — deliberately: it's
  pure read-only alerting (confirmed `observability/alerts.py::_active_channels()`
  always includes `"console"` unconditionally, with webhook/email channels only
  added when actually configured, so a default-enabled check has zero external blast
  radius — worst case is a log line), it targets a proven, costly (2.5-day) incident,
  and it never mutates anything. Flagging this explicitly as a deliberate call.
- `PIPELINE_STALL_ALERT_SECONDS: int`, default **1800** (30 min) — well above
  `DATA_FETCH_TASK_TIMEOUT_SECONDS`'s worst case and any legitimate single-stage
  duration, far below the observed hang.

Register both in `pilots/feature_flags.py::DIAGNOSTIC_FLAG_REASONS` (matching the
`MARKET_DATA_LATENCY_TRACKING_ENABLED` precedent) and `gui/env_io.py::ALLOWED_KEYS`
next to `ORCHESTRATOR_INTERVAL_SECONDS`/`RUNTIME_FLAGS_REFRESH_ENABLED`.

## Tests

- `tests/test_main_orchestrator.py::TestFetchAllDataAsyncDeadLetter` — new method
  `test_macro_fetch_hang_isolated_dict_fallback_within_bounded_time`:
  `monkeypatch.setattr(settings, "DATA_FETCH_TASK_TIMEOUT_SECONDS", 0.05)`, stub
  `de.fetch_macro_raw` with a **bounded** real sleep (`time.sleep(0.3); return {}`,
  not an unset `threading.Event().wait()` — an unbounded blocked thread can hang
  pytest/interpreter shutdown). Assert wall-clock elapsed < ~1s and `macro_raw == {}`.
- New `tests/test_data_engine.py` (or extend the existing data_engine test file if one
  covers `fetch_macro_raw_detailed`/`fetch_macro_history` already) — unit test for
  `_bounded_fred_timeout`: verifies `socket.getdefaulttimeout()` is set then restored
  (including when the body raises), plus a real-timeout test against a TCP port that
  accepts-but-never-responds (`socket.socket(); s.bind(('127.0.0.1',0)); s.listen()`,
  never `accept()`) to prove the bound is real, not mocked.
- New `tests/test_daemon_runtime.py::TestMaybeAlertOnPipelineStall` (or extend the
  existing daemon runtime test file) — cases: no progress file → no alert; fresh
  running state → no alert; stale running state past threshold → `send_alert` called
  once with `dedup_key="pipeline_stall"`; a second call within the dedup window → not
  called again (use `observability.alerts`'s own dedup-reset test helper for
  isolation); `PIPELINE_STALL_ALERT_ENABLED=False` → never called regardless of
  staleness.

## Docs (per CLAUDE.md's mandatory documentation-update step)

- New `docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md`, following
  the existing template (see `watchlist_env_inline_comment_hang.md` for structure —
  Status/Symptom/Root cause/Fix/Verification). Explicitly cross-reference that doc:
  same symptom class (cycle wedged in `state: "running"` forever, manual restart the
  only recovery), different root cause. Disclose the "no full 6-stage audit performed"
  scope boundary here.
- `docs/known_issues/README.md` — one new table row, same style as the existing rows.
- `docs/architecture/orchestration-entrypoints.md` — append to the existing
  `main_orchestrator.py` bullet for Fix A/B (that bullet already documents
  `fetch_all_data_async`'s prior addendum, append-only style).
- `docs/architecture/observability-and-apis.md` — append to the existing
  `observability/alerts.py` bullet for Fix C (no existing `daemon_runtime.py`/
  `_timer_loop` bullet exists to extend instead).
- `CLAUDE.md` — one new dated bullet at the bottom of `## Recent Architecture
  Updates`, matching the existing bullets' file/date/short-paragraph/"Tests:"/
  "New env var:" structure. (`AGENTS.md` auto-syncs via the existing hook — no
  separate manual edit needed there.)

## PR artifacts (per CLAUDE.md's unique-naming rule)

`.claude/data_pipeline_stall_fix_implementation_plan.md`,
`.claude/data_pipeline_stall_fix_task.md`,
`.claude/data_pipeline_stall_fix_walkthrough.md` — copied from this plan/session,
never generic `plan.md`/`task.md`.

## Verification

1. `pytest tests/test_main_orchestrator.py tests/test_data_engine.py tests/test_daemon_runtime.py -q`
   — all new + existing tests green (existing `TestFetchAllDataAsyncDeadLetter` tests
   must still pass unchanged, proving no regression to the exception-isolation path).
2. `make verify` / the repo's standard gate (full suite + one live `run_once()`).
3. Manual smoke check: monkeypatch/temporarily lower `DATA_FETCH_TASK_TIMEOUT_SECONDS`
   and confirm a deliberately-hung `fetch_macro_raw` stub causes the cycle to
   degrade-and-continue (or fail fast into `PipelineFatalError`, letting the next
   scheduled cycle run) within the bounded time, instead of hanging — mirroring
   exactly what was observed live before the fix.
4. `git diff` review + open the PR against `main` from the current branch
   `claude/data-pipeline-refresh-e7ccf7`, with the artifacts above committed.
