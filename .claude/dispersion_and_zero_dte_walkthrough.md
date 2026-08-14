# Walkthrough: Options Dispersion Arbitrage & 0DTE Breakout Desk (Phases 15 & 16)

We have completed the implementation and verification of **Phase 15 (Cross-Asset Options Dispersion & Implied Correlation Arbitrage Engine)** and **Phase 16 (0DTE Intraday Options Momentum & Volatility Breakout Engine)** across 6 specialized subagents.

---

## 🌟 What Was Built & Verified

### 1. Phase 15: Cross-Asset Options Dispersion & Implied Correlation Arbitrage
- **[`pilots/dispersion_trading.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/pilots/dispersion_trading.py)**:
  - `compute_implied_correlation`: Decomposes index variance into constituent weighted variances and average pairwise correlation (Driessen, Maenhout, Vilkov 2009):
    $$\bar{\rho}_{\text{implied}} = \frac{\sigma_{\text{Index}}^2 - \sum_{i=1}^N w_i^2 \sigma_i^2}{\left(\sum_{i=1}^N w_i \sigma_i\right)^2 - \sum_{i=1}^N w_i^2 \sigma_i^2}$$
  - `compute_realized_correlation_matrix`: Computes historical pairwise returns correlation matrix and weighted realized average $\bar{\rho}_{\text{realized}}$.
  - `evaluate_dispersion_opportunity`: Evaluates Correlation Spread $\Delta\rho = \bar{\rho}_{\text{implied}} - \bar{\rho}_{\text{realized}}$. Classifies **Long Dispersion** ($\Delta\rho \ge +0.15 \implies \text{Short Index Straddle} + \text{Long Component Straddles}$) vs. **Short Dispersion** ($\Delta\rho \le -0.15$).
  - `build_dispersion_basket`: Dynamically sizes constituent stock straddles to achieve strict Vega Neutrality ($\sum \mathcal{V}_{\text{constituent}} \approx \mathcal{V}_{\text{index}}$) and Delta Neutrality.
  - `execute_dispersion_trade`: Atomically submits the index straddle and all constituent straddles into `PaperAccountStore` under `strategy="Dispersion Arbitrage"`.
- **[`webapp/src/components/options/DispersionScanner.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/components/options/DispersionScanner.tsx)**:
  - Correlation spread gauge ($\bar{\rho}_{\text{implied}} - \bar{\rho}_{\text{realized}}$), constituent weighting & IV table, vega balance meter, and 1-click "⚡ Execute Dispersion Basket" action.

### 2. Phase 16: 0DTE Intraday Options Momentum & Volatility Breakout Engine
- **[`pilots/zero_dte_engine.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/pilots/zero_dte_engine.py)**:
  - `compute_opening_range`: Calculates 15-minute Opening Range High, Low, and volume.
  - `detect_volatility_squeeze`: Computes Bollinger Bands ($20, 2\sigma$) and Keltner Channels ($20, 1.5\text{ATR}$); detects squeeze compression and explosive release.
  - `scan_0dte_breakouts`: Generates Bullish Breakouts (ATM/1-OTM Call, $\Delta \approx 0.45$) and Bearish Breakdowns (ATM/1-OTM Put, $\Delta \approx -0.45$) on price thrust past 15-min ORB + Squeeze Release + Volume $> 1.25\times$.
  - `evaluate_0dte_exits`: Fast risk lifecycle with +75% Profit Target (`PROFIT_TARGET_75`), -30% Stop Loss (`STOP_LOSS_30`), and mandatory **15:45 ET Hard Time Stop** (`HARD_TIME_STOP_1545`) to eliminate settlement/pin risk.
  - `execute_0dte_trade`: Submits 0DTE single-leg option orders tagged `strategy="0DTE Momentum Breakout"`.
- **[`webapp/src/components/options/ZeroDteDesk.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/components/options/ZeroDteDesk.tsx)**:
  - 15-min Opening Range Breakout box with High/Low levels, glowing TTM Squeeze indicator light, relative volume burst meter, and 1-click "⚡ Trade 0DTE Breakout" action.

### 3. API Endpoints & Navigation
- **[`api/pilots_api.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/api/pilots_api.py)**:
  - `GET /pilots/options/dispersion/opportunities` & `POST /pilots/options/dispersion/execute`
  - `GET /pilots/options/zero-dte/signals` & `POST /pilots/options/zero-dte/execute`
- **[`webapp/src/screens/OptionsChain.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/screens/OptionsChain.tsx)** & **[`webapp/src/screens/PaperBroker.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/screens/PaperBroker.tsx)**:
  - Added dedicated `🌐 Dispersion` and `⚡ 0DTE` tabs and quick-access toolbar controls.

---

## 🧪 Verification & Gate Checks

```bash
# 1. Full Backend Python Test Suite (321 tests passed, 0 failures)
pytest tests/test_dispersion_trading.py tests/test_zero_dte_engine.py \
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

# 3. Frontend Vitest Options Suite (56 tests passed across 10 test files)
npm test src/screens/PaperBroker.test.tsx src/screens/OptionsChain.test.tsx \
         src/components/options/DispersionScanner.test.tsx \
         src/components/options/ZeroDteDesk.test.tsx \
         src/components/options/VolForecastScanner.test.tsx \
         src/components/options/GammaScalperView.test.tsx \
         src/components/options/EarningsCrushScanner.test.tsx \
         src/components/options/UnusualFlowFeed.test.tsx \
         src/components/options/ScenarioHeatmap.test.tsx \
         src/components/options/VolSurfaceView.test.tsx
```
