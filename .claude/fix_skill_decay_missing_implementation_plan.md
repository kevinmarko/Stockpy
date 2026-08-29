# Implementation Plan: Forecast-skill decay_pct fix

Branch: `fix-skill-decay-missing`
Source: confirmed gap — `investyo_mcp_server.py::get_model_drift_report` reads
a `decay_pct` field from each row of
`pilots.observability.forecast_skill_by_symbol_summary`'s output that was
never actually computed anywhere in the codebase.

## Scope

`get_model_drift_report` (~line 5141) reads `r.get("decay_pct")` from each
row and renders `"—"` whenever it's missing/non-numeric. It always was
missing: `forecast_skill_by_symbol_summary` (~line 819) builds each row with
only `symbol`, `pending`, `completed`, `skill_weights`, `n_by_model` — never
`decay_pct`. `grep -rniE decay` across the whole repo confirmed no "skill
decay" formula exists anywhere, including the legacy Streamlit panel this
module was ported from (`gui/panels/observability.py
::_render_observability_forecast_skill`/`_forecast_skill_rows`) — so this
metric was never implemented, not merely dropped in a port.

## Prior-art check (done first, per this repo's reuse-over-reinvention rule)

- `grep -rniE decay` over the whole repo (excluding `.venv`): every hit is
  either option-theta decay (unrelated), a docstring claiming this tool
  reports "skill decay" (the bug itself), or the pre-existing test asserting
  `decay_pct` is absent (also part of the bug's documentation).
- Read `gui/panels/observability.py`'s `_forecast_skill_rows`/
  `_render_observability_forecast_skill` in full — no decay logic; it only
  ever computed per-model RMSE/skill-weight, same as the ported module.
- Conclusion: no existing formula to reuse. Implemented from scratch,
  reusing the existing inverse-RMSE building block
  (`forecasting.forecast_tracker._MIN_RMSE`) rather than a new constant.

## Design decisions

- **Formula**: `decay_pct = (baseline_skill - recent_skill) / baseline_skill
  * 100`, positive = skill degrading. `baseline_skill`/`recent_skill` are
  each `1.0 / max(rmse, _MIN_RMSE)` over a pooled (all-models-combined) MSE
  for that sub-window — the SAME inverse-RMSE piece
  `compute_skill_weights_from_stats` uses per model, imported rather than
  re-hardcoded (`_MIN_RMSE`), so the two formulas cannot silently drift
  apart the way this codebase's Kelly-sizing formula once did.
- **Window split**: the existing `window_days` argument is split in half —
  the most recent half is "recent," the older half is "baseline" — rather
  than introducing a second, independently-tunable knob. A product/human
  call: this is the simplest defensible split, not a tuned decision.
- **Pooled across models, not per-model**: `decay_pct` is a single
  symbol-level headline number. `skill_weights` (already on the row)
  carries the per-model breakdown. A per-model decay signal was considered
  and rejected as unnecessary complexity for a first cut.
- **CONSTRAINT #4 (never fabricate)**: a symbol without at least `min_obs`
  pooled completed forecasts in BOTH sub-windows gets `decay_pct: None`
  plus an honest `decay_reason` string — never a fabricated number.
  `baseline_skill` is always `> 0` when not `None` (the `_MIN_RMSE` floor
  forbids zero), so there is no division-by-zero path.
- **CONSTRAINT #6 (fail closed)**: the new `_forecast_decay_stats_by_symbol`
  helper never raises — any DB/query failure degrades every requested
  symbol to the same honest `None` + reason, matching this file's existing
  `except Exception` dead-letter convention.
- **Portfolio-wide decay — deliberately NOT added.** Checked
  `portfolio_forecast_skill`'s only consumer (`observability_summary` →
  the PWA Mission Control screen) — nothing downstream reads a
  portfolio-level decay figure today; only `forecast_skill_by_symbol_summary`
  feeds `get_model_drift_report`. Per-symbol only, stated explicitly rather
  than silently scoped down.

## Files touched

- `pilots/observability.py` — new `_skill_from_pooled_stats` (pure helper)
  and `_forecast_decay_stats_by_symbol` (bulk SQL aggregate); wired
  `decay_pct`/`decay_reason` into every row of
  `forecast_skill_by_symbol_summary`.
- `investyo_mcp_server.py::get_model_drift_report` — surfaces
  `decay_reason` inline (`"— (reason)"`) when `decay_pct` is `None`,
  instead of a bare dash.
- `tests/test_observability_skill_decay.py` (new) — 16 tests: (a) recent
  error worse than baseline → positive decay; (b) recent error better →
  negative decay; (c) insufficient history in either sub-window → `None` +
  honest reason (both directions); pooled-across-models behavior; DB-failure
  dead-lettering; empty-symbol-list; pure-function edge cases including the
  `_MIN_RMSE` floor.
- `tests/test_pilots_observability.py` — one pre-existing exact-dict
  assertion (`TestForecastSkillBySymbol
  ::test_warm_path_computes_independent_weights_per_symbol`) updated for
  the new row shape (`decay_pct`/`decay_reason` keys now present).
- `tests/test_investyo_mcp_widgets.py` — the existing
  `test_get_model_drift_report_emits_json_matching_widget_schema` test's
  stale comment ("no such field is ever computed") and absence-assertion
  were factually wrong after this fix; updated to assert `decay_pct` is
  now correctly relayed through, while keeping the (still true)
  `drift_detected`-absence assertion.
- `docs/architecture/observability-and-apis.md` — new dated note on the
  `pilots/observability.py` bullet describing the gap and the fix.

## Verification

- `pytest tests/test_observability_skill_decay.py tests/test_pilots_observability.py tests/test_investyo_mcp_widgets.py tests/test_investyo_mcp_server.py::TestGetModelDriftReport -q`
  — 166 passed, 3 failed. The 3 failures
  (`test_plot_equity_curve_emits_json_payload_matching_widget_schema`,
  `test_propose_paper_trade_emits_json_matching_widget_schema`,
  `test_run_backtest_emits_json_matching_widget_schema`) are confirmed
  pre-existing sandbox-environment artifacts unrelated to this diff
  (numba cache-locator `RuntimeError` under the sandboxed `.venv`, and a
  `sqlite3.OperationalError: attempt to write a readonly database` on an
  unrelated table) — reproduced identically via `git stash`/isolated
  reruns before this change existed.
- Re-ran the full touched-file suite again after rebasing onto
  `origin/main` (10 unrelated upstream commits) — same result, no new
  failures, no conflicts.
