# Implementation Plan: Phases 26 to 30 — Institutional Gateways, DRL Market Making, Transformer Volatility & Diffusion Stress

## Subsystem Architecture

1. **Phase 26 (Multi-Broker Execution Gateway & Failover Protocol)**:
   - Module: `broker_live_execution_mcp.py`
   - Active broker health monitoring, latency profiling, and automated failover orchestration.
   - Restful broker status and failover endpoints (`GET /pilots/execution/brokers/status`, `POST /pilots/execution/brokers/failover`).
2. **Phase 27 (FIX 4.4 Institutional Gateway Engine & Sequence Recovery)**:
   - Module: `execution/fix_gateway.py`
   - FIX 4.4 protocol engine with asynchronous socket session management.
   - Sequence gap detection, automatic resend requests (`FixMsgType.RESEND_REQUEST`), and multi-venue smart order routing.
3. **Phase 28 (Avellaneda-Stoikov Deep Reinforcement Learning Market Maker)**:
   - Module: `ml/drl_market_maker.py`
   - Avellaneda-Stoikov optimal bid/ask reservation pricing ($r(s, q, t) = s - q \gamma \sigma^2 (T - t)$).
   - Dynamic inventory risk penalization, Poisson fill arrival intensities, and actor-critic reinforcement learning policy training.
4. **Phase 29 (Transformer Multi-Horizon Volatility Forecaster)**:
   - Module: `ml/transformer_vol_forecaster.py`
   - Causal multi-head self-attention transformer architecture for multi-horizon forward volatility forecasting (1d, 5d, 22d).
   - Zero lookahead feature pipelines incorporating day-of-week embeddings and normalized return series.
5. **Phase 30 (Synthetic Financial Diffusion Stress Engine)**:
   - Module: `validation/synthetic_diffusion_engine.py`
   - Score-based generative diffusion model for synthetic financial time series simulation.
   - Extreme tail-risk stress testing and non-parametric Value-at-Risk ($VaR$) / Expected Shortfall ($ES$) evaluation.

---

## Verification Plan
1. **Targeted Pytest Gate**:
   - `pytest tests/test_fix_gateway.py tests/test_drl_market_maker.py tests/test_transformer_vol_forecaster.py tests/test_synthetic_diffusion_engine.py tests/test_pilots_paper_broker.py -k "Brokers or Fix or MarketMaker or AIForecasting" -v`
2. **Frontend Typecheck & Vitest**:
   - `tsc --noEmit` and `vitest run` in `webapp/`
3. **Security & Code Quality**:
   - `stockpy_codebase_auditor.py` and `bandit -r .`
