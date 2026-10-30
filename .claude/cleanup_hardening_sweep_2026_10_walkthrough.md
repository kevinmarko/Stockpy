# Cleanup and hardening sweep walkthrough (2026-10-29)

Tracker: `.claude/cleanup_hardening_sweep_2026_10_task.md`. PR: kevinmarko/Stockpy#1133.

**Scope.** Tests, test tooling, comments, docs and two review plans, then (operator, 2026-10-30) the deletion of the GitHub workflows,
a Pilots PWA layout fix, and a few stale user-visible strings. **No trading or runtime logic changed.** Through `652e9730`, every `.py`
file outside `tests/` and `scripts/local_ci.py` differs from `HEAD` in comments and docstrings only (proved mechanically, below).
The later commits add: webapp layout CSS and four small TSX edits (`fdebcd97`), and string-only edits (settings descriptions, three daemon
log lines, one help entry, `.env.example`; `e807305d`). Those are line-count neutral, so the settings census stays valid.
No setting was added, removed or renamed; nothing touches `.env`, the runtime-flags store or `~/.stockpy_local`.

## Why this exists
1. Shrink step 5 finished while work was paused (`main.py` archived, `ORCHESTRATOR_DAEMON_ENABLED` retired), leaving stale
   comments, docs and skill text that still told people to run archived things.
2. **GitHub Actions is no longer used** (operator, 2026-10-29), so nothing outside the working tree checks a change. That needed a
   local replacement for the old jobs, and the first honest run of it exposed problems that CI had been hiding.
3. Two risky changes (a safety-gate fix, the forecasting F5 decision rewrite) need operator-reviewed plans before code.

## Commits (one concern each)
| Commit | What |
|---|---|
| `62a44216` | `make local-ci`: `scripts/local_ci.py` runs the old jobs' commands and prints a table. Fails closed. |
| `aca8888c` | Throttle tests assert on the throttle's own issuance times, not on how long the caller took to return. |
| `32792dd5` | 47 files, comments/docstrings only. |
| `9cddbc2d` | Docs, skills, plists, installer text. |
| `99a5337c` | The two plans, for review. |
| `0a8bb41c` | local-ci caps OpenMP threads for the xdist steps (found by the first real run). |
| `652e9730` | Two more pre-existing flakes fixed (urllib opener leak, perf guard). |
| `c1c86b62` | Deleted `.github/workflows/` (all five files; operator decision). `CODEOWNERS` and the PR template stay. |
| `fdebcd97` | Pilots PWA: 17 pages scrolled sideways on a phone, and a grey shadow band ran down every page. Fixed, with tests. |
| `e807305d` | Stale user-visible text; the FinBERT/LightGBM test now runs in a fresh process with a timeout. |

## The GitHub workflows (deleted, 2026-10-30)
The last remote run on this branch (run 2721, head `e8fd3ca7`) never finished. Shard 2 of 4 hung until GitHub's 6-hour job limit cancelled it,
on a commit that differs from the previous passing one (`652e9730`, shard 2 in 4 m 18 s) only by two markdown files, so the hang is
nondeterministic and not from this PR. The job log shows where: worker `gw1` started
`test_news_catalyst.py::TestFinbertOpenMPCoexistence::...coexists_with_lightgbm` and never reported a result; 178 s later (the CI per-test limit is
180 s, `--timeout-method=thread`) xdist printed `node down: Not properly terminated` and the run went silent. That test loads FinBERT (torch), trains
LightGBM and runs a torch pass in one process, which the repo's own known-issue doc names as a deadlock risk. The workflow had no `timeout-minutes`
and no OpenMP thread cap. The test now runs in a fresh interpreter under a timeout (`e807305d`).
What stops being checked by anything: **CodeQL** (bandit covers part of it) and the **daily network + slow run** (`make local-ci ARGS="--profile nightly"`
runs it on demand; nothing schedules it). The Supabase preview check is a GitHub app, not a workflow, so it is unaffected by the deletion.
Branch protection is a GitHub setting: any required checks from the old workflows must be removed there, or the PR will keep showing as blocked.
Restore with the command in `CLAUDE.md` (`git show "$(git log --diff-filter=D -1 --format=%H -- <file>)^:<file>"`, for each of the five files). The commit
SHAs named in this walkthrough are the pre-squash branch commits; after the squash merge they stop being commits on `main` and stay reachable only through
`refs/pull/1133/head` on GitHub.

## The UI pass (`fdebcd97`)
Method: `npm run dev` (mock mode), then a Playwright script that loaded all 54 routes at 1280 px and at 390 px, recording console errors, uncaught page
errors, failed or 4xx/5xx requests, near-blank pages, error boundaries, bad text (`undefined`, `NaN`, `[object Object]`, `Invalid Date`, `main.py`) and
`document.documentElement.scrollWidth > clientWidth`.
- **Found:** 0 console errors, 0 page errors, 0 failed requests across 108 page loads. But **17 of 54 routes scrolled sideways at 390 px** (every Settings page,
  `/sentiment`, `/create-data-app`; worst 381 px too wide), and a **grey shadow band down the right edge of every page** at every width.
- **Causes:** `SettingsLayout` was an inline flex row with a fixed 250 px sidebar and no breakpoint, which left the content pane about 60 px wide on a phone;
  `CreateDataApp` used an inline `1fr 1fr` grid (a bare `1fr` cannot shrink below its content); two card headers could not wrap; the closed AI chat drawer
  kept a 30 px `box-shadow` while parked just off-screen, and the blur reached back into the viewport.
- **Fix:** mobile-first `.split-*` classes in `index.css` at the stylesheet's existing 768 px breakpoint (an inline style cannot carry a media query); two
  `flexWrap`s; the drawer shadow only while open. **Result:** 0 of 108 page loads overflow (was 17), still 0 errors; desktop layout unchanged (screenshot checked).
- **Tests:** `responsiveLayout.test.ts` (stylesheet stays mobile-first), `SettingsLayout.test.tsx`, two `AIChatInterface` cases; each mutation-checked. jsdom has
  no layout engine, so these pin the cause, not the pixels; the pixels were measured in a real browser.
- **Verified:** typecheck, vitest (155 files, 1,817 tests), `vite build`, `make local-ci --profile quick` all pass.
- **Not a bug, left alone:** the top status bar is `overflow-x: auto`, so below about 1440 px the Trip Kill Switch button is reached by scrolling the bar.

## How `make local-ci` works, and why it is built this way
- Each step is the same command the old workflow ran (`--list` prints them), plus a profile (`pr`, `quick`, `nightly`).
- **Fail closed.** A missing tool, a command that cannot start, or a timeout is a failure. The only skips are labelled:
  you asked (`--skip`, `--no-coverage`), a dependency failed (`coverage` after a failed `offline`), or the path-gated ML steps when
  git positively says no watched path changed. If git cannot say, they run.
- No `os.environ` reads (a repo rule): settings are applied by running the command under `env KEY=VALUE`, which also makes the
  full command visible in `--list`.
- Reported exit status is the runner's own, never a pipeline's. (An early "exit 0" from my shell was the exit of an `echo`, which is
  exactly the mistake a gate must not make.)
- 38 tests; 12 mutations (error counted as pass, git-unknown as skip, dependency check dropped, exit code forced 0, timeout as pass,
  fail-fast ignored, marker filter dropped, coverage args, thread cap removed/weakened/not applied...) are each caught.

## What the first real run found (all pre-existing; none from this sweep)
1. **LightGBM deadlock under pytest-xdist.** `-n auto` starts one worker per core and LightGBM starts one OpenMP thread per core
   *in each worker*, so 4 workers on 4 cores run 16 threads. Training stalled for the whole per-test timeout or crashed the worker
   (xdist "node down", then an internal `KeyError`). Measured on the same 23 LightGBM tests: serial 9.7 s; one worker 8 s; `-n 4` 6-8
   failures over 7-12 minutes (also on a clean `origin/main`); `-n 4` with `OMP_THREAD_LIMIT=1` 6.9 s. Fix: the xdist steps run under
   `env OMP_THREAD_LIMIT=1`. A first guess (`OMP_NUM_THREADS=1`) did not work because the code can override it; the hard cap cannot be.
2. **A urllib opener leak.** `fredapi.Fred(...)` installs a process-wide urllib opener whenever `HTTP_PROXY` or `HTTPS_PROXY` is set. With
   only `HTTPS_PROXY` set (this sandbox, and common on corporate machines) the proxy map has `'http': None`, and every later plain-`http://`
   request in that xdist worker raises `TypeError` in urllib. `tests/test_pilots_api.py::TestAutomationStatus::test_read_token_gates_the_endpoint`
   therefore failed intermittently and passed alone. Fix: a root `conftest.py` autouse fixture restores the default opener after each test.
3. **A wall-clock perf guard** (`TestPerfSmoke::test_tiny_window_completes_quickly`, bound 30 s) failed under load. I first switched it to
   CPU time and measured that: about 5 CPU-seconds alone but 24-27 under contention (threads spin-wait), so that would have been flaky the
   other way. Its purpose is catching a daily-refit regression, so it now counts model fits: the real code does 16, the ceiling is 28, a
   daily refit does 72. Making the adapter refit daily fails it.
4. **`pip-audit` is genuinely red**, locally and on GitHub: 59 advisories (pillow 12.3.0: 39, filelock 4.1.1: 17, certifi 2026.6.17: 2,
   setuptools 83.0.0: 1) on pins in `requirements.txt`. Not fixed here; the pillow bump is a major version and deserves its own PR.

## Verification (on the pushed commit `652e9730`)
| Step | Result |
|---|---|
| offline suite | PASS: 11,799 passed, 0 failed, 29 skipped (6:53) |
| coverage floor | PASS: 77% against `fail_under = 74` |
| slow suite | PASS (97 s) |
| lint | PASS (1,101 files scanned; also checked that the rule set fails on a planted undefined name and syntax error) |
| webapp typecheck / tests / build | PASS / PASS / PASS |
| bandit | PASS |
| ML audit / ML lookahead tests | PASS / PASS (path-gated; they ran because `cnn_lstm_worker.py` was touched) |
| pip-audit | **FAIL** (59 advisories, pre-existing pins) |

Other checks: the 47 comment-only files have identical ASTs (docstrings stripped) to `HEAD` and unchanged line counts, checked by the agent and
independently by me; settings census/liveness regenerated with the repo scripts and passing; `.env.example` key/value lines identical to `HEAD`;
all `scripts/*.plist` parse under a strict XML parser; `CLAUDE.md` equals `AGENTS.md`.

GitHub's runners still triggered on the PR (the workflows are still in the repo). On the earlier head `99a5337c` they passed everything except
`pip-audit`, independently of the local run.

## Not verified / honest limits
- The throttle fix is justified structurally and shown against an injected-stall fault model (old file failed 127 of 560 runs, new passed 640 of
  640). Natural CPU contention never reproduced the original CI failure (0 of 1,527), so I cannot say it fixes *that* failure.
- Nothing was observed on the live machine. CodeQL and the Supabase preview have no local equivalent.
- `COVERAGE_CORE=sysmon` cannot be used with branch coverage in this coverage version; coverage falls back to its default core (slower, same checks).
- The two plans were written by agents and checked for their load-bearing claims, not line by line; they exist so the operator can decide.
- The 4-core thread cap changes how many OpenMP threads LightGBM uses in the xdist steps, not which tests run; I did not check whether any
  result differs numerically with one thread (the suite passes either way).
- **The UI audit ran against the offline mock API only.** Nothing was checked against a live backend (`VITE_USE_MOCK=false`); a typecheck and a mock-mode
  browser pass have missed live field mismatches before, so a live pass is still owed. Only a handful of screens were looked at as screenshots; the rest
  were checked by the script's measurements (errors, overflow, blank pages, bad text), which cannot see a misaligned or ugly screen.
- The mobile Settings section links now wrap as plain text rows; they work, but they are not styled as tabs or chips. Cosmetic, not changed.
- **This sandbox's `.venv` has no `torch`** (`requirements.txt` lists it), so every torch-dependent test is skipped here, including the one I rewrote. I verified
  that test against a throwaway CPU torch installed outside the repo and put on `PYTHONPATH`, not in the venv. On a machine with torch it runs for real.
  Under `make local-ci` (`OMP_THREAD_LIMIT=1`), the test's `torch.get_num_threads() == 1` check cannot catch a removed thread cap, because torch already
  defaults to 1 there; it can when the variable is unset (checked). The hang and crash checks do not depend on it.
- `DASHBOARD_REFRESH_SECONDS` is described as unused (no code reads it), but it was not removed. The settings model uses `extra="ignore"`, so removing the
  field would not break startup for someone whose `.env` still has it; the cleanup (env allowlist, domain map, tunables editor list, mock, census, tests) is
  its own change.

## Decisions for the operator
1. Gate plan (Q1-Q5): gate the writers or stop the cycle early; inner durable writers now or later; a deduped alert; run-status wording; confirm the
   live start log shows `data_engine=DataEngine`. Correction to my earlier description: the daemon builds its own engine and falls back to mock
   data both with no key and when a configured key fails; the standalone branch I had pointed at is not the daemon's path.
2. F5/F6 plan (Q1-Q10): under the code's own 60-observation rule, F5's data does not mature until about February 2027, so the plan recommends
   building F6 first; plus flag names during the freeze and the flip bar (the existing forecast-direction proxy already fails the deployability gate).
3. ~~Delete `.github/workflows/`?~~ Done (`c1c86b62`). Still yours: remove any required checks from the old workflows in branch protection, and decide whether
   anything should schedule `make local-ci ARGS="--profile nightly"` (new capability, so it needs your OK under the feature freeze).
4. A dependency PR for the pip-audit findings (pillow 13, certifi, setuptools 84.1, filelock 4.2.1).
5. PR #1131 (merge after one small hardening: a spec-less `sentence_transformers` stub makes the import raise `ValueError` where `main` falls back
   cleanly) and #1132 (safe to merge). The review was read-only and posted nothing.
