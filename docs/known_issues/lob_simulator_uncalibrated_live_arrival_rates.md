# Known issue (2026-08-24): live LOB queue-fill endpoint runs on fixed constants, not the module's own empirical calibration

**Status: `theta_market` is now genuinely live-calibrated (see the "Update" section
below); `lambda_limit`/`mu_cancel` remain structurally uncalibratable given this
codebase's available data sources — not a deferred task, a real data-availability
limit.** Sign/units bugs (`mu_cancel`) fixed and tested. Branch
`fix-sor-lob-simulator-audit-findings` (original fix), continued on
`fully-fix-lob-theta-calibration` (the `theta_market` calibration wiring below).

## What happened

`pilots/lob_simulator.py`'s module docstring (and, less overtly,
`settings.py`'s `OPTIONS_LOB_DEFAULT_MARKET_ORDER_RATE` field description)
described this module's honesty guarantee as "True Poisson arrival rates
derived from input order flow records; zero fabricated fills." That's an
accurate description of `compute_lob_arrival_rates()` *as a function* — it is
correctly implemented, unit-tested, and genuinely computes empirical
$\lambda$/$\mu$/$\theta$ Poisson rates from real order-flow event records
when called.

The problem is what the docstring implied about the *live path*: a repo-wide
grep confirmed `compute_lob_arrival_rates()` has **zero production callers**.
The one live caller reaching the module, `simulate_queue_fill()` (backing
`POST /pilots/options/lob/simulate-queue`), never calls it — it runs entirely
on fixed constants:

```python
sim_res = simulate_queue_position(
    ...
    lambda_limit=float(lambda_limit) if lambda_limit is not None else 4.0,
    mu_cancel=float(mu_cancel) if mu_cancel is not None else 0.05,
    theta_market=float(theta_market) if theta_market is not None else DEFAULT_MARKET_ORDER_RATE,
    ...
)
```

`LobSimulateQueueRequest` (`api/pilots_api.py`) itself defaults
`lambda_limit=4.0`/`mu_cancel=0.05`/`theta_market=5.0`
(`DEFAULT_MARKET_ORDER_RATE` = `settings.OPTIONS_LOB_DEFAULT_MARKET_ORDER_RATE`).
Confirmed by direct read of `webapp/src/components/options/LobDepthView.tsx`
that the frontend never sends override values for any of the three either —
its `handleSimulate()` only ever sends `symbol`/`price_level`/`order_size`/
`depth_ahead`. So every live LOB queue simulation, for every symbol, uses the
identical fixed global rates regardless of the actual symbol's real liquidity
— arbitrary constants dressed up (by the docstring's own framing) as an
empirically calibrated Poisson model.

## Why full live-calibration wiring wasn't attempted this pass

Investigated directly rather than assumed. Wiring `compute_lob_arrival_rates()`
into `simulate_queue_fill()` needs a real per-event LIMIT/CANCEL/MARKET
order-flow stream (or at minimum, real bid/ask depth) for the requested
symbol at request time. Neither exists anywhere in this codebase's data
layer:

- Grepped `data/market_data.py` and `data/fmp_client.py` for any Level-2/
  Level-3 order-book, tick-trade, or bid/ask-*size* field — none found.
- This is a previously-settled, independently-confirmed finding, not a new
  guess: CLAUDE.md's `execution/dynamic_circuit_breaker.py` Phase 32 section
  already documents "no configured market-data provider (Alpaca/FMP/
  yfinance) populates bid/ask size anywhere in this codebase's `Quote`
  type... confirmed by direct research, not an oversight" — the exact same
  gap that blocks a real Order Flow Imbalance signal there.

This is a meaningfully harder gap than the sibling
[`options_vpin_fabricated_live_data.md`](options_vpin_fabricated_live_data.md)
issue. VPIN's fix substituted a coarse, disclosed, bar-level Bulk Volume
Classification approximation — a defensible academic formula (Easley/López de
Prado/O'Hara BVC) computable from real OHLCV bars alone. LOB arrival-rate
calibration has no equivalent: there is no established formula for deriving
per-order LIMIT/CANCEL/MARKET event *rates* from bar-level OHLCV data: an OHLCV
bar simply doesn't carry order-count information. Inventing a proxy formula
here would risk creating a new instance of exactly the fabrication-dressed-as-
calibration bug class CONSTRAINT #4 exists to prevent, rather than fixing the
existing one — so it was deliberately not attempted.

## What was fixed instead

1. **The module docstring's CONSTRAINT #4 bullet was corrected** to describe
   what's actually true: `compute_lob_arrival_rates()` provides real
   calibration when called, but the live endpoint currently runs on fixed
   defaults, and no real order-flow data source exists to calibrate from
   honestly today. `docs/architecture/execution.md`'s `pilots/lob_simulator.py`
   entry was updated the same way, with a pointer to this file.
2. **Two independent, real defects in `compute_lob_arrival_rates()`'s
   `mu_cancel` formula were fixed and regression-tested**, defensively —
   `compute_lob_arrival_rates()` is a public, exported, directly-tested
   function, and any future integration (this gap's eventual real fix, a
   test, or another caller) would silently inherit either bug otherwise:
   - The depth-observed (primary) branch divided by cancel event *count*
     instead of canceled *volume* (`total_sizes["CANCEL"]`), understating/
     overstating $\mu$ whenever average cancel size isn't exactly 1 share.
   - The no-depth-data fallback branch returned a raw events/sec rate where
     every downstream caller (`simulate_queue_position`,
     `compute_cst_fill_probability`) expects units of events/(sec·share) —
     empirically confirmed this flipped `fill_probability` from ~0.03% to
     100% and collapsed expected wait time ~59s → ~12.5s the moment a
     nontrivial `queue_ahead` was supplied. Now normalized by the average
     canceled-order size as a best-effort per-share proxy — still an
     approximation absent real depth data, but no longer orders of magnitude
     too large.

   See `pilots/lob_simulator.py`'s `compute_lob_arrival_rates()` inline
   comments for the full derivation. Regression tests:
   `tests/test_lob_simulator.py::test_compute_lob_arrival_rates_mu_cancel_uses_canceled_shares_not_event_count`
   and
   `::test_compute_lob_arrival_rates_mu_cancel_fallback_is_unit_consistent_with_downstream_use`
   (the latter reproduces this issue's own repro numbers as a regression,
   confirmed to fail against the pre-fix code and pass against the fix).
3. **A separate, live-reachable sign bug in the sibling `pilots/options_sor.py`
   module was fixed in the same PR** (unrelated math, same audit pass) — see
   that module's entry in `docs/architecture/execution.md` for detail.

## What this does not fix

- The live `POST /pilots/options/lob/simulate-queue` endpoint still runs on
  fixed default rates for every symbol. `LobDepthView.tsx`'s displayed fill
  probabilities and wait-time percentiles are not calibrated to the
  requested symbol's actual liquidity.
- `compute_lob_arrival_rates()` still has zero production callers.
- No plan exists yet for a real order-flow/tick data source that would make
  live calibration honest. If one becomes available (a future
  market-data-provider tier, or a genuine L2/L3 feed), `compute_lob_arrival_rates()`
  is already correct and tested and is the right place to wire it in —
  `simulate_queue_fill()`'s call site is the one place that needs to change.

## Update (theta_market now genuinely calibrated) — 2026-08-24, branch `fully-fix-lob-theta-calibration`

Everything above this section describes the state as of the original audit
pass and remains historically accurate for `lambda_limit`/`mu_cancel` and for
what the live endpoint did before this follow-up. This section documents
what changed.

**What was found**: while `data/market_data.py`/`data/fmp_client.py` genuinely
have no L2/L3 order-book, bid/ask-size, or per-order-event data anywhere (the
finding above still holds in full), the `alpaca-py` `Bar` model that
`AlpacaProvider` already wraps for OHLCV bars carries a real
`trade_count` field per bar — the exchange-reported count of executed
trades that occurred within that bar. That is not a limit-order or
cancellation count, but it is a genuine, real, non-fabricated per-symbol
measurement of executed *market* order flow, which is exactly what
`theta_market` (the CST(2010) market-order Poisson arrival rate) is defined
to measure. This closes one of the model's three arrival-rate parameters,
not all three.

**What was wired in**:

- `AlpacaProvider.get_intraday_trade_counts(symbol, lookback_hours)`
  (`data/market_data.py`) fetches real hourly bars from Alpaca and returns a
  `TradeCount`-indexed DataFrame — a thin, honest wrapper around the
  existing Alpaca bars machinery, not a new data source.
- `CompositeProvider.get_intraday_trade_counts(symbol, lookback_hours)`
  (`data/market_data.py`) is the caller-facing entry point: it is
  **Alpaca-only regardless of `settings.MARKET_DATA_PROVIDER`** (it
  constructs a dedicated `AlpacaProvider` directly, since `trade_count` has
  no equivalent on FMP or yfinance — both were checked directly, not
  assumed), returns `(None, reason)` rather than raising on any failure
  (Alpaca not configured, construction failure, live fetch failure), and
  caches results in-process for `settings.OPTIONS_LOB_TRADE_COUNT_CACHE_TTL_SECONDS`
  in a cache instance kept fully separate from the real OHLCV bars cache.
- `pilots/lob_simulator.py::estimate_calibrated_theta_market(symbol)` calls
  the above with `lookback_hours=settings.OPTIONS_LOB_TRADE_COUNT_LOOKBACK_HOURS`,
  requires at least `settings.OPTIONS_LOB_TRADE_COUNT_MIN_BARS` real bars
  before trusting the estimate (guards against single-bar noise on a thin
  window or a thinly-traded symbol), and converts the mean per-bar
  `TradeCount` (over an hourly bar) into a per-second rate. Returns an
  honest `{"calibrated": False, "reason": ...}` sentinel on any degrade
  path — never a fabricated number (CONSTRAINT #4) — and never raises
  (CONSTRAINT #6).
- `pilots/lob_simulator.py::simulate_queue_fill()` (the live resolver behind
  `POST /pilots/options/lob/simulate-queue`) now calls
  `estimate_calibrated_theta_market()` whenever the request omits
  `theta_market`, using the calibrated value when available and falling
  back to the fixed `settings.OPTIONS_LOB_DEFAULT_MARKET_ORDER_RATE`
  constant otherwise. An explicit caller-supplied `theta_market` is always
  honored as-is and is never recalibrated or overridden. Which case applied
  is always reported on the response, never silently:
  `theta_market_is_calibrated` (bool), `theta_market_data_source`
  (`"alpaca_real_trade_count"` | `"fixed_default"` | `"caller_supplied"`),
  and `theta_market_bars_used` (the real bar count behind a calibrated
  estimate, `None` otherwise).

**Real-world reach of this fix**: this codebase's default
`settings.MARKET_DATA_PROVIDER` is `"fmp"`, and Alpaca is an optional,
credential-gated provider (`ALPACA_API_KEY`/`ALPACA_SECRET_KEY`). An
operator running the default configuration without Alpaca credentials set
still gets the exact pre-fix behavior — `theta_market_data_source` reports
`"fixed_default"` and the simulation runs on
`OPTIONS_LOB_DEFAULT_MARKET_ORDER_RATE` — honestly, not silently. This fix
only changes behavior for an operator who has also configured Alpaca.
`LobDepthView.tsx` now surfaces this honestly too: it renders
`theta_market_is_calibrated`/`theta_market_bars_used` as a small disclosure
line under the Queue Dynamics panel ("calibrated from N real Alpaca
trade-count bars" vs. "fixed default (no real trade-count data
available)"), treating a missing `theta_market_is_calibrated` field (an
older cached response, or a mock fixture that predates this field) the same
as "not calibrated" — never defaulted to a calibrated-sounding claim.
Regression-tested in `LobDepthView.test.tsx`.

**Disclosed, not-fixed sub-limitation: extended-hours bar mixing**. Alpaca's
`StockBarsRequest` (used by both `get_intraday_bars` and the new
`get_intraday_trade_counts`) carries no regular-trading-hours filter. A
`OPTIONS_LOB_TRADE_COUNT_LOOKBACK_HOURS`-hour window (default 24) run
outside market hours, or spanning the open/close, can mix genuinely
low-volume pre/post-market bars in with regular-session ones; since
`estimate_calibrated_theta_market()`'s `mean()` weights every bar equally,
this can pull the calibrated rate below what the regular session alone
would show. This is real and plausible, not fixed in this pass — a correct
fix needs a verified (not guessed) Alpaca regular-session filtering
convention, which neither this method nor the pre-existing
`get_intraday_bars` implements today. Disclosed here and in
`AlpacaProvider.get_intraday_trade_counts`'s own docstring rather than
silently left unmentioned.

**What this update does NOT fix — stated plainly, not softened**:
`lambda_limit` (new-limit-order Poisson arrival rate) and `mu_cancel`
(per-share cancellation rate) remain on their fixed request-model defaults
(`4.0`/`0.05`) and are **not** calibrated by this change. This is not a
scope choice or something merely deferred to a future pass — it is a
genuine structural limitation of every data source available to this
codebase. `trade_count`, like every other feed reachable from
`data/market_data.py`, reports only already-EXECUTED trades. No provider
checked (Alpaca included) reports a new resting limit order being placed or
an existing resting order being canceled — those are the two event types
`lambda_limit`/`mu_cancel` would need to measure, and no such feed exists
here. Closing that gap for real would require a genuine L2/L3 order-book or
per-order-event data source, which this codebase does not have access to
today. `pilots/lob_simulator.py`'s module docstring and
`docs/architecture/execution.md`'s `pilots/lob_simulator.py` entry were
both updated in this same pass to state this distinction explicitly rather
than lump `theta_market` in with the still-uncalibratable pair.
