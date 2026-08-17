# Signal: `edge_garch`

**File:** `signals/edge_garch.py`  
**Default weight:** 35.0  
**Score range:** `[-1.0, +1.0]`  
**Regime gate:** Always active  
**Pilot:** Edge & Volatility (`edge-garch`, `pilots/catalog.py`) — backed by a real,
PBO/DSR-gated backtest (`garch_vol_target` in `scripts/refresh_validations.py`): a
RiskMetrics EWMA vol-timing proxy on SPY (a full per-day GJR-GARCH MLE refit across 20
years of history is prohibitively slow and lookahead-risky — see the adapter's docstring).
This backtest covers only the GARCH tail-risk-veto half of the live signal; the
`edge_ratio` half (depends on real closed-trade history) still isn't backtested
standalone — same narrower-proxy precedent as the `multifactor` Pilot's own backtest.

---

## Rationale

This module combines two related ideas:

1. **Mathematical edge** — is the strategy's historical payoff ratio above or below
   break-even? A Kelly bet requires `p × b > (1 − p)`; when the edge ratio (avg win /
   avg loss) is below 0.8, a bet will have negative expected value regardless of win rate.

2. **GARCH tail risk** — GJR-GARCH volatility captures asymmetric volatility clustering
   (leverage effect): downward price moves amplify subsequent volatility more than upward
   moves of the same magnitude (Glosten, Jagannathan & Runkle, 1993).

The weight of 35.0 reflects that these two factors together act as a per-symbol
risk-gate: a stock with a strong macro tailwind (macro_regime) but extreme GARCH vol or
negative edge should still be sized conservatively.

---

## Signal Logic

| Condition | Points |
|-----------|--------|
| `edge_ratio >= 1.2` | +15 pts — strong mathematical edge |
| `0.8 <= edge_ratio < 1.2` | 0 pts — edge within noise band |
| `edge_ratio < 0.8` | −15 pts — negative mathematical edge |
| `garch_vol > 0.40` (40% annualised) | −20 pts — extreme tail risk |

**Normalization:** raw points / 35.0.

`garch_vol` is the GJR-GARCH(1,1,1) annualised volatility from
`technical_options_engine.estimate_gjr_garch_volatility()`, which falls back to the
20-day historical standard deviation when the `arch` library fails to converge.

---

## GJR-GARCH Background

Standard GARCH(p,q) models symmetric volatility responses. GJR-GARCH adds an
asymmetric term `γ * I(εₜ₋₁ < 0) * εₜ₋₁²` to capture the leverage effect.
The `arch ≥ 8.0` library call is `model.fit(update_freq=0, disp='off')` with **no**
`method=` kwarg — earlier versions used `method='slsqp'` explicitly but that argument
was removed in arch 8.0. If you see `"got an unexpected keyword argument 'method'"`, a
dependency upgrade re-introduced the old call signature.

---

## Failure Modes

| Failure | Behaviour |
|---------|-----------|
| `arch` library API break (method= kwarg) | Falls back to 20-day historical std. Warning logged. Edge signal still computed from `edge_ratio`. |
| `edge_ratio` not available | 0 pts from edge component — module still computes GARCH penalty. |
| Insufficient price history (< 20 bars) | GARCH estimator returns NaN → no GARCH penalty applied. Module does not fabricate a score. |
| `garch_vol` between 0.25 and 0.40 | No penalty applied — the 0.40 threshold is intentionally conservative to avoid false positives on legitimately volatile growth stocks. |

**Verify the GARCH test passes:**
```bash
.venv/bin/python3 -m pytest tests/test_quantitative_models.py -k garch -v
```

---

## Empirical Notes

- The 0.40 annualised vol threshold corresponds roughly to a stock with daily moves of
  ~2.5%; at that level GJR-GARCH estimates frequently carry a fat-tail multiplier that
  makes standard option pricing dangerous without IV adjustment.
- The `edge_ratio` threshold of 1.2 (20% above break-even) corresponds to a payoff
  ratio that, at a 50% win rate, produces a Kelly fraction of about 0.10 — meaningful
  but not dominant.

---

## Options Premium Context

For options-selling strategies the GJR-GARCH vol is also the denominator of the
Volatility Risk Premium (VRP = realized_vol / implied_vol). The `edge_garch` signal
firing negatively (extreme GARCH vol) should correlate with a high IVR reading that
redirects the options engine toward debit strategies (hedged plays) rather than
premium-selling (naked short vol). See `docs/signals/news_catalyst.md` for the IVR gate.

---

## Backtest Validation (`garch_vol_target`, 2026-07 / 2026-08)

The `STRATEGY_REGISTRY` proxy for this module — `garch_vol_target`, covering the
GARCH-veto half only — originally failed the MaxDD gate at 34.3% (>30%). Fixed 2026-07
by layering a de-risking trigger on top of the existing vol-target sizing: pure
vol-targeting alone still eats the front of a calm-but-declining move before the EWMA
vol forecast catches up.

| Metric | Before (2026-07) | After (2026-07) | Gate |
|---|---|---|---|
| Sharpe | 0.776 | 0.767 | > 0.50 ✅ |
| PBO | 0.444 | 0.422 | < 0.50 ✅ |
| DSR | 1.000 (shortcut) | 1.000 (shortcut) | > 0.95 ✅ |
| MaxDD | 34.3% | **18.8%** | < 30% ✅ (was FAIL) |
| `deployable` | False | **True** | |

See [PR #310](https://github.com/kevinmarko/Stockpy/pull/310) and
[`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) for the
full 12-strategy series this fix was part of.

### 2026-08 addendum: `VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED` re-validation

`garch_vol_target` is a single-variant adapter (`n_trials=1`), so the `DSR = 1.000`
above is the legacy shortcut value (`deflated_sharpe_ratio` never ran its real
computation for `n_trials<=1`), not a genuinely computed result. Re-run under
`settings.VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED=True` (same
2005-01-01–2024-12-31 window, real network-backed data) to close the gap left when
that flag was enabled operator-side on 2026-08-14 with no follow-up validation:

| Metric | Before (flag off) | After (flag on) | Gate |
|---|---|---|---|
| Sharpe | 0.767 | 0.767 | > 0.50 ✅ (unchanged) |
| PBO | 0.422 | 0.422 | < 0.50 ✅ (unchanged) |
| DSR | 1.000 (shortcut) | **0.999656** (real) | > 0.95 ✅ |
| MaxDD | 18.8% | 18.8% | < 30% ✅ (unchanged) |
| `deployable` | True | **True** (unchanged) | |

A forward check through 2026-08-01 (~19 months more live data) gives DSR=0.9998509,
the same conclusion. See
[`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md)'s
2026-08-17 entry for the full 5-strategy re-validation and methodology.
