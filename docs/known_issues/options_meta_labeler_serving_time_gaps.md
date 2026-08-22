# Known issue (2026-08-22): Stage 4 Options ML Meta-Labeler had six compounding gate-safety bugs, all more permissive than intended

**Status: fixed.** Branch `fix-options-meta-labeler-gate-safety`.

## What happened

A comprehensive audit of `ml/options_meta_labeler.py` +
`execution/options_paper_executor.py` + `api/pilots_api.py`'s retrain
endpoint found six independent bugs in the Stage 4 ML meta-labeler that
gates and sizes automated options paper trades
(`settings.OPTIONS_META_LABELER_ENABLED`). The gate is genuinely
load-bearing when its inputs and execution are clean — a real rejection or
derate correctly blocks or shrinks a trade. The problem is what happens
when they *aren't* clean: every one of the six failure modes below made the
gate **more permissive**, never more conservative, which is backwards for a
risk gate and a direct violation of this repo's CONSTRAINT #6 ("a failure
must never silently relax a risk limit or a gate to keep going").

### 1. Four of ten model features were always hardcoded constants at live prediction time

`execution/options_paper_executor.py::get_actionable_directives()` built its
`item` dict with only `symbol, strategy, action, directive, legs,
net_premium, ivr, trend_bias, target_dte` — it never set `vrp`, `vix`,
`credit_to_width_ratio`, or `short_delta`. `OptionsMetaLabeler.
_extract_feature_vector`'s dict path only substituted a default when a key
was entirely *absent*, so all four of these silently defaulted
(`vrp=0.02`, `vix=20.0`, `credit_to_width_ratio=0.25`, `short_delta=0.30`)
on **every single live prediction**, regardless of actual market conditions
or the actual spread being evaluated. All four real values were already
sitting at the call site the whole time: `vrp`/`macro_dto` are existing
function parameters (already used a few lines away for
`passes_premium_gate`), `directive["Short_Delta"]` is a real,
already-computed Black-Scholes delta, and `credit_to_width_ratio` is
trivially derivable from `directive["Short_Strike"]`/`["Long_Strike"]`/
`["Net_Premium"]` — none of the four were ever copied into `item`.

### 2. `trend_bias` meant a different thing at train time vs. serve time

Training (`api/pilots_api.py`'s retrain endpoint) computed it as `1.0 if
"put" in strategy.lower() else -1.0` — a pure, deterministic function of the
strategy name, 100% collinear with the `is_put_spread`/`is_call_spread`/
`is_iron_condor` one-hot features already in the same vector, and
outright wrong for Iron Condor (a neutral strategy, mislabeled bearish).
Live serving instead passed a genuinely independent real technical signal
(`directive["Trend_Bias"]`, Aroon Oscillator + Coppock Curve derived).
Whatever coefficient the model learned for this column during training was
a proxy for "which strategy type", not "market trend" — applying it to a
real trend value at inference was not statistically meaningful.

### 3. An unresolvable required feature reached the model as a normal NaN value and produced a confident, INCREASED prediction

`_extract_feature_vector`'s `row.get("ivr", 50.0)`-style defaulting only
fires when a key is missing — in real production use the key is often
*present* but holds `float("nan")` (`directive.get("True_IVR")` defaults to
`nan`, not absence). That NaN sailed through unchanged into
`HistGradientBoostingClassifier.predict_proba` (which natively imputes NaN
splits and answers confidently regardless). Reproduced directly: a NaN-ivr
candidate returned `predict_probability=0.7387`, `sizing_multiplier=1.354`
— a directive whose IVR (a REQUIRED premium-selling gate criterion
elsewhere in this codebase, `true_ivr > 50`) was completely unknown got a
confident 74% win probability and an INCREASED 1.35x position size, not a
decline-to-score.

### 4. A degenerate single-outcome training run produced unclipped 100%/0% confidence forever

When `train()` sees `len(np.unique(y)) < 2` (plausible for a fresh retrain
against a short window or a single strategy), `self.model` collapses to
`("baseline", float(y[0]))` — exactly `0.0` or `1.0`. `predict_probability`'s
`"baseline"` branch returned that value with **no clipping**, unlike the
`"linear_fallback"` branch two cases below it (already clipped to
`[0.05, 0.95]`) and the real sklearn path (`[0.01, 0.99]`). Reproduced: an
all-win degenerate training set produced `predicted_prob = 1.0` for an
objectively terrible synthetic candidate, mapping to the maximum 1.5x
sizing multiplier — 100% confidence from a training run that never
observed a single loss.

### 5. Any exception during ML scoring silently failed OPEN to full, un-gated trade size, logged only at DEBUG

```python
except Exception as exc:
    logger.debug("ML Meta-labeler evaluation skipped: %s", exc)
```
No `continue`, no derating — execution fell through to the next step with
`contracts` still at its full pre-ML-gate value. Any exception during
scoring (a `KeyError`, a malformed directive, a future refactor
reintroducing a crash) caused the trade to proceed at FULL size as if the
gate never existed, with the only trace a DEBUG-level log line most
production log configurations never surface.

### 6. No purged/held-out evaluation — `train()` reported in-sample metrics with no disclosure

`clf.fit(X, y)`, then `accuracy`/`roc_auc` computed by calling
`predict()`/`predict_proba()` on that SAME training set — no train/test
split, no `CombinatorialPurgedCV`, no embargo, inconsistent with this
repo's established CPCV convention (`validation/harness.py`). These
in-sample numbers were surfaced verbatim via `GET
/pilots/options/meta-model/status` and the retrain response, where an
operator could mistake them for genuine out-of-sample validation.

## The fix

- **Items 1 & 3**: two new helpers in `ml/options_meta_labeler.py` —
  `_resolve_numeric_feature(row, key, default)` (dict path) and
  `_finite_or_nan(value)` (dataclass path) — distinguish a **missing** key
  (keeps today's default, backward compatible) from a key that is
  **present but `None`/NaN/unparseable** (returns `NaN`, which propagates
  into the feature vector rather than being silently defaulted).
  `predict_probability` gates on `np.all(np.isfinite(x_vec))` *before*
  dispatching to any model branch — a non-finite feature vector declines to
  score and returns the existing neutral `0.65` fallback (which resolves to
  exactly `1.0x` sizing, matching the untrained-model cold-start behavior),
  logged at WARNING. `score_option_directive` gained an additive
  `features_resolved: bool` key so callers/tests can distinguish "the model
  gave a genuinely neutral answer" from "scoring was skipped because
  required data was unresolved". `execution/options_paper_executor.py::
  get_actionable_directives` now resolves `vrp`/`vix` from the real `vrp`/
  `macro_dto` parameters and adds two new helpers,
  `_resolve_short_delta`/`_resolve_credit_to_width_ratio`, mirroring
  `validation/options_harness.py`'s training-side formulas exactly (`abs`
  of the short leg's Black-Scholes delta; `abs(net premium) / strike
  width`) — all four new keys (`vrp`, `vix`, `short_delta`,
  `credit_to_width_ratio`) are always explicitly present on the `item`
  dict, `None` (never fabricated) when unresolvable, so an unresolved value
  is distinguishable from "this feature was never asked for" and reaches
  the finiteness gate above rather than vanishing into a stale default.
- **Item 2**: `trend_bias` was dropped from the model's feature vector
  entirely (`FEATURE_NAMES` and `_extract_feature_vector` both shrank from
  10 to 9 columns) rather than made to match between train and serve.
  Reconstructing a genuine historical Aroon/Coppock trend-bias-at-entry-date
  for training would require pulling full per-ticker price history +
  rolling-window indicator computation into
  `validation/options_harness.py`, which has none of that today — a
  separate, larger, independently-riskable feature addition, not a bug fix.
  The `trend_bias` field remains on `OptionsTradeFeatureRow` (harmless,
  unused) so `api/pilots_api.py`'s existing retrain-endpoint construction
  call needed no changes.
- **Item 4**: the degenerate `"baseline"` branch in `predict_probability`
  now clips to `[0.05, 0.95]`, matching the other two model branches — a
  training run that never observed both outcomes can no longer report
  absolute certainty.
- **Item 5**: the ML-gating exception handler in
  `execute_strategy_directives` now fails CLOSED — logs at WARNING, appends
  the trade to `skipped` with a reason naming the exception, and
  `continue`s — instead of silently proceeding at full un-derated size.
- **Item 6 (minimal fix, chosen over full CPCV integration)**: full
  purged/embargoed CV integration for this model is a separate, larger
  validation-infrastructure task (nothing in this file threads
  synthetic/backtest trades through `CombinatorialPurgedCV` today) — out of
  proportion to a bug-fix pass. Instead, `train()`'s three return-dict
  branches (sklearn success, linear-fallback, degenerate-baseline) all gain
  `"metrics_are_in_sample": True`; `api/pilots_api.py`'s
  `get_options_meta_model_status`/`post_options_meta_model_retrain`
  handlers echo the same key and gained an explicit in-sample/optimistic
  caveat in their docstrings; the Pilots PWA (`PaperBroker.tsx`) relabels
  the two metric tiles "Model Accuracy (in-sample)" / "ROC-AUC Score
  (in-sample)" and the retrain success toast gained the same qualifier.

## What's still open

- **`trend_bias` was dropped, not replaced.** No genuine train-time trend
  signal exists for this model as of this fix — a real fix would need
  per-ticker historical price data threaded into
  `validation/options_harness.py`'s backtest trade construction. Disclosed
  here rather than silently left unaddressed.
- **No CPCV/purged evaluation exists for this model.** `metrics_are_in_sample:
  True` discloses the gap honestly; it does not close it. A real fix is a
  separate, larger validation-infrastructure task.
- **`pilots/paper_broker.py`'s on-demand execute path
  (`get_strategy_options_candidates`/`execute_strategy_options`, backing
  `GET/POST /pilots/paper-broker/strategy-options/*`) never passes
  `vrp`/`macro_dto` into `get_actionable_directives()` at all** — only
  `main.py`'s automated cycle-level call (`_executor.execute_strategy_directives(macro_dto=result.macro_dto)`)
  supplies a real `macro_dto`, and neither production call site passes
  `vrp` explicitly. This means `vrp`/`vix` will legitimately read as
  `None`/unresolved on every request through the on-demand Pilots API path
  today — which, after this fix, degrades safely and honestly (neutral
  `1.0x` sizing via the finiteness gate) rather than defaulting to a
  plausible-looking constant, but it does mean the model is scoring on
  incomplete inputs more often than it needs to on that path. Wiring a real
  `vrp`/`macro_dto` into that call site is a disclosed, out-of-scope
  follow-up, not silently left undocumented.
- **Webapp field parity is additive/type-only.** `StrategyOptionCandidate`
  in `webapp/src/api/types.ts` gained optional `vrp`/`vix`/`short_delta`/
  `credit_to_width_ratio` fields (mirrored in `mock.ts`) for mock/live
  parity, since `GET /pilots/paper-broker/strategy-options/candidates`
  serializes `get_actionable_directives()`'s dicts directly — no screen
  renders them yet; surfacing them in the Paper Broker UI is a reasonable
  follow-up, not required by this fix.

## Tests

`tests/test_options_meta_labeler.py`: `test_feature_vector_extraction`
updated for the 9-column vector;
`test_nan_ivr_declines_to_score_instead_of_predicting_confidently` and
`test_none_valued_feature_also_declines_to_score` (item 3, both the
present-but-NaN and present-but-`None` paths);
`test_degenerate_single_class_training_never_produces_unclipped_confidence`
(item 4); `test_train_and_predict` and
`test_score_option_directive_reports_features_resolved_key` extended for
`metrics_are_in_sample`/`features_resolved` (items 4/6).

`tests/test_options_paper_executor.py`:
`test_get_actionable_directives_filters_cash_and_wait` extended with the
top-level `Short_Strike`/`Long_Strike`/`Short_Delta` keys real production
directives actually carry (the prior fixture's omission of these is exactly
why items 1/3/4 shipped undetected) and now asserts the four new keys
resolve to real values;
`test_get_actionable_directives_no_short_leg_never_fabricates_derived_features`
(a no-short-leg directive yields `None`, never a fabricated ratio);
`test_get_actionable_directives_vrp_and_vix_present_but_none_when_unresolvable`
(keys always present, `None` when the caller has no macro/vrp context);
`test_execute_strategy_directives_fails_closed_on_ml_scoring_exception`
(item 5, asserts the trade is skipped and a WARNING — not DEBUG — is
logged); `test_get_actionable_directives_end_to_end_derived_features_drive_ml_decision`
(a full end-to-end proof: a real trained model whose win probability
depends on `short_delta`/`credit_to_width_ratio`, fed a production-shaped
directive through the real `get_actionable_directives` →
`execute_strategy_directives` pipeline, confirming the derived values —
not hardcoded defaults — drove the approval/sizing decision).

Full regression sweep after the fix: `tests/test_options_meta_labeler.py`,
`tests/test_options_paper_executor.py`, `tests/test_pilots_paper_broker.py`,
`tests/test_pilots_api.py`, `tests/test_options_harness.py`,
`tests/test_options_queue_builder.py` — 668 passed, 0 failed.
