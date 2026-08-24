# Walkthrough: Earnings Crush / UOA Diagnostics Follow-Up (Finding #7)

## What changed

### `pilots/earnings_crush.py`

Added an optional `diagnostics: Optional[Dict[str, Any]] = None` kwarg to
`evaluate_earnings_crush_candidates` and `get_earnings_crush_candidates` (the
convenience alias forwards it). When a caller passes a mutable dict, it is
populated with:

- `symbols_total` — universe size.
- `store_available` / `options_provider_available` — booleans reflecting
  whether `HistoricalStore`/the options provider resolved to a usable
  instance (set after the existing resolution `try/except` blocks, so a
  construction failure is honestly captured).
- `symbols_errored` — a list, appended to inside the existing per-symbol
  `except Exception` block (no new catch site added).

`diagnostics=None` (the default) is a complete no-op — every existing caller
and test is unaffected.

### `api/pilots_api.py`

Both `get_options_flow_unusual` and `get_options_earnings_crush_candidates`
now construct a local `diagnostics` dict, thread it into the underlying scan
function, and derive an honest `degraded: bool` from it — added to each
response alongside a `symbols_fetch_failed`/`symbols_errored` list
respectively. `get_options_flow_unusual` calls `pilots.unusual_options_flow.
get_unusual_options_activity(..., diagnostics=diagnostics)` — that function
does not yet carry the `diagnostics` kwarg on this branch (it is implemented
on the sibling `unusual-options-flow-engine-fixes` branch); this branch trusts
the documented contract and verifies via mocking.

### `webapp/src/api/types.ts`

- `UnusualOptionsFlowResponse` gained `degraded?: boolean;
  symbols_fetch_failed?: string[];`
- `EarningsCrushCandidatesResponse` gained `degraded?: boolean;
  symbols_errored?: string[];`
- `UnusualOptionTrade` gained `price_is_estimated?: boolean;
  spot_price_is_estimated?: boolean;` (a sibling branch's finding #5,
  included here so the merged PR carries one clean, complete `types.ts` diff).

No `mock.ts` changes were needed — all new fields are optional.

### Docs

- `docs/signals/options_flow_sentiment.md`: new "Defects found while
  analysing `pilots/unusual_options_flow.py`" section summarizing findings
  #3–#8 (implementation detail on the sibling branch, described here without
  claiming authorship of code not written on this branch).
- `docs/known_issues/earnings_crush_uoa_followup_audit_findings.md`: new
  combined write-up covering all 8 findings (#2–#9) at the level of detail
  the task specified — full detail for #7 (this branch's own work), summary
  level for the rest.
- `docs/known_issues/README.md`: new index row pointing to the doc above.

## Verification

```
pytest tests/test_earnings_crush.py -q
# 30 passed

pytest tests/test_pilots_paper_broker.py -q
# 183 passed (full file — no regressions in the other 24 endpoint test
# classes sharing this file)

npm run --prefix webapp typecheck
# clean (tsc --noEmit)
```

## Files touched

- `pilots/earnings_crush.py`
- `api/pilots_api.py`
- `webapp/src/api/types.ts`
- `tests/test_earnings_crush.py`
- `tests/test_pilots_paper_broker.py`
- `docs/signals/options_flow_sentiment.md`
- `docs/known_issues/earnings_crush_uoa_followup_audit_findings.md` (new)
- `docs/known_issues/README.md`
- `.claude/earnings_crush_uoa_diagnostics_followup_implementation_plan.md` (new)
- `.claude/earnings_crush_uoa_diagnostics_followup_task.md` (new)
- `.claude/earnings_crush_uoa_diagnostics_followup_walkthrough.md` (new, this file)

## Not touched (by design)

- `pilots/unusual_options_flow.py` — owned by the sibling
  `unusual-options-flow-engine-fixes` branch.
- `webapp/src/api/types.ts`'s `EarningsCrushCandidate`/
  `EarningsCrushExecutionResult` interfaces — owned by
  `earnings-crush-followup-historical-moves-net-credit`.
- `webapp/src/api/mock.ts` — no changes required.
