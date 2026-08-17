# Implementation Plan: Phase 2 — Quantitative Models, Optimization & Anti-Fabrication

## Goal
Implement mathematical rigor, true calculated metrics, and exact reference testing across quantitative modules:
1. **CVaR 95% Calculation & HRP Optimization**: Verify real non-placeholder CVaR 95% calculations in `api/pilots_api.py` and `sizing/hrp_cvar_optimizer.py`.
2. **Avellaneda-Stoikov Market Maker Policy Optimization**: Expose `train_market_maker_policy` via `POST /pilots/options/market-maker/train` and document validation exemption and metrics in `docs/VALIDATION_STRATEGY_FIX_LOG.md`.
3. **Exact Mathematical Reference Tests**:
   - Closed-form Black-Scholes Greeks reference tests in `tests/test_options_risk.py`.
   - Asymmetric multi-asset Driessen-Maenhout-Vilkov implied correlation tests in `tests/test_dispersion_trading.py`.

## Verification Plan
- Automated pytest across `test_options_risk.py`, `test_dispersion_trading.py`, `test_drl_market_maker.py`, `test_hrp_cvar_optimizer.py`, `test_pilots_api.py`.
- Bandit SAST scan.
- Webapp typecheck via `tsc --noEmit`.
