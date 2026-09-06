# Walkthrough: forecast-fallback disclosure

## Which path was taken, and why

**Path (a): the original design (`current_price`, never `NaN`) is correct and was NOT
changed.** What was missing was disclosure — a way for a downstream reader to tell "a real
model produced this price" apart from "every model failed and `_blend_with_skill` fell back
to today's real price." That gap is now closed with a new boolean flag threaded end-to-end.

This was a genuine investigation, not a rubber-stamp of the status quo:

1. **Confirmed the scenario is real, not hypothetical.** Monte Carlo (unconditional — no
   settings flag disables it) succeeds under essentially every normal condition given a
   positive `current_price` (a GBM terminal price is always positive). The only way
   `model_forecasts` ends up genuinely empty is `mu`/`sigma` themselves coming back `NaN`,
   which requires `history_series` to have ≤1 valid observation after `log_returns.dropna()`
   — e.g. a brand-new listing or a badly degraded per-symbol historical fetch. Reproduced this
   exactly in `tests/test_forecasting_engine.py::TestGenerateForecast::test_thin_history_falls_back_to_current_price_and_discloses_it`.
2. **Confirmed `current_price` is indistinguishable from a real forecast downstream** — no
   flag, no confidence field, no `model_used` marker existed anywhere before this change.
3. **Confirmed `NaN` would make things worse, not neutral**, by reading the actual consumers:
   - `pipeline/production_steps.py`: `vec_df['forecast_price'] = dashboard_df.get('Forecast_30', ...).fillna(0.0).values`
     — a `NaN` `Forecast_30` becomes a literal fabricated `$0` price target fed into
     cross-sectional scoring. That is a *worse* CONSTRAINT #4 violation than reusing a real
     observed price (it actively claims "predicted to go to zero" instead of "no model ran").
   - The per-ticker path (`se.evaluate_security(forecast_price=row.get('Forecast_30', 0.0))`)
     has no established NaN-handling contract for `forecast_price` — a raw `NaN` would flow
     straight into scoring arithmetic with unverified consequences.
   - Changing `_blend_with_skill`'s return value to `NaN` would therefore have required
     auditing and fixing BOTH of those consumer sites too, which was never part of the
     original ask and is a materially larger, riskier change to runtime scoring logic than
     adding a diagnostic field.
4. **Decision: keep `current_price`; add `Forecast_{h}_Is_Fallback` disclosure instead.**

The full write-up, including a disclosed-but-unfixed adjacent bug found while reading
`_blend_with_skill`'s static-blend `elif` chain, lives in
`docs/known_issues/forecast_fallback_current_price_disclosure.md`.

## Exactly what changed

| File | Change |
|---|---|
| `forecasting_engine.py` | `generate_forecast()` sets `results[f'Forecast_{h}_Is_Fallback'] = not bool(model_forecasts)` for each horizon. `_blend_with_skill()` itself is untouched — same signature, same behavior, same existing test. |
| `pipeline/production_steps.py` | `ForecastingStep.forecast_cols` gains the four new keys (extra `dashboard_df` columns, deliberately not in `config.COLUMN_SCHEMA`). The `except`-branch Monte-Carlo recovery marks all four `True`. `_eval_one`'s `ADVISORY_REUSE_PIPELINE_COMPUTE` selection block now also extracts `Forecast_30_Is_Fallback` (trusting only an actual `bool`) and threads it into `evaluate(precomputed_forecast_is_fallback=...)`. |
| `engine/advisory.py` | New `precomputed_forecast_is_fallback: Optional[bool] = None` kwarg on `evaluate()`. `key_indicators["forecast_is_fallback"]` is `1.0`/`0.0`/`NaN` (same float-encoding convention as `kelly_raw_was_capped`), sourced from the fresh-fit `fc_results` or the precomputed kwarg. |
| `reporting/state_snapshot.py` | Advisory-path `state_snapshot.json` writer emits `"forecast_is_fallback": _safe_float_or_none(ki.get("forecast_is_fallback"))`. |
| `main_orchestrator.py` | Orchestrator-path `_write_state_snapshot` emits the same key, sourced from `row.get("Forecast_30_Is_Fallback")`. |
| `docs/known_issues/forecast_fallback_current_price_disclosure.md` | New — the full investigation write-up. |
| `docs/known_issues/README.md` | New index row. |
| `docs/architecture/signal-engines.md` | Addendum to the `forecasting_engine.py` bullet. |

## Tests added/extended

- `tests/test_forecasting_engine.py::TestGenerateForecast` — two new tests: real history
  discloses `False` on every horizon; single-point history discloses `True` on every horizon
  and `Forecast_{h}` equals `current_price` exactly.
- `tests/test_forecasting_step_fallback_disclosure.py` (new file) — `ForecastingStep`'s
  writeback: happy path per-horizon round-trip (including a mixed True/False case within one
  ticker), a dead-lettered (zero-price) row stays `NaN` not `False`, and the exception-recovery
  path marks every horizon `True`.
- `tests/test_advisory.py::TestForecastIsFallbackKeyIndicator` (new class) — fresh-fit
  True/False/absent-key encode to `1.0`/`0.0`/`NaN`; precomputed-reuse with/without the new
  kwarg.
- `tests/test_advisory_dedup_wiring.py` — extended the byte-for-byte `_eval_one` reproduction
  to the new third value, including the NaN-cell-must-not-read-as-True guard.
- `tests/test_state_snapshot_parity.py::TestForecastIsFallbackParity` (new class) — both
  writers round-trip `True`/`False`/absent identically; added to `SHARED_SIGNAL_FIELDS`.

## Verification

**Environment note**: this sandbox's `.venv` hits a numba caching `RuntimeError` on any
import that pulls in `pandas_ta`/`pandas_ta_classic` (confirmed pre-existing and unrelated to
this change — reproduced identically on an untouched `origin/main` checkout) unless
`NUMBA_CACHE_DIR` is pointed at a writable directory. All commands below were run with
`NUMBA_CACHE_DIR` set; this is an environment quirk, not a code fix.

```
$ python -m ruff check . --select=F821,F822,F823,E9
All checks passed!
```

```
$ pytest tests/test_forecasting_engine.py tests/test_forecast_tracker.py \
    tests/test_forecasting_improvements.py tests/test_advisory.py \
    tests/test_advisory_dedup_wiring.py tests/test_state_snapshot_parity.py \
    tests/test_production_steps_columns_contract.py \
    tests/test_forecasting_step_fallback_disclosure.py -q
260 passed, 76 warnings in 24.12s
```

Full offline suite (`pytest -m "not network and not slow" -n 4 --dist loadgroup`, mirrors
`make ci` other than worker count):

```
==== 10 failed, 12862 passed, 16 skipped, 920 warnings in 400.28s (0:06:40) ====
```

All 10 failures are pre-existing and environmental (this sandbox restricts raw socket
binding and writes to some SQLite paths outside the allowlisted directories) — **verified,
not assumed**: the identical 10 tests were re-run against a fresh, untouched `origin/main`
checkout and fail with the exact same errors:

| Test | Real cause |
|---|---|
| `test_net_util.py::TestFindFreePort` (×3) | `PermissionError: [Errno 1] Operation not permitted` on `socket.bind()` — sandbox blocks raw socket binding |
| `test_data_engine_macro_history.py` (×3) | Same `socket.bind()` `PermissionError`, inside a test harness that opens a real local "black hole" server |
| `test_alpaca_http.py::TestMountTimeoutAdapterEndToEnd` (×2) | Same class of real-socket test, same sandbox restriction |
| `test_investyo_mcp_widgets.py::test_propose_paper_trade_emits_json_matching_widget_schema` | `sqlite3.OperationalError: attempt to write a readonly database` — sandbox filesystem write restriction on that DB path |
| `test_command_execution.py::test_non_command_job_has_no_command_name` | `assert 403 == 200` — an auth/env-config difference in this sandboxed run, unrelated to forecasting/advisory code |

None of the 10 touch `forecasting_engine.py`, `pipeline/production_steps.py`,
`engine/advisory.py`, `reporting/state_snapshot.py`, or `main_orchestrator.py` — the five
files this change edits. Zero regressions introduced.

## Scope explicitly not covered

- `config.COLUMN_SCHEMA` / Google Sheets / HTML report / webapp UI. See the known-issues doc's
  "Explicitly out of scope" section.
- The adjacent Holt-Winters-only static-blend gap found during investigation (disclosed, not
  fixed — different bug, requires Monte Carlo to fail while Holt-Winters succeeds, a narrow
  and not-yet-reproduced case).

## PR

<!-- PR_URL_PLACEHOLDER -->
