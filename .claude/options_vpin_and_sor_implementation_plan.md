# Master Implementation Plan: Options VPIN Toxicity & Smart Order Router (Phases 17 & 18)

## Executive Overview
This phase integrates high-frequency quantitative market microstructure analytics and smart order routing into the options trading desk:

1. **Phase 17: Options VPIN (Volume-Synchronized Probability of Toxicity) & Microstructure Risk Engine**
   - **Easley, López de Prado, O'Hara (2012) VPIN Model**:
     - Synchronizes option trades into equal-volume buckets ($V$).
     - Classifies buy vs. sell volume via Bulk Volume Classification (BVC) based on standard normal price change distribution:
       $$V_\tau^B = V_\tau \cdot \Phi\left(\frac{\Delta P_\tau}{\sigma_{\Delta P}}\right), \quad V_\tau^S = V_\tau - V_\tau^B$$
     - Computes rolling VPIN across $N$ volume buckets:
       $$VPIN_t = \frac{\sum_{\tau=1}^N |V_\tau^B - V_\tau^S|}{N \cdot V} \in [0.0, 1.0]$$
   - **Toxicity Gating & Defense**: Flags toxic order flow regimes ($VPIN > 0.35$) where informed institutional traders are active, automatically triggering defensive spread adjustments and toxicity risk alerts.

2. **Phase 18: Multi-Leg Options Smart Order Router (SOR) & Legging Optimization Engine**
   - **Complex Order Book (COB) vs. Synthetic Legging**:
     - **Direct COB Net Route**: Submits all legs simultaneously at net mid/natural price (guarantees atomic execution, zero leg risk).
     - **Synthetic Legging-In**: Fills the passive leg first at the bid/ask to capture spread edge, then immediately executes the active leg.
   - **Legging Hazard & Adverse Selection Simulator**:
     - Models the probability of a "hung leg" during inter-leg execution latency ($\Delta t$).
     - Computes Expected Legging Savings ($\$$) vs. Hazard Risk ($\%$) and recommends optimal execution policy (`COB_NET_PACKAGE` vs. `LEG_PASSIVE_FIRST` vs. `SPLIT_DIRECT`).

Execution is organized across **6 Specialized Subagents** with clean AST safety, full test coverage, and 100% Mock/Live UI parity.

---

## Subagent Architecture & Workstream Division

```mermaid
graph TD
    subgraph Phase 17: Options VPIN Toxicity Engine
        A1["<b>Agent 1: VPIN Math & Volume Bucket Engine</b><br/>• Volume-Synchronized Partitioning<br/>• Bulk Volume Classification (BVC)<br/>• pilots/options_vpin.py"]
        A2["<b>Agent 2: Toxicity Gating & Defense</b><br/>• Toxicity Thresholds (VPIN > 0.35)<br/>• Defensive Concession & Spread Widening<br/>• Toxicity Alert Integration"]
    end

    subgraph Phase 18: Smart Order Router (SOR)
        A3["<b>Agent 3: Multi-Leg SOR & Routing Optimizer</b><br/>• COB Net Spread vs Synthetic Legging<br/>• Optimal Execution Policy Decision Matrix<br/>• pilots/options_sor.py"]
        A4["<b>Agent 4: Legging Hazard & Adverse Selection Simulator</b><br/>• Inter-Leg Latency & Hung Leg Risk Model<br/>• Monte Carlo Execution Shortfall Simulation"]
    end

    subgraph Platform & UI Integration
        A5["<b>Agent 5: Pilots API & Backend Routing</b><br/>• Endpoints for VPIN Metrics & SOR Analysis<br/>• Token Auth & AST Boundary Safety"]
        A6["<b>Agent 6: Webapp PWA UI & Visual Desks</b><br/>• VpinGauge.tsx & SmartOrderRouterView.tsx<br/>• Client/Mock Parity & Vitest Test Suites"]
    end

    A1 --> A2
    A2 --> A5
    A3 --> A4
    A4 --> A5
    A5 --> A6
```

---

## User Review Required

> [!IMPORTANT]
> **Key Architectural Controls**:
> 1. **VPIN Bucket Configuration**: Default bucket size $V = \frac{\text{Daily Volume}}{50}$ with rolling window $N = 50$ buckets.
> 2. **Toxicity Regimes**: Low ($VPIN < 0.20$), Moderate ($0.20 \le VPIN \le 0.35$), High / Toxic ($VPIN > 0.35$).
> 3. **AST Safety**: All modules under `pilots/` and `api/pilots_api.py` import only stdlib, numpy, scipy, pandas, and data stores.

---

## Detailed Agent Tasks & Proposed Changes

### Workstream 1: Agent 1 — VPIN Math & Volume Bucket Specialist
- **Module**: `[NEW]` [`pilots/options_vpin.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/pilots/options_vpin.py)
- **Features**:
  - `compute_vpin_buckets(trades_df, bucket_size, num_buckets=50)`: Groups trades into equal-volume buckets and computes Buy/Sell volume via BVC.
  - `calculate_vpin(trades_df, num_buckets=50)`: Calculates rolling VPIN metric series and current instantaneous VPIN $\in [0.0, 1.0]$.
  - Includes degenerate guards (zero volume, identical price ticks, NaN inputs).

### Workstream 2: Agent 2 — Toxicity Gating & Microstructure Defense Specialist
- **Module**: [`pilots/options_vpin.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/pilots/options_vpin.py), [`execution/options_paper_executor.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/execution/options_paper_executor.py)
- **Features**:
  - `evaluate_toxicity_regime(vpin_value)`: Classifies `LOW`, `MODERATE`, `HIGH_TOXICITY`.
  - `apply_defensive_spread_concession(order_intent, vpin_value)`: When $VPIN > 0.35$, widens limit concessions or delays execution to prevent market maker adverse selection.

### Workstream 3: Agent 3 — Multi-Leg SOR & Routing Optimizer Specialist
- **Module**: `[NEW]` [`pilots/options_sor.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/pilots/options_sor.py)
- **Features**:
  - `analyze_routing_options(legs, spot_price, quotes_map)`:
    - Calculates COB Net Debit/Credit Mid vs Natural price.
    - Calculates Synthetic Legging prices (Passive-First vs Active-First).
    - Determines optimal routing policy: `COB_NET_PACKAGE`, `LEG_PASSIVE_FIRST`, or `SPLIT_DIRECT`.

### Workstream 4: Agent 4 — Legging Hazard & Adverse Selection Simulator Specialist
- **Module**: [`pilots/options_sor.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/pilots/options_sor.py)
- **Features**:
  - `simulate_legging_execution(legs, spot_price, volatility, latency_seconds=2.0, num_simulations=1000)`:
    - Simulates price movement between leg fills.
    - Computes probability of hung leg, expected adverse selection cost, and net edge distribution.

### Workstream 5: Agent 5 — Pilots API & Backend Routing Specialist
- **Module**: [`api/pilots_api.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/api/pilots_api.py)
- **Features**:
  - `GET /pilots/options/vpin/metrics?symbol=...`: Returns current VPIN, bucket history, and toxicity regime (Read token).
  - `POST /pilots/options/sor/analyze`: Analyzes COB vs Legging routing for a multi-leg order (Read token).
  - `POST /pilots/options/sor/simulate-legging`: Runs Monte Carlo legging hazard simulation (Read token).

### Workstream 6: Agent 6 — Webapp PWA UI Specialist
- **Module**: [`webapp/src/api/types.ts`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/api/types.ts), [`webapp/src/api/client.ts`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/api/client.ts), [`webapp/src/api/mock.ts`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/api/mock.ts), `[NEW]` [`webapp/src/components/options/VpinGauge.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/components/options/VpinGauge.tsx), `[NEW]` [`webapp/src/components/options/SmartOrderRouterView.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/components/options/SmartOrderRouterView.tsx), [`webapp/src/screens/PaperBroker.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/screens/PaperBroker.tsx), [`webapp/src/screens/OptionsChain.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/screens/OptionsChain.tsx)
- **Features**:
  - `VpinGauge.tsx`: Circular/arc VPIN toxicity gauge $[0\%, 100\%]$ with color-coded safe/toxic zones and volume bucket imbalance history bars.
  - `SmartOrderRouterView.tsx`: Interactive COB vs. Legging routing decision matrix, Expected Edge vs Hung Leg Risk chart, and 1-click execution routing toggle.
  - Full TypeScript types, mock data parity, and Vitest test suites.

---

## Verification Plan

### Automated Tests
1. `pytest tests/test_options_vpin.py` (Volume bucket synchronization, BVC formula, VPIN metric calculation, toxicity regime gating).
2. `pytest tests/test_options_sor.py` (COB vs Legging routing math, legging hazard simulation, hung leg probability, policy selection).
3. `pytest tests/test_pilots_paper_broker.py` (API endpoints).
4. `npm run --prefix webapp typecheck` & `npm test`.
