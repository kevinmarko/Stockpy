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

**Real, measured result** (live yfinance + FRED data via `HistoricalStore`, `python -m
scripts.refresh_validations --strategies macro_regime_pit --start 2023-08-08 --end 2026-08-06
--json`, run 2026-08-10):

| Metric | Value | Gate | Result |
|---|---|---|---|
| Sharpe | **1.556** | > 0.50 | ✅ |
| PBO | 0.000 | < 0.50 | ✅ |
| DSR | **0.000** | > 0.95 | ❌ FAIL |
| MaxDD | 15.4% | < 30% | ✅ |
| `deployable` | **False** | | |

Actual window used: **2023-08-08 → 2026-08-05** (`n_trials=2`, the two variants below). This is
*not* a self-imposed bound like `forecast_direction_arima_hw`'s — it's forced by real data
availability: `BAMLH0A0HYM2` (HY OAS, the CREDIT EVENT input) only has FRED history starting
**2023-08-08** in this platform's `HistoricalStore` (`VIXCLS`/`T10Y2Y`/`UNRATE` all go back
decades further). `_reconstruct_macro_regime_series` correctly degrades any date missing an
input series to `market_regime=None` (never a fabricated classification — CONSTRAINT #4), so
requesting an earlier `--start` would only prepend years of NaN-scored rows, not add real
signal — `--start 2023-08-08` was chosen deliberately to match the real constraint rather than
pad the window with uninformative dates.

**Honest read**: PBO and MaxDD both pass comfortably, and the raw Sharpe (1.556) looks strong —
but DSR fails hard (0.000, far below the 0.95 gate). This is not a bug or a data-wiring problem;
it is DSR doing exactly what it's designed to do. Bailey & López de Prado's Deflated Sharpe
Ratio penalizes an observed Sharpe for (a) the number of trials tested (`n_trials=2` here — the
rank-based `MacroRegime_TopHalf` book and the explicit `MacroRegime_SectorRotation` book), (b)
non-normal return skew/kurtosis, and — the dominant factor at this sample size — **(c) the
standard error of the Sharpe estimate itself, which shrinks only as ~1/√N**. With real HY-OAS
coverage starting 2023-08-08, this backtest has roughly 2.5 years (~650 trading days) of usable
history — genuinely too short a track record for DSR to statistically distinguish a Sharpe of
1.556 from one that arose by chance, no matter how good it looks in-sample. A separate
family-wide multiple-testing correction (`family_multiple_testing.family_dsr`, Benjamini-Hochberg
across signal modules) reports a softer **0.849** for the same single-strategy observation using
a different, simpler formula — still short of 0.95, consistent with the same short-sample
conclusion, and *not* the number the deployability gate actually reads (`ValidationReport.dsr`,
sourced from the CPCV path-distribution DSR at `n_trials=2`, is the gating value — 0.000, per
`validation/harness.py`).

**What would change this**: the two real levers are (1) time — as `BAMLH0A0HYM2` accumulates
more FRED history against the live pipeline's already-decades-deep `VIXCLS`/`T10Y2Y`/`UNRATE`
coverage, the usable window lengthens and DSR's sample-size penalty eases on its own, with no
code change; or (2) a v2 macro-regime backtest that doesn't gate on HY OAS at all (e.g. a
yield-curve/VIX/Sahm-only variant, dropping the CREDIT EVENT branch) — not attempted here, since
that would validate a *different*, narrower regime rule than what `signals/macro_regime.py`
actually runs live. No threshold was loosened and no date range was cherry-picked to avoid this
result — `--start` was set to the earliest date the real inputs support, and the honest FAIL is
recorded as-is. The two documented v1 caveats from earlier in this file (HMM downgrade not
replayed; sector is a current snapshot applied across history) remain unaddressed and are
orthogonal to the DSR failure above — neither would move the DSR result materially, since both
affect signal *magnitude*, not sample length. See `docs/VALIDATION_STRATEGY_FIX_LOG.md`'s 2026-08
entry for the full writeup.
