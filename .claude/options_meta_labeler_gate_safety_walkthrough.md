# Walkthrough: fix six compounding gate-safety bugs in the Stage 4 Options ML Meta-Labeler

## Summary

A comprehensive audit of `ml/options_meta_labeler.py` +
`execution/options_paper_executor.py` + `api/pilots_api.py`'s retrain endpoint
found six independent bugs in the Stage 4 ML meta-labeler that gates and sizes
automated options paper trades. The gate is genuinely load-bearing when its
inputs and execution are clean — the problem is entirely in what happens when
they *aren't*: every one of the six failure modes made the gate **more
permissive**, never more conservative, which is backwards for a risk gate and
a direct violation of this repo's CONSTRAINT #6 ("a failure must never
silently relax a risk limit or a gate to keep going"). This PR fixes all six,
adds regression tests against the model's *actual* production input shape
(rather than the fully-populated fixtures the existing tests used), and
documents the finding per this repo's `docs/known_issues/` convention.

Full detail: [`docs/known_issues/options_meta_labeler_serving_time_gaps.md`](../docs/known_issues/options_meta_labeler_serving_time_gaps.md).

## The six bugs and their fixes

1. **4 of 10 model features were always hardcoded constants at live
   prediction time** (`vrp`/`vix`/`credit_to_width_ratio`/`short_delta` were
   never copied into `get_actionable_directives`'s `item` dict, silently
   triggering the model's defaults on every real prediction) — fixed by
   wiring all four from the real `vrp`/`macro_dto` parameters and the
   directive's own `Short_Strike`/`Long_Strike`/`Short_Delta`/`Net_Premium`
   fields, always explicit (`None` when unresolvable, never omitted).
2. **`trend_bias` meant a different thing at train time (a pure function of
   strategy name) vs. serve time (a real Aroon/Coppock signal)** — dropped
   from the model's feature vector entirely (10 → 9 columns) rather than
   made to match; a genuine historical trend-at-entry-date reconstruction
   would need price history threaded into `validation/options_harness.py`,
   which is a separate, larger task.
3. **An unresolvable required feature (present but NaN) reached the model as
   a normal value and produced a confident, INCREASED prediction** — fixed
   via two new helpers (`_resolve_numeric_feature`/`_finite_or_nan`) that
   propagate `NaN` for an explicitly-unresolved value instead of silently
   defaulting, plus a finiteness gate in `predict_probability` that declines
   to score (neutral `0.65`/`1.0x`) before ever calling the model. Surfaced
   via a new `features_resolved` key on `score_option_directive`'s response.
4. **A degenerate single-outcome training run produced unclipped 100%/0%
   confidence forever** — the `"baseline"` branch in `predict_probability`
   now clips to `[0.05, 0.95]`, matching the other two model branches.
5. **Any scoring exception silently failed OPEN to full un-gated trade size,
   logged only at DEBUG** — the exception handler in
   `execute_strategy_directives` now fails CLOSED (skips the trade, logs at
   WARNING).
6. **`train()` reported in-sample metrics with no disclosure** — minimal fix
   (full CPCV integration is a separate, larger validation-infrastructure
   task): `train()` and both `api/pilots_api.py` meta-model endpoints now
   echo `metrics_are_in_sample: True`; the Pilots PWA relabels the two
   metric tiles and the retrain toast with "(in-sample)".

## Files changed

- `ml/options_meta_labeler.py` — items 2, 3, 4, and the model-side half of
  item 6.
- `execution/options_paper_executor.py` — item 1's wiring fix and item 5.
- `api/pilots_api.py` — item 6's API-side echo + docstring caveats.
- `webapp/src/screens/PaperBroker.tsx`, `webapp/src/api/types.ts`,
  `webapp/src/api/mock.ts` — item 6's UI relabeling + mock/live type parity
  for the four new candidate fields.
- `tests/test_options_meta_labeler.py`, `tests/test_options_paper_executor.py`
  — regression coverage for all six items, including tests built against the
  actual production `item`-dict shape (the prior fixtures' fully-populated,
  always-finite shape is exactly why items 1/3/4 shipped undetected).
- `docs/architecture/ml-and-reports.md`, new
  `docs/known_issues/options_meta_labeler_serving_time_gaps.md`,
  `docs/known_issues/README.md` — documentation.
- `docs/settings_field_census.json`/`.md`, `docs/settings_liveness.json` —
  regenerated (both artifacts went stale purely from line-number shifts in
  `api/pilots_api.py`'s docstring additions; both tests' own messages
  document this as expected on an otherwise-unrelated PR).

## What's still open (disclosed, not silently left)

- `pilots/paper_broker.py`'s on-demand execute path
  (`GET/POST /pilots/paper-broker/strategy-options/*`) never passes
  `vrp`/`macro_dto` into `get_actionable_directives()` at all — only
  `main.py`'s automated cycle-level call supplies a real `macro_dto`. This
  means `vrp`/`vix` legitimately read as unresolved on every request through
  that on-demand path today, which now degrades safely (neutral `1.0x`) but
  is still incomplete. Wiring real values into that call site is a
  follow-up.
- No CPCV/purged evaluation exists yet for this model — `metrics_are_in_sample`
  discloses the gap honestly, it does not close it.
- `trend_bias` was dropped, not replaced with a genuine train-time trend
  signal.

## Verification

- `pytest tests/test_options_meta_labeler.py tests/test_options_paper_executor.py
  tests/test_pilots_paper_broker.py tests/test_pilots_api.py
  tests/test_options_harness.py tests/test_options_queue_builder.py` → **668
  passed**.
- Full offline suite (`pytest -m "not network and not slow"`) → **12002
  passed, 31 skipped, 5 failed** — all 5 failures are pre-existing,
  unrelated to this change (missing `openai`/`google-genai` packages in this
  sandbox; confirmed identical on a clean checkout via `git stash`).
- `ruff check . --select=F821,F822,F823,E9` → clean.
- `npm run --prefix webapp typecheck` → clean.
- `vitest run src/screens/PaperBroker.test.tsx` → 22 passed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
