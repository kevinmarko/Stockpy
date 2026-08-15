# Goal Description

This plan outlines the implementation of **Tier C: Institutional Portfolio & Execution Infrastructure** (Phases 25 and 26). 

We will transition from deep volatility and predictive AI models to **institutional position sizing, cross-margin constraints, and execution sequencing**. This phase bridges the gap between pure mathematical signals and market microstructure impact.

### Phase 25: Hierarchical Risk Parity (HRP) & CVaR Optimizer
- Focuses on constructing diversified portfolios without the instability of Markowitz mean-variance optimization. We will implement López de Prado's HRP alongside a non-linear Expected Shortfall (CVaR) risk constraint.
- The output will visualize the clustering dendrogram and inverse-variance tree allocations.

### Phase 26: Almgren-Chriss Optimal Execution Engine
- Introduces block-trade splitting (TWAP/VWAP models).
- Balances temporary market impact vs. permanent impact and timing risk (variance), computing the most efficient execution trajectory.

## User Review Required

> [!IMPORTANT]
> Both engines will utilize standard math and AST-safe libraries (NumPy, SciPy). 
> For HRP, we rely heavily on `scipy.cluster.hierarchy`. 
> For CVaR constraints, we will use sequential least squares programming via `scipy.optimize`. 
>
> **Resolution:** Confirmed. `scipy.optimize` and `scipy.cluster` are perfectly aligned with AST safety bounds provided inputs are sanitized. All incoming data is cast directly to NumPy arrays with no dynamic string evaluations.

## Open Questions

> [!WARNING]
> The Pilots PWA requires 100% Mock/Live parity.
> Do we want the HRP optimization to rely on real incoming multi-asset correlation matrices in the Mock API, or should the Mock API return pre-computed clustered asset groupings to ensure front-end stability?
>
> **Resolution:** The Mock API must return pre-computed clustered asset groupings to maintain front-end stability and reliable rendering of the Recharts dendrograms. Live multi-asset correlations are strictly reserved for the Live API backend.

## Proposed Changes

---

### Phase 25: HRP & CVaR Optimizer Math Engine
Implements the core hierarchical risk parity clustering and bisection logic.

#### [NEW] [hrp_cvar_optimizer.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/sizing/hrp_cvar_optimizer.py)
- `compute_correlation_distance`: Computes tree distance metric.
- `quasi_diagonalization`: Reorders covariance matrix.
- `recursive_bisection`: Allocates weights based on inverse variance.
- `constrain_cvar`: Non-linear optimizer applying the 99% CVaR threshold.
#### [NEW] [test_hrp_cvar_optimizer.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/tests/test_hrp_cvar_optimizer.py)
- Validates the cluster groupings, bisection logic, and pure mathematical exactness.

---

### Phase 26: Almgren-Chriss Optimal Execution Engine
Implements block trade trajectory schedules.

#### [NEW] [almgren_chriss_router.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/execution/almgren_chriss_router.py)
- `compute_trading_trajectory`: Calculates $n_k$ (shares per interval).
- Computes Expected Shortfall and Variance of the execution strategy.
#### [NEW] [test_almgren_chriss_router.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/tests/test_almgren_chriss_router.py)
- Mathematical validations of trajectory decay rates.

---

### Pilots API & Backend Routing
Wire the new quantitative engines to the UI via secure fail-closed API endpoints.

#### [MODIFY] [pilots_api.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/api/pilots_api.py)
- `POST /pilots/portfolio/optimize/hrp-cvar` (Returns optimal weights and dendrogram links).
- `POST /pilots/execution/optimize/almgren-chriss` (Returns expected trajectory arrays).
#### [MODIFY] [test_pilots_paper_broker.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/tests/test_pilots_paper_broker.py)
- Integration tests ensuring proper formatting and exact AST safety.

---

### Webapp PWA & UI Integration
Surface the calculations visually in the React frontend.

#### [MODIFY] [types.ts](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/api/types.ts), [client.ts](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/api/client.ts), [mock.ts](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/api/mock.ts)
- Add complete type parity for the new endpoints.
#### [NEW] [HrpCvarOptimizerView.tsx](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/components/portfolio/HrpCvarOptimizerView.tsx)
- Recharts-based dendrogram clustering UI and asset allocation breakdown.
#### [NEW] [AlmgrenChrissRouterView.tsx](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/components/execution/AlmgrenChrissRouterView.tsx)
- Execution trajectory vs. time line chart with shortfall calculations.
#### [MODIFY] [PaperBroker.tsx](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/implement_multi_leg_paper_trading/webapp/src/screens/PaperBroker.tsx)
- Add toggles to enter the new Execution Strategy routing interface.

## Verification Plan

### Automated Tests
- Run `pytest` on all new engine test suites.
- Run `pytest tests/test_pilots_paper_broker.py` to test the API.
- Run `npm run --prefix webapp typecheck` to verify frontend TS integrity.
- Run `npm run --prefix webapp test` via vitest.
- Run `make verify` for total repository consistency checking.

### Manual Verification
- Render the Webapp mock UI manually if needed, checking console errors and visual layout mapping of the Almgren-Chriss curves.
