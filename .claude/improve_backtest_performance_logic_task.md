# Task Tracker: Backtest Validation & Documentation Refresh

- [x] 1. Run full strategy validation fleet (`scripts/refresh_validations.py`) across all 28 strategies <!-- id: 1 -->
- [x] 2. Verify output summary JSONs, HTML reports, and history JSONL files in `reports/` <!-- id: 2 -->
- [x] 3. Update `docs/VALIDATION_STRATEGY_FIX_LOG.md` with full 2026-08 rollup table and metrics <!-- id: 3 -->
- [x] 4. Update corresponding signal markdown documents in `docs/signals/*.md` <!-- id: 4 -->
- [x] 5. Run verification tests & preflight validation check <!-- id: 5 -->
- [x] 6. Commit plan, task tracker, and walkthrough to `.claude/` directory <!-- id: 6 -->
- [x] 7. Rebase onto current `main` (branch had drifted 36 commits stale) and resolve the resulting
      conflicts in `docs/VALIDATION_STRATEGY_FIX_LOG.md` / `docs/signals/vol_mispricing.md` by
      appending, not replacing, the entries `main` had independently added <!-- id: 7 -->
- [x] 8. Re-run the full 28-strategy suite against the rebased tree (the pre-rebase numbers were
      measured against a stale tree and were superseded, not reused) <!-- id: 8 -->
- [x] 9. Investigate and honestly document why `pairs_trading`, `rsi14_extremes`, and
      `forecast_direction_arima_hw` regressed from a previously-verified `deployable=True` to
      `False`, rather than reasserting an unverified "reasoning unchanged" note <!-- id: 9 -->
- [x] 10. Add regression tests to `tests/test_refresh_validations.py` for the EDGAR-PIT
      double-encoded-JSON / NaN-sector crash fix in `scripts/refresh_validations.py` <!-- id: 10 -->
