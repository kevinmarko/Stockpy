# Quantitative Platform Enhancements & Institutional Engines (Phases 31 – 36) Task Tracker

## Task List

- [x] **Memory Hygiene & Settings Polish**
  - [x] Integrate `AbortController` in `AIChatInterface.tsx`
  - [x] Declare `NO_VENV_REEXEC` in `settings.py` and allowlist in `gui/env_io.py`
  - [x] Run Step 1 Independent Audit (`f4047f02` - PASS)

- [x] **Phase 31: Real-Time Risk Streamer & WebSocket Hub**
  - [x] Build `pilots/realtime_risk_streamer.py` core calculation engine
  - [x] Build `/ws/risk/portfolio` endpoint in `api/ws_api.py`
  - [x] Build `RealTimeRiskRadar.tsx` UI component
  - [x] Run Phase 31 Independent Audits (`86345bf8`, `a0a9e91b`, `22fab448` - PASS)

- [x] **Phase 32 (Step 1): Dynamic Circuit Breakers & UI Badge**
  - [x] Build `execution/dynamic_circuit_breaker.py` (Vol burst, OFI/VPIN shield, Loss velocity brake)
  - [x] Integrate Check #0 into `execution/risk_gate.py`
  - [x] Expose `GET /risk/circuit-breaker/status` in `api/data_api.py`
  - [x] Build `DynamicCircuitBreakerBadge.tsx`
  - [x] Run Phase 32 Step 1 Independent Audit (`be0b950d` - PASS)

- [x] **Phase 33: Multi-Quantile TFT Volatility Surface & Macro Cone**
  - [x] Implement Pinball Loss $\mathcal{L}_\alpha$ and quantile regression heads in `ml/transformer_vol_forecaster.py`
  - [x] Add causal FRED macro feature conditioning (`VIXCLS`, `T10Y2Y`, `BAMLC0A0CM`, `FEDFUNDS`)
  - [x] Enforce monotonic quantile rearrangement ($q_{10} \le q_{50} \le q_{90}$)
  - [x] Build `TransformerVolForecastView.tsx` probabilistic cone chart
  - [x] Run Phase 33 Independent Audit (`a5918e7e` - PASS)

- [x] **Phase 34: Macro-Regime Guided Generative Diffusion Stress Engine**
  - [x] Implement score diffusion model with Classifier-Free Guidance ($p_{\text{uncond}} = 0.15$) in `validation/synthetic_diffusion_engine.py`
  - [x] Implement reverse-time OU SDE Euler-Maruyama solver with regime steering
  - [x] Implement multi-quantile tail risk calculations ($\text{VaR}_{95}, \text{CVaR}_{95}, \text{VaR}_{99}, \text{CVaR}_{99}$)
  - [x] Build `GenerativeDiffusionStressView.tsx` UI component
  - [x] Run Phase 34 Independent Audit (`683eb710` - PASS)

- [x] **Phase 35: Turnover-Regularized & Factor-Neutral HRP Multi-Asset Optimizer**
  - [x] Implement SLSQP CVaR minimization with L1 turnover penalty ($\lambda_{\text{turnover}} \|\mathbf{w} - \mathbf{w}_0\|_1$) in `sizing/hrp_cvar_optimizer.py`
  - [x] Implement linear sector caps and factor beta bounds
  - [x] Implement Choueifaty Diversification Ratio calculation
  - [x] Update `POST /pilots/portfolio/optimize/hrp-cvar` in `api/pilots_api.py`
  - [x] Build `HrpPortfolioOptimizerView.tsx` UI console
  - [x] Run Phase 35 Independent Audit (`2f58f965` - PASS)

- [x] **Phase 36: Production FIX 4.4 Engine & Resilient Session Recovery**
  - [x] Implement Tag 10 CheckSum calculation and validation in `execution/fix_gateway.py`
  - [x] Implement `FixSessionState` lifecycle state machine
  - [x] Implement sequence gap detection, `ResendRequest (35=2)`, out-of-order buffering, and `SequenceReset-GapFill (35=4)` contiguous draining
  - [x] Implement heartbeat and inactivity watchdog timers
  - [x] Implement atomic session persistence to `output/fix_session_state.json`
  - [x] Expose FIX session management endpoints in `api/pilots_api.py`
  - [x] Build `FixGatewayStatusRadar.tsx` UI console
  - [x] Run Phase 36 Independent Audit (`2dddfb14` - PASS)
