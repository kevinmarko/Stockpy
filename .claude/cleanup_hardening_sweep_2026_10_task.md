# Cleanup and hardening sweep (2026-10-29) task tracker

Walkthrough: `.claude/cleanup_hardening_sweep_2026_10_walkthrough.md`. PR: kevinmarko/Stockpy#1133.
Plans written in this sweep (review before any code): `.claude/synthetic_data_gate_keyless_implementation_plan.md`,
`.claude/forecasting_rebuild_f5_f6_implementation_plan.md`.

- [x] Confirm what "this" meant: shrink step 5 was already finished on `main` (#1124, #1125), so the scope is the cleanup sweep plus plans for F5/F6 (operator's choice)
- [x] Restart the designated branch from `origin/main`; force-with-lease over the closed #1122's head (`c700688e`, kept at `refs/pull/1122/head` and in the local ref `backup-closed-pr-1122`)
- [x] Six agents on disjoint files: throttle flake, runtime comments, docs/skills/plists, PR review (read-only), keyless synthetic-gate plan, forecasting F5/F6 plan
- [x] Read every agent diff against `main`; verify new factual claims in the code (preflight count 27 / auto-skip 6, risk gate "eleven-check", the daemon's engine fallback)
- [x] Prove the 47 comment-only files change no code (AST with docstrings stripped identical to `HEAD`, line counts unchanged) - done by the agent and again independently
- [x] GitHub Actions retired (operator): `scripts/local_ci.py` + `make local-ci` + `tests/test_local_ci.py` (38 tests, 12/12 mutations caught) + CLAUDE.md/AGENTS.md + architecture docs
- [x] Regenerate the settings census/liveness with the repo scripts (one more scanned file); both freshness tests pass
- [x] First real run of the gate found three pre-existing problems; each root-caused, fixed and mutation-checked (see the walkthrough): LightGBM OpenMP oversubscription under xdist, a urllib opener leak from `fredapi`, a wall-clock perf guard
- [x] Full local gate on the pushed commit `652e9730`: offline 11,799 passed / 0 failed; coverage 77% (floor 74%); slow, lint, webapp x3, bandit, ML audit, ML lookahead all PASS
- [ ] `pip-audit` is RED (59 advisories: pillow 39, filelock 17, certifi 2, setuptools 1) on pins in `requirements.txt` that predate this PR - separate dependency PR, operator's call
- [ ] Operator answers the gate plan's Q1-Q5 and the F5/F6 plan's Q1-Q10
- [ ] Operator decides whether to delete `.github/workflows/` (they still trigger on GitHub) and whether branch protection still requires those checks
- [ ] Operator decides on PR #1131 (merge after one small hardening fix) and #1132 (safe to merge)

## Found, deliberately not fixed here
- User-visible strings that still name archived things: `pipeline/production_steps.py` log text (~2879, ~3258, ~3337), `settings.py` descriptions (~1883, ~2235, ~2635, the last mirrored in `webapp/src/api/mock.ts`), `shared/help_content.py:790`
- 11 references to the deleted `app_shell.py` in `desktop/orchestrator_daemon.py` (the file no longer exists)
- Dead code left by step 5 (verified: no production importer/caller; only docstrings or the definition mention them): `shared/engine_status.py`, `pipeline/agentic_queue.py::daemon_owns_agentic_side_effects`, `pipeline/advisory_inputs.py::build_universe`, the unused `*_fn` fields on `pipeline/context.py::RunContext`
- `.env.example` still carries a dead `NTFY_TOPIC=` key (no such setting; the real one is `ALERT_NTFY_TOPIC`); left alone because keys and values are not editable in a comment-only change
