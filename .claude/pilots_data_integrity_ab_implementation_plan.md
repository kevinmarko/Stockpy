# Pilots / Paper-Trading Data Integrity, Learning Loop, and A/B Framework

## Context

You asked four things: review the pilots, analyze the databases for insights and
errors, design an A/B testing system for our metrics, and make sure paper trading
is feeding our models so they learn better strategies.

I audited the live databases and traced the actual call paths. The headline finding
is that **the paper-trading learning loop does not exist**, and the reason is
structural rather than a missing feature: `PaperAccountStore` has no closed-trade
record and no strategy column, so there is nothing for a model to learn *from*.
Everything else — honest per-pilot metrics, Kelly warm-up, A/B testing — is blocked
behind that same gap. Fixing the data foundation first is therefore the plan.

### What I verified against the live system (not from docs)

**The loop is broken, with evidence:**

- `data/paper_account_store.py:347-348` — `apply_fill` **deletes** the position row when
  flattened. Entry price and holding period are destroyed at close. `settle_expired_options`
  (`:721`) does the same. There is no realized-PnL record anywhere in the paper store.
- `paper_orders` has **no strategy column**. Multi-leg attribution is smuggled into the
  symbol *string* (`f"{strategy_name} {symbol}"`, `:479`) and recovered by SQL `LIKE`
  (`execution/options_paper_executor.py:913`). Single-leg `apply_fill` (`:253`) has no
  strategy parameter at all — every equity paper fill has zero attribution.
- `transactions_store.py`'s `trades` table has exactly the right shape and **0 rows**. Its
  only production writers are two lines inside an MCP tool (`investyo_mcp_server.py:1368`,
  `:1391`) where a human types the price. Nine automated paper writers write none of it.
- Consequence: `sizing/kelly.py` (needs 30 closed trades per `strategy_id`),
  `evaluation_engine.py` (MAE/MFE, edge ratio, calibration) are permanently starved.
  `execution/order_manager.py::reconcile_state` compares broker positions against the
  empty `trades` table and logs `logger.critical("RECONCILIATION DRIFT")` every cycle.

**The one ML path that reads paper data is train/serve skewed:**

- `ml/training_data.py:218` is the only ML read of `paper_orders`, producing 6 `paper_*`
  features. `ml/feature_engineering.py:178-181` always takes the `else` branch live and
  fills all six with `np.nan` — nothing populates them in `universe_df`.
- Those six features are baked into the currently `deployable: true` `lgbm_ranker`.
- Worse: the registry's train window ends **2026-07-14/20**; the first paper order is
  **2026-08-14**. Zero overlap. The features were NaN in training *and* are NaN at
  inference. `paper_avg_realized_pnl_30d` is not realized PnL either — it is a
  triple-barrier simulation off price history (`:332-395`), because no exit prices exist.
- `lgbm_ranker`'s live weight is `0.1` of a `~300` total — 0.03% of the composite score.

**The options meta-labeler gates real paper sizing with no gate of its own:**

- `POST /pilots/options/meta-model/retrain` (`api/pilots_api.py:6001-6011`) says
  "on simulated/paper trades" but trains on a hardcoded `SPY`, `2020-01-01`→`2024-01-01`
  Black-Scholes backtest. It never reads `PaperAccountStore`.
- `ml/options_meta_labeler.py:165-172` fits on `X` then scores on the same `X` — the
  accuracy/ROC-AUC surfaced by `GET .../meta-model/status` are **in-sample**.
- It has no `ml/registry.yaml` row, so no DSR/PBO gate — unlike every other model here.
  `scripts/retrain_models.py` does not retrain it.
- `.env` has `OPTIONS_META_LABELER_ENABLED=true` and `PAPER_OPTIONS_AUTO_EXECUTE_ENABLED=true`.

**Both signal meta-labelers are inert.** Live registry: `cpcv_dsr` of `6.7e-45` and
`3.0e-07`, `cpcv_mean_oos_max_dd: 1.0`, TSMOM `cpcv_mean_oos_sharpe: -0.73`, both
`deployable: false`. `meta_bootstrap` fails closed, so Stage-4 meta-gating never fires.

**Corrupt paper options book — root cause found.** 38 of 51 `paper_positions` are options;
several carry `avg_entry_price = 0.00`, including a full dispersion basket at a fabricated
uniform `$150.00` constituent / `$500.00` QQQ strike. Mechanism:
`data/paper_account_store.py:434` reads `float(leg.get("fill_price", 0.0))` — **silently
defaults to zero and never validates the price is positive**. The reachable live path is
`pilots/dispersion_trading.py:1026-1031`, which accepts a **client-supplied basket dict**
(`DispersionBasket(**basket)`) and passes its legs through unvalidated. `build_dispersion_basket`
itself is now honest (refuses on missing spot/IV, `:444-459`), so the `$150`/`$500` rows are
residue from a pre-fix build — but the ingress that allows it is still open today.

**Zero A/B infrastructure exists.** I searched for variant/champion/challenger/shadow/
experiment/bandit/holdout across `.py`/`.ts`/`.tsx`/`.md`. Every hit is a false positive
(FMP bar-adjustment "variant", the Bandit linter, a Holt-Winters holdout). No order carries
an arm tag; no two models ever run side by side.

**Database state (live `~/.stockpy_local/quant_platform.db`, 697 MB, 42 tables):**
- Working: `forecast_errors` 2.31M rows (300K resolved) — the *one* genuinely closed
  feedback loop, and `FORECAST_SKILL_WEIGHTING_ENABLED=true` is live.
- Empty and load-bearing: `trades` (0), `execution_audit_records` (0), `DailySignals` (0,
  dead by design), `rag_indexed_docs` (0), all three `cache_ls_*` (0).
- Never created: `live_trade_proposals` — the human-in-the-loop live-order approval gate
  has never run.
- Suspect: `sector_correlations.as_of = '2026-08-24'`, **two days in the future**, 341 rows,
  read by `pilots/sector_selection.py`. `etf_holdings` has **1 row** (a QQQ futures contract,
  negative weight, `as_of` 2026-03-31) yet feeds three `COLUMN_SCHEMA` columns.
- Split-brain remnant: `/Users/kevinlee/Stockpy-live/quant_platform.db` (16 KB) still holds a
  live `forecast_errors` schema, now drained to 0 rows — a re-write target if anything regresses.

**Metric duplication (this repo's known failure mode, still present):** Sortino has 4
independent implementations with 3 different formulas; Calmar 3; max drawdown is
re-implemented inline in 3 places despite a canonical `compute_max_drawdown`. The worst
offenders cluster in `validation/options_harness.py`, which sets `pbo_val = 0.0` (`:593`)
— the best possible value — when the sample is thin, and `n_trials=max(1, total_trades)`
(`:573`), which trips `deflated_sharpe_ratio`'s `return 1.0` shortcut. **A thin options
backtest auto-passes both halves of the deployability gate.**

**Unregistered pilots still executing:** `earnings_crush`, `dispersion_trading`,
`zero_dte_engine`, `gamma_scalper` have no `STRATEGY_REGISTRY` entry. `vol_mispricing` and
`copula_stat_arb` now do (the doc was accurate). `gamma_scalper` additionally has no entry in
`OPTIONS_DESK_DEPLOYABILITY_GATES`, so it discloses nothing at all. `pilots/zero_dte_engine.py`'s
15:45 ET hard-exit is now wired into `desktop/daemon_runtime.py` — that previously-flagged gap
is closed.

---

## Approach

Five PRs, strictly sequenced. PR 1 is a safety fix that must land first. PRs 2–3 build the
data foundation. PR 4 closes the learning loop. PR 5 is the A/B framework, which is only
honest once 2–4 exist.

**Guiding constraint:** do not add a second implementation of anything. All arm comparison
reuses `validation/metrics.py`. All sizing stays in `sizing/position_sizer.py::size_position`.

---

### PR 1 — `fix-paper-fill-price-guard` (safety, land first)

**Problem:** a zero or missing option leg price books a free position, and every downstream
P&L, Greek, and meta-label computed on it is meaningless.

- `data/paper_account_store.py::apply_multi_leg_fill` (`:434`) — replace
  `float(leg.get("fill_price", 0.0))` with a required, validated read. A leg with a missing,
  non-numeric, or `<= 0` `fill_price` **rejects the whole atomic order** (CONSTRAINT #6 —
  fail closed, not skip-the-leg), inserting a `REJECTED` order row with a reason. Apply the
  same guard to `apply_roll_fill` and the single-leg `apply_fill`.
- `pilots/dispersion_trading.py:1026-1031` — validate a client-supplied `basket` dict before
  `DispersionBasket(**basket)`: every leg must carry a positive `fill_price` and a strike
  consistent with a real resolved spot. On failure, refuse with the existing honest message
  rather than falling back to `build_dispersion_basket(index_symbol=idx_sym)` with empty maps.
- **Purge (operator-approved):** `scripts/purge_corrupt_paper_options.py`, `--dry-run`
  default / `--apply`, backing up `quant_platform.db` first. Deletes `paper_positions` rows
  where the symbol parses as an option **and** `avg_entry_price <= 0`, and reverses the
  corresponding cash impact. Leaves the 13 equity positions and correctly-priced option legs
  (the real `2026-09-18` basket) untouched. Prints a before/after table.
- `pilots/vol_mispricing.py:1355` — remove the surviving
  `spot_price = 500.0 if sym == "SPY" else (130.0 if sym == "NVDA" else 150.0)` fabrication;
  degrade to `None` and let the caller's honest path run, matching the fix already applied in
  `pilots/options_gex.py:1160` and `pilots/volatility_surface.py`.

Tests: extend `tests/test_paper_account_store.py` (reject-on-zero-price, atomicity — no
partial leg writes on rejection), `tests/test_dispersion_trading.py` (client-supplied basket
validation), new `tests/test_purge_corrupt_paper_options.py`, extend `tests/test_vol_mispricing.py`.
Docs: `CLAUDE.md` paper-broker bullet, `docs/architecture/execution.md`, and a new
`docs/known_issues/paper_options_zero_fill_price.md` write-up.

---

### PR 2 — `paper-trade-strategy-attribution`

Give every paper fill a real `strategy_id`, replacing the symbol-string hack.

- `data/paper_account_store.py` — additive `ALTER TABLE ADD COLUMN` migration (this repo's
  established pattern, see `data/historical_store.py`) adding to `paper_orders`:
  `strategy_id TEXT`, `pilot_id TEXT`, `experiment_arm TEXT` (nullable now, used by PR 5),
  `leg_group_id TEXT` (links legs of one multi-leg order), `order_kind TEXT`
  (`equity`|`option_leg`). Bump `CURRENT_SCHEMA_VERSION`.
- Add `strategy_id: Optional[str] = None` to `apply_fill`, `apply_multi_leg_fill`,
  `apply_roll_fill`, `settle_expired_options`. Default `None` preserves today's behavior
  exactly, so no call site breaks.
- Thread a real value through all nine writers: `pilots/paper_broker.py`,
  `pilots/paper_broker_options_order.py`, `pilots/zero_dte_engine.py`,
  `pilots/dispersion_trading.py`, `pilots/copula_stat_arb.py`, `pilots/options_hedging.py`,
  `pilots/vol_mispricing.py`, `execution/options_paper_executor.py`,
  `execution/fmp_paper_broker.py`.
- Keep the `f"{strategy_name} {symbol}"` symbol prefix for one release so the existing
  `LIKE` readers keep working; migrate `execution/options_paper_executor.py:913` to the
  column and mark the string form deprecated in a comment.

Tests: extend `tests/test_paper_account_store.py` (migration is additive and idempotent;
`strategy_id=None` reproduces prior behavior byte-for-byte), plus one attribution assertion
in each of `tests/test_options_paper_executor.py`, `tests/test_dispersion_trading.py`,
`tests/test_zero_dte_engine.py`.

---

### PR 3 — `paper-closed-trades-and-transactions-bridge`

The core fix. **Recommendation: do both halves** — a paper-native round-trip record *and*
a bridge into the existing `trades` table — because they serve different consumers and the
bridge un-starves Kelly/evaluation/calibration for free.

- New `paper_closed_trades` table in `PaperAccountStore`: `trade_id`, `strategy_id`,
  `pilot_id`, `experiment_arm`, `symbol`, `side`, `qty`, `entry_ts`, `entry_price`,
  `exit_ts`, `exit_price`, `commission`, `realized_pnl`, `realized_pnl_pct`,
  `holding_period_days`, `close_reason` (`flatten`|`expiry_settlement`|`roll`|`stop`|`target`),
  `leg_group_id`.
- Write it at the three points that currently destroy the information: the flatten branch of
  `apply_fill` (`:347-348`), the multi-leg flatten branch, and `settle_expired_options`
  (`:721`) — **before** the `session.delete(pos)`, inside the same transaction.
- Bridge: on each close, also `transactions_store.record_trade(...)` +
  `close_trade(...)` with `strategy=strategy_id`, wrapped best-effort (a bridge failure logs
  and never aborts a fill — CONSTRAINT #6). This is what makes
  `sizing/kelly.py::estimate_win_rate_and_payoff_per_strategy` and
  `evaluation_engine.py`'s MAE/MFE start warming up, and it resolves the permanent
  `RECONCILIATION DRIFT` critical in `execution/order_manager.py::reconcile_state`.
- New setting `PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED`, default **True** — this is an
  additive record-keeping capability, not a trading-behavior change, and Kelly's own
  `MIN_TRADES_REQUIRED=30` gate means it cannot alter sizing until 30 real closes exist.
- Rewrite `ml/training_data.py`'s `paper_avg_realized_pnl_30d` / `paper_hit_rate_30d` to read
  `paper_closed_trades` — genuine realized PnL, replacing the triple-barrier stand-in. Keep
  the PIT filter (`exit_ts < as_of`).
- **Close the train/serve skew:** populate the six `paper_*` columns in the live
  `universe_df` from `pipeline/production_steps.py` so `ml/feature_engineering.py:178-181`
  stops taking the NaN branch. Until this lands, `lgbm_ranker` should honestly be treated as
  running without those features.

Tests: new `tests/test_paper_closed_trades.py` (round-trip PnL correctness incl. shorts and
option multipliers, expiry settlement, partial closes); extend
`tests/test_paper_account_store.py`, `tests/test_training_panel.py` (real closed trades feed
the features), `tests/test_order_manager.py` (drift resolves).
Docs: `CLAUDE.md`, `docs/architecture/execution.md`, `docs/architecture/ml-and-reports.md`.

---

### PR 4 — `options-meta-labeler-honest-gate`

- Rewrite `POST /pilots/options/meta-model/retrain` to train on **real** `paper_closed_trades`
  from PR 3, falling back to the backtest path only when fewer than a minimum number of real
  closes exist — and saying so explicitly in the response (`data_source: "paper" | "backtest"`,
  `n_real_trades`). Remove the hardcoded `ticker="SPY"` / `2020-2024` as the *only* source, and
  the hardcoded `target_dte=35` and strategy-name-derived `trend_bias`, which are fabricated
  feature values.
- Replace in-sample `clf.fit(X, y)` → `predict(X)` metrics (`ml/options_meta_labeler.py:165-172`)
  with a purged walk-forward split; surface both and label them honestly.
- Add an `ml/registry.yaml` row with real CPCV DSR/PBO via `validation/metrics.py::run_cpcv_evaluation`,
  and make `execution/options_paper_executor.py` fail **closed** (multiplier `1.0`, no derate,
  no hard-reject) when the model is absent or `deployable: false` — matching
  `ml/meta_bootstrap.py`'s existing convention.
- Add `OptionsMetaLabeler` to `scripts/retrain_models.py` so the monthly launchd job covers it.
- Fix `validation/options_harness.py`'s two fail-open gates: `pbo_val = 0.0` (`:593`) → `NaN`,
  and `n_trials=max(1, total_trades)` (`:573`) → refuse to report a DSR at all below a minimum
  trade count rather than returning `1.0`. Also `:548-570`'s fabricated `1e-4` Sortino floor,
  `0.0` max-drawdown, `99.0`/`1.0` profit factor, and `3.0` kurtosis.
- Register `earnings_crush`, `dispersion_trading`, `zero_dte_engine`, `gamma_scalper` in
  `OPTIONS_DESK_DEPLOYABILITY_GATES` at minimum (`gamma_scalper` has no entry at all today),
  and document honestly in `docs/VALIDATION_STRATEGY_FIX_LOG.md` why each lacks a
  `STRATEGY_REGISTRY` entry.

Tests: extend `tests/test_options_meta_labeler.py`, `tests/test_options_paper_executor.py`
(fail-closed on missing/non-deployable model), new `tests/test_options_harness_gate_honesty.py`.
Docs: `docs/signals/*.md` per strategy, `docs/VALIDATION_STRATEGY_FIX_LOG.md`, `CLAUDE.md`.

---

### PR 5 — `experiment-framework` (A/B testing)

New `experiments/` package — a flat top-level module set matching this repo's engine convention.

- `experiments/registry.py` — `Experiment(id, name, unit, arms, allocation, started_at,
  min_samples_per_arm, status)`. `unit` ∈ `signal_weights` | `pilot_params` | `sizing_params`
  | `model_variant`, covering all four units you selected. An `Arm` carries an overrides dict
  applied on top of the live config; the control arm is always the empty override, so a
  disabled experiment is provably byte-identical to today.
- `experiments/assignment.py` — deterministic arm assignment, seeded by
  `hash(experiment_id, symbol, cycle_date)`. Deterministic rather than random so a cycle can
  be replayed and so the same symbol does not flip arms daily.
- `experiments/store.py` — `experiment_runs` and `experiment_observations` tables, mirroring
  `desktop/run_history_store.py`'s conventions (own `Base`, `readonly=True` engine,
  `db_config.resolve_database_url()`).
- **Arm tagging costs nothing extra:** PR 2 already added `experiment_arm` to `paper_orders`,
  and PR 3 to `paper_closed_trades`. The framework only has to set it.
- **Shadow arms** (`model_variant`, e.g. meta-labeler on/off) log the counterfactual decision
  and its size to `experiment_observations` without executing, so a challenger can be
  evaluated with zero capital at risk. This is the right default for anything model-shaped.
- `experiments/compare.py` — arm comparison that **imports** `validation/metrics.py`
  (`sharpe_ratio`, `deflated_sharpe_ratio`, `probability_of_backtest_overfitting`) and
  `validation/stress_scenarios.py::compute_max_drawdown`. Deliberately introduces no new
  metric function. Multi-arm comparison runs through
  `validation/multiple_testing.py::deflated_sharpe_family`, because comparing N arms is
  exactly the multiple-testing problem DSR exists to deflate.
- **Honest insufficient-data gate (non-negotiable):** with 269 orders over 8 days, any
  "winner" today would be noise. `compare.py` returns
  `{"verdict": "insufficient_data", "n_per_arm": {...}, "required": N, "reason": ...}` and
  **never a ranked winner** until every arm clears `min_samples_per_arm`. The API and the
  webapp render that state explicitly rather than a leaderboard of noise — CONSTRAINT #4
  applied to a comparison rather than a scalar.
- Settings: `EXPERIMENTS_ENABLED` (default **False** — this changes what gets traded, so it
  follows the trading-behavior convention, not the admin-capability one), `EXPERIMENT_DEFAULT_MIN_SAMPLES`,
  `EXPERIMENT_MAX_CONCURRENT`. All default to today's exact behavior.
- API: `GET /pilots/experiments`, `GET /pilots/experiments/{id}`,
  `POST /pilots/experiments` (write-gated by `require_command_token` +
  `EXPERIMENTS_WRITES_ENABLED`), `POST /pilots/experiments/{id}/stop`. Read helpers live in
  `pilots/experiments.py` to stay off the AST-guarded heavy-import path.
- Webapp: new `webapp/src/screens/Experiments.tsx` following the `new-pwa-screen` skill's
  fixed order (types → client + mock → screen → route → test), with an honest-disabled and an
  honest-insufficient-data mock fixture.

Tests: `tests/test_experiments_registry.py`, `tests/test_experiments_assignment.py`
(determinism, allocation balance), `tests/test_experiments_store.py`,
`tests/test_experiments_compare.py` (**including a test asserting that below
`min_samples_per_arm` no winner is ever returned**), `tests/test_pilots_experiments.py`,
`webapp/src/screens/Experiments.test.tsx`.
Docs: `CLAUDE.md`, new `docs/architecture/experiments.md`, `docs/HOW_TO_GUIDE.md`.

---

### Deferred (flagged, not in scope)

- `sector_correlations.as_of` two days in the future — trace the label producer; this is a
  lookahead-shaped smell in a table `pilots/sector_selection.py` reads.
- `etf_holdings` at 1 stale row while feeding three `COLUMN_SCHEMA` columns.
- The 4-way Sortino / 3-way Calmar / 3-way max-drawdown duplication outside the options path.
- `pilots/realized.py` — the only real-fill P&L computation in the repo, **zero tests**.
- `live_trade_proposals` table never created; the live-order approval gate has never run.
- The 16 KB split-brain `forecast_errors` DB at the main checkout root.

---

## Verification

Per PR, before claiming done:

1. `pytest tests/test_<module>.py` for every test file the PR touches — run, not inferred.
2. `make verify` (ruff + offline suite) before the final PR in each pair.
3. PR 1: run `scripts/purge_corrupt_paper_options.py --dry-run` and read the table; confirm
   the 13 equity and the real `2026-09-18` option legs are untouched. Then re-query
   `SELECT COUNT(*) FROM paper_positions WHERE avg_entry_price <= 0` → expect 0.
4. PR 3: execute a paper round-trip end-to-end (open → close via the Paper Broker screen),
   then confirm one row in `paper_closed_trades` with correct `realized_pnl`, one row in
   `trades` with the right `strategy`, and that `reconcile_state` no longer logs
   `RECONCILIATION DRIFT`.
5. PR 4: `python -m validation.harness` against an options strategy and confirm a thin sample
   now reports `NaN` PBO / refuses a DSR rather than auto-passing.
6. PR 5: create a 2-arm experiment, run one cycle, confirm `experiment_arm` is populated on
   real orders and that `compare.py` returns `insufficient_data` — not a winner.
7. Webapp changes: `npm run --prefix webapp typecheck` **and** a real `npm run dev` browser
   check (console clean + the screen renders).

PR artifacts per `CLAUDE.md`: `.claude/pilots_data_integrity_ab_implementation_plan.md`,
`_task.md`, `_walkthrough.md`.
