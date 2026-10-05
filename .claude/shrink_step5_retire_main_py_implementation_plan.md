# Step 5 plan: move agentic trading into the orchestrator daemon, then retire `main.py`

## Operator decisions (2026-09-28)
1. **Option (a):** keep `engine/advisory.py` as the queue's decision engine, running inside the daemon.
2. **`ADVISORY_REUSE_PIPELINE_COMPUTE`:** keep reuse on (the live value), and show the before/after diff in PR 5.2 gate (ii).
3. **Queue freshness:** the `robinhood-execution` skill triggers `POST /run` and waits for the fresh queue.
4. **`--agent` features:** archive them. Only `is_automatic_run_gated` stays, and it moves to `shared/`.
5. **`daily_report.html`:** retire it. Use `daily_report_dashboard.html` and the webapp.
6. **Robinhood device-approval login:** a daemon hook runs it at a fixed weekday time, e.g. 08:40 ET.
7. **Universe change:** accepted, with every moved symbol listed in the PR 5.1 diff.
8. **`ORCHESTRATOR_DAEMON_ENABLED`:** it becomes the only mode and is retired in PR 5.6.


Everything below was read at `origin/main` @ `c2968b7c`. Your main checkout is one commit behind, at `8a8f21bf` (step 4f). The live-state checks were read-only: `~/Stockpy-live/.env` (I read mode flags only, never secret values), `~/.stockpy_local/output/`, `~/Library/LaunchAgents`, and the logs.

## Recommendation

**Do option (a): keep `engine/advisory.py` as the decision engine and run it inside the daemon.** Don't let `StrategyEvalStep` output drive the queue.

Most of the work is already done. `BrokerExecutionStep` calls `engine.advisory.evaluate()` for every ticker in every daemon cycle (`pipeline/production_steps.py:2618-2735`). It then throws away everything except five `Advisory_*` columns. It never writes `queue_sources/advisory.json` and never calls `compose_and_emit`. So "porting" mostly means three things:
- keep the `Recommendation` objects;
- feed them the same inputs `main.py` builds;
- add the queue, watch-engine and report side-effects as their own steps.

Option (b) would change what trades you see (details in section 3).

## Decisions you need to make first

1. **Option (a) or (b).** I recommend (a).
2. **`ADVISORY_REUSE_PIPELINE_COMPUTE`** is `true` in the live `.env`. It changes the queue.
   - With it on, the daemon's advisory run reuses the pipeline's `GARCH_Vol` and `Forecast_30` (`production_steps.py:2649-2682`). `main.py` fits its own.
   - The bullish-forecast confirmation is what lifts a BUY to 0.85 conviction (`engine/advisory.py:1196-1197`). 0.85 is the queue floor (`execution/queue_builder.py` CONFIG, `min_conviction` 0.85). So whether the forecast is refit or reused decides which BUYs reach the queue.
   - I recommend keeping reuse on, which matches the forecasting plan's step F4 ("the advisory path reads the pipeline's persisted forecast"). I would show you the before/after diff first.
3. **How fresh the queue must be.**
   - The `robinhood-execution` skill refuses a queue older than about 30 minutes (`.claude/skills/robinhood-execution/SKILL.md:90-91`).
   - The daemon runs every 3600 s (`ORCHESTRATOR_INTERVAL_SECONDS=3600`). Interval cycles can also be skipped by the data-freshness gate (`main_orchestrator.py:1196-1203`, TTL 900 s).
   - Choose one: the skill triggers `POST /run` on the Control API and waits (recommended), or you shorten the interval during market hours, or you relax the skill's rule.
4. **The `--agent` features** (adaptive cadence, backlog reminders, the conviction-momentum and price-trigger alerts in `engine/trade_signals.py`, and `agent_state.json`).
   - `agent_state.json` has never existed in the live output directory, so `--agent` has never run there. The Agentic Trading tab's status reader (`pilots/agentic.py`) already shows "no state".
   - I recommend archiving them. Keep only `is_automatic_run_gated`, which the daemon uses (`desktop/daemon_runtime.py:55`).
5. **`daily_report.html`** (the advisory report with the account summary band). Keep it by generating it from the daemon, or retire it in favour of `daily_report_dashboard.html`.
6. **When the daily Robinhood device-approval login happens.**
   - Today the 08:45 launchd run triggers it.
   - Under the daemon it fires whenever the cached account snapshot passes 20 h (`data/robinhood_portfolio.py:513-518`). That could be at an hour when you can't approve it.
   - Options: a daemon hook at a fixed time (for example 08:40 ET on weekdays), or rely on the webapp's refresh button (`POST /brokerage/refresh`).
7. **The daemon's universe will change** (section 1). It gains the recently-closed retention symbols and main.py's rule for when to fall back to `DEFAULT_TICKERS`. Cross-sectional ranks, and therefore scores, move for every symbol.
8. **`ORCHESTRATOR_DAEMON_ENABLED`**: I recommend making the daemon the only mode and retiring the flag in the last PR (section 4).

## What runs today (live)

- **The launchd job `com.investyo.daily-advisory` is installed and loaded.** It runs `.venv/bin/python3 main.py` once, on weekdays at 08:45, from `/Users/kevinlee/Stockpy-live`, and logs to `output/scheduled_advisory.err`.
  - The last runs took 342–364 s for 30 symbols ("25 held, 2 watchlist-only, 3 discovered, 2 recently-closed").
  - The watch engine fires every day: 2 rules, 2–9 alerts per run.
- **The daemon is started by hand through `launch_webapp.command`.** `daemon.json` says interval 3600, ports 8601/8602, last started 2026-09-27, currently stopped.
  - Nothing is running right now, and `com.investyo.stack` is not installed.
  - Daemon cycles take about 3 min for 28–29 tickers (`~/.stockpy_local/logs/investyo.log.1`, 2026-09-10).
- **Mode flags in `.env`:**
  - `ORCHESTRATOR_DAEMON_ENABLED=true`
  - `ROBINHOOD_EXECUTION_MODE=live`
  - `ADVISORY_ONLY=false` (also `false` in `runtime_flags.json`)
  - `ROBINHOOD_MAX_NOTIONAL_PER_ORDER=25`
  - `ADVISORY_REUSE_PIPELINE_COMPUTE=true`
  - `RUNTIME_FLAGS_REFRESH_ENABLED=true`
  - `PILOTS_API_ENABLED=true`
  - `BROKERAGE_REFRESH_ENABLED=true`
  - `ROBINHOOD_AUTO_REFRESH_ENABLED=true`
  - `CLOSED_POSITION_RETENTION_DAYS=180`
  - `SYMBOL_RATING_AUTO_DROP_ENABLED=true`
  - `DEAD_LETTER_RETRY_ENABLED=false`
  - `BROKER_BACKEND=fmp_paper`
- **`ORCHESTRATOR_EXTENDED_HOURS_ONLY` is effectively `true`.** It is `false` in `.env`, but `runtime_flags.json` sets it `true`, and the store wins (precedence is shell > store > `.env`, `runtime_flags.py`).
- **Keys that are set (values not read):** `FRED_API_KEY`, `RH_USERNAME`, `RH_PASSWORD`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `FINNHUB_API_KEY`, `FMP_API_KEY`, `WATCHLIST`, `NTFY_TOPIC`, `NTFY_DASHBOARD_URL`.
- **The equity queue has not refreshed since 2026-08-21.**
  - `compose` refused every morning because of a stale Follow source: `scheduled_advisory.err:25502`, "refusing to compose -- follow source follow-cross-sectional-momentum … stale=True".
  - Step 4c's compose reads only `advisory.json` (`execution/compose.py:418-429`). So the next 08:45 run will write the first live-mode queue in five weeks.
  - The ten `queue_sources/follow-*.json` files are no longer read.
  - On 2026-09-25 the advisory source held 23 actionable targets. Only 1 cleared the 0.85 floor (a BUY).

---

## 1. What `main.py` does, and what the daemon has instead

**Startup** (main.py):

| Responsibility | main.py | Daemon equivalent |
|---|---|---|
| Re-launch under the project's `.venv` Python | 57-68 | none needed (launchers use the venv) |
| Import TensorFlow before pandas (deadlock guard) | 117-120 | `production_steps.py`/`main_orchestrator.py` (covered by `test_cnn_lstm_import_order`); isolation flag on in `.env` |
| Load `.env`, set up logging, FRED-key leak warning | 1307-1310 | `orchestrator_daemon.py:74-80`, `:602-603`; the leak warning is at `production_steps.py:86` |
| Keep one `MacroEngine` alive for the process | 180-203 | the warm `EngineContext` (`main_orchestrator.py:319-372`) |
| `--interval` / `--agent` / single-run modes, signal handling | 1269-1297, 1452-1486 | the daemon's timer (`daemon_runtime.py:1053-1111`) and `POST /run` |

**Each cycle: `run_once` (946-1039) and `_run_cycle` (1319-1450):**

| Responsibility | main.py | Daemon | Divergence |
|---|---|---|---|
| Account snapshot (20 h cache, empty snapshot on failure) | `pipeline/steps.py:29-65` | `production_steps.py:145-159`; fetched again at `:2633-2640` | same cache; the daemon's second call is a cache hit |
| Universe | `_build_universe` 312-399 | `production_steps.py:88-161` | see "Universe" below |
| Discovery candidates | 355-362 | `production_steps.py:89-97` | same `pilots.discovery.discovery()` |
| Closed-position retention | 282-309, 383-389 | **none** | 2 symbols in the live runs; the daemon drops them |
| Kill-switch gate | `steps.py:92-118` (before macro) | `production_steps.py:205-216` (after data fetch) | both stop the cycle before any queue write |
| Macro inputs | `_build_macro_dto` 406-537 | `production_steps.py:245-305` | see "Macro" below |
| Meta-labeler registry bootstrap | `steps.py:135-139` | `production_steps.py:1859-1860` | same |
| Universe-wide pre-compute | `_build_context_extras` 651-903 | xsec `production_steps.py:1721`, shared context `:1902`; CoVaR/excursion via `dashboard_df` | see "Advisory run" below |
| Advisory evaluation | `steps.py:155-238` | `production_steps.py:2624-2735` | the daemon keeps 5 columns and **drops the `Recommendation` objects** |
| Symbol-rating audit write | `steps.py:247-277` | `production_steps.py:2558-2563` | equivalent |
| Execution queue (`write_advisory_source` + `compose_and_emit`) | 1420-1430 | **none** | the only production caller is `main.py` |
| Watch engine | 1366-1399 | **none** | live and firing every day |
| Alerting (`summarize_run`, error push, one "clean run" push per launch) | 1325-1357 | `_dispatch_daily_summary` (`main_orchestrator.py:1115-1127`, `:1254-1257`) | a different channel and content |
| HTML report | 1435-1449, which calls `reporting/html_publisher.py:24-125` | `production_steps.py:2813-2848` | different file (`daily_report_dashboard.html`) |
| State snapshot | `html_publisher.py:114` calls `reporting/state_snapshot.py:59-283` | `StateSnapshotStep` (`production_steps.py:2794-2799`) calls `main_orchestrator.py:742-1003` | see "Snapshots" below |
| Progress reporting | 1018-1025 | `main_orchestrator.py:1157` | equivalent |
| Extended-hours gate | 213-216, 1125, 1467 (interval/agent only; single-run is not gated) | `daemon_runtime.py:1106-1110` (interval only) | equivalent |
| `--refresh-account` | 1264-1285, 1314-1322 | `POST /brokerage/refresh` (`api/pilots_api.py:3201-3246`) | same device-approval worker, but no cycle runs afterwards |
| Research engine (CoVaR) | 803-818 | inside `processing_engine` technical metrics, surfaced as the `CoVaR Proxy` column | equivalent source |
| Agent loop (backlog reminders, trade signals, `agent_state.json`, adaptive sleep) | 1072-1237 | **none** | decision 4 |

**Universe.**
- `main.py` passes `held` into `compute_tracked_universe` (main.py:368-373), so the `DEFAULT_TICKERS` fallback only fires when held, watchlist and discovered are *all* empty.
- The daemon calls it without `held` (`production_steps.py:109-113`) and appends held symbols afterwards (`:150-153`). An empty watchlist plus empty discovery therefore pulls in `DEFAULT_TICKERS` even when you hold positions.
- The daemon also falls back to `["AAPL"]` on `MockDataEngine` (`:139-140`).
- Cross-sectional ranks are computed over the universe, so a different universe gives different scores.

**Macro.**
- Both paths use `_calculate_sahm_rule_detailed` and mark the DTO when data is missing (`data_unavailable`).
- The VIX default differs: 18.0 in main.py (512) and 15.0 in the daemon (`:300`).
- The HMM's SPY input differs:
  - main.py reads 504 days from `HistoricalStore` (464-485);
  - the daemon uses `tech_raw['SPY']` (`:281`; SPY is added at `main_orchestrator.py:278`).
- Only the daemon calls `run_macro_killswitch` (`:277`).

**Advisory run.** The daemon passes only xsec ranks and multifactor scores as context extras (`:2628-2631`). Those are the only extras that affect `StrategyEngine` scoring (`strategy_engine.py:322-324`). The rest (bars, fundamentals, news, CoVaR, excursion) only fill `key_indicators` and avoid a second fetch (`advisory.py:668-670`, `:743-744`).

**Snapshots.**
- Both writers write the same `state_snapshot.json` and rotate into `history/`, so the file alternates between them.
- `action` and `kelly_target` mean different things in the two writers:
  - advisory writer: the advisory action and `suggested_position_pct`;
  - orchestrator writer: `Action Signal` (STRONG BUY / RISK REDUCE vocabulary) and the strategy `Kelly Target`.
- Only the advisory writer emits `garch_vol` and `suggested_exit_pct`. `garch_vol` feeds `pilots/symbols.py:490` and `api/data_api.py:1096`, so SymbolDetail's GARCH value goes blank under daemon snapshots.
- `tests/test_state_snapshot_parity.py:131-142` lists the fields only the orchestrator writes: `attention_score`, `sector_heat_factor`, `google_trends_asvi`.

**Bug in main.py today.** `main.py:1435-1449` passes a hand-built neutral `MacroEconomicDTO` to the report and snapshot writer instead of `result.macro_dto`. It doesn't set `data_unavailable`, so the default `False` applies. The advisory-written history files `state_snapshot_20260924T125159Z.json` and `…0925…` show `market_regime: RISK ON`, `vix: 0.0` and `macro_kill_switch: False` taken from those fake values. This is a CONSTRAINT #4 violation that overwrites the daemon's real macro fields every morning. `_read_macro_snapshot_hint` (1046-1069) also reads it.

## 2. Who calls `main.py`

**Launchers and schedulers**
- `launch.command:22-24, 120-134` (`--interval 60` or single run).
- `scripts/com.investyo.daily-advisory.plist:44` and `scripts/install_schedule.command:8, 45, 134`; installed and loaded live.
- `scripts/install_stack_service.command:69-76` already unloads the daily-advisory job, but the stack service has never been installed.
- `scripts/com.investyo.stack.plist` hard-codes `/Users/kevinlee/Desktop/Stockpy`, but the installer rewrites the paths.
- `Makefile:50-62` (`_live_run`, part of `make verify`) and `verify.command:103-115` both call `main.run_once()`.
- These do **not** call `main.py`:
  - `scripts/investyo_stack_service.sh:134` and `deploy/investyo-daemon.service:32` already run the daemon;
  - `launch_webapp.command:497-512` starts the daemon only when the flag is on;
  - `launch_app.command`, `desktop/engine_supervisor.py` and `app_shell.py` no longer exist on `origin/main`.

**`shared/orchestrator_runner.py`**
- `launch_advisory_main` (389-447, `main.py` at 417) is reached through `api/_jobs.py:240-243` (`JobType.ADVISORY`) and the webapp Console's "Advisory Pipeline" button (`webapp/src/screens/Console.tsx:42`).
- `launch_scheduled_advisory` (450-545, `main.py` at 506) has no production caller left; only Gravity and tests reference it.
- `launch_symbol_retry` (1466-1530, `main.py` at 1494) backs `POST /dead-letter/retry` (`api/pilots_api.py:5377-5408`); the flag is off in the live env.
- `HIGH_STAKES_COMMANDS["main.py"]` (1116-1118) gates `--refresh-account` for the Commands screen.
- Existing bug: `launch_orchestrator` appends `--refresh-account` to `main_orchestrator.py` (353-357). That script's argparse has no such flag (`main_orchestrator.py:1361-1377`), so the subprocess fallback exits with status 2.

**Commands screen and completions**
- `cli_introspect/targets.py:27` and `cli_introspect/command_manifest.json:7-8`.
- `completions/investyo.{bash,zsh}` and `webapp/src/commandParse.ts:59`.

**MCP**
- `investyo_mcp_server.py:1074-1115` (`generate_html_report` shells out to `main.py` and reads `daily_report.html`).
- Text hints at `:2172-2183` and `:4079`.
- `broker_live_execution_mcp.py` and `pilots/live_trade_proposals.py` are independent of `main.py`.

**Hint text shown to you** (tells you to run `python3 main.py …`)
- `data/robinhood_portfolio.py:608, 624`, `data/robinhood_orders.py:458`, `scripts/preflight_check.py:651, 959-987`, `pilots/observability.py:314`.
- `webapp/src/screens/SettingsBrokers.tsx:196`, `TradeHistory.tsx:57`.
- `shared/help_content.py:786`.

**Skills and commands**
- `.claude/skills/robinhood-execution/SKILL.md:14, 74, 91` (and the `.agents/` copy).
- `.claude/skills/agentic-discovery/SKILL.md:22, 150`.
- `.agents/skills/incident-triage/SKILL.md:104, 113` and `.agents/skills/alert-rule-authoring/SKILL.md:32-52`.

**Gravity AI Review Suite** reads `main.py` source or imports it at lines 3561, 3765, 4143, 7218, 7740, 8560, 9313, 12450, 12656 and 13138. After the archive these checks will report failures.

**Tests that import or patch `main`** (9 files, 121 test functions; not every test uses `main`):

| Group | Files (test count) |
|---|---|
| `run_once` pipeline | `test_run_once` (45), `test_pipeline_smoke` (9, `TestRunOncePipeline`), `test_progress_emission` (11) |
| Universe and retention | `test_universe_retention` (13); `test_run_once` `TestBuildUniverse` |
| Pre-compute | `test_main_multifactor_precompute` (11), `test_xsec_momentum_advisory_parity` (2) |
| Kill switch / macro | `test_advisory_pause_gate` (22), `test_run_once` macro classes |
| Misc | `test_main` (5), `test_reporting_package` (3, skips if the import fails) |

These read `main.py` from disk or assume its argv, and will fail when it moves:
- `test_watch_alerts.py:754-763`, `test_cnn_lstm_import_order.py:30`, `test_env_loading.py:42`;
- `test_command_execution.py:396-412`, `test_orchestrator_runner.py:191-193`, `test_pilots_commands.py`, `test_shell_completion.py:76`;
- `test_investyo_mcp_server.py:2163-2166`.

The advisory snapshot writer is pinned by `test_state_snapshot_parity.py` and `test_state_snapshot_advisory.py` (13 tests). `test_compose_advisory_only_golden.py` pins how `compose_and_emit` turns the source file into the queue; the golden file itself stays valid under both options.

## 3. Decision logic: option (a) versus option (b)

`advisory.evaluate()` wraps `StrategyEngine.evaluate_security()` (`advisory.py:927-967`) and then adds:
- the signal-to-action and conviction mapping (1118-1131);
- the holding-aware rules: Case A, loss plus bearish forecast gives SELL at 0.80 (1142-1151); Case B, dividend hold (1157-1169); Case C, gain plus flat forecast gives HOLD (1178-1190);
- the bullish-forecast conviction boost to 0.85 (1196-1197);
- SELL exit sizing (1204-1209);
- Kelly sizing capped at 5% (`CONFIG` 282, 291; 1217-1224);
- conviction multipliers for degraded data (1451-1453).

**Option (a): advisory inside the daemon (recommended).**
- The queue keeps today's rules:
  - BUY/SELL/HOLD vocabulary, at most 5% per name, the 0.85 floor;
  - SELL means a full exit (`queue_builder.py:298-313`, `pct == 0`).
- In practice that means about one BUY a day. Regular SELLs never reach 0.85, since 0.65 and 0.80 are the maximums; the only exception is a STRONG BUY escalated by Case A.
- What still differs from `main.py` comes only from inputs: the universe, the macro VIX/HMM source, and the reuse flag (decision 2). Each can be diffed and explained.

**Option (b): `StrategyEvalStep` drives the queue.**
- The vocabulary becomes STRONG BUY / BUY / HOLD / RISK REDUCE.
- There is no conviction value, so the 0.85 compose floor (`compose.py:344-354`) has nothing to filter on. You would have to invent a mapping, which is a product decision.
- Sizing becomes `Kelly Target`, up to `MAX_POSITION_WEIGHT=1.0` and `MAX_PORTFOLIO_GROSS=3.7` in the live env. A positive target on a RISK REDUCE row would turn full exits into partial trims (`queue_builder.py:305-313`).
- Case A/B/C and `suggested_exit_pct` disappear.
- You would see different, more numerous and larger intents. Only the $25 per-order cap would hold notional down.

**What pins current behaviour:**
- `tests/fixtures/compose_advisory_only_queue.golden`, pinning source to queue;
- `test_pipeline_smoke.py::TestAdvisoryTailoringRules` (Case A/B/C);
- `test_advisory.py` (61 tests, CONFIG);
- `test_advisory_pause_gate.py::TestMacroTriggeredGating`;
- `test_xsec_momentum_advisory_parity.py`.

Nothing pins recommendations produced from frozen market inputs. PR 5.1 adds that golden.

## 4. PR sequence

Each PR merges with the suite green. Every equivalence gate runs a **frozen-input harness** in the style of the step 3d GARCH gate:
- inputs pinned: account snapshot, universe inputs, bars/fundamentals/`macro_raw`/SPY from a copied `HistoricalStore`, `scan_candidates.json`, `watchlist.txt`, a copy of the `TransactionsStore`, `now`, `ADVISORY_MAX_CONCURRENCY=1`, network off;
- compared: `queue_sources/advisory.json`, `execution_queue.json` (bytes), the per-symbol `Recommendation` fields (action, conviction, `suggested_position_pct`, `suggested_exit_pct`, rationale), and `state_snapshot.json` with timestamps stripped.

**5.0: Prep, no behaviour change**
- Move main.py's input helpers (`_load_watchlist`, `_recently_closed_universe_symbols`, `_build_universe`, `_build_macro_dto` and its engine cache, `_fetch_bars/_fundamentals_for_universe`, `_build_realized_vol_60d_map`, `_build_context_extras`) into `pipeline/advisory_inputs.py`.
- `main.py` re-exports the names so `patch("main.X")` keeps working. `run_once` already binds the functions from module globals at call time (990-998).
- Add a fixture-backed golden of `main.run_once()` recommendations and the queue.
- Gate: that golden is identical before and after the move.

**5.1: One universe builder**
- `AsyncDataFetchStep` calls the shared `build_universe(snapshot)`, which brings the held-in-fallback rule and closed-position retention. The `universe_funnel` diagnostics stay.
- Gate: the diff is limited to the added retention symbols, plus rank and score shifts caused by the larger cross-section. List every symbol that moved.
- Update `docs/known_issues/daemon_universe_watchlist_divergence.md`.

**5.2: Advisory step in the daemon, shadow mode**
- Split the advisory block (`production_steps.py:2624-2735`) out of the async `BrokerExecutionStep` into a new **sync** `AdvisoryOverlayStep`. Being sync puts it under the step timeout. It fills `ctx.recommendations`, `ctx.account_snapshot` and the context extras from 5.0, and writes the same `Advisory_*` columns.
- Add an `AgenticQueueStep` behind a new setting `DAEMON_AGENTIC_QUEUE_MODE=off|shadow|primary`, default `off`.
  - In shadow mode it writes to `OUTPUT_DIR/shadow/` only. It skips the write when `data_is_synthetic` is set or the cycle was stopped.
- Gates:
  - (i) frozen inputs, reuse **off**, same universe and macro: daemon output equals `main.py` output byte-for-byte;
  - (ii) the same run with reuse **on**, the intended diff shown to you;
  - (iii) at least 5 trading days of live shadow, with a diff script comparing each 08:45 queue to the nearest daemon shadow queue after it.

**5.3: Switch to primary**
- Set the mode to `primary`. The daemon writes the real `advisory.json` and `execution_queue.json`. The mode is captured at step start and passed explicitly to `compose_and_emit(mode=…)`.
- Port the watch engine and a summary push into the daemon.
- Optionally produce `daily_report.html` (decision 5) **without** `write_state_snapshot`.
- Add `garch_vol` and `suggested_exit_pct` to `_write_state_snapshot`.
- `main.py`'s `_run_cycle` skips the queue, watch engine and report when the mode is `primary`, and logs why. That prevents two writers.
- Runbook step for you, not code: `launchctl unload` the daily-advisory job and install `com.investyo.stack`.
- Update the `robinhood-execution` skill to trigger `POST /run` via `shared/daemon_client.trigger_run` and poll it.
- Gate: repeat the frozen diff with the mode set to primary; watch alerts for the same inputs are identical to `main.py`'s (`watch_state.json` diff).

**5.4: Switch the callers**
- `launch.command`: point it at the daemon or delete it.
- Drop the daily-advisory job: the plist and `install_schedule.command`.
- `Makefile` `_live_run` and `verify.command`: run one cycle with `asyncio.run(main_orchestrator._main_body(...))`, then summarize from the snapshot.
- `orchestrator_runner`:
  - delete `launch_advisory_main` and `launch_scheduled_advisory`;
  - point `launch_symbol_retry` at a daemon cycle, or retire the endpoint;
  - fix the `--refresh-account` passthrough (353-357);
  - remove the `HIGH_STAKES` `main.py` entry.
- `api/_jobs.py`: map `JobType.ADVISORY` to an orchestrator trigger. Update the Console button (`webapp/src/screens/Console.tsx`).
- Regenerate the command manifest (`cli_introspect/targets.py:27`) and the shell completions; update `webapp/src/commandParse.ts`.
- MCP `generate_html_report`: trigger a daemon cycle and read the report.
- Change every "`python3 main.py`" hint listed in section 2.
- Gate: `grep -rn "main\.py"` outside `legacy/`, `docs/` and `.claude/` returns only intended hits.

**5.5: Archive `main.py` to `legacy/`**
- Move `main.py`, `pipeline/steps.py`, and the advisory snapshot writer (keep `_safe_float_or_none` and the `_rating_*` helpers in a small module).
- Move `html_publisher` unless decision 5 keeps it.
- Move `engine/trade_signals.py` and the agent parts of `engine/advisory_agent.py` if decision 4 says archive; `is_automatic_run_gated` moves to `shared/`.
- Move or rewrite the tests:
  - xsec parity becomes a two-way check;
  - the entry-point lists in `test_env_loading` and `test_cnn_lstm_import_order` drop `main.py`;
  - the watch-alert wiring test targets the new step;
  - the parity test becomes a single-writer schema pin;
  - the retention tests target the daemon's builder.
- Update Gravity and add a `legacy/README.md` entry.
- Gate: the step-4 style check that nothing active imports the archived modules (AST import graph), plus `make ci` green.

**5.6: Retire `ORCHESTRATOR_DAEMON_ENABLED`**
- First remove the readers:
  - `launch_webapp.command:497-512` always starts the daemon;
  - the fast path in `orchestrator_runner.py:323` becomes unconditional, keeping the subprocess fallback;
  - `api/pilots_api.py:4081`, `shared/env_io.py:192`.
- Then remove the field (`settings.py:2602`), the census files, `.env.example:661` and the webapp mock entries, 4f-style.
- The live `.env` value is already `true`, so nothing changes at runtime.

## 5. Traps

- **Silent breakage if `main.py` disappears before 5.3/5.4:**
  - The launchd job fails into `scheduled_advisory.err` with no alert. The queue just goes stale and the watch alerts stop.
  - The Console "Advisory Pipeline" job, the Commands `--refresh-account` entry, `/dead-letter/retry`, MCP `generate_html_report` and `make verify` all fail, visible only in logs.
  - The skill keeps telling you to run `python3 main.py`.
- **Two writers during the transition.** `state_snapshot.json` and `history/` currently alternate between writers with different `action` vocabulary. `pilots/scoring` diffs consecutive history snapshots, so this can show spurious trade events.
  - `html_publisher.write_html_report` writes the snapshot internally (`html_publisher.py:114`); don't port it as-is.
  - `compose_and_emit` leaves the old queue in place when nothing is composable (`compose.py:435-437`).
- **Robinhood login.**
  - `start_login` has no single-flight guard (`data/robinhood_login.py:232`). A daemon auto-refresh and a webapp `/brokerage/refresh` can send two approval prompts.
  - A live login can block `AsyncDataFetchStep` for up to `RH_LOGIN_DEADLINE_SECONDS=180`. That step is async, so it has no timeout.
  - After `POST /brokerage/refresh` no cycle runs. Chain a `POST /run`.
- **Runtime-flag hot reload.** The daemon applies `runtime_flags.json` to the shared settings object between cycles (`daemon_runtime.py:1071-1072, 1099-1100`) while a cycle thread may be running. `ROBINHOOD_EXECUTION_MODE` and `ADVISORY_ONLY` are store-writable after confirmation. Capture the mode once per step.
- **Crash scope.** The daemon hosts the Control and Pilots APIs (`orchestrator_daemon.py:252-300`). Python exceptions are contained (`daemon_runtime.py:524-532`), but a native crash (the known lightgbm/libomp and TensorFlow issues) takes the webapp and the queue down together. Nothing restarts the daemon unless `com.investyo.stack` (KeepAlive) is installed. Keep the new steps pure Python and wrapped in try/except.
- **Cycle-time budget.**
  - `PIPELINE_STEP_TIMEOUT_SECONDS=1800` applies only to sync steps (`pipeline/runner.py:91-106`).
  - A timed-out `to_thread` step keeps running and can write files after the cycle is marked failed, while the next cycle may already be allowed to start. Write the queue from a short step and have it check that it still belongs to the current run.
  - Expected cost: about 3 min today plus 1–3 min for the pre-fetch, within the 3600 s interval.
- **Paper broker execution is separate.** `ADVISORY_ONLY=false`, `BROKER_BACKEND=fmp_paper` and the Alpaca keys mean `BrokerExecutionStep` places **paper** orders from the strategy Kelly targets. Keep the synthetic-data gate first (`production_steps.py:2750-2757`), and keep that path separate from the Robinhood queue.
- **Symbol ratings when `main.py` goes (added 2026-10-05; resolved the same day).** Resolved: the daemon now records one rating cycle per trading day (`pipeline/production_steps.py::_daemon_should_record_ratings`: market open AND no cycle yet today ET), so archiving `main.py` needs no extra step for ratings. Original note: Only `main.py`'s advisory path (`pipeline/steps.py`) writes `symbol_rating_events`. The daemon's writer is deliberately gated off by `pipeline/production_steps.py::_DAEMON_RECORDS_SYMBOL_RATINGS = False`: `SYMBOL_RATING_AUTO_DROP_ENABLED` counts consecutive BAD *cycles*, and an hourly daemon would drop symbols within hours. Before archiving `main.py` (5.5), give the daemon writer a once-per-trading-day cadence and flip the gate, or auto-drop silently freezes on stale ratings. See `docs/known_issues/daemon_strategy_score_always_nan.md`.
- **The TTL skip.** An interval cycle returns `CYCLE_SKIPPED` without a queue refresh when data is younger than the TTL. The skill's staleness check depends on decision 3.

## 6. Docs to update, and verification per PR

**Docs**
- `CLAUDE.md` / `AGENTS.md` (28 mentions each, including the universe paragraph at 191 and a new step-5 bullet).
- `docs/RUNBOOK.md` (23 mentions; the execution bridge section around 1193).
- `docs/architecture/{orchestration-entrypoints,execution,execution-boundary,data-layer}.md`, `docs/HOW_TO_GUIDE.md`, `README.md`, `docs/FEATURE_TIER_HISTORY.md` (history note only).
- `docs/known_issues/daemon_universe_watchlist_divergence.md`.
- `legacy/README.md`, the skills and commands listed in section 2, and `.env.example:535-661`.

**Verification for every PR**
- `make ci` (offline suite), the ruff genuine-bug lint, and the PR's equivalence harness output attached to the PR.
- 5.3 and 5.4 also need a live smoke test:
  - start the daemon with `launch_webapp.command`;
  - `POST /run`, then confirm `execution_queue.json`'s `generated_at`, `mode=live` and `n_placeable` are consistent with the $25 cap;
  - run `/rh-execute` in review;
  - a `/verify-webapp` pass on the Agentic, Console and Commands screens.
- 5.5 also needs a Gravity run and the AST import-graph check.

### Critical files for implementation
- /Users/kevinlee/Stockpy-live/main.py
- /Users/kevinlee/Stockpy-live/pipeline/production_steps.py
- /Users/kevinlee/Stockpy-live/execution/compose.py
- /Users/kevinlee/Stockpy-live/engine/advisory.py
- /Users/kevinlee/Stockpy-live/desktop/daemon_runtime.py