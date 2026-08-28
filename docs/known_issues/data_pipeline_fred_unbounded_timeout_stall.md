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

## Follow-up: comprehensive unbounded-timeout sweep (2026-08-27)

### Status
**Fixed** (this follow-up PR). The scope boundary above disclosed that no
full audit of the rest of the codebase had been performed for the same bug
class — unbounded blocking calls with no timeout — and that the read-only
stall alert (Fix C) existed specifically to catch whatever that audit would
have found. That audit has now been performed: three parallel agent passes
covering (1) every `subprocess.run`/`subprocess.Popen` call site, (2) every
`ThreadPoolExecutor`/`ProcessPoolExecutor`/`multiprocessing` wait, and (3)
every LLM SDK client construction site. Four real gaps were found and fixed;
one is disclosed and deliberately deferred.

### What was audited, and what was already safe
The sweep also re-verified, against real code rather than docstrings, that
several already-hardened external clients genuinely have the timeout
protection this codebase's documentation claims:
- **FMP** (`data/fmp_client.py`) — `FMP_TIMEOUT_SECONDS` is passed on every
  `requests.get(...)` call, backed by retries and a cooldown circuit breaker.
- **GDELT** (`data/sentiment_sources.py::_gdelt_get`) — explicit read
  timeouts, a shared rate limiter, and a consecutive-failure cooldown
  circuit breaker (see CLAUDE.md's "Shared GDELT rate limiter" bullet).
- **EDGAR** (`data/edgar_fundamentals.py`) — its own throttle/`_http_get`
  wrapper sets an explicit timeout on every SEC request.
- **Robinhood device-approval login worker** (`data/robinhood_login.py`/
  `data/robinhood_login_worker.py`) — already a killable, deadline-enforced
  subprocess (`RH_LOGIN_DEADLINE_SECONDS`/`RH_LOGIN_GRACE_SECONDS`/
  `RH_LOGIN_STARTUP_SECONDS`) specifically because `robin_stocks`' own
  device-approval loop has no timeout of its own — this was the correct
  prior fix for that hazard and needed no further change here.
- **CNN-LSTM subprocess pool** (`cnn_lstm_process_pool.py`) — already bounds
  every dispatch with `CNN_LSTM_SUBPROCESS_TIMEOUT_SECONDS`.
- **yfinance** — the installed library sets its own internal HTTP timeout by
  default (confirmed against the installed source, not assumed), so the
  yfinance fallback paths in `data/market_data.py` were not a gap.

### What was found and fixed

**Fix 1 — `execution/alpaca_broker.py` + `data/market_data.py`'s
`AlpacaProvider` (the most serious finding).** `alpaca-py`'s `RESTClient`
(the base class of both `TradingClient` and `StockHistoricalDataClient`) has
**no timeout anywhere** — confirmed by reading the installed library source
(`alpaca/common/rest.py`): a bare `requests.Session()` with no `timeout` key
ever passed to `_one_request()`. This is worse in **kind**, not blast
radius, than the FRED bug: `AlpacaBroker`'s methods (`submit_order`,
`cancel_order`, `get_open_positions`, `get_account`, `get_orders`) call
`self._client.X(...)` directly and synchronously inside `async def`
methods — not even offloaded via `asyncio.to_thread` — so a stalled
connection freezes that cycle's own dedicated event loop directly, rather
than merely a background thread within it (the blast radius still matches
the FRED bug: per `desktop/daemon_runtime.py`'s per-cycle-own-event-loop
architecture, only that one cycle's thread hangs — the daemon's Control/
Pilots APIs on their own separate event loops stay responsive). Reachable
from `BrokerExecutionStep`, a live daemon-cycle step, whenever Alpaca is the
active broker/data provider — including the live order-submission path.

Fixed via a new shared module, `data/alpaca_http.py`, exporting
`mount_timeout_adapter(session, timeout_seconds)` — it mounts a
`_TimeoutHTTPAdapter(requests.adapters.HTTPAdapter)` on the client's own
`self._session` attribute, since alpaca-py's constructor exposes no timeout
parameter to pass through and neither client class accepts one per-call
either. Called from both `AlpacaBroker.__init__` and
`AlpacaProvider._build_client()`. New setting
`settings.ALPACA_REQUEST_TIMEOUT_SECONDS` (default 15.0). Deliberately NOT
converting `AlpacaBroker`'s methods to `asyncio.to_thread` — a larger,
separate refactor — since the session-level timeout alone eliminates the
"hangs forever" risk; the disclosed lesser residual is "blocks the event
loop for up to 15s" instead of indefinitely.

**Fix 2 — `pipeline/runner.py::AsyncPipelineRunner.run()` structural
per-step timeout.** This exact call site
(`await asyncio.to_thread(step.run, ctx)`) was named in the original FRED
incident write-up above as one of two independently-unbounded paths, but
the landed fix bounded FRED itself (`data_engine.py`), not this generic
dispatcher — see the "Scope boundary" section above, which said so
explicitly. Now wrapped in
`asyncio.wait_for(..., timeout=settings.PIPELINE_STEP_TIMEOUT_SECONDS)`
(new setting, default 900.0 / 15 min — well below `PIPELINE_STALL_ALERT_SECONDS`'s
1800s, so this fires and lets the daemon reschedule the next cycle before
the stall alert would even need to trigger). A timeout propagates as a
`TimeoutError` exactly like any other step exception already would — this
runner deliberately never wraps steps in a blanket try/except, per its own
module docstring.

**Fix 3 — three trivial one-line timeout additions.** All previously-
unbounded `subprocess.run` calls found by the audit; all LOW blast radius
since none block a shared service, only strand one background job/check:
- `investyo_mcp_server.py::run_platform_tests()` — added `timeout=900`
  (matching its sibling tool calls in the same file) plus a new
  `except subprocess.TimeoutExpired` branch.
- `scripts/preflight_check.py`'s `.env`-not-tracked check — added
  `timeout=10` to its `git ls-files --error-unmatch .env` call plus a new
  `except subprocess.TimeoutExpired: pass` branch (degrades to "not
  tracked" rather than blocking the whole preflight run).
- `"Gravity AI Review Suite.py"`'s check #14 (the `import llm,
  llm.status_store, sys; ...` sentinel/SDK-reach subprocess check) — added
  `timeout=120` plus `except subprocess.TimeoutExpired: no_sdk = False`.

**Fix 4 — LLM chat endpoint timeouts (`api/data_api.py`'s `POST /api/chat`,
`api/ws_api.py`'s `/ws/chat/live`).** 5 client-construction sites (Gemini
×2, Anthropic, OpenAI, local/OpenAI-compatible) had no explicit timeout —
confirmed by reading the installed SDK source directly:
`google-genai`'s own default is **no timeout at all** when unset (traced to
`_api_client.py`'s `max_allowed_time = float('inf')`), and Anthropic/OpenAI
silently inherit their SDK's 10-minute default. New setting
`settings.AI_CHAT_TIMEOUT_SECONDS` (default 120.0 — larger than the
existing short, non-streaming `LLM_COMMENTARY_TIMEOUT_SECONDS`/
`OPAL_RESEARCH_TIMEOUT_SECONDS` since this covers a streaming interactive
chat response; httpx's `Timeout`, which all three SDKs build on, applies
its read-timeout PER CHUNK for a streaming call rather than as a hard
end-to-end cutoff, so this bounds "how long we wait for the next token,"
not total conversation length) is now passed explicitly at all 5 sites.
Both endpoints are interactive, user-initiated, and run on a separate
FastAPI process from the orchestrator daemon — a stall there is a
resource-leak/bad-UX risk on that API process, not a silent multi-day
pipeline outage like the original FRED bug.

### Deliberately deferred, not fixed
`api/ws_api.py`'s `client_to_gemini()` function's
`await websocket.receive_text()` call has no idle timeout — an idle browser
client can leak one asyncio task pair plus one OS socket to Gemini
indefinitely. This is a session-lifecycle/UX design decision (when should an
idle voice/chat session actually be torn down?), not a one-line bound, and
was deliberately left as a documented follow-up rather than bolted on under
time pressure.

Tests: `tests/test_alpaca_http.py`, extended `tests/test_alpaca_broker.py`,
extended `tests/test_market_data.py`, `tests/test_pipeline_runner.py`,
extended `tests/test_investyo_mcp_server.py` and
`tests/test_preflight.py`, plus extended chat-endpoint tests.

## Follow-up 2 (2026-08-27, same day): two more failure modes surfaced live, both closed

Deploying the fix above onto the live operator daemon surfaced two further
issues in quick succession, both observed on the real running system (not
just in tests):

**(a) Stale in-memory `Settings` singleton, not a code defect.** The very
first cycle after this fix's `PIPELINE_STEP_TIMEOUT_SECONDS` field was added
failed with `unexpected: 'Settings' object has no attribute
'PIPELINE_STEP_TIMEOUT_SECONDS'`. An 8-agent parallel investigation (settings.py
field-definition audit, `pipeline/runner.py` usage/import-pattern audit, a
duplicate/stale-`Settings()`-construction sweep, a settings-registry/census
consistency audit, targeted pytest verification, a live-process diagnostic,
git-history forensics, and a codebase-wide sweep for the same missing-attribute
bug class elsewhere) all independently confirmed the on-disk code was already
correct and internally consistent everywhere — field and usage land in the
same atomic commit (`f96a3908`, PR #921) on every branch/worktree checked, and
no other latent `settings.X` gap exists anywhere in the codebase (794 access
sites checked). The actual cause: a long-lived orchestrator daemon process had
already constructed its `Settings()` singleton in memory *before* this commit
was pulled — Python does not hot-reload an already-imported module, so the
in-memory object genuinely lacked the new field even after the file on disk
was updated. No code change was needed for this half; the daemon process was
independently restarted, after which the error stopped recurring.

**(b) `PIPELINE_STEP_TIMEOUT_SECONDS`'s original 900s default was too tight
under real contention — raised to 1800s.** After the restart above, the next
two live cycles (`26259a60`, `09c649aa`) both failed again, this time with a
genuine `TimeoutError` from the *new* guard firing as designed — not a hang.
Both failed at ~1380s wall-clock, well past the 900s per-step budget, while
the "Computational Core (Processing)" step was running. Historical successful
full cycles (measured from `pipeline_runs.duration_seconds`, four days
earlier) typically completed in 250-340s total under normal conditions — so
900s should have been generous. Root cause: at the exact time of both
failures, two heavy operator-launched jobs were running concurrently on the
same machine — a `scripts.refresh_validations` CPCV backtest (21 years, 6
options strategies, CPU-heavy) and `scripts/backfill_sentiment_history.py
--sources gdelt,edgar,finnhub,reddit` (shares the daemon's own rate-limited
FMP/GDELT/EDGAR budgets) — starving the daemon's own cycle of both CPU and
API throughput. This is the same class of shared-resource contention already
documented for concurrent `refresh_validations.py` runs across worktrees;
here it happened within a single machine between the daemon and manually
launched jobs. `PIPELINE_STEP_TIMEOUT_SECONDS` was raised 900.0 → 1800.0, and
`PIPELINE_STALL_ALERT_SECONDS` raised 1800 → 3600 in lockstep to preserve the
original 2x safety-margin ratio between the two (see both fields' own
descriptions in `settings.py`). This does not relax hang protection — a step
that is genuinely wedged, not merely contended, is still killed and the
daemon still recovers; it only accepts a wider window of legitimate slowness
under concurrent load before doing so. Test:
`tests/test_pipeline_runner.py::TestAsyncPipelineRunnerStepTimeout::test_default_timeout_setting_is_generous`
updated to pin the new 1800.0 default.
