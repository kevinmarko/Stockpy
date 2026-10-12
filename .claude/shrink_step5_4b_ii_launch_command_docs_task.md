# Step 5.4b-ii task tracker: delete `launch.command` and move the remaining docs, skills and installer text to the daemon

Plan: `.claude/shrink_step5_4_callers_implementation_plan.md` (PR 5.4b, re-split 2026-10-12). Walkthrough:
`.claude/shrink_step5_4b_ii_launch_command_docs_walkthrough.md`. Follows 5.4a (#1117) and 5.4b-i (#1121), both merged.

- [x] Port the parked draft (`step5-4b-ii-cutover-dependent-full`, commit `08f0630`) onto current `main` with a 3-way cherry-pick (no commit), so hunks that 5.4b-i already landed merged as no-ops; nine files that collided with 5.4b-i / #1120 text were reset to `main` and re-applied by hand
- [x] Undo the two things the draft did before the operator's decision: it deleted `scripts/com.investyo.daily-advisory.plist` (now KEPT, byte-identical to `main`) and it rewrote the RUNBOOK rollback text (now verbatim)
- [x] Delete `launch.command`
- [x] `robinhood-execution` skill (both copies) and `/rh-execute`: refresh the queue only through a daemon cycle in `primary`; in `off`/`shadow` stop, name the mode, do not run a cycle or `main.py`, do not load a queue file (fail closed, no escape hatch); pinned in `tests/test_robinhood_e2e.py`
- [x] RUNBOOK §0, §0.1, §1.1, §2, §3.1, §3.14, §3.15 (status paragraph, cutover record), §5.1, §6: the daemon path; rollback text unchanged plus how to reload the job and what a rollback does to skill-driven execution
- [x] `scripts/install_schedule.command` keeps installing the daily-advisory job (no behaviour change) and prints one warning when it reaches that label; `install_stack_service.command` keeps its unload block; installer / plist / CI comments corrected
- [x] Architecture docs: `main.py` row, `orchestration-entrypoints.md` (`.env` loading, `alerting.py` daemon equivalent, `main.py` bullet), `execution.md`, `observability-and-apis.md`, `simulation-eval-reporting.md`
- [x] Two known-issue notes reworded ("unloaded since the cutover, plist kept", not "retired")
- [x] `CLAUDE.md` / `AGENTS.md`: steps 5.0-5.4 done, `./launch.command` command removed, feature-freeze allowance now 5.5-5.6
- [x] `FEATURE_TIER_HISTORY.md` entry extended (one line, not a second entry)
- [x] `HOW_TO_GUIDE.md`, `README.md`, `.env.example` (comments only: every `KEY=value` line, active or commented-out, is identical to `main`)
- [x] Census / liveness freshness gates and the doc-reading tests (574 passed, 1 skipped: no `zsh` in this container); every agent diff read against `main` and each new factual claim checked in the code
- [ ] Squash the local checkpoints into one commit, push, open the draft PR, subscribe to its activity
- [ ] CI green; the operator decides the open points below before merge

## Open for the operator (not decided here)

- `scripts/install_schedule.command` still installs the daily-advisory job by default, so re-running it reloads a job the cutover unloaded. This PR only adds a warning.
- During a rollback (`off`/`shadow`) the execution skill and `/rh-execute` stop. If the operator wants them to work with `main.py` as the writer, that needs an explicit, narrower allowance.
- The kept plist runs `main.py`, so step 5.5 (archive `main.py`) has to move or retire it as well.
