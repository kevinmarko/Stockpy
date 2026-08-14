# Walkthrough: Earnings Volatility Crush & Unusual Options Activity Flow (Phases 10 & 11)

We have completed the implementation and verification of **Phase 10 (Earnings Volatility Crush & Event Risk Scanner)** and **Phase 11 (Unusual Options Activity & Flow Sentiment Scanner)** across 6 specialized subagents.

---

## 🌟 What Was Built & Verified

### 1. Phase 10: Earnings Volatility Crush & Event Risk Scanner
- **[`pilots/earnings_crush.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/pilots/earnings_crush.py)**:
  - `calculate_expected_earnings_move`: Calculates ATM straddle implied moves ($0.80 \times S \times \sigma_{\text{ATM}} \times \sqrt{\text{DTE}/365}$).
  - `get_historical_earnings_moves`: Queries trailing 8 quarters of historical earnings releases from `HistoricalStore.get_earnings_events()` and computes actual gap percentages ($|\text{Open} - \text{PrevClose}| / \text{PrevClose}$) with honest sparse-history fallbacks.
  - `evaluate_earnings_crush_candidates`: Scans upcoming earnings in next 1–5 days, computes Crush Edge Ratio ($\frac{\text{Implied Move}}{\text{Median Realized Move}}$), and generates delta-neutral Iron Condors when Edge $\ge 1.25\times$.
- **[`execution/options_paper_executor.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/execution/options_paper_executor.py)**:
  - `execute_earnings_crush_trade`: Executes multi-leg Iron Condor / Short Straddle fills labeled `strategy="Earnings Crush"` into `PaperAccountStore`.
  - `settle_post_earnings_trades`: Automatically closes completed earnings trades at market open to harvest pure IV crush.
- **[`webapp/src/components/options/EarningsCrushScanner.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/components/options/EarningsCrushScanner.tsx)**:
  - Interactive table showing report dates (AMC/BMO), spot prices, Expected vs. Realized moves, Edge badges, and 1-click "⚡ Trade Crush Spread" button.

### 2. Phase 11: Real-Time Options Order Flow & Unusual Options Activity (UOA) Scanner
- **[`pilots/unusual_options_flow.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/pilots/unusual_options_flow.py)**:
  - `scan_unusual_options_activity`: Filters anomalous institutional contracts satisfying $\text{Volume} / \text{OI} \ge 3.0$, $\text{Notional} \ge \$100,000$, and $\text{Volume} \ge 500$.
  - Categorizes trade aggressiveness: Aggressive Ask Sweeps ($\text{Price} \ge \text{Ask}$), Aggressive Bid Sweeps ($\text{Price} \le \text{Bid}$), and Mid-Market Blocks.
  - Flags IV expansion anomalies ($\text{IV} \ge 1.25 \times \text{HV}_{30}$).
  - `calculate_net_flow_sentiment`: Aggregates institutional directional flow into normalized sentiment score in $[-1.0, +1.0]$, Call/Put volume ratios, and top active strikes.
- **[`signals/options_flow_sentiment.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/signals/options_flow_sentiment.py)**:
  - `OptionsFlowSentimentSignal(SignalModule)` plugin evaluating net options flow sentiment as a registered quantitative factor in `StrategyEngine` (weight: `10.0`).
- **[`webapp/src/components/options/UnusualFlowFeed.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/components/options/UnusualFlowFeed.tsx)**:
  - Real-time order stream of sweeps and blocks with Bullish/Bearish badges, V/OI multipliers, notional sizing, and a visual $[-100\%, +100\%]$ Institutional Flow Sentiment Gauge.

### 3. API & PWA Integration
- **[`api/pilots_api.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/api/pilots_api.py)**:
  - `GET /pilots/options/earnings-crush/candidates`
  - `POST /pilots/options/earnings-crush/execute`
  - `GET /pilots/options/flow/unusual`
  - `GET /pilots/options/flow/sentiment?symbol=...`
- **[`webapp/src/screens/PaperBroker.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/screens/PaperBroker.tsx)** & **[`webapp/src/screens/OptionsChain.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/screens/OptionsChain.tsx)**:
  - Added dedicated `⚡ Earnings Crush` and `🌊 Unusual Flow` drawer panels and tabs.

---

## 🧪 Verification & Gate Checks

```bash
# 1. Backend Python Test Suite (194 tests passed, 0 failures)
pytest tests/test_earnings_crush.py tests/test_unusual_options_flow.py \
       tests/test_options_lifecycle.py tests/test_options_hedging.py \
       tests/test_volatility_surface.py tests/test_scenario_matrix.py \
       tests/test_pilots_paper_broker.py tests/test_options_risk.py \
       tests/test_options_meta_labeler.py tests/test_options_harness.py \
       tests/test_options_paper_executor.py tests/test_paper_account_store.py \
       tests/test_fmp_paper_broker.py tests/test_order_sizing.py -v

# 2. Frontend TypeScript Compilation (0 errors)
npm run --prefix webapp typecheck

# 3. Frontend Vitest Options Suite (33 tests passed across 6 test files)
npm test src/screens/PaperBroker.test.tsx src/screens/OptionsChain.test.tsx \
         src/components/options/EarningsCrushScanner.test.tsx \
         src/components/options/UnusualFlowFeed.test.tsx \
         src/components/options/ScenarioHeatmap.test.tsx \
         src/components/options/VolSurfaceView.test.tsx
```
