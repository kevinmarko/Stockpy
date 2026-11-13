# Plan: the agentic review launcher hides a failed run

Branch: `fix-agentic-launch-silent-failure` (from `origin/main` 9caaa779) · PR artifacts:
`.claude/agentic_review_launch_silent_failure_{implementation_plan,task,walkthrough}.md`

## Context
On 2026-11-13 the 16:40 `com.investyo.rh-agentic-review` timer fired and exited 0, but no review was
recorded. The headless session's transcript says
`Failed to authenticate: OAuth session expired and could not be refreshed`.
- `claude -p` exits **1** on that error.
- `execution/agentic_review.py::launch()` treats exit codes 0 and 1 as normal, reading 1 as "NOT READY".
  That is wrong: those are `claude`'s exit codes, not the review's. `claude -p` returns 0 when it ran and
  1 when it errored, whatever the verdict.
- So no "review did not run" alert was sent.
- `capture_output=True` swallowed the error text too, so `output/agentic_review.out` and `.err` are empty.

The burn-in only counts days with a recorded review, so a silent failure stalls READY with nothing
telling the operator.

The cycle launcher (`execution/agentic_cycle.py::launch`) is already safe:
- It decides from `cycle.json`, not from the exit code.
- An auth failure leaves no `cycle.json` plus a non-zero exit, which already calls `fail_cycle(...)`, and
  that pushes an alert.
- Its only gap is the same diagnosis problem: the alert says "exited with code 1" but not why.

## Changes

### 1. `execution/agentic_review.py::launch()`: decide from the artifact, not the exit code
- Note `started = now()` and today's UTC day (the day `run` uses by default) before running `claude`.
- After the run, success means `review_dir(day)/review.json` exists, was written at or after
  `started`, and has `usable: true`.
  - Check the start day and the end day, in case the run crosses UTC midnight.
  - Reuse `review_dir`, `_read_json` and `REVIEW_FILENAME`.
- On success: return `EXIT_READY` or `EXIT_NOT_READY` from that file's `burnin.ready`, matching what
  `status` returns.
- Otherwise: one high-priority push "Agentic burn-in review did not run", return `EXIT_UNUSABLE`.
  - Body: the reason (timeout, CLI not found, exit code), then "No review was recorded today.", then the
    last line of `claude`'s output, trimmed to 300 characters.
  - For today's failure that last line is "Failed to authenticate: OAuth session expired…".
- Always print one JSON line to stdout: `{"review_recorded", "claude_exit", "reason", "output_tail"}`.
  That way the launchd `.out` log shows what happened. The tail is the last 500 characters of
  stdout + stderr.
- Disclosed edge: if the agent's own `run --notify` already pushed "review could not run" (inputs
  unusable, so no file is written), the operator gets two pushes that day. That is rare and
  preferable to silence.

### 2. `execution/agentic_cycle.py::launch()`: diagnosis only
- When `reason` is set from a non-zero exit, a timeout or an exception, append the same trimmed output
  tail, so the existing `fail_cycle` alert says why.
- Factor out one small helper, `_output_tail(proc) -> str`, in `agentic_cycle.py`. `agentic_review`
  already imports from it.
- The control flow does not change.

### 3. Tests
`tests/test_agentic_review.py::TestLaunch`:
- **Rewrite the existing test** "exit 1 is a normal NOT READY day". It now needs the fake runner to write
  a usable `review.json`. Exit 1 with no file must alert, which is today's bug.
- **No file written, any exit code** → one alert, `EXIT_UNUSABLE`, and the body includes the output tail
  (auth-error text).
- **Usable file written during the run** → no alert; the exit code follows `burnin.ready` (0 or 1).
- **Stale file from before `started`, or `usable: false`** → alert.
- **Timeout and CLI-not-found** stay covered.
- **stdout JSON line** is printed in every case.

`tests/test_agentic_cycle.py::TestLaunch`:
- An auth-error runner (rc 1, stderr text, no `cycle.json`) → `fail_cycle` alert whose body contains
  the error text.

### 4. Docs
- `docs/RUNBOOK.md`, Phase 5 burn-in, "A review that didn't run" paragraph:
  - The alert now fires on any run that records no review.
  - The commonest cause is an expired Claude Code login. The fix is to run `claude`, then `/login`, then
    `python -m execution.agentic_review launch`.
  - The launchd `.out` now holds the JSON summary.
- `docs/architecture/execution.md`, `agentic_review` entry: success means a usable `review.json`
  written during the run.
- `docs/known_issues/agentic_review_launch_silent_auth_failure_2026_11.md`: new, plus an index row in
  `docs/known_issues/README.md`.
- No settings, so no census regen. No `CLAUDE.md` change.

## Out of scope
- Re-authenticating Claude Code. The operator has to do that: run `claude`, then `/login`.
- A login-expiry pre-check or health probe.

## Verification
- `pytest tests/test_agentic_review.py tests/test_agentic_cycle.py tests/test_agentic_timer_installer.py`
- `make local-ci`, with its table in the PR.
- **Live, after the operator logs in again:**
  - `.venv/bin/python -m execution.agentic_review launch` should print `review_recorded: true`, and
    `status` should show today's day.
  - Before the login, the same command should push the alert containing the auth error. That doubles as
    a live repro of the bug.
