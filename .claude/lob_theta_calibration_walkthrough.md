# Walkthrough: LOB simulator — real theta_market calibration

Branch: `fully-fix-lob-theta-calibration`

## Summary

Follow-up to `docs/known_issues/lob_simulator_uncalibrated_live_arrival_rates.md`
(previously "disclosed, not fixed"). Discovered `alpaca-py`'s `Bar` model
carries a real, exchange-reported `trade_count` field that
`AlpacaProvider.get_intraday_bars()` discards — a genuine, non-fabricated
lever for calibrating `theta_market` (market-order Poisson arrival rate).
Neither FMP nor yfinance expose an equivalent field (checked both), so this
is Alpaca-only. `lambda_limit`/`mu_cancel` remain structurally
uncalibratable with any data source in this codebase, Alpaca included — no
data source anywhere measures new-limit-order or cancellation events, only
executed trades. This is a real, disclosed ceiling, not a scope choice.

Executed as a 6-stage sequential `Workflow` (background subagent pipeline)
per the user's explicit request, then independently re-verified.

## What changed

**Data layer** (`data/market_data.py`): new `AlpacaProvider.get_intraday_trade_counts(symbol, lookback_hours=24)`
(preserves `trade_count`, renamed to `TradeCount`; raises `MarketDataError`
on failure, matching `get_intraday_bars`'s convention) and new
`CompositeProvider.get_intraday_trade_counts(symbol, lookback_hours=24) ->
Tuple[Optional[pd.DataFrame], Optional[str]]` (never raises; `(None,
reason)` when Alpaca isn't configured/reachable, `(df, None)` on success;
dedicated TTL cache, separate instance from the OHLCV bars cache so the two
can never collide). Three new settings:
`OPTIONS_LOB_TRADE_COUNT_LOOKBACK_HOURS` (24),
`OPTIONS_LOB_TRADE_COUNT_MIN_BARS` (3),
`OPTIONS_LOB_TRADE_COUNT_CACHE_TTL_SECONDS` (300).

**Calibration + live wiring** (`pilots/lob_simulator.py`,
`api/pilots_api.py`): new `estimate_calibrated_theta_market(symbol)` —
lazy-imports `data.market_data.get_provider`, degrades honestly
(`calibrated: False, theta_market: None, reason: ...`) on any failure or
insufficient bars, never fabricates. `LobSimulateQueueRequest.theta_market`'s
Pydantic default changed `5.0 → None` — this is what actually lets the live
endpoint distinguish "caller omitted it" from "caller wants 5.0" (the
webapp never sent this field, so it was always silently masked to `5.0`
before). `simulate_queue_fill()`: explicit caller value → used as-is,
calibration skipped entirely; omitted → real calibration attempted first,
fixed default only as fallback. Response gains
`theta_market_is_calibrated`/`theta_market_data_source`
(`"alpaca_real_trade_count"` | `"caller_supplied"` | `"fixed_default"`)/
`theta_market_bars_used`.

**Webapp** (`LobDepthView.tsx`, `types.ts`, `mock.ts`): honest disclosure
line mirroring `VpinGauge.tsx`'s pattern — "θ calibrated from N real Alpaca
trade-count bars" vs. "θ = fixed default (no real trade-count data
available)". A missing/`undefined` field (older cached response, mock
fixture) is treated as uncalibrated, never defaulted to calibrated.

**Docs**: module docstring, `docs/architecture/execution.md`, and
`docs/known_issues/lob_simulator_uncalibrated_live_arrival_rates.md` (new
"Update" section, original content preserved as historically accurate for
`lambda_limit`/`mu_cancel`) all updated to describe the real, current
behavior.

## Adversarial audit findings (Stage 6)

Independently re-reviewed the full diff against 7 specific CONSTRAINT #4/#6
risks (stale-cache-without-disclosure, fabricated-value-on-failure,
calibrated-flag/value divergence, zero-trade-count handling, extended-hours
bias, webapp missing-field handling, `lambda_limit`/`mu_cancel` scope
boundary). 6 of 7 were already correct; 1 real gap was found and fixed:

- **Extended-hours bar mixing** — `get_intraday_trade_counts`'s
  `StockBarsRequest` carries no regular-trading-hours filter, so a 24h
  lookback can mix low-volume pre/post-market bars into the mean,
  systematically biasing `theta_market` downward. Not fixed in code (no
  verified Alpaca session-filtering convention to reach for without
  guessing) — disclosed instead, via a docstring paragraph on
  `AlpacaProvider.get_intraday_trade_counts` and a new section in the
  known-issues doc.

A second, unprompted finding: the known-issues doc's own draft claimed the
webapp UI disclosure was "not attempted" as a follow-up, when the same diff
already shipped it with a passing test — a stale, self-contradictory claim,
fixed.

## Verification

Ran by the workflow's Stage 5, then **independently re-run by me** (not
taken on trust):

```
python3 -m pytest tests/test_market_data.py tests/test_lob_simulator.py tests/test_options_sor.py tests/test_pilots_paper_broker.py -q
379 passed, 2 warnings in 26.22s
```

`npm run --prefix webapp typecheck` — clean. Also independently read
`simulate_queue_fill()` and `estimate_calibrated_theta_market()`'s actual
source (not just the subagents' self-reports) to confirm: explicit
caller-supplied `theta_market` is never overridden; the calibrated flag and
the value actually used can never diverge (same branch sets both); every
failure path returns `None`/an explicit reason, never a fabricated number.

## Note: items #5/#6 from the original audit

While this workflow ran, a separately-spawned follow-up task
(`task_84ccc43b`) independently fixed and merged items #5 (the divergent
CST fill-probability formulas — `evaluate_optimal_queue_level` now calls
the rigorous `compute_cst_fill_probability` instead of the heuristic) and
#6 (added a real commission-cost differential to `options_sor.py`'s
`policies_comparison`, reusing `execution/cost_model.py::TieredCostModel`)
as PR from branch `fix-sor-lob-fillprob-and-commission-model` — see
`.claude/sor_lob_audit_fixes_5_6_walkthrough.md` on `main`. All six
original audit findings are now addressed.
