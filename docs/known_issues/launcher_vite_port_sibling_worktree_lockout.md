# The Pilots PWA stopped opening: a sibling worktree's Vite dev server locked out the launcher

**Date:** 2026-09-13
**Status:** **Fixed.**
**Component:** `launch_webapp.command` (`_check_vite_port`)
**Symptom as reported:** "the program doesn't seem to open anymore."

---

## Symptom

Double-clicking the Dock's **Stockpy Pilots** icon (an AppleScript applet that runs
`cd /Users/kevinlee/Stockpy-live && ./launch_webapp.command --live`) no longer opened the
app. Nothing rendered, and the Terminal window closed or scrolled past the cause.

The failure was silent in the place an operator would look. `output/daemon.json` and the
daemon log made it look like the *daemon* was crashing:

```
2026-09-13 11:00:20  INFO     OrchestratorDaemon started: engines warm, ... interval_seconds=3600
2026-09-13 11:00:20  INFO     Wrote daemon discovery file: .../daemon.json (state=started)
2026-09-13 11:00:20  WARNING  Received signal 15 (pid=96632) — tearing down orchestrator daemon before exit.
2026-09-13 11:00:21  INFO     Orchestrator daemon shut down cleanly.
```

A clean start followed **one second later** by SIGTERM. `data_api` and `metrics_api` show the
same shape in their own logs: startup complete, `GET /health 200 OK`, then `Shutting down`.
Every backend was healthy. None of them was the problem.

## Root cause

`launch_webapp.command` runs Vite with `--strictPort`, because a silent port bump would break
CORS against the backends (which are pinned to `:5173` via `settings.CORS_ALLOWED_ORIGINS`).
So `_check_vite_port` has to classify whatever holds `:5173` as either **ours** (a leftover dev
server it may recycle) or **foreign** (`exit 1` with an actionable message, rather than Vite's
raw `EADDRINUSE` stack trace).

The ownership test was an exact cwd-prefix match against the calling checkout:

```bash
if [[ "$cmd" == *"vite"* ]] && lsof -p "$pid" -a -d cwd 2>/dev/null | grep -q "$SCRIPT_DIR/webapp"; then
```

At the time of the report, `:5173` was held by PID 85931:

```
node 85931 kevinlee ... cwd  /Users/kevinlee/Stockpy-live/.claude/worktrees/strategyengine-roc-6m-error-322e0d/webapp
```

That is this repository's *own* dev server — just started from a **different git worktree**, by an
abandoned agent session. `$SCRIPT_DIR/webapp` for the real checkout is
`/Users/kevinlee/Stockpy-live/webapp`, which is not a prefix of that path, so the check classified
it as a total stranger and took the hard `exit 1` path on **every** launch, forever, until the
operator hunted the PID down by hand.

This is a design gap, not fresh breakage. The port/lock bookkeeping is *deliberately* global
across worktrees (the script says so itself: *"Ports are fixed (8601-8604, 5173) regardless of
which checkout/worktree this script runs from"*), but the safety-recognition check that decides
whether a stray Vite process is safe to kill was never made worktree-aware. `git worktree list` on
this machine shows ~90 concurrent worktrees created by Claude Code and Antigravity agent
sessions; any one of them leaving `npm run dev` running blocks the real launcher for good.

### Why the daemon looked like the culprit

`_check_vite_port` was called *last*, immediately before `npm run dev` — i.e. **after**
`data_api`, `metrics_api` and the orchestrator daemon had each been started and waited on. So a
port conflict cost ~20s of booting three backends, and then the `EXIT` trap tore all three back
down. The daemon's clean-start-then-SIGTERM log is that trap firing, not a daemon fault. The
component that failed and the component that appeared to fail were different.

## Fix

Two changes, both in `launch_webapp.command`:

1. **Ownership is now decided by git common dir, not a cwd prefix.** Two new helpers, `_pid_cwd`
   (reads a pid's cwd via `lsof -Fn`, which survives paths containing spaces — this repo has a
   `Stockpy Pilots.app` sibling) and `_repo_common_dir` (`git rev-parse --path-format=absolute
   --git-common-dir`). That path is **identical** for a repository's main checkout and every
   `git worktree add` of it, and different for any unrelated project. A dev server from our own
   repository is a disposable thing we may recycle; everything else still takes the `exit 1` path
   unchanged.

   This degrades *toward* the conservative branch: a non-directory, a non-checkout, or a git too
   old for `--path-format` all yield an empty string, which is treated as "not ours". It can
   never kill a process it failed to identify.

2. **The port check is pre-flighted before anything heavy starts.** `_check_vite_port` now also
   runs right after the mock/live mode decision and before the first `_start_api` call, so a real
   conflict fails immediately and attributably instead of booting and then killing three backends.
   The original late call is kept as a no-op guard against something grabbing `:5173` during the
   backend startup window.

## Verification

* The real blocker was classified correctly by the new logic: PID 85931's cwd and the main
  checkout both resolve to `/Users/kevinlee/Stockpy-live/.git` → **auto-heal**, where the old
  test said foreign.
* `/tmp`, `$HOME`, a fresh unrelated git repo, a non-git directory, and an empty path all still
  resolve to **foreign** → `exit 1` preserved.
* `tests/test_launch_webapp_vite_port_ownership.py` (9 tests) builds real temporary git repos and
  worktrees and exercises the launcher's *actual* shell helpers, plus source-level guards pinning
  both the ownership condition and the call ordering. Reverting the fix was confirmed to fail 8 of
  the 9 — the test genuinely catches the regression rather than merely passing.
* `bash -n launch_webapp.command` clean.
* **End-to-end against live processes, not just the helpers.** The unit tests above exercise
  `_repo_common_dir` and pin `_check_vite_port`'s source, but never *execute* `_check_vite_port`
  itself — an integration slip (a mistyped variable, a kill that never fires) would have passed
  them. So the real function was run twice against a real listener on `:5173`:
  a `vite`-named process whose cwd was a genuine sibling worktree
  (`.claude/worktrees/agent-a088e0f07eaa5c9b6`) was **recycled** — the "another worktree of this
  same repository" branch fired, the process was killed, the port freed, return code 0 — and the
  same binary started from `$HOME` was **left running** with return code 1 and the actionable
  `kill <pid>` message. Both halves of the fix therefore work in situ, not merely in unit tests.
  This is a manual verification: it is deliberately NOT in the pytest suite, because binding a
  fixed port and killing processes would be flaky under parallel CI runs.

## Follow-up (same day): the message named the wrong culprit

Found by running the merged launcher end-to-end from the **main checkout**, which the first
round's verification had not done (it set `SCRIPT_DIR` to a worktree). Ownership — the kill /
don't-kill decision, i.e. the actual bug — was correct in every case. The *message* was not.

The branch choosing between "a previous run of this project" and "another worktree" used a plain
path-prefix test, `[ "${vite_cwd#"$SCRIPT_DIR"}" != "$vite_cwd" ]`. But this repo's worktrees live
**underneath** the main checkout:

```
SCRIPT_DIR : /Users/kevinlee/Stockpy-live
sibling    : /Users/kevinlee/Stockpy-live/.claude/worktrees/<name>
```

so every sibling worktree *is* prefixed by the main checkout's path and was reported as "a previous
run of this project" — in exactly the configuration the incident happens in. Worse, that arm
printed no cwd, so the operator lost the one detail that made the original failure traceable: which
worktree the server actually belonged to.

Fixed with a second helper, `_repo_toplevel` (`git rev-parse --show-toplevel`), which is distinct
per worktree where `--git-common-dir` is deliberately shared. The two together answer the two
different questions — `--git-common-dir` "same repository?" (ownership, unchanged) and
`--show-toplevel` "same checkout?" (wording only). The cwd is now echoed after the `fi`, so both
arms report it.

Verified the same way: a sibling worktree's server now reports "another worktree of this same
repository" with its path, and a real previous run of the main checkout reports "a previous run of
this checkout" with its path. `TestNestedWorktreeIsNotMisreportedAsThisCheckout` pins it — all 5 of
its tests fail against the pre-fix launcher, including a positional assertion that the cwd echo
sits outside the if/else (a plain occurrence count does not catch this: the old code contained the
same echo exactly once, buried in one arm).

## Deliberately not changed

`_sweep_stray_ports` (the `--stop` path, and the source of the
`"— not this project, leaving it alone"` line in `app_launcher.log`) still uses the narrow
cwd-prefix test. That conservatism is correct for *stop*: a Stop click should not kill backends
belonging to an active agent session in another worktree that the operator never started. With
start-side auto-healing in place, Stop no longer needs to.

## Unrelated finding, not fixed

`scripts/com.investyo.stack.plist` contains `--` inside XML comments, which `expat` rejects as
malformed. macOS's own parser — the one `launchd` actually uses — accepts it (`plutil -lint`
reports OK), so this has no runtime effect. Left alone rather than risk editing a live launchd
plist for a cosmetic issue.
