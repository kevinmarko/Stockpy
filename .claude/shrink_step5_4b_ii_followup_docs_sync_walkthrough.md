# Step 5.4b-ii follow-up walkthrough: bring the remaining docs, skill copy and installer comments in line with #1123

Plan: `.claude/shrink_step5_4_callers_implementation_plan.md`. Tracker: `.claude/shrink_step5_4b_ii_followup_docs_sync_task.md`.
Builds on `.claude/shrink_step5_4b_ii_cutover_dependent_walkthrough.md` (#1123).

**Documentation, one skill copy, shell/plist comments. No Python runtime code and no test file changed.** Nothing touches the live `.env`,
the runtime-flags store or `~/.stockpy_local`, and no setting was added, removed or renamed.

## Why this is a follow-up and not the step itself

This PR was opened as the whole of step 5.4b-ii. While its CI was running, **#1123 landed on `main` with the same title and the same
`launch.command` deletion**, written by a session that could not see the parked draft this branch was ported from. Both contained a
full set of edits to the same ten files, so GitHub reported the PR as conflicting.

#1123 also **decided the two questions this PR had left open**, and differently from how this PR had answered them:

| Question | This PR's original answer | `main` (#1123), which wins |
|---|---|---|
| Should `install_schedule.command` still install the daily-advisory job by default? | Yes, with a warning line | **No.** Only with `--with-daily-advisory`, which refuses unless `DAEMON_AGENTIC_QUEUE_MODE` is `off`/`shadow` (operator decision, 2026-10-12) |
| What does the execution skill do in `off`/`shadow`? | Hard stop, no queue file loaded | Stop and **ask whether this is a RUNBOOK §3.15 rollback**; if confirmed, continue with the queue the 08:45 job last wrote, under every hard stop |

Those are your decisions, recorded on `main`, so this PR defers to them. The ten files they touched (`CLAUDE.md`, `AGENTS.md`, `README.md`,
`.env.example`, `docs/RUNBOOK.md`, `docs/HOW_TO_GUIDE.md`, `docs/FEATURE_TIER_HISTORY.md`, `.claude/skills/robinhood-execution/SKILL.md`,
`.claude/commands/rh-execute.md`, `scripts/install_schedule.command`) were taken from `main` whole, not hand-merged: merging them line by
line would only have rebuilt this PR's version of each decision. My broader rewrites of the `HOW_TO_GUIDE`/RUNBOOK `main.py` passages were
dropped for the same reason; #1123 left those for step 5.5, which archives `main.py` and has to rewrite them anyway.

## What this PR still contains (all of it complementary to #1123)

| File(s) | Change |
|---|---|
| `.agents/skills/robinhood-execution/SKILL.md` | **Synced to `main`'s `.claude` copy.** #1123 updated only the `.claude` copy, so the `.agents` copy still differed in five places (the `off`/`shadow` handling among them). It is now `main`'s `.claude` text plus the Antigravity header comment, and nothing else differs. |
| `.claude/skills/robinhood-execution/SKILL.md`, `.agents/...` | `NTFY_TOPIC` -> `ALERT_NTFY_TOPIC`. The setting `alerting.py` reads is `settings.ALERT_NTFY_TOPIC`; `NTFY_TOPIC` is not a setting. |
| `docs/architecture.md` + 7 files in `docs/architecture/` | Wording follows the daemon and the kept plist (`main.py` row, `.env`-loading bullet, `alerting.py` daemon equivalent, `main.py` "being retired" marker, `daily_report.html` is not retired outside `primary`, MCP report mode "full or not reported"). A staged hunk that said the plist "was deleted" was corrected, and one that duplicated a test-index entry was dropped. |
| 3 files in `docs/known_issues/` | "retired (step 5.4)" -> "unloaded since the cutover, plist kept for rollback". |
| `scripts/install_stack_service.command`, `scripts/investyo_stack_service.sh`, `scripts/com.investyo.monthly-retrain.plist`, `.github/workflows/ci.yml`, `docs/AGENTIC_TRADING_SAFETY_FRAMEWORK.md` | Comment/text corrections only (a stale "no longer ships its plist", a note about a `launch_app.command` that no longer exists, the installer's job list now matching #1123's opt-in flag). The unload block in `install_stack_service.command` is kept. |
| `docs/FEATURE_TIER_HISTORY.md`, `.claude/shrink_step5_4b_i_verify_helper_walkthrough.md` | Corrected my own earlier overstatement: I called the `weekly-edgar.plist` `--`-in-a-comment bug "unparseable". It is not well-formed XML, so strict parsers (Python's `expat`) reject it, but macOS's own parser, which launchd uses, accepts the same pattern (the repo already records this for `com.investyo.stack.plist` in `docs/known_issues/launcher_vite_port_sibling_worktree_lockout.md`), so it was cosmetic. |

## Verification

- `pytest` on the settings census and liveness freshness gates, `test_agent_docs_sync`, `test_help_content`, `test_robinhood_e2e` (`main`'s own file,
  unchanged, which pins both skill copies), `test_skill_directory_parity`, `test_step5_4_verify_cycle`, command-manifest and shell-completion
  freshness, the discovery/jules skill tests, `test_investyo_mcp_server`, `test_settings_keysets`: **572 passed, 1 skipped** (`zsh` is not on this container's PATH).
- The two skill copies differ only by the header comment; the `.claude` copy differs from `main` only by the setting name.
- `bash -n` on the three shell files; `plistlib` parse of every plist except `com.investyo.stack.plist`; no conflict markers; `CLAUDE.md` equals `AGENTS.md`.
- **A mistake caught by that check:** while updating the monthly-retrain plist comment I wrote `--with-daily-advisory` inside an XML comment, the exact
  pattern described above. The parse check flagged it and it was reworded before commit.

## Not verified / disclosed

- Nothing was run on the live machine, and the installer was not executed here (it loads real launchd jobs); `main`'s guard was read, not exercised.
- I did not check #1123's own CI results; CI for this PR runs on the merged tree.
- `com.investyo.stack.plist` is not strict-XML-valid (pre-existing, unchanged, cosmetic per the repo's own note), so I did not edit the live service plist.
- Still outstanding for 5.5, unchanged from #1123's list: three RUNBOOK passages (near lines 321, 341 and 1205) still offer `python3 main.py --refresh-account` (works until `main.py` is
  archived); about a dozen `.env.example` comments still describe the deleted Streamlit GUI; the kept plist and the `--with-daily-advisory` path run `main.py`, so 5.5
  must move or retire them.
- Stale comments in runtime files, found and not fixed: `api/pilots_api.py:3186`, `settings.py:3967`, `data/robinhood_login.py:587` (cite `main.py --refresh-account`),
  `scripts/generate_shell_completion.py:10`, `execution/queue_builder.py` (`NTFY_TOPIC`, a moved panel), `desktop/orchestrator_daemon.py:92` (a deleted launcher).
