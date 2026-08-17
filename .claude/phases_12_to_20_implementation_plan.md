# Implementation Plan: Phases 12 to 20 — Advanced Quantitative Options & Market Microstructure Desks

## Subsystem Scope

This plan covers the implementation and verification of Phases 12 through 20 across quantitative models, order routing, market microstructure, and webapp integration:

1. **Phase 12 (HAR-RV & Strike Mispricing)**: Corsi (2009) Heterogeneous Autoregressive Realized Volatility model fitting, realized variance decomposition ($RV_d, RV_w, RV_m$), forward term structures ($\hat{\sigma}_{1d}, \hat{\sigma}_{5d}, \hat{\sigma}_{22d}$), and Black-Scholes strike mispricing spread calculation ($\Delta \sigma = IV_{\text{market}} - \hat{\sigma}_{\text{HAR-RV}}$).
2. **Phase 13 (Intraday Gamma Scalping & Greek Attribution)**: Dynamic delta-neutral rebalancing with deadband thresholding, GBM price path simulation, higher-order Greek PnL attribution ($\Delta, \Gamma, \Theta, \text{Vega}, \text{Rho}$), and Brinson-Fachler asset allocation vs selection decomposition.
3. **Phase 14 (Multi-Channel Webhook Alerts)**: Discord, Slack, and generic webhook payload dispatching for UOA whale flow, earnings crush, delta hedge imbalances, and risk limit breaches.
4. **Phase 15 (Cross-Asset Dispersion Trading)**: Driessen-Maenhout-Vilkov index implied correlation formula ($\rho_{\text{imp}}$), ETF basket component weighting, dirty hedge ratio sizing, and Correlation Risk Premium (CRP) trading signals.
5. **Phase 16 (0DTE Momentum & TTM Squeeze Desk)**: 15-min Opening Range Breakout (ORB), Bollinger Band inside Keltner Channel TTM squeeze detection, 15:45 ET hard liquidation stop, +75% profit target, and -30% stop loss.
6. **Phase 17 (Options VPIN Order Flow Toxicity)**: Volume-Synchronized Probability of Toxicity (VPIN), bulk volume classification using standard normal CDF, volume bucket discretization, and toxic flow defense thresholding.
7. **Phase 18 (Smart Order Router & Legging Hazard Simulator)**: Multi-exchange routing, fee/rebate optimization, and multi-leg legging hazard Monte Carlo simulation.
8. **Phase 19 (Limit Order Book & Queue Dynamics Simulator)**: Cont-de Larrard analytical queue dynamics, order book depth simulation, and Laplace fill probability calculations.
9. **Phase 20 (Options Gamma Exposure & Zero-Gamma Flip)**: Dollar GEX summation ($GEX = \sum \Gamma \times S \times \text{OI} \times 100$), Call/Put Gamma Walls, Zero-Gamma Flip level ($S^*$), and volatility regime classification.
10. **Integration**: REST endpoint routing in `api/pilots_api.py`, TypeScript types in `webapp/src/types.ts`, and 100% Mock/Live schema parity in `webapp/src/api/client.ts` vs `mock.ts`.

---

## Verification Plan
1. **Automated Unit Tests**:
   - `pytest tests/test_har_volatility.py tests/test_vol_mispricing.py tests/test_gamma_scalper.py tests/test_pilots_attribution.py tests/test_pilots_attribution_brinson.py tests/test_pilots_portfolio_attribution.py tests/test_options_alerts.py tests/test_dispersion_trading.py tests/test_zero_dte_engine.py tests/test_options_vpin.py tests/test_options_sor.py tests/test_lob_simulator.py tests/test_options_gex.py -q`
2. **Frontend Typecheck & Vitest**:
   - `tsc --noEmit` and `vitest run` in `webapp/`
3. **Static & Security Audits**:
   - `stockpy_codebase_auditor.py` and `bandit -r .`
