# Signal: `multifactor`

**File:** `signals/multifactor.py`  
**Default weight:** 15.0  
**Score range:** `[-1.0, +1.0]`  
**Regime gate:** Always active  
**Hook pattern:** Two-phase `pre_compute` / `compute`  
**Microcap exclusion threshold:** $300M (configurable via `MULTIFACTOR_MICROCAP_THRESHOLD`)  
**Pilot:** Multifactor (`multifactor`, `pilots/catalog.py`) — backed by a real,
PBO/DSR-gated backtest (`multifactor_lowvol_size` in `scripts/refresh_validations.py`),
but the backtest validates only the Low-Vol + Size sleeve; Value/Quality need
point-in-time fundamentals no free vendor supplies, so the headline Sharpe reflects the
honest, narrower proxy — not the full 4-factor blend.

---

## Rationale

This module implements a composite of four Fama-French factors adapted for the
long-only advisory context:

> **Primary reference:** Hou, K., Xue, C., & Zhang, L. (2020). "Replicating Anomalies."
> *The Review of Financial Studies*, 33(5), 2019–2133.

The four factors and their priors:

| Factor | Variable | Prior (Hou-Xue-Zhang) |
|--------|----------|----------------------|
| **Value** | Book-to-market + earnings yield | High value → higher returns |
| **Quality** | ROE + operating margin | High quality → higher returns (Asness, Frazzini & Pedersen 2019) |
| **Low Volatility** | −realized vol (60-day) | Low vol → higher risk-adjusted returns (Frazzini & Pedersen 2014) |
| **Size** | −log(market cap) | Smaller → higher expected returns (Fama & French 1993) |

Note: **momentum is handled by the separate `cross_sectional_momentum` module** following
the Hou-Xue-Zhang architecture recommendation to avoid double-counting momentum in the
multifactor composite.

---

## Two-Phase Hook

```
pre_compute(universe_df, context):
    1. For each symbol, read raw factor inputs from dashboard_df:
       book_to_market, earnings_yield, quality_factor_score,
       low_vol_score, log_market_cap
    2. EXCLUDE tickers with market_cap < MULTIFACTOR_MICROCAP_THRESHOLD from z-scoring
       (they stay in the universe but receive a neutral 0.0 score)
    3. Z-score each factor across the remaining universe; winsorise at ±3
    4. Average factors into Multifactor_Composite per symbol; re-clip to ±3
    5. Write composite back to context.multifactor_scores[symbol]

compute(row, context):
    z = context.multifactor_scores.get(symbol, 0.0)
    score = tanh(z / 2)    # maps ±3 → approx ±0.96; maps 0 → 0
```

The `tanh` mapping ensures the score is bounded in [−1, +1] without hard clipping,
preserving relative ordering while bounding extremes.

---

## Factor Construction

```python
# Value
book_to_market = 1 / pb_ratio        # NaN if pb_ratio unavailable
earnings_yield = 1 / pe_ratio        # NaN if pe_ratio unavailable

# Quality
quality_factor_score = 0.5 * roe + 0.5 * operating_margin   # or −debt_to_equity fallback

# Low-Vol (negative realised vol → lower vol = positive score)
low_vol_score = −realized_vol_60d

# Size (negative log cap → smaller = positive score)
log_market_cap = log(market_cap)
Size_Z = −zscore(log_market_cap)
```

All missing fields → `NaN`, never `0.0` (CONSTRAINT #4). A ticker with all-NaN
factor inputs receives `Multifactor_Composite = 0.0` (neutral score).

---

## Microcap Exclusion

Tickers with `market_cap < MULTIFACTOR_MICROCAP_THRESHOLD` ($300M) are excluded from
the cross-sectional z-scoring population. Including them would distort the cross-section:
a $100M company with a high book-to-market ratio is not comparable to a $10B company
with the same ratio — the former likely reflects distress, the latter value.

Microcap tickers:
- Still appear in the universe and receive advisory signals from all other modules.
- Receive `Multifactor_Composite = 0.0` (neutral) from this module specifically.
- Are never assigned a fabricated score.

---

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| Universe has < 2 non-microcap tickers | Z-scoring is meaningless. Composite defaults to 0.0 for all tickers. |
| `pb_ratio` or `pe_ratio` not available (yfinance NaN) | `book_to_market` and/or `earnings_yield` are NaN. Composite uses only available factors. If all four factors are NaN, composite = 0.0. |
| `realized_vol_60d` not available (< 60 bars) | `low_vol_score = NaN`. Composite built from remaining 3 factors. |
| Earnings not yet reported (stale) | `earnings_yield` may lag by up to 1 quarter. `FUNDAMENTALS_REFRESH_DAYS=1` ensures daily fundamentals updates, but the underlying data source (yfinance trailing-12M EPS) lags earnings releases by 1–2 days. |
| One extreme outlier distorts z-scores | Winsorisation at ±3 standard deviations prevents any single outlier from collapsing the cross-sectional distribution. |

---

## Empirical Notes

- The `tests/test_multifactor.py` synthetic test confirms that a 50-stock universe
  with engineered high-value/high-quality/low-vol characteristics correctly assigns
  those names to the top quintile of `Multifactor_Composite`.
- The validation harness in `tests/test_validation_multifactor.py` is restricted to
  Low-Vol + Size (the two factors derivable from price/share-count data alone). Value
  and Quality require point-in-time historical fundamentals that yfinance's current
  snapshot cannot provide — extending to those factors requires PIT fundamental data
  from `HistoricalStore.get_fundamentals_history()` after ≥ 90 days of Phase 3 accumulation.
- **Weight note:** 15.0 in the points-scale system (where `contribution = score × weight`)
  is equivalent to an effective multiplier that ranges from −15 to +15 pts. This was
  rescaled from a proposed 0.15 relative weight that would have been numerically inert
  at this codebase's scoring scale.

---

## Backtest Validation (`multifactor_lowvol_size`, 2026-07)

The `multifactor_lowvol_size` adapter (Low-Vol + Size sleeve only) was previously a
fully-invested, always-long book with no drawdown control — MaxDD 34.0%, failing the
harness's `<30%` gate despite an already-passing Sharpe (0.669) and PBO (0.000).

**Fix:** `SPY` was added to the adapter's `STRATEGY_REGISTRY` universe as a
benchmark-only trend-filter input (excluded from the tradeable book and from `y`,
mirroring `relative_strength_xsec`'s existing SPY-splitting pattern). The book now
de-risks to cash whenever `SPY < SPY.rolling(200).mean()` (Faber 2007) — the same fixed
rule already load-bearing in `macd_trend`'s passing `MACD_TrendFilter` variant. Degrades
gracefully (byte-identical to before) when SPY is absent from the ticker set, so offline
test fixtures are unaffected.

| Metric | Before | After | Gate |
|---|---|---|---|
| Sharpe | 0.617 | 0.621 | > 0.50 ✅ |
| PBO | 0.000 | 0.000 | < 0.50 ✅ |
| DSR | 1.000 | 1.000 | > 0.95 ✅ |
| MaxDD | 34.0% | **21.1%** | < 30% ✅ (was FAIL) |
| `deployable` | False | **True** | |

See [PR #310](https://github.com/kevinmarko/Stockpy/pull/310) and
[`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) for the
full 12-strategy series this fix was part of.
