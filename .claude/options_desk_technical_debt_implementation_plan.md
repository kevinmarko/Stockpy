# Implementation Plan: Resolve Flagged Options Desk Technical Debt & Correctness Issues

This plan addresses the four specific open issues flagged in the options trading desk modules and frontend:

1. **`pilots/options_alerts.py` Production Callers**: Wire `dispatch_uoa_whale_alert`, `dispatch_earnings_crush_alert`, and `dispatch_delta_hedge_alert` into their respective production evaluation pipelines.
2. **`UnusualFlowFeed.tsx` Contract Mismatch**: Fix client-backend contract mismatch between `records` vs `trades`, case differences in `option_type` ("CALL" vs "call"), and missing `id` / aggressiveness fields so UOA feed renders cleanly.
3. **`pilots/copula_stat_arb.py` Lookahead Leaks**: Fix the full-sample `latest_beta` scalar in `evaluate_copula_stat_arb_pair` and full-sample copula family fit in `generate_copula_stat_arb_signals` by enforcing strictly causal, time-varying trailing calculations.
4. **Black-Scholes & Greeks Consolidation**: Consolidate redundant BS/Greeks implementations across `options_sor.py`, `vol_mispricing.py`, `dispersion_trading.py`, `gamma_scalper.py`, and `volatility_surface.py` into the canonical `pilots/options_risk.py`.

---

## User Review Required

> [!IMPORTANT]
> - **Alert Dispatching**: Alerts are non-blocking and protected by `observability/alerts.py`'s existing deduplication window (`ALERT_DEDUP_WINDOW_SECONDS`, default 900s). When live conditions qualify (Whale sweeps $\ge \$250\text{k}$, Crush Edge $\ge 1.35\times$, Delta imbalance breaching tolerance band), alerts will now fire to configured alert channels (Discord/Slack/email/console) rather than being dormant.
> - **Copula Stat Arb**: Causal fixes ensure all backtesting and evaluation paths are strictly lookahead-free.

---

## Proposed Changes

### 1. Alert Dispatchers Production Wiring

#### [MODIFY] [`pilots/unusual_options_flow.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/resolve_flagged_technical_debt/pilots/unusual_options_flow.py)
- In `scan_unusual_options_activity()` and `get_unusual_options_activity()`, when records are scanned and qualify under whale thresholds (`vol_oi_ratio >= DEFAULT_UOA_WHALE_MIN_VOL_OI` and `notional >= DEFAULT_UOA_WHALE_MIN_NOTIONAL`), invoke `dispatch_uoa_whale_alert(record)` in a safe, non-blocking try/except block.

#### [MODIFY] [`pilots/earnings_crush.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/resolve_flagged_technical_debt/pilots/earnings_crush.py)
- In `evaluate_earnings_crush_candidates()`, when an Iron Condor candidate is generated with `crush_edge_ratio >= DEFAULT_EARNINGS_CRUSH_MIN_EDGE` (1.35x), invoke `dispatch_earnings_crush_alert(candidate)` in a safe, non-blocking try/except block.

#### [MODIFY] [`pilots/options_hedging.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/resolve_flagged_technical_debt/pilots/options_hedging.py)
- In `get_delta_hedge_preview()` and `execute_delta_hedge()`, when delta imbalance exceeds tolerance band (`required_action=True`), invoke `dispatch_delta_hedge_alert(preview_or_order)` in a safe, non-blocking try/except block.

#### [MODIFY] [`docs/architecture/execution.md`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/resolve_flagged_technical_debt/docs/architecture/execution.md)
- Update documentation to reflect that the three dispatchers are actively wired into production evaluation paths.

---

### 2. Frontend Contract & UOA Feed Parity

#### [MODIFY] [`api/pilots_api.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/resolve_flagged_technical_debt/api/pilots_api.py)
- In `GET /pilots/options/flow/unusual`, return both `records` and `trades` in response dict: `{"count": len(records), "records": records, "trades": records}` for dual compatibility.

#### [MODIFY] [`webapp/src/api/types.ts`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/resolve_flagged_technical_debt/webapp/src/api/types.ts)
- Update `UnusualOptionsFlowResponse` to include both `trades: UnusualOptionTrade[]` and `records?: UnusualOptionTrade[]`.
- Update `UnusualOptionTrade` interface to support optional/flexible fields from `UOARecord` (`contract_symbol`, `aggressiveness`, `trade_type`, `option_type`, etc.).

#### [MODIFY] [`webapp/src/components/options/UnusualFlowFeed.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/resolve_flagged_technical_debt/webapp/src/components/options/UnusualFlowFeed.tsx)
- Read from `flowQuery.data?.trades || flowQuery.data?.records || []`.
- Standardize row identifiers with a fallback key: `t.id || t.contract_symbol || `${t.symbol}-${t.expiration}-${t.strike}-${t.option_type}-${t.timestamp}``.
- Case-insensitively check `option_type` (`"CALL"` / `"call"`), `trade_type` / `aggressiveness` (`"SWEEP"` vs `"ask_sweep"` / `"bid_sweep"`), and `aggressor_side`.

#### [MODIFY] [`webapp/src/components/options/UnusualFlowFeed.test.tsx`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/resolve_flagged_technical_debt/webapp/src/components/options/UnusualFlowFeed.test.tsx)
- Add tests confirming that both `trades` and `records` formats render correctly and that lowercase backend data formats are properly displayed.

---

### 3. Lookahead-Free Copula Statistical Arbitrage

#### [MODIFY] [`pilots/copula_stat_arb.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/resolve_flagged_technical_debt/pilots/copula_stat_arb.py)
- In `evaluate_copula_stat_arb_pair`:
  - Replace `spread_portfolio = y - kalman_res.latest_beta * x` with the causal time-varying spread $S_t = y_t - \beta_t x_t$ (using `kalman_res.beta` with warm-up stabilization, matching `compute_copula_spread_and_zscore`).
- In `generate_copula_stat_arb_signals`:
  - Enforce rolling / expanding window copula estimation so that copula tail risk at historical step $t$ uses strictly causal trailing data $y[:t], x[:t]$ rather than full-sample fitting.

#### [MODIFY] [`tests/test_copula_stat_arb.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/resolve_flagged_technical_debt/tests/test_copula_stat_arb.py)
- Add targeted tests asserting that perturbation of future price bars does not change historical signals or half-life metrics (zero lookahead verification).

---

### 4. Consolidate Black-Scholes & Greeks Implementations

#### [MODIFY] [`pilots/options_risk.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/resolve_flagged_technical_debt/pilots/options_risk.py)
- Confirm `calculate_black_scholes_greeks` exports all necessary Greeks and pricing components (`delta`, `gamma`, `theta_daily`, `vega_1pct`, `price`).

#### [MODIFY] [`pilots/options_sor.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/resolve_flagged_technical_debt/pilots/options_sor.py)
- Update `calculate_leg_greeks` to delegate directly to `pilots.options_risk.calculate_black_scholes_greeks`.

#### [MODIFY] [`pilots/vol_mispricing.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/resolve_flagged_technical_debt/pilots/vol_mispricing.py)
- Update `calculate_black_scholes_greeks_and_price` to delegate directly to `pilots.options_risk.calculate_black_scholes_greeks`.

#### [MODIFY] [`pilots/dispersion_trading.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/resolve_flagged_technical_debt/pilots/dispersion_trading.py)
- Update `calculate_option_price` to delegate directly to `pilots.options_risk.calculate_black_scholes_greeks`.

#### [MODIFY] [`pilots/gamma_scalper.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/resolve_flagged_technical_debt/pilots/gamma_scalper.py)
- Update `_black_scholes_greeks` to delegate directly to `pilots.options_risk.calculate_black_scholes_greeks`.

#### [MODIFY] [`pilots/volatility_surface.py`](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/resolve_flagged_technical_debt/pilots/volatility_surface.py)
- Update `_black_scholes_price` and `_black_scholes_delta` to delegate directly to `pilots.options_risk.calculate_black_scholes_greeks`.

---

## Verification Plan

### Automated Tests
1. **Python Unit Tests**:
   ```bash
   pytest tests/test_options_alerts.py tests/test_unusual_options_flow.py tests/test_earnings_crush.py tests/test_options_hedging.py tests/test_copula_stat_arb.py tests/test_options_sor.py tests/test_vol_mispricing.py tests/test_dispersion_trading.py tests/test_gamma_scalper.py tests/test_volatility_surface.py -q
   ```
2. **Lookahead Perturbation Tests**:
   - Run tests ensuring that modifying future price returns does not alter past signals or Kalman/Copula statistics.
3. **AST Safety Audit**:
   - Run AST tests to ensure no circular or forbidden imports are introduced into any `pilots/` modules:
   ```bash
   python3 scripts/auditor/stockpy_codebase_auditor.py --root . --fail-on HIGH
   ```
4. **Webapp Parity & Typecheck**:
   ```bash
   npm run --prefix webapp typecheck
   npm test --prefix webapp -- --run
   ```
