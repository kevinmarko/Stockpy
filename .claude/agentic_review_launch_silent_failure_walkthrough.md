# Walkthrough: agentic review launcher hid a failed run

## What happened (2026-11-13)
The 16:40 Reviewer timer ran `claude -p /rh-agentic-review`, which failed on an expired Claude Code
login and exited 1. `launch()` read exit 1 as a normal "NOT READY" day, so there was no alert, no review,
and empty logs. Details: `docs/known_issues/agentic_review_launch_silent_auth_failure_2026_11.md`.

## Change
- **`execution/agentic_review.py`.**
  - `launch()` now checks `_recorded_review(days, started)`: a usable `review.json` for today (UTC) whose
    mtime is at or after the launch start (1 s slack).
  - Exit codes: 0 or 1 from that file's `burnin.ready`, otherwise 2 plus one high-priority alert.
    The alert says the reason, "No review was recorded today.", and "claude said: <last line>".
  - It always prints `{"review_recorded", "claude_exit", "reason", "output_tail"}`.
  - New keyword arguments, `output_dir` (the CLI passes `--output-dir`) and `clock`, exist for tests.
- **`execution/agentic_cycle.py`.**
  - `_output_tail(proc)` returns the last 500 characters of stdout + stderr, and also works on
    `TimeoutExpired`.
  - `_last_line(text)` returns the last non-empty line, up to 300 characters.
  - A non-zero exit or a timeout now puts `claude`'s last line into the `fail_cycle` reason, and so into
    the existing alert. The control flow is unchanged.

## Tests
`tests/test_agentic_review.py::TestLaunch`:
- **Rewritten:** the old test treated exit 1 as normal, which was the bug.
- **New cases:**
  - expired login (exit 1, no file) alerts with the message
  - clean exit with no file alerts
  - a stale file doesn't count
  - an unusable file doesn't count
  - a file written and then a late error counts
  - READY and NOT READY verdicts
  - timeout and missing CLI
  - a notifier exception never raises
  - the CLI passes `--output-dir`

`tests/test_agentic_cycle.py`:
- the auth-failure alert carries the message
- the helper edge cases

## Not verified
The live run needs the operator to log in again first. Before that, a manual `launch` reproduces the
alert; after it, `launch` should print `review_recorded: true`.
