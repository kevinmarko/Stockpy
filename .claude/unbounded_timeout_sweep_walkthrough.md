# Walkthrough: comprehensive unbounded-timeout sweep (follow-up to the FRED-timeout incident)

## What happened

The prior PR on this same branch fixed a real incident: the persistent
orchestrator daemon wedged in the "data" stage of a pipeline cycle for 2.5
days because `fredapi.Fred.get_series()` had no timeout anywhere in the
library. That PR's write-up
(`docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md`)
explicitly disclosed a scope boundary: it fixed the two proven
FRED-dependent hang paths plus a generic read-only stall alert, but did
**not** attempt a full audit of the rest of the codebase for the same bug
class — unbounded blocking calls with no timeout. This PR is that audit,
dispatched as 3 parallel agent passes (subprocess calls, executor/
multiprocessing waits, LLM SDK client timeouts).

## Root cause (the bug class, restated)

The same pattern recurs anywhere a blocking call is made with no explicit
timeout and no upstream bound: a stalled TCP connection or hung subprocess
blocks the calling thread/coroutine forever, with nothing to notice or
recover. The FRED incident was one instance; this sweep found the same
pattern in `alpaca-py`'s HTTP client, a generic pipeline-step dispatcher,
three `subprocess.run` call sites, and five LLM SDK client constructions.

## What was changed

- **`data/alpaca_http.py`** (new module) — `mount_timeout_adapter(session,
  timeout_seconds)` mounts a custom `requests.HTTPAdapter` on an
  `alpaca-py` `RESTClient`'s own `self._session`, since neither
  `TradingClient` nor `StockHistoricalDataClient` exposes a
  constructor-level or per-call timeout. Called from both
  `execution/alpaca_broker.py::AlpacaBroker.__init__` and
  `data/market_data.py::AlpacaProvider._build_client()`. New setting
  `ALPACA_REQUEST_TIMEOUT_SECONDS` (default 15.0).
- **`pipeline/runner.py::AsyncPipelineRunner.run()`** — the generic
  `await asyncio.to_thread(step.run, ctx)` dispatch now wrapped in
  `asyncio.wait_for(..., timeout=settings.PIPELINE_STEP_TIMEOUT_SECONDS)`
  (new setting, default 900.0). A timeout propagates as `TimeoutError`
  exactly like any other step exception, unchanged from this runner's
  existing no-blanket-try/except contract.
- **`investyo_mcp_server.py::run_platform_tests()`** — `timeout=900` +
  `except subprocess.TimeoutExpired`.
- **`scripts/preflight_check.py`**'s `.env`-not-tracked check —
  `timeout=10` + `except subprocess.TimeoutExpired: pass`.
- **`"Gravity AI Review Suite.py"`**'s check #14 SDK-reach sentinel —
  `timeout=120` + `except subprocess.TimeoutExpired: no_sdk = False`.
- **`api/data_api.py`**'s `/api/chat` (Gemini, Anthropic, OpenAI, local/
  OpenAI-compatible) and **`api/ws_api.py`**'s `/ws/chat/live` (Gemini
  Live) — all 5 LLM client-construction sites now pass an explicit
  timeout. New setting `AI_CHAT_TIMEOUT_SECONDS` (default 120.0).
- **`gui/env_io.py`** — all 3 new settings registered in `ALLOWED_KEYS`
  (non-secret; each only changes how soon a stalled call is given up on).
- **Docs**: new dated follow-up section appended to
  `docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md`, a
  `docs/known_issues/README.md` row update, append-only additions to
  `docs/architecture/execution.md`, `docs/architecture/data-layer.md`,
  `docs/architecture/orchestration-entrypoints.md`, a new bullet in
  `docs/architecture/observability-and-apis.md`, and a `CLAUDE.md` bullet
  (auto-synced to `AGENTS.md` by the existing hook).

## What was deliberately NOT done

- **`AlpacaBroker`'s methods were not converted to `asyncio.to_thread`.**
  The session-level timeout alone eliminates the "hangs forever" risk; the
  disclosed lesser residual is "blocks the event loop for up to 15s"
  instead of indefinitely. Converting to `asyncio.to_thread` is a larger,
  separate refactor.
- **`api/ws_api.py`'s `client_to_gemini()` idle-timeout gap was left
  unfixed, disclosed explicitly.** Its `await websocket.receive_text()`
  call has no idle timeout, so an idle browser client can leak one asyncio
  task pair + one OS socket to Gemini indefinitely. This is a
  session-lifecycle/UX design decision (when should an idle voice/chat
  session actually be torn down?), not a one-line bound, and was left as a
  documented follow-up rather than bolted on under time pressure.
- **No `.py` production file was modified by this documentation pass** —
  the 4 fixes above were implemented and syntax-checked in a concurrent
  session; this pass covers documentation and PR artifacts only, per the
  task's own explicit scope.

## Verification

Test authorship for all 4 fixes was dispatched to concurrent subagent
sessions, covering: `tests/test_alpaca_http.py` (new), extended
`tests/test_alpaca_broker.py`, extended `tests/test_market_data.py`, a
new/extended `tests/test_pipeline_runner.py`, extended
`tests/test_investyo_mcp_server.py` and `tests/test_preflight.py`,
and extended chat-endpoint test file(s) under `tests/`. This documentation
pass did not run tests or `git` commands itself (out of scope per the
task); running the full offline CI-mirroring suite and opening the PR
remain open items on `.claude/unbounded_timeout_sweep_task.md`.
