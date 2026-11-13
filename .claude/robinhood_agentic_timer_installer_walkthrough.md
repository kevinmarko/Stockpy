# Robinhood agentic timers: one-command installer (walkthrough)

Follow-up to Phases 4–5 of `.claude/robinhood_four_agent_trading_implementation_plan.md`.
The operator was offered "make the timer setup automatic, so it fills in your repo
path and installs both timers with one command" (2026-11-13) and said to continue.
Plan: `.claude/robinhood_agentic_timer_installer_implementation_plan.md`.

## Why

Starting the burn-in took three manual steps on the Mac: set the mode and cap,
`sed` each plist into `~/Library/LaunchAgents`, `launchctl load` each one. Building
the installer turned up a real bug in those steps: the `sed` rewrote only the repo
path. Both templates' `PATH` also hard-codes `/Users/kevinlee/.local/bin` and
`/Users/kevinlee/.claude/local`, and both launchers start `claude` from `PATH`
(`claude_bin="claude"`). On a Mac where `claude` lives anywhere else (npm global,
Homebrew under another prefix, another user name), every cycle that passed the gate
would have failed with a "cycle stopped" push, and every review with "did not run".

## What changed

| File | Change |
|---|---|
| `scripts/agentic_timers.py` (new) | `render_plist` (structural `plistlib` rewrite: repo placeholder, home placeholder in `PATH`, found `claude` folder first; refuses leftovers or a foreign python), `find_claude`, `assess`/`readiness`, `start_burnin` (typed `REVIEW`, cap then review, never `live`, refuses a live setup), CLI `find-claude/render/check/burnin`. |
| `scripts/install_agentic_timers.command` (new) | Thin wrapper: venv check, claude, optional `--burnin [--cap N]`, render + `plutil -lint` + unload/load per job, readiness summary. `--check`, `--uninstall`, `--claude PATH`. `AGENTIC_TIMERS_DRY_RUN=1` for tests. |
| `scripts/com.investyo.rh-agentic-{cycle,review}.plist` | Header comments point at the installer (no `--` in the comment: XML forbids it). Job definitions unchanged. |
| `tests/test_agentic_timer_installer.py` (new) | 52 tests. |
| Docs | RUNBOOK (cycle timer + burn-in step 1, hand steps kept as fallback), HOW_TO_GUIDE (burn-in), architecture `webapp-and-gui.md`, `execution.md`, `testing.md`, `CLAUDE.md`/`AGENTS.md`; settings census and liveness regenerated (the installer reads four settings). |

`install_schedule.command` is untouched: it still never installs the trading
agents (pinned by the Phase 4/5 tests and a new one).

## Safety

- `--burnin` is the only path that changes settings. It asks for the exact word
  `REVIEW` (EOF or anything else → nothing changes), writes through
  `runtime_flags_writer.write_override` (the sanctioned store, never `.env`), and
  writes the cap before the mode so review never starts uncapped. It never writes
  `live` and refuses outright when the mode already is `live`.
- `ROBINHOOD_EXECUTION_MODE` is a `DANGEROUS_KEYS` member; the typed confirmation
  is that rule's equivalent here.
- No `claude` → nothing is installed (the timers would only fail).

## Verification

- `pytest tests/test_agentic_timer_installer.py` 52 passed, plus the agentic cycle,
  review, runtime-flags, census, liveness, timeout-guard, pipeline-smoke and docs
  suites (counts in the PR).
- Dry run here: both plists rendered for this container's paths, no `kevinlee`
  left, `/opt/node22/bin` (where `claude` is) first on `PATH`, schedules unchanged.
- Not verified here: `launchctl load` and `plutil -lint` (macOS only; the wrapper
  runs `plutil -lint` before loading and stops on failure).
