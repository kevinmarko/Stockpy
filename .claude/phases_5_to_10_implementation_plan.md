# Implementation Plan: Phases 5 to 10 — Advanced Volatility Alpha, Meta-Labeling & Execution Lifecycles

## Overview
Phases 5 through 10 encompass the quantitative core for advanced volatility trading, machine learning meta-labeling, position auto-exits, delta hedging, monotonic PCHIP volatility surface modeling, scenario matrices, and earnings crush trading.

## Phase Breakdown

### Phase 5: Stage 4 ML Meta-Labeling
- **Module**: `ml/options_meta_labeler.py`, `execution/options_paper_executor.py`
- **Capability**: HistGradientBoosting classifier predicting $P(\text{Win})$ to scale position sizing and apply risk vetoes before trade routing.

### Phase 6: Expiration Cash Settlement & Multi-Leg Lifecycle
- **Module**: `pilots/paper_broker_options_order.py`, `pilots/paper_broker.py`
- **Capability**: Atomic multi-leg order execution, cash settlement at expiration, and profit target/stop loss threshold management.

### Phase 7: Dynamic Position Lifecycle & SPY Delta Hedging
- **Module**: `pilots/options_hedging.py`, `pilots/options_risk.py`
- **Capability**: Portfolio-wide $\beta$-weighted dollar delta accumulation and automated SPY equity hedge generation.

### Phase 8: Monotonic PCHIP Volatility Surface
- **Module**: `pilots/volatility_surface.py`
- **Capability**: Arbitrage-free monotonic cubic spline interpolation across strike moneyness and expiration term structures, extracting 25$\Delta$ skew and ATM term structures.

### Phase 9: 2D/3D Scenario Matrix & Stress Testing
- **Module**: `pilots/scenario_matrix.py`
- **Capability**: Full multi-leg re-pricing across $S \times \sigma$ shock grids with historical crisis overlays (Black Monday, 2008 GFC, 2020 COVID).

### Phase 10: Earnings Volatility Crush & Event Move Scanner
- **Module**: `pilots/earnings_crush.py`
- **Capability**: Cross-referencing 8-quarter historical post-earnings moves vs implied straddle pricing to identify volatility crush arbitrage and construct Iron Condors.

---

## Verification Plan
- Automated pytest across all Phase 5-10 test suites (`test_options_paper_executor.py`, `test_pilots_paper_broker.py`, `test_options_lifecycle.py`, `test_options_hedging.py`, `test_volatility_surface.py`, `test_scenario_matrix.py`, `test_earnings_crush.py`).
- TypeScript typecheck (`tsc --noEmit`).
- Bandit SAST scan.
