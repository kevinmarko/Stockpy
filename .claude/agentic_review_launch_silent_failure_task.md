# Task: agentic review launcher hid a failed run

- [x] `agentic_review.launch()`: success = usable review.json written during the launch; alert with claude's message otherwise; JSON summary line
- [x] `agentic_cycle`: `_output_tail` / `_last_line` helpers; failure reason carries claude's message
- [x] Tests: review launch (expired login, clean exit no file, stale file, unusable file, ready/not-ready, timeout, missing CLI, notifier failure, CLI output-dir); cycle auth-failure alert + helpers
- [x] Docs: RUNBOOK, execution.md, known issue + index
- [x] `make local-ci`: all PASS except pip-audit (pre-existing dependency advisories), table in the PR
- [x] Operator logged the CLI in again (2026-11-13); live launch re-run
