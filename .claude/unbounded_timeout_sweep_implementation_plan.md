# Fix: comprehensive unbounded-timeout sweep (follow-up to the FRED-timeout incident)

## Context

The prior PR (`claude/data-pipeline-refresh-e7ccf7`, [PR #916](https://github.com/kevinmarko/Stockpy/pull/916))
fixed a real incident: the persistent orchestrator daemon wedged in the
"data" stage of a pipeline cycle for 2.5 days because
`fredapi.Fred.get_series()` had no timeout anywhere in the library. That
PR's write-up, `docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md`,
explicitly disclosed a scope boundary: it fixed the two proven FRED-dependent
hang paths plus a generic read-only stall alert, but did **not** attempt a
full audit of the rest of the codebase for the same bug class — unbounded
blocking calls with no timeout. `PIPELINE_STALL_ALERT_ENABLED` exists
specifically because that broader audit was not performed.

This PR is that audit. Three parallel Explore-agent passes were dispatched:

1. Every `subprocess.run`/`subprocess.Popen` call site in the repo.
2. Every `ThreadPoolExecutor`/`ProcessPoolExecutor`/`multiprocessing` wait.
3. Every LLM SDK client construction site (the interactive AI chat surface).

Branch: already on `claude/data-pipeline-refresh-e7ccf7` (same branch as the
prior PR — this is a direct follow-up, not a new feature). Touches broker
execution, market-data, and orchestrator pipeline code, so it goes through
review + PR per CLAUDE.md's "Everything else" tier, never a direct commit to
main.

## What was found already safe (re-verified against real code, not docstrings)

- **FMP** (`data/fmp_client.py`) — `FMP_TIMEOUT_SECONDS` passed on every
  `requests.get(...)`, backed by retries and a cooldown circuit breaker.
- **GDELT** (`data/sentiment_sources.py::_gdelt_get`) — explicit read
  timeouts, a shared rate limiter, and a consecutive-failure cooldown
  circuit breaker.
- **EDGAR** (`data/edgar_fundamentals.py`) — its throttle/`_http_get`
  wrapper sets an explicit timeout on every SEC request.
- **Robinhood device-approval login worker** (`data/robinhood_login.py`/
  `data/robinhood_login_worker.py`) — already a killable, deadline-enforced
  subprocess, purpose-built because `robin_stocks`' device-approval loop has
  no timeout of its own.
- **CNN-LSTM subprocess pool** (`cnn_lstm_process_pool.py`) — already bounds
  every dispatch with `CNN_LSTM_SUBPROCESS_TIMEOUT_SECONDS`.
- **yfinance** — the installed library sets its own internal HTTP timeout by
  default (confirmed against the installed source).

## Fix 1 — `execution/alpaca_broker.py` + `data/market_data.py`'s `AlpacaProvider` (most serious finding)

`alpaca-py`'s `RESTClient` (base class of both `TradingClient` and
`StockHistoricalDataClient`) has **no timeout anywhere** — confirmed by
reading the installed library source (`alpaca/common/rest.py`):
`_one_request()` builds its `requests.Session.request(...)` call with no
`timeout` key ever passed. Worse in **kind**, not blast radius, than the
FRED bug: `AlpacaBroker`'s methods (`submit_order`, `cancel_order`,
`get_open_positions`, `get_account`, `get_orders`) call
`self._client.X(...)` directly and synchronously inside `async def`
methods — not even offloaded via `asyncio.to_thread` — so a stalled
connection freezes that cycle's own dedicated event loop directly.
Reachable from `BrokerExecutionStep`, a live daemon-cycle step, whenever
Alpaca is the active broker/data provider, including the live
order-submission path.

Fixed via a new shared module, `data/alpaca_http.py`, exporting
`mount_timeout_adapter(session, timeout_seconds)` — mounts a
`_TimeoutHTTPAdapter(requests.adapters.HTTPAdapter)` on the client's own
`self._session`, since alpaca-py's constructor exposes no timeout parameter
to pass through. Called from both `AlpacaBroker.__init__` and
`AlpacaProvider._build_client()`. New setting
`settings.ALPACA_REQUEST_TIMEOUT_SECONDS` (default 15.0). Deliberately NOT
converting `AlpacaBroker`'s methods to `asyncio.to_thread` (a larger,
separate refactor) — the session-level timeout alone eliminates the "hangs
forever" risk, leaving a disclosed lesser residual of "blocks the event
loop for up to 15s" instead of indefinitely.

## Fix 2 — `pipeline/runner.py::AsyncPipelineRunner.run()` structural per-step timeout

This exact call site (`await asyncio.to_thread(step.run, ctx)`) was named
in the original FRED incident doc as one of two independently-unbounded
paths, but the landed fix bounded FRED itself (`data_engine.py`), not this
generic dispatcher — the doc's own "Scope boundary" section says so
explicitly. Now wrapped in
`asyncio.wait_for(..., timeout=settings.PIPELINE_STEP_TIMEOUT_SECONDS)` (new
setting, default 900.0 / 15 min — well below `PIPELINE_STALL_ALERT_SECONDS`'s
1800s so this fires and lets the daemon reschedule before the stall alert
would even need to trigger). A timeout propagates as a `TimeoutError`
exactly like any other step exception already would — this runner
deliberately never wraps steps in a blanket try/except, per its own module
docstring.

## Fix 3 — three trivial one-line timeout additions

All previously-unbounded `subprocess.run` calls found by the audit, all LOW
blast radius since none block a shared service, only strand one background
job/check:

- `investyo_mcp_server.py::run_platform_tests()` — `timeout=900` (matching
  its sibling tool calls in the same file) + a new
  `except subprocess.TimeoutExpired` branch.
- `scripts/preflight_check.py`'s `.env`-not-tracked check — `timeout=10` on
  the `git ls-files --error-unmatch .env` call + a new
  `except subprocess.TimeoutExpired: pass` branch (degrades to "not
  tracked" rather than blocking preflight).
- `"Gravity AI Review Suite.py"`'s check #14 (the `import llm,
  llm.status_store, sys; ...` sentinel/SDK-reach subprocess check) —
  `timeout=120` + `except subprocess.TimeoutExpired: no_sdk = False`.

## Fix 4 — LLM chat endpoint timeouts (`api/data_api.py`'s `/api/chat`, `api/ws_api.py`'s `/ws/chat/live`)

5 client-construction sites (Gemini ×2, Anthropic, OpenAI, local/OpenAI-
compatible) had no explicit timeout — confirmed by reading the installed
SDK source directly: `google-genai`'s own default is **no timeout at all**
when unset (`_api_client.py`'s `max_allowed_time = float('inf')`), and
Anthropic/OpenAI silently inherit their SDK's 10-minute default. New
setting `settings.AI_CHAT_TIMEOUT_SECONDS` (default 120.0 — larger than
`LLM_COMMENTARY_TIMEOUT_SECONDS`/`OPAL_RESEARCH_TIMEOUT_SECONDS` since this
covers a streaming interactive chat response; httpx's `Timeout`, which all
three SDKs build on, applies its read-timeout PER CHUNK for a streaming
call, not as a hard end-to-end cutoff, so this bounds "how long we wait for
the next token," not total conversation length) now passed explicitly at
all 5 sites. Both endpoints are interactive, user-initiated, and run on a
separate FastAPI process from the orchestrator daemon — a stall there is a
resource-leak/bad-UX risk on that API process, not a silent multi-day
pipeline outage like the FRED incident this mirrors.

## Deliberately deferred, NOT fixed

`api/ws_api.py`'s `client_to_gemini()` function's
`await websocket.receive_text()` call has no idle timeout — an idle browser
client can leak one asyncio task pair + one OS socket to Gemini
indefinitely. This is a session-lifecycle/UX design decision (when should
an idle voice/chat session actually be torn down?), not a one-line bound,
and is deliberately left as a documented follow-up rather than bolted on
under time pressure.

## Tests

(Written by concurrent subagents against the production code above — not
part of this documentation pass, listed here for completeness.)

- `tests/test_alpaca_http.py` — new, for `mount_timeout_adapter`/
  `_TimeoutHTTPAdapter`.
- Extended `tests/test_alpaca_broker.py` — proves `AlpacaBroker.__init__`
  mounts the timeout adapter.
- Extended `tests/test_market_data.py` — proves `AlpacaProvider._build_client()`
  mounts the timeout adapter.
- New/extended `tests/test_pipeline_runner.py` — proves a hanging step
  degrades within `PIPELINE_STEP_TIMEOUT_SECONDS` instead of hanging
  forever, and that a `TimeoutError` propagates like any other step
  exception.
- Extended `tests/test_investyo_mcp_server.py` — `run_platform_tests()`'s
  new `TimeoutExpired` branch.
- Extended `tests/test_preflight.py` — the `.env`-not-tracked check's
  new `TimeoutExpired` branch.
- Extended chat-endpoint test file(s) under `tests/` — proves all 5 LLM
  client constructions pass `AI_CHAT_TIMEOUT_SECONDS`/`http_options`.

## Docs (per CLAUDE.md's mandatory documentation-update step)

- `docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md` — new
  dated "Follow-up: comprehensive unbounded-timeout sweep (2026-08-27)"
  section appended (same file, not a new doc — this is explicitly a
  follow-up to the same incident/investigation).
- `docs/known_issues/README.md` — updated the existing row for that doc to
  mention the follow-up sweep landed too.
- `docs/architecture/execution.md` — appended to the existing
  `execution/alpaca_broker.py` bullet.
- `docs/architecture/data-layer.md` — appended to the existing
  `data/market_data.py`/`AlpacaProvider` bullet.
- `docs/architecture/orchestration-entrypoints.md` — appended to the
  `fetch_all_data_async()`/`DATA_FETCH_TASK_TIMEOUT_SECONDS` paragraph with
  a note about `pipeline/runner.py`'s new `PIPELINE_STEP_TIMEOUT_SECONDS`.
- `docs/architecture/observability-and-apis.md` — new bullet for the AI
  chat endpoint timeouts, including the disclosed deferred WS idle-timeout
  gap.
- `CLAUDE.md` — one new dated bullet at the bottom of `## Recent
  Architecture Updates` (`AGENTS.md` auto-synced via the existing
  `.claude/hooks/sync_agent_docs.sh` hook — confirmed in sync).

## PR artifacts (per CLAUDE.md's unique-naming rule)

`.claude/unbounded_timeout_sweep_implementation_plan.md`,
`.claude/unbounded_timeout_sweep_task.md`,
`.claude/unbounded_timeout_sweep_walkthrough.md` — never generic
`plan.md`/`task.md`.

## Verification

1. `pytest tests/test_alpaca_http.py tests/test_alpaca_broker.py tests/test_market_data.py tests/test_pipeline_runner.py tests/test_investyo_mcp_server.py tests/test_preflight.py -q`
   — all new + existing tests green.
2. `make verify` / the repo's standard gate (full offline suite + one live
   `run_once()`).
3. `git diff` review + open the PR against `main` from
   `claude/data-pipeline-refresh-e7ccf7`, with the artifacts above
   committed.
