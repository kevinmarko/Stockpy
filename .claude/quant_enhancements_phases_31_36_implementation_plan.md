# Quantitative Platform Enhancements & Institutional Engines (Phases 31 – 36) Implementation Plan

This document outlines the architecture, mathematical formulations, implementation requirements, and multi-agent audit gates for Phases 31 through 36 of the quantitative trading platform.

---

## 1. Scope & Phased Architecture

- **Phase 31**: Real-Time Portfolio Risk Streamer & WebSocket Hub (`pilots/realtime_risk_streamer.py`, `api/ws_api.py`, `RealTimeRiskRadar.tsx`)
- **Phase 32 (Step 1)**: Dynamic Circuit Breakers & UI Badge (`execution/dynamic_circuit_breaker.py`, `execution/risk_gate.py`, `DynamicCircuitBreakerBadge.tsx`)
- **Phase 33**: Multi-Quantile TFT Volatility Surface & Macro Cone (`ml/transformer_vol_forecaster.py`, `TransformerVolForecastView.tsx`)
- **Phase 34**: Macro-Regime Guided Generative Diffusion Stress Engine (`validation/synthetic_diffusion_engine.py`, `GenerativeDiffusionStressView.tsx`)
- **Phase 35**: Turnover-Regularized & Factor-Neutral HRP Multi-Asset Optimizer (`sizing/hrp_cvar_optimizer.py`, `HrpPortfolioOptimizerView.tsx`)
- **Phase 36**: Production FIX 4.4 Engine & Resilient Session Recovery (`execution/fix_gateway.py`, `FixGatewayStatusRadar.tsx`)

---

## 2. Multi-Agent Audit Review Gates

Every phase is audited by an independent subagent enforcing:
1. **Mathematical & Causal Integrity**: Exact pinball loss, SDE Euler-Maruyama solvers, SLSQP constraints, Tag 10 checksums, zero lookahead bias.
2. **Constraint #4 (Data Honesty)**: Missing data surfaced honestly (HTTP 422, `missing_positions`), never fabricated dummy values.
3. **Constraint #6 (Dead-Letter Protection)**: Corrupt/empty inputs degrade cleanly without unhandled exceptions.
4. **AST Architecture Boundaries**: Subsystem isolation (stdlib + numpy + pandas, zero heavy engine/GUI imports).
5. **Mock/Live API Parity**: 1:1 schema alignment across `types.ts`, `client.ts`, and `mock.ts`.
6. **Frontend Lifecycle Safety**: `isMounted` guards, cleanup on unmount, full ARIA accessibility.

---

## 3. Verification Plan

- `pytest tests/test_realtime_risk_streamer.py tests/test_ws_risk_stream.py -v`
- `pytest tests/test_dynamic_circuit_breaker.py tests/test_risk_gate.py -v`
- `pytest tests/test_transformer_vol_forecaster.py -v`
- `pytest tests/test_synthetic_diffusion_engine.py -v`
- `pytest tests/test_hrp_cvar_optimizer.py -v`
- `pytest tests/test_fix_gateway.py -v`
- `pytest tests/test_pilots_api.py -v`
- `npm run --prefix webapp typecheck`
- `npm run --prefix webapp test -- --run`
