# Task Tracking: Multi-Leg Options Paper Trading, Strategy Auto-Execution, Portfolio Risk Greeks & Stage 4 ML Pipeline

## Overview
- **Objective**: Full end-to-end multi-leg option paper trading, automated strategy paper execution, options portfolio risk & aggregate Greeks, backtest harness integration, and Stage 4 ML meta-labeling.
- **Branch**: `implement_multi_leg_paper_trading`
- **Current Status**: All 5 Phases Completed & Fully Verified.

## Phase Checklist

### Phase 1: Multi-Leg Paper Trading Core (COMPLETE ✅)
- [x] Position sizing logic for multi-leg option spreads (`pilots/order_sizing.py`)
- [x] SQLite paper account store support for atomic multi-leg orders and short options (`data/paper_account_store.py`)
- [x] Multi-leg execution in `FMPPaperBroker` (`execution/fmp_paper_broker.py`)
- [x] Single-leg and multi-leg order execution API helper (`pilots/paper_broker_options_order.py`)
- [x] REST endpoint `POST /pilots/paper-broker/options/order` in `api/pilots_api.py`
- [x] Pilots PWA Paper Broker screen option badges & mock API updates (`webapp/`)
- [x] Unit & integration tests in `tests/test_order_sizing.py`, `tests/test_paper_account_store.py`, `tests/test_fmp_paper_broker.py`, `tests/test_paper_broker_options_order.py`

### Phase 2: Automated Strategy Paper Execution (COMPLETE ✅)
- [x] Configuration settings in `settings.py` (`PAPER_OPTIONS_AUTO_EXECUTE_ENABLED`, `MAX_OPTION_NOTIONAL_PER_TRADE`, `MAX_CONCURRENT_OPTION_POSITIONS`)
- [x] Automated executor engine `OptionsPaperExecutor` (`execution/options_paper_executor.py`)
- [x] Orchestrator cycle integration in `main.py`
- [x] API endpoints `GET/POST /pilots/paper-broker/strategy-options/*` in `api/pilots_api.py`
- [x] Automated Strategy Options execution card and candidate table in `webapp/src/screens/PaperBroker.tsx`
- [x] Unit and API tests in `tests/test_options_paper_executor.py` and `tests/test_pilots_paper_broker.py`

### Phase 3: Options Portfolio Risk & Aggregate Greeks (COMPLETE ✅)
- [x] Greeks & Risk calculation engine in `pilots/options_risk.py` ($\Delta_{\text{net}}$, $\Delta_{\$}$, $\Gamma$, $\Theta_{\text{daily}}$, $\mathcal{V}_{\text{1\%}}$, $\beta$-weighted $\Delta_{\text{SPY}}$)
- [x] Paper broker helper integration `get_portfolio_greeks` in `pilots/paper_broker.py`
- [x] REST API endpoint `GET /pilots/paper-broker/greeks` in `api/pilots_api.py`
- [x] TypeScript interfaces `PositionGreekBreakdown` and `PortfolioGreeks` in `webapp/src/api/types.ts`
- [x] Client API and Mock API parity in `webapp/src/api/client.ts` and `webapp/src/api/mock.ts`
- [x] Portfolio Greeks summary cards & positions table columns in `webapp/src/screens/PaperBroker.tsx`
- [x] Unit and API tests in `tests/test_options_risk.py`, `tests/test_pilots_paper_broker.py`, and `webapp/src/screens/PaperBroker.test.tsx`

### Phase 4: Options Backtest Harness Integration (COMPLETE ✅)
- [x] Multi-leg options simulation engine (`validation/options_harness.py`)
- [x] Payoff curve calculations, profit-taking, stop-losses, and expiration resolution
- [x] Integration with `validation/harness.py` for PBO/DSR/Sharpe/MaxDD options evaluation & stress testing
- [x] CLI entrypoint `python -m validation.options_harness`
- [x] Unit tests in `tests/test_options_harness.py`

### Phase 5: Stage 4 ML Meta-Labeling & Model Feed (COMPLETE ✅)
- [x] Feature extraction from executed paper options trades (`ml/options_meta_labeler.py`)
- [x] Secondary ML meta-labeler predicting probability of profit ($P(\text{Win})$)
- [x] Dynamic position sizing multipliers and gating integrated into `execution/options_paper_executor.py`
- [x] Setting `OPTIONS_META_LABELER_ENABLED` added to `settings.py`
- [x] Unit tests in `tests/test_options_meta_labeler.py`
