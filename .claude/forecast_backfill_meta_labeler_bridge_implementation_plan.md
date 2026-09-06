# Wire the Forecast Backfill screen's meta-labelers into the real live gate

## Context

The Pilots PWA's **Forecast Backfill** screen (`webapp/src/screens/ForecastBackfillScreen.tsx`) trains a
RandomForestClassifier meta-labeler per (signal × horizon) via `ml/forecast_backfill.py::AgenticForecastBackfiller`,
but its own UI copy currently discloses this is a standalone research/diagnostic artifact that never reaches live
trading: the only path that actually gates live position sizing is `scripts/train_meta_labelers.py` →
`ml/registry.yaml` (PBO/DSR-derived `deployable`) → `ml/meta_bootstrap.py::bootstrap_meta_registry()` →
`signals/aggregator.py`'s meta-label confidence gate, and today it only covers 2 signals
(`timeseries_momentum`, `cross_sectional_momentum`).

The operator asked to make this screen actually feed the live models — specifically, after being shown the
methodology mismatch between the two paths (screen: date-grid RandomForest per signal×horizon; live: AFML
CUSUM/triple-barrier `MetaLabeler` per signal), they chose the larger of two options: **extend the live registry
to accept the screen's own trained models**, not merely add a UI trigger for the existing pipeline. The
PBO/DSR deployability gate must never be bypassed or spoofed — this is additive coverage through the same gate,
not a shortcut around it.

Investigation surfaced two things that shape the design:
1. **A real pre-existing bug**: both training paths write pickles into the same directory, and
   `MetaLabeler.load_latest()`'s lexicographic-sort glob means the screen's own `..._90d.pkl` files can
   permanently shadow the real dated AFML pickles for the 2 overlapping signals. Must be fixed regardless of
   scope, via a prefix-namespaced glob.
2. **A real feature-schema gap**: none of the 6 backfill-trainable signals' declared `meta_label_features` are
   fully present in the live per-ticker `row`/`vec_df` the aggregator actually queries a `MetaLabeler` with
   (`strategy_engine.py`'s `row` construction, `pipeline/production_steps.py`'s `vec_df` builder). Registering a
   model with partially-missing features would silently run live inference on a zero-filled feature vector
   (`MetaLabeler._prepare_X()` fills any missing declared feature with `0.0`) — a real correctness risk, not a
   hypothetical one, and it already affects the two existing AFML models' features too (separate, disclosed,
   out-of-scope pre-existing gap). This plan adds a fail-closed feature-compatibility check so a model is never
   registered on a partial/zero-filled feature match — meaning, honestly, this bridge will likely be fully wired,
   tested, and safe but functionally inert until a separate follow-up widens the live row schema. That must be
   stated plainly in the docs and to the operator, not glossed over.

This is core signal/sizing-adjacent infrastructure (per CLAUDE.md's "Everything else" tier) — build on the
current feature branch (`claude/screen-model-training-019a30`, already off `main`), open a PR when done, and do
not merge until targeted tests pass and the gate's fail-closed behavior is verified end-to-end.

## Execution model

**Antigravity builds this out, not this Claude Code session.** Per CLAUDE.md's Branch Workflow, work is
assigned ad hoc regardless of which agent opens the branch — this plan is the Implementation Plan artifact to
hand to Antigravity (its native equivalent of this session's plan-mode output). Before Antigravity starts:
commit this plan into the repo under a unique, feature-scoped name per CLAUDE.md's PR-artifact naming rule —
e.g. `.claude/forecast_backfill_meta_labeler_bridge_implementation_plan.md` — never a bare `implementation_plan.md`.

**Important asymmetry to account for**: CLAUDE.md is explicit that Antigravity has *no automatic blocking test
gate* (`.agents/hooks/stop_test.sh` is advisory-only — it cannot force continuation the way Claude Code's
`verify_before_stop.sh` does). So Antigravity finishing and reporting "done" is not itself evidence the targeted
tests pass — that must be independently confirmed, not assumed, which is exactly what the audit pass below is for.

**Once Antigravity reports the implementation complete, this session (Claude Code) runs a 6-agent audit pass**
over the full diff before it is considered mergeable — deeper than a single reviewer pass, because this change
touches the live position-sizing gate surface. Launch all 6 in parallel (single message, multiple `Agent` calls),
each scoped to one dimension of risk specific to this feature, then consolidate findings before any merge
decision:

1. **Deployability-gate integrity** — confirm `deployable` for `meta_labeler_backfill_<signal_id>` is genuinely
   *derived* from `cpcv_dsr`/`pbo` (never passed in or spoofable), the feature-compatibility gate is real and
   fail-closed (not a stub that always returns `True`), and the AFML-priority tie-break in
   `bootstrap_meta_registry()` is correctly implemented and covered by a real test, not just described in a
   docstring. Load the `stockpy-quant-integrity` skill first.
2. **Live-safety / blast-radius** — confirm every new setting defaults to a fully inert value, the master flag
   is in `settings_keysets.SAFETY_CRITICAL_KEY_REASONS`, and — by tracing the code, not by trusting a comment —
   that with default settings the change is byte-identical to pre-change behavior for the 2 existing AFML
   signals and the diagnostic screen's CSV/JSON output.
3. **Filename-collision regression** — specifically re-verify the `prefix` kwarg added to
   `MetaLabeler.load_latest()`: check every existing call site still gets the unchanged default, and confirm the
   regression test actually proves cross-namespace isolation (construct both an old-style AFML pickle and a new
   backfill pickle, assert each loader only ever finds its own) rather than being tautological.
4. **Quant-integrity / no-lookahead** — confirm `compute_backfill_cpcv_metrics` is genuine reuse of
   `validation.metrics.run_cpcv_evaluation` (not a parallel reimplementation of DSR/PBO math), check
   `_build_training_set`/CPCV fold construction for lookahead bias, and confirm CONSTRAINT #4 — honest `None`s
   below `min_events`, never a fabricated metric. This is the `honesty-auditor` agent's specific mandate.
5. **Test coverage & CI gate** — confirm every test named in this plan actually exists and asserts real
   behavior (not "doesn't crash"), then actually run
   `pytest tests/test_meta_labeling.py tests/test_train_meta_labelers.py tests/test_forecast_backfill.py tests/test_registry_load.py -q`
   plus the repo's `make verify` gate — reporting pass/fail, not assuming it from Antigravity's own report.
6. **Docs & webapp parity** — confirm all required docs (the 6 `docs/signals/*.md` files,
   `docs/VALIDATION_STRATEGY_FIX_LOG.md`, the architecture doc, `docs/plans/FORECAST_BACKFILL_PLAN.md`,
   CLAUDE.md) were actually updated with accurate content — including the disclosed feature-compatibility-gate
   caveat, not a rosier version of it — and check webapp mock/live parity for the new
   `ForecastBackfillModelMetrics` fields (`api-parity-reviewer` agent's specific mandate).

Only after all 6 report back, findings are triaged, and any real issues are fixed (by Antigravity or by this
session, whichever is faster) does this go through the normal rebase/merge step in CLAUDE.md's Start-of-session
checklist.

## Design decisions (resolved, not deferred)

| Question | Decision |
|---|---|
| Model format | Add `MetaLabeler.from_fitted(...)` — a minimal classmethod that wraps an already-fitted sklearn classifier by setting the same attrs `.fit()` sets. Zero changes to `.fit()`/`.predict_proba()`/`.save()`/`.load()`. |
| Per-horizon vs per-signal | Live gate stays single-model-per-signal (out of scope to make horizon-aware). One settings-configurable "live horizon" per signal, default 10 trading days. Other 3 horizons stay diagnostic-only. |
| DSR/PBO computation | Reuse `validation.metrics.run_cpcv_evaluation` (the same validated primitive `scripts/train_meta_labelers.py`/`scripts/train_lgbm.py` call) with a new `strategy_fn` fitting candidate `RandomForestClassifier`s — never reimplement DSR/PBO math. |
| Registry namespacing | New keys `meta_labeler_backfill_<signal_id>`, never reusing `meta_labeler_<signal_id>` (the AFML key). Both entries persist independently; `bootstrap_meta_registry()` prefers the AFML entry when both exist and are deployable, falls back to backfill otherwise. |
| Filename collision (independent bug) | `MetaLabeler.load_latest()` gains an additive `prefix: str = "meta"` kwarg (default unchanged). Backfill-bridge pickles use `backfill_meta_...` — disjoint glob namespace, bug fixed at the root regardless of any flag state. |
| Feature compatibility | New fail-closed check (`LIVE_ROW_FEATURE_WHITELIST` + `check_feature_compatibility()`) refuses to register any model whose declared features aren't fully covered by the live row schema. Applied at registration time (backfill side) and again at bootstrap time (defense in depth). |
| Settings | 4 new fields, all defaulting to fully inert (`False` master flag, empty eligible-signals list) — no behavior change until an operator explicitly opts in per-signal. |

## Files to change

**`ml/meta_labeling.py`**
- Add `MetaLabeler.from_fitted(signal_id, model, feature_names, n_train_samples, *, lgbm_params=None, retrain_freq_days=30, trained_at=None)` classmethod — sets `_model`/`_feature_names`/`_n_train_samples`/`_last_trained` directly, same shape `.fit()` produces.
- `load_latest(signal_id, prefix: str = "meta")` — glob becomes `f"{prefix}_{signal_id}_*.pkl"`. Default value reproduces today's exact behavior for every existing caller.

**`ml/meta_bootstrap.py`**
- Rename the internal `_is_deployable(signal_id, ...)` parameter to `_is_deployable(model_key, ...)` (single call site; existing call becomes `_is_deployable(f"meta_labeler_{signal_id}", registry_data)`).
- Add `LIVE_ROW_FEATURE_WHITELIST: frozenset[str]` — hand-copied from `strategy_engine.evaluate_security()`'s `row` construction (with a comment that it must be kept in sync by hand), plus `check_feature_compatibility(feature_names) -> tuple[bool, list[str]]`.
- Add a second loop in `bootstrap_meta_registry()`, appended **after** the existing untouched AFML loop, gated by `settings.META_LABELING_BACKFILL_BRIDGE_ENABLED` and `settings.META_LABELING_BACKFILL_ELIGIBLE_SIGNALS`: for each eligible signal not already registered by the AFML loop, load `MetaLabeler.load_latest(signal_id, prefix="backfill_meta")`, check `meta_labeler_backfill_<signal_id>`'s derived `deployable`, check feature compatibility, register only if both pass. Fully inert when the flag is off or the list is empty — existing tests for the AFML loop stay green untouched.

**`ml/forecast_backfill_registry_bridge.py` (new module)**
- `BACKFILL_ELIGIBLE_SIGNAL_IDS` (the 6 signals), `DEFAULT_LIVE_HORIZON_DAYS = 10`, `resolve_live_horizon(signal_id)` (settings-driven per-signal override).
- `compute_backfill_cpcv_metrics(X, signal_sign, target, dates, horizon_days, *, theta_c, n_estimators, max_depth, random_state, n_splits=6, n_test_splits=2, min_events=100) -> dict` — builds `t1`, a multi-candidate `strategy_fn` (needed for real PBO measurement, not a trivial single-trial collapse), calls `run_cpcv_evaluation`; returns honest `None`s (never fabricated) below `min_events` or on empty CPCV paths. `mean_oos_max_dd` always `None` (discrete ±1 outcome series isn't a compoundable return, matching the AFML path's own documented limitation).
- `register_backfill_model(signal_id, horizon_days, model, feature_names, n_train, cpcv_result, hyperparameters, train_window, registry_path=None) -> tuple[bool, Optional[str]]` — checks feature compatibility first (cheap no-op skip if incompatible, no pickle/registry write at all), else wraps via `MetaLabeler.from_fitted`, saves to `backfill_meta_{signal_id}_{horizon_days}d_{stamp}.pkl` (confinement-checked, matching the existing step-5 write pattern), calls `ml.registry_io.update_model_metrics(f"meta_labeler_backfill_{signal_id}", ...)`. Dead-letter try/except; never raises.

**`ml/forecast_backfill.py`**
- Extract the existing "resolve features → dropna → sort by Date → build X/y/dates" block (step 5) into `_build_training_set(self, model_type, h)`, stashing results per-combo in `self._training_sets` for step 7 to reuse. Pure refactor, no behavior change.
- New `step_7_register_live_meta_labelers(self) -> Dict[str, Any]`: for each signal in `active_strategies ∩ BACKFILL_ELIGIBLE_SIGNAL_IDS ∩ settings.META_LABELING_BACKFILL_ELIGIBLE_SIGNALS` (only runs at all if `settings.META_LABELING_BACKFILL_BRIDGE_ENABLED`), resolves its live horizon, skips if that (signal, horizon) wasn't trained this run, else runs `compute_backfill_cpcv_metrics` + `register_backfill_model` and writes `cpcv_dsr`/`pbo`/`mean_oos_sharpe`/`registry_key`/`registered`/`skip_reason` into `self.metrics[model_key]` so `export_results()`'s existing summary-JSON serialization picks them up with no further change. Disabled state adds nothing to `self.metrics` — byte-identical CSV/JSON to today.

**`ml/forecast_backfill_worker.py`**
- Add a `("registry_bridge", 7)` phase entry (renumbering `("exporting", ...)` to 8); call `engine.step_7_register_live_meta_labelers()` between `step_6_execute_backfill()` and `export_results()`.

**`ml/registry.yaml`**
- Add 6 stub entries (`meta_labeler_backfill_timeseries_momentum`, `..._cross_sectional_momentum`, `..._rsi2_mean_reversion`, `..._sector_quality_rank`, `..._vrp_premium_selling`, `..._options_flow_sentiment`), each `deployable: false`, `cpcv_dsr: null`, `pbo: null`, `n_train: 0` (never fabricated placeholders), with a `notes` field explaining the AFML-priority tie-break and that these are distinct from the existing `meta_labeler_<signal_id>` rows. `update_model_metrics` requires the key to pre-exist ("never invent new roles"), so these stubs must ship with this PR.

**`settings.py`** (near the existing `META_LABELING_ENABLED` block)
- `META_LABELING_BACKFILL_BRIDGE_ENABLED: bool` (default `False`) — master switch, full description of what it gates.
- `META_LABELING_BACKFILL_ELIGIBLE_SIGNALS: list[str]` (default `[]`) — explicit per-signal opt-in; nothing registers even with the master flag on until a signal is listed here.
- `META_LABELING_BACKFILL_DEFAULT_HORIZON_DAYS: int` (default `10`).
- `META_LABELING_BACKFILL_LIVE_HORIZON_DAYS: dict[str, int]` (default `{}`) — per-signal override.

**`settings_keysets.py`**
- Add `META_LABELING_BACKFILL_BRIDGE_ENABLED` to `SAFETY_CRITICAL_KEY_REASONS` (direct change to live position-sizing gate surface, same tier as `FORECAST_BACKFILL_ENABLED`).

**`webapp/src/api/types.ts`**
- Extend `ForecastBackfillModelMetrics` with optional `cpcv_dsr`, `pbo`, `mean_oos_sharpe`, `registry_key`, `registered`, `skip_reason`.

**`webapp/src/screens/ForecastBackfillScreen.tsx`**
- Add **Live Registry / CPCV DSR / PBO** columns to the "Trained Meta-Labelers Performance" table, rendered only when `registry_key` is present on a row (unaffected rows render exactly as today).
- Correct the "How This Research Engine Works" disclaimer: replace "which this screen's runs do not feed" with an accurate description — this screen's models *can* now reach the live gate for an operator-designated signal/horizon, still PBO/DSR-gated and still subject to a feature-compatibility check.

## Documentation updates (required, not optional)

- `docs/signals/timeseries_momentum.md`, `cross_sectional_momentum.md`, `rsi2_mean_reversion.md`, `sector_quality_rank.md`, `vrp_premium_selling.md`, `options_flow_sentiment.md` — each gets a "**Backfill-Screen Live Meta-Labeler Bridge**" subsection (registry key, chosen live horizon, measured DSR/PBO once run, and the honest feature-compatibility-gate caveat).
- `docs/VALIDATION_STRATEGY_FIX_LOG.md` — dated entry describing the bridge, the AFML-priority tie-break, and the feature-schema gap found.
- Whichever architecture doc currently covers `ml/meta_bootstrap.py`/`ml/registry.yaml` (`docs/architecture/ml-and-reports.md` or `signal-engines.md` — confirm exact file when editing) — document the second bootstrap loop, the whitelist, and the tie-break.
- `docs/plans/FORECAST_BACKFILL_PLAN.md` — update; it's referenced by `ml/meta_bootstrap.py`'s own docstring as describing this engine as purely standalone, no longer fully accurate.
- `CLAUDE.md` — one new changelog bullet (matching the file's existing dense style) covering what changed, the tie-break rule, the filename-collision fix, and the feature-schema-gate finding with its practical activation consequence.

## Tests

- `tests/test_meta_labeling.py` (or wherever `MetaLabeler` is unit-tested): `from_fitted` wraps a pre-fitted classifier correctly; `load_latest` default-prefix behavior is unchanged; custom-prefix calls never cross-match the default namespace (the collision-fix regression test).
- `tests/test_train_meta_labelers.py`: bridge-disabled-by-default is byte-identical to today; registers when AFML absent and gate clears; prefers AFML over backfill when both deployable; falls back to backfill when AFML isn't deployable; refuses registration on a feature-incompatible model (the fail-closed gate); ignores a signal not in the eligible list.
- `tests/test_forecast_backfill.py`: step 7 no-ops when disabled (metrics/CSV/JSON unchanged); computes real CPCV and registers when the gate clears; leaves the registry untouched when DSR/PBO fails; never writes a `meta_`-prefixed pickle that could collide with the AFML glob (explicit regression); skips a feature-incompatible signal with a documented `skip_reason`.
- `tests/test_registry_load.py`: `meta_labeler_backfill_<signal_id>` and `meta_labeler_<signal_id>` update independently, no cross-clobbering.
- Settings test (wherever `FORECAST_BACKFILL_ENABLED`'s default/dangerous-key assertions live): equivalent assertions for `META_LABELING_BACKFILL_BRIDGE_ENABLED`.
