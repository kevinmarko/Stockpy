# Task Tracker: Close Options-Desk Deployability Runtime Gap

- [x] Fix `pilots/zero_dte_engine.py`: Remove fabricated `$1.50` fill price fallback; use Black-Scholes spot resolution or explicit error rejection.
- [x] Fix `pilots/dispersion_trading.py`: Add `INDEX_CONSTITUENTS_MAP` & `INDEX_WEIGHTS_MAP` with distinct weights for SPY vs QQQ.
- [x] Fix `pilots/dispersion_trading.py`: Evaluate correlation spread sign dynamically in `execute_dispersion_trade`.
- [x] Fix `api/pilots_api.py`: Add `OPTIONS_DESK_DEPLOYABILITY_GATES` and wire `gate_status` into execution responses.
- [x] Fix `docs/signals/vrp_premium_selling.md`: Remove duplicate `## Backtest Validation` header.
- [x] Update `docs/VALIDATION_STRATEGY_FIX_LOG.md` with 2026-08 remediation entry.
- [x] Add comprehensive test suite in `tests/test_options_desk_deployability_runtime_gap.py`.
- [x] Verify all 48 options desk tests pass with zero regressions.
- [x] Commit, push, and open PR `close-options-desk-deployability-gap`.
