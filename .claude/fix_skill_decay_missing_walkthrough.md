# Walkthrough: Forecast-skill decay_pct fix

## What was wrong

`investyo_mcp_server.py::get_model_drift_report` (~line 5141) is documented
as reporting "forecast-skill decay" and reads a `decay_pct` field from each
row of `pilots.observability.forecast_skill_by_symbol_summary`'s output:

```python
decay = r.get("decay_pct")
decay_str = f"{decay:.1f}%" if isinstance(decay, (int, float)) else "—"
```

But `forecast_skill_by_symbol_summary` (~line 819) never set that key — each
row only ever carried `symbol`, `pending`, `completed`, `skill_weights`,
`n_by_model`. Every drift report silently rendered `"—"` for every symbol,
for every user, always. A repo-wide `grep -rniE decay` (excluding `.venv`)
confirmed this wasn't a regression from a port — no "skill decay" formula
existed anywhere in the codebase, including the legacy Streamlit panel
(`gui/panels/observability.py::_render_observability_forecast_skill`/
`_forecast_skill_rows`) this module's own docstring says it reproduces. The
metric was simply never built.

## Prior-art check

Per this repo's reuse-over-reinvention convention, the legacy panel was read
in full first. It computes per-model RMSE and the live inverse-RMSE
skill-weight blend — nothing that compares skill across time. No formula to
reuse existed. This is a from-scratch implementation, built out of the one
piece of existing machinery worth reusing:
`forecasting.forecast_tracker._MIN_RMSE` (the floor
`compute_skill_weights_from_stats` uses to avoid an infinite weight when a
model's RMSE is near zero) — imported directly rather than re-hardcoded as a
second `0.01` literal, so the two formulas can't silently drift apart the
way this codebase's Kelly-sizing formula once did in two places at once.

## The fix

Two new functions in `pilots/observability.py`:

- **`_skill_from_pooled_stats(n, mse, min_obs)`** — a pure function
  returning `1.0 / max(sqrt(mse), _MIN_RMSE)` (the same inverse-RMSE piece
  used per-model elsewhere), or `None` when `n < min_obs` or `mse` is
  missing/negative.
- **`_forecast_decay_stats_by_symbol(db_path, symbols, horizon_days,
  window_days, min_obs)`** — one bulk SQL query per call (not per symbol)
  that pools all models together and splits `window_days` into a recent
  half and an older baseline half via a `CASE WHEN forecast_ts >= <mid> THEN
  'recent' ELSE 'baseline'` group. For each symbol:

  ```
  decay_pct = (baseline_skill - recent_skill) / baseline_skill * 100
  ```

  Positive means skill is degrading (recent RMSE worse than baseline);
  negative means it's improving. `baseline_skill` is always `> 0` when not
  `None` (the `_MIN_RMSE` floor forbids exactly zero), so this never divides
  by zero.

`forecast_skill_by_symbol_summary` now calls this alongside the existing
`_forecast_stats_by_symbol` call and adds `decay_pct`/`decay_reason` to
every row. `get_model_drift_report` was updated to show the honest
`decay_reason` inline (`"— (reason)"`) instead of a bare dash when
`decay_pct` is `None`.

## Judgment calls to sanity-check

1. **Window split is an even 50/50 halve of the existing `window_days`**,
   not a new independently-tunable knob. Simple and defensible, but
   arbitrary — a human might prefer e.g. a fixed "most recent 30 days" vs.
   "prior 30 days" regardless of `window_days`.
2. **Pooled across models, not per-model.** `decay_pct` is one
   symbol-level headline number. An alternative design would decay each
   model separately (mirroring `skill_weights`'s shape) — rejected here as
   unnecessary complexity for a first cut; `skill_weights` on the same row
   already answers "which model," this answers "is this symbol's forecast
   quality getting worse."
3. **No portfolio-wide decay figure.** Checked `portfolio_forecast_skill`'s
   only consumer (`observability_summary`, feeding the PWA Mission Control
   screen) — nothing downstream reads a portfolio-level decay number today.
   Only `forecast_skill_by_symbol_summary` feeds `get_model_drift_report`,
   so this stayed per-symbol only rather than adding an unused field.

## Test changes

`tests/test_observability_skill_decay.py` (new, 16 tests):
`TestSkillFromPooledStats` (pure-function edge cases including the
`_MIN_RMSE` floor), `TestForecastDecayStatsBySymbol` (the three required
cases — recent-worse/positive, recent-better/negative,
insufficient-history-either-direction/`None`+reason — plus pooling-across-
models, empty-input, no-rows, and DB-failure dead-lettering), and
`TestForecastSkillBySymbolSummaryDecayIntegration` (the same cases exercised
through the public `forecast_skill_by_symbol_summary` entry point, including
a symbol with real history for ANOTHER symbol only resolving to an honest
`None` rather than a `KeyError` or fabricated `0.0`).

Two pre-existing tests needed updates because the row shape changed:

- `tests/test_pilots_observability.py
  ::TestForecastSkillBySymbol::test_warm_path_computes_independent_weights_per_symbol`
  had an exact-dict-equality assertion on a zero-history row; added the new
  `decay_pct: None`/`decay_reason: mock.ANY` keys.
- `tests/test_investyo_mcp_widgets.py
  ::test_get_model_drift_report_emits_json_matching_widget_schema` had a
  comment and assertion stating "no such field is ever computed by
  forecast_skill_by_symbol_summary" — now false. Updated to assert
  `decay_pct` is correctly relayed through when present (kept the
  still-true `drift_detected`-absence assertion).

## Verification

- `pytest tests/test_observability_skill_decay.py
  tests/test_pilots_observability.py tests/test_investyo_mcp_widgets.py
  tests/test_investyo_mcp_server.py::TestGetModelDriftReport -q` —
  **166 passed, 3 failed.**
- The 3 failures
  (`test_plot_equity_curve_emits_json_payload_matching_widget_schema`,
  `test_propose_paper_trade_emits_json_matching_widget_schema`,
  `test_run_backtest_emits_json_matching_widget_schema`) are unrelated to
  this diff: a numba cache-locator `RuntimeError` under the sandboxed
  `.venv` (`vectorbt`'s `set_seed_nb`) and a
  `sqlite3.OperationalError: attempt to write a readonly database` on an
  unrelated `rlhf_calib*` table. Confirmed pre-existing via `git stash` +
  isolated reruns before any of this change existed.
- Rebased onto `origin/main` (10 unrelated upstream commits — a
  fundamentals-deadline fix, a pipeline-timeout fix, a module-efficiency
  audit doc, a `universe_engine.py` `iterrows` optimization) — clean, no
  conflicts, diff unchanged. Re-ran the touched/added test files again
  post-rebase: 96 passed, same result.

## Documentation

`docs/architecture/observability-and-apis.md`'s existing
`pilots/observability.py` bullet gained a dated note describing the gap,
the fix, the formula, the per-symbol-only scope decision, and the new test
file — following that bullet's own established pattern of documenting each
fix pass inline rather than as a separate doc.
