# Task Tracker: comprehensive unbounded-timeout sweep (follow-up to FRED-timeout incident)

- [x] Dispatch 3 parallel Explore-agent audits: (1) every `subprocess.run`/
      `subprocess.Popen` call site, (2) every `ThreadPoolExecutor`/
      `ProcessPoolExecutor`/`multiprocessing` wait, (3) every LLM SDK client
      construction site.
- [x] Re-verify (against real installed-library source, not docstrings)
      that FMP, GDELT, EDGAR, the Robinhood device-approval login worker,
      the CNN-LSTM subprocess pool, and yfinance's own library-level
      timeout default were all already genuinely safe.
- [x] Fix 1: new shared module `data/alpaca_http.py::mount_timeout_adapter()`,
      wired into `execution/alpaca_broker.py::AlpacaBroker.__init__` and
      `data/market_data.py::AlpacaProvider._build_client()`. New setting
      `ALPACA_REQUEST_TIMEOUT_SECONDS` (default 15.0).
- [x] Fix 2: `pipeline/runner.py::AsyncPipelineRunner.run()`'s
      `await asyncio.to_thread(step.run, ctx)` wrapped in
      `asyncio.wait_for(..., timeout=settings.PIPELINE_STEP_TIMEOUT_SECONDS)`.
      New setting `PIPELINE_STEP_TIMEOUT_SECONDS` (default 900.0).
- [x] Fix 3a: `investyo_mcp_server.py::run_platform_tests()` —
      `timeout=900` + `except subprocess.TimeoutExpired`.
- [x] Fix 3b: `scripts/preflight_check.py`'s `.env`-not-tracked check —
      `timeout=10` + `except subprocess.TimeoutExpired: pass`.
- [x] Fix 3c: `"Gravity AI Review Suite.py"`'s check #14 SDK-reach sentinel —
      `timeout=120` + `except subprocess.TimeoutExpired: no_sdk = False`.
- [x] Fix 4: `api/data_api.py`'s `/api/chat` (Gemini, Anthropic, OpenAI,
      local/OpenAI-compatible — 4 sites) and `api/ws_api.py`'s
      `/ws/chat/live` (Gemini Live — 1 site) all now pass an explicit
      timeout. New setting `AI_CHAT_TIMEOUT_SECONDS` (default 120.0).
- [x] Disclose (not fix) `api/ws_api.py`'s `client_to_gemini()`
      `await websocket.receive_text()` idle-timeout gap as a deliberate
      follow-up.
- [x] Register the 3 new settings in `gui/env_io.py::ALLOWED_KEYS`
      (non-secret; each only changes how soon a stalled call gives up).
- [x] Production code for all 4 fixes written and syntax-checked (owned by
      a concurrent session; this tracker covers the documentation +
      PR-artifacts half of the task).
- [x] Tests written and passing (owned by 3 concurrent subagent sessions):
      `tests/test_alpaca_http.py`, extended `tests/test_alpaca_broker.py`,
      extended `tests/test_market_data.py`, `tests/test_pipeline_runner.py`,
      extended `tests/test_investyo_mcp_server.py` and
      `tests/test_preflight.py`, extended `tests/test_data_api_chat.py` and
      `tests/test_gemini_live_chat.py`. **Two independent test-writing
      agents found and correctly reported (rather than papered over) a
      genuine bug in `data/alpaca_http.py::_TimeoutHTTPAdapter`**:
      `kwargs.setdefault("timeout", self._timeout)` never fired because
      `requests.Session.request()` always threads an explicit
      `timeout=None` down to the adapter (a present key, not a missing
      one), so `setdefault` was a silent no-op — the mounted default never
      actually applied to any real alpaca-py call. Fixed to
      `if kwargs.get("timeout") is None: kwargs["timeout"] = self._timeout`,
      and updated the two tests that had documented the bug (correctly, as
      failing/pinning tests) to instead confirm the fix. All 780 tests
      across every touched/new file green.
- [x] Docs: new dated section appended to
      `docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md`
      (same file — a follow-up to the same incident, not a new doc),
      `docs/known_issues/README.md` row updated,
      `docs/architecture/execution.md` appended,
      `docs/architecture/data-layer.md` appended,
      `docs/architecture/orchestration-entrypoints.md` appended,
      `docs/architecture/observability-and-apis.md` new bullet added,
      `CLAUDE.md` bullet appended (`AGENTS.md` auto-synced via the existing
      `.claude/hooks/sync_agent_docs.sh` hook — verified in sync via
      `diff <(tail -1 CLAUDE.md) <(tail -1 AGENTS.md)`).
- [x] PR artifacts committed under the task-scoped
      `.claude/unbounded_timeout_sweep_*` names (this implementation plan,
      this task tracker, and the walkthrough).
- [x] Ran the full offline CI suite (`pytest -m "not network and not slow"`,
      matching `.github/workflows/ci.yml`'s `test` job) — **12414 passed,
      13 skipped, 0 failed** — after regenerating the 2 committed
      settings-census artifacts the 3 new settings fields made stale
      (`scripts/settings_liveness.py --write`,
      `scripts/measure_settings_census.py --write`). `ruff check . --select=F821,F822,F823,E9` clean.
- [x] Opened PR against `main` from `fix-unbounded-timeout-sweep` (a fresh
      branch off `main`, distinct from the already-merged
      `claude/data-pipeline-refresh-e7ccf7` FRED-fix branch) with these
      `.claude/unbounded_timeout_sweep_*` artifacts committed --
      [PR #921](https://github.com/kevinmarko/Stockpy/pull/921).
