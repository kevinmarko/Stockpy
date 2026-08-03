# AGENTS.md — Master Prompt for AI Assistants

This file is the model-agnostic onboarding prompt for **any** AI coding assistant
(Claude, GPT, Gemini, Cursor, Aider, Codex, etc.) working in this repository. If
your tool reads a tool-specific file instead (`CLAUDE.md`), read
this one first — it is the shortest path to not breaking something.

---

## 1. What this project is

**InvestYo Quant Platform** ("Stock Dashboard Py") — a solo-operator, local-first
quantitative trading research pipeline. It fetches market/macro data, computes
technical & fundamental indicators, runs multi-horizon forecasts, backtests
strategies with realistic costs, persists signals to SQLite, and publishes
results to Google Sheets and an HTML report. A Streamlit "Command Center" GUI
sits on top for day-to-day operation.

**Owner/operator:** Kevin Marko Lee, an individual investor running this against
his own capital. There is no team, no other users, no SLA — optimize for
correctness and honesty over polish or speed.

**Frontend strategy: web app only, desktop app decommissioned.** The Pilots PWA
(`webapp/`) is the one actively-developed frontend — all new UI features and
fixes go there. The Streamlit "InvestYo Command Center" (`gui/`) and its native
desktop wrapper (`app_shell.py`, `desktop/net_util.py`, `desktop/ui_server.py`,
`desktop/engine_supervisor.py`, `launch_app.command`, `launch_gui.command`) are
frozen/legacy — still runnable, but get no new tabs, panels, or capability. This
does NOT include the backend pipeline (`main.py`, `main_orchestrator.py`,
`api/*.py`, or the orchestrator daemon in `desktop/daemon_runtime.py`/
`desktop/orchestrator_daemon.py`) — those keep being developed normally; the
web app's backend depends on them. See `CLAUDE.md`'s "Frontend strategy"
section for the full detail.

## 2. Safety posture — read this before touching execution code

- **`ADVISORY_ONLY=true` is the default and the normal operating mode.** In this
  mode the pipeline produces recommendations and rationale only; **no order is
  ever submitted to any broker.** Treat this quarantine as load-bearing safety
  infrastructure, not a feature flag to casually flip.
- There is a real, working Alpaca broker integration (`execution/`) and a
  Robinhood MCP-based execution bridge (`execution/queue_builder.py` +
  `skills/robinhood-execution`), both gated behind multiple independent
  controls: `ADVISORY_ONLY`, a file-based global kill switch
  (`execution/kill_switch.py`), a ten-check pre-trade risk gate
  (`execution/risk_gate.py`), and `ROBINHOOD_EXECUTION_MODE=off|review|live`.
  **Do not bypass, weaken, or simplify any of these gates** unless explicitly
  instructed by the user to configure live execution or run real trades.
- If the user explicitly asks you to set up live trading (`ADVISORY_ONLY=false`),
  you are permitted to configure it and assist with live execution.
- **Never fabricate data.** If a value can't be computed (missing upstream
  data, insufficient history, a failed fetch), the correct output is `NaN` /
  `None` / an explicit "unavailable" state — never a plausible-looking zero or
  guessed number. This shows up throughout the codebase as comments like
  `never fabricated` — take them literally.
- **Dead-letter, don't crash.** A single bad ticker, a single failed API call,
  or one broken signal module must never abort a whole pipeline run. Per-item
  failures are caught, logged, and recorded (often literally in a "dead
  letter" list); the run continues for everything else.
- **Never log or persist secrets** (API keys, passwords, TOTP secrets, DB DSNs
  with embedded credentials). Several modules explicitly avoid logging full
  connection strings for this reason — follow that pattern.
- **No lookahead bias.** Every indicator, forecaster, and backtest must be
  causal — computed only from data available at that point in time. This
  codebase has repeatedly caught real bugs here; when adding anything
  time-series-shaped, ask "could this see the future?" and write a
  perturbation test if the answer isn't obviously no.
- **New admin/API write or execution capabilities default to ON, not gated
  behind a fresh opt-in flag.** (Operator decision, 2026-08-03.) The 11
  existing fail-closed gates in `gui/env_io.py::EXCLUDED_FROM_GUI`
  (`AI_GENERATION_API_ENABLED`, `AUTOMATION_WRITES_ENABLED`,
  `BROKERAGE_REFRESH_ENABLED`, `COMMAND_EXECUTION_ENABLED`,
  `DEAD_LETTER_RETRY_ENABLED`, `GENERAL_SETTINGS_WRITES_ENABLED`,
  `LLM_WRITES_ENABLED`, `MACRO_GATE_WRITES_ENABLED`,
  `PROMPT_REGISTRY_WRITES_ENABLED`, `RAG_QUERY_API_ENABLED`,
  `STRATEGY_WRITES_ENABLED`) predate this decision and are unaffected — do
  not flip their defaults without being explicitly asked to. Going forward,
  when you build a *new* admin/API capability (a new write endpoint, a new
  command dispatch, a new automation trigger), ship it active by default
  instead of behind a freshly-added `_ENABLED` flag defaulting `False`;
  requiring a new opt-in flag for every new admin feature was judged too
  much overhead. This is scoped narrowly to admin/write/execution surface
  area and does **not** extend to anything that changes what the pipeline
  computes or trades on — new signal modules, sizing changes, new data
  sources, forecasting changes all keep the existing default-`False`/opt-in
  convention, since a silent behavior change on `git pull` is a real risk
  for a live capital account.

## 3. Non-negotiable workflow rules

- **Never commit directly to `main`.** Always work on a feature branch and open
  a PR. See `CLAUDE.md`'s "Branch Workflow" section for the exact start-of-
  session checklist (`git fetch && git rebase origin/main`, then a new
  `lowercase-kebab` branch).
- **Never commit `.env`** or any file containing real credentials.
  `credentials.json` (Google service account) and `.env` are gitignored for a
  reason.
- **`quant_platform.db` and other runtime SQLite files are gitignored and must
  stay that way.** They're per-machine state that accumulates local trades/
  signals; a past incident corrupted a similar tracked-and-mutating file
  (`ml/registry.yaml`) via a concurrent-PR merge — don't repeat it.
- Add tests for new/changed indicators (numeric drift on existing ones must
  stay below `1e-5`) and for any new lookahead-sensitive logic.
- Iterating over tickers or time series must be vectorized (pandas/numpy), not
  Python `for`/`.iterrows()` loops — this is enforced by convention throughout
  `processing_engine.py`, `strategy_engine.py`, etc.
- Every options-selling strategy validation must pass an additional tail-
  scenario stress gate (`validation/stress_scenarios.py`) on top of the
  standard PBO/DSR/Sharpe/MaxDD gates. Don't loosen deployability thresholds to
  force a green check — a strategy that fails a gate should report
  `deployable=False`, honestly.

## 4. Architecture at a glance

Flat, modular "Engine" architecture with dependency injection — no package
directories for the core pipeline; every engine is a top-level module.

```
Data (Yahoo/FRED/Alpaca/Robinhood)
  → DTOs (dto_models.py — the only allowed shape for market/fundamental/macro data)
  → Engines (processing_engine, forecasting_engine, macro_engine, technical_options_engine)
  → Signals (signals/ package — pluggable SignalModule implementations, weighted-sum aggregator)
  → StrategyEngine (scoring, sizing via sizing/ — fractional Kelly / vol-target)
  → Advisory (engine/advisory.py — holding-aware recommendation engine, the primary output)
  → [Execution — quarantined behind ADVISORY_ONLY + kill switch + risk gate]
  → Reporting (Google Sheets, HTML report, SQLite, api/*.py → webapp/ Pilots PWA
     [primary frontend] — the Streamlit Command Center is legacy, see §1)
```

Two orchestrator entry points exist side by side:
- **`main.py`** — the clean advisory orchestrator (recommended). Two-tier
  refresh cadence: Robinhood account fetched at most once/day, market data
  every cycle.
- **`main_orchestrator.py`** — the fuller async pipeline (broker execution,
  Pandera schema validation, all 50+ dashboard columns).

`config.py` is the single source of truth for every data column
(`COLUMN_SCHEMA`) — add a new field there before using it anywhere else.

Full architectural detail, every module's contract, and every environment
variable live in **`CLAUDE.md`** (long — read it selectively via search, not
top to bottom) and **`docs/architecture.md`** (Mermaid diagram). Don't
duplicate that detail here; this file is the map, not the territory.

## 5. Where to look for what

| Need | Look here |
|---|---|
| Full module-by-module architecture reference | `CLAUDE.md` |
| AI-facing MCP server surface (tools/resources/prompts) | `investyo_mcp_server.py` + CLAUDE.md "InvestYo MCP Server" section |
| Data-flow diagram | `docs/architecture.md` |
| Every signal module's logic + academic reference | `docs/signals/README.md`, `docs/signals/<name>.md` |
| Dated changelog of every feature tier ever shipped | `docs/FEATURE_TIER_HISTORY.md` |
| End-user how-to for every GUI feature | `docs/HOW_TO_GUIDE.md` |
| Operational runbook / incident playbooks | `docs/RUNBOOK.md` |
| Pre-live readiness checklist | `docs/GO_LIVE_CHECKLIST.md`, `scripts/preflight_check.py` |
| Test-coverage gaps and roadmap | `docs/test_coverage_analysis.md` |
| Quick-start / required `.env` keys | `README.md` |
| Web app (primary frontend) — run, structure, mock↔live flag | `webapp/README.md` |

## 6. Common commands

```bash
# ── Web app (primary interface) ────────────────────────────────────────────────
./launch_webapp.command              # Starts Pilots PWA + backend APIs automatically (Mock or Live)
cd webapp && npm install
npm run dev                          # http://localhost:5173 — offline mock data by default
VITE_USE_MOCK=false npm run dev      # against the live backend (see below)
uvicorn api.pilots_api:app --port 8602    # backend the PWA talks to (+ data_api:8603 / metrics_api:8604 as needed)

# ── Legacy desktop app — decommissioned, no new development ───────────────────
# gui/ (Streamlit "InvestYo Command Center") and app_shell.py/desktop/'s native-shell
# modules are frozen; see "Frontend strategy" above. Kept runnable for existing setups.
#   chmod +x launch.command launch_app.command   # one-time setup, already done
# REFRESH_INTERVAL_SECONDS at the top of launch.command controls single-run (=0)
# vs interval-loop (>0, default 60 s) mode.
# The script verifies .venv exists and that Python is exactly 3.12.x before
# launching, then pauses ("Press any key") on exit so errors are always visible.

./launch_app.command                 # legacy: opens the unified Command Center in a native
                                      # desktop window (pywebview) with an always-on
                                      # background refresh loop tied to the window's lifecycle
python3 app_shell.py [--interval N]  # what launch_app.command runs under the hood
./scripts/build_macos_app.command    # ONE-TIME (legacy): builds InvestYo.app (real macOS app
                                      # bundle, custom icon, no Terminal flash) that runs
                                      # launch_app.command — drag the result into /Applications
                                      # or the Dock; not committed to git (bakes in an absolute
                                      # path — see .gitignore)
streamlit run gui/app.py             # legacy: InvestYo Command Center — full 18-tab operational GUI
./launch_gui.command                 # legacy: same as above, macOS double-click launcher

# ── Backend pipeline (unaffected by the frontend decommission) ────────────────
./setup.sh                       # creates .venv (Python 3.12, via uv — https://astral.sh/uv), installs requirements.txt
# Everything below works either after `source .venv/bin/activate`, or prefixed
# with `uv run` (e.g. `uv run pytest`) to skip activation entirely — uv
# auto-discovers .venv in the repo root, no pyproject.toml required.
python3 main.py                  # clean advisory orchestrator — runs one full cycle (or loops with --interval N); use --refresh-account to force Robinhood re-auth
python3 main.py --interval 60   # refresh market data every 60 s; Robinhood account fetched at most once/day
python3 main.py --refresh-account  # force fresh Robinhood login on this launch, then resume normal daily-cache behavior
python3 main_orchestrator.py     # newer async master orchestrator (data acquisition, schema validation, HTML report compilation)
pytest                           # run test suite
pytest tests/test_quantitative_models.py            # run a single test file
pytest tests/test_quantitative_models.py::test_graham_number_imaginary_bounds  # run a single test
python3 database_setup.py        # (re)build the SQLite schema in quant_platform.db from config.COLUMN_SCHEMA
python3 -m validation.harness --strategy <name> --start YYYY-MM-DD --end YYYY-MM-DD # run strategy validation harness
python scripts/preflight_check.py            # pre-live readiness gate (exit 0 = all pass)
python scripts/preflight_check.py --json     # machine-readable JSON output
python -m execution.kill_switch --status     # check / activate / deactivate the global kill switch
make verify                                  # env-var check + pytest + one live run_once() + print summary
./verify.command                             # same as make verify, macOS double-click
```

`main.py`/`main_orchestrator.py` auto-re-exec themselves under `.venv`'s
interpreter — no need to manually activate the venv first.

## 7. Footguns — things that look like bugs but are deliberate

- `dividendYield` is emitted as a **fraction** (`0.0257`), `debtToEquity` as a
  **percent** (`150.0`, i.e. ×100). These are opposite conventions on purpose —
  downstream consumers depend on the exact scale. Don't "fix" one to match the
  other.
- Monte Carlo forecasting needs **daily** sigma; GARCH returns **annualized**
  vol. The `/sqrt(252)` conversion is mandatory and easy to accidentally
  duplicate or drop.
- `RSI2MeanReversionSignal`'s score range is `[0, 1]` (long-only), while every
  other signal module is `[-1, 1]`. Intentional — don't normalize it away.
- `regime_multiplier`'s signal module always returns `score=0.0` — it carries
  information through its `confidence` field instead, as a position-sizing
  multiplier, not a score input. This is structurally enforced
  (`SIGNAL_WEIGHTS["regime_multiplier"] == 0.0`), not accidental.
- A `SignalAggregator.aggregate()` call returns a **6-tuple**; code that
  unpacks 5 elements is stale, not a template to copy.
- The repo has *two* Gravity-suite-adjacent files historically: the actually-
  executed `Gravity AI Review Suite.py` (note the space in the filename — it is
  deliberately **not** meant to be imported as a Python module) and a now-
  deleted `gravity/__init__.py` package that had silently forked into dead
  code. If you ever see a reference to `gravity/__init__.py` in a stale doc or
  comment, it's leftover from that and should be corrected, not resurrected.

## 8. If you're not sure

This codebase has strong, explicit conventions (see `CLAUDE.md`) built up over
many iterations, several of them in direct response to real bugs. If a change
you're about to make contradicts something stated as a convention or
invariant, that's a signal to stop and ask the user rather than "fixing" the
convention — it is very likely intentional and load-bearing.


## Recent Architecture Updates
- **Signal Engine Vectorization**: As of Phase 4, the entire `SignalAggregator` and all `SignalModule` implementations are natively vectorized in pandas/numpy (O(1) block computation). Row-based ticker iteration in the aggregation step has been removed to maximize performance.
- **Market Holiday Defense**: `HistoricalStore` uses `USFederalHolidayCalendar` to detect and skip redundant bar fetches on weekends and market holidays.
- **Benchmark Fallbacks**: `pilot_performance` in `pilots/performance.py` automatically falls back to SPY (`macro_benchmark_curve`) if the strategy-specific benchmark is missing.
- **Live Execution Preflight Check**: `main_orchestrator.py` validates that Alpaca API keys are present when running live (`dry_run=False` and `ADVISORY_ONLY=False`), raising a fatal error to exit gracefully if keys are missing.
- **Caddy Auto-HTTPS Support**: `deploy/setup_gcp_vm.sh` supports dynamic configuration of Caddy with Let's Encrypt for a custom domain via `$DOMAIN_NAME`.
