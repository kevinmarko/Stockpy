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

## Defects found while analysing this pilot

Recorded so they are not lost; `pilots/*.py` is out of this task's ownership.

1. **FIXED (2026-08-19) — the identical-8-stock-basket defect.** `INDEX_CONSTITUENTS_MAP["SPY"]`
   and `INDEX_CONSTITUENTS_MAP["QQQ"]` are no longer set-identical. Both baskets keep the real
   mega-cap tech overlap that genuinely exists between the two indices in real life (AAPL, NVDA,
   MSFT, AMZN, GOOGL, META are legitimately top holdings of both) — that overlap is a market
   fact, not a bug. What now genuinely differs reflects a real structural distinction between the
   indices: SPY (S&P 500) carries real non-tech sector weight (JPM financials, UNH healthcare)
   that QQQ (Nasdaq-100, which structurally excludes financials by index rule) does not; QQQ
   keeps its real growth/semiconductor tilt (AVGO, TSLA) in their place. These are static,
   reasoned approximations of real index composition, not a live-fetched snapshot, and will drift
   from the real, current index weights over time — see `pilots/dispersion_trading.py`'s own
   comment above the two maps. Covered by
   `tests/test_options_desk_deployability_runtime_gap.py::test_dispersion_trading_baskets_distinct_for_spy_and_qqq`,
   strengthened to assert on the constituent SETS differing (not just weights on an identical
   set), and by `docs/VALIDATION_STRATEGY_FIX_LOG.md`'s 2026-08-19 entry.
2. **FIXED (2026-08-18) — the hardcoded-Long defect.** `execute_dispersion_trade(basket=None)`
   no longer always constructs a Long Dispersion basket. Verified directly against the current
   source: the `basket is None` branch now calls `evaluate_dispersion_opportunity(...)` first and
   derives `is_long = opp.get("direction") != "short_dispersion"` from the actual measured
   correlation spread's sign before calling `build_dispersion_basket(..., is_long_dispersion=is_long, ...)`
   — the execution path now follows its own signal direction rather than ignoring it. Regression
   coverage: `tests/test_dispersion_trading.py::test_execute_dispersion_trade_none_basket_derives_short_direction_from_real_data`
   (spread strongly negative → short basket: index leg `buy`, constituent legs `sell`) and
   `::test_execute_dispersion_trade_none_basket_derives_long_direction_from_real_data` (spread
   strongly positive → long basket: index leg `sell`, constituent legs `buy`).

See [`docs/VALIDATION_STRATEGY_FIX_LOG.md`](../VALIDATION_STRATEGY_FIX_LOG.md) and
`.claude/giant_master_plan_audit.md`'s finding F4.

`POST /pilots/options/dispersion/execute`'s response body now includes a `gate_status` field
(sourced from `OPTIONS_DESK_DEPLOYABILITY_GATES["dispersion_trading"]` in `api/pilots_api.py`) —
`"UNGATEABLE_DATA_GAP"` — echoing this doc's `deployable=False` verdict inline on every execution
attempt, so an operator hitting the live endpoint sees the same honest gate status documented
here without cross-referencing this file.
