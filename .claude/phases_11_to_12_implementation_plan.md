# Implementation Plan: Phases 11 to 12 — UOA Sentiment, Corsi HAR-RV & Strike Mispricing

## Overview
Phases 11 and 12 provide institutional-grade order flow analytics and realized volatility forecasting:
- **Phase 11**: Unusual Options Activity (UOA) flow sentiment scanner, sweep classification, IV burst detection, and webhook alerts.
- **Phase 12**: Corsi (2009) Heterogeneous Autoregressive Realized Volatility (HAR-RV) multi-horizon term structure forecasting and strike mispricing arbitrage scanner.

## Subsystem Architecture

### Phase 11: Unusual Options Activity & Flow Sentiment
- **Module**: `pilots/unusual_options_flow.py`, `pilots/options_alerts.py`
- **Core Logic**:
  - Filter institutional order flow where Volume/OI $> 3.0$ and Notional $> \$100\text{k}$.
  - Classify trades into Buyer-Aggressive Ask Sweeps (Bullish Calls / Bearish Puts) vs Seller-Aggressive Bid Sweeps (Bearish Calls / Bullish Puts).
  - Compute Net Flow Sentiment score $\in [-1.0, +1.0]$ and detect IV Expansion Bursts ($IV > 1.25 \times HV_{30}$).
  - Dispatch multi-channel webhook notifications (Discord / Slack / Generic webhook).

### Phase 12: Corsi HAR-RV & Strike Mispricing Scanner
- **Modules**: `pilots/har_volatility.py`, `pilots/vol_mispricing.py`
- **Core Logic**:
  - Fit Corsi (2009) HAR-RV autoregressive model: $RV_{t+1}^{(d)} = c + \beta_d RV_t^{(d)} + \beta_w RV_t^{(w)} + \beta_m RV_t^{(m)}$.
  - Generate multi-horizon forward realized volatility forecasts ($\hat{\sigma}_{1d}, \hat{\sigma}_{5d}, \hat{\sigma}_{22d}$).
  - Invert option chain prices to market implied volatilities and calculate Mispricing Spread ($\Delta \sigma = IV_{\text{market}} - \hat{\sigma}_{\text{HAR-RV}}$).
  - Categorize contracts into Overvalued (Sell / Write Premium) vs Undervalued (Buy / Long Volatility).

---

## Verification Plan
1. **Automated Unit Tests**:
   - `pytest tests/test_unusual_options_flow.py tests/test_options_alerts.py tests/test_har_volatility.py tests/test_vol_mispricing.py -v`
2. **Frontend Typecheck & Vitest**:
   - `tsc --noEmit` and `vitest run`
3. **Static & Security Audits**:
   - `stockpy_codebase_auditor.py` and `bandit -r .`
