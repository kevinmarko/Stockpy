# Step 5.2 walkthrough: advisory overlay step + daemon shadow queue

Plan: `.claude/shrink_step5_retire_main_py_implementation_plan.md` (section 4, "5.2"; section 5 "Traps"; operator decision 2).
Branch: `step5-advisory-shadow`, cut from `origin/step5-one-universe-builder` @ `d4cd3e97` (PR 5.1, #1080, not yet merged).

No live behaviour change by default: `DAEMON_AGENTIC_QUEUE_MODE` defaults to `off`, main.py is still the only writer of the real queue, and shadow mode was **not** enabled in the live `.env`. One disclosed display change is listed under "What changes with the mode off".

## The step split

`main_orchestrator._main_body_impl`'s "full" step list is now:

```
AsyncDataFetchStep → RunPipelineStep → AdvisoryOverlayStep → AgenticQueueStep → BrokerExecutionStep → StateSnapshotStep
```

**`AdvisoryOverlayStep` (sync, `name = "execution"`, the progress label the advisory loop always used).** It is the old advisory block from the async `BrokerExecutionStep`, moved into a sync step so `AsyncPipelineRunner` runs it under `asyncio.wait_for(..., PIPELINE_STEP_TIMEOUT_SECONDS)`. It now matches main.py's `pipeline/steps.py` stages D and E:
- evaluates `ctx.symbols` in universe order (5.1 made this main.py's universe). A dashboard row whose symbol is not in `ctx.symbols` is appended and evaluated too, with a warning. This can't happen today, because the dashboard is built from `ctx.symbols`;
- reuses the snapshot `AsyncDataFetchStep` already fetched: `ctx.snapshot` is new there, and before 5.2 this block fetched the same cached snapshot a second time. When that fetch failed it substitutes main.py's empty `AccountSnapshot`;
- calls `pipeline.advisory_inputs.fetch_bars_for_universe` and `build_context_extras`, the same functions main.py calls. They are stored in `ctx.bars_dict` and `ctx.context_extras["advisory_context_extras"]`;
- captures `ADVISORY_REUSE_PIPELINE_COMPUTE` and `ADVISORY_MAX_CONCURRENCY` once, at step start. The precompute selection moved into `_select_precomputed_for_row()` unchanged. A new test checks it against `tests/test_advisory_dedup_wiring.py`'s pinned copy;
- the per-symbol loop, thread pool and ordered assembly are the same as main.py's. Failures are dead-lettered into `ctx.errors`, and the per-symbol log line matches main.py's;
- fills `ctx.recommendations` (the full `Recommendation` objects) and writes the same five `Advisory_*` columns, with the same rounding and blanks;
- never raises. If the whole loop fails, the columns stay blank and `advisory_overlay_ok` is not set.

**`BrokerExecutionStep` (async)** keeps only the paper/Alpaca path. The synthetic-data gate is still first, followed by `ADVISORY_ONLY` and the credentials check. The loop no longer lives here, and the step no longer fetches the snapshot or imports `engine.advisory`. `tests/test_production_steps_broker_gate.py` passes unchanged.

**`AgenticQueueStep` (sync, `name = "agentic_queue"`).** It captures `DAEMON_AGENTIC_QUEUE_MODE`, `ROBINHOOD_EXECUTION_MODE` and `OUTPUT_DIR` once, then calls `write_queue(ctx, mode=, execution_mode=, output_dir=)`. The whole step is wrapped in try/except, so it never raises into the cycle.
- `off`: returns immediately and creates nothing.
- `shadow`: runs `write_advisory_source(ctx.recommendations, output_dir=OUTPUT_DIR/shadow, now=)`, then `compose_and_emit(ctx.snapshot, output_dir=OUTPUT_DIR/shadow, mode=<captured ROBINHOOD_EXECUTION_MODE>, now=, macro_dto=ctx.macro_dto, side_effects=False)`. These are the same calls `_run_cycle` makes. One `now` is used for both files. A timestamped copy of each file goes to `shadow/history/`, keeping the newest 480 files (about 10 days of hourly cycles). A copy is taken only when that cycle actually wrote the file.
- `primary`: logs a WARNING that primary lands in 5.3, then does exactly what `shadow` does. It never writes the real queue.
- It skips and logs why when the cycle stopped, `data_is_synthetic` is set, the advisory step didn't finish (`advisory_overlay_ok` is missing), or there are no recommendations.
- Why a separate short step: if `AdvisoryOverlayStep` times out, the runner raises and this step never runs for that cycle. A still-running advisory thread therefore cannot write a queue after its cycle has been marked failed (the plan's cycle-time trap).

**Keeping the shadow write off the real queue:**
1. `shadow_output_dir()` is always the strict subdirectory `OUTPUT_DIR/shadow`.
2. `shadow_collision_reason()` compares four paths with their real counterparts after resolving links, plus `os.path.samefile` for hard links: the output dir, `queue_sources/`, `advisory.json` and `execution_queue.json`. On a match it refuses with an ERROR log. Tests cover `shadow` symlinked to `OUTPUT_DIR`, `shadow/queue_sources` symlinked to the real one, and a hard-linked queue file, in both shadow and primary modes.
3. `side_effects=False` is a new kwarg, default `True`, on `compose_and_emit`, `queue_builder.emit_execution_queue` and `build_execution_queue`, plus a `PreTradeRiskGate.side_effects` attribute. With it off, the shadow run sends no ntfy push, writes no `execution_queue_notified.json`, makes no risk-gate `send_alert` calls (halt, portfolio heat, correlation), and appends nothing to `OUTPUT_DIR/risk_gate_blocks.jsonl`.
   - Without it, shadow mode would have pushed "Trades Ready to Place" notifications (each run keeps its own notified-state file) and inflated the dashboard's risk-gate block count every hour.
   - The gate verdicts and the queue payload are identical either way.
   - `build_execution_queue` sets the attribute after construction, so the existing no-arg test doubles for `PreTradeRiskGate` keep working.

## Settings plumbing and classification

`DAEMON_AGENTIC_QUEUE_MODE: str = "off"`. A validator collapses anything outside `off|shadow|primary` to `off`, like `ROBINHOOD_EXECUTION_MODE`'s validator.
- **`settings_keysets.SAFETY_CRITICAL_KEY_REASONS` (so it is a `DANGEROUS_KEYS` member).** The field decides which process writes the queue that the robinhood-execution skill places orders from, and `primary` (in 5.3) hands that queue to the daemon. That is the same risk class as `ROBINHOOD_EXECUTION_MODE`, so a write needs typed confirmation. Being in `DANGEROUS_KEYS` also puts it on the Feature Flags screen automatically, so I added:
  - the enum spec to `api/pilots_api.py::_FEATURE_FLAGS_NON_BOOL_SPECS`;
  - `webapp/src/api/mock.ts` parity entries in `MOCK_DANGEROUS_KEYS` and `FEATURE_FLAGS_TUNABLE_DEFS`.
- `shared/env_io.py` `ALLOWED_KEYS` (it is not a secret), next to `ROBINHOOD_EXECUTION_MODE`.
- `pilots/settings_domains.py`: the "Execution/Brokers" domain.
- `.env.example` documents all three modes.
- `shared/help_content.py` and `webapp/src/help/helpContent.ts`: not touched. Neither covers per-setting text for the execution-mode settings.
- Regenerated `docs/settings_field_census.{json,md}` and `docs/settings_liveness.json`.

## Gate (i): frozen inputs, reuse off, same universe and macro

The test is `tests/test_daemon_advisory_shadow_equivalence.py::TestGateIDaemonShadowEqualsMainPy`.

It reuses the run_once golden's frozen inputs, refactored into `_install_frozen_inputs()` in `tests/test_run_once_advisory_golden.py`:
- seeded bars and a fake provider;
- a fake forecasting engine;
- fake macro engines;
- a trade-history store holding one closed MSFT trade;
- network blocked, `ADVISORY_MAX_CONCURRENCY=1`, a fixed `now`.

In order, the test:
1. runs `main.run_once()` and `_run_cycle`'s queue block into the real tmp `OUTPUT_DIR`;
2. runs the daemon's real `AsyncDataFetchStep`, stubbing only its account fetch, bulk data fetch and freshness marker. This produces the universe and `ctx.snapshot`;
3. sets `ctx.macro_dto` to main.py's macro DTO ("same macro"), and sets `ctx.dashboard_df` to the universe symbols in place of `RunPipelineStep`, which would fit the full forecaster;
4. runs `AdvisoryOverlayStep` and `AgenticQueueStep` through the real `AsyncPipelineRunner`, so they take the `to_thread` + timeout path.

**Result: PASS.**
- `ctx.symbols` equals main.py's universe: AAPL, INTC, JNJ, KO, MSFT, NVDA, XOM.
- The per-symbol recommendation bytes are identical to main.py's. This covers action, conviction, `suggested_position_pct`, `suggested_exit_pct`, rationale and the full `key_indicators` dict. The comparison is exact, including the GARCH and Kelly keys, because both sides ran in one process. Against the committed cross-platform golden, the existing rel 1e-2 tolerance on those keys applies.
- `shadow/queue_sources/advisory.json` and `shadow/execution_queue.json` equal main.py's real files byte for byte, and also equal the committed `advisory_source.golden` / `execution_queue.golden`. The queue has 3 intents: AAPL, NVDA and XOM BUY.
- The `Advisory_*` columns carry the same recommendations.
- The real queue files, `execution_queue_notified.json` and `risk_gate_blocks.jsonl` are unchanged by the shadow run. No push was sent; main.py's compose did push, which checks the fixture. There is no sidecar or block log in `shadow/`.
- `scripts/compare_shadow_queue.py`'s `compare()` on those files reports 0 differences and picks the `history/` copy.

**A harness bug I hit and fixed.** The golden used a `sqlite:///:memory:` TransactionsStore. SQLAlchemy gives each thread its own in-memory database, so the daemon step saw an empty trade history on its `to_thread` worker. The symptom was every `kelly_*` at 0 and NaN excursion for MSFT. It is now a tmp file database, the run_once golden still passes unchanged, and this is only a test artifact: production uses a file or Postgres URL.

**Mutation checks** (temporary edits, each reverted):

| Temporary edit | Result |
|---|---|
| Pass the old thin extras (xsec + multifactor only) | gate (i) fails |
| `shadow_output_dir()` returns `OUTPUT_DIR` | 7 tests fail, including gate (i) and the clobber tests |
| `side_effects=True` in the shadow compose | gate (i) and the no-push test fail |

## Gate (ii): the same frozen run with reuse on

The test is `tests/test_daemon_advisory_reuse_diff.py`. The pipeline values that get reused come from the daemon's real `TrendVolatilityStep` and `ForecastingStep` code, run on the frozen full history (320 bars; about 2 years live). The advisory refit uses its own 252-bar window.

**Variant `fake`** (in the normal suite, asserted). Both sides use the golden's fake forecaster, which isolates the GARCH source:

| symbol | action off→on | conviction | pos % | garch_vol off→on |
|---|---|---|---|---|
| AAPL | BUY→BUY | 0.85→0.85 | 0.05→0.05 | 0.188387→0.189181 |
| INTC | SELL→SELL | 0.65→0.65 | 0→0 | 0.255891→0.257488 |
| JNJ | BUY→BUY | 0.70→0.70 | 0.05→0.05 | 0.106058→0.104777 |
| KO | SELL→SELL | 0.80→0.80 | 0→0 | 0.123859→0.125380 |
| MSFT | BUY→BUY | 0.70→0.70 | 0.05→0.05 | 0.185178→0.160233 |
| NVDA | BUY→BUY | 0.85→0.85 | 0.05→0.05 | 0.315305→0.325856 |
| XOM | BUY→BUY | 0.85→0.85 | 0.05→0.05 | 0.193986→0.193904 |

- Only `garch_vol` and the Kelly telemetry derived from it (`kelly_raw`, `kelly_target_pre_regime`, `kelly_target_post_regime`) move.
- `suggested_position_pct` doesn't move, because the advisory's 5% single-name cap binds before Kelly on this fixture.
- The queue is unchanged: AAPL, NVDA and XOM BUY at 0.85, $5,000 each.

**Variant `real`** (`-m slow`; reproduce with `pytest -m slow tests/test_daemon_advisory_reuse_diff.py -s -p no:randomly`). Both sides use the real `ForecastingEngine`. It gave the same numbers on 2 runs:

| symbol | action off→on | conviction | pos % | score | fcst 30d % | why |
|---|---|---|---|---|---|---|
| AAPL | HOLD→HOLD | 0.70→0.70 | 0→0 | 70→55 | +0.39→−0.31 | forecast flips sign; the Case C hold holds either way |
| INTC | SELL→SELL | 0.65 | 0 | 5→5 | −7.96→−10.76 | |
| **JNJ** | **HOLD→BUY** | **0.55→0.70** | **0→0.05** | **45→60** | **−0.10→+0.24** | forecast flips to positive, `forecast_alignment` lifts the score, and the raw signal goes HOLD→BUY |
| KO | HOLD→HOLD | 0.55 | 0 | 50→50 | −0.96→−0.57 | |
| MSFT | BUY→BUY | 0.70 | 0.05 | 55→55 | −0.24→−1.49 | |
| NVDA | BUY→BUY | 0.85 | 0.05 | 55→55 | +16.32→+15.54 | |
| XOM | BUY→BUY | 0.85 | 0.05 | 100→100 | +3.98→+4.83 | |

- Queue off: NVDA and XOM BUY at 0.85. Queue on: the same.
- JNJ's new BUY is at 0.70, below the 0.85 queue floor, so it changes the advisory source (`advisory.json` gains a JNJ BUY target) but not the queue on this fixture.
- The forecast differs because the pipeline fits on the full history with the GJR-GARCH term structure and the F2 guards, while the advisory refit uses 252 bars. That is the mechanism behind operator decision 2: which BUYs reach 0.85 depends on it.
- **Not audited:** which ensemble members, such as CNN-LSTM, actually produced output in this offline test process. The run took about 6 s.

## Gate (iii)

`scripts/compare_shadow_queue.py` is read-only.
- It uses the real `queue_sources/advisory.json` `generated_at` as the reference time, then picks the earliest shadow run at or after it: `shadow/history/` first, the live shadow files as a fallback, and optionally `--max-lag-hours`.
- Per symbol, it prints the advisory target (action, conviction, `suggested_position_pct`) and the queue intent (side, conviction, `target_notional`, `gate_allowed`, `allow_place`), and marks every difference and one-sided symbol.
- It prints every file's `generated_at`, because `compose_and_emit` can leave an older queue in place.
- Flags: `--json`, `--output-dir`. Exit codes: 0 identical, 2 differences, 1 nothing to compare.
- Tests: `tests/test_compare_shadow_queue.py`. The operator runs gate iii live for 5 trading days with `DAEMON_AGENTIC_QUEUE_MODE=shadow`. Not enabled here.

## What changes with the mode off (disclosed)

- **Daemon `Advisory_*` display columns can move.** The daemon's advisory now gets main.py's full context extras (bars, fundamentals, news sentiment, CoVaR, excursion, and xsec/multifactor from main.py's formula over its own bars) instead of only the pipeline's xsec ranks and multifactor scores. These columns feed `state_snapshot.json`'s advisory fields and the dashboard report. They drive no order: the paper broker path uses the strategy Kelly targets, not `Advisory_*`.
- **Extra cost per cycle.** One universe bars pre-fetch through `HistoricalStore`, a fundamentals pre-fetch, and a second `global_registry.run_pre_compute()`, which includes `news_catalyst`'s per-symbol FMP news fetch. The per-symbol bars fetch inside `evaluate()` is replaced by the pre-fetch. Universe symbols the pipeline dropped for missing data are now evaluated too, as main.py does.
- **The advisory loop now has a timeout.** It is bounded by `PIPELINE_STEP_TIMEOUT_SECONDS` (default 1800 s). A loop that slow now fails the cycle instead of hanging it.
- **Progress bar.** The `agentic_queue` and broker steps each reset the per-symbol counter. The bar already dropped back from 100% at the `snapshot` stage before this change.

## Verification

- `tests/test_daemon_advisory_shadow_equivalence.py`: 37 passed.
- `tests/test_compare_shadow_queue.py`: 10 passed.
- `tests/test_daemon_advisory_reuse_diff.py`: fast 1 passed, slow 1 passed.
- `tests/test_run_once_advisory_golden.py`: 2 passed, goldens unchanged.
- Full offline suite, run as `LOCAL_DATA_ROOT=<scratch> NO_VENV_REEXEC=1 pytest -m "not network and not slow" -n auto --dist loadgroup -q -p no:randomly`: **11640 passed, 18 skipped, 0 failed**.
- `ruff check . --select=F821,F822,F823,E9`: clean.
- Webapp: `npm run typecheck` is clean, and `FeatureFlagsScreen.test.tsx` + `SettingsReference.test.tsx` pass 9/9. They ran against the main checkout's `node_modules` via a temporary symlink, since removed. No browser check: the only UI change is a mock-parity entry on the existing Feature Flags screen.
- `main.py` is untouched, so no line numbers shifted. The new sync code makes no new `subprocess`/`requests` calls (`tests/test_no_missing_call_timeouts.py` passes).

## Not done / follow-ups

- No live run. Shadow mode is not enabled anywhere; the operator enables it for gate (iii).
- `primary` (the real queue from the daemon) and the watch engine, summary push and snapshot fields are 5.3.
- The daemon's macro DTO still comes from its own `MacroStep`. The VIX default and HMM SPY source differ from main.py (plan section 1, "Macro"). Gate (i) held macro equal by construction, so gate (iii) is where any macro-driven difference will show up.
