# Walkthrough — CodeQL alert #91 (`py/command-line-injection`)

## What the alert said

GitHub code scanning (CodeQL, `python`, rule `py/command-line-injection`,
"Uncontrolled command line", CWE-78/CWE-88, `security_severity_level:
critical`) opened
[alert #91](https://github.com/kevinmarko/Stockpy/security/code-scanning/91)
against `gui/orchestrator_runner.py:954`:

> This command line depends on a
> [user-provided value](https://github.com/kevinmarko/Stockpy/blob/2deb0365/api/control_api.py#L598C16-L598C20).

The flagged call is `subprocess.Popen(cmd, ...)` inside
`launch_train_meta_labelers`. The dataflow CodeQL traced: `POST /jobs`'s
request body (`JobCreateRequest.params`, `api/control_api.py:598`) →
`job_manager.start_job(jtype, body.params)` → `api/_jobs.py:254`'s
`launch_train_meta_labelers(signal=params.get("signal"))` → the `cmd` list
passed to `Popen`.

## What's actually there

`launch_train_meta_labelers` already guards `signal` with an exact-match
allowlist check before it can reach `cmd`:

```python
cmd: List[str] = [sys.executable, "-m", "scripts.train_meta_labelers"]
if signal:
    if signal not in META_LABELED_SIGNAL_IDS:
        raise ValueError(
            f"Invalid signal identifier: {signal!r} (expected one of {META_LABELED_SIGNAL_IDS})"
        )
    cmd.extend(["--signal", signal])
```

`META_LABELED_SIGNAL_IDS` (`ml/meta_bootstrap.py`) is a hardcoded 2-item
tuple — `("timeseries_momentum", "cross_sectional_momentum")` — not derived
from any request, config file, or env var. Any string that isn't a literal,
case-sensitive match for one of those two raises `ValueError` immediately,
and `cmd` is never extended, so `Popen` never sees it. `Popen` is also
called with a list (not a shell string) and no `shell=True`, so there's no
shell to reinterpret metacharacters even hypothetically.

This is the exact same rule flagging the exact same file for the exact same
reason as a previously-triaged alert, **#11**, on a sibling function
(`launch_validation_run`) — documented in
`docs/known_issues/2026_08_security_quality_review.md`. CodeQL's
`py/command-line-injection` query doesn't model a hand-written
`if x not in <tuple>: raise` guard as a taint sanitizer, so it keeps
flagging fully-validated input as if it were unvalidated.

## What changed

1. **`gui/orchestrator_runner.py`** — added the same explanatory-comment +
   `# codeql[py/command-line-injection]` suppression pattern already used
   for alert #11, directly above/on the `Popen` call in
   `launch_train_meta_labelers`. No behavior change — the allowlist check
   was already there and already correct; this only makes the reasoning
   explicit for CodeQL and for the next reviewer.
2. **`tests/test_security_audit_fixes.py`** — added
   `TestLaunchTrainMetaLabelersInputValidation`, mirroring the existing
   `TestLaunchValidationInputValidation` class:
   - 10 adversarial `signal` values (`; rm -rf /`, `&&`, backticks,
     `$(...)`, a pipe, an injected `--flag`, a bare `-x` flag, path
     traversal, a plausible-but-wrong signal name, and a case-mismatched
     near-miss of a real one) — every one must raise `ValueError` matching
     `"Invalid signal identifier"`, with `Popen` never called.
   - A real allowlist member (`"timeseries_momentum"`) must still launch
     successfully against a mocked `Popen`, with `--signal
     timeseries_momentum` in the resulting argv.
   - Omitting `signal` entirely must omit `--signal` from argv (unchanged
     existing behavior, now covered under this class too).
3. **`docs/known_issues/2026_08_security_quality_review.md`** — new `## 6.`
   section documenting the alert, the traced dataflow, why it's a reviewed
   false positive, and the fix — so a future CodeQL sweep (or a human
   re-reading the alert) doesn't have to re-derive this from scratch.

## Verification

```
ruff check gui/orchestrator_runner.py tests/test_security_audit_fixes.py --select=F821,F822,F823,E9
# All checks passed!

pytest tests/test_security_audit_fixes.py tests/test_orchestrator_runner.py tests/test_control_api.py -q
# 177 passed
```

## Next step for the operator

Once this PR merges, alert #91 can be dismissed in the GitHub Security tab
(Code scanning alerts) as "Used in tests" / reviewed-and-mitigated —
consistent with how alerts #21/#22 were handled in the prior security
review. This PR does not (and cannot) dismiss the GitHub-side alert itself;
that's a manual step in the GitHub UI.
