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

1. **HALF FIXED (2026-08-18) — the identical-8-stock-basket defect.** `get_dispersion_opportunities`
   no longer reads a single shared basket constant for every index — `pilots/dispersion_trading.py`
   now has a per-index `INDEX_CONSTITUENTS_MAP`/`INDEX_WEIGHTS_MAP`. But verified directly against
   the current source: `INDEX_CONSTITUENTS_MAP["SPY"]` is
   `["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO"]` and
   `INDEX_CONSTITUENTS_MAP["QQQ"]` is `["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA"]`
   — the same 8 tickers as a set, only `TSLA`/`AVGO`'s list position swapped. Only
   `INDEX_WEIGHTS_MAP`'s per-symbol weight allocations genuinely differ between the two indices
   (e.g. `AAPL` is 0.18 under `SPY` vs 0.20 under `QQQ`). So the SPY dispersion reading is still
   computed against a basket that is set-identical to QQQ's — a real S&P 500 dispersion trade and
   a real Nasdaq-100 dispersion trade would draw on different, only partially overlapping,
   mega-cap constituent sets, not the same 8 names reordered. Covered (as a "distinct, not
   copy-pasted" check on the *weights*, not the constituent sets) by
   `tests/test_options_desk_deployability_runtime_gap.py::test_dispersion_trading_baskets_distinct_for_spy_and_qqq`
   — that test passes today and is correct on what it checks, but does not by itself establish
   that the constituent baskets themselves differ.
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
