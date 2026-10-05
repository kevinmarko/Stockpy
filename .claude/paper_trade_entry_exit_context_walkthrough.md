# Paper trade entry/exit context: walkthrough

Branch `paper-trade-entry-exit-context`. Plan: `.claude/paper_trade_entry_exit_context_implementation_plan.md`
(approved with: conviction column None for pipeline trades, no backfill, duplicate `target_qty` cleanup out of scope).

## Why
The feature freeze ends on the pipeline's own closed paper trades, and those trades could not explain themselves.
Read-only live DB, 2026-10-05:
- 0 `paper_entry_snapshots` rows for `main_pipeline`.
- 8 open pipeline positions with no snapshot.
- The one closed trade (SPY) says only `close_reason='flatten'`.

## What changed
- **`execution/trade_context.py`** (new, stdlib only):
  - `build_entry_context` / `build_exit_context` read the decision row. NaN, inf, non-numeric or placeholder values
    (e.g. `Forecast_30 <= 0`) become None. So does the advisory `0.0` blank-fill when `Advisory_Action` is empty.
  - `exit_close_reason` maps EXIT_SIGNALS to `signal_sell|signal_trim|signal_risk_reduce|signal_avoid`.
  - `apply_fill_kwargs` maps a context to extra `apply_fill` kwargs.
  - JSON is strict and size-capped (8 KB entry / 4 KB exit; large keys are dropped first, with `context_status="truncated"`).
  - Snapshot `conviction` is always None for the pipeline. The advisory engine's same-cycle value goes only into
    `key_indicators_json.advisory_conviction_same_cycle`.
- **`execution/broker_base.py`**: `OrderIntent.decision_context: Optional[dict] = None`, as the last field.
- **`main_orchestrator._execute_broker_orders`**:
  - After each `OrderIntent(...)` is built exactly as before, it sets `intent.decision_context`.
  - `_safe_decision_context` imports and runs the builder; any error, including an import error, gives None plus
    a warning. The order is unaffected.
  - A `used_probe` local records whether the paper probe sized the BUY. Probe logic is unchanged.
- **`execution/fmp_paper_broker.py`**: `apply_fill(..., **context_kwargs)`. No context means `{}`, which is the same
  kwarg set as before (pinned by a test). A mapping failure is caught and the order still fills.
- **`data/paper_account_store.py`**:
  - `apply_fill` gains keyword-only `close_reason` and `exit_context_json`, used at both equity close sites.
    `_normalize_close_reason` enforces `[a-z][a-z0-9_]{0,19}`; anything else falls back to `flatten`.
  - New nullable column `paper_closed_trades.exit_context_json`, with a migration.
  - `get_full_closed_trades` returns it.
  - Multi-leg, roll and expiry paths are unchanged.
- **Readers are safe before migration** (added after rebasing onto #1106). A readonly store never migrates, and
  #1106's `feature_freeze_status` tests hit `no such column: paper_closed_trades.exit_context_json`. Readers now go
  through `PaperAccountStore.query_closed_trades()` / `closed_trade_exit_context()`: on an unmigrated DB the
  column is deferred and reads as None. These are used by `get_full_closed_trades`, the bridge-metrics last-failure
  query and the composer. Checked against a scratch copy of the live DB, which lacks the column: 2 closed trades
  read, `exit_context_json=None`, bridge status healthy.
- **`database_setup.py`**: the same column in `migrate_paper_closed_trades_schema`.
- **Readers**:
  - The composer adds `exit_context` and `exit_context_status`.
  - The narrative appends `Exit trigger: <SIGNAL> signal.` only for `signal_*` codes, via a fixed lookup.
  - Webapp `types.ts`/`mock.ts`: optional fields, and mock fixture 104 is now a pipeline-style exit. No UI change.
- **Docs**:
  - `docs/architecture/execution.md`, `data-layer.md`, `orchestration-entrypoints.md`, `observability-and-apis.md`, `testing.md`.
  - New `docs/known_issues/paper_pipeline_trades_missing_entry_exit_context.md`, with a row in the README.
  - A close_reason vocabulary cross-link in `paper_trade_strategy_id_vocabulary.md`.

## What did not change
- Sizing, probe, risk gate, kill switch, priority queue and `make_client_order_id`. A grep-based test checks that
  `order_manager.py`, `risk_gate.py`, `kill_switch.py` and `priority_queue.py` never mention `decision_context`.
- The `BrokerBase.submit_order` signature, and every other `OrderIntent` producer (MCP servers, flatten proposal,
  queue builder, Quick Trade).
- `_create_entry_snapshot`'s `has_context` rule. A pipeline fill without context still writes no snapshot.

## Out of scope / follow-ups
- **No backfill.** The 8 open positions and the SPY trade stay `not captured` for entry context, because
  reconstructing it is forbidden. Those positions still get a real `close_reason` when they close.
- `OrderIntent` declares `target_qty` twice (around lines 76 and 93 of `execution/broker_base.py`). It is harmless
  (the dataclass keeps one field) and was left alone as out of scope.
- Rendering `exit_context` in `RetrospectiveDetailModal`.
- The change takes effect only after a daemon restart.

## Verification
All runs used `INVESTYO_RUNTIME_FLAGS_PATH` pointing at a scratch file in the worktree, so nothing wrote the live
`runtime_flags.json`. All tests use tmp_path or in-memory SQLite. The live DB was only ever opened `mode=ro`.

**Targeted tests: 570 passed.** These files:
- `tests/test_paper_trade_entry_exit_context.py` (69 tests) and `test_execute_broker_orders.py` (38 tests, including
  the invariance test with the priority queue on and off, and the context-content test).
- `test_fmp_paper_broker.py`, `test_paper_account_store.py`, `test_order_manager_idempotency.py`,
  `test_order_manager_rate_limit.py`.
- The `test_retrospective_*` files (composer, narrative, adversarial m1/m6_2, challenger m1/m6, schema_and_bridge,
  e2e) plus `test_pilots_retrospective_api.py` and `test_challenger_m4_adversarial.py`.
- `test_pilots_paper_broker.py`, `test_paper_marking_and_model_feed.py`, `test_feature_freeze_status.py`,
  `test_database_setup.py`.

**Lint and webapp checks: all clean.**
- `ruff --select=F821,F822,F823,E9` on the changed files: all checks passed.
- `npm run --prefix webapp typecheck`: clean.
- vitest for `RetrospectiveM4Adversarial` + `PaperBroker`: 23 passed.

**`make ci`: 11700 passed, 2 failed, 24 skipped.** Neither failure comes from this change:
- `tests/test_runtime_flags.py::TestPathAnchoring::test_explicit_path_beats_env_var_beats_default` fails because the
  required `INVESTYO_RUNTIME_FLAGS_PATH` export changes `store_path()`. That test's first assert expects the variable
  to be unset. It passes when run alone without the export (1 passed); it only reads a path.
- `tests/test_main_orchestrator.py::TestFetchAllDataAsyncDeadLetter::test_macro_fetch_hang_isolated_dict_fallback_within_bounded_time`
  is a timing-sensitive data-fetch test that got an empty `tech_raw` under xdist load. It passed in the first full
  run, and the whole file passes alone (47 passed). This change does not touch the data-fetch path.

**Settings census artifacts were stale, now regenerated.** The first `make ci` run also failed the
settings-census / settings-liveness freshness checks. The new module raised the scanned file count from 375 to 376.
`docs/settings_field_census.{json,md}` and `docs/settings_liveness.json` were regenerated with their `--write`
scripts, and those checks pass on the second run.

### After rebase onto origin/main (39e19533, includes #1106; #1107 not yet on main)
Added a mock parity fix (`exit_context_json: null` on Quick Trade closes) and the readonly-reader fix above.

**Targeted tests: 589 passed.** Same files as above, plus `test_pilots_strategy_report_card.py`.

**Lint and typecheck: clean.**
- `ruff --select=F821,F822,F823,E9`: all checks passed.
- `npm run --prefix webapp typecheck`: clean.

**`make ci` (with `INVESTYO_RUNTIME_FLAGS_PATH` exported, since #1107 is not on main): 11716 passed, 1 failed, 24 skipped.**
The one failure is `test_runtime_flags.py::TestPathAnchoring::test_explicit_path_beats_env_var_beats_default`.
It is caused by the export, and passes alone without it.

**Settings artifacts were already current.** Regenerating them changed only the measured-at commit hash, so
nothing was committed.

### After rebase onto origin/main (6313b85d, includes #1107)
- **What conflicted:**
  - `docs/known_issues/README.md`: both sides added a row. Kept both.
  - `docs/settings_field_census.{json,md}`: took main's copy, then regenerated with `--write`.
  - `docs/settings_liveness.json` did not conflict but was regenerated anyway.
  - `conftest.py` did not conflict, since this branch does not touch it.
- **Targeted tests: 646 passed.** Same files as before, plus the census and liveness tests.
- **Lint and typecheck: clean.**
  - `ruff --select=F821,F822,F823,E9`: all checks passed.
  - `npm run --prefix webapp typecheck`: clean.
- **`make ci` with `INVESTYO_RUNTIME_FLAGS_PATH` unset: 11730 passed, 24 skipped, 0 failed.**
- **Live runtime-flags store untouched:** `~/.stockpy_local/output/runtime_flags.json` had the same mtime and size before and after the run.
