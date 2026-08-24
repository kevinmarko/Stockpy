# Known issue (2026-08-24): Robinhood live snapshot fabricated $0 equity/buying-power on a swallowed robin_stocks API failure

**Status: fixed.** Branch `fix-rh-snapshot-fabrication`.

## What happened

`data/robinhood_portfolio.py::_fetch_live_snapshot()` read the account-level
equity and buying-power fields as:

```python
portfolio_profile: dict = r.load_portfolio_profile() or {}
account_profile: dict = r.load_account_profile() or {}

equity_str: str = (
    portfolio_profile.get("equity")
    or portfolio_profile.get("extended_hours_equity")
    or "0"
)
buying_power_str: str = (
    account_profile.get("buying_power")
    or account_profile.get("cash")
    or "0"
)
total_equity = float(equity_str or 0.0)
buying_power = float(buying_power_str or 0.0)
```

**Root cause, confirmed against the installed `robin_stocks` source**
(`robin_stocks/robinhood/helper.py::request_get`, `dataType='indexzero'`,
which `load_portfolio_profile()`/`load_account_profile()` both call
internally): on a non-200 HTTP response, a missing `"results"` key, or an
empty `"results"` list, `request_get` does **not** raise — it prints a
message and returns bare `None`. `build_holdings()` behaves the same way
(`robin_stocks/robinhood/account.py:770-771`: `if not positions_data or not
portfolios_data or not accounts_data: return({})`). None of these three
calls ever propagate an auth failure, rate limit, or transient 5xx as an
exception — they all silently degrade to an empty/`None` result.

This module's `... or {}` / `... or "0"` fallbacks then treated that
swallowed failure exactly like a legitimate response, silently substituting
a fabricated `$0` equity/buying-power reading with zero indication anything
had gone wrong — a CONSTRAINT #4 violation.

## Why this was severe

Because `_fetch_live_snapshot()` never raised on this path,
`fetch_account_snapshot()`'s existing, correctly-implemented three-tier
DB → JSON-cache → live fallback (its outer `try/except` around the live
fetch) was **never invoked** — the fabricated snapshot was instead treated
as a bona fide successful fetch, unconditionally written to both the JSON
cache and the DB via `_write_cache()`/`HistoricalStore.save_account_snapshot()`,
and then served as genuinely "fresh" (`is_stale() == False`) for up to
`max_age_hours` (default 20 h) on every subsequent call. Any downstream
sizing/risk-gate/reporting consumer reading `total_equity`/`buying_power`
during that window would silently see a fabricated `$0` for a real,
non-empty account.

## Reproduced end-to-end (before the fix)

Both scenarios below were reproduced against the real, unmodified
`_fetch_live_snapshot()` with `robin_stocks` monkeypatched — no code under
test was changed to make them fail:

- **Scenario A** — the profile calls return a malformed/empty body (auth
  failure, rate limit, transient error) while the holdings call succeeds:
  produced a snapshot with a real position (e.g. AAPL) but
  `total_equity=0.0`/`buying_power=0.0`, no exception raised.
- **Scenario B** — `build_holdings()`'s own *internal* profile round-trip
  transiently fails (it calls `load_portfolio_profile()`/
  `load_account_profile()` itself, separately from this module's own later
  calls at what were then lines ~367-368) while this module's later, direct
  profile calls succeed: produced `positions={}` with
  `total_equity=50000.0`/`buying_power=1234.56` — an internally-inconsistent
  "$50k account holding nothing" snapshot, also with no exception raised.
  This module makes two independent round-trips for profile data (once
  inside `build_holdings()`, once again directly a few lines later), so a
  transient hiccup landing between them is a real, not contrived, trigger.

## The fix

After `r.load_portfolio_profile()`/`r.load_account_profile()`, the code now
validates that each returned a genuinely non-empty, well-formed response
**before** treating the live fetch as successful:

```python
portfolio_profile: dict = r.load_portfolio_profile() or {}
account_profile: dict = r.load_account_profile() or {}

if not portfolio_profile:
    raise RuntimeError(...)  # "load_portfolio_profile() returned an empty/malformed response..."
if not account_profile:
    raise RuntimeError(...)  # "load_account_profile() returned an empty/malformed response..."
```

A raised exception flows into the *existing*, already-correct three-tier
fallback in `fetch_account_snapshot()` — no new fallback logic was needed,
only making the failure actually observable.

**Why this signal is unambiguous.** Robinhood's own "indexzero" profile
record always carries its full field set on a genuine response — a real
account, even a brand-new one with zero everything, reports real (possibly
`"0.00"`) values for `equity`/`buying_power`, not a missing dict entirely.
Verified directly against the `robin_stocks` source: `request_get(...,
dataType='indexzero')`'s only way to return an empty dict-like value on
success would require `data['results'][0]` to itself be falsy, which
Robinhood's API never returns for a real account — the `None`/failure path
is entirely distinct and produces the empty/`None` result this fix guards
on.

**Why `build_holdings()` returning `{}` is deliberately *not* guarded the
same way.** Unlike the two profile calls, `build_holdings()` returning `{}`
is genuinely ambiguous — it is also `robin_stocks`' own return value for a
real account that legitimately holds zero positions (e.g. an account that
just deposited cash and hasn't bought anything yet, where `equity`/
`buying_power` are real, non-zero values). Structuring the check around
"did we get a well-formed response at all" (the two profile dicts) rather
than "is there any content" (`build_holdings()`'s dict) is what lets a
genuinely empty account pass through un-flagged while still closing the
fabricated-$0 hole. Scenario B above therefore still does not raise after
this fix — its `positions={}` outcome is an accepted, disclosed scope
boundary (see Tests below), not a residual gap this change attempts to
close: there is no way to distinguish "build_holdings()'s own internal
profile call had a transient hiccup" from "this account really holds
nothing" without also risking a false failure on every legitimate
zero-position account.

**Secondary, related fix — asymmetric exception-fallback fan-out.** The
live-fetch-*exception* handler in `fetch_account_snapshot()` (the outer
`try/except` around `_fetch_live_snapshot()`) previously only checked the
JSON cache tier on failure, never the DB tier — asymmetric with the
"auto-refresh disabled" branch a few lines above it, which already
correctly checks DB then JSON. This is now consistent: DB first, then JSON,
matching the existing order used elsewhere in this same function. This
guards against a JSON-cache-missing-but-DB-present split (a real class of
gap this repo has hit before — see
`docs/known_issues/forecast_tracker_local_data_root_split.md` for a prior
`LOCAL_DATA_ROOT`/worktree desync between two stores).

## Tests

`tests/test_robinhood_portfolio.py`:

- `TestFetchLiveSnapshotFailsClosedOnMalformedProfile` — Scenario A
  reproduced against the real `_fetch_live_snapshot()`: an empty
  `load_portfolio_profile()`/`load_account_profile()` dict (and the bare
  `None` robin_stocks actually returns) now raises `RuntimeError` naming
  the failing call, even when holdings and the other profile call both
  succeeded with real values; a dedicated end-to-end test
  (`test_failure_flows_through_three_tier_fallback_to_stale_cache`) drives
  the *public* `fetch_account_snapshot(force=True)` entry point through the
  unmodified live-fetch code path and confirms the raised failure correctly
  reaches the existing DB → JSON-cache fallback, returning the honest stale
  cache instead of a fabricated $0 snapshot.
- `TestFetchLiveSnapshotHoldingsAmbiguity` — documents and locks in the
  deliberate scope boundary: Scenario B (`build_holdings()={}` with
  well-formed, non-zero profile dicts) and a genuinely-empty new account
  (`build_holdings()={}` with well-formed, all-`"0.00"` profile dicts) both
  do **not** raise.
- `TestLiveFetchExceptionFallbackChecksBothTiers` — the exception-fallback
  path now checks DB first (JSON cache absent), falls through to JSON when
  the DB read itself fails, and still raises when neither tier has
  anything.

One pre-existing test
(`TestFetchLiveSnapshotLoginDelegation::test_worker_marker_set_calls_login_with_device_approval_mode`)
previously used empty `{}` profile dicts purely to check `r.login()`'s call
arguments, not to exercise this fabrication path — updated to use
well-formed (`"0.00"`) dicts so it keeps testing what it was written to
test.

## What's still open

- Scenario B's internally-inconsistent-looking `positions={}` +
  real-nonzero-equity outcome is not fully closed — see "Why
  `build_holdings()` returning `{}` is deliberately not guarded the same
  way" above. A future fix disambiguating this would need a different
  signal than emptiness alone (e.g. retrying `build_holdings()` once before
  giving up, or reading Robinhood's raw HTTP status rather than
  `robin_stocks`' already-swallowed result) — out of scope for this change.
