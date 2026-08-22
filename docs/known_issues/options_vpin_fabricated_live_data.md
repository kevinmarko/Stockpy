# Known issue (2026-08-22): live VPIN endpoint always returned a fabricated toxicity reading, indistinguishable from a real measurement

**Status: fixed.** Branch `fix-options-vpin-fabricated-live-data`.

## What happened

`pilots/options_vpin.py::get_options_vpin_metrics()` — the function backing the live
`GET /pilots/options/vpin/metrics` endpoint (`api/pilots_api.py`, via
`get_options_vpin_metrics_for_frontend`), which drives the Pilots PWA's `VpinGauge.tsx` screen —
unconditionally called:

```python
trades_df = generate_synthetic_option_trades(
    num_trades=1000,
    initial_price=5.0,
    volatility=0.02,
    seed=hash(clean_sym) % (2**31 - 1),
)
```

`generate_synthetic_option_trades()`'s own docstring says "Generates synthetic option trade
stream for testing and simulation" — it is a random-walk generator with `informed_fraction`
defaulting to `0.0` (pure noise, never real toxic flow). This meant the live, non-mock VPIN
endpoint always returned a number computed from fabricated data, deterministic per-symbol
(seeded by `hash(symbol)`), with **zero indication anywhere** — not in the response, not in the
endpoint docstring, not in `docs/architecture/execution.md`'s prior VPIN entry, not in
`VpinGauge.tsx` — that this wasn't real. A user viewing the live backend saw a VPIN toxicity
reading indistinguishable from a genuine measurement, in a screen whose entire purpose is to warn
about adverse-selection risk and recommend defensive spread widening.

This is distinct from (and was not caught by) the previously-fixed field-mismatch bug on this
same endpoint documented in
[`options_desk_mock_live_parity_sweep_2026_08_19.md`](options_desk_mock_live_parity_sweep_2026_08_19.md)
item #12 — that fix addressed response *shape* (missing `price_start`/`price_end` fields, a
fabricated `?? 50` percentile fallback), not the fact that the underlying trade data feeding the
whole computation was synthetic.

## Why a real per-trade fix wasn't possible

There is no real per-trade options tick stream anywhere in this codebase today —
`pilots/unusual_options_flow.py` only has point-in-time chain snapshots, and a direct check of
`docs/FMP_INTEGRATION.md` and `data/fmp_client.py` (grepped for any options-trade or tick-level
feed) turned up nothing. FMP's documented endpoints cover EOD/intraday OHLCV bars, quotes,
fundamentals, news, and analyst/insider data — no options trade tape. So a genuinely
tick-resolution VPIN is not achievable short-term.

## The fix

`desktop/daemon_runtime.py::OrchestratorDaemon.maybe_update_circuit_breaker` already had a real,
honestly-documented precedent for exactly this situation: it computes a "coarse, bar-level Bulk
Volume Classification approximation" for its own VPIN sub-check, using real hourly Close/Volume
bars from the configured market-data provider, explicitly disclosed in its own docstring as
non-tick-resolution but genuinely non-fabricated.

`pilots/options_vpin.py` now follows the same pattern:

- **New `fetch_real_underlying_bar_trades(symbol, lookback_days=10)`** calls
  `data.market_data.get_provider().get_intraday_bars(symbol, lookback_days=10, interval="1h")`,
  reshapes the result into a `['price', 'volume', 'time']` trade-stream proxy, and returns
  `(df, None)` on success or `(None, reason)` on any failure (provider error, empty/malformed
  frame, missing `Close`/`Volume`, or fewer than 2 usable rows). Never raises (CONSTRAINT #6).
- **`get_options_vpin_metrics()`** calls this first. On success it computes VPIN from the real
  bars (bucket count sized down to fit the actual row count, mirroring
  `maybe_update_circuit_breaker`'s own `max(2, min(N, len(bars) // 2))` precedent) and tags the
  response `data_available: True`, `data_source: "bar_level_bvc_approximation"`. On failure it
  returns an explicit `data_available: False`, `vpin: None`, `toxicity_regime: None`,
  `reason: "<why>"` response — CONSTRAINT #4's "missing data returns `None`, never a fabricated
  plausible number" rule, applied to the one spot in this module that wasn't already following it.
- **`get_options_vpin_metrics_for_frontend()`** threads `data_available`/`data_source`/`reason`
  through to the API response and rewrites `warning_message` to state the measurement is
  unavailable (rather than describing a toxicity regime that was never computed) when
  `data_available` is `False`.
- **`VpinGauge.tsx`** now checks `data_available`/`vpin != null` before rendering the gauge. When
  unavailable it shows an explicit "UNAVAILABLE" regime badge, an em-dash in place of every
  numeric readout (VPIN %, toxicity percentile, defensive spread concession), and a plain-text
  explanation — instead of its previous `data?.vpin ?? 0.25` fallback, which would have silently
  rendered a fake 25%/MODERATE reading for a `null` vpin (`??` treats `null` the same as
  `undefined`). When data IS available, a small italic label now states plainly that the number
  is a "bar-level BVC approximation from real hourly bars -- not tick-level options order flow."
- `generate_synthetic_option_trades()` itself is untouched and remains legitimately used by
  `tests/test_options_vpin.py`'s pure-math unit tests of the BVC/bucketing logic — the fix is
  narrowly that the LIVE endpoint no longer reaches it.

## Verification

Tested against real live data (not mocked) using the operator's own `FMP_API_KEY`
(`MARKET_DATA_PROVIDER=fmp`) for a small sample of tickers:

```
SPY: data_available=True, data_source=bar_level_bvc_approximation, vpin=0.1801, regime=LOW
QQQ: data_available=True, data_source=bar_level_bvc_approximation, vpin=0.1463, regime=LOW
NOTATICKERXYZ: data_available=False, vpin=None, reason="market data fetch failed ... all
    providers in the fallback chain failed"
```

Real hourly bars for SPY/QQQ (154 rows over the ~30-day FMP intraday window) produced genuine,
non-fabricated VPIN readings; an unresolvable symbol degraded honestly to the unavailable
response instead of silently falling back to synthetic data.

Regression tests added:
- `tests/test_options_vpin.py::TestFetchRealUnderlyingBarTrades` — the new fetch helper's
  success/failure paths (provider exception, empty frame, missing columns, insufficient rows),
  fully mocked (no live network in the offline suite).
- `tests/test_options_vpin.py::TestGetOptionsVpinMetricsHonesty` — asserts
  `generate_synthetic_option_trades` is never called by the live path, and that both the raw and
  frontend-adapted responses degrade honestly (`data_available: False`, every numeric field
  `None`, non-empty `reason`) when real bars are unavailable.
- `tests/test_pilots_paper_broker.py::TestOptionsVpinEndpoint` — extended with a mocked
  `data.market_data.get_provider` for the existing success/no-token tests (previously these would
  have made real network calls the moment the synthetic fallback was removed) plus a new
  `test_get_vpin_metrics_honestly_unavailable_when_no_real_data` covering the full HTTP round
  trip.
- `webapp/src/components/options/VpinGauge.test.tsx` — new test asserting the unavailable state
  renders honestly (no fabricated percentage/regime, an explicit "UNAVAILABLE" badge and
  explanatory text).

Full suite: `pytest tests/test_options_vpin.py tests/test_pilots_paper_broker.py
tests/test_daemon_runtime.py tests/test_market_data.py -q -m "not network"` — 376 passed.
`npx vitest run src/components/options/VpinGauge.test.tsx` — 7 passed.
`npm run --prefix webapp typecheck` — clean.

## What this does not fix

The response is still a **bar-level, not tick-level**, approximation — genuinely coarser than the
literature's tick-resolution VPIN formulation, exactly like `maybe_update_circuit_breaker`'s own
disclosed scope. If a real options-trade tick feed becomes available from a future data-provider
tier, `fetch_real_underlying_bar_trades()` is the one place to swap in a tick-level trade stream;
`get_options_vpin_metrics()`'s own bucketing/BVC math is unaffected either way.
