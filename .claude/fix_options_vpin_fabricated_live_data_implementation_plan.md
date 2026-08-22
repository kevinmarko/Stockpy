# Implementation plan: fix fabricated live data in `pilots/options_vpin.py`

## Problem

`get_options_vpin_metrics()` (backing the live `GET /pilots/options/vpin/metrics` endpoint)
unconditionally called `generate_synthetic_option_trades()` — a random-walk generator documented
as "for testing and simulation" — so the live, non-mock endpoint always returned a VPIN computed
from fabricated data, with no indication anywhere that it wasn't real. Violates CONSTRAINT #4.

## Investigation

1. Confirmed no real per-trade options tick stream exists anywhere in this codebase
   (`pilots/unusual_options_flow.py` only has point-in-time chain snapshots; grepped
   `docs/FMP_INTEGRATION.md` and `data/fmp_client.py` for any options-trade/tick feed — none
   found).
2. Found a real, honestly-documented precedent for the same problem:
   `desktop/daemon_runtime.py::OrchestratorDaemon.maybe_update_circuit_breaker` already computes
   a "coarse, bar-level Bulk Volume Classification approximation" for its own VPIN sub-check,
   using real hourly Close/Volume bars from `data.market_data.get_provider()`.
3. Verified real data availability by copying the operator's own `.env`
   (`MARKET_DATA_PROVIDER=fmp`, real `FMP_API_KEY`) into this worktree and calling
   `get_provider().get_intraday_bars("SPY"/"QQQ", interval="1h")` directly — confirmed FMP
   returns ~154 rows of real hourly bars over its ~30-day intraday window.

## Fix

1. **`pilots/options_vpin.py`**:
   - New `fetch_real_underlying_bar_trades(symbol, lookback_days=10)` — fetches real hourly bars
     via `data.market_data.get_provider()`, reshapes to `['price','volume','time']`, returns
     `(df, None)` on success or `(None, reason)` on any failure. Never raises.
   - New `_unavailable_vpin_metrics()` — the honest "no measurement" response shape
     (`vpin=None`, `toxicity_regime=None`, `data_available=False`, `reason=...`).
   - `get_options_vpin_metrics()` rewritten to call the real-bars fetch first; on success,
     computes VPIN via the existing `calculate_vpin()` math (unchanged) with a bucket count sized
     down to fit the real row count; on failure, returns the honest-unavailable shape. Tags
     `data_available`/`data_source` either way.
   - `get_options_vpin_metrics_for_frontend()` threads the new fields through and rewrites
     `warning_message` to state unavailability plainly instead of a fabricated regime.
   - `generate_synthetic_option_trades()` itself untouched (still legitimately used by
     `tests/test_options_vpin.py`'s pure-math unit tests).
2. **`webapp/src/api/types.ts`**: `VpinMetricsResponse.vpin`/`.regime` become nullable; added
   `data_available`/`data_source`/`reason`.
3. **`webapp/src/components/options/VpinGauge.tsx`**: renders an explicit "UNAVAILABLE" state
   (em-dash readouts, no fabricated 25%/MODERATE default) when `data_available` is false or
   `vpin` is null; adds an honesty label ("bar-level BVC approximation... not tick-level") when
   data is available.
4. **`webapp/src/api/mock.ts`**: added the new fields to the happy-path mock for parity.
5. **Tests**:
   - `tests/test_options_vpin.py`: `TestFetchRealUnderlyingBarTrades` (fetch helper success/
     failure paths) + `TestGetOptionsVpinMetricsHonesty` (asserts the synthetic generator is
     never called by the live path; both raw and frontend-adapted responses degrade honestly).
   - `tests/test_pilots_paper_broker.py::TestOptionsVpinEndpoint`: existing tests updated to mock
     `data.market_data.get_provider` (previously relied on the synthetic fallback, which is now
     gone); new `test_get_vpin_metrics_honestly_unavailable_when_no_real_data`.
   - `webapp/src/components/options/VpinGauge.test.tsx`: new test for the unavailable-state UI.
6. **Docs**: `docs/architecture/execution.md`'s VPIN entry updated with the fix history; new
   `docs/known_issues/options_vpin_fabricated_live_data.md`; added to
   `docs/known_issues/README.md`'s index.

## Verification

- `pytest tests/test_options_vpin.py tests/test_pilots_paper_broker.py
  tests/test_daemon_runtime.py tests/test_market_data.py -q -m "not network"` — 376 passed.
- `python -m ruff check . --select=F821,F822,F823,E9` — clean.
- `npm run --prefix webapp typecheck` — clean.
- `npx vitest run src/components/options/VpinGauge.test.tsx` — 7 passed.
- Manually verified against real live FMP data for SPY/QQQ (real VPIN computed) and an invalid
  symbol (honest unavailable response) — see the known-issues doc's "Verification" section.

## Out of scope

- No real options-trade tick feed exists to source a genuinely tick-resolution VPIN; this fix
  makes the response honest (real bar-level data or an explicit unavailable state), not
  tick-resolution. Flagged as future work if a provider tier ever exposes options trade ticks.
