# Task Tracker: `fix_jpm_pit_coverage`

Diagnosing and fixing the JPM PIT-fundamentals row-count anomaly (135 rows vs.
~47-54 for every comparable ticker) and investigating whether
`run_pit_audit(JPM)` returning `UNVERIFIABLE` is a related defect. See
`.claude/fix_jpm_pit_coverage_implementation_plan.md` for the full root-cause
writeup and `.claude/fix_jpm_pit_coverage_walkthrough.md` for the reviewer
walkthrough.

## Checklist

- [x] Read `validation/pit_fundamentals.py` and `data/historical_store.py`;
      identified the three `fundamentals_history` writers (`edgar` backfill,
      daily `fmp`/`yahoo_computed` snapshot, dev/test `_fakemarket`/
      `audit_injection`).
- [x] Reproduced the row-count anomaly directly against the live shared DB
      (read-only queries) — confirmed JPM's 135 rows are all `source='edgar'`,
      with a clean 4/year cadence 2015-2025 and a 91-row spike confined to
      2026 alone, almost exactly one per business day.
- [x] Downloaded JPM's live SEC companyfacts payload and traced the spike to
      the filer-specific `ffd` namespace (`ffd:NrrtvMaxAggtOfferingPric`,
      `form: "424B2"`) populated by JPM's near-daily structured-note pricing
      supplements — confirmed via direct inspection of the raw payload, not
      inferred.
- [x] Reproduced `run_pit_audit`'s underlying call
      (`audit_from_historical_store`) for JPM, AAPL, and IBM at
      `decision_date="2024-06-15"` — all three return identical
      `UNVERIFIABLE`, debunking the "JPM-specific date-field" theory. No
      change to `REPORT_DATE_KEYS`/`_extract_report_date` made.
- [x] Root-caused to `get_all_filed_dates()` scanning every XBRL namespace
      instead of only the namespaces `extract_shares`/`compute_pit_ratios`
      actually read.
- [x] `data/edgar_fundamentals.py`: added `FUNDAMENTALS_NAMESPACES = ("dei",
      "us-gaap")` constant.
- [x] `scripts/backfill_edgar_fundamentals.py`: `get_all_filed_dates()`
      scoped to `FUNDAMENTALS_NAMESPACES`. Verified live against JPM: 146 →
      48 filed dates, landing in the 47-54 peer range.
- [x] `scripts/cleanup_pit_fundamentals_noise.py`: new, dry-run-by-default,
      re-runnable script (opens the live DB `mode=ro` unless `--apply`) that
      re-fetches a symbol's companyfacts, recomputes the corrected date set,
      and reports stale `source='edgar'` rows.
- [x] Ran the cleanup script `--dry-run` (default) against JPM/AXP/SYF —
      JPM 135→48 (87 stale), SYF 49→47 (2 stale); AXP hit transient sandbox
      network truncation on SEC's multi-MB endpoint and could not be
      verified live (its already-normal stored count of 50 is consistent
      with not sharing JPM's magnitude). **`--apply` was never run — the
      live DB was not written to.**
- [x] `tests/test_backfill_edgar_fundamentals.py`:
      `TestFiledDateNamespaceFiltering` regression class added.
- [x] `tests/test_pit_fundamentals.py`:
      `TestVerdictIsIndependentOfPitRowCount` regression class added.
- [x] `pytest tests/test_pit_fundamentals.py -q` → 31 passed.
- [x] `pytest tests/test_pit_fundamentals.py tests/test_backfill_edgar_fundamentals.py tests/test_edgar_fundamentals.py -q` → 61 passed.
- [x] Committed to `fix-jpm-pit-coverage` (not `main`).
- [x] `git fetch origin && git rebase origin/main` — clean, no conflicts,
      diff unchanged; tests re-run and still green post-rebase.
- [x] `.claude/fix_jpm_pit_coverage_implementation_plan.md` written.
- [x] `.claude/fix_jpm_pit_coverage_task.md` written (this file).
- [x] `.claude/fix_jpm_pit_coverage_walkthrough.md` written.
- [ ] Pushed to `origin/fix-jpm-pit-coverage` and PR opened against `main`.
- [ ] Human decision on whether/when to run
      `scripts/cleanup_pit_fundamentals_noise.py --apply` for JPM/SYF's
      already-flagged stale rows — explicitly OUT OF SCOPE for this PR.
