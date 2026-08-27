# Implementation plan — CodeQL alert #91 (`py/command-line-injection`)

## Source

GitHub code-scanning alert
[#91](https://github.com/kevinmarko/Stockpy/security/code-scanning/91)
(`py/command-line-injection`, "Uncontrolled command line", CWE-78/CWE-88,
critical severity per CodeQL's `security_severity_level`), opened
2026-08-27 against `main` commit `2deb0365` ("Global job-status visibility
in the Pilots PWA", PR #917).

- **Flagged sink**: `gui/orchestrator_runner.py:954` —
  `subprocess.Popen(cmd, ...)` inside `launch_train_meta_labelers`.
- **Traced source**: `api/control_api.py:598` — the `body: JobCreateRequest`
  parameter of `POST /jobs` (`create_job`). `api/_jobs.py:254` forwards
  `params.get("signal")` from that request body straight into
  `launch_train_meta_labelers(signal=...)`.

## Investigation

1. Read the alert via `gh api repos/kevinmarko/Stockpy/code-scanning/alerts/91`.
2. Read `gui/orchestrator_runner.py::launch_train_meta_labelers` end to end.
   Confirmed `signal` is already checked against the hardcoded, exact-match
   `ml.meta_bootstrap.META_LABELED_SIGNAL_IDS` tuple
   (`("timeseries_momentum", "cross_sectional_momentum")`) — anything else
   raises `ValueError` *before* `cmd` is built or `Popen` is called.
   `Popen` is called with a list, no `shell=True`.
3. Found this exact rule was already triaged once before, for a sibling
   function in the same file (`launch_validation_run`, alert #11,
   documented in `docs/known_issues/2026_08_security_quality_review.md`).
   That fix's pattern: keep the existing allowlist validation, add an
   explanatory comment plus a `# codeql[py/command-line-injection]`
   suppression annotation directly on the `Popen` call (CodeQL does not
   model a hand-written `if x not in ALLOWLIST: raise` as a taint
   sanitizer), and add adversarial-input regression tests.
4. Conclusion: alert #91 is the same class of reviewed false positive as
   alert #11 — already functionally mitigated, just undocumented/
   unsuppressed and untested against adversarial input.

## Plan

1. **Code**: apply the alert-#11 treatment to `launch_train_meta_labelers`
   — explanatory comment + `# codeql[py/command-line-injection]`
   suppression on the `Popen(cmd, ...)` call.
2. **Tests**: add `TestLaunchTrainMetaLabelersInputValidation` to
   `tests/test_security_audit_fixes.py`, mirroring
   `TestLaunchValidationInputValidation` — shell metacharacters, an
   injected CLI flag, a leading-dash flag-injection attempt, path
   traversal, and a case-mismatched near-miss must all raise `ValueError`;
   a real allowlist member must still launch (mocked `Popen`) correctly;
   the no-`signal` path must omit `--signal` entirely.
3. **Docs**: append a new dated section (`## 6.`) to
   `docs/known_issues/2026_08_security_quality_review.md` documenting the
   alert, the trace, the reasoning, and the fix — following that doc's
   existing per-finding format so a future scan doesn't re-litigate this.
4. **Verification**: `ruff check --select=F821,F822,F823,E9` on the two
   changed Python files; `pytest tests/test_security_audit_fixes.py
   tests/test_orchestrator_runner.py tests/test_control_api.py`.
5. **PR**: since this touches `gui/orchestrator_runner.py` (imported by
   `api/_jobs.py`, i.e. live webapp backend infrastructure, not the frozen
   Streamlit-only surface) it is "Everything else" tier per CLAUDE.md's
   Branch Workflow — branch + PR, no direct commit to `main`. Already on a
   dedicated branch (`claude/uncontrolled-command-line-security-518cd2`).

## Documentation-update step (required by CLAUDE.md's plan checklist)

- `docs/known_issues/2026_08_security_quality_review.md` — new `## 6.`
  section (done as part of this change, not a follow-up).
- No other `docs/` file references this alert, this rule, or
  `launch_train_meta_labelers`'s security posture, so no further doc
  updates are in scope.
