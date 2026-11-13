# Robinhood agentic timers: one-command installer (plan)

Scope (operator OK, 2026-11-13, after the offer to automate the timer setup): ops
tooling only. No change to what the cycle, the Reviewer or any agent does.

1. `scripts/agentic_timers.py`: testable logic. Render each agentic plist with
   `plistlib` (repo path, home in `PATH`, `claude` folder first), find `claude`,
   report what would make cycles skip, and an opt-in `--burnin` that sets review
   mode + a cap via `runtime_flags_writer` after a typed `REVIEW`. Never `live`.
2. `scripts/install_agentic_timers.command`: launchctl wrapper (install,
   `--burnin [--cap]`, `--check`, `--uninstall`, `--claude`), with a dry-run mode.
3. Keep `install_schedule.command` unchanged (it must never start the agents).
4. Tests: `tests/test_agentic_timer_installer.py`.
5. Docs: RUNBOOK (cycle timer, burn-in step 1), HOW_TO_GUIDE, architecture
   (`webapp-and-gui.md`, `execution.md`, `testing.md`), `CLAUDE.md`/`AGENTS.md`,
   plist header comments, census/liveness regeneration, walkthrough.
