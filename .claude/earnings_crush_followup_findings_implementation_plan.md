# Implementation Plan — earnings_crush.py follow-up findings #2 and #9

## Scope

Two follow-up findings from an audit of `pilots/earnings_crush.py` (stacked on top of the
BMO/AMC bar-alignment fix, PR #889):

- **Finding #2**: `historical_moves` and `company_name` were computed/resolvable but never
  wired through `to_earnings_crush_candidate_response()`, so the webapp's earnings-crush
  scanner bar chart and company-name display had no real data. `report_timing` is deliberately
  NOT added — no real BMO/AMC source exists in this codebase (verified against FMP's own
  `/earnings` schema, per item 1's own finding).
- **Finding #9**: `execute_earnings_crush_trade()`'s success branch never returned `net_credit`,
  while the webapp unconditionally called `.toFixed(2)` on it.

## Changes

1. `pilots/earnings_crush.py`
   - `evaluate_earnings_crush_candidates()`: resolve `company_name` defensively via
     `store.get_fundamentals_raw(sym)` behind a `hasattr()` guard (the test fixture
     `MockHistoricalStore` doesn't implement it); add `"moves": hist_res["moves"]` to
     `historical_summary`; add `"company_name"` to the candidate dict.
   - `to_earnings_crush_candidate_response()`: add `historical_moves` (percent-scaled,
     reversed to oldest-first to match the webapp's `Q-8`..`Q-1` labeling) and `company_name`
     (omit-if-missing convention); add a comment explaining why `report_timing` is
     intentionally absent.
   - `execute_earnings_crush_trade()`: reconstruct `net_credit` from the executor's real
     `net_cash_impact`/`commission`/`contracts` fields; `None` (never fabricated) when those
     fields are missing.
2. `webapp/src/api/types.ts`: `EarningsCrushExecutionResult.net_credit` changed to optional
   (`number | undefined`). `historical_moves`/`company_name`/`report_timing` already existed
   as optional fields — no change needed.
3. `webapp/src/components/options/EarningsCrushScanner.tsx`: guard `res.net_credit.toFixed(2)`
   with `res.net_credit?.toFixed(2) ?? "—"`.
4. `tests/test_earnings_crush.py`: new test classes covering company_name resolution (present,
   absent-store-method, unusable-value cases), `historical_moves` reshape (oldest-first,
   percent-scaled, omitted-when-empty, `report_timing` never populated), and `net_credit`
   reconstruction/None-on-missing-fields.
5. `docs/signals/earnings_crush.md`: new "Defects found" item 2, forward-referencing
   `docs/known_issues/earnings_crush_uoa_followup_audit_findings.md` (created in a separate PR).

## Verification

- `pytest tests/test_earnings_crush.py tests/test_pilots_paper_broker.py -q` — all pass.
- `npm run --prefix webapp typecheck` — clean.
