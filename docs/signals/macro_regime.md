# Signal: `macro_regime`

**File:** `signals/macro_regime.py`  
**Default weight:** 45.0 (highest of all modules)  
**Score range:** `[-1.0, +1.0]`  
**Regime gate:** Always active (this module *defines* the regime context for all others)

---

## Rationale

The macro regime is the single most powerful predictor of equity market returns at the
portfolio level. Academic support spans decades:

- **Fama & French (1989)** documented that business-cycle variables explain a large
  fraction of expected return variation.
- **Ilmanen (2011)** "Expected Returns" demonstrates that regime-aware allocation
  dramatically outperforms static allocation over full market cycles.
- The **Sahm Rule** (Claudia Sahm, 2019 Fed note) is an empirically validated real-time
  recession indicator with a perfect post-WWII track record when the 3-month average
  unemployment rate rise exceeds 0.5 pp vs. the prior 12-month low.

The 45.0 weight reflects the empirical observation that regime-blind stock-picking
(getting the stock right, the cycle wrong) produces inferior risk-adjusted returns
compared to starting with the macro environment and working down.

---

## Signal Logic

| Condition | Score contribution | Points |
|-----------|-------------------|--------|
| `market_regime == "RISK ON"` | +10 pts | Favorable macro |
| `market_regime == "NEUTRAL"` | 0 pts | No adjustment |
| `market_regime == "RECESSION"` | −15 pts | Yield curve inverted + Sahm ≥ 0.6 |
| `market_regime == "CREDIT EVENT"` | −25 pts | HY OAS > 6% |
| `killSwitch == True` | Additional −5 pts | Sahm ≥ 0.5 OR VIX > 30 |
| Sector = Financials/Real Estate + RECESSION/CREDIT EVENT | Additional −15 pts | Structural exposure |
| Sector = Consumer Staples/Healthcare + RECESSION | Additional +10 pts | Defensive premium |

**Normalization:** raw points divided by 45.0 → score ∈ [−1, +1].

The kill switch is also wired *outside* this module: when `MacroEconomicDTO.killSwitch`
is `True`, `engine/advisory.py` forces all BUY/STRONG BUY signals to HOLD before the
holding-aware overlay even runs.

---

## Regime Classification Inputs (from `macro_engine.py`)

| FRED Series | Threshold | Role |
|-------------|-----------|------|
| `T10Y2Y` (10y−2y spread) | < −0.25 → inversion | Yield curve |
| `BAMLH0A0HYM2` (HY OAS) | > 6% → credit stress | Credit spread |
| `SAHMREALTIME` | ≥ 0.5 → kill switch / ≥ 0.6 → RECESSION | Unemployment momentum |
| `VIXCLS` | > 30 → kill switch | Volatility regime |

The HMM second opinion (`regime/hmm_regime.py`) can downgrade RISK ON → NEUTRAL when
`hmm_risk_on_probability < 0.30`, but cannot upgrade any regime.

---

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| FRED API unavailable | `MacroEngine` returns neutral defaults (NEUTRAL regime, `killSwitch=False`). All regime scores default to 0 — signal is informationless, not misleading. |
| HMM fit fails (< 100 rows) | `hmm_risk_on_probability = None`; module ignores the multiplier. Kelly Target unchanged. |
| Sahm Rule series stale | Falls back to the most recent cached value in `HistoricalStore.get_macro('SAHMREALTIME')`. |
| RECESSION regime with false VIX spike | Sector veto and kill switch both fire; operator must manually deactivate the kill switch after confirming it is a false positive. |

---

## Empirical Notes

- The sector veto (Finance/Real Estate + inverted yield curve) is motivated by the
  2007–2009 episode where the two sectors suffered 70–80% peak-to-trough losses while
  the rest of the market fell ~50%.
- The defensive premium (Consumer Staples/Healthcare in RECESSION) captures the
  well-documented flight-to-quality effect; historically these sectors outperform the
  market by 15–25% during recessions (Fama/French 5-factor data, 1963–2023).

---

## Adjusting the Weight

Reduce below 30.0 only if your strategy is explicitly **macro-agnostic** (e.g. a pure
pairs trade). For the multi-asset advisory pipeline, reducing this weight below 30 is not
recommended without also re-validating the strategy harness (`python -m validation.harness`).
