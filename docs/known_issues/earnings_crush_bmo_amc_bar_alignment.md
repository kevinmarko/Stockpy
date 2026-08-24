# Known issue (2026-08-24): earnings_crush.py's realized-move calculation assumed every company reports before market open

**Status: fixed.** Branch `fix-earnings-crush-bmo-amc-blindspot`.

## What happened

`pilots/earnings_crush.py::get_historical_earnings_moves` computed each quarter's realized
post-earnings price gap as:

```python
open_price = float(event_bar["Open"])       # Open on the first bar >= event_date
prev_close = float(prev_bar["Close"])        # Close on the prior bar
gap_usd = abs(open_price - prev_close)
gap_pct = gap_usd / prev_close
```

This is `Open[event_date] - Close[event_date-1]` — the correct measurement **only** when a
company reports its earnings **before market open (BMO)** on `event_date`. `event_bar["Close"]`
(the event date's own close) was never read anywhere in the function, and no bar after
`bar_idx` was ever consulted. When a company reports **after market close (AMC)** — the
majority case for large-cap tech, and this module's own default universe in
`get_earnings_crush_candidates` (`NVDA, AAPL, MSFT, TSLA, AMZN, GOOGL, META, AMD, NFLX, DIS`)
— the real reaction shows up one trading day later, as `Open[event_date+1] -
Close[event_date]`. That pair of values was structurally invisible to this function.

`evaluate_earnings_crush_candidates`'s front-week expiration picker carried the identical
blind spot: it accepted any expiration with `ed >= event_date`, including one dated exactly
`event_date`. For an AMC reporter, an expiration on `event_date` itself expires before the
reaction ever happens, so the constructed Iron Condor would never actually experience the
move the strategy exists to trade.

Reproduced with synthetic bars simulating a true 14.66% AMC overnight reaction (flat
same-day `Open`/`Close`, with the real gap landing on `Close[event_date] →
Open[event_date+1]`): the unfixed function reported `median_move_pct ≈ 0.0` for that quarter
instead of the real ~14.66%.

## Confirmed impact

`crush_edge_ratio = expected_move_pct / realized_move_pct`. Understating the realized-move
denominator **inflates** the edge ratio and can flip `is_recommended = True` for a candidate
that does not actually have a genuine edge over the market's own implied move. A candidate
whose `is_recommended` is `True` fires `pilots/options_alerts.py::dispatch_earnings_crush_alert`
— a real (though paper-only) trade alert. This is a live-path correctness bug, not a display
bug: it affects the majority of the module's default universe, since large-cap tech names
predominantly report AMC.

## Was there a real BMO/AMC field to read instead?

Checked directly (not assumed) before choosing an inference-based fix, per CONSTRAINT #4:

- `data/fmp_client.py::earnings()` calls FMP's `/earnings` endpoint — its own docstring
  documents the date field and the `epsActual`-null lookahead rule, and says nothing about a
  time-of-day/session field.
- `data/fmp_feeds_company.py::fetch_earnings_rows()` extracts exactly `date`, `epsActual`,
  `epsEstimated`, `revenueActual`, `revenueEstimated`, `lastUpdated` from each FMP payload
  item — nothing resembling `time`/`bmo`/`amc`/`hour`/`session` is extracted or referenced.
- `data/historical_store.py`'s `earnings_events` table DDL has columns `symbol, event_date,
  eps_actual, eps_estimated, revenue_actual, revenue_estimated, last_updated, source,
  fetched_at` — no timing/session column.
- A repo-wide, case-insensitive grep for "bmo"/"amc"/"before market open"/"after market
  close"/"reportedTime"/"earningsTime" hits only two `.claude/` pre-implementation planning
  documents describing what the feature was originally intended to eventually do — aspirational
  prose, never backed by a real ingested field. `validation/pit_fundamentals.py`'s
  `earningsTimestamp` is an unrelated yfinance lookahead-audit constant, not a BMO/AMC signal,
  and is not fetched by `fmp_client.py` or persisted anywhere.

Conclusion: there is no real, already-ingested per-event BMO/AMC label anywhere in this
codebase today. A fix that pulls a real field instead of inferring one is not currently
possible without adding a new, unverified data source.

## The fix

`get_historical_earnings_moves` now computes **two** real, bar-derived candidate gaps for
each quarter instead of one:

- **BMO hypothesis** (unchanged): `|Open[bar_idx] - Close[bar_idx-1]| / Close[bar_idx-1]`.
- **AMC hypothesis** (new): `|Open[bar_idx+1] - Close[bar_idx]| / Close[bar_idx]`, computed
  only when `bar_idx+1` exists in the fetched bar window (degrades to BMO-only — today's
  exact prior behavior — when the event is on the last available bar).

Whichever gap is **larger** is used as that quarter's realized move. This is a deliberate,
principled choice, not an arbitrary one:

- It never fabricates a number — both candidate gaps are real, computed observations from
  actual price bars (CONSTRAINT #4).
- A genuine earnings reaction dominates ordinary single-day price noise, so taking the larger
  of the two gaps correctly attributes the move to whichever session actually held it, in the
  overwhelming majority of cases.
- It is the **conservative** direction for the specific failure mode this bug caused: taking
  the max can only *increase* the realized-move denominator relative to the old BMO-only
  reading, which can only *decrease* (never inflate further) `crush_edge_ratio` — the opposite
  direction of the bug's actual danger (a falsely-inflated edge ratio triggering a bad alert).

Each quarter's `moves` entry now carries `reaction_session_inferred: "bmo" | "amc"` —
explicitly labeled *inferred*, never presented as a source-confirmed label — and the
function's top-level return dict now carries `timing_data_available: False` on every code
path, self-documenting that no real per-event timing field exists today (this key is
forward-compatible: it can flip to `True` if a real field is ever integrated, without any
other consumer needing to change).

`evaluate_earnings_crush_candidates`'s front-week expiration picker now requires
`ed > event_date` (was `ed >= event_date`), so the selected expiration always clears the
earnings date entirely — covering an AMC reaction (which lands on `event_date + 1`) as well
as a BMO one (which happens during `event_date`'s own session and remains fully covered by an
expiration one day later).

## What's still open

- This is an **inference**, not a certainty. Without a real per-event timing field, there is
  no way to be 100% sure which session held the reaction. The max-of-two heuristic can, in
  rare cases, overestimate the realized move if an unrelated market-wide gap on the day
  *after* a genuine BMO reaction happens to exceed that BMO reaction itself — this would make
  the strategy *more* conservative (a lower `crush_edge_ratio`), not less, so it does not
  reintroduce the original bug's danger, but it is disclosed here rather than hidden.
- No attempt was made to add a real BMO/AMC data source (e.g. a different FMP endpoint, a
  third-party earnings-calendar API) — the fix works entirely within the existing
  `earnings_events` schema and price-bar data already available. Adding a real timing field
  later remains a valid, separate follow-up; `timing_data_available` exists specifically to
  make that a clean, additive change when/if it happens.
- The identical AMC-blindness pattern was not audited for in any other pilots module that
  reasons about earnings timing (this fix's scope was `pilots/earnings_crush.py` only).

## Tests

`tests/test_earnings_crush.py`:
- `TestHistoricalEarningsMoves::test_amc_reaction_captured_via_next_day_open` — the direct
  reproduction: a synthetic 14.66% AMC overnight reaction, asserts the fixed function reports
  the true move and labels it `"amc"` (previously reported `≈ 0.0`).
- `TestHistoricalEarningsMoves::test_bmo_reaction_still_captured_correctly` — a classic BMO
  gap, asserts it is still measured correctly and labeled `"bmo"` (no regression to the
  common case).
- `TestEvaluateEarningsCrushCandidates::test_same_day_expiration_rejected_in_favor_of_later_one`
  — an expiration dated exactly `event_date` is rejected in favor of the next later one; under
  the pre-fix comparison this scenario would have selected an expiration with no matching
  chain quotes, silently dropping the candidate entirely (`len(candidates) == 0`).
- Full existing suite (17 pre-existing tests) re-run green — the fix is a verified no-op
  against every existing fixture (they only ever vary `Open` on the event date, never `Close`,
  so the AMC-hypothesis gap always computes to `0` there).
