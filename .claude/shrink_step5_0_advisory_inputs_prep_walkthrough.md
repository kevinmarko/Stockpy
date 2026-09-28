# Step 5.0 walkthrough: advisory input builders moved out of `main.py`

Plan: `.claude/shrink_step5_retire_main_py_implementation_plan.md` (section 4, "5.0: Prep, no behaviour change").
Branch: `step5-advisory-inputs-prep`, cut from `origin/main` @ `df72833d` (already includes PR #1075).

## What moved

`main.py` → `pipeline/advisory_inputs.py`. The code is verbatim. Only the leading underscore was dropped from each name.

| Old name in `main.py` | New name in `pipeline/advisory_inputs.py` |
|---|---|
| `WATCHLIST_FILE` | `WATCHLIST_FILE` |
| `_MACRO_ENGINE_CACHE` | `_MACRO_ENGINE_CACHE` (the same dict object) |
| `_get_macro_engine` | `get_macro_engine` |
| `_reset_macro_engine_cache` | `reset_macro_engine_cache` |
| `_load_watchlist` | `load_watchlist` |
| `_recently_closed_universe_symbols` | `recently_closed_universe_symbols` |
| `_build_universe` | `build_universe` |
| `_build_macro_dto` | `build_macro_dto` |
| `_fetch_bars_for_universe` | `fetch_bars_for_universe` |
| `_fetch_fundamentals_for_universe` | `fetch_fundamentals_for_universe` |
| `_build_realized_vol_60d_map` | `build_realized_vol_60d_map` |
| `_build_context_extras` | `build_context_extras` |

Other details:
- The logger keeps the name `InvestYo.main`, so log output is unchanged. `pipeline/steps.py` does the same.
- One log string still says `_recently_closed_universe_symbols failed`, kept verbatim.
- `main.py` no longer imports `discovery`, `MarketDataProvider`, `FundamentalDataDTO`, `MarketBarDTO`, `global_registry` or `SignalContext`, because only the moved code used them.
- Unused imports that were already there (`config`, `Path`, `PortfolioPosition`, `np`, `pd`) were left alone. Keeping `pandas` keeps the TensorFlow-before-pandas guard's ordering exactly as before.
- The daemon side (`pipeline/production_steps.py`, `desktop/*`, `main_orchestrator.py`) is untouched. Comments there that still call the xsec formula copy "`main.py::_build_context_extras`" are left for PR 5.1.

## Patch targets: what stayed and what moved

`main.py` re-exports every old underscore name, so direct calls such as `main._build_universe(snap)` and `m._get_macro_engine("KEY")` get the moved function. `tests/test_advisory_inputs_patch_seams.py::test_main_reexports_are_the_moved_objects` checks this with identity (`is`).

**The `run_once` injection seams stay on `main`.** `run_once()` is unchanged. It still builds the `RunContext` from `main`'s own globals at call time. So these still intercept, with no test changes:
- `patch("main._build_universe")`
- `patch("main._build_macro_dto")`
- `patch("main._fetch_bars_for_universe")`
- `patch("main._build_context_extras")`
- `patch("main.fetch_account_snapshot")`, `patch("main.get_provider")`, `patch("main.advisory_evaluate")`

**Calls made inside the moved code now patch through `pipeline.advisory_inputs`.** The moved code calls its helpers through its own module globals, for example `build_universe` → `discovery` / `load_watchlist` / `recently_closed_universe_symbols`, `build_macro_dto` → `get_macro_engine` / `get_provider`, and `build_context_extras` → `fetch_fundamentals_for_universe` / `build_realized_vol_60d_map`. I did not route those calls back through `main`. `main.py` is archived in PR 5.5, and the daemon will import the new module directly in 5.1, so `pipeline.advisory_inputs.<name>` is the seam that lasts.

The tests that patched one of these through `main` were retargeted:

| File | Old target | New target |
|---|---|---|
| `tests/test_run_once.py` (autouse fixture) | `"main.discovery"` | `"pipeline.advisory_inputs.discovery"` |
| `tests/test_run_once.py` (HistoricalStore SPY routing test) | `patch("main.get_provider")` | `patch("pipeline.advisory_inputs.get_provider")` |
| `tests/test_run_once.py` (exception-fallback test) | `patch("main._get_macro_engine")` | `patch("pipeline.advisory_inputs.get_macro_engine")` |
| `tests/test_universe_retention.py` (autouse fixture) | `"main.discovery"` | `"pipeline.advisory_inputs.discovery"` |
| `tests/test_universe_retention.py` (4 sites) | `"main._recently_closed_universe_symbols"` | `"pipeline.advisory_inputs.recently_closed_universe_symbols"` |
| `tests/test_pipeline_smoke.py` (2 fixtures) | `setattr(m, "WATCHLIST_FILE" / "discovery")` | `setattr(pipeline.advisory_inputs, ...)` |
| `tests/test_progress_emission.py` (fixture) | `setattr(m, "WATCHLIST_FILE")` | `setattr(pipeline.advisory_inputs, "WATCHLIST_FILE")` |

These retargets are required, not cosmetic. Left on `main`, several would have passed while no longer isolating anything:
- The discovery fixture would read the operator's real `scan_candidates.json`.
- The `_get_macro_engine` fallback test would build a real `DataEngine`.

A stale `main.discovery` patch now raises instead of silently doing nothing, because `main` no longer has that attribute.

The following were left unchanged because they genuinely still intercept:
- `mock.patch("main.logger.warning")` in `tests/test_main_multifactor_precompute.py`: it is the same `InvestYo.main` Logger object.
- `patch("main.settings.X")` / `m.settings`: it is the same `settings` singleton.
- `test_main.py`'s `monkeypatch.setattr("main.settings", ...)`: `_read_macro_snapshot_hint` stayed in `main`.

**Guard.** `tests/test_advisory_inputs_patch_seams.py::test_no_test_patches_an_internal_helper_through_main` scans every test file. It fails if one of the internal-only names is patched through `main`, in either form: a `"main.X"` string, or `setattr(<main alias>, "X")` / `patch.object`. To check it works, I temporarily reintroduced a `"main.discovery"` patch and a `setattr(m, "WATCHLIST_FILE")` patch. Both were flagged, and I then reverted them.

## The golden

`tests/test_run_once_advisory_golden.py` has three fixtures in `tests/fixtures/run_once_advisory_golden/`. They are JSON files named `*.golden`, because the repo `.gitignore` ignores `*.json`.

**Real code in the run.** Everything in `main.run_once()` runs for real:
- the universe, the macro DTO, the bars/fundamentals pre-fetch, the realized-vol map, and the xsec/multifactor/CoVaR/excursion pre-compute;
- `engine.advisory.evaluate()`: technicals, GJR-GARCH, StrategyEngine, the Case A/B/C overlay, and Kelly sizing.

**Frozen inputs.** Every patch target is either a `run_once` seam on `main` or a defining module that the moved code imports lazily. So the golden needed no edits across the move.
- **Account snapshot:** a real `AccountSnapshot` holding AAPL (at a gain), MSFT and KO (at a loss, with dividends).
- **Universe:**
  - `WATCHLIST=NVDA`;
  - a real `watchlist.txt` in a tmp CWD containing JNJ;
  - a real `scan_candidates.json` in a tmp `OUTPUT_DIR` containing XOM;
  - retention via `data.broker_fills_store.recently_closed_symbols` → INTC (AAPL is filtered out because it is held);
  - `DEFAULT_TICKERS=["SPY"]` (not reached);
  - rating auto-drop off.
- **Market provider:** a fake with seeded 320-day bars on fixed business dates ending 2026-07-16, fixed quotes and fixed fundamentals.
- **Macro:** fake `data_engine.DataEngine` / `macro_engine.MacroEngine` classes, so the real `build_macro_dto` runs. `HISTORICAL_STORE_ENABLED=False`.
- **Forecast:** a fake ForecastingEngine returning a fixed 30-day multiple per symbol. The real ensemble is too slow and not reproducible for a unit golden.
- **Models:** no LGBM ranker model, and meta-labeler bootstrap is a no-op.
- **Trade history:** an in-memory TransactionsStore holding one closed MSFT trade.
- **Execution settings:** `ADVISORY_MAX_CONCURRENCY=1`, kill-switch files in tmp, `ROBINHOOD_EXECUTION_MODE=review`.
- **Network:** `socket.connect` and `create_connection` raise.

**What is pinned.**
- Per symbol: `action`, `conviction`, `suggested_position_pct`, `suggested_exit_pct`, `rationale` and the full `key_indicators` dict. `key_indicators` carries what the pre-compute produced for that symbol: `xsec_12_1m`, `xsec_momentum_rank`, the multifactor z-scores, `covar_proxy`, and the MFE/MAE/edge/slippage fields.
- The bytes of `queue_sources/advisory.json` and `execution_queue.json`. These are written exactly as `_run_cycle` writes them (`write_advisory_source` then `compose_and_emit(result.snapshot, macro_dto=result.macro_dto)`), with only `now` and `output_dir` pinned.

**It is not vacuous.** `test_golden_is_not_vacuous` checks four things:
- all 7 universe sources appear;
- both BUY and SELL occur (KO triggers Case A, SELL 0.80 with a full exit; three BUYs are lifted to 0.85 by the bullish-forecast boost);
- the xsec, multifactor and CoVaR fields are finite for every symbol, and MSFT's excursion is finite;
- the queue has intents: AAPL, NVDA and XOM buys, all passing the gate.

**Mutation checks** (temporary edits to `pipeline/advisory_inputs.py`, each reverted):

| Temporary edit | Did the golden fail? |
|---|---|
| VIX +20 in `build_macro_dto` | Yes |
| Dropping the recently-closed union | Yes |
| xsec `SKIP_DAYS` 22→21, before widening | No: action/conviction/rationale/queue bytes unchanged |
| xsec `SKIP_DAYS` 22→21, after widening to `key_indicators` | Yes (`xsec_12_1m` moves) |
| Reversing `build_context_extras`'s own `XSec_Momentum_Rank` series | No, and correctly so |

The last row is a genuine no-op. `signals/cross_sectional_momentum.py::pre_compute` re-ranks from the `XSec_12_1M` column itself and never reads that column, so no output changes.

**Capture order.**
- Commit `e69d8042` captured the golden on the pre-move code.
- The first mutation checks showed the recommendation fields alone are not very sensitive to the pre-compute. So `714183e1` widened the golden to `key_indicators` and a closed trade. It was regenerated and committed on the pre-move code too: I checked out the golden commit, extended it, and committed it before re-applying the move.
- The move commit comes after both.

**Determinism.** The golden passed 3 times in a row on the pre-move code and 3 times after the move. It also passed under `-n 3` (xdist) mixed with `test_run_once`/`test_pipeline_smoke`, and in random order (pytest-randomly on).

## Verification

- **Named suites after the move**, all passing:

  | Test file | Passed |
  |---|---|
  | `test_run_once` | 45 |
  | `test_universe_retention` | 13 |
  | `test_main_multifactor_precompute` | 11 |
  | `test_xsec_momentum_advisory_parity` | 2 |
  | `test_advisory_pause_gate` | 22 |
  | `test_progress_emission` | 11 |
  | `test_pipeline_smoke` | 12 |
  | `test_main` | 5 |
  | `test_main_report_real_macro` | 1 |
  | `test_reporting_package` | 3 |
  | `test_advisory_inputs_patch_seams` (new) | 3 |
  | the golden | 2 |

- **Full offline suite:** `LOCAL_DATA_ROOT=<scratch> NO_VENV_REEXEC=1 pytest -m "not network and not slow" -n auto --dist loadgroup -q -p no:randomly`. Results are in the final report.
  - The first run caught `tests/test_no_missing_call_timeouts.py`, which allowlists `("main.py", 68)`, the venv re-exec `_sp.call`. My docstring edit had shifted that line to 69. I fixed it by keeping the docstring the same line count, not by editing the allowlist.
- **Ruff:** `ruff check --select=F821,F822,F823,E9 main.py pipeline/advisory_inputs.py tests/...` is clean.
- **Census regeneration:** I regenerated `scripts/settings_liveness.py --write` and `scripts/measure_settings_census.py --write`. The only change is the scanned-file count (+1 for the new module) and the commit hash.

## Not done / follow-ups

- No live run. PR 5.0 has no runtime change.
- The xsec-parity comments in `pipeline/production_steps.py` / `main_orchestrator.py` still name `main.py::_build_context_extras` as the third copy. That is daemon-side and deliberately out of scope; update it in 5.1.
