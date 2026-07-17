# Signal: `rsi2_mean_reversion`

**File:** `signals/rsi2_mean_reversion.py`  
**Default weight:** 10.0  
**Score range:** `[0.0, +1.0]` (long-only — no negative scores)  
**Regime gate:** `is_active_in_regime()` returns `False` in RECESSION, CREDIT EVENT, or VIX > 30  
**Pilot:** Dip Buyer (`dip-buyer`, `pilots/catalog.py`) — backed by a real, PBO/DSR-gated
backtest (`rsi2_mean_reversion` in `scripts/refresh_validations.py`).

---

## Rationale

RSI(2) mean reversion is the single most back-tested short-term strategy in the retail
quant literature:

> **Reference:** Connors, L., & Alvarez, C. (2009). *Short-Term Trading Strategies
> That Work*. TradingMarkets Publishing Group.

The strategy exploits the well-documented tendency of large-cap stocks to temporarily
dip below their 200-day SMA during otherwise-uptrending markets. Key findings:

- **2-period RSI** is far more sensitive than RSI-14: it can reach single-digit levels
  within 1–3 bars of a pullback, whereas RSI-14 takes 2–3 weeks to reach 30.
- **Trend filter (Close > SMA-200):** Only long in uptrends. In downtrends, RSI(2) < 10
  is the *beginning* of a larger decline, not a bounce. The trend filter alone eliminates
  most drawdown exposure from the 2008–2009 period.
- **Exit at Close > SMA-5 (already-reverted guard):** If the stock has already bounced
  back above its 5-day SMA, the entry thesis (oversold bounce) has already played out.
  Firing a new entry at that point is chasing, not mean-reverting.

---

## Signal Logic

```python
IS_ACTIVE:
    market_regime NOT IN {RECESSION, CREDIT EVENT}
    AND vix <= 30.0

SCORE (when active):
    trend_filter   = (Close > SMA_200)           # primary long-only gate
    reversal_guard = (Close > SMA_5)             # already reverted
    oversold       = (RSI_2 < oversold_threshold)  # default threshold = 10

    IF NOT trend_filter:  score = 0.0
    IF reversal_guard:    score = 0.0   (bounce already happened)
    IF oversold:
        score = (oversold_threshold - RSI_2) / oversold_threshold
        # RSI_2 = 0 → score = 1.0; RSI_2 = 10 → score = 0.0 (boundary)
    ELSE:
        score = 0.0
```

**Key property:** score is strictly non-negative (long-only signal). The `SignalAggregator`
adds this module's contribution only in the positive direction to the weighted sum.

---

## Regime Gate (`is_active_in_regime`)

```python
def is_active_in_regime(self, macro: MacroEconomicDTO) -> bool:
    if macro.market_regime in {"RECESSION", "CREDIT EVENT"}:
        return False
    if macro.vix > 30.0:
        return False
    return True
```

When `is_active_in_regime` returns `False`, the `SignalAggregator` skips this module
entirely — `compute()` is not called and the module contributes 0 pts to `final_score`.
This is more reliable than relying on `compute()` to self-zero because:
1. It is enforced centrally in the aggregator, not in module logic.
2. The suppression appears in `score_log` as an explicit "suppressed by regime gate" entry.
3. Future modifications to the module cannot accidentally re-enable it in adverse regimes.

---

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| `RSI_2` not computed (< 5 bars of price history) | Score = 0.0 (insufficient history). No fabrication. |
| `SMA_200` not computed (< 200 bars) | Trend filter defaults to `False` → score = 0.0 (conservative: no signal when trend is uncertain). |
| `SMA_5` not computed (< 5 bars) | Reversal guard defaults to `True` (conservative: treats unknown as already-reverted, no entry). |
| VIX = 29.9 (just below threshold) | Module is active. The −5 pts from `macro_regime.killSwitch` (if Sahm ≥ 0.5) may still suppress the overall score below BUY threshold. |
| RSI(2) stuck below 10 for 5+ consecutive bars | Score stays near 1.0 each bar. The advisory system does not send repeated BUY signals for a held position — the holding-aware overlay (Case B/C) applies at the `engine/advisory.py` level. |

---

## Empirical Finding (Backtest — from `tests/test_validation_rsi2.py`)

Running the strategy over SPY 2000–2023 via `validation.harness`:

| Variant | Notes |
|---------|-------|
| **Gated (trend filter + regime gate)** | Passes validation harness: PBO < 0.5, DSR > 0.95, net Sharpe > 0.50, max drawdown < 30%. |
| **Ungated (no regime gate)** | The strategy's **own trend filter** (Close > SMA-200) already fully excludes 2008 exposure — the long-only trend filter's protective effect is load-bearing in 2008. The regime gate (VIX > 30 suppression) adds meaningful additional protection primarily for **2020** (COVID crash Q1). |

**Implication:** Removing the `is_active_in_regime()` VIX gate would leave the strategy
exposed to the March 2020 draw-down. The 2008 immunity comes from the trend filter, not
the regime gate — but both are required for full coverage of historical stress episodes.

---

## STRATEGY_REGISTRY Backtest Validation (`rsi2_mean_reversion`, 2026-07)

Distinct from the `tests/test_validation_rsi2.py` finding above (a different code path
that reimplements similar logic inline): the production `_build_rsi2_adapter` in
`scripts/refresh_validations.py`, joined to the Dip Buyer Pilot's `rsi2_mean_reversion`
`STRATEGY_REGISTRY` entry, previously emitted **2 variants** (`RSI2_Gated`,
`RSI2_Ungated`) that were near-duplicates of each other — measured at 0.886 return
correlation, differing on only 10 of 4833 trading days (2005–2024) — driving PBO to
0.67 on top of an already-failing net Sharpe (0.41).

**Fix:** dropped `RSI2_Ungated`, keeping only `RSI2_Gated` (the empirically more robust
of the two: higher full-sample Sharpe, shallower drawdown). A single variant cannot
suffer CPCV selection-bias PBO by construction (PBO=0.0, DSR=1.0).

| Metric | Before | After | Gate |
|---|---|---|---|
| Sharpe | 0.41 | 0.276 | needs > 0.50 — **still FAILS** |
| PBO | 0.67 | **0.000** | < 0.50 ✅ (was FAIL) |
| DSR | 0.998 | 1.000 | > 0.95 ✅ |
| MaxDD | 8.3% | 8.3% | < 30% ✅ (unchanged) |
| `deployable` | False | **False (honest)** | |

**Verdict:** PBO is genuinely fixed, but net Sharpe on the sole surviving variant
(0.276) is honestly below the gate — this is a genuinely weak short-horizon SPY
mean-reversion edge net of realistic transaction costs. Per this repo's honesty rules
(never loosen a gate or a filter to force a pass), the RSI<10 entry threshold and the
SMA-200/crash-recession risk-off filters that keep the strategy causal and conservative
were left untouched rather than tuned to chase a higher number.

See [PR #311](https://github.com/kevinmarko/Stockpy/pull/311) and
[`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) for the
full 12-strategy series this fix was part of.

---

## Position-Level Notes

The module scores the **entry** condition. Exit conditions are position-management rules:
- **5-bar time stop:** not implemented in `SignalModule.compute()` (which is stateless);
  would need to be tracked in `OrderManager` or a position-age field.
- **Close > SMA-5 exit:** the "already-reverted guard" prevents a new *entry* when the
  bounce has already occurred, but does not generate an explicit exit signal for an open
  position. The `aroon_trend` and `macd_momentum` modules naturally flip negative as
  the stock mean-reverts — aggregate score drops → HOLD or RISK REDUCE advisory follows.
