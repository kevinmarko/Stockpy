# Task tracker — CodeQL alert #91 (`py/command-line-injection`)

- [x] Fetch and read alert #91 (`gh api repos/kevinmarko/Stockpy/code-scanning/alerts/91`).
- [x] Trace source (`api/control_api.py:598`, `POST /jobs`) → sink
      (`gui/orchestrator_runner.py:954`, `launch_train_meta_labelers`'s
      `Popen` call) via `api/_jobs.py:254`.
- [x] Confirm existing mitigation: exact-match allowlist
      (`ml.meta_bootstrap.META_LABELED_SIGNAL_IDS`) checked before `signal`
      reaches `cmd`; `Popen` called with a list, no `shell=True`.
- [x] Find precedent: alert #11, same rule, same file
      (`launch_validation_run`), documented in
      `docs/known_issues/2026_08_security_quality_review.md`.
- [x] Apply the same treatment to `launch_train_meta_labelers`: explanatory
      comment + `# codeql[py/command-line-injection]` suppression on the
      `Popen` call.
- [x] Add `TestLaunchTrainMetaLabelersInputValidation` regression tests to
      `tests/test_security_audit_fixes.py` (10 adversarial `signal` values →
      `ValueError`; valid allowlist member → launches; no `signal` → flag
      omitted).
- [x] Document alert #91 in `docs/known_issues/2026_08_security_quality_review.md`
      (new `## 6.` section).
- [x] Verify: `ruff check --select=F821,F822,F823,E9` on changed files —
      clean.
- [x] Verify: `pytest tests/test_security_audit_fixes.py
      tests/test_orchestrator_runner.py tests/test_control_api.py` — 177
      passed.
- [x] Copy plan/task/walkthrough artifacts into `.claude/` with a
      branch-scoped, unique filename prefix (`uncontrolled_command_line_alert91_*`).
- [x] Push branch, open PR (https://github.com/kevinmarko/Stockpy/pull/920).
- [x] CI: `test (offline suite)` failed
      (`tests/test_settings_liveness.py::TestCommittedArtifactIsFresh::
      test_committed_json_matches_a_fresh_run`) -- the alert-#91 comment
      block shifted `gui/orchestrator_runner.py` line numbers, staling one
      recorded site in the committed `docs/settings_liveness.json`.
      Regenerated via `python3 scripts/settings_liveness.py --write` and
      pushed. (Branch had also been fast-forwarded with a `main` merge
      commit in between by the repo owner's git client; second regen run
      on top of that merged state, same single-line fix.)
- [ ] After merge: sync local `main` checkout per CLAUDE.md's post-merge step.
