# Task Tracker: Options Desk Technical Debt Follow-Up Fixes

- [x] Re-audit PR #749's four claimed fixes with 4 parallel independent review agents
- [x] Fix `pilots/earnings_crush.py`: dispatch gate on `is_recommended` alone, not
      `is_recommended or crush_edge_ratio >= 1.35`
- [x] Fix `pilots/options_hedging.py`: `execute_delta_hedge()`'s dispatch call now passes a
      preview-shaped dict matching `dispatch_delta_hedge_alert`'s qualifying gate
- [x] Fix `pilots/copula_stat_arb.py`: restore causal per-bar OU half-life check alongside the
      already-causal copula tail-dependence check in `generate_copula_stat_arb_signals`
- [x] Add regression tests for all three fixes (`tests/test_earnings_crush.py`,
      `tests/test_options_hedging.py`, `tests/test_copula_stat_arb.py`)
- [x] Update `docs/architecture/execution.md` to accurately describe current wiring + fix history
- [x] Confirm no regressions: full targeted test suite (272 tests) green
- [x] Copy implementation plan / task / walkthrough artifacts to `.claude/`
- [ ] Open PR against `main`
