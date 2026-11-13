# Cleanup and hardening sweep (2026-10-29) task tracker

Walkthrough: `.claude/cleanup_hardening_sweep_2026_10_walkthrough.md`. PR: kevinmarko/Stockpy#1133.
Plans written in this sweep (review before any code): `.claude/synthetic_data_gate_keyless_implementation_plan.md`,
`.claude/forecasting_rebuild_f5_f6_implementation_plan.md`.

## Done
- [x] Confirm what "this" meant: shrink step 5 was already finished on `main` (#1124, #1125), so the scope is the cleanup sweep plus plans for F5/F6 (operator's choice)
- [x] Restart the designated branch from `origin/main`; force-with-lease over the closed #1122's head (`c700688e`, kept at `refs/pull/1122/head` and in the local ref `backup-closed-pr-1122`)
- [x] Six agents on disjoint files: throttle flake, runtime comments, docs/skills/plists, PR review (read-only), keyless synthetic-gate plan, forecasting F5/F6 plan
- [x] Read every agent diff against `main`; verify new factual claims in the code (preflight count 27 / auto-skip 6, risk gate "eleven-check", the daemon's engine fallback)
- [x] Prove the 47 comment-only files change no code (AST with docstrings stripped identical to `HEAD`, line counts unchanged) - done by the agent and again independently
- [x] GitHub Actions retired (operator): `scripts/local_ci.py` + `make local-ci` + `tests/test_local_ci.py` (38 tests, 12/12 mutations caught) + CLAUDE.md/AGENTS.md + architecture docs
- [x] Regenerate the settings census/liveness with the repo scripts (one more scanned file); both freshness tests pass
- [x] First real run of the gate found three pre-existing problems; each root-caused, fixed and mutation-checked: LightGBM OpenMP oversubscription under xdist, a urllib opener leak from `fredapi`, a wall-clock perf guard
- [x] Full local gate on `652e9730`: offline 11,799 passed / 0 failed; coverage 77% (floor 74%); slow, lint, webapp x3, bandit, ML audit, ML lookahead all PASS
- [x] **Operator: "delete the workflows"** (2026-10-30). All five `.github/workflows/*.yml` removed (`c1c86b62`); `CODEOWNERS` and the PR template kept; the four stale references fixed (`/verify`, the ruff comment, `local_ci.py` docstring, CLAUDE.md/AGENTS.md)
- [x] Diagnosed the last remote run (2721): shard 2 hung 6 h on an unchanged commit. The log pins it to `TestFinbertOpenMPCoexistence` (torch + LightGBM in one pytest worker; no `timeout-minutes`, no OpenMP cap). One PR comment posted with the evidence
- [x] **Operator: "fix the UI"** (2026-10-30). Browser audit of 54 routes x 2 widths: 17 pages scrolled sideways at 390 px and a grey shadow ran down every page. Fixed and tested (`fdebcd97`); 0 of 108 loads overflow now, 0 console/page/network errors
- [x] Stale user-visible text (settings descriptions + mock mirrors, three daemon log lines, one help entry, the `app_shell.py` mention, the dead `NTFY_TOPIC=` key) and the FinBERT/LightGBM test moved to a fresh process with a timeout (`e807305d`)
- [x] Gates on the final tree: ruff + offline suite 11,799 passed / 29 skipped; `make local-ci --profile quick` (lint, webapp typecheck/test/build, bandit, ML audit, ML lookahead) all PASS; vitest 155 files / 1,817 tests

## Open: yours to decide
- [ ] `pip-audit` is RED (59 advisories: pillow 39, filelock 17, certifi 2, setuptools 1) on pins in `requirements.txt` that predate this PR - separate dependency PR
- [ ] Gate plan Q1-Q5 and F5/F6 plan Q1-Q10
- [ ] Branch protection (re-checked 2026-11-13: still on in both the "main" ruleset and the classic `main` rule; the operator's own account can bypass them, this session's cannot): remove any required checks from the deleted workflows (a GitHub setting, not in the repo), or the PR keeps showing "blocked". Confirmed 2026-10-30/31/11-02: three squash-merge attempts refused with 405 (CodeQL results + "5 of 5 required status checks are expected"); no tool in this session can edit rulesets
- [x] **Operator: standing policy, no GitHub Actions ever** (2026-11-02): rule added to CLAUDE.md/AGENTS.md; the PR template's test plan now asks for the `make local-ci` table (and its stale Streamlit GUI path is now `webapp/`)
- [ ] Nothing schedules the daily network + slow run now (`make local-ci ARGS="--profile nightly"` runs it on demand). A launchd job would be new capability, so it needs your OK under the feature freeze. CodeQL has no local equivalent (bandit covers part)
- [x] PR #1131 merged by the operator 2026-11-13 (merged into this branch as `a4bbd2f`; the 6 test files it touched: 214 passed)
- [ ] PR #1132 (docs-only skill fix; claims re-verified 2026-11-13, merges cleanly) - blocked by the same `main` rules as this PR

## Found, deliberately not fixed here
- **Live-backend UI pass is still owed.** The audit ran against the offline mock only (`VITE_USE_MOCK=false` not exercised); only a few screens were reviewed as screenshots
- `shared/help_content.py`, entry `ai_control_center` (~line 1167): documents the deleted Streamlit AI Control Center ("Section D starts and stops an `--interval` or `--agent` run"). The webapp's `/settings/ai` has no such section. Probably more entries describe deleted GUI; needs an audit of the Python help catalog against the webapp
- `DASHBOARD_REFRESH_SECONDS` is now described as unused (nothing reads it). Removing it is safe at startup (`extra="ignore"`) but touches `shared/env_io.py`, `pilots/settings_domains.py`, the `api/pilots_api.py` tunables list, `mock.ts`, the census and tests
- `scripts/ci_shard.py`, `.test_durations.json` and `tests/test_ci_shard.py` only served the deleted workflow's 4-way sharding; `local-ci` runs one process. Candidates to archive or remove
- Dead code left by step 5 (verified: no production importer/caller; only docstrings or the definition mention them): `shared/engine_status.py`, `pipeline/agentic_queue.py::daemon_owns_agentic_side_effects`, `pipeline/advisory_inputs.py::build_universe`, the unused `*_fn` fields on `pipeline/context.py::RunContext`. (`make local-ci`'s ML audit also lists more "orphaned module" candidates; those are the auditor's heuristic and were not verified)
- Code comments in many files still mention `main.py` or `app_shell.py` as historical context (comments only; the user-visible strings are done)
- This sandbox's `.venv` has no `torch` although `requirements.txt` lists it, so torch tests skip here (the rewritten FinBERT test was verified against a throwaway CPU torch on `PYTHONPATH`, not the venv)
- Cosmetic: the mobile Settings section links wrap as plain text rather than tabs; the top status bar scrolls sideways below ~1440 px by design, with no visual cue

## Round 2 (2026-11-10): four agents on the leftovers
- [x] **Live-backend UI pass** (real `api.pilots_api` / `data_api` / `metrics_api` + `VITE_USE_MOCK=false`, 56 routes x 2 widths): no page errors, no failed requests. Found and **fixed** `/settings/reference`: dict/list-valued settings rendered as `[object Object]` (the real backend sends them raw; the mock never did) and the page scrolled sideways on a phone (long unbreakable descriptions/keys). Regression tests are mutation-checked; mock now carries a dict-valued field.
- [x] **Python help catalog** (`shared/help_content.py`, `docs/HOW_TO_GUIDE.md`): every entry audited against the webapp and live backend; stale screens renamed, wrong claims fixed, deleted-surface entries removed; new `tests/test_help_content_live_surfaces.py`.
- [x] **`DASHBOARD_REFRESH_SECONDS` removed** everywhere; tests prove an old `.env` / runtime-flags store carrying it still loads.
- [x] **Step-5 dead code: verified plan written, nothing deleted** (`.claude/cleanup_hardening_sweep_2026_10_dead_code_removal_plan.md`). Three symbols are still read by `legacy/main.py`, so deleting them retires the documented "revert the 5.5 PR" rollback: **operator decision D1** (D2-D6 in the plan).
- [x] Removed the 4-way shard splitter (`scripts/ci_shard.py`, `.test_durations.json`, `tests/test_ci_shard.py`); standing policy "no GitHub Actions ever" in CLAUDE.md/AGENTS.md and the PR template.

### Still open from round 2 (found, not fixed)
- [x] FIXED 2026-11-10: `ErrorState` (`webapp/src/components/ui.tsx` ~465) treated every 404 as "run the pipeline" and drops the server's message, so a bad pilot id (`/pilots/does-not-exist`) says "Nothing here yet - run the pipeline". Low severity; shared component.
- NOT REPRODUCED 2026-11-10 (mock and live backend, 1024/1280/1440: scrollWidth == viewport): `/commands` at 1280 px scrolls ~6 px sideways (cosmetic; candidate: the top-bar Trip Kill Switch button).
- Stale mentions found by the help audit in files outside its scope: `shared/ai_control_center.py` ("Section B/C/D", `--agent`), `settings.py` `PROGRESS_POLL_SECONDS` description ("Launcher"), `webapp/src/help/helpContent.ts` (Buy/Sell Range "technical options engine", `garch vol` "and options"), `pilots/reports.py` docstrings, `shared/help_widgets.py` (Streamlit-only module kept alive by Gravity steps 76/77), Gravity step 77 check 9 (reads a directory that no longer exists, so it can never pass).
- Live pass could not exercise populated-data shapes, a real daemon, auth paths, POST actions or chat/streaming (no keys or data in the sandbox).

### Round 3 (2026-11-10, same day)
- [x] Unknown pilot id now says "Not found / There is no pilot with the id ..." (opt-in `ErrorState.notFound`; other screens keep the cold-start wording). Tests pinned, mutation-checked, checked in a browser.
- [x] Stale text: `PROGRESS_POLL_SECONDS` description (it is read only by a function nothing calls), webapp help for Buy/Sell Range (strategy engine, not the archived options engine) and GARCH vol, and `shared/ai_control_center.py` (no more Streamlit "Section B/C/D" or `--agent` run). `pilots/reports.py` / `api/pilots_api.py` Streamlit mentions are provenance notes ("ported from"), left as accurate history.
- [ ] Decision **D7** (added to the dead-code plan): remove `PROGRESS_POLL_SECONDS` + `compute_run_progress` + the `pipeline_progress` help section, all dead since the Streamlit Launcher.
- [x] Dead-code plan Steps A and E done (`engine_status` + three Streamlit-era helpers, with their tests); D1 (rollback-coupled Steps B-D), D2 and D7 still open.
- [x] Operator decisions 2026-11-10: **D1 keep the rollback** (Steps B-D not done); **D2 and D7 done** (dead `state_snapshot_age_seconds`, `compute_run_progress` + `PROGRESS_POLL_SECONDS` + `pipeline_progress` help removed; Gravity step 74 pins it).
