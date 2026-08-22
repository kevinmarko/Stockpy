# Fix fabricated SPY spot price + dead beta fallback in options portfolio Greeks

## Context

The operator (via a subagent report they independently re-verified against the
real code, and which I independently re-verified again by reading the source,
reproducing the `TypeError` live, and querying the real paper-trading DB) found
two CONSTRAINT #4 violations in `pilots/options_risk.py` that reach the live
automated SPY delta-hedging cycle in `main.py` (gated by
`settings.OPTIONS_DELTA_HEDGE_ENABLED`, currently `False` by default but
already `True` for this operator's own paper account):

- **Bug A**: `_resolve_symbol_beta`'s second-tier fallback calls
  `data.fmp_fundamentals.compute_beta(clean)` with one positional argument,
  but the real signature is `compute_beta(stock_returns, market_returns, *,
  min_obs=60)`. Every call raises `TypeError`, silently swallowed by a bare
  `except Exception: pass`, so beta collapses to a hardcoded `1.0`
  indistinguishable from a genuinely-measured beta of 1.0.
- **Bug B**: `calculate_portfolio_greeks` resolves SPY spot as
  `spot_map.get("SPY") or 500.0` — a book with no direct SPY position never
  queries a real SPY quote and `beta_weighted_delta_spy` is silently computed
  off a fabricated $500, unlike the sibling functions in
  `pilots/options_hedging.py` (`get_delta_hedge_preview`,
  `execute_delta_hedge`'s internal-recompute branch), which already resolve a
  real quote or honestly refuse.

**Confirmed live-path impact**: `main.py`'s automated hedge cycle
(`run_once()`, ~line 1486) calls `calculate_portfolio_greeks(store=...)` with
no `spy_spot`, hitting Bug B, then `execute_delta_hedge(...)` which correctly
resolves the REAL SPY price for the fill a few lines later — so the hedge's
**share quantity is sized off a fabricated $500 while it fills at the real
price**. I confirmed this against the operator's own local
`~/.stockpy_local/quant_platform.db`: 4 real `hedge_spy_*` paper orders exist,
filled at real prices (~$762–769, consistent with real SPY around 2026-08-20
through 2026-08-22), including one that **sold 1,226 shares** (~$943k
notional) — a size consistent with the ~1.53x inflation (`765/500`) the $500
fabrication would produce relative to a correctly-sized hedge.

**A third, adjacent bug (Bug C, found while reading this exact block)**:
`main.py`'s post-hedge logging reads `_hedge_res.get("executed")` and
`_hedge_res.get("spot_price", 0.0)`, but `execute_delta_hedge`'s real return
contract uses `"hedged"` (not `"executed"`) and nests the fill price at
`fill["fill_price"]` (not top-level `"spot_price"`) — so the INFO log
confirming an automated hedge fired has never once printed. Same bug class
CLAUDE.md already documents for `options_hedging.py`'s alert-dispatch calls
(PR that first wired those: wrong-shaped dict passed to a key-reading
consumer, silently no-op).

Goal: make `calculate_portfolio_greeks` never fabricate a SPY price, make the
dead beta fallback either real or removed-with-visibility, thread ONE
consistently-resolved real SPY quote through the automated hedge cycle's
sizing AND fill, fix the dead log-key bug, and document all of it per
CLAUDE.md's "Everything else" tier (feature branch + PR, planned first,
docs updated as part of the change).

## Root-cause analysis backing the fix choices

- `pilots/rolling_beta.py::rolling_beta_view` (tier 1 of `_resolve_symbol_beta`)
  and a correctly-fixed tier 2 (`compute_beta` called properly, per the one
  real working reference at `api/ws_api.py:625`'s `_compute_betas_sync`) both
  source `HistoricalStore.get_bars` for the same two tickers with materially
  the same floor: tier 1 needs `window` (60) overlapping days to emit even one
  non-NaN rolling-beta row (`lookback_days = max(504, window*3)` = 504 days
  fetched); a correctly-fixed tier 2 mirroring `_compute_betas_sync` would use
  `lookback_days=400` and `min_obs=60`. Tier 2 fetches **less** history than
  tier 1 (400 vs 504 days) for the **same** minimum-observation floor (60) —
  it can essentially never succeed when tier 1 already failed for lack of
  cached history. **Conclusion: fixing tier 2's call signature would not add
  real coverage — it is genuinely redundant dead weight, not a working
  fallback that happens to be miscalled.** The fix removes it rather than
  repairing it, replacing the silent 1.0 default with a logged one and an
  explicit "was this beta measured or defaulted" flag surfaced per-position
  (closing the observability gap the operator's report explicitly flagged as
  worth having either way).
- `calculate_portfolio_greeks` already resolves a real quote for every other
  ticker via the injected/resolved `market_provider.get_latest_quote(t)` loop
  over `distinct_tickers`. The fix adds `"SPY"` to that same set (only when
  the caller didn't pass an explicit `spy_spot`), so SPY gets a REAL quote
  through the exact same mechanism and test seam every other symbol already
  uses — no new provider call path, no risk of diverging from the
  test-injected `market_provider` mocks already used throughout
  `tests/test_options_risk.py`.

## Files to change

1. **`pilots/options_risk.py`**
   - Add `import logging` / `logger = logging.getLogger(__name__)` (currently
     absent from this module).
   - `_resolve_symbol_beta(ticker) -> Tuple[float, bool]`: keep the SPY/VOO/IVV
     `(1.0, True)` identity shortcut and the `rolling_beta_view` tier 1 path
     unchanged in behavior; remove the dead `compute_beta(clean)` tier 2
     block (with a comment citing the 400-vs-504-day/same-60-floor analysis
     above); on any failure, `logger.warning(...)` once per call (not spammy —
     this only fires when a symbol genuinely lacks 60 days of cached history)
     and return `(1.0, False)`.
   - `calculate_portfolio_greeks`: call `_resolve_symbol_beta` and unpack
     `(beta_val, beta_is_measured)`; add `beta_is_estimated: bool` to each
     position breakdown dict and a new top-level
     `symbols_with_estimated_beta: List[str]` (mirrors the existing
     `positions_with_missing_data`/`beta_excluded_symbols` pattern).
   - SPY spot resolution: add `"SPY"` to `distinct_tickers` when
     `spy_spot is None` so it resolves through the same `market_provider`
     loop as every other ticker; drop the `or 500.0` fabrication entirely.
     Track a new `spy_spot_resolved: bool` (True when a real, positive quote
     was used — either caller-supplied or freshly resolved; True vacuously in
     the zero-positions early return; False only when genuinely unresolvable).
     `beta_weighted_delta_spy` stays `0.0` (never fabricated math) when
     `spy_spot_resolved` is False, and the returned dict gains `"spy_spot"`
     (the resolved value or `None`) and `"spy_spot_resolved"` — additive keys
     on an already-`Dict[str, Any]` response, so no frontend/API type changes
     are required for this PR.

2. **`pilots/paper_broker.py::get_portfolio_greeks()`** — resolve SPY via
   `pilots.price_provider.get_current_price("SPY")` (the same helper
   `get_delta_hedge_preview`/`execute_delta_hedge` already use) before calling
   `calculate_portfolio_greeks`, passing the resolved value through (or
   `None` on failure, so `calculate_portfolio_greeks`'s own internal
   resolution gets a second real attempt rather than immediately reporting
   unresolved).

3. **`main.py`** — extract the "3. Dynamic SPY Delta Hedging" block (~line
   1486) into a small top-level helper,
   `_run_automated_delta_hedge_cycle(executor) -> Optional[Dict[str, Any]]`,
   placed near `run_once()`. It resolves `spy_spot` ONCE via
   `pilots.price_provider.get_current_price("SPY")`; if unavailable, logs a
   WARNING and returns `None` without calling either function (fail closed —
   no cycle at all, rather than a badly-sized one); otherwise passes the
   SAME resolved `spy_spot` into both `calculate_portfolio_greeks(...,
   spy_spot=spy_spot)` and `execute_delta_hedge(..., spy_spot=spy_spot)`, so
   sizing and fill are computed off one consistent real quote. Fixes Bug C in
   the same helper: the post-hedge INFO log reads `hedge_res.get("hedged")`
   (not `"executed"`) and `hedge_res.get("fill", {}).get("fill_price", 0.0)`
   (not top-level `"spot_price"`). `run_once()`'s call site shrinks to a
   3-line `if settings.OPTIONS_DELTA_HEDGE_ENABLED: _run_automated_delta_hedge_cycle(_executor)`,
   pulling the logic out from under `run_once()`'s single giant
   `try/except` so it's unit-testable without mocking the whole pipeline.

4. **Tests**
   - `tests/test_options_risk.py`: update the two existing tests that
     monkeypatch `_resolve_symbol_beta` to return `(beta, True)` tuples
     instead of bare floats. Add: (a) a test that an empty
     `rolling_beta_view` result yields `(1.0, False)` and logs a WARNING
     (via `caplog`); (b) a test that `compute_beta`/`fmp_fundamentals` is
     never imported/called by `_resolve_symbol_beta` anymore; (c) a test that
     `calculate_portfolio_greeks` with no SPY position but a `market_provider`
     mock that resolves a real (non-$500) SPY quote produces
     `beta_weighted_delta_spy` computed off that real value, with
     `spy_spot_resolved is True` and `spy_spot` equal to it; (d) a test that
     when the `market_provider` mock returns no SPY quote at all,
     `beta_weighted_delta_spy == 0.0`, `spy_spot_resolved is False`,
     `spy_spot is None` — proving no `500.0` appears anywhere in the output;
     (e) a test that a position with an unmeasurable beta shows up in
     `symbols_with_estimated_beta` and `positions[i]["beta_is_estimated"] is True`,
     while a measured one shows `False`.
   - `tests/test_pilots_paper_broker.py`: a direct unit test of
     `pilots.paper_broker.get_portfolio_greeks()` (not just the HTTP
     endpoint) asserting it calls `calculate_portfolio_greeks` with a
     `spy_spot` resolved from `pilots.price_provider.get_current_price`
     rather than omitting it.
   - `tests/test_run_once.py` (or wherever `main.py` helpers are unit tested
     today — will confirm exact file during implementation): tests for
     `_run_automated_delta_hedge_cycle` — (a) real SPY quote available →
     `calculate_portfolio_greeks` and `execute_delta_hedge` both called with
     the identical `spy_spot`; (b) SPY quote unavailable → neither is called,
     a WARNING is logged, returns `None`; (c) a `hedged=True` result logs the
     INFO line via the corrected `"hedged"`/`fill.fill_price` keys (closes
     Bug C).
   - Run the full targeted suite (`pytest tests/test_options_risk.py
     tests/test_options_hedging.py tests/test_pilots_paper_broker.py
     tests/test_run_once.py -q`) plus `make verify` / the repo's `/verify`
     gate before opening the PR.

5. **Docs** (per CLAUDE.md's mandatory documentation-update step)
   - `docs/architecture/execution.md`: update the existing
     `pilots/options_risk.py` (line 10) and `pilots/options_hedging.py`
     (line 11) bullets to describe the fixed beta-fallback/estimation-flag
     behavior and the no-longer-fabricated SPY spot resolution.
   - New `docs/known_issues/options_risk_fabricated_spy_spot.md` (exact slug
     TBD at write time) following the established format (see
     `scenario_matrix_field_mismatch.md`, `macro_killswitch_fail_open_on_missing_fred_data.md`):
     what happened, root cause (both bugs + Bug C), the fix, the DB evidence
     of the 4 already-placed hedge orders (including the anomalous
     1,226-share one) — stated as informational/measured evidence of
     historical impact, explicitly NOT retroactively "fixed" — and what's
     intentionally left open (no webapp change in this PR; `spy_spot`/
     `spy_spot_resolved`/`beta_is_estimated` are additive API fields not yet
     surfaced in the Paper Broker screen).
   - `CLAUDE.md` (mirrors to `AGENTS.md` via the existing sync hook): a new,
     concise dated bullet under the options-desk section pointing to the
     known-issues doc for full detail, matching the file's existing pattern
     for this class of fix.
   - `docs/VALIDATION_STRATEGY_FIX_LOG.md`: not applicable — this isn't a
     `STRATEGY_REGISTRY` deployability-gate fix, skip per that log's own
     documented scope.

## Explicitly out of scope for this PR

- No webapp/`webapp/src/**` changes. The new `spy_spot`, `spy_spot_resolved`,
  `beta_is_estimated`, `symbols_with_estimated_beta` fields are additive on an
  already-`Dict[str, Any]` response and don't require frontend changes to
  avoid breaking anything; surfacing them in the Paper Broker UI (e.g. an
  "estimated beta" badge, an "SPY quote unavailable" banner) is a reasonable
  follow-up but not required to fix the reported correctness bug.
- No retroactive correction of the 4 already-placed hedge orders in the
  operator's local paper account — documented as informational evidence only,
  per the operator's own instruction that this is "not required to fix
  retroactively."

## Verification

- Reproduce Bug A's `TypeError` and Bug B's `500.0` fabrication is gone via
  the new/updated unit tests above (all should fail against current `main`
  and pass after the fix — will confirm the "fails first" step during
  implementation for the new tests specifically).
- `pytest tests/test_options_risk.py tests/test_options_hedging.py
  tests/test_pilots_paper_broker.py tests/test_run_once.py -q` — zero
  failures.
- `make verify` (or `./verify.command`) — full offline gate.
- This is a live (paper) order-sizing path per CLAUDE.md's "Everything else"
  tier: branch `fix-delta-hedge-fabricated-spy-spot`, PR with plan/task/
  walkthrough artifacts under `.claude/` using that branch-scoped filename
  prefix, no direct commits to `main`.
