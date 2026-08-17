# Implementation Plan: Phase 1 — Backend Execution Integrity & Safety Gating

## Overview
Phase 1 focuses on execution safety, life-cycle automation, and quantitative hedging precision across the options desk and execution routers.

## Proposed Changes

### 1. Options Risk Engine & Beta Hedging (`pilots/options_risk.py` & `pilots/options_hedging.py`)
- **Real $\beta$-SPY Weighting**:
  - Implement `_resolve_symbol_beta(ticker: str) -> float` to look up symbol regression beta vs SPY from `HistoricalStore.get_symbol_beta()` / fundamental data.
  - Upgrade `calculate_portfolio_greeks()` to accumulate $\text{Beta-Weighted Dollar Delta} = \sum_i (\text{Dollar Delta}_i \times \beta_i)$ and compute $\text{Beta-Weighted SPY Delta} = \frac{\sum_i (\text{Dollar Delta}_i \times \beta_i)}{S_{\text{SPY}}}$.
  - Ensure positions with $\beta \neq 1.0$ (e.g. high-beta tech $\beta \approx 1.5$ or low-beta defensive $\beta \approx 0.6$) generate accurate delta hedge requirements.

### 2. 0DTE 15:45 ET Auto-Liquidation & Daemon Periodic Loop (`desktop/daemon_runtime.py` & `main.py`)
- **0DTE Fast Risk Lifecycle Management**:
  - In `desktop/daemon_runtime.py`, add 0DTE lifecycle step in `_run_one_cycle` / periodic loops when `settings.OPTIONS_0DTE_ENABLED` or `settings.OPTIONS_AUTO_EXIT_ENABLED` is active.
  - In `main.py`, invoke `pilots.zero_dte_engine.manage_0dte_exits` alongside standard multi-leg options auto-exit routines.

### 3. ML Meta-Labeler Startup Lifecycle (`execution/options_paper_executor.py`)
- **Cold-Start Verification**:
  - Ensure `_ensure_meta_labeler_loaded()` executes before directive scoring in paper broker cycles and API execution flows.

### 4. FIX 4.4 Gateway Session Gap-Fill & Sequence Handling (`execution/fix_gateway.py`)
- **Session Sequence Continuity**:
  - Verify `FixMsgType.RESEND_REQUEST` and `FixMsgType.SEQUENCE_RESET` in `FixSession` state machine, ensuring gap recovery transitions smoothly.

---

## Verification Plan

### Automated Tests
1. **Targeted Options Risk & Beta Tests**:
   - `pytest tests/test_options_risk.py` (validate non-1.0 beta SPY delta weighting).
   - `pytest tests/test_options_hedging.py` (validate delta hedge order generation with beta weighting).
2. **0DTE Auto-Close & Daemon Lifecycle**:
   - `pytest tests/test_zero_dte_engine.py` (test 15:45 ET hard stop liquidation).
   - `pytest tests/test_daemon_runtime.py` (test daemon cycle execution).
3. **Execution & FIX Gateway**:
   - `pytest tests/test_options_paper_executor.py`
   - `pytest tests/test_fix_gateway.py` (if applicable)
4. **CI & Static Auditing**:
   - `python3 scripts/auditor/stockpy_codebase_auditor.py --root . --fail-on HIGH`
   - `bandit -r . -x './tests,./webapp,./.venv,./node_modules,./gui' -ll -ii`
