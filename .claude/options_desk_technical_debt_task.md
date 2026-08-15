# Task: Options Desk Technical Debt Resolution & Hardening

## Overview
Resolve the 4 technical debt items flagged during the options desk build-out and verify with rigorous multi-suite testing.

## Task Checklist
- [x] Phase 1: Consolidate Black-Scholes & Greeks implementations into `pilots/options_risk.py`
  - [x] Enhance `calculate_black_scholes_greeks` with case-insensitive option types and aliases (`theta_annual`, `theta`, `vega`, `vega_raw`, `intrinsic`, `extrinsic`)
  - [x] Refactor `pilots/options_sor.py`
  - [x] Refactor `pilots/vol_mispricing.py`
  - [x] Refactor `pilots/dispersion_trading.py`
  - [x] Refactor `pilots/gamma_scalper.py`
  - [x] Refactor `pilots/volatility_surface.py`
  - [x] Verify with 75 unit tests across all 6 modules
- [x] Phase 2: Eliminate Lookahead Bias in `pilots/copula_stat_arb.py`
  - [x] Enforce causal time-varying Kalman hedge ratio spread $S_t = y_t - \beta_t x_t$ with warm-up trimming in `evaluate_copula_stat_arb_pair`
  - [x] Enforce trailing-window copula tail-risk fitting in `generate_copula_stat_arb_signals`
  - [x] Add perturbation test `test_copula_stat_arb_zero_lookahead_bias` in `tests/test_copula_stat_arb.py`
  - [x] Verify with 28 passing unit tests
- [x] Phase 3: Wire Alert Dispatchers in `pilots/options_alerts.py` to Production Callers
  - [x] Wire `dispatch_uoa_whale_alert` into `pilots/unusual_options_flow.py`
  - [x] Wire `dispatch_earnings_crush_alert` into `pilots/earnings_crush.py`
  - [x] Wire `dispatch_delta_hedge_alert` into `pilots/options_hedging.py`
  - [x] Update `docs/architecture/execution.md` documentation
  - [x] Verify with 80 passing unit tests across alert and consumer modules
- [x] Phase 4: Fix Client/Backend Contract Mismatch for `UnusualFlowFeed.tsx`
  - [x] Update `api/pilots_api.py` to return both `records` and `trades` keys
  - [x] Update `webapp/src/api/types.ts` with dual format definitions
  - [x] Update `webapp/src/components/options/UnusualFlowFeed.tsx` with fallback parsing and field normalization
  - [x] Add test in `webapp/src/components/options/UnusualFlowFeed.test.tsx` verifying backend `records` format
  - [x] Verify typecheck and 151 Vitest test suites (1633 tests) pass
