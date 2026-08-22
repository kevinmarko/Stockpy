# Known issue (2026-08-22): Automated SPY delta hedge sized off a fabricated $500 price while filling at the real one

**Status: fixed.** Branch `fix-delta-hedge-fabricated-spy-spot`.

## What happened

`pilots/options_risk.py::calculate_portfolio_greeks` resolved SPY's spot
price as:

```python
if spy_spot is None:
    spy_spot = spot_map.get("SPY") or 500.0
```

`spot_map` only contains tickers actually held in the book. A portfolio with
no direct SPY position or SPY-underlying option never queried a real SPY
quote at all — `spy_spot` unconditionally fell back to a hardcoded `$500.0`,
a CONSTRAINT #4 violation indistinguishable downstream from a genuinely
resolved quote. The sibling functions in `pilots/options_hedging.py`
(`get_delta_hedge_preview`, and `execute_delta_hedge`'s internal-recompute
branch) already did this correctly — resolve a real quote or honestly
refuse — this function did not.

A second, independent bug lived in the same module:
`_resolve_symbol_beta`'s second-tier fallback called
`data.fmp_fundamentals.compute_beta(clean)` with one positional argument,
but the real signature is `compute_beta(stock_returns: pd.Series,
market_returns: pd.Series, *, min_obs=60)`. Every call raised `TypeError`,
silently swallowed by a bare `except Exception: pass`, so beta collapsed to
a hardcoded `1.0` — again indistinguishable from a genuinely-measured beta
of 1.0.

## Confirmed live-trading-path impact

`main.py`'s automated SPY delta-hedging cycle (`run_once()`, gated by
`settings.OPTIONS_DELTA_HEDGE_ENABLED`) called:

```python
_greeks = calculate_portfolio_greeks(store=_executor.store)
_hedge_res = execute_delta_hedge(store=_executor.store, portfolio_greeks=_greeks)
```

`calculate_portfolio_greeks` hit the $500 fabrication above (no `spy_spot`
was ever passed in), while `execute_delta_hedge` separately resolved and
**filled** at the real live SPY price a few lines later. The result: the
hedge's **share quantity was sized off a fabricated $500 while it filled at
the real price** — a genuine order-sizing correctness bug in a live
(paper) trading path, not merely a display bug.

**DB evidence** (this operator's own local
`~/.stockpy_local/quant_platform.db`, queried before the fix landed): 4
real `hedge_spy_*` paper orders, all filled at real prices (~$762–769,
consistent with real SPY over 2026-08-20 through 2026-08-22):

| client_order_id | side | qty | fill price | timestamp (UTC) |
|---|---|---|---|---|
| `hedge_spy_a3661c801d53` | sell | 1,226 | $769.06 | 2026-08-20 12:08:42 |
| `hedge_spy_2453b7860975` | buy | 640 | $762.60 | 2026-08-21 11:13:45 |
| `hedge_spy_2799dc6cb3c2` | sell | 27 | $765.62 | 2026-08-21 20:56:30 |
| `hedge_spy_a16cd347cb4c` | buy | 31 | $765.72 | 2026-08-22 11:42:17 |

The first order — selling 1,226 shares (~$943k notional) — is a size
consistent with the ~1.53x inflation (`765 / 500`) the $500 fabrication
would produce relative to a correctly-sized hedge: `beta_weighted_delta_spy
= net_beta_dollar_delta / spy_spot`, so understating `spy_spot` by ~35%
overstates the computed imbalance (and therefore the hedge order size) by
the same ratio. This is stated as **informational evidence of historical
impact only** — per the operator's own instruction, the already-placed
orders were not retroactively corrected as part of this fix.

The account also holds numerous small/thin-history symbols (e.g. `AGNC`,
`SKHY`, `CGBD`, `IBN`, `TWO`, `L`, `AM`, `CCC`, `SI`, `AR`) whose beta
resolution plausibly also hit the dead fallback tier (Bug A) rather than a
measured value, though this was not individually confirmed per-symbol.

## A third, adjacent bug found while fixing the above

`main.py`'s post-hedge success logging read `_hedge_res.get("executed")`
and `_hedge_res.get("spot_price", 0.0)`, but `execute_delta_hedge`'s real
return contract uses `"hedged"` (not `"executed"`) and nests the fill price
at `fill["fill_price"]` (not a top-level `"spot_price"`). Every real
`hedged=True` result therefore silently failed this key lookup and the
confirmation INFO log had never once printed. Same bug class CLAUDE.md
already documents for `options_hedging.py`'s alert-dispatch calls (a
wrong-shaped dict passed to a key-reading consumer, silently no-op) — this
is a second, independent instance of it in the same area of the code.

## The fix

- `_resolve_symbol_beta` now returns `(beta, is_measured)`. The dead
  `compute_beta(clean)` tier was removed rather than repaired: a
  correctly-fixed version (mirroring the one real working caller,
  `api/ws_api.py`'s `_compute_betas_sync`) would source the same
  `HistoricalStore` bars for the same two tickers with **less** lookback
  (400 days) than `pilots/rolling_beta.py::rolling_beta_view`'s 504, for
  the **same** 60-observation floor — it could never rescue a case the
  primary tier already failed for lack of cached history. When no real
  beta resolves, this now logs a WARNING and returns `(1.0, False)`;
  `calculate_portfolio_greeks` surfaces this per-position as
  `beta_is_estimated` and in a new top-level `symbols_with_estimated_beta`
  list.
- `calculate_portfolio_greeks` no longer fabricates a SPY price. `"SPY"` is
  added to the same `distinct_tickers` set resolved via
  `market_provider.get_latest_quote()` as every other symbol (only when the
  caller didn't already supply `spy_spot`), so it gets a REAL quote through
  the exact mechanism — and test seam — every other symbol already uses.
  When genuinely unresolvable, `beta_weighted_delta_spy` reports `0.0`
  (never a fabricated-price computation), and new `spy_spot` /
  `spy_spot_resolved` response fields let a caller distinguish "genuinely
  flat book" from "SPY quote unavailable."
- `pilots/paper_broker.py::get_portfolio_greeks()` (backing `GET
  /pilots/paper-broker/greeks`, the Paper Broker screen's headline
  β-weighted Δ_SPY metric) now resolves SPY via
  `pilots.price_provider.get_current_price` before calling
  `calculate_portfolio_greeks`, matching how `get_delta_hedge_preview`
  already does it.
- `main.py`'s automated hedge cycle was extracted into
  `_run_automated_delta_hedge_cycle(executor)`. It resolves `spy_spot`
  **once** via `pilots.price_provider.get_current_price("SPY")` and threads
  the identical value into both `calculate_portfolio_greeks(...,
  spy_spot=spy_spot)` (sizing) and `execute_delta_hedge(...,
  spy_spot=spy_spot)` (fill), so sizing and fill can never diverge again.
  When no live SPY quote is available, the cycle is skipped entirely
  (logged at WARNING) rather than running with a guessed price — fail
  closed. The dead `"executed"`/`"spot_price"` log-key bug is fixed in the
  same helper (`"hedged"` / `fill["fill_price"]`).

## What's still open

- No webapp change. The new `spy_spot`, `spy_spot_resolved`,
  `beta_is_estimated`, `symbols_with_estimated_beta` fields are additive on
  an already-`Dict[str, Any]` API response and require no frontend change
  to avoid breaking anything (`GET /pilots/paper-broker/greeks` has no
  strict Pydantic response model). Surfacing them in the Paper Broker UI
  (e.g. an "estimated beta" badge, an "SPY quote unavailable" banner) is a
  reasonable follow-up, not required by this fix.
- The 4 already-placed hedge orders listed above are left as-is in the
  operator's local paper account; no retroactive correction was performed.
- Per-symbol confirmation of which held tickers actually hit the dead beta
  fallback (Bug A) historically was not performed — the thin-history
  symbols named above are a plausible, not confirmed, list.

## Tests

`tests/test_options_risk.py` (six new tests: `_resolve_symbol_beta`'s
estimated-vs-measured distinction and WARNING log, that
`data.fmp_fundamentals.compute_beta` is never called anymore, that a real
non-$500 SPY quote drives `beta_weighted_delta_spy` when no SPY position is
held, that an unresolvable SPY quote yields `0.0`/`spy_spot_resolved=False`
rather than a fabricated computation, and that estimated-beta symbols are
surfaced), `tests/test_pilots_paper_broker.py` (two new tests on
`get_portfolio_greeks()` threading a resolved/unavailable `spy_spot`),
`tests/test_main.py` (three new tests on
`_run_automated_delta_hedge_cycle`: consistent `spy_spot` across sizing and
fill, fail-closed skip on an unavailable quote, and the corrected
success-log keys actually firing).
