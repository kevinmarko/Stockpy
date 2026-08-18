# Task Tracker: vol_mispricing Live Paper-Execution Endpoint

- [x] Branch off `origin/phased_agent_audit_system` (`add-vol-mispricing-execute-endpoint`)
- [x] Fix `execution/options_paper_executor.py::execute_earnings_crush_trade`
  - [x] Add `strategy_name: Optional[str] = None` param (backward compatible)
  - [x] Fix `$1.50`/`$150.00` fabrication fallback → honest refusal, no partial fill
  - [x] Verify no existing test relied on the fabricated fallback
  - [x] `uv run pytest tests/test_options_paper_executor.py -q -m "not network"` clean
- [x] Add `pilots/vol_mispricing.py::execute_vol_mispricing_trade`
  - [x] Symbol validation, `is_live` refusal, `dry_run` preview
  - [x] Require non-empty `candidate["legs"]`
  - [x] Leg translation: `action`→`side`, `unit_price × 100.0`→`fill_price`
  - [x] Delegates to shared executor with `strategy_name="Vol Mispricing"`
  - [x] Added to `__all__`
  - [x] AST import-safety test (`tests/test_vol_mispricing.py::test_vol_mispricing_ast_import_safety`) still passes
- [x] Add `POST /pilots/options/mispricing/execute` to `api/pilots_api.py`
  - [x] `VolMispricingExecuteRequest` model
  - [x] Same auth tier as siblings (`require_command_token` + `require_paper_broker_writes_enabled`)
  - [x] Enforced deployability gate — blocks unless `override_deployability_gate=true`
  - [x] `gate_status` always echoed in response
  - [x] Corrected stale comment above `OPTIONS_DESK_DEPLOYABILITY_GATES["vol_mispricing"]`
- [x] Update `tests/test_pilots_api.py`
  - [x] Replace `test_vol_mispricing_has_no_paper_execute_endpoint` per its own docstring
  - [x] Blocked-without-override test (verifies `execute_vol_mispricing_trade` never called)
  - [x] Override + dry_run proceeds test
  - [x] `gate_status` always present (blocked and overridden paths)
  - [x] Fails-closed-on-writes-disabled / wrong-token tests
- [x] Add executor tests: `strategy_name` default-preserved + override, no-fabrication refusal
- [x] Add `tests/test_vol_mispricing.py` tests: validation, dry-run, missing-candidate,
      leg-translation worked example (hand-computed), no-fabrication-refusal
- [x] Docs: `docs/signals/vol_mispricing.md` "Live Paper-Execution Status" rewritten
- [x] Docs: `docs/VALIDATION_STRATEGY_FIX_LOG.md` dated entry appended
- [x] Docs: `CLAUDE.md` bullet corrected; `AGENTS.md` auto-synced (verified identical)
- [x] Full verification suite green (see plan doc's Verification section)
- [x] `scripts/measure_settings_census.py --write` + `scripts/settings_liveness.py --write` run;
      census diff committed (route count 78→79 + line drift), liveness unchanged
- [ ] Commit, push, open PR (never merge — leave for orchestrating session)
- [ ] Poll CI to green or genuine failure
