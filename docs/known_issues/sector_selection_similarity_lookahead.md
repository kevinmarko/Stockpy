# `sector_selection_engine.py`'s semantic-similarity term had no point-in-time awareness (dormant lookahead bias)

**Status: Fixed and verified.** Found during a secondary audit pass (2026-08-24)
deep-auditing `sector_selection_engine.py`'s ranking math (a prior, shallower pass
had only confirmed the module uses semantic-embedding similarity rather than price
correlation, without auditing the arithmetic or the lookahead-safety of every term).

## How this was found

Tracing `run_sector_selection`'s `as_of` parameter — whose entire stated purpose is
letting a backtest replay compute "as of" a past date — through every call it makes,
to confirm it's genuinely threaded end to end rather than silently dropped at some
call site in favor of wall-clock time.

## Root cause

The Sector Heat Factor term is genuinely causal: `compute_spec_sector_heat(...,
as_of=resolved_now, ...)` is explicitly threaded and covered by its own dedicated
lookahead test file. The **semantic-similarity term was not** — `_rank_one_target`
called `resolve_target_description(target, historical_store=historical_store)` with
no `as_of` at all, and `resolve_target_description` itself had no such parameter
either. Internally it read:

```python
history_df = historical_store.get_fundamentals_history(symbol_upper)  # no upper bound
...
raw_json_str = history_df.iloc[-1].get("raw_json")  # always the LATEST row
```

`HistoricalStore.get_fundamentals_history(symbol, since=None)` only ever supported a
lower bound (`since`) — there was no mechanism anywhere in the chain to say "as of
date X." A backtest replaying `run_sector_selection(["X"], as_of=<2015 date>)` for a
company whose core business changed materially since 2015 would silently embed the
company's **current** `longBusinessSummary` when scoring 2015 — textbook lookahead
bias, in the one term of the ranking formula whose entire job is describing what the
company IS.

**Dormant, not exploited, at the time of the audit**: `run_sector_selection`'s only
real production caller (`pipeline/production_steps.py`, the live daily pipeline)
never passes a historical `as_of` — it always scores "now," where "current business
description" and "as-of business description" are the same thing. No backtest/replay
caller of this function exists yet. But the gap had zero test coverage (every
existing test mocked `resolve_target_description` away entirely) and would have
silently corrupted the first backtest/replay caller that ever exercised it, with no
error and no warning.

## Fix

Mirrors this codebase's existing, established point-in-time convention
(`HistoricalStore.get_fundamentals_asof`, added for the PIT fundamentals audit) —
`report_date`, the causal filing date, not `as_of`, the cache-write timestamp, is
the correct filter column.

- New `HistoricalStore.get_fundamentals_raw_json_asof(symbol, as_of_date)`: the most
  recent `raw_json` whose `report_date <= as_of_date`, `NULL`-`report_date` rows
  excluded (can't be PIT-verified, so not trusted) — same SQL shape as
  `get_fundamentals_asof`, just returning the raw JSON blob instead of the 9 typed
  numeric fields that method returns.
- `resolve_target_description` gained an `as_of: Optional[datetime] = None`
  parameter. When given, it routes through the new point-in-time method; when
  omitted (every existing caller today), it preserves the exact prior behavior
  (`get_fundamentals_history(...).iloc[-1]`, most-recent-row-regardless-of-date) —
  purely additive, byte-identical for every caller that doesn't pass it.
- `sector_selection_engine.py::_rank_one_target` gained the matching `as_of`
  parameter, and `run_sector_selection` now threads `as_of=resolved_now` into it —
  the same instant already threaded into the heat-term call, so both terms are now
  consistently as-of-aware.

## A second, unrelated finding fixed in the same pass — `degraded_reason` masking

`_rank_one_target`'s `degraded_reason = heat_degraded_reason or similarity_reason`
had the precedence backwards. `heat_degraded_reason` (e.g. `"review_unavailable"`)
is a deliberately broad provenance flag, stamped even when `shf` computed fine and
`correlation_coefficient` is a genuinely valid number — that priority IS intentional
and is preserved (confirmed by the existing
`test_heat_degraded_reason_takes_priority_over_similarity` test, unaffected by this
fix). But when `cos` (cosine similarity) is itself `NaN`, `similarity_reason` is the
ACTUAL reason `correlation_coefficient` is `None` — the old precedence let a routine,
non-blocking heat flag silently mask that real, blocking cause whenever both
happened to be set on the same row (a realistic combination: this repo's own
documentation notes Reddit — the heat term's review-volume input — is typically
unconfigured by default, so `"review_unavailable"` is a common, non-fatal
degradation). Fixed by swapping the operand order:
`similarity_reason or heat_degraded_reason` — `similarity_reason` is `None`
whenever `cos` is valid, so this still falls through to `heat_degraded_reason` in
that case, changing nothing about the intentional-priority test's outcome.

## Verification

- `tests/test_historical_store.py`: new `TestPITFundamentals` coverage for
  `get_fundamentals_raw_json_asof` — before-first-filing (`None`), between two
  filings (older description, never the newer one), after both (newer description),
  unknown symbol (`None`).
- `tests/test_sector_embeddings.py`: new `TestResolveTargetDescription` coverage —
  `as_of=None` stays on `get_fundamentals_history` unchanged; `as_of` given routes
  through `get_fundamentals_raw_json_asof` and never touches
  `get_fundamentals_history`; no PIT-eligible row returns `None`, never a
  fallback to the most-recent (potentially future-relative) description.
- `tests/test_sector_selection_lookahead.py`: new
  `test_as_of_forwarded_to_resolve_target_description`, the similarity-term
  companion to the pre-existing heat-term threading test.
- `tests/test_sector_selection_engine.py`: new
  `test_blocking_similarity_reason_not_masked_by_informational_heat_reason`,
  reproducing the exact masking combination (heat degraded AND similarity blocked
  on the same row) and confirming `degraded_reason` now reports the real blocking
  cause.
- Full affected suite (`test_sector_selection_engine.py`,
  `test_sector_selection_lookahead.py`, `test_sector_embeddings.py`,
  `test_historical_store.py`): 165 passed, 0 regressions.

## A real behavioral consequence for the live pipeline, checked rather than assumed

`run_sector_selection` threads `as_of=resolved_now` into `_rank_one_target`
UNCONDITIONALLY — including for the one real production caller
(`pipeline/production_steps.py`), which never passes an explicit `as_of` but always
gets `resolved_now = datetime.now(timezone.utc)` internally. So the live daily
pipeline now ALSO goes through the point-in-time-aware branch of
`resolve_target_description`, not the old unconditional-most-recent branch — this is
NOT a no-op for production, and was verified rather than assumed to be safe:

- `data/yahoo_fundamentals.py::compute_fundamentals` (this repo's PRIMARY
  fundamentals source, `FUNDAMENTALS_SOURCE="yahoo"` default) never includes
  `longBusinessSummary` in its return dict at all — grepped directly, confirmed
  absent. So the primary provider's cached rows never fed
  `resolve_target_description`'s fundamentals fallback path either before or after
  this fix; nothing changes for those rows.
- `data/market_data.py::YFinanceProvider.get_fundamentals` (the emergency fallback)
  passes yfinance's raw `Ticker.info` dict through nearly verbatim — this DOES carry
  `longBusinessSummary`, and it also naturally carries one of
  `REPORT_DATE_KEYS` (`mostRecentQuarter`/`lastFiscalYearEnd`) from the SAME
  underlying dict, so `report_date` gets populated correctly for these rows too.
  Confirmed by direct code reading, not assumed.
- **The one row shape that genuinely changes behavior**: any cached row that carries
  `longBusinessSummary` but NO usable `REPORT_DATE_KEYS` value (a real, reproduced
  case — a raw payload with `longBusinessSummary` but none of `mostRecentQuarter`/
  `lastFiscalYearEnd`/`report_date`/`earningsTimestamp` writes `report_date=NULL` via
  the real `_upsert_fundamentals`/`_extract_report_date_str` write path, verified by
  test). Before this fix, such a row's description would still have been used
  (`get_fundamentals_history().iloc[-1]`, no report_date requirement). After this
  fix, it is correctly excluded — `resolve_target_description` returns `None`, and
  that sector-ranking cycle's similarity term degrades honestly to `None` /
  `no_target_description` rather than using a row whose real-world timing can't be
  verified. This is the intended, CONSTRAINT #4-consistent behavior (prefer an
  honest gap over an unverifiable value), not an accidental regression — but it IS a
  real behavior change for any live row shaped this way, disclosed here rather than
  silently shipped.

## What this does NOT fix / disclosed scope

- No backtest/replay caller of `run_sector_selection` exists in this codebase yet —
  this fix closes a latent gap ahead of that caller being written, it does not add
  one.
- This sandbox has no live-market network access, so the actual, current shape of
  `fundamentals_history.raw_json` rows in any real operator's live database was not
  inspected — the analysis above is grounded in reading every provider's real return
  shape and a passing regression test reproducing the exact degrade case, not in a
  live-DB query.
