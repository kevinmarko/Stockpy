# Walkthrough: fix fabricated live data in `pilots/options_vpin.py`

## The bug

`GET /pilots/options/vpin/metrics` (the endpoint behind the Pilots PWA's `VpinGauge.tsx` VPIN
toxicity screen) always computed its answer from `generate_synthetic_option_trades()` — a
random-walk generator whose own docstring says it's "for testing and simulation." There was no
code path where a real trade/quote ever reached the calculation, and nothing in the response,
docs, or UI said so. An operator looking at the live (non-mock) backend saw a VPIN toxicity
number that looked exactly like a real measurement.

## Why this matters

VPIN feeds `apply_defensive_spread_concession()`, which is meant to widen spreads/limit prices
when real order flow looks toxic. A fabricated VPIN can never actually detect toxic flow — it's
pure noise dressed up as a signal — so any downstream decision leaning on it was leaning on
nothing.

## What's real vs. what isn't, before and after

**Before:** every call to the live endpoint → `generate_synthetic_option_trades()` (fabricated,
deterministic-per-symbol via `hash(symbol)`) → `calculate_vpin()` on fake data → a plausible
0.0–1.0 number with a LOW/MODERATE/HIGH_TOXICITY label, presented with no caveat.

**After:** every call to the live endpoint → `fetch_real_underlying_bar_trades()` →
`data.market_data.get_provider().get_intraday_bars(symbol, interval="1h")` (real FMP/Alpaca/
yfinance hourly bars) → reshaped into a trade-stream proxy → `calculate_vpin()` on real data. If
the real fetch fails for any reason, the response is `data_available: False`, `vpin: None`,
`reason: "<what went wrong>"` — never a fallback to the fabricated generator.

This is the same honest tradeoff `desktop/daemon_runtime.py::maybe_update_circuit_breaker`
already made for its own VPIN sub-check: real data at bar resolution, clearly labeled as coarser
than a genuine tick-level VPIN, rather than either (a) fake data pretending to be real, or (b)
blocking the whole feature until a tick-level options-trade feed exists (which doesn't, and may
never, given the provider tiers this platform has access to).

## Proof it works against real data

Copied the operator's own `.env` (which has a real `FMP_API_KEY`, `MARKET_DATA_PROVIDER=fmp`)
into this worktree and ran the fixed code directly against three symbols:

```
SPY:  data_available=True,  vpin=0.1801, regime=LOW      (real 154-row hourly bar series)
QQQ:  data_available=True,  vpin=0.1463, regime=LOW      (real 154-row hourly bar series)
NOTATICKERXYZ: data_available=False, vpin=None, reason="market data fetch failed ..."
```

The first two are genuine measurements off real market data — not a coincidence that they landed
in a plausible LOW-toxicity range, since SPY/QQQ's real hourly order flow during a quiet session
genuinely is low-toxicity. The third proves the failure path doesn't quietly fall back to
fabricating a number for a symbol the provider chain can't resolve.

## Frontend

`VpinGauge.tsx` previously did `const vpinVal = data?.vpin ?? 0.25;` — if the backend ever
returned `vpin: null` (which it does now, honestly, when data is unavailable), `??` treats `null`
the same as `undefined` and would have silently rendered a fake 25%/MODERATE gauge reading. Fixed
to check `data_available`/`vpin != null` explicitly and render an "UNAVAILABLE" badge with
em-dash readouts instead. When data IS available, added a small disclosure label so nobody
mistakes the bar-level approximation for tick-level toxicity.

## Test coverage added

- Fetch-helper unit tests covering every failure mode (provider exception, empty frame, missing
  columns, too few rows) — all mocked, no live network in the offline suite.
- Live-endpoint honesty tests proving `generate_synthetic_option_trades` is never called and that
  both the raw and frontend-shaped responses degrade to an explicit unavailable state.
- Updated the existing FastAPI endpoint tests (which used to implicitly rely on the synthetic
  fallback) to mock the market-data provider instead, plus a new end-to-end HTTP test for the
  unavailable path.
- A new frontend test asserting the honest unavailable UI renders (no fabricated percentage,
  regime, or defensive-gate value).

## What didn't change

- `calculate_vpin()`, `compute_vpin_buckets()`, the BVC math, `evaluate_toxicity_regime()`,
  `apply_defensive_spread_concession()` — all untouched. The bug was entirely in what data fed
  the math, not the math itself.
- `generate_synthetic_option_trades()` is untouched and still legitimately used by
  `tests/test_options_vpin.py`'s pure unit tests of the bucketing/BVC logic.
- `desktop/daemon_runtime.py::maybe_update_circuit_breaker`'s own separate, already-correct VPIN
  call site is untouched — it was never part of the bug.
