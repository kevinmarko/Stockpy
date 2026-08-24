# Known issue (2026-08-24): live LOB queue-fill endpoint runs on fixed constants, not the module's own empirical calibration

**Status: partially fixed — sign/units bugs fixed and tested; live calibration wiring
disclosed as an open, not-attempted gap.** Branch `fix-sor-lob-simulator-audit-findings`.

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
