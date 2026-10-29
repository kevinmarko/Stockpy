# Cleanup and hardening sweep walkthrough (2026-10-29)

Tracker: `.claude/cleanup_hardening_sweep_2026_10_task.md`. PR: kevinmarko/Stockpy#1133.

**Scope.** Tests, test tooling, comments, docs and two review plans. No runtime logic changed: every `.py` file outside `tests/`
and `scripts/local_ci.py` differs from `HEAD` in comments and docstrings only, which was proven mechanically (below).
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

## Decisions for the operator
1. Gate plan (Q1-Q5): gate the writers or stop the cycle early; inner durable writers now or later; a deduped alert; run-status wording; confirm the
   live start log shows `data_engine=DataEngine`. Correction to my earlier description: the daemon builds its own engine and falls back to mock
   data both with no key and when a configured key fails; the standalone branch I had pointed at is not the daemon's path.
2. F5/F6 plan (Q1-Q10): under the code's own 60-observation rule, F5's data does not mature until about February 2027, so the plan recommends
   building F6 first; plus flag names during the freeze and the flip bar (the existing forecast-direction proxy already fails the deployability gate).
3. Delete `.github/workflows/`? They still trigger. Branch protection may still require them.
4. A dependency PR for the pip-audit findings (pillow 13, certifi, setuptools 84.1, filelock 4.2.1).
5. PR #1131 (merge after one small hardening: a spec-less `sentence_transformers` stub makes the import raise `ValueError` where `main` falls back
   cleanly) and #1132 (safe to merge). The review was read-only and posted nothing.
