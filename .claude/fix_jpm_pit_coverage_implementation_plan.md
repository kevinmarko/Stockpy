# Implementation Plan: JPM PIT-Fundamentals Row-Count Anomaly

**Slug:** `fix_jpm_pit_coverage`
**Date:** 2026-08-28
**Author:** Claude Code (data-integrity audit)

## Context

`get_pit_coverage_report` showed JPM with 135 point-in-time (PIT) fundamentals
rows in `fundamentals_history`, versus ~47-54 for every other comparable
large-cap ticker in the same universe (AXP, CAT, IBM, MRK, T, VZ, ...) across
the same ~2015-2026 span — a 2.5-2.8x outlier. Separately, `run_pit_audit(JPM,
"2024-06-15")` returned `UNVERIFIABLE` ("no report/quarter-end date field
found"). Both symptoms needed independent diagnosis before assuming either was
a bug, per this repo's CONSTRAINT #4/#6 discipline (never fabricate a finding,
fail closed on what can't be verified).

## Diagnostic approach

1. Read `validation/pit_fundamentals.py` (the audit's date-field lookup) and
   `data/historical_store.py` (`fundamentals_history` DDL, `upsert_fundamentals_pit`,
   the three writers: `edgar` backfill, daily `fmp`/`yahoo_computed` snapshot,
   dev/test `_fakemarket`/`audit_injection`).
2. Queried the live shared `fundamentals_history` table directly (read-only)
   at `settings.LOCAL_DATA_ROOT / "quant_platform.db"`. Found JPM's 135 rows
   are ALL `source='edgar'`, broken down by year: a clean, uniform 4/year
   (10-K + 3x10-Q) from 2015-2025 (44 rows) — then **91 rows in 2026 alone**,
   almost exactly one per business day from 2026-04-06 through 2026-08-13.
   AXP (50), SYF (49), and every other comparator showed no such spike.
3. Downloaded JPM's live SEC `companyfacts` payload
   (`https://data.sec.gov/api/xbrl/companyfacts/CIK0000019617.json`, ~7.9MB)
   and cross-referenced every XBRL fact whose `filed` date fell in that dense
   window. Found the entire spike traced to ONE non-`us-gaap`/`dei` namespace:
   `ffd:NrrtvMaxAggtOfferingPric` (SEC's Rule 456/457 fee-tagging fact),
   `form: "424B2"` — JPMorgan's near-daily structured-note/CD pricing
   supplement filings. 101 distinct `filed` dates from this ONE fact in the
   2026-04..2026-08 window alone; 3,369 individual data points.
4. Confirmed `data/edgar_fundamentals.py`'s `extract_shares()`/
   `compute_pit_ratios()` — the only consumers of a companyfacts payload for
   actual fundamentals values — read exclusively from `facts["facts"]["dei"]`
   and `facts["facts"]["us-gaap"]`. `scripts/backfill_edgar_fundamentals.py`'s
   `get_all_filed_dates()`, however, scanned **every** top-level namespace in
   the payload (`facts.get("facts", {}).values()`), including `ffd` — a drift
   between what dates get promoted to "report dates" and what data those
   dates could possibly be backing.
5. Reproduced `run_pit_audit`'s underlying call
   (`validation.pit_fundamentals.audit_from_historical_store`) directly for
   JPM, AAPL, and IBM at `decision_date="2024-06-15"`. All three returned
   **identical** `UNVERIFIABLE` verdicts. Traced why: the function always
   audits the single *newest* `fundamentals_history` row by `as_of`, and for
   every actively-fetched symbol today, that newest row is a same-day
   `_fakemarket`/`fmp` snapshot with no report-date-bearing field at all —
   independent of how much real PIT history sits underneath it. This ruled
   out the "JPM's payload uses a differently-named date field" hypothesis
   entirely; no `REPORT_DATE_KEYS`/`_extract_report_date` change is needed.

## Root cause

`get_all_filed_dates()` treated every XBRL fact's `filed` timestamp as a
candidate PIT "report date," regardless of which namespace it came from. For
most tickers this is harmless because their companyfacts payload only ever
has `dei` and `us-gaap` facts. JPM's payload additionally carries a
filer-specific `ffd` namespace populated by its structured-note issuance
program (effectively a daily-cadence Securities Act shelf takedown), and each
one of those pricing supplements got promoted to a spurious "report date"
with zero fundamentals content riding along with it — the resulting row just
silently re-stamps whatever `us-gaap` fact was last legitimately filed
on/before that date, under a new artificial `report_date`.

## Fix

- `data/edgar_fundamentals.py`: new `FUNDAMENTALS_NAMESPACES = ("dei",
  "us-gaap")` constant — the single source of truth for which XBRL
  namespaces carry real fundamentals data, matching exactly what
  `extract_shares`/`compute_pit_ratios` already read.
- `scripts/backfill_edgar_fundamentals.py`: `get_all_filed_dates()` now
  skips any namespace not in `FUNDAMENTALS_NAMESPACES` before collecting
  `filed` dates.
- Verified against JPM's live companyfacts payload: filed-date count drops
  from 146 (all namespaces, `since="2015-01-01"`) to 48 (dei+us-gaap only) —
  squarely inside the 47-54 peer range.

## Cleanup of already-ingested bad rows

Existing `fundamentals_history` rows can't be reclassified from what's
already stored (`upsert_fundamentals_pit` persists only the *computed
ratios*, never the raw XBRL payload, so there's no stored namespace/form
info to filter on retroactively). `scripts/cleanup_pit_fundamentals_noise.py`
(new) re-fetches each target symbol's live companyfacts, recomputes the
corrected date set with the fixed `get_all_filed_dates`, and diffs it against
what's actually stored for `source='edgar'`.

Safety, mirroring `scripts/migrate_to_local_data_root.py`'s convention:
- **Dry-run by default.** The DB is opened via SQLite's `mode=ro` URI unless
  `--apply` is passed, so a dry-run cannot write to the live shared DB even
  by accident.
- `--apply` performs a targeted `DELETE ... WHERE symbol=? AND
  source='edgar' AND report_date=?` per flagged row, only after the dry-run
  report has listed the exact same rows up front.
- A symbol whose live re-fetch fails is skipped with a warning, never
  treated as "0 legitimate rows" (CONSTRAINT #6 — a failed check must not
  become a more aggressive delete).

**This PR only runs `--dry-run`.** Results: JPM 135 → 48 stored rows (87
flagged stale), SYF 49 → 47 (2 flagged stale — same bug class, negligible
scale, consistent with SYF's much lower structured-note issuance volume).
AXP's live re-fetch hit transient sandbox network truncation on SEC's
multi-MB endpoints and could not be verified this way; its already-normal
stored count (50) is consistent with it not sharing JPM's magnitude of this
bug. **Actually deleting the flagged rows requires an explicit human
decision to run `--apply` — not done as part of this change.**

## Tests added

- `tests/test_backfill_edgar_fundamentals.py::TestFiledDateNamespaceFiltering`
  — regression coverage for the namespace-scoping fix: a non-fundamentals
  extension namespace's dates are excluded; `dei`+`us-gaap` dates are both
  included; a high-frequency noise namespace (mirroring JPM's real shape)
  does not inflate the corrected date count.
- `tests/test_pit_fundamentals.py::TestVerdictIsIndependentOfPitRowCount` —
  pins down the debunked UNVERIFIABLE theory: a symbol with abundant real
  PIT history and one with almost none produce the identical `UNVERIFIABLE`
  verdict once a newer, non-PIT snapshot lands on top, proving PIT row count
  never explains `run_pit_audit`'s verdict.

## Files changed

- `data/edgar_fundamentals.py` — `FUNDAMENTALS_NAMESPACES` constant.
- `scripts/backfill_edgar_fundamentals.py` — `get_all_filed_dates()` scoped
  to it.
- `scripts/cleanup_pit_fundamentals_noise.py` — new, dry-run-by-default
  cleanup/reporting script.
- `tests/test_backfill_edgar_fundamentals.py` — new
  `TestFiledDateNamespaceFiltering` class.
- `tests/test_pit_fundamentals.py` — new
  `TestVerdictIsIndependentOfPitRowCount` class.
- `.claude/fix_jpm_pit_coverage_implementation_plan.md` / `_task.md` /
  `_walkthrough.md` — this PR-artifact trio.

## Verification

- `pytest tests/test_pit_fundamentals.py -q` → 31 passed.
- `pytest tests/test_pit_fundamentals.py tests/test_backfill_edgar_fundamentals.py tests/test_edgar_fundamentals.py -q` → 61 passed.
- Both re-run clean after `git rebase origin/main` (origin had advanced ~10
  unrelated commits — a fundamentals-deadline feature, a pipeline-timeout
  fix, a module-efficiency audit doc — rebase was conflict-free, diff
  unchanged).
- `python3 scripts/cleanup_pit_fundamentals_noise.py --tickers JPM,AXP,SYF`
  (dry-run, default) run against the live shared DB — see cleanup-of-bad-rows
  section above for results. No `--apply` run at any point.
