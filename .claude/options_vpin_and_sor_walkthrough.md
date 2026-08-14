# Walkthrough: Options VPIN Toxicity & Smart Order Router (Phases 17 & 18)

We have completed the implementation and verification of **Phase 17 (Options VPIN & Order Flow Toxicity Engine)** and **Phase 18 (Multi-Leg Smart Order Router & Legging Simulator)** across 6 specialized subagents.

---

## 🌟 What Was Built & Verified

### 1. Phase 17: Options VPIN (Volume-Synchronized Probability of Toxicity)
- **[`pilots/options_vpin.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/pilots/options_vpin.py)**:
  - `compute_vpin_buckets`: Groups option trades into equal-volume buckets of size $V$ using Bulk Volume Classification (BVC) based on standard normal price change distribution:
    $$V_\tau^B = V_\tau \cdot \Phi\left(\frac{\Delta P_\tau}{\sigma_{\Delta P}}\right), \quad V_\tau^S = V_\tau - V_\tau^B$$
  - `calculate_vpin`: Rolling $N$-bucket metric calculation $\in [0.0, 1.0]$.
  - `evaluate_toxicity_regime`: Classifies `LOW` ($< 0.20$), `MODERATE` ($0.20 - 0.35$), and `HIGH_TOXICITY` ($> 0.35$).
  - `apply_defensive_spread_concession`: Dynamically widens limit concessions under toxic regimes to avoid adverse selection by informed flow.
- **[`webapp/src/components/options/VpinGauge.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/components/options/VpinGauge.tsx)**:
  - Semicircle arc gauge meter $[0\%, 100\%]$ with color-coded safety bands, toxic flow warning alerts, and $N=50$ volume bucket trade imbalance history bars.

### 2. Phase 18: Multi-Leg Options Smart Order Router (SOR) & Legging Simulator
- **[`pilots/options_sor.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/pilots/options_sor.py)**:
  - `analyze_routing_options`: Compares Complex Order Book (`COB_NET_PACKAGE`) mid/natural execution vs. Synthetic Legging (`LEG_PASSIVE_FIRST` vs. `SPLIT_DIRECT`), computing expected spread savings vs. adverse hazard.
  - `simulate_legging_execution`: Monte Carlo simulation of inter-leg execution latency ($\Delta t$), computing hung-leg probability $P_{\text{hung}}$, adverse slippage cost, and net edge distribution.
- **[`webapp/src/components/options/SmartOrderRouterView.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/components/options/SmartOrderRouterView.tsx)**:
  - Side-by-side COB vs. Synthetic Legging comparison cards, interactive latency slider ($50\text{ms} - 3000\text{ms}$), fill sequence table, and 1-click execution routing toggle.

### 3. API Endpoints & Screen Integration
- **[`api/pilots_api.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/api/pilots_api.py)**:
  - `GET /pilots/options/vpin/metrics?symbol=...`
  - `POST /pilots/options/sor/analyze`
  - `POST /pilots/options/sor/simulate-legging`
- **[`webapp/src/screens/PaperBroker.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/screens/PaperBroker.tsx)** & **[`webapp/src/screens/OptionsChain.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/screens/OptionsChain.tsx)**:
  - Added dedicated `⏱ VPIN` and `🔀 Smart Router` quick-access toolbar actions and tabs.

---

## 🧪 Verification & Gate Checks

```bash
# 1. Full Backend Python Test Suite (361 tests passed, 0 failures)
pytest tests/test_options_vpin.py tests/test_options_sor.py \
       tests/test_dispersion_trading.py tests/test_zero_dte_engine.py \
       tests/test_har_volatility.py tests/test_vol_mispricing.py \
       tests/test_gamma_scalper.py tests/test_options_alerts.py \
       tests/test_earnings_crush.py tests/test_unusual_options_flow.py \
       tests/test_options_lifecycle.py tests/test_options_hedging.py \
       tests/test_volatility_surface.py tests/test_scenario_matrix.py \
       tests/test_pilots_paper_broker.py tests/test_options_risk.py \
       tests/test_options_meta_labeler.py tests/test_options_harness.py \
       tests/test_options_paper_executor.py tests/test_paper_account_store.py \
       tests/test_fmp_paper_broker.py tests/test_order_sizing.py -v

# 2. Frontend TypeScript Compilation (0 errors)
npm run --prefix webapp typecheck

# 3. Frontend Vitest Options Suite (68 tests passed across 12 test files)
npm test src/screens/PaperBroker.test.tsx src/screens/OptionsChain.test.tsx \
         src/components/options/VpinGauge.test.tsx \
         src/components/options/SmartOrderRouterView.test.tsx \
         src/components/options/DispersionScanner.test.tsx \
         src/components/options/ZeroDteDesk.test.tsx \
         src/components/options/VolForecastScanner.test.tsx \
         src/components/options/GammaScalperView.test.tsx \
         src/components/options/EarningsCrushScanner.test.tsx \
         src/components/options/UnusualFlowFeed.test.tsx \
         src/components/options/ScenarioHeatmap.test.tsx \
         src/components/options/VolSurfaceView.test.tsx
```
