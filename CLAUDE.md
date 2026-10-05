# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

It describes what the system is today and the rules you must follow. It is not a changelog: dated
feature narratives, audit write-ups and PR-by-PR bug histories live in `docs/FEATURE_TIER_HISTORY.md`,
`docs/known_issues/*.md`, `docs/architecture/*.md` and the `.claude/*_walkthrough.md` files. When you
finish a change, update the doc that owns its detail and touch this file only if a rule, command or
top-level fact changed.

## Branch Workflow

Antigravity IDE works on this repo alongside Claude Code (since 2026-07-20). There is **no fixed
multi-agent domain split or per-agent branch-prefix convention** — the operator assigns work to each
agent per task. Do not defer to, flag PRs for, or avoid editing any file on the assumption another
agent owns it. Branch naming is lowercase-kebab (e.g. `fix-hmm-lookahead`) for every agent.

### Start-of-session checklist
1. `git fetch origin && git rebase origin/main` — sync from main before starting.
2. **Low-risk changes** — docs, `.claude/` config (settings, hooks, skills, agents), comments,
   test-only additions, and other non-behavioral edits — may be committed directly to `main`
   after a self-review pass (re-read the full diff, confirm it does what it's supposed to and
   touches nothing else). No branch or PR required for these.
3. **Everything else** — anything touching engines, signals, execution, sizing, validation,
   orchestrators, or other runtime/trading logic — always goes through
   `git checkout -b <short-description>` (lowercase-kebab) and a PR. Never commit these directly
   to `main`.
4. Open a PR when a feature-branch change is complete; do not squash or amend published commits —
   never rewrite `main`'s history or another PR's already-reviewed commits without being asked.
   This does **not** forbid maintaining your own open PR: rebasing its branch onto `origin/main`
   (and force-pushing that same branch) to resolve staleness/conflicts is expected. Prefer an
   additive fix commit when the base hasn't moved; rebase when GitHub reports the PR as
   behind/conflicting and a clean rebase is possible.
5. **Rebase and merge when ready** — once a PR's targeted verification has actually passed this
   session (run and shown green, not assumed) and you've self-reviewed the full diff, merge it
   yourself (`gh pr merge`, matching the repo's established merge style) without waiting for a
   separate go-ahead — unless the operator said otherwise for that PR, or the change is ambiguous/
   high-stakes enough that explicit-permission categories apply (e.g. live execution/broker
   behavior the operator hasn't signed off on). When a rebase surfaces a real conflict, resolve it
   faithfully to both sides' intent, re-run verification, and say what the conflict was and how
   you resolved it — don't silently pick a side.
6. **PR artifacts & unique naming** — when submitting a PR, copy and commit the implementation
   plan, task tracker and walkthrough to `.claude/` on your branch, with unique, task-scoped names
   (e.g. `.claude/fmp_pipeline_optimization_implementation_plan.md`, `..._task.md`,
   `..._walkthrough.md`). Test files for a feature likewise carry clear, project-scoped names.
   **This applies to every AI agent (Claude Code, Antigravity, any other) at all times, not only at
   PR time:** bare, unscoped filenames — `plan.md`, `implementation_plan.md`, `walkthrough.md`,
   `task.md`, `tracker.md`, or any other generic name — must never be created anywhere in the repo
   (`.claude/`, `.agents/`, docs, scratch dirs), even transient ones you intend to delete: a
   concurrent agent can read, edit or collide with it first. Always prefix with the
   task/feature/branch slug.
7. **After merging any PR**, sync the local main checkout immediately so the next session starts
   from the merged state:
   `git -C <main-checkout-path> fetch origin && git -C <main-checkout-path> merge --ff-only origin/main`.

Never use `git stash` in this repo: `refs/stash` is shared across all worktrees, and a concurrent
session's push/pop can silently swap uncommitted changes between worktrees. Use a temporary WIP
commit instead.

## Agent Workflow: Verification & Planning

Applies to whichever agent does the work — Claude Code or Antigravity — with no per-agent carve-out.

- **Verification is mandatory, not advisory.** A task is not done until the relevant check has been
  run and shown to pass — not "should pass." Python changes: the corresponding
  `tests/test_<module>.py` (or the fuller `make ci` gate) must show zero failures. `webapp/src/`
  changes: `npm run --prefix webapp typecheck` clean AND, for anything UI-visible, an actual
  `npm run dev` + browser check (console errors + visual confirmation) — a clean typecheck proves
  the code compiles, not that a screen renders or behaves as intended.
- **Claude Code enforces the Python half automatically.** `.claude/hooks/verify_targeted_tests.sh`
  (`PostToolUse`, non-blocking) runs the mapped `tests/test_<module>.py` after every edit to a
  tracked `.py` file. `.claude/hooks/verify_before_stop.sh` (`Stop`) is the enforcement gate: it
  blocks ending a turn while a targeted test tied to uncommitted changes is failing, capped at 2
  consecutive blocks per session so a stuck check can't deadlock. `/verify` and `/verify-webapp`
  (`.claude/commands/`) run the fuller gate and the browser check on demand.
  `.claude/hooks/block_env_write.sh` (`PreToolUse` on `Edit|Write`) hard-denies any write to a file
  named exactly `.env`. `.claude/hooks/webapp_typecheck.sh` (`PostToolUse` on `Edit|Write`) runs
  `npm run --prefix webapp -s typecheck` after any edit under `webapp/src/**` — the mock/live API
  parity gate.
- **Antigravity has no equivalent automatic blocking gate.** Its `Stop` hook has no "force
  continuation" semantics and its `PreToolUse`/`PostToolUse` shell hooks do not intercept the IDE's
  file-editing tools, so "don't mark a task done until tests pass" is **policy-only** there — a
  real, open gap, not a claimed equivalence. `.agents/hooks/stop_test.sh` (`Stop`) is the
  advisory-only port of the Python check (re-runs the mapped tests for uncommitted `.py` changes and
  reports failures to stderr, but cannot block). `.agents/hooks/block_env_write.sh` and
  `.agents/hooks/webapp_typecheck.sh` port the other two hooks to Antigravity's I/O contract
  (`.agents/hooks.json`, matching
  `default_api:write_to_file|default_api:replace_file_content|default_api:multi_replace_file_content`).
- **Plan before building in the "Everything else" tier** (engines, signals, execution, sizing,
  validation, orchestrators): produce an Implementation Plan and get it reviewed before writing code.
  Claude Code: `EnterPlanMode`. Antigravity: its native implementation-plan artifact (saved under a
  task-scoped name, per rule 6 above).
- **Every Implementation Plan includes an explicit documentation-update step** — name which of
  `CLAUDE.md`/`AGENTS.md`, `docs/architecture/*.md`, `docs/signals/<name>.md` or other `docs/` files
  the change touches and scope those edits into the plan. Documentation is part of the deliverable.
- Steer a plan or diff you're unhappy with via inline comments on the artifact itself, not a fresh
  open-ended re-prompt.
- **Decompose isolated, mechanical subtasks to a subagent** (writing/extending a test file, a
  migration, a schema stub). `test-writer` (`.claude/agents/test-writer.md` /
  `.agents/agents/test-writer.md`) is the ready-made subagent for "write tests for `<module>`." For
  hard multi-file refactoring, don't default a subagent to a cheap model tier: Claude Code — leave
  `model:` unset so it inherits the session's model; Antigravity — use `model: pro`.
- **`CLAUDE.md` and `AGENTS.md` are exact mirrors** (`AGENTS.md`'s first line is `# CLAUDE.md`).
  `.claude/hooks/sync_agent_docs.sh` / `.agents/hooks/sync_agent_docs.sh` copy whichever was just
  edited onto the other; `tests/test_agent_docs_sync.py` fails CI if they differ.

## Project

InvestYo Quant Platform ("Stock Dashboard Py") — an automated quantitative analysis pipeline: fetches
market/macro data, computes technical & fundamental indicators, scores symbols through pluggable signal
modules, runs multi-horizon forecasts, sizes positions, backtests and validates strategies, persists
results to SQLite, and publishes them to the Pilots PWA and an HTML report. Agentic Robinhood trading
reads a gated execution queue the pipeline writes.

### Runtime today (2026-09)

- **The orchestrator daemon is the one runtime.** `python -m desktop.orchestrator_daemon`
  (`desktop/orchestrator_daemon.py` + `desktop/daemon_runtime.py::OrchestratorDaemon`) keeps the
  pipeline engines warm, runs `main_orchestrator.py`'s async pipeline on its own timer
  (`ORCHESTRATOR_INTERVAL_SECONDS`, default `0` = on-demand) or on request, and hosts the Control API
  (`api/control_api.py`, port `ORCHESTRATOR_API_PORT`) plus, when `PILOTS_API_ENABLED`, the Pilots
  API. Trigger a cycle with `POST /run` or `shared.daemon_client.trigger_run()` (poll
  `get_run_status`; a 409 means one is already running — attach to it).
- **`main.py` is being retired** (shrink step 5, plan `.claude/shrink_step5_retire_main_py_implementation_plan.md`).
  It still runs the advisory cycle and, until cutover, is the real execution-queue writer (the
  `com.investyo.daily-advisory` launchd job, weekdays 08:45). Steps 5.4–5.6 (switch callers,
  archive `main.py` to `legacy/`, retire `ORCHESTRATOR_DAEMON_ENABLED`) are pending. Don't build new
  features on `main.py`.
- **Daemon pipeline steps** (`pipeline/production_steps.py`, assembled in `main_orchestrator.py`):
  `AsyncDataFetchStep` → `RunPipelineStep` (inner `MacroStep` → `TrendVolatilityStep` →
  `ProcessingStep` → `ForecastingStep` → `StrategyEvalStep`) → `AdvisoryOverlayStep` (runs
  `engine/advisory.py::evaluate()` per symbol and keeps the `Recommendation`s) → `AgenticQueueStep`
  → `BrokerExecutionStep` (paper orders on the FMP paper ledger only, never when going
  live; trades only in regular US hours, skips reconciliation, acts only on `main_pipeline` positions, sizes
  zero-Kelly buys with `PAPER_PIPELINE_PROBE_WEIGHT` only at cold start (holiday-aware hours, stale
  quotes rejected), and closes on `RISK REDUCE`) →
  `StateSnapshotStep`.
- **`settings.DAEMON_AGENTIC_QUEUE_MODE`** (`off` | `shadow` | `primary`, default `off`, a
  `DANGEROUS_KEYS` member) controls `AgenticQueueStep`. `shadow` writes the advisory source and
  `execution_queue.json` under `OUTPUT_DIR/shadow/` with no side effects; `primary` makes the daemon
  the real queue writer (summary push, watch engine, real queue files, with a run-ownership guard so
  a timed-out cycle cannot write late) and `main.py` then skips those side effects. **The live
  deployment runs `shadow` until the operator cuts over after a 5-trading-day review ending
  2026-10-05** (`scripts/compare_shadow_queue.py` diffs the real queue against the shadow one;
  cutover steps: `docs/RUNBOOK.md` §3.15). Walkthroughs: `.claude/shrink_step5_*_walkthrough.md`.
- **Symbol universe** — one builder, `pipeline/advisory_inputs.py::build_universe_detailed()`, used
  by both `main.py` and the daemon: held positions ∪ `WATCHLIST` env var ∪ `watchlist.txt` ∪
  discovered scan candidates, minus rating auto-drops; `settings.DEFAULT_TICKERS` only when that
  union is empty (via `data/portfolio_sync.py::compute_tracked_universe()`); recently-closed
  positions (`CLOSED_POSITION_RETENTION_DAYS`) unioned last. Anything needing "the full tracked
  universe" must go through these functions, never an ad-hoc union. Symbols merely browsed in the
  Symbol Screener or Quick Trade never enter it — see `docs/architecture/execution-boundary.md`.

### Key documentation files
| File | Purpose |
|------|---------|
| `docs/README.md` | Master index of the full `docs/` library — start here if you don't know a doc's path |
| `docs/architecture.md` | Mermaid data-flow diagram (Engines → DTOs → Signals → Strategy → Advisory → Broker) |
| `docs/architecture/*.md` | Per-subsystem architecture reference — see the index under `## Architecture` |
| `docs/signals/README.md` + `docs/signals/<name>.md` | The registered `SignalModule`s: references, logic, failure modes, Backtest Validation |
| `docs/known_issues/README.md` | Index of dated production-issue write-ups (root cause, fix, status) |
| `docs/incident_log.md` | Template + log for production incidents (referenced by RUNBOOK §6) |
| `docs/HOW_TO_GUIDE.md` | End-user guide for platform features |
| `docs/regime_model_tuning_guide.md` | Gaussian HMM regime model tuning and CLI audit tooling |
| `docs/RUNBOOK.md` | Operational runbook — pre-market checklist, incident playbooks, shutdown ladder, daemon cutover |
| `docs/GO_LIVE_CHECKLIST.md` | Pre-live checklist (automatable items covered by `scripts/preflight_check.py`) |
| `docs/FEATURE_TIER_HISTORY.md` | Dated history of shipped features and the entries relocated out of this file |
| `docs/VALIDATION_STRATEGY_FIX_LOG.md` | Dated rollup of `STRATEGY_REGISTRY` deployability-gate fixes and honest FAILs |
| `docs/FMP_INTEGRATION.md` | FMP data layer: settings, verification status, eyeball gates before flipping flags |
| `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md` | Capability map, guardrail status and structural gaps for agentic trading |
| `docs/JULES_INTEGRATION.md` | Jules coding-agent dispatch: gates, approval tokens, disclosed limits |
| `docs/BUG_HUNTING_PROCESS.md` | Bug-hunting SOP, severity model, domain checklists |
| `docs/test_coverage_analysis.md` | Test-suite inventory and coverage-gap roadmap |

## Feature freeze (step 7, since 2026-09-29)

The platform is in a **feature freeze** until the pipeline has closed **30** of its own paper trades
(target 50), so the next decisions rest on measured outcomes instead of more features. Only
`paper_closed_trades` rows with `strategy_id == "main_pipeline"` count — manual Quick Trades, delta
hedges and untagged rows never do. Check progress with `python scripts/feature_freeze_status.py`
(exit 0 = the minimum is reached, 2 = still frozen; `--json` for machine output). Ending the freeze
is the operator's call once the count is reached.

- **Allowed:** bug fixes; security and dependency fixes; tests; docs; measurement or observability
  of existing behavior; removing or archiving code; and the already-approved plans — shrink steps
  5.4–5.6 (`.claude/shrink_step5_retire_main_py_implementation_plan.md`) and the forecasting
  rebuild (`.claude/forecasting_rebuild_implementation_plan.md`).
- **Needs the operator's explicit OK first:** new signal modules, strategies, Pilots, webapp
  screens, data sources, ML models, or new settings flags for new capabilities.
- If a request looks like new-feature work, say that the freeze is on and ask before building.

## Frontend strategy: web app only

**The Pilots PWA (`webapp/`) is the platform's only frontend.** All operator-facing features, UI
improvements and bug fixes go into `webapp/`. The Streamlit "InvestYo Command Center" and its
pywebview shell were deleted in 2026-09 (git history has them); if it had a feature `webapp/` lacks,
build it in `webapp/`.

**`legacy/` holds archived code** — working code moved out so it can be restored. Nothing active
imports from it, pytest does not collect it, and you add to it only when archiving (record each move
in `legacy/README.md`).

**`shared/` is a normal, maintained package** of backend logic (`env_io.py`, `orchestrator_runner.py`,
`daemon_client.py`, `strategy_registry.py`, …) used by `api/*`, `pilots/*`, `main.py` and `scripts/*`.

## Commands

```bash
# ── Web app ───────────────────────────────────────────────────────────────────
./launch_webapp.command              # starts the Pilots PWA + backend APIs (mock or live)
cd webapp && npm install
npm run dev                          # http://localhost:5173 — offline mock data by default
VITE_USE_MOCK=false npm run dev      # against the live backend
npm run --prefix webapp typecheck    # mock/live API parity gate
uvicorn api.pilots_api:app --port 8602    # backend the PWA talks to (+ api.data_api:8603 / api.metrics_api:8604)

# ── Runtime ───────────────────────────────────────────────────────────────────
./setup.sh                           # creates .venv (Python 3.12, via uv) and installs requirements.txt
python -m desktop.orchestrator_daemon    # the daemon (Control API + Pilots API + pipeline timer)
python3 main_orchestrator.py         # one async pipeline run without the daemon
python -m execution.kill_switch --status # check / activate / deactivate the global kill switch
# Being retired (step 5) — prefer POST /run or shared.daemon_client.trigger_run():
python3 main.py                      # one advisory cycle; --interval N loops; --refresh-account forces a Robinhood login
./launch.command                     # runs main.py in a Terminal window (pauses on exit)

# ── Tests & gates ─────────────────────────────────────────────────────────────
pytest tests/test_<module>.py        # targeted tests; add ::test_name for one test
make ci                              # offline suite: pytest -m "not network and not slow" -n auto --dist loadgroup
make verify                          # env check + pytest + one live run + summary (./verify.command = same)
python scripts/preflight_check.py [--json]   # pre-live readiness gate (exit 0 = all pass)

# ── Validation & data ─────────────────────────────────────────────────────────
python3 -m validation.harness --strategy <name> --start YYYY-MM-DD --end YYYY-MM-DD
python -m scripts.refresh_validations --strategies <name> --workers 4
python3 database_setup.py            # (re)build the SQLite schema from config.COLUMN_SCHEMA
python scripts/verify_fmp_bars.py    # hard gate before changing FMP_BARS_ADJUSTMENT (network)
python scripts/verify_fmp_profile.py # FMP /profile check (exit 0 pass, 1 fail, 2 unconfigured)
```

`main.py`, `main_orchestrator.py` and every `scripts/*.py` entry point (via `scripts/_bootstrap.py`)
re-exec themselves under `.venv`'s interpreter when started from another Python, and load `.env` from
`settings.ENV_PATH`. `scripts/_bootstrap.py` also honors `NO_VENV_REEXEC=1` to skip its re-exec.

## Architecture

Flat, modular "Engine" architecture with dependency injection: engines are top-level modules or small
packages imported directly by the orchestrators. The per-module reference lives in `docs/architecture/`;
load the file(s) for what you're touching.

| File | Covers |
|------|--------|
| [`docs/architecture/data-layer.md`](docs/architecture/data-layer.md) | `config.py`, `dto_models.py`, `data_engine.py`, `data/market_data.py`, FMP layer, Robinhood client/portfolio/orders/login, `data/portfolio_sync.py`, `data/historical_store.py`, `data/paper_account_store.py`, `settings.LOCAL_DATA_ROOT` layout |
| [`docs/architecture/signal-engines.md`](docs/architecture/signal-engines.md) | `processing_engine.py`, `macro_engine.py`, `regime/hmm_regime.py`, `forecasting_engine.py`, `strategy_engine.py`, `sizing/` |
| [`docs/architecture/simulation-eval-reporting.md`](docs/architecture/simulation-eval-reporting.md) | `simulation_engine.py`, `universe_engine.py`, `research_engine.py`, `evaluation_engine.py`, `transactions_store.py`, `database_setup.py`, `diagnostics_and_visuals.py`, `reporting/` |
| [`docs/architecture/execution.md`](docs/architecture/execution.md) | `execution/cost_model.py`, `broker_base.py`, `fmp_paper_broker.py`, `broker_selection.py`, `kill_switch.py`, `risk_gate.py`, `order_manager.py`, `queue_builder.py`, `compose.py` |
| [`docs/architecture/execution-boundary.md`](docs/architecture/execution-boundary.md) | The explore/execute universe boundary |
| [`docs/architecture/observability-and-apis.md`](docs/architecture/observability-and-apis.md) | `observability/*`, `api/state_api.py`, `api/control_api.py`, `api/pilots_api.py`, `investyo_mcp_server.py`, `mcp_remote_adapter.py`, `pilots/retrospective_*` |
| [`docs/architecture/webapp-and-gui.md`](docs/architecture/webapp-and-gui.md) | `webapp/`, `api/data_api.py`, `api/metrics_api.py`, `shared/daemon_client.py`, `desktop/` (daemon runtime), `scripts/*` |
| [`docs/architecture/validation-and-signals.md`](docs/architecture/validation-and-signals.md) | `validation/` (purged CV, metrics, harness, stress scenarios), `signals/` (registry, aggregator, modules) |
| [`docs/architecture/ml-and-reports.md`](docs/architecture/ml-and-reports.md) | `ml/` pipeline, `reports/*.html.j2`, `ai_verification_prompts.py` |
| [`docs/architecture/testing.md`](docs/architecture/testing.md) | Index of `tests/` and what each file covers |
| [`docs/architecture/orchestration-entrypoints.md`](docs/architecture/orchestration-entrypoints.md) | `engine/advisory.py`, `.env` loading, `alerting.py`, `Makefile`, `main.py`, `main_orchestrator.py` |

**Gravity** — three different things share the name: `Gravity AI Review Suite.py` (the
`GravityAIAuditor` structural audit launcher, no LLM calls, not importable because of the space in its
filename), `ai_verification_prompts.py::GravityAIAuditor` (a different class: keyword grader for text
pasted back from an external LLM session), and `engine/gravity_ai_runner.py` (Claude + Gemini
structured-output auditor, gated by `GRAVITY_AI_RUNNER_ENABLED`, default off).

## Conventions enforced in this codebase

Each rule is short; the pointer names where the detail or the enforcing test lives.

### Honesty and resilience
- **CONSTRAINT #4 — never fabricate.** A missing or unmeasurable value is `NaN`/`None`/an explicit
  "unavailable" reason, never `0.0`, a placeholder price, or a plausible default. This applies to
  metrics, prices, IV, market caps, sample sizes and "healthy" status fields alike. A fabricated
  number that looks real is worse than a gap. See the `stockpy-quant-integrity` skill.
- **CONSTRAINT #6 — fail closed, never crash the cycle.** Read helpers and pipeline stages degrade
  to an honest empty/unavailable result instead of raising; safety gates (risk gate, kill switch,
  macro gate, deployability) fail *closed* on missing data. Per-ticker loops (`data_engine.py`,
  orchestrators, per-symbol fetches) wrap each ticker in try/except and dead-letter it so one bad
  symbol never aborts a run.
- **Degenerate-std guard.** Any ratio dividing by a computed std or drawdown guards with `< 1e-12`
  (or `>= 1e-12` to proceed), never an exact `== 0`/`> 0` check (e.g.
  `validation/metrics.py::_DEGENERATE_STD`).
- **Graduated degrade for N-way blends.** A blend of independent estimators excludes an immature or
  missing component and renormalizes over the survivors; never an `any()`/`all()` gate that lets one
  gap silence the rest. True all-or-nothing gating is only for structurally coupled objects (a
  covariance matrix) or deliberate worst-case safety gates, with a comment saying why. See
  `docs/known_issues/graduated_degrade_all_or_nothing_blends.md`.

### Data and math
- All data crossing into calculation code goes through the DTOs in `dto_models.py`, not raw dicts.
- Pipeline data fetching goes through `IDataProvider` implementations in `data_engine.py`; other
  quote/bar/fundamentals fetches go through `data/market_data.py`'s `CompositeProvider`
  (`get_provider()`), which is FMP-primary with yfinance fallback. Never call `yfinance` or a
  vendor SDK directly from feature code.
- Technical/fundamental math is vectorized — no per-row Python loops or `iterrows` in core engines
  (`tests/test_no_iterrows_in_core_engines.py`, which also lists signal modules still using the
  per-row `compute_vectorized` fallback).
- Every indicator, forecaster and feature must be lookahead-free, proven with a perturbation test
  (mutate the future, assert the past is bit-identical). New/changed indicators need a test;
  numeric drift on existing indicators must stay below 1e-5.
- **`FMP_BARS_ADJUSTMENT`** (default `"dividend-adjusted"`) must match the incumbent split+dividend
  adjusted convention; FMP's `light`/`full` variants are split-only and silently corrupt every return
  series. `scripts/verify_fmp_bars.py` must PASS before it changes. See `docs/FMP_INTEGRATION.md`.
- **Data-source policy for new features:** a new capability needing live data this codebase doesn't
  already have may depend only on **FMP or Yahoo (yfinance)**. Alpaca and Finnhub were
  removed (2026-09-30 / 2026-09). If neither source has the data, disclose the gap
  rather than build around a third provider.

### Settings, credentials and storage
- **Read config via `settings.X`, never `os.environ`/`os.getenv`.** pydantic-settings loads `.env`
  into `Settings` only, not into the process environment, so an `os.environ` read silently sees
  nothing for a value that lives only in `.env`. Enforced by
  `tests/test_measure_settings_census.py::TestFormDOsEnvironIsFullyAllowlisted` (only `GCLOUD_BIN`
  and `NO_VENV_REEXEC` are allowed). Tests patch `settings.settings.X`, not `os.environ`.
- **Every external call has a timeout.** `subprocess.run/call/check_call/check_output` and
  `requests.*` calls need `timeout=` (`tests/test_no_missing_call_timeouts.py`, an AST guard).
  Libraries with no timeout parameter get one another way: FRED via
  `data_engine.py::_bounded_fred_timeout`.
  Pipeline steps are bounded by `PIPELINE_STEP_TIMEOUT_SECONDS`, data sub-fetches by
  `DATA_FETCH_TASK_TIMEOUT_SECONDS`, LLM chat clients by `AI_CHAT_TIMEOUT_SECONDS`. See
  `docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md`.
- **Shared rate limiters per host.** FMP, GDELT and SEC EDGAR each have one module-level throttle
  (`data/fmp_client.py`, `data/sentiment_sources.py::_gdelt_get`, `data/edgar_fundamentals.py`); a
  new consumer of the same host must reuse it, never open a second client.
- **Paths:** `settings.ENV_PATH` is the single `.env` locator (anchored to the repo, never
  CWD-relative or `find_dotenv()`). `settings.LOCAL_DATA_ROOT` (default `~/.stockpy_local`) is the
  machine-global home for the DB, `OUTPUT_DIR`, models, caches and logs, shared by every worktree.
  A store that opens its own DB must resolve it through `db_config.py`
  (`resolve_database_url()`/`create_db_engine()`) — never a bare-literal default `db_path` (a
  recurring bug class; see `docs/known_issues/forecast_tracker_local_data_root_split.md`).
- **Test isolation for implicit-default stores.** If a store's default constructor resolves the real
  shared DB and is reachable from widely-called production code, add a session-wide autouse fixture
  in the root `conftest.py` that redirects it (pattern: `_isolate_validation_runs_db_in_tests`,
  `_isolate_broker_fills_db_in_tests`, `_isolate_paper_and_transactions_db_in_tests`,
  `_isolate_forecast_tracker_db_in_tests`, `_isolate_runtime_flags_store_in_tests`,
  `_isolate_symbol_rating_db_in_tests`). Without one, the suite writes fake rows into the operator's live DB (it has happened:
  `docs/known_issues/pr872_live_db_test_contamination_2026.md`).
  Network-touching defaults get the same treatment (`_force_mock_data_engine_in_tests`,
  `_stub_paper_marking_network_in_tests`).
- **`.env` writes go through `shared/env_io.py`** (`write_setting`/`write_many`/`write_many_atomic`):
  `ALLOWED_KEYS` is the allowlist of non-secret GUI-writable keys, `SECRET_KEYS` are masked on read and
  refused on write, anything else raises. Never add a credential to `ALLOWED_KEYS`. Pass `_JSON_KEYS`
  values as plain Python objects — `write_setting` owns the JSON encoding. The `.env` file itself is
  never written by an agent (hook-enforced).
- **`settings_keysets.py`:** `DANGEROUS_KEYS` (execution/write master switches and other
  safety-critical keys) require typed confirmation in any settings editor; `BOOTSTRAP_KEYS` can't be
  changed at runtime.
- **Runtime flags store** (`output/runtime_flags.json`). Precedence: real shell env > store > `.env` >
  field default. Read path `runtime_flags.py::apply_overrides` is a stdlib-only leaf imported by
  `settings.py` — it must never import `settings`/`shared.env_io`/`config` (circular import; AST
  test in `tests/test_runtime_flags.py`). Write path `runtime_flags_writer.py`
  (`write_override`/`delete_override`) refuses `SECRET_KEYS`, `BOOTSTRAP_KEYS`, unknown fields and
  invalid values, validates via `Settings.__pydantic_validator__.validate_assignment` (never
  `TypeAdapter`, which skips field validators), writes atomically, and never logs a value. The daemon
  re-reads the store each wake when `RUNTIME_FLAGS_REFRESH_ENABLED`. **A `.env` write of a key must
  also update or clear that key's runtime-flags store override**, or the stored value silently wins
  (`PUT /automation/execution-mode` does this; see
  `docs/known_issues/runtime_flags_store_test_contamination_2026_10.md`).
- **Default-value policy.** New admin/API write or execution capabilities ship active by default
  rather than behind a fresh opt-in flag. New settings that change **trading behavior** (signals,
  sizing, data sources, forecasting, execution) default to today's exact behavior (off/opt-in) — a
  silent behavior change on `git pull` is a real risk for a live account.

### Signals, validation and sizing
- Scoring in `StrategyEngine.evaluate_security` is decoupled into `SignalModule`s under `signals/`,
  combined by the weighted-sum `SignalAggregator` with weights from `settings.SIGNAL_WEIGHTS`.
  Cross-sectional modules use the two-phase hook: `pre_compute(universe_df, context)` once per cycle
  (via `global_registry.run_pre_compute()`), then per-ticker `compute(row, context)` reading the
  precomputed ranks. Regime-fragile modules opt out via `is_active_in_regime()`; the operator
  disables modules via `settings.DISABLED_SIGNAL_MODULES`. Both are enforced centrally in the
  aggregator. See the `new-signal-module` skill.
- **Deployability gate** (`validation/harness.py`, thresholds in `validation/thresholds.py`): a
  strategy is deployable only with PBO < 0.5, DSR > 0.95, net-of-cost Sharpe > 0.5 and MaxDD < 30%.
  Options-selling strategies additionally need MaxDD < 50% and survival in every dated shock window
  (`validation/stress_scenarios.py`, `is_options_selling=True`; fails closed if never stress-tested).
  Harness cost modeling scales with turnover and uses `execution/cost_model.py::TieredCostModel`.
  A gate change (fix or honest FAIL) is documented in both `docs/signals/<name>.md` (Backtest
  Validation section) and `docs/VALIDATION_STRATEGY_FIX_LOG.md`. See the `strategy-validation` skill.
- Every backtest shows the survivorship-bias warning (`simulation_engine.py`); new trading rules are
  optimized in `vectorbt` and validated in `backtrader` before reaching `strategy_engine.py`.
- **Position sizing** has one source of truth: `StrategyEngine._calculate_kelly_sizing` →
  `sizing/kelly.py`/`sizing/vol_target.py`, composed with the regime multiplier and meta-label
  composite by `sizing/position_sizer.py::size_position()`, then clamped by `MAX_POSITION_WEIGHT` and
  the cycle-wide `MAX_PORTFOLIO_GROSS` cap. The advisory path (`engine/advisory.py`) keeps its own,
  tighter per-position cap. Production Kelly reads the `transactions_store` `trades` ledger
  unfiltered by strategy, so manual/broker trades must never be written there
  (`data/broker_fills_store.py` stays structurally separate).
- Snapshot parity: fields added to `output/state_snapshot.json` must be written by both writers or
  pinned as orchestrator-only in `tests/test_state_snapshot_parity.py`.

### Execution, brokers and Robinhood
- All order submission goes through `execution/order_manager.py::OrderManager`, typed against
  `BrokerBase` — never a concrete broker directly. `BROKER_BACKEND` is `fmp_paper` only
  (`execution/fmp_paper_broker.py` filling against real FMP quotes + `TieredCostModel`). When going
  live (`PAPER_TRADING=false` and `ADVISORY_ONLY=false`) `execution/broker_selection.py::resolve_broker_backend()`
  returns None and the automated pipeline places **no orders** (CRITICAL log + alert); real money moves
  only through the Robinhood queue below. `ALPACA_PAPER` in an old `.env` still works as an alias of `PAPER_TRADING`.
- Every intent gets a deterministic `client_order_id` from `make_client_order_id(...)`; never build or
  reuse IDs by hand. `intent.dry_run` is enforced in `OrderManager` (the authoritative check).
- Broker execution is best-effort: errors are logged and never crash the analysis pipeline.
- The kill switch (`execution/kill_switch.py`) and `execution/risk_gate.py::PreTradeRiskGate`
  (incl. the macro kill-switch check behind `MACRO_REGIME_GATE_ENABLED`) sit in front of every order.
  Macro inputs fail closed: when FRED data is missing or fabricated, `MacroEconomicDTO.data_unavailable`
  forces the kill switch on.
- **Robinhood trading** is queue-driven: the pipeline writes `execution_queue.json`; the
  `robinhood-execution` skill / `/rh-execute` previews and, only in `ROBINHOOD_EXECUTION_MODE=live`
  with per-trade human confirmation, places orders. Paper first.
- **Robinhood login is device-approval only.** `data/robinhood_portfolio.py::_login_with` rejects by
  default (`RobinhoodApprovalRequired`) and logs in only inside the isolated worker
  (`data/robinhood_login_worker.py`, `RH_LOGIN_WORKER=1`), launched per attempt as a killable,
  deadline-bounded subprocess by `data/robinhood_login.py::start_login`. There is no TOTP path.
  Logins are single-flight per process and across processes (file lock under `OUTPUT_DIR`). Live
  Tier-3 refresh is opt-in (`ROBINHOOD_AUTO_REFRESH_ENABLED`, default off); headless scripts must
  never trigger a login. See `docs/known_issues/robinhood_device_approval_login_hang_risk.md`.

### Daemon and APIs
- **`OrchestratorDaemon.is_running` and `.last_result` are `@property`** — read them without parens;
  `status()`, `get_run()`, `trigger_run()`, `set_interval()`, `start()`, `shutdown()` are methods.
  Test fakes for classes with properties must be hand-written classes (not bare `MagicMock`, not
  `create_autospec`); `tests/test_control_api.py` pins this with an AST guard and a shape check.
- Shutdown is bounded by one budget, `DAEMON_SHUTDOWN_TIMEOUT_SECONDS`; outer supervisors must wait
  longer than it (ladder in `docs/RUNBOOK.md`). State files are written atomically (temp + `os.replace`).
- Automatic runs are skipped outside the 04:00–20:00 ET weekday window when
  `ORCHESTRATOR_EXTENDED_HOURS_ONLY` (default on); manual runs always bypass it.
- Control API command endpoints are fail-closed on `ORCHESTRATOR_DAEMON_TOKEN`. Pilots API tiers:
  fail-open read (`STATE_API_TOKEN`), fail-closed command (`FOLLOW_API_TOKEN`), and command + a
  dedicated `_ENABLED` flag for writes. `pilots/*.py` read helpers stay dependency-light (no heavy
  engine imports; `tests/test_pilots_strategy_matrix.py`). See the `pilots-endpoint` skill.
- Webapp API changes keep `webapp/src/api/client.ts` and `mock.ts` in parity, with honest mock
  fixtures that exercise degraded/blocked shapes, and must be checked against a live backend — a
  typecheck alone has repeatedly missed live field mismatches. See the `new-pwa-screen` skill and
  the `api-parity-reviewer` agent.

### Help content
- Webapp help lives in `webapp/src/help/helpContent.ts` (`TAB_HELP`, `GLOSSARY`), rendered by
  `webapp/src/components/TabGuide.tsx`. Tunable values in help text come from live `GET /thresholds`,
  never hard-coded literals (a fixed algorithm constant may be literal with a comment saying why).
- Python help lives in `shared/help_content.py`; every `guide_anchor` must resolve to a heading in
  `docs/HOW_TO_GUIDE.md` (`tests/test_help_content.py::TestAnchorValidity`), and thresholds are read
  from `settings`/`validation.thresholds`/`engine.advisory.CONFIG`, never re-typed.

## Archived subsystems (in `legacy/`, not active)

Don't build on these or cite them as live. `legacy/README.md` lists every moved file.

- **Options desk** (steps 3d–4b): `legacy/technical_options_engine.py`, `legacy/pilots/options*.py`,
  dispersion/copula/earnings-crush/vol-mispricing/GEX/LOB/0DTE (`legacy/pilots/zero_dte_engine.py`),
  `legacy/execution/options_*.py`, `legacy/validation/options_harness.py`, the options signal modules
  and the implied-vol engine. Kept in core: `data/option_symbols.py` (paper option marking),
  `volatility/garch.py` and `trend_indicators.py`.
- **FIX / multi-broker gateway, SEC Rule 606, dynamic circuit breaker, Almgren-Chriss router:**
  `legacy/execution/{fix_gateway,multi_broker_gateway,sec_rule_606_reporter,dynamic_circuit_breaker,almgren_chriss_router}.py`,
  `legacy/data/execution_audit_store.py`.
- **Research/ML extras:** `legacy/sizing/hrp_cvar_optimizer.py`, `legacy/validation/synthetic_diffusion_engine.py`,
  `legacy/ml/drl_market_maker*.py`, `legacy/ml/transformer_vol_forecaster.py`, `legacy/llm/research_copilot.py`.
- **ETF volatility transmission** (step 4d): `legacy/risk/etf_transmission.py`, `legacy/data/etf_holdings.py`.
- **Follow-a-Pilot** (step 4c): `legacy/pilots/{mirror,follows_store,portfolio_attribution}.py`. The
  Pilots catalog/marketplace stays; `FOLLOW_API_TOKEN` remains the general command token.
- **Google Sheets publisher** (step 4e): `legacy/reporting/{sheet_publisher,sheets_client}.py`.
- **Alpaca** (2026-09-30): `legacy/execution/alpaca_broker.py`, `legacy/data/{alpaca_http,market_data_ws,websocket_streamer,alpaca_provider}.py`
  and their tests in `legacy/tests/`. Market data is FMP then yfinance; `/ws/ticks/{symbol}` pushes REST quotes.
- **Cache Long/Short and the Pairs radar** (2026-10): `legacy/engine/cache_long_short_engine.py`,
  `legacy/data/cache_long_short_store.py`, `legacy/pilots/{cache_long_short,pairs}.py`,
  `legacy/reporting/pairs_snapshot.py`, with their webapp screens and the `CACHE_LONG_SHORT_*` /
  `PAIRS_SNAPSHOT_*` settings. The `pairs_trading` signal and `pairs_ondemand.py` (MCP tools) stay.
- **Streamlit desktop app**: deleted (git history only).
