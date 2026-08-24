# Implementation Plan: Earnings Crush / UOA Diagnostics Follow-Up (Finding #7)

## Scope

Implement finding #7 (the "distinguish nothing-found from fetch-failed"
honesty fix) across `pilots/earnings_crush.py` and `api/pilots_api.py`, plus
webapp type updates and a combined follow-up-audit known-issues doc. Explicitly
excludes `pilots/unusual_options_flow.py` (owned by a sibling agent/branch,
`unusual-options-flow-engine-fixes`).

## Steps

1. **`pilots/earnings_crush.py`**
   - Add `diagnostics: Optional[Dict[str, Any]] = None` kwarg to
     `evaluate_earnings_crush_candidates` and `get_earnings_crush_candidates`
     (the alias forwards it).
   - Populate `diagnostics["symbols_total"]` near the top of
     `evaluate_earnings_crush_candidates`.
   - Populate `diagnostics["store_available"]`/`diagnostics["options_provider_available"]`
     after the existing store/options_provider resolution block.
   - Populate `diagnostics["symbols_errored"]` inside the existing per-symbol
     `except Exception` block.
   - Purely additive: `diagnostics=None` (default) leaves behavior unchanged.

2. **`api/pilots_api.py`**
   - `get_options_flow_unusual`: pass a local `diagnostics` dict into
     `get_unusual_options_activity`, derive `degraded` from
     `symbols_fetch_failed`/`read_from_cache`, add `degraded`/
     `symbols_fetch_failed` to the response.
   - `get_options_earnings_crush_candidates`: pass a local `diagnostics` dict
     into `get_earnings_crush_candidates`, derive `degraded` from
     `store_available`/`options_provider_available`, add `degraded`/
     `symbols_errored` to the response.

3. **`webapp/src/api/types.ts`**
   - `UnusualOptionsFlowResponse`: add `degraded?`/`symbols_fetch_failed?`.
   - `EarningsCrushCandidatesResponse`: add `degraded?`/`symbols_errored?`.
   - `UnusualOptionTrade`: add `price_is_estimated?`/`spot_price_is_estimated?`
     (sibling branch's finding #5, added here so the merged PR has one clean
     `types.ts` diff).

4. **Tests**
   - `tests/test_earnings_crush.py::TestEarningsCrushDiagnostics` — 5 new
     tests covering the additive-default, happy-path, store-construction-
     failure, per-symbol-error, and alias-forwarding cases.
   - `tests/test_pilots_paper_broker.py` — 4 new tests (2 per endpoint) added
     to the existing `TestEarningsCrushEndpoints`/`TestUnusualFlowEndpoints`
     classes, mocking the underlying scan functions with a `diagnostics`-
     mutating `side_effect`.

5. **Docs**
   - `docs/signals/options_flow_sentiment.md`: new "Defects found while
     analysing `pilots/unusual_options_flow.py`" section summarizing findings
     #3–#8.
   - `docs/known_issues/earnings_crush_uoa_followup_audit_findings.md`: new
     combined write-up covering findings #2–#9, plus a new
     `docs/known_issues/README.md` index row.

6. **Verification**
   - `pytest tests/test_earnings_crush.py -q`
   - `pytest tests/test_pilots_paper_broker.py -q`
   - `npm run --prefix webapp typecheck`

7. **PR artifacts** — this file, the task tracker, and the walkthrough, all
   under `.claude/` with the `earnings_crush_uoa_diagnostics_followup_` prefix.
