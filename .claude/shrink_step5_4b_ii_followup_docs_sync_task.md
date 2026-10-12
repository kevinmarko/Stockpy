# Step 5.4b-ii follow-up task tracker: sync the remaining docs, skill copy and installer comments with #1123

Plan: `.claude/shrink_step5_4_callers_implementation_plan.md`. Walkthrough: `.claude/shrink_step5_4b_ii_followup_docs_sync_walkthrough.md`.
Builds on #1123 (`.claude/shrink_step5_4b_ii_cutover_dependent_{task,walkthrough}.md`), which landed on `main` while this PR was in CI.

- [x] Port the parked 5.4b-ii draft onto `main` (3-way cherry-pick; five agents on disjoint file sets; every diff read against `main`, new claims checked in the code)
- [x] Notice that #1123 landed the same step and decided both open questions (installer opt-in via `--with-daily-advisory`; skill asks whether it is a rollback in `off`/`shadow`)
- [x] Resolve the conflict by taking `main` whole for the ten files #1123 decided; drop this PR's own answers to those questions
- [x] Sync the `.agents` copy of the `robinhood-execution` skill to `main`'s `.claude` copy (it differed in five places); fix `NTFY_TOPIC` -> `ALERT_NTFY_TOPIC` in both
- [x] Restore `main`'s `tests/test_robinhood_e2e.py` (this PR's extra pins encoded the overruled wording)
- [x] Architecture docs, three known-issue notes, installer / plist / CI comments (kept: they are consistent with #1123)
- [x] Fix the monthly-retrain plist comment to #1123's opt-in flag, without a `--` inside the XML comment (the parse check caught my first attempt)
- [x] Correct my own 5.4b-i overstatement that the `weekly-edgar.plist` bug made it "unparseable" (strict parsers only; macOS accepts it)
- [x] 572 passed, 1 skipped (no `zsh` in this container) on the merged tree
- [ ] Merge commit, push, update the PR title and body, confirm CI green on the merged head
- [ ] The operator decides whether this follow-up is wanted at all, or whether #1122 should simply be closed

## For 5.5 (unchanged from #1123's list, plus what this PR found)

- Move or retire `scripts/com.investyo.daily-advisory.plist` and the `--with-daily-advisory` path (they run `main.py`).
- Three RUNBOOK passages still offer `python3 main.py --refresh-account`; about a dozen `.env.example` comments still describe the deleted Streamlit GUI.
- Comment-only staleness in runtime files: see the walkthrough's last bullet.
