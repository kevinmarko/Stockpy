# Dispersion & Implied Correlation Arbitrage (`pilots/dispersion_trading.py`)

## Rationale

Decomposes index variance versus weighted constituent variances via the Driessen, Maenhout,
Vilkov (2009) implied-correlation model, comparing implied correlation against historical
realized correlation to identify Long Dispersion (short index vol / long constituent vol) or
Short Dispersion (the reverse) opportunities.

## Backtest Validation — NOT GATEABLE (measured reason)

**Not registered in `STRATEGY_REGISTRY`.** Same precedent as `earnings_crush.md` and
`pilots/catalog.py`'s `validation_strategy_id=None` convention — the decline is evidence-backed,
not asserted.

**The blocking gap**: the DMV implied-correlation formula needs BOTH the index's own IV and each
constituent's own ATM IV. The index leg is real (VIX). The 8 constituent ATM IVs have **no
historical source** in this codebase (same gap documented in `earnings_crush.md`).

**Measured bias, not just "no data"**: substituting realized volatility for the missing
constituent implied volatilities is not a neutral simplification. Over the same 2005–2026 real
VIX-vs-HAR-RV comparison run for `vol_mispricing` (see that doc), real index IV exceeds the
realized-vol forecast by **+1.18 volatility points on average (sd 6.2)**. Understating the
constituent σᵢ terms in the DMV denominator systematically **inflates** the implied-correlation
estimate — biasing the signal toward "Long Dispersion" regardless of the real market's actual
correlation structure, and driving the pilot's own ±0.15 threshold with the substitution
artifact rather than with market information. Registering this pilot with a realized-vol
substitute would therefore not just be a narrower measurement — it would be a measurement of the
substitution's own systematic bias.

## Defects found while analysing this pilot (out of scope here — not fixed)

Recorded so they are not lost; `pilots/*.py` is out of this task's ownership:

1. `get_dispersion_opportunities` applies the **same 8 mega-cap constituent basket to both QQQ
   and SPY** — the SPY dispersion reading is computed against a Nasdaq-shaped basket, not an
   S&P-representative one.
2. `execute_dispersion_trade(basket=None)` always constructs a **Long Dispersion** basket
   regardless of the measured correlation spread's sign — the execution path ignores its own
   signal direction.

See [`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) and
`.claude/giant_master_plan_audit.md`'s finding F4.
