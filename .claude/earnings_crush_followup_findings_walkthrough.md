# Walkthrough — earnings_crush.py follow-up findings #2 and #9

## What changed

**`pilots/earnings_crush.py`**
- `evaluate_earnings_crush_candidates()` now resolves a `company_name` per candidate via
  `store.get_fundamentals_raw(sym)`, guarded by `hasattr(store, "get_fundamentals_raw")` so a
  test double/stub lacking the method never raises (it's caught by the existing outer
  per-symbol `try/except`, which would otherwise silently drop the candidate). It also copies
  the already-computed `hist_res["moves"]` list into `historical_summary["moves"]`.
- `to_earnings_crush_candidate_response()` now emits `response["historical_moves"]` — the raw
  per-quarter gap moves, percent-scaled (`gap_pct * 100`) and **reversed to oldest-first**
  (the source list is newest-first; the webapp's bar chart labels index 0 as the oldest
  quarter, `Q-8`) — and `response["company_name"]` when available. Both follow the function's
  existing "omit if missing/empty" convention. A comment documents why `report_timing` is
  deliberately never populated: no real BMO/AMC source exists in this codebase (see
  `get_historical_earnings_moves`'s `timing_data_available: False` field).
- `execute_earnings_crush_trade()`'s success branch now reconstructs `net_credit` from the
  executor's real `net_cash_impact`/`commission`/`contracts` fields
  (`(net_cash_impact + commission) / (100 * contracts)`), never fabricating a value — `None`
  when those fields are absent from the executor's response.

**Webapp**
- `EarningsCrushExecutionResult.net_credit` is now optional in `types.ts`.
- `EarningsCrushScanner.tsx`'s success-toast message now guards `res.net_credit?.toFixed(2) ?? "—"`.

**Tests** (`tests/test_earnings_crush.py`)
- `TestCompanyNameResolution`: company_name populated from a real fundamentals row; `None`
  when the store lacks `get_fundamentals_raw` entirely (proves the existing `MockHistoricalStore`
  fixture keeps working); `None`/omitted for missing/empty/whitespace/non-string/`None`
  fundamentals values.
- `TestToEarningsCrushCandidateResponseHistoricalMoves`: `historical_moves` is oldest-first and
  percent-scaled; omitted (not `[]`) when the candidate's moves list is empty; `report_timing`
  is never populated.
- `TestExecuteEarningsCrushTradeNetCredit`: `net_credit` matches the hand-computed formula
  against a mocked `OptionsPaperExecutor.execute_earnings_crush_trade` response; `None` when
  `net_cash_impact`/`commission` are missing.

**Docs**
- `docs/signals/earnings_crush.md` gained a new "Defects found while analysing this pilot"
  item 2, in the same style as item 1 (the BMO/AMC fix), forward-referencing
  `docs/known_issues/earnings_crush_uoa_followup_audit_findings.md` (created in a separate,
  concurrent PR — not present in this branch).

## Verification

```
pytest tests/test_earnings_crush.py tests/test_pilots_paper_broker.py -q
# 32 + 175 = 207 passed

npm run --prefix webapp typecheck
# clean, no errors
```

No new failures introduced relative to the pre-existing test suite.
