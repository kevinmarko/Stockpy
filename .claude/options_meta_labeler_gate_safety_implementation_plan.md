# Fix Stage 4 Options ML Meta-Labeler: 6 compounding gate-safety bugs

## Context

An audit of `ml/options_meta_labeler.py` + `execution/options_paper_executor.py` +
`api/pilots_api.py`'s retrain endpoint found six compounding bugs in the Stage 4
ML meta-labeler that gates/sizes automated options paper trades. The gate works
correctly when its inputs are clean — the bugs are all in what happens when
inputs or execution *aren't* clean, and every failure mode found is **more
permissive**, never more conservative: unresolved features silently default
to plausible-looking constants, a degenerate training run reports 100%
certainty, and a scoring exception lets a trade through at full un-derated
size. This directly violates this repo's CONSTRAINT #6 (a failure must never
silently relax a risk limit). This plan fixes all six, adds regression tests
proving each fix against the model's *actual* production input shape (not the
fully-populated fixtures the existing tests use), and documents the finding
per this repo's `docs/known_issues/` convention.

Already confirmed by direct code read (not assumed):
- `api/pilots_api.py`'s retrain endpoint (`post_options_meta_model_retrain`,
  ~line 5986) already sources real per-trade `entry_ivr/entry_vrp/entry_vix/
  entry_credit_to_width_ratio/entry_short_delta` from
  `validation/options_harness.py::OptionsValidationHarness.run_backtest` and
  skips trades missing any of them — so item 3's "unresolved → skip, not
  silently default" fix already exists on the **training** side. The bug is
  serving-time-only (`execution/options_paper_executor.py::get_actionable_directives`).
- `trend_bias` at training time (`api/pilots_api.py:6026`) is
  `1.0 if "put" in strategy else -1.0` — a pure function of strategy name,
  100% collinear with the `is_put_spread`/`is_call_spread`/`is_iron_condor`
  one-hot columns already in the feature vector, and wrong for Iron Condor
  (mislabeled bearish). No historical Aroon/Coppock trend value exists
  anywhere in `validation/options_harness.py`'s trade records, and
  reconstructing one causally per-trade-entry-date would require pulling in
  full price history + rolling-window indicator computation into the harness
  — a substantial, separately-riskable feature addition, not a bug fix.
  **Decision: drop `trend_bias` from the model's feature vector entirely**
  (item 2's explicitly offered fallback option) rather than attempt to make
  training match serving. This is disclosed in the known-issues doc.

## Fixes (file-by-file)

### 1 & 3. `ml/options_meta_labeler.py` — NaN propagation + finiteness gate

- Add two small helpers: `_resolve_numeric_feature(row, key, default)` (dict
  path) and `_finite_or_nan(value)` (dataclass path). Contract: a **missing**
  key/absent value keeps today's default (backward compatible for existing
  fully-populated dict callers); a key that's **present but `None`/NaN/
  unparseable** returns `float("nan")` — this is the crux of item 3: today
  `row.get("ivr", 50.0)` only defaults on a missing key, so a key set to an
  explicit `NaN` (the real production case — `directive.get("True_IVR")`
  defaults to `nan`, not absence) sailed straight through as if it were a
  real value.
- Rewrite `_extract_feature_vector` to use these helpers for
  `ivr/vrp/vix/target_dte/credit_to_width_ratio/short_delta`, and **drop
  `trend_bias`** from `FEATURE_NAMES` and the returned vector (item 2) —
  vector shrinks from 10 to 9 columns. Keep the `trend_bias` field on
  `OptionsTradeFeatureRow` untouched (harmless, unused) so
  `api/pilots_api.py:6026`'s existing construction call keeps working
  without edits.
- In `predict_probability`, after building `x_vec`, add:
  `if not np.all(np.isfinite(x_vec)): log WARNING; return 0.65` — placed
  *before* the `isinstance(self.model, tuple)` branches so it uniformly
  protects the baseline/linear-fallback/sklearn paths alike. `0.65` is the
  existing, already-tested "no model" neutral fallback — feeding it through
  `get_sizing_multiplier` yields exactly `1.0x`, i.e. item 3's requested
  "neutral 1.0x" outcome, for free, with no new sizing-multiplier code path
  to test.
- Add `_row_features_finite(row) -> bool` (recomputes the vector, checks
  finiteness) and surface it as a new `"features_resolved"` key in
  `score_option_directive`'s returned dict — additive, doesn't change
  `predict_probability`'s float return contract, gives tests (and future
  operators) a clean, unambiguous signal for *why* a neutral score was
  returned.

### 4. `ml/options_meta_labeler.py` — clip the degenerate baseline path

In `predict_probability`, change:
```python
if isinstance(self.model, tuple) and self.model[0] == "baseline":
    return float(self.model[1])
```
to clip into the same `[0.05, 0.95]` band the `linear_fallback` path already
uses, so a degenerate (single-outcome) training run can never report
absolute 0%/100% certainty forever.

### 5. `execution/options_paper_executor.py` — fail closed on a scoring exception

In `execute_strategy_directives`'s ML-gating `try/except` (~line 331-360),
replace the current `except Exception as exc: logger.debug(...)` (falls
through with `contracts` unchanged = full size) with: log at **WARNING**,
append to `skipped` with a clear reason, and `continue` — skip the trade
entirely, matching how a genuine ML rejection is already handled. Chosen
over the "derate to 1.0x" alternative because it's simpler, and a scoring
*exception* (as opposed to a resolved-but-neutral score) is an anomaly worth
sitting out entirely rather than guessing a size for.

### 1. `execution/options_paper_executor.py` — wire real features into `item`

In `get_actionable_directives`, before the per-symbol loop, resolve
`vrp_val` (from the existing `vrp` param, already used a few lines away for
`passes_premium_gate`) and `vix_val` (from `macro_dto.vix`, the existing
`macro_dto` param) once — both `Optional[float]`, `None` when unresolvable,
never silently defaulted. Add two new module-level helpers mirroring
`validation/options_harness.py`'s own training-time formulas exactly (so
serving-time features are measured the same way training-time features
were):
- `_resolve_short_delta(directive) -> Optional[float]`: `abs(directive["Short_Delta"])`,
  `None` on missing/NaN — matches `entry_short_delta`'s
  `abs(_black_scholes_delta(...))` semantics documented in the harness.
- `_resolve_credit_to_width_ratio(directive) -> Optional[float]`:
  `abs(Net_Premium) / abs(Short_Strike - Long_Strike)`, `None` when a strike
  or premium is missing/NaN or width ≈ 0 — matches the harness's
  `entry_credit_to_width_ratio = abs(net_entry_value) / spread_width_dollars`
  (the `*100` contract multiplier on both sides of the harness's ratio
  cancels, so the per-share values here are equivalent).

Add all four (`vrp`, `vix`, `short_delta`, `credit_to_width_ratio`) as
**explicit** keys on the `actionable.append({...})` dict — always present,
`None` when unresolvable — never omitted, so a genuinely-unresolved value is
distinguishable from "this feature was never asked for" and reaches
`OptionsMetaLabeler`'s new finiteness gate rather than silently vanishing
into a stale default.

### 6. In-sample metrics relabeling (minimal fix — chosen over full CPCV)

Full purged/embargoed CV integration for this model is a separate, larger
validation-infrastructure task (would need synthetic/backtest trades
threaded through `CombinatorialPurgedCV`, which nothing in this file does
today) — out of proportion to a bug-fix pass. Minimal fix instead:
- `OptionsMetaLabeler.train()`'s three return-dict branches (sklearn,
  linear-fallback, degenerate-baseline) each gain
  `"metrics_are_in_sample": True`.
- `api/pilots_api.py`'s `get_options_meta_model_status` and
  `post_options_meta_model_retrain` handlers echo `"metrics_are_in_sample": True`
  in their responses (additive keys on existing `Dict[str, Any]` returns —
  no Pydantic response model to update) and their docstrings gain an
  explicit "these are in-sample, optimistic, not held-out" caveat.
- Webapp (`webapp/src/screens/PaperBroker.tsx`): relabel the two metric-tile
  headers "Model Accuracy" → "Model Accuracy (in-sample)" and "ROC-AUC
  Score" → "ROC-AUC Score (in-sample)"; the retrain success toast
  (line ~331) gains the same "(in-sample)" qualifier. No new API fields are
  read by the webapp (avoids touching `types.ts`/`client.ts`/`mock.ts` for a
  field nothing renders) — purely a string change, so no mock/live parity
  risk.

### Webapp field parity for the new `item` keys (small, additive)

`GET /pilots/paper-broker/strategy-options/candidates` serializes
`get_actionable_directives()`'s dicts directly (`Dict[str, Any]`, no strict
response model) — the four new keys will flow through automatically. Add
them as optional fields to `StrategyOptionCandidate` in
`webapp/src/api/types.ts` (`vrp`, `vix`, `short_delta`,
`credit_to_width_ratio`, all `number | null`) and mirror in the
corresponding mock fixture in `webapp/src/api/mock.ts`, for mock/live
parity — no screen currently renders them, so this is type-contract hygiene
only, not a UI change.

## Tests

`tests/test_options_meta_labeler.py`:
- Update `test_feature_vector_extraction` for the 9-column vector (drop the
  `trend_bias` index assertion, shift indices).
- New: a NaN-ivr dict (`ivr: float("nan")`, rest finite) — after training a
  real model — produces `prob_win == 0.65`, `sizing_multiplier == 1.0`,
  `approved is True`, `features_resolved is False` (item 3).
- New: a `None`-valued required feature (e.g. `vix: None`) behaves
  identically to the NaN case (proves the dict-present-but-null path, not
  just missing-key, is caught).
- New: force a degenerate single-class `train()` call, assert
  `predict_probability(...)` on both an extreme-looking good and bad
  candidate is clipped inside `[0.05, 0.95]`, never exactly `0.0`/`1.0`
  (item 4).
- New: assert `train()`'s returned dict carries
  `metrics_are_in_sample: True` for all three branches (sklearn, forced
  linear-fallback via a monkeypatched sklearn import failure, degenerate).

`tests/test_options_paper_executor.py`:
- Extend `test_get_actionable_directives_filters_cash_and_wait`'s
  `mock_directive_pcs` fixture to include the top-level `Short_Strike`,
  `Long_Strike`, `Short_Delta` keys that the REAL `_directive_for_symbol` /
  `build_premium_directive` output actually carries (the existing fixture
  omits them — named by the user as exactly why items 1/3/4 shipped
  undetected). Assert the returned candidate dict's `vrp`, `vix`,
  `short_delta`, `credit_to_width_ratio` are the real resolved values (not
  omitted, not defaulted), by passing a real `vrp=` and a
  `macro_dto` stub/real `MacroEconomicDTO` with a distinctive `.vix` into
  `get_actionable_directives(...)`.
- New: a directive with no short leg (`Short_Delta`/`Short_Strike`/
  `Long_Strike` absent or NaN) yields `short_delta: None`,
  `credit_to_width_ratio: None` — never a fabricated ratio.
- New: force `global_options_meta_labeler.score_option_directive` to raise
  (monkeypatch it to throw) inside `execute_strategy_directives`; assert the
  trade lands in `skipped` (not `executed`) with a reason string mentioning
  the exception/fail-closed behavior, and assert via `caplog` that the log
  line is emitted at `WARNING` (not `DEBUG`).
- New (end-to-end, using the real feature-wiring fix): train a real model
  where win probability depends on `short_delta`/`credit_to_width_ratio`/
  `vix`/`vrp` in a learnable way, run a directive shaped like the "actual
  production item shape" (i.e. without directly passing those keys — let
  `get_actionable_directives` populate them from a realistic `directive`
  dict + `vrp`/`macro_dto` params) through `execute_strategy_directives`,
  and assert the sizing/gating differs from what it would be if those four
  features were still defaulted to their old hardcoded constants — proving
  the real values, not the defaults, drove the decision.

## Docs

- `docs/architecture/ml-and-reports.md`: update the one-line
  `ml/options_meta_labeler.py` entry — drop the "underlying trend" feature
  mention, note the finiteness gate + in-sample-metrics caveat, link the new
  known-issues doc.
- New `docs/known_issues/options_meta_labeler_serving_time_gaps.md`
  following the established template (`options_risk_fabricated_spy_spot.md`
  is the closest recent example): what happened (all 6 items, each with the
  concrete before/after), confirmed-live-impact framing (this is a paper
  gate, so frame as "would apply live-analogous risk if this fed a real
  broker"), the fix, what's still open (trend_bias dropped not replaced;
  no CPCV; `pilots/paper_broker.py`'s on-demand execute path never passes
  `vrp`/`macro_dto` at all today, so `vrp`/`vix` will legitimately read as
  unresolved there until a separate follow-up wires them — noted as a
  disclosed, out-of-scope gap, not silently left undocumented), tests added.
- Add the new doc to `docs/known_issues/README.md`'s index table.

## Delegation

Per user instruction, the core fix implementation is split across 2 parallel
subagents (not done inline by the orchestrating session):
- **Agent 1** owns `ml/options_meta_labeler.py` + `tests/test_options_meta_labeler.py`
  (items 2, 3, 4, and the model-side half of item 6).
- **Agent 2** owns `execution/options_paper_executor.py` +
  `tests/test_options_paper_executor.py` (item 1's wiring fix and item 5).

The orchestrating session handles everything else directly: branch creation,
`api/pilots_api.py`'s item-6 relabeling, the webapp label/type/mock changes,
docs, the known-issues write-up, and the final PR.

## Branch / PR workflow (per CLAUDE.md)

- Branch: `fix-options-meta-labeler-serving-gaps` off current
  `claude/sleepy-ramanujan-06cd48` (already up to date with `origin/main`
  per git status).
- Copy the implementation plan / task tracker / walkthrough into `.claude/`
  under scoped names, e.g. `.claude/options_meta_labeler_fix_implementation_plan.md`,
  `.claude/options_meta_labeler_fix_task.md`,
  `.claude/options_meta_labeler_fix_walkthrough.md`.
- Run targeted tests (`pytest tests/test_options_meta_labeler.py
  tests/test_options_paper_executor.py -q`), then the fuller
  `verify` skill/gate before opening the PR.
- Open PR against `main` with the walkthrough content, ending with the
  required Co-Authored-By / Generated-with footer per harness convention.

## Verification

1. `pytest tests/test_options_meta_labeler.py tests/test_options_paper_executor.py -q`
   — all new + existing tests green.
2. `pytest tests/test_pilots_paper_broker.py tests/test_options_paper_executor.py -q`
   (existing suites touching the same modules) to catch regressions.
3. `npm run --prefix webapp typecheck` (types.ts/mock.ts touched).
4. Full `make ci` / `verify` skill gate before PR.
