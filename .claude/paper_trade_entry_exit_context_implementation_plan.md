# Paper trade entry/exit context: implementation plan

> **Approved 2026-10-05 with these decisions (they override the options below):**
> (Q1) the snapshot `conviction` column stays `None` for pipeline trades; the advisory engine's
> same-cycle conviction is recorded only inside `key_indicators_json` as
> `advisory_conviction_same_cycle`. (Q2) No backfill. (Q3) #1105 (real Score) is merged, so
> `signal_score` is real from the first cycle. The duplicate `OrderIntent.target_qty` cleanup
> (section 4.1) is **out of scope** and not done.

Branch (when approved): `paper-trade-entry-exit-context` off `origin/main`. Tier: **Everything else**
(touches `execution/` and the pipeline order path), so this plan needs review before any code.
Freeze status: this is allowed work. It measures existing behavior and changes no trading decision.

## 1. Problem, measured (live DB read with `file:...?mode=ro`, 2026-10-05)

| Fact | Value |
|---|---|
| `paper_entry_snapshots` rows | 4, all `strategy_id='Manual Trade'`, `provenance='manual'`, `provenance_tag='manual:ticket'`, every context field NULL |
| `paper_entry_snapshots` rows for `main_pipeline` | **0** |
| Open `main_pipeline` positions | 8 (ARR, SDIV, AGNC, ET, DX, PBF, PK, DIV), all `entry_snapshot_id = NULL` |
| Closed `main_pipeline` trades | 1: SPY, 1.301487 sh, 767.69 -> 764.39, PnL -4.87, held 0.958 d, `close_reason='flatten'`, `entry_snapshot_id=NULL`, bridged |
| Distinct `close_reason` values in the table | `flatten` only (code also writes `roll` and `expiry_settlement`) |

### Root cause (A): entry context
- `execution/fmp_paper_broker.py::submit_order` (~line 326) calls `store.apply_fill(...)` with only
  `client_order_id/symbol/side/qty/fill_price/commission_and_fees/target_qty/status/strategy_id`.
- `PaperAccountStore._create_entry_snapshot` (~line 970) writes a snapshot only when `has_context`
  is true. That means a context kwarg was passed, or `strategy_id == "Manual Trade"`, or a non-untagged
  strategy **with a `pilot_id`**. `main_pipeline` has no pilot_id and passes no context, so the
  function returns `None` and no snapshot is written. That is the anti-fabrication rule working
  as designed: no context was supplied, so nothing was recorded.
- **"How Quick Trades populate snapshots"**: they don't supply context. `pilots/paper_equity_order.py`
  (~line 105) passes `provenance=` (usually None) through `apply_fill`. The snapshot exists only
  because of the `strategy_id == "Manual Trade"` rule, and `_create_entry_snapshot` deliberately
  nulls conviction/forecast/score for `manual`. So the mechanism to reuse is the **same
  `apply_fill` keyword surface** (`provenance`, `provenance_tag`, `conviction`, `macro_regime`,
  `signal_score`, `raw_forecast`, `forecast_model`, `key_indicators_json`, `decision_rationale`).
  It already exists, is tested (`tests/test_retrospective_*`) and already feeds the composer. No new
  snapshot table or writer is needed.

### Root cause (B): close reason
- `apply_fill` hard-codes `"flatten"` at both equity close sites (`_record_closed_trade(..., "flatten", ...)`
  ~lines 1245 and 1326). The order path has no way to say why the position closed.
- In `main_orchestrator.py::_execute_broker_orders`, the exit branch fires only on
  `signal in EXIT_SIGNALS and symbol in open_symbols`. The trigger is the row's `Action Signal`.
  Dual Momentum safe-asset only zeroes `Kelly Target` (`pipeline/production_steps.py` ~line 2485);
  it does not change `Action Signal`. So the SPY exit was some row whose `Action Signal` was in
  `{SELL, TRIM, RISK REDUCE, AVOID}`. Recording the real signal plus the row context at exit
  answers this question for every future trade, which we cannot do for SPY now.

## 2. Design principles (non-negotiable)

1. **No trading-decision change.** `qty`, `target_qty`, `side`, `priority`, `order_type`, `strategy_id`,
   probe logic, skip logic, risk-gate and kill-switch inputs are computed exactly as today. Context is
   built **after** the intent's trading fields are final and is attached as an inert payload.
2. **Idempotency untouched.** `make_client_order_id(strategy_id, symbol, side, qty, timestamp)` keeps
   its signature and inputs. The context never enters it, and `OrderManager` never reads it.
3. **BrokerBase contract unchanged for other callers.** `submit_order(intent)` keeps its signature. The new
   `OrderIntent` field is optional, defaults to `None`, and goes **last**, so positional
   construction elsewhere is unaffected. With `None`, `FMPPaperBroker` calls `apply_fill` with exactly
   today's kwargs. `investyo_mcp_server.py`, `broker_live_execution_mcp.py`, `execution/flatten_proposal.py`,
   `execution/queue_builder.py` and the Quick Trade path do not change.
4. **CONSTRAINT #4.** Every value is read from the row the decision actually used. Missing, NaN, inf,
   non-numeric or placeholder values (e.g. `Forecast_30 <= 0`, the `0.0` blank-fill of
   `Advisory_Conviction` when `Advisory_Action` is `""`) become `None`/JSON `null`, never `0.0`.
   No backfill of past trades (see section 8).
5. **CONSTRAINT #6, telemetry direction.** This is observability, not a safety gate, so it must
   *fail open for the order*. Any exception while building or serialising context is caught. The
   order goes out with `decision_context=None` (or a minimal `{"context_status": "unavailable", "error": ...}`),
   and a WARNING is logged. A context failure can never block, delay or resize an order. The
   store-side context writes happen inside the same transaction as the fill (as for Quick
   Trades today), so they cannot leave a half-written fill. The inputs are sanitised before they
   reach the store, so an ORM-level failure from bad context is not reachable.

## 3. Data flow

```
final_df row (Action Signal, Score, Kelly Target, Kelly_Target_Pre/Post_Regime, Regime_Multiplier,
              Meta_Label_Composite, Sizing_Binding_Constraint, HMM_Regime_State,
              HMM_Risk_On_Probability, Macro Status, DualMomentum_Signal, Forecast_30,
              Forecast_30_Pct, Forecast_30_Is_Fallback, GARCH_Vol, Score_Components,
              Actionable Advice Signal, Advisory_Action/Conviction/Rationale, Price)
   │  main_orchestrator._execute_broker_orders  (trading fields computed exactly as today)
   │  then: ctx = trade_context.build_entry_context(...) / build_exit_context(...)   [try/except -> None]
   ▼
OrderIntent(..., decision_context=ctx)        # new optional, inert field
   │  OrderManager.submit_order_with_idempotency  (passes the same object through; coid from
   │  strategy_id/symbol/side/qty/bucket only; risk gate/kill switch unchanged)
   │  LeakyBucketPriorityQueue (stores the same object; unchanged)
   ▼
FMPPaperBroker.submit_order
   │  extra = trade_context.apply_fill_kwargs(intent.decision_context)   [try/except -> {}]
   ▼
PaperAccountStore.apply_fill(..., **extra)
   ├─ BUY opening  -> _create_entry_snapshot(provenance='signal_driven', provenance_tag, conviction,
   │                   macro_regime, signal_score, raw_forecast, forecast_model=None,
   │                   key_indicators_json, decision_rationale)  -> paper_positions.entry_snapshot_id
   └─ SELL closing -> _record_closed_trade(close_reason='signal_risk_reduce' | ..., exit_context_json)
                       -> paper_closed_trades.close_reason / .exit_context_json (+ snapshot.trade_id link,
                          + transactions bridge notes "Paper bridge, reason: signal_risk_reduce")
   ▼
Readers: get_full_closed_trades -> pilots/paper_broker.py, /pilots/paper-broker/trades (PaperBroker.tsx
         already renders close_reason), RetrospectiveComposer (snapshot becomes "captured", provenance
         'signal_driven' -> automated cohort in retrospective_insights), retrospective_narrative.
```

## 4. Changes, file by file

### 4.1 `execution/broker_base.py`, `OrderIntent`
- Add, as the **last** field:
  `decision_context: Optional[dict] = None`, with a comment: telemetry only, never read by sizing,
  the risk gate, the kill switch, idempotency or `OrderManager`; brokers may ignore it; today only
  `FMPPaperBroker` reads it.
- Use `Optional[dict]` rather than a new dataclass. It survives the priority queue and the tests'
  `MockBroker` with no further plumbing, and `trade_context` owns its schema (`context_schema_version`).
- Housekeeping found while reading: `target_qty` is declared **twice** in `OrderIntent` (lines ~76 and
  ~93). The dataclass keeps one field, so this is harmless, but delete the first declaration and keep
  the documented one. It is a pure cleanup with a test asserting `fields(OrderIntent)` names are unique
  and ordered as before, except for the appended field.

### 4.2 New `execution/trade_context.py` (pure, dependency-light, unit-testable)
Stdlib + `math`/`json` only. No pandas or settings import, so it is trivially testable (it accepts `row`
as any mapping with `.get`).
- `CONTEXT_SCHEMA_VERSION = 1`
- `_finite_or_none(v) -> Optional[float]`: handles numpy scalars, NaN, inf, bool-guard and str-to-float.
- `_json_safe(obj, depth)`: dict/list/scalars to JSON-safe, NaN/inf to None, numpy to python, other objects
  to `str()`, depth-limited.
- `build_entry_context(row, *, sizing_source: str, effective_weight: float, equity: float, price: float, macro_dto=None) -> dict`
  returns `{"kind": "entry", "context_schema_version": 1, ...}` with the apply_fill fields:
  - `provenance`: `"signal_driven"`.
  - `provenance_tag`: `f"main_pipeline:{sizing_source}"` (`kelly` | `probe`), ≤100 chars.
  - `signal_score`: `Score` if finite, else None. It is NaN on main until `fix-daemon-empty-score`
    (fb616d69) merges; None is the honest value until then.
  - `macro_regime`: `Macro Status` (str, ≤50), falling back to `macro_dto.market_regime` if the row
    lacks it, else None.
  - `raw_forecast`: `Forecast_30` if finite and > 0 (a price; `<= 0` is a placeholder, so None).
  - `forecast_model`: **None**. No column records the winning model today; do not invent one.
  - `conviction`: see **Open question Q1**. The default proposal is `Advisory_Conviction` only when
    `Advisory_Action` is a non-empty string and the value is finite in [0, 1], else None.
    `key_indicators.conviction_source = "advisory"` records where it came from.
  - `decision_rationale`: `Actionable Advice Signal` (the strategy engine's own reason for this
    signal), str, ≤2000 chars, else None.
  - `key_indicators_json` (≤8 KB, sorted keys): `action_signal`, `score`, `kelly_target_row`
    (the row's post-overlay Kelly Target), `effective_weight` (what was actually sized, which differs on
    probe), `sizing_source`, `kelly_target_pre_regime`, `kelly_target_post_regime`, `regime_multiplier`,
    `meta_label_composite`, `sizing_binding_constraint`, `hmm_regime_state`, `hmm_risk_on_probability`,
    `dual_momentum_signal`, `garch_vol`, `forecast_30`, `forecast_30_pct`, `forecast_30_is_fallback`
    (bool only if an actual bool), `advisory_action`, `advisory_conviction`, `advisory_rationale` (≤500),
    `score_components` (json-safe, dropped first if over budget), `price_at_decision`, `equity_at_decision`,
    `context_status` (`ok` | `partial` (some fields None) | `truncated`).
- `build_exit_context(row, *, signal: str, held_qty: float) -> dict` returns
  `{"kind": "exit", "close_reason": exit_close_reason(signal), "exit_context": {...}}`. The exit context
  has `exit_signal` (raw Action Signal), `score`, `kelly_target_row`, `dual_momentum_signal`,
  `macro_status`, `hmm_regime_state`, `hmm_risk_on_probability`, `regime_multiplier`, `meta_label_composite`,
  `actionable_advice` (≤1000), `advisory_action`, `advisory_rationale` (≤500), `price_at_decision`,
  `held_qty`, `context_schema_version`, `context_status`. It is ≤4 KB.
- `exit_close_reason(signal) -> Optional[str]`: `"signal_" + signal.lower().replace(" ", "_")` for
  members of `{"SELL","TRIM","RISK REDUCE","AVOID"}`, giving `signal_sell`, `signal_trim`,
  `signal_risk_reduce` (18 chars) and `signal_avoid`. Anything else returns None, and the store keeps `flatten`.
  All outputs fit `String(20)` and the regex `^[a-z][a-z0-9_]{0,19}$`.
- `apply_fill_kwargs(ctx) -> dict`: maps an entry ctx to the 9 snapshot kwargs and an exit ctx to
  `{"close_reason": ..., "exit_context_json": json.dumps(...)}`. `None`, a non-dict or an unknown
  `kind` gives `{}`. It never raises (internal try/except returns `{}` and logs a warning).
- `EXIT_SIGNALS` stays defined in `main_orchestrator.py` (single source). `trade_context` keeps its own
  mapping table, and a test asserts `set(mapping) == main_orchestrator.EXIT_SIGNALS` so they cannot drift.

### 4.3 `main_orchestrator.py::_execute_broker_orders`
- BUY branch: after `intent = OrderIntent(...)` is built exactly as today, add
  ```python
  intent.decision_context = _safe_context(lambda: trade_context.build_entry_context(
      row, sizing_source="probe" if used_probe else "kelly", effective_weight=kelly,
      equity=equity, price=price, macro_dto=macro_dto), symbol)
  ```
  Here `used_probe` is a new local boolean set inside the existing probe block when `kelly = w` is
  assigned. That is a pure record of what already happened; the probe logic itself is unchanged.
- EXIT branch: after the SELL `OrderIntent(...)`, set
  `intent.decision_context = _safe_context(lambda: trade_context.build_exit_context(row, signal=signal, held_qty=sell_qty), symbol)`.
- `_safe_context(fn, symbol)`: a module-level helper that runs `fn()` and returns its dict. On any exception
  it logs `telemetry.warning("trade context unavailable for %s: %s", ...)` and returns `None`.
- Attach the context **after** construction instead of passing it to the constructor. This keeps the
  diff to the trading lines at zero and makes "same qty/target_qty/priority" obvious in review.
- `main.py` does not call `_execute_broker_orders` (grep-verified), so nothing changes there.

### 4.4 `execution/fmp_paper_broker.py::submit_order`
- Before step 6:
  ```python
  try:
      context_kwargs = trade_context.apply_fill_kwargs(getattr(intent, "decision_context", None))
  except Exception:  # noqa: BLE001 -- telemetry must never block a fill
      logger.warning(...); context_kwargs = {}
  ```
  then `self.store.apply_fill(..., **context_kwargs)`. With `decision_context=None` the call is
  byte-identical to today (a test pins the exact kwarg set).
- Multi-leg path (`apply_multi_leg_fill`) is untouched; the pipeline never sends legs.
- Dry-run returns before any of this, unchanged.

### 4.5 `data/paper_account_store.py`
- `PaperClosedTrade`: add `exit_context_json = Column(Text, nullable=True)`.
- `_ensure_account_exists` migration list (~line 489): add `("exit_context_json", "TEXT")`.
- `apply_fill`: new keyword-only params `close_reason: Optional[str] = None` and
  `exit_context_json: Optional[str] = None`. At the two equity close sites (~1245 buy-to-close-short,
  ~1326 sell-against-long), pass `self._normalize_close_reason(close_reason, default="flatten")` and
  `exit_context_json`.
- New `_normalize_close_reason(value, default)`: None gives `default`; a value matching
  `^[a-z][a-z0-9_]{0,19}$` passes through; anything else gives `default` with a WARNING (never raises).
- `_record_closed_trade(..., close_reason, commission=0.0, exit_context_json=None)`: store it on the
  row. Bridge notes already interpolate `close_reason`, so they improve automatically.
- `get_full_closed_trades`: add `"exit_context_json": t.exit_context_json`.
- `apply_multi_leg_fill`, `apply_roll_fill` and expiry settlement are **unchanged** (`flatten`, `roll`,
  `expiry_settlement` stay).
- No change to `_create_entry_snapshot`'s `has_context` rule. The pipeline now passes
  `provenance="signal_driven"`, which already satisfies it. A context failure (None) gives today's
  behavior, i.e. no snapshot. The composer reports that honestly as `not_captured`.

### 4.6 `database_setup.py`
- `migrate_paper_closed_trades_schema`: add `("exit_context_json", "TEXT")` to `new_columns`, and update
  the docstring column list.

### 4.7 Readers
- `pilots/retrospective_composer.py`: `_paper_row_to_closed_trade_dict` (~line 154) adds
  `exit_context_json`. `_tx_row_to_closed_trade_dict` adds `None`. The composed record (~line 680) adds
  `"exit_context": <parsed dict or None>`, where a parse failure gives None (`exit_context_status`:
  `captured` / `not_captured` / `unparseable`). Nothing else changes. Pipeline trades now flow into the
  existing `captured` branch, with `provenance='signal_driven'` from the real snapshot, not inferred.
- `pilots/retrospective_insights.py`: **no code change.** Pipeline trades with snapshots now land in
  `automated_cohort`, and the Brier calibration uses `conviction` when present (see Q1).
- `pilots/retrospective_narrative.py`: in the Outcome clause, when `close_reason` starts with `signal_`,
  append `" Exit trigger: <SIGNAL> signal."`, with the label from `exit_context.exit_signal` (sanitised
  through the existing quote/escape helper) or else derived from the code. Any other close_reason
  produces exactly today's strings, so existing narrative tests are untouched.
- `webapp/src/api/types.ts` + `mock.ts`: add an optional `exit_context_json?: string | null` to the
  paper closed-trade type, and `exit_context?: Record<string, unknown> | null` to the retrospective
  record type. Add one mock fixture with `close_reason: "signal_risk_reduce"` and a populated
  `exit_context`, and one with `null`. **No new UI.** `PaperBroker.tsx` already renders
  `close_reason`. A rendered exit panel in `RetrospectiveDetailModal` is a follow-up (spawn as its own
  task; it is an existing-screen tweak, but keep this PR backend-only). Gate: `npm run --prefix webapp typecheck`.
- `ml/training_data.py` reads `paper_closed_trades` with `read_sql_table`. The extra column is ignored.
  `scripts/feature_freeze_status.py` and `pilots/strategy_report_card.py` are unaffected (verified by grep:
  no production code filters on `close_reason == "flatten"`).

## 5. Tests

New file `tests/test_paper_trade_entry_exit_context.py` (project-scoped name), plus extensions:

1. **Sizing/ID invariance (the key test)**, in `tests/test_execute_broker_orders.py`:
   - Build a `final_df` with one BUY row (positive Kelly), one probe-eligible zero-Kelly BUY row (probe on,
     `count_closed_trades` stubbed to 0) and one RISK REDUCE row for an open pipeline position. Run
     `_execute_broker_orders` twice with `MockBroker` capturing intents: (a) normally, (b) with
     `trade_context.build_entry_context`/`build_exit_context` monkeypatched to **raise**.
   - Assert the captured intents are equal on `(strategy_id, symbol, side, qty, target_qty, order_type,
     priority, limit_price, time_in_force, dry_run)`, the submission order is the same, and
     `client_order_id` is equal under a fixed timestamp (monkeypatch `main_orchestrator.datetime` /
     pass-through `now`). Also assert (a) carries a context, (b) carries `None`, and (b) still submitted
     every order.
   - Run the same test with `EXECUTION_PRIORITY_QUEUE_ENABLED=True`, so the context survives the queue.
2. `tests/test_order_manager_idempotency.py`: the same intent with and without `decision_context` gives
   the same coid from `submit_order_with_idempotency(timestamp=fixed)`. A second submit that differs only
   in context is **deduped**. An AST check confirms `make_client_order_id` has no `decision_context`
   parameter and `order_manager.py` never references `decision_context`.
3. `tests/test_fmp_paper_broker.py`:
   - With `decision_context=None`, a spy on `store.apply_fill` sees **exactly** today's kwarg set (pins the
     contract for other callers).
   - With an entry ctx, the snapshot kwargs are forwarded. With an exit ctx, `close_reason` and
     `exit_context_json` are forwarded.
   - With a malformed ctx (non-dict, unknown kind, `apply_fill_kwargs` monkeypatched to raise), the order
     **still fills** and the kwargs are unchanged from today.
4. `tests/test_paper_trade_entry_exit_context.py`, `trade_context` unit tests:
   - NaN/inf/None/str/numpy inputs give None or finite floats. `Score` NaN gives `signal_score=None`.
     `Forecast_30` 0.0 or negative gives None. `Advisory_Conviction=0.0` with `Advisory_Action=""` gives
     `conviction=None`.
   - `sizing_source` is `probe` vs `kelly`, and `effective_weight` differs from `kelly_target_row` on probe.
   - `exit_close_reason` covers every EXIT_SIGNAL and gives None for others. The mapping keys equal
     `main_orchestrator.EXIT_SIGNALS`. Every output matches `^[a-z][a-z0-9_]{0,19}$`.
   - Size caps: oversized `Score_Components`/rationale is truncated, gives `context_status="truncated"`,
     and the JSON stays valid and within budget.
   - The builders never raise on `row=None`, an empty dict, or exotic objects.
   - Output is `json.dumps(..., allow_nan=False)`-safe.
5. `tests/test_paper_account_store.py` (tmp DB):
   - Opening `apply_fill` for `main_pipeline` with entry kwargs creates a snapshot with
     provenance `signal_driven` and the fields stored. Without kwargs, still **no snapshot**
     (backward compatibility).
   - A closing fill with `close_reason="signal_risk_reduce"` and `exit_context_json` persists both and
     links `snapshot.trade_id`. `get_full_closed_trades` returns both. Bridge notes contain the reason.
   - `close_reason=None` gives `flatten`. An invalid value (`"DROP TABLE"`, 25 chars, uppercase) gives
     `flatten` plus a warning.
   - Migration: an old-schema `paper_closed_trades` (no `exit_context_json`) gains the column via both
     `PaperAccountStore()` and `database_setup.migrate_paper_closed_trades_schema`. Existing rows read
     back `exit_context_json=None`.
6. `tests/test_retrospective_composer.py` / `tests/test_retrospective_narrative.py`: a pipeline trade with
   a snapshot is `captured` with `provenance='signal_driven'`. `exit_context` is parsed, unparseable JSON
   gives None and `unparseable`, and the exit-trigger sentence appears only for `signal_*`. Existing
   strings are byte-identical for `flatten`/`roll`.
7. End-to-end (tmp DB, `fmp_client.quote` stubbed): `OrderManager` + `FMPPaperBroker` BUY with entry ctx,
   then SELL with exit ctx. Expect one closed trade, `close_reason='signal_risk_reduce'`, `entry_snapshot_id`
   resolving to a snapshot with `trade_id` set, and `RetrospectiveComposer.compose_trade_retrospective`
   showing it captured with the exit context.
8. `OrderIntent` field test: field names are unique, and the existing field order is preserved with
   `decision_context` appended.

The conftest isolation fixtures (`_isolate_paper_and_transactions_db_in_tests`) already redirect the
paper DB. All new tests use `tmp_path` stores and never touch `~/.stockpy_local`.

**Verification gate:** run the targeted files above, then `make ci` (zero failures). Run
`npm run --prefix webapp typecheck` for the types/mock change. After merge and a daemon restart
(`launchctl kickstart -k ... com.investyo.stack`), do a read-only DB check after the next in-hours cycle
that opens or closes a pipeline position: a `paper_entry_snapshots` row for `main_pipeline` with
non-null `key_indicators_json`, and any new close with `close_reason LIKE 'signal_%'`.

## 6. Documentation updates (part of the deliverable)

- `docs/architecture/execution.md`: `OrderIntent.decision_context` (inert, telemetry-only, not part of
  idempotency); `fmp_paper_broker` forwards it to `apply_fill`; new `execution/trade_context.py` entry.
- `docs/architecture/data-layer.md` (paper_account_store / Retrospective section): the producers of
  `paper_entry_snapshots` (Quick Trade stub, pipeline full context), `close_reason` vocabulary
  (`flatten` default/manual, `roll`, `expiry_settlement`, `signal_sell|trim|risk_reduce|avoid`), and the new
  `paper_closed_trades.exit_context_json` column with its key list and schema version.
- `docs/architecture/orchestration-entrypoints.md`: `_execute_broker_orders` attaches entry/exit context
  after sizing, and fails open for the order.
- `docs/architecture/observability-and-apis.md` + `docs/architecture/simulation-eval-reporting.md`: composer
  `exit_context`, and the narrative exit-trigger sentence.
- `docs/architecture/testing.md`: the new test file.
- `docs/known_issues/paper_pipeline_trades_missing_entry_exit_context.md` (new) + a row in
  `docs/known_issues/README.md`: root cause, the 8 open plus 1 closed trades without context (permanent;
  not backfilled), the fix, and status.
- `docs/known_issues/paper_trade_strategy_id_vocabulary.md`: cross-link the close_reason vocabulary.
- `CLAUDE.md`/`AGENTS.md`: **no change** (no rule or command changes).
- PR artifacts: `.claude/paper_trade_entry_exit_context_{implementation_plan,task,walkthrough}.md`.

## 7. Rollout and risk

- No settings flag. This is pure telemetry under the default-value policy and changes no trading
  behavior, so no opt-in is needed. Everything defaults to today's behavior when context is absent.
- Additive nullable column. The migration is idempotent and runs on the first write-mode store
  construction (the daemon) and in `database_setup.py`.
- Risk: a large `key_indicators_json` slows nothing material (one row per fill, ≤8 KB).
- Risk: the shadow/primary cutover (2026-10-05) touches `AgenticQueueStep`, not `BrokerExecutionStep`,
  so there is no interaction.
- Takes effect only after a daemon restart.

## 8. Open questions for the operator

- **Q1. Snapshot `conviction` source.** The pipeline buys on the strategy engine's `Action Signal`, which
  has no probability-scale conviction. Options: (a) default: `Advisory_Conviction` from the same cycle's
  `engine/advisory.py` (a [0, 1] estimate, labelled `conviction_source="advisory"`). It feeds the
  existing Brier calibration, but that then measures the advisory engine's calibration on pipeline trades.
  (b) `None` always, keeping `advisory_conviction` only inside `key_indicators_json`. This is the most
  conservative choice, and calibration stays "not captured" for pipeline trades.
- **Q2. Backfill.** The 8 open positions and the SPY trade will never get entry context. A backfill from
  `DailySignals` would be reconstruction, which the snapshot design forbids
  (`get_entry_snapshot` docstring: "Never attempts retroactive inference"). The recommendation is **no
  backfill**. Up to 8 of the first 30 freeze trades will show `not_captured` entry context, but they
  will get a real `close_reason`.
- **Q3.** Should `fix-daemon-empty-score` (fb616d69) merge first? It is not required, since NaN Score
  correctly gives `signal_score=None`. But merging it first makes the very next snapshots carry a real score.
