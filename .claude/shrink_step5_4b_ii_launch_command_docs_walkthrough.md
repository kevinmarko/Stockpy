# Step 5.4b-ii walkthrough: delete `launch.command` and move the remaining docs, skills and installer text to the daemon

Plan: `.claude/shrink_step5_4_callers_implementation_plan.md` (PR 5.4b). Tracker:
`.claude/shrink_step5_4b_ii_launch_command_docs_task.md`. Follows 5.4a (#1117) and 5.4b-i (#1121), both merged.

**Documentation, skills, shell text, comments and one deleted file. No Python runtime code changed** (the only `.py` file touched is
`tests/test_robinhood_e2e.py`). Nothing here changes the live `.env`, `runtime_flags.json` or `~/.stockpy_local`, and no setting was
added, removed or renamed.

## What this PR is, and the decision it respects

By 5.4a the runtime callers (Console job, dead-letter retry, MCP report) already trigger a daemon cycle, and 5.4b-i moved
`make verify` off `main.py`. What was left was the *human-facing* half: a double-click launcher, the operator skills, the RUNBOOK
and guides, and installer text, all still telling people to run `main.py` to get the live queue.

**Operator decision (2026-10-12): keep `scripts/com.investyo.daily-advisory.plist` for rollback.** The earlier draft of this work
deleted the plist and rewrote the RUNBOOK rollback line to "run `main.py` by hand". Both were undone. The plist is byte-identical to
`main`, and the rollback line is verbatim ("set the mode to `off`/`shadow` and reload `com.investyo.daily-advisory`").

## How it was built (and why that way)

The earlier draft existed on a parked branch but predated both #1120 (the cutover record) and the keep-the-plist decision. Rather than
copy files over, it was ported with `git cherry-pick --no-commit` of the draft commit onto current `main`. A 3-way merge uses the
draft's own parent as the base, so every hunk that 5.4b-i had already landed merged as a no-op, and only real divergences surfaced as
conflicts. Nine files collided (they carry 5.4b-i's or #1120's wording on the same long lines). Those were reset to `main`'s text and
the still-correct intent re-applied by hand, instead of hand-merging conflict markers inside lines thousands of characters long.

Five agents then worked in parallel on **disjoint file sets** (two agents editing one file silently lose edits), each with a shared
brief of verified facts. Every diff was then read against `main` by the lead, and each new factual claim was checked in the code
(listed under Verification).

## What changed

| Area | Change |
|---|---|
| `launch.command` | Deleted. Its job (a `main.py --interval 60` loop in a Terminal) is replaced by the daemon: `scripts/install_stack_service.command` for always-on, or `python -m desktop.orchestrator_daemon`. |
| `robinhood-execution` skill (both copies), `/rh-execute` | A fresh queue comes only from a daemon cycle (`trigger_run()`, poll `get_run_status`, attach on 409). In `off`/`shadow` the skill **stops**, names the mode, does not run a cycle or `main.py`, and does not load a queue file. Pinned in `tests/test_robinhood_e2e.py`. Also fixed `NTFY_TOPIC` to the real `ALERT_NTFY_TOPIC`, and a pointer to a deleted GUI panel. |
| `docs/RUNBOOK.md` | §0/§0.1/§1.1/§2/§3.1/§3.14/§5.1/§6 describe the daemon path. §3.15 gets a "Status: DONE" paragraph (per the operator's record) and the skipped extended review; the rollback paragraph is unchanged plus how to reload the job and what a rollback does to skill-driven execution. §5.1 retitled (nothing linked the old anchor). |
| `scripts/install_schedule.command` | Still installs the daily-advisory job (no behaviour change). Added one warning `echo` when it reaches that label. Comments now say the job is rollback-only. |
| `scripts/install_stack_service.command`, `investyo_stack_service.sh`, monthly-retrain plist, `ci.yml` | Comment/text corrections only. The unload block for a leftover daily-advisory job is kept. |
| `docs/HOW_TO_GUIDE.md`, `README.md`, `.env.example` | `launch.command` and "run `main.py`" replaced by the daemon path. `.env.example`: **comments only** (checked: every `KEY=value` line, active or commented-out, is identical to `main`). Keyless-run caveat added to the guide. |
| `docs/architecture*.md`, two known-issue notes | Wording follows the daemon and the kept plist ("unloaded, plist kept"). A hunk that said the plist "was deleted" and one that duplicated a test-index line were corrected. |
| `CLAUDE.md` / `AGENTS.md` | Steps 5.0-5.4 done; the `./launch.command` command removed; feature-freeze allowance now 5.5-5.6. Mirror intact. |
| `docs/FEATURE_TIER_HISTORY.md` | The existing 5.4 entry extended (not a second entry). |

## Decisions and judgment calls (please review)

1. **The execution skill fails closed in `off`/`shadow`.** An agent proposed letting it review an *already-existing* queue file
   in those modes if the operator asks. I removed that: it sat inside a rule that said "hard stop", and the plan says to stop. The
   cost is real and documented in the RUNBOOK: **during a rollback (mode `off`/`shadow`, `main.py` writing the queue) `/rh-execute`
   stops until the mode is back to `primary`.** If you want skill-driven execution to work during a rollback, that needs an explicit,
   narrower allowance. This is your call.
2. **The installer warns but does not change.** `install_schedule.command` still installs the daily-advisory job, so re-running it
   (say, to change the weekly-edgar schedule) reloads a job the cutover deliberately unloaded. I added a visible warning and nothing
   more, because "should it install that job by default" is not mine to decide.
3. **No `main.py` edits.** `main.py:94` still has a comment mentioning `launch.command`. It is archived in 5.5.

## Verification

Run in this container, in an isolated `LOCAL_DATA_ROOT`:

- `pytest` on `test_measure_settings_census`, `test_settings_liveness` (both freshness gates), `test_agent_docs_sync`, `test_help_content`,
  `test_robinhood_e2e`, `test_skill_directory_parity`, `test_step5_4_verify_cycle`, `test_command_manifest_freshness`,
  `test_shell_completion`, `test_discovery_skill`, `test_jules_delegation_skill`, `test_investyo_mcp_server`, `test_settings_keysets`:
  **574 passed, 1 skipped** (`zsh` is not on this container's PATH). No census regeneration was needed: no scanned source moved.
- `ruff check tests/test_robinhood_e2e.py --select=F821,F822,F823,E9`: clean.
- `bash -n` on the three shell files, `plistlib` parse of the monthly-retrain/daily-advisory/weekly-edgar plists (no `--` in XML comments),
  `yaml.safe_load` of `ci.yml`; `install_schedule.command` run as a sandbox copy with a stub `launchctl` and a fake `HOME`: the warning
  printed and all three jobs still installed.
- Byte checks: the daily-advisory and weekly-edgar plists equal `main`; `.env.example` key/value lines equal `main`; `CLAUDE.md`
  equals `AGENTS.md`; no conflict markers; no text says the kept plist is deleted.
- Claims checked against the code rather than taken on trust: `ALERT_NTFY_TOPIC`; the dashboard button label; the daemon's new-intent push
  (`side_effects` defaults true; only the shadow queue passes false); no webapp receipts view; `PUT /interval`; `setup.sh --clean`;
  the "Brokers & Keys" label; `send_daily_summary`; `main.py`'s primary-mode WARNING; `launch.command` defaulting to `--interval 60`;
  dry-run runs always use the subprocess; the daemon's report passes no `account_summary` (so no Portfolio band); the `main_orchestrator.py` flags.

## Not verified / disclosed limits

- **The full offline suite was not run locally** (about an hour here). This PR changes no Python runtime code, so CI is the check for it.
- **Nothing was observed on the live machine.** "Live on `primary` since 2026-10-12" and "the daily-advisory job is unloaded" are repo
  record (#1120 and the RUNBOOK cutover record), and are worded that way in the docs.
- The rollback path (set `off`/`shadow`, reload the job) was not exercised.
- The "Run full advisory pipeline" button and the Settings -> Brokers -> Refresh flow were taken from the code and docs, not clicked.
- Five agents worked from one brief; their reports are model output and were checked by reading diffs and code, not by re-running each.

## Stale items found and not fixed (outside this PR's files)

- `main.py:94` comment; `api/pilots_api.py:3186`, `settings.py:3967`, `data/robinhood_login.py:587` comments still cite `main.py --refresh-account`
  as the current route; `scripts/generate_shell_completion.py:10` comment; `execution/queue_builder.py` comments say `NTFY_TOPIC`
  (the setting is `ALERT_NTFY_TOPIC`) and name a panel that moved to `shared/`; `desktop/orchestrator_daemon.py:92` cites a deleted
  `launch_app.command`. All comment-only, in runtime files, so left for a follow-up (or 5.5, for `main.py`).
- README still describes Google Sheets in lines 5 and 38-40; `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md` Options Desk row is stale.
- Pre-existing and unchanged: the keyless-run synthetic-data gate gap, and the throttle-test timing flake.
