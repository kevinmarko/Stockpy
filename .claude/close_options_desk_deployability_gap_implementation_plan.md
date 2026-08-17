# Implementation Plan: Close Options-Desk Deployability Runtime Gap (PR #765 F4 Remediation)

## Goal Description
Remediate the runtime deployability enforcement gap across the five options desk strategies (`zero_dte_engine`, `dispersion_trading`, `earnings_crush`, `vol_mispricing`, `vrp_premium_selling`) and resolve all five identified compounding defects without compromising mathematical integrity or violating platform constraints.

## Problem Context
During the PR #765 review (Finding F4), the auditor noted that while several options strategies had failing or ungateable backtest statuses recorded in `docs/VALIDATION_STRATEGY_FIX_LOG.md`, their runtime execution endpoints did not communicate deployability gate statuses. Furthermore, compounding defects were discovered:
1. `zero_dte_engine.py`: Fabricated `$1.50` default fill price when price fields were omitted.
2. `dispersion_trading.py`: Baskets for SPY and QQQ shared identical default weights.
3. `dispersion_trading.py`: `execute_dispersion_trade(basket=None)` hardcoded Long Dispersion rather than evaluating the correlation spread sign.
4. `api/pilots_api.py`: Execution endpoints lacked `gate_status` metadata in their JSON responses.
5. `docs/signals/vrp_premium_selling.md`: Had duplicate section headers.

## Remediations
1. **`pilots/zero_dte_engine.py`**:
   - Replaced `$1.50` fill price fabrication with real Black-Scholes theoretical pricing from spot, or explicit failure rejection (`ok: False`).
2. **`pilots/dispersion_trading.py`**:
   - Introduced `INDEX_CONSTITUENTS_MAP` and `INDEX_WEIGHTS_MAP` with distinct weights for SPY vs QQQ.
   - Evaluated correlation spread dynamically in `execute_dispersion_trade` to derive `is_long_dispersion`.
3. **`api/pilots_api.py`**:
   - Created `OPTIONS_DESK_DEPLOYABILITY_GATES` and injected `gate_status` into `post_options_earnings_crush_execute`, `post_options_dispersion_execute`, and `post_options_zero_dte_execute`.
4. **`docs/signals/vrp_premium_selling.md` & `docs/VALIDATION_STRATEGY_FIX_LOG.md`**:
   - Cleaned up duplicate headers and documented all remediation steps.
5. **`tests/test_options_desk_deployability_runtime_gap.py`**:
   - Added unit and integration tests verifying the runtime gap closure.
