# Walkthrough: JPM PIT-Fundamentals Row-Count Anomaly

**Slug:** `fix_jpm_pit_coverage`
**Audience:** PR reviewer

## What was reported

`get_pit_coverage_report` showed JPM with 135 point-in-time (PIT) fundamentals
rows in `fundamentals_history`, versus ~47-54 for every other comparable
large-cap ticker (AXP, CAT, IBM, MRK, T, VZ, ...) across the same ~2015-2026
span — JPM was a 2.5-2.8x outlier. Separately, `run_pit_audit(JPM,
"2024-06-15")` returned `UNVERIFIABLE`, with the open question of whether
that was connected to the row-count anomaly or a second, independent bug.

## Finding #1: the row-count anomaly is real, and it's JPM-specific

Querying the live shared `fundamentals_history` table directly (read-only)
confirmed JPM's 135 rows are all `source='edgar'` (the SEC EDGAR backfill
writer). Breaking them down by year told the whole story immediately:

```
2015-2025: 4 rows/year, every year (44 rows total) — a completely normal
           10-K + 3x10-Q cadence, matching every comparator ticker.
2026:      91 rows — almost exactly one per business day, 2026-04-06
           through 2026-08-13.
```

AXP (50 rows) and SYF (49 rows) — also bank holding companies — showed no
such spike, ruling out "any bank holding company files this much" as the
explanation.

## Finding #2: the actual filing behind the spike

Downloading JPM's live SEC `companyfacts` payload
(`https://data.sec.gov/api/xbrl/companyfacts/CIK0000019617.json`) and
cross-referencing every XBRL fact whose `filed` date fell inside the dense
2026-04..2026-08 window traced the entire spike to **one** fact:

```json
{"end": "2024-07-31", "val": 1000000, "accn": "0001213900-26-040362",
 "form": "424B2", "filed": "2026-04-06", "frame": "CY2024Q2I"}
```

`ffd:NrrtvMaxAggtOfferingPric` — SEC's Rule 456/457 registration-fee tagging
fact — populated by Rule 424(b)(2) pricing supplements. JPMorgan runs an
extremely high-cadence structured-note/CD issuance program and files one of
these supplements on nearly every business day; 101 distinct `filed` dates
came from this single fact in the 2026-04..08 window alone (3,369 individual
data points across all its tranches). Each one carries **zero** fundamentals
content (no EPS, ROE, revenue — nothing `compute_pit_ratios` would ever use)
but was still promoted to a full PIT "report date."

## Root cause

`scripts/backfill_edgar_fundamentals.py::get_all_filed_dates()` iterated
`facts.get("facts", {}).values()` — **every** top-level XBRL namespace in the
companyfacts payload — collecting any `filed` date it found. Meanwhile
`data/edgar_fundamentals.py`'s `extract_shares()`/`compute_pit_ratios()` (the
functions that actually compute the values a PIT row stores) only ever read
`facts["facts"]["dei"]` and `facts["facts"]["us-gaap"]`. For most tickers
that gap is invisible because their companyfacts payload never has anything
outside those two namespaces. JPM's payload has a third, filer-specific `ffd`
namespace from its note-issuance program, and every one of its entries
slipped through as a spurious report date — each one silently re-stamping
whatever real `us-gaap` fact was last legitimately filed on/before it under
a fabricated `report_date`.

## What changed

- `data/edgar_fundamentals.py`: added `FUNDAMENTALS_NAMESPACES = ("dei",
  "us-gaap")` — a single, named source of truth for which namespaces carry
  real fundamentals data.
- `scripts/backfill_edgar_fundamentals.py::get_all_filed_dates()`: now
  skips any namespace not in `FUNDAMENTALS_NAMESPACES` before collecting
  dates, so the date-scan and the ratio/share extraction can never drift
  apart again.

Verified directly against JPM's live payload: filed-date count drops from
146 (unscoped) to 48 (scoped) — landing squarely inside the 47-54 range
every other ticker already sits in.

## Finding #3 (the red herring): `run_pit_audit`'s UNVERIFIABLE verdict is NOT JPM-specific

The task also flagged `run_pit_audit(JPM, "2024-06-15")` → `UNVERIFIABLE` as
a possible second bug, hypothesizing JPM's payload might use a differently
named report-date field the currently-checked list (`REPORT_DATE_KEYS`)
doesn't cover. Reproducing the exact same underlying call
(`validation.pit_fundamentals.audit_from_historical_store`, which is all
`investyo_mcp_server.py::run_pit_audit` calls) for JPM, AAPL, and IBM at the
same `decision_date` returned the **identical** `UNVERIFIABLE` verdict for
all three:

```
JPM  -> UNVERIFIABLE | report_date=None
AAPL -> UNVERIFIABLE | report_date=None
IBM  -> UNVERIFIABLE | report_date=None
```

Why: `audit_from_historical_store()` always audits the single **newest**
`fundamentals_history` row by `as_of` — it does not select a row based on
`decision_date`, and it is completely unaffected by how much real PIT
history exists underneath. For every actively-fetched symbol today, that
newest row happens to come from a same-day, non-PIT snapshot writer
(`_fakemarket` for JPM/AAPL, `fmp` for IBM) whose raw payload has no
`mostRecentQuarter`/`lastFiscalYearEnd`/`report_date`/`earningsTimestamp`
field at all — so the fail-closed `UNVERIFIABLE` fires for everyone, symbol
row-count irrelevant. **No change to `REPORT_DATE_KEYS` or
`_extract_report_date` was made** — there is nothing wrong with the date-key
list; the newest-row-always-wins selection is working exactly as documented,
just against a shape of stored data (a fresher non-PIT snapshot sitting on
top of real PIT history) the original bug report didn't have visibility into.

## Cleanup of already-ingested bad rows — dry-run only

`fundamentals_history` only ever persists the *computed* ratios, never the
raw XBRL payload (see `upsert_fundamentals_pit`'s docstring), so there's no
way to tell "was this stored report_date real or `ffd` noise" from the DB
alone after the fact. `scripts/cleanup_pit_fundamentals_noise.py` (new)
re-fetches each target symbol's live companyfacts, recomputes the corrected
date set with the now-fixed `get_all_filed_dates`, and diffs it against what
that symbol actually has stored for `source='edgar'`.

It is dry-run by default and opens the live DB via SQLite's `mode=ro` URI
unless `--apply` is explicitly passed, mirroring
`scripts/migrate_to_local_data_root.py`'s safety convention. This PR ran it
**dry-run only**:

```
[JPM]  stored: 135   corrected: 48   would delete 87 stale rows
[SYF]  stored: 49    corrected: 47   would delete 2 stale rows
[AXP]  SKIP — SEC EDGAR companyfacts fetch returned nothing (transient
       sandbox network truncation on the multi-MB endpoint; not a real
       "no data" result)
```

SYF's 2 stale rows confirm the same bug *class* affects other structured-note
issuers, just at negligible scale relative to JPM's issuance cadence. AXP
could not be verified live in this sandbox, but its already-normal stored
count (50, matching every other peer) is consistent with it not sharing
JPM's magnitude of the problem.

**No `--apply` was run. The live shared database was never written to by
this change.** Actually deleting JPM's/SYF's flagged stale rows is left as an
explicit follow-up decision for a human operator to make and execute.

## Tests

- `tests/test_backfill_edgar_fundamentals.py::TestFiledDateNamespaceFiltering`
  — a non-fundamentals extension namespace's dates are excluded; `dei` and
  `us-gaap` dates are both still included; a high-frequency noise namespace
  sized like JPM's real one does not inflate the corrected date count.
- `tests/test_pit_fundamentals.py::TestVerdictIsIndependentOfPitRowCount` —
  locks in Finding #3: a symbol with dozens of real PIT rows and one with a
  single PIT row both return `UNVERIFIABLE` once a newer non-PIT snapshot
  sits on top, proving row count and audit verdict are unrelated.

`pytest tests/test_pit_fundamentals.py -q` → 31 passed.
`pytest tests/test_pit_fundamentals.py tests/test_backfill_edgar_fundamentals.py tests/test_edgar_fundamentals.py -q` → 61 passed.
Both re-verified green after rebasing onto `origin/main` (which had advanced
~10 unrelated commits) with a clean, conflict-free rebase.
