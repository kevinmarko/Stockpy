# Step 5.4b-i task tracker: a cutover-neutral `make verify`, plus the dry-run / keyless-run warnings

Plan: `.claude/shrink_step5_4_callers_implementation_plan.md` (PR 5.4b section, re-split 2026-10-12). Walkthrough:
`.claude/shrink_step5_4b_i_verify_helper_walkthrough.md`. Stacked on 5.4a (PR #1117): the branch contains 5.4a's commits, so its diff
against `main` shows both until 5.4a merges.

- [x] Re-split 5.4b after `main` (#1119) showed the live daemon is still on `shadow` until the 2026-10-12..10-16 review ends; the full cutover-dependent change set is parked on the local branch `step5-4b-ii-cutover-dependent-full` for 5.4b-ii
- [x] `scripts/step5_4_verify_cycle.py` (new): one standalone dry-run cycle; queue mode, symbol-rating and cap-audit recording forced off; Robinhood account fetch cache-only; heartbeat task and every alert suppressed; all restored on exit; fail closed if an override cannot be applied
- [x] Exit codes: 0 ok, 1 cycle failed, 3 snapshot not refreshed by this run, 4 synthetic data / no signals
- [x] `Makefile` (`&&`-chained recipe, `.PHONY`) and `verify.command` (propagates the status, `--dist loadgroup`)
- [x] `tests/test_step5_4_verify_cycle.py` (50 tests; each guard mutation-checked) incl. the production `AgenticQueueStep` run under the forced mode
- [x] Real run in an isolated data root with `primary` in the environment and no keys: exit 4, no queue/watch/advisory/heartbeat files, `make _live_run` fails
- [x] `--dry-run` is not side-effect-free in `primary`: HOW_TO_GUIDE, RUNBOOK day-of-switch step and the `regime-model-tuning` skill now say so
- [x] The synthetic-data gate does not cover a keyless run: RUNBOOK 3.15 and the `robinhood-execution` skills now say so
- [x] Skill accuracy fixes that hold in any mode: `alert-rule-authoring` (who sends which push, `settings.ALERT_NTFY_TOPIC`), `incident-triage` (verify via `trigger_run`, 423), `agentic-discovery` (universe builder)
- [x] `ORCHESTRATOR_DAEMON_ENABLED` description/comment corrected (+ `mock.ts`), `weekly-edgar.plist` made valid XML
- [x] Settings census/liveness regenerated; CLAUDE.md/AGENTS.md `make verify` line; README, architecture and history entries
- [x] Full offline suite on the final tree (12,060 passed, 37 skipped, 1 known wall-clock flake `TestPerfSmoke::test_tiny_window_completes_quickly`, passes alone)
- [x] Commit; the operator chose a stacked draft PR on #1117 (base `claude/ecstatic-maxwell-f6ruct`)
- [ ] CI green on the stacked PR; retarget to `main` once #1117 merges
- [ ] 5.4b-ii (after the review and the operator's cutover): see "Deferred" in the walkthrough
