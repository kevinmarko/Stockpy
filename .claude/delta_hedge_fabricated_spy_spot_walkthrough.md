# Walkthrough — Fix fabricated SPY spot price + dead beta fallback in options portfolio Greeks

Branch: `fix-delta-hedge-fabricated-spy-spot`
Full root-cause narrative: [`docs/known_issues/options_risk_fabricated_spy_spot.md`](../docs/known_issues/options_risk_fabricated_spy_spot.md)

## What was reported

An operator (via an independently-verified subagent report) found two
CONSTRAINT #4 violations in `pilots/options_risk.py` reaching the live
automated SPY delta-hedging cycle in `main.py`:

- **Bug A**: `_resolve_symbol_beta`'s second-tier fallback called
  `data.fmp_fundamentals.compute_beta(clean)` with one positional argument
  against a function requiring two `pd.Series` — every call raised
  `TypeError`, silently swallowed, collapsing beta to a hardcoded `1.0`
  indistinguishable from a genuinely-measured value.
- **Bug B**: `calculate_portfolio_greeks` resolved SPY spot as
  `spot_map.get("SPY") or 500.0` — a book with no direct SPY position never
  queried a real SPY quote, so `beta_weighted_delta_spy` was silently
  computed off a fabricated $500.

I independently re-verified both by reading the real code, reproducing the
`TypeError` live, and querying the operator's own local paper-trading DB —
confirming 4 already-placed `hedge_spy_*` orders sized inconsistently with
their own real fill prices (see the known-issues doc for the table).

## What I found in addition

**Bug C**: while reading `main.py`'s automated hedge block to fix A/B, its
post-hedge success log read `_hedge_res.get("executed")` /
`_hedge_res.get("spot_price", 0.0)` — keys `execute_delta_hedge`'s real
return contract never had (`"hedged"`, nested `fill["fill_price"]`). The
confirmation INFO log had never once printed.

## The fix

1. **`pilots/options_risk.py`**
   - `_resolve_symbol_beta(ticker) -> Tuple[float, bool]` — keeps the
     SPY/VOO/IVV identity shortcut and the `rolling_beta_view` tier
     unchanged; removes the dead `compute_beta` tier entirely (justified by
     a lookback-window comparison showing it could never rescue a case the
     primary tier already failed — see the code comment and known-issues
     doc); logs a WARNING and returns `(1.0, False)` when no real beta
     resolves.
   - `calculate_portfolio_greeks` adds `"SPY"` to the same
     `distinct_tickers` set resolved via the existing
     `market_provider.get_latest_quote()` loop (only when the caller didn't
     already pass `spy_spot`), so SPY gets a real quote through the exact
     mechanism every other symbol uses. Drops the `or 500.0` fabrication.
     New response fields: `spy_spot`, `spy_spot_resolved`,
     `beta_is_estimated` (per position), `symbols_with_estimated_beta`
     (top-level list). `beta_weighted_delta_spy` is `0.0` — never a
     fabricated-price computation — when `spy_spot_resolved` is `False`.
2. **`pilots/paper_broker.py::get_portfolio_greeks()`** — resolves SPY via
   `pilots.price_provider.get_current_price("SPY")` before calling
   `calculate_portfolio_greeks`, matching `get_delta_hedge_preview`'s
   existing pattern.
3. **`main.py`** — extracted `_run_automated_delta_hedge_cycle(executor)`:
   resolves ONE real SPY quote and threads it into both
   `calculate_portfolio_greeks(..., spy_spot=spy_spot)` (sizing) and
   `execute_delta_hedge(..., spy_spot=spy_spot)` (fill), so they can never
   diverge again. Fails closed (skips the cycle, logs a WARNING) when no
   live quote is available. Fixes Bug C's dead log keys in the same helper.
   `run_once()`'s call site is now a 2-line conditional call.

## Explicitly out of scope

- No `webapp/src/**` changes — the new response fields are additive on an
  already-`Dict[str, Any]` API response.
- No retroactive correction of the operator's 4 already-placed hedge orders
  — documented as historical evidence only, per the operator's own
  instruction.

## Verification

- `python3 -m ruff check . --select=F821,F822,F823,E9` — all checks passed.
- Targeted suite (`tests/test_options_risk.py`, `tests/test_options_hedging.py`,
  `tests/test_pilots_paper_broker.py`, `tests/test_main.py`,
  `tests/test_run_once.py`) — 265 passed, 0 failures (includes 11 new
  regression tests plus 2 pre-existing tests updated for
  `_resolve_symbol_beta`'s new `(beta, is_measured)` return contract).
- Full offline suite (`pytest -m "not network and not slow" --dist loadgroup`,
  mirroring CI's `test` job) — **11,941 passed, 5 failed, 31 skipped.** The 5
  failures (`tests/test_data_api_chat.py::TestMultiProviderRouting::*`,
  `tests/test_gemini_live_chat.py::TestLiveChatSession::*`) are pre-existing
  and unrelated to this change — confirmed by `git stash`/`git stash pop`
  reproducing the identical 5 failures on unmodified `main` (missing
  `google-genai`/`openai` packages in this sandbox environment, not a code
  regression this PR introduced).
