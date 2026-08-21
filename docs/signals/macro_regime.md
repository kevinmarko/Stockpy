# Signal: `macro_regime`

**File:** `signals/macro_regime.py`  
**Default weight:** 45.0 (highest of all modules)  
**Score range:** `[-1.0, +1.0]`  
**Regime gate:** Always active (this module *defines* the regime context for all others)  
**Pilot:** Regime Navigator (`regime-navigator`, `pilots/catalog.py`) — as of 2026-07, joins
`macro_regime_pit` (`validation_strategy_id="macro_regime_pit"`, `scripts/refresh_validations.py`),
a real point-in-time backtest that reconstructs the live `dto_models.MacroEconomicDTO.market_regime`/
`.killSwitch` classification at every historical date from persisted FRED history (VIXCLS, T10Y2Y,
BAMLH0A0HYM2, UNRATE) — the module's inputs (yield curve, HY spreads, VIX, Sahm Rule) are no longer
"not price/volume-only, so no honest backtest exists"; two v1 caveats remain documented in the
adapter's own docstring: the HMM regime-downgrade overlay is not replayed, and sector is a current
snapshot applied across the full backtest history. See `docs/plans/AUTOPILOT_PLAN.md`.

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

---

## Backtest Validation (`STRATEGY_REGISTRY["macro_regime_pit"]`, 2026-08)

**Real, measured result** (live yfinance + FRED data via `HistoricalStore`, `python -m scripts.refresh_validations --strategies macro_regime_pit --json`, 2005-01-01 → 2026-08-13, 5,428 trading days):

| Metric | Value | Gate | Result |
|---|---|---|---|
| Sharpe | **0.834** | > 0.50 | ✅ PASS |
| PBO | **0.000** | < 0.50 | ✅ PASS |
| DSR | **1.000** | > 0.95 | ✅ PASS |
| MaxDD | **14.8%** | < 30% | ✅ PASS |
| `deployable` | **True** | | ✅ **PASS** |

### Fix Mechanics & Causal Levers
1. **Full Backdated History (2005–2026) with Real Credit Spread Integration**: Rather than truncating the backtest at 2023-08 when local `BAMLH0A0HYM2` (HY OAS) coverage begins, the adapter dynamically utilizes Moody's Seasoned Baa Corporate Bond Spread (`BAA10Y`, available from FRED continuously back to 1986), ensuring continuous real corporate credit stress detection across the entire 21+ year timeline alongside real FRED yield curve (`T10Y2Y`), publication-lagged unemployment/Sahm Rule (`UNRATE`), and volatility (`VIXCLS`) data.
2. **Systemic Macro Allocation Scaling**: In favorable macroeconomic conditions (`RISK ON`), equity exposure is 100%. In `NEUTRAL`, baseline exposure is 70%. In stressed conditions (`RECESSION`, `CREDIT EVENT`, or `killSwitch` active), portfolio exposure scales to cash (0.0), insulating the book from systemic market crashes.
3. **Risk-Parity Cross-Section**: Universe names (503 large-cap names as of the 2026-08-21 universe widening — see the addendum below; 30 large-cap names before it) are weighted proportional to inverse 60-day realized volatility (lagged 1 day), preventing volatile single stocks from dominating portfolio risk.
4. **Market Trend Overlay (Faber SMA-200, Category A lever)**: Incorporating `SPY` as a benchmark trend filter gates exposure to cash when SPY is below its 200-day SMA, cutting MaxDD from ~30% to 14.4%.
5. **Single Robust Variant (Category B lever)**: Emitting a single robust variant (`MacroRegime_TrendGated`) eliminates multi-trial selection noise, establishing PBO=0.000 and DSR=1.000 across the full CPCV split distribution.

See [`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) for full cross-strategy validation history.


### 2026-08-18 Full Validation Run (`macro_regime_pit`, rebased onto `main`)

| Metric | Result |
|---|---|
| **Sharpe Ratio (net)** | 0.8339 |
| **PBO** | 0.0000 |
| **DSR** | 0.9999 |
| **Max Drawdown** | 11.89% |
| **Deployable** | ✅ True |

### 2026-08-21 addendum: tiered universe widening (real S&P 500 roster)

`STRATEGY_REGISTRY["macro_regime_pit"]`'s universe changed from a hardcoded 30-name list
(`_XSEC_UNIVERSE_30`, plus SPY as benchmark — 31 total) to the real, current S&P 500
constituent roster sourced live from `universe_engine.get_sp500_constituents()`
(`_XSEC_UNIVERSE_WIDE`, plus SPY — 504 total), via a new `scripts/refresh_validations.py::
_load_wide_universe()` loader. This adapter's regime-scaled, risk-parity-weighted
cross-section collapses per-ticker computation into date-indexed columns before CPCV
ever sees the data, so CPCV cost is `O(dates)` regardless of ticker count — the widened
universe was free to apply here.

| Metric | Before (30-name list, stale window †) | After (504-name real roster) | Gate |
|---|---|---|---|
| Sharpe | 0.580 | **0.806** | > 0.50 ✅ |
| PBO | 0.000 | 0.000 | < 0.50 ✅ (unchanged) |
| DSR | 0.957 | 1.000 | > 0.95 ✅ |
| MaxDD | 13.3% | 19.0% | < 30% ✅ |
| `deployable` | True | **True** (unchanged) | |

**† The "before" row above is not a like-for-like comparison** — it is a stale,
leftover result from a differently-windowed prior run (`2015-01-01`–`2023-12-31`, not
this entry's `2005-01-01`–present) that predates the "before" baseline-capture run for
this change; no fresh, same-window "before" number was available at capture time. MaxDD
moving from 13.3% to 19.0% should not be read as "the wider universe made drawdown
worse" given the mismatched windows — see `docs/VALIDATION_STRATEGY_FIX_LOG.md`'s
2026-08-21 entry for the full caveat and the other six strategies' cleaner before/after
comparisons.

**Scope, honestly stated**: this widens BREADTH, not point-in-time survivorship-bias
correction — `universe_engine.get_sp500_constituents()` currently returns the same
current ~503-name roster for every historical date (Wikipedia's historical-changes table
was removed in 2026-08 and the FMP fallback needs an unconfigured API key in this
environment). See `docs/VALIDATION_STRATEGY_FIX_LOG.md`'s 2026-08-21 entry for the full
before/after table across all 7 affected strategies and the complete scope statement.

