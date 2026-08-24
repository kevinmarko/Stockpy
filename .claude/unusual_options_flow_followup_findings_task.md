# Task Tracker — `pilots/unusual_options_flow.py` follow-up audit findings #3–#8 (UOA half)

| # | Finding | Status |
|---|---------|--------|
| 3 | Real HV30 threaded into the live IV-burst path (`_resolve_live_historical_vol_30d` + wiring into `get_unusual_options_activity`'s live-scan loop) | Done |
| 4 | Mid-block deadband (`MID_BLOCK_DIRECTIONAL_THRESHOLD`) in `categorize_trade_aggressiveness` | Done |
| 5a | `trade_price_is_estimated` tracked in `_extract_contract_fields` | Done |
| 5b | `UOARecord.price_is_estimated` / `.spot_price_is_estimated` fields + wiring in `scan_unusual_options_activity`; `webapp/src/api/types.ts` additive fields | Done |
| 6 | Per-contract isolation (`try/except ... continue` inside the anomaly loop) | Done |
| 7 (UOA half) | `diagnostics` kwarg on `get_unusual_options_activity` | Done |
| 8 | Atomic temp+rename write in `save_uoa_records` | Done |
| — | AST-safety allowlist widened (module docstring + `TestASTSafety`) for `data.historical_store` | Done |
| — | Regression tests for all of the above | Done (19 new tests) |

## Explicitly out of scope (owned by a sibling branch)

- `pilots/earnings_crush.py` — untouched.
- `api/pilots_api.py` — untouched.
- `docs/signals/earnings_crush.md` — untouched.
- Finding #7's `earnings_crush.py` half — not implemented here (UOA half only, per task).

## Verification performed

- `pytest tests/test_unusual_options_flow.py -q` → 51 passed.
- `pytest tests/test_unusual_options_flow.py tests/test_pilots_strategy_matrix.py -q` → 133 passed.
- `ruff check pilots/unusual_options_flow.py tests/test_unusual_options_flow.py` → no new
  genuine-bug-class findings vs. the pre-existing baseline (89 → 96 findings, all 7 new
  ones are pre-existing-style `Dict`/`dict` annotation nits matching the file's own
  established convention).
- `webapp/src/api/types.ts` — additive-only interface fields; `tsc` unavailable in this
  worktree (`node_modules` not installed), so typecheck could not be executed directly,
  but the risk is minimal (two new optional fields, no other code references them yet).

## Files touched

- `pilots/unusual_options_flow.py`
- `tests/test_unusual_options_flow.py`
- `webapp/src/api/types.ts`
- `.claude/unusual_options_flow_followup_findings_implementation_plan.md` (new)
- `.claude/unusual_options_flow_followup_findings_task.md` (new)
- `.claude/unusual_options_flow_followup_findings_walkthrough.md` (new)
