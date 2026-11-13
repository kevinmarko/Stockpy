# Cleanup: dead-code decisions D3-D6 + nightly local CI timer (walkthrough)

Date: 2026-11-13. Branch: `cleanup-dead-code-d3-d5-nightly`. Operator approval: 2026-11-13.
Plan: `.claude/cleanup_hardening_sweep_2026_10_dead_code_removal_plan.md` ("Final decisions" section).
Tracker: `.claude/cleanup_hardening_sweep_2026_10_task.md` ("Round 4").

## A. Dead-code decisions
- **D1** stays KEEP: `build_universe`, `daemon_owns_agentic_side_effects` and the `RunContext` `*_fn` fields are untouched.
- **D3 (yes)**: already done by #1133 (`68d9e69`). Re-verified on `origin/main` that the three modules and their
  tests are gone and that nothing outside `legacy/` imports them (incl. the Gravity suite and `investyo_mcp_server.py`).
  No file deleted on this branch.
- **D4**: `tests/test_production_steps_universe.py::TestDaemonMatchesAdvisoryInputsUniverse` compared the step to the
  function the step calls. Rewritten as `TestDaemonUniverseMatchesDocumentedRules`: same 8 scenarios, expected
  universes written by hand from the documented rules. Mutation-checked twice (see the plan).
- **D5 (keep)** and **D6 (moot)**: recorded, no code change.

## B. Nightly local CI timer
- `scripts/com.investyo.nightly-local-ci.plist`: daily 22:30 local, `RunAtLoad` false, no `KeepAlive`, placeholder
  `__STOCKPY_REPO__` as its only path (no user home), not installed by either installer.
- `scripts/nightly_local_ci.sh`: runs `make local-ci ARGS="--profile nightly"` in its own checkout, logs under
  `settings.LOCAL_DATA_ROOT/logs/nightly_ci/` (resolved through `settings`, falling back to the env var or
  `~/.stockpy_local`), writes `latest.log` and `last_status`, prunes run logs older than 30 days, exits with make's code.
- Install/uninstall: `docs/RUNBOOK.md` §5.1b. Tests: `tests/test_nightly_local_ci_schedule.py` (also lints every
  `scripts/*.plist`). No GitHub workflow.

## C. Tracker
#1132 (`90ec9ab`) and #1133 (`68d9e69`) merged; branch rules deleted by the operator (force-push/deletion
protection gone too); pip-audit clean after #1140; nightly done; D3-D6 recorded.
