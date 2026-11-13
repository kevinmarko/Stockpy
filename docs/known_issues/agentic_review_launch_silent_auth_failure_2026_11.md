# Agentic review launcher hid a failed run (expired Claude Code login, 2026-11)

**Status:** Fixed in code (2026-11-13). The operator still has to log Claude Code in again
(`claude`, then `/login`) for the headless timers to work.

## Symptom
The first burn-in day's 16:40 `com.investyo.rh-agentic-review` run exited 0 and wrote nothing:
- `output/agentic_review.out` and `.err` were empty.
- There was no `OUTPUT_DIR/agentic/review/2026-11-13/`.
- `python -m execution.agentic_review status` said `no usable review yet`.
- No alert was sent.

The headless session's transcript (under `~/.claude/projects/`) held one line:
`Failed to authenticate: OAuth session expired and could not be refreshed`.

## Root cause
`execution/agentic_review.py::launch()` treated `claude -p` exit codes 0 and 1 as normal, reading 1 as
"NOT READY". But these are `claude`'s exit codes, not the review's: `claude -p` exits 0 when the
session ran and 1 when it errored, whatever the verdict. The expired login exited 1. The launcher
returned 0 and skipped its "did not run" alert, and `capture_output=True` swallowed the error text.

The cycle launcher (`agentic_cycle.launch`) was already safe. It decides from `cycle.json`, so the same
failure gives `fail_cycle` and an alert. That alert only said "exited with code 1", though.

## Fix
- **`agentic_review.launch()` decides from the artifact.**
  - Success means a usable `review.json` for today (UTC, the start or end day of the run) written at or
    after the launch started.
  - On success it returns that file's verdict.
  - Otherwise it pushes "Agentic burn-in review did not run", with the reason and `claude`'s last output
    line, and returns 2.
  - Every launch prints one JSON summary line (`review_recorded`, `claude_exit`, `reason`,
    `output_tail`) to the launchd `.out` log.
- **`agentic_cycle.launch()` appends `claude`'s last output line to its failure reason**, using the
  shared `_output_tail` and `_last_line` helpers.

## Known edge
If the Reviewer's own `run --notify` already pushed "review could not run" (unusable inputs, so no file
is written), that day sends two pushes. That is rare, and preferable to silence.

## Not fixed here
There is no check ahead of time for an expiring login, so the first sign is still a failed run's alert.
