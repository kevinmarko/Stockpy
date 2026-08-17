# Implementation Plan: Phases 21 to 25 — Advanced Statistical Arbitrage, AI Sandbox, Options Rolling, SEC 606 & 3D Vol Mesh

## Subsystem Architecture

1. **Phase 21 (Copula Statistical Arbitrage)**:
   - Module: `pilots/copula_stat_arb.py`
   - Archimedean (Clayton, Gumbel, Frank) and Elliptical (Gaussian) copula MLE parameter fitting.
   - Dynamic Kalman filter time-varying hedge ratio estimation ($\beta_t$).
   - Ornstein-Uhlenbeck spread mean-reversion half-life calculation and trading signal generation.
2. **Phase 22 (AI Quantitative Research IDE & Sandboxed Backtester)**:
   - AI strategy synthesis from natural language prompts.
   - AST-isolated secure code evaluation sandboxing blocking unsafe built-ins, file writes, and process spawning.
3. **Phase 23 (Automated Position Rolling & Roll-Forward Desk)**:
   - Atomic multi-leg roll execution (simultaneous closing of near-term leg and opening of far-dated leg).
   - Insufficient cash rejection safeguards and live advisory veto constraints.
4. **Phase 24 (SEC Form 606 Execution Quality & Order Routing Analysis)**:
   - Quarterly SEC Form 606 order routing reports (Non-Directed Orders, Market Orders, Marketable/Non-Marketable Limit Orders).
   - Net payment for order flow (PFOF) rates and execution venue breakdowns.
5. **Phase 25 (3D Interactive Volatility Surface Mesh & Skew Visualizer)**:
   - Multi-strike and multi-expiry 3D volatility surface coordinate generation.
   - Moneyness interpolation, $25\Delta$ risk-reversal skew, and butterfly kurtosis metrics.

---

## Verification Plan
1. **Targeted Backend Pytest**:
   - `pytest tests/test_copula_stat_arb.py tests/test_options_lifecycle.py tests/test_pilots_paper_broker.py -k "Copula or AIResearch or Roll or Sec606 or VolSurface3DMesh" -v`
2. **Frontend Typecheck & Vitest**:
   - `tsc --noEmit` and `vitest run` in `webapp/`
3. **Security & Code Quality**:
   - `stockpy_codebase_auditor.py` and `bandit -r .`
