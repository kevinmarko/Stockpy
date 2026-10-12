# Step 5.4b-ii walkthrough: cutover-dependent callers

Plan: `.claude/shrink_step5_retire_main_py_implementation_plan.md` §4 (5.4), scope as listed in
`.claude/shrink_step5_4b_i_verify_helper_walkthrough.md` ("5.4b-ii"). Branch `step5-4b-ii-cutover-dependent`.

## Why rebuilt from scratch
The 5.4b-i walkthrough pointed at a finished draft on local branch `step5-4b-ii-cutover-dependent-full`
(commit `08f0630`). Neither the branch nor the commit exists anywhere in this repository's refs or
unreachable objects any more, so this PR was written fresh against `main` at `1c827a84`. That also
removed the need to "undo" the draft's plist deletion: the operator's 2026-10-12 decision (keep
`scripts/com.investyo.daily-advisory.plist` for rollback) is built in from the start.

## What changed
- **`launch.command` deleted.** It ran `python main.py --interval 60` in a Terminal window. The daemon is
  the one runtime; the headless replacement is the `com.investyo.stack` service or
  `python -m desktop.orchestrator_daemon`.
- **`scripts/install_schedule.command`** no longer installs `com.investyo.daily-advisory` by default
  (the open question from 5.4b-i). Running it as before would have reloaded the job #1120 unloaded and
  made `main.py` a second queue writer. The job is installed only with `--with-daily-advisory`, and that
  flag refuses (exit 1) unless `settings.DAEMON_AGENTIC_QUEUE_MODE` is `off` or `shadow` (operator
  decision 2026-10-12). The mode is read through `settings` with `NO_VENV_REEXEC=1`, so the runtime-flags
  store (where the live `primary` lives) wins over `.env`; an unreadable setting fails closed. Unknown
  arguments exit 2. The plist itself is unchanged.
- **`robinhood-execution` skill / `/rh-execute`:** in `off`/`shadow` the daemon is not the writer, so the
  skill stops and asks whether this is a RUNBOOK §3.15 rollback. It never triggers `POST /run` there
  (that doesn't write the real queue) and never tells the operator to run `python3 main.py`. In a
  confirmed rollback it may use the queue the 08:45 job last wrote, under every hard stop.
- **RUNBOOK:** §0 launcher text replaced; §2 pre-market "Start pipeline" and "Account snapshot fresh"
  rows point at `POST /run` and the daemon's 08:40 login; §3.15 rollback names the installer flag and
  notes `/rh-execute` doesn't refresh in a rollback; §5.1 rewritten as "remaining scheduled jobs",
  daily-advisory rollback-only; §5.1a reworded from future to past tense.
- **README, HOW_TO_GUIDE:** launcher and `main.py` run instructions point at the daemon; the
  `main.py` section is marked "being retired". Remaining descriptive `main.py` mentions (universe
  builder, `--agent`, report paths) are left for 5.5, which archives the file.
- **`.env.example`:** comments only, no key or default changed (`DAEMON_AGENTIC_QUEUE_MODE` still
  defaults to `off`; the comment says the live deployment runs `primary`).
- **CLAUDE.md/AGENTS.md:** status line (5.4 done, 5.5/5.6 pending, installer flag) and the Commands
  block (`./launch.command` removed). `main.py` comment that named `launch.command` updated.
- Records: `docs/FEATURE_TIER_HISTORY.md` entry, status note in the step 5 plan.

## Verification
- `scripts/install_schedule.command`: `bash -n` clean. The rollback guard was exercised in isolation
  (the `case` block only, never the `launchctl` loop, so nothing was loaded on this Mac) for `off`,
  `shadow` (install), `primary`, empty, `bogus` (exit 1 with the reason). The live machine reads
  `primary` through `settings`, so the flag refuses today.
- `plutil -lint scripts/*.plist`: all four OK.
- `pytest tests/test_agent_docs_sync.py tests/test_help_content.py tests/test_measure_settings_census.py tests/test_settings_liveness.py`: 101 passed.
- `tests/test_investyo_mcp_server.py tests/test_orchestrator_daemon.py tests/test_stocktwits_source.py tests/test_validation_lab_panel.py` (the other files mentioning these docs/scripts) with the help/sync tests: 480 passed.
- No anchors pointed at the renamed HOW_TO_GUIDE/RUNBOOK headings (searched docs, help content, skills, tests).
- `make ci`: see the PR description for the result of the run on this tree.

## Not verified / follow-ups
- The installer was not run end to end (it loads real launchd jobs on this Mac).
- About a dozen other `.env.example` comments still describe tabs of the deleted Streamlit GUI
  (lines ~179–912); not part of retiring `main.py`.
- RUNBOOK §3.1 still offers `python3 main.py --refresh-account` (it still works until 5.5).
- 5.5 must move or retire `scripts/com.investyo.daily-advisory.plist` and the `--with-daily-advisory`
  path along with `main.py`.
