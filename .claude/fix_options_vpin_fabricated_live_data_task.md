# Task tracker: fix fabricated live data in `pilots/options_vpin.py`

- [x] Read and understand `pilots/options_vpin.py`'s existing `get_options_vpin_metrics()` bug.
- [x] Confirm no real options-trade tick feed exists (grep `docs/FMP_INTEGRATION.md`,
      `data/fmp_client.py`).
- [x] Find the existing real-data precedent (`desktop/daemon_runtime.py::maybe_update_circuit_breaker`).
- [x] Copy operator's `.env` into this worktree (per explicit operator request) to test against
      real FMP data.
- [x] Verify real hourly bars are fetchable for SPY/QQQ via `get_provider().get_intraday_bars`.
- [x] Implement `fetch_real_underlying_bar_trades()` in `pilots/options_vpin.py`.
- [x] Implement `_unavailable_vpin_metrics()` honest-degradation response shape.
- [x] Rewrite `get_options_vpin_metrics()` to use real bars, degrade honestly on failure.
- [x] Update `get_options_vpin_metrics_for_frontend()` to thread through
      `data_available`/`data_source`/`reason` and rewrite `warning_message` for the unavailable case.
- [x] Verify the fix against real live data for SPY, QQQ, and an invalid symbol.
- [x] Update `webapp/src/api/types.ts` (`VpinMetricsResponse` nullability + new fields).
- [x] Update `webapp/src/components/options/VpinGauge.tsx` (honest unavailable-state UI + data
      source disclosure label).
- [x] Update `webapp/src/api/mock.ts` for parity.
- [x] Add/extend `webapp/src/components/options/VpinGauge.test.tsx`.
- [x] Add/extend `tests/test_options_vpin.py` (fetch helper + live-endpoint honesty tests).
- [x] Update `tests/test_pilots_paper_broker.py::TestOptionsVpinEndpoint` (mock the provider;
      add honest-unavailable HTTP round-trip test).
- [x] Update `docs/architecture/execution.md`'s VPIN entry.
- [x] Write `docs/known_issues/options_vpin_fabricated_live_data.md` and index it in
      `docs/known_issues/README.md`.
- [x] Run `pytest tests/test_options_vpin.py tests/test_pilots_paper_broker.py
      tests/test_daemon_runtime.py tests/test_market_data.py -q -m "not network"` — 376 passed.
- [x] Run `python -m ruff check . --select=F821,F822,F823,E9` — clean.
- [x] Run `npm run --prefix webapp typecheck` — clean.
- [x] Run `npx vitest run src/components/options/VpinGauge.test.tsx` — 7 passed.
- [x] Run the full offline suite (`make ci` / `pytest -m "not network and not slow"`).
- [x] Fix a real regression the first full-suite run caught:
      `tests/test_pilots_strategy_matrix.py`'s dependency-light AST guard needed `"data"` added
      to `options_vpin`'s allowlist (matching the `dispersion_trading`/`zero_dte_engine`/
      `earnings_crush` precedent) for the new lazy `data.market_data` import.
- [x] Identified 3 more failures on the first full-suite run
      (`test_forecast_backfill.py`, `test_portfolio_context.py::test_rag_index_lookback_days_default`,
      `test_sector_selection_review_populated.py`) as caused by the copied `.env` overriding
      real settings defaults the tests pin (e.g. `RAG_INDEX_LOOKBACK_DAYS`), NOT by this change.
      Removed the `.env` copy and `webapp/node_modules` symlink (both gitignored scratch/test
      aids, never meant to be committed) and confirmed all 3 pass clean without them.
- [x] Re-ran the full offline suite clean (no `.env`, no symlink): 11873 passed, 1 pre-existing
      unrelated flaky test (`test_reports_library.py::TestInlineViewToggle::test_hide_button_absent_until_report_opened`,
      passes standalone — an xdist-parallel-only flake, confirmed unrelated to this change).
- [x] Write PR artifacts (this plan/task/walkthrough) with a unique, branch-scoped filename.
- [x] Open PR.
