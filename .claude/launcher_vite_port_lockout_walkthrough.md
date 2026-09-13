# Walkthrough — "the program doesn't open anymore" (launcher `:5173` lockout)

**Branch:** `claude/program-wont-open-3b8bef`
**Date:** 2026-09-13
**Reported as:** "the program doesn't seem to open anymore."

---

## How the cause was found

The report had no error message attached, so the first job was reproducing rather than guessing.
Six agents were run in parallel on distinct hypotheses (webapp build/render, launcher scripts,
backend API startup, frontend code review, python code review, machine/runtime state) while the
primary session traced the actual launch path.

The decisive evidence was not in the repo — it was in `/tmp/stockpy_webapp_logs/`, written by the
operator's own failed launch minutes before the report:

* `app_launcher.log` — `:5173 is in use by PID 85931 (node .../worktrees/strategyengine-roc-6m-error-322e0d/webapp/node_modules/.bin/vite) — not this project, leaving it alone.`
* `orchestrator_daemon.log` — daemon started cleanly at `11:00:20`, then `Received signal 15` at `11:00:20`, shut down at `11:00:21`.
* `data_api.log` / `metrics_api.log` — same shape: startup complete, `GET /health 200 OK`, then `Shutting down`.

The Dock icon was confirmed to be an AppleScript applet running
`cd /Users/kevinlee/Stockpy-live && ./launch_webapp.command --live` (decompiled with
`osadecompile`) — so the path itself was fine and the failure was inside the launcher.

`lsof` confirmed PID 85931 was **still holding `:5173` at the time of the investigation**.

## Root cause

`launch_webapp.command::_check_vite_port` classified the process holding `:5173` via an exact
cwd-prefix match (`grep -q "$SCRIPT_DIR/webapp"`). PID 85931's Vite belongs to this same
repository but a **different git worktree**, so it failed that match, was treated as a foreign
process, and hit the hard `exit 1` branch — on every launch, indefinitely. This machine runs ~90
concurrent worktrees created by agent sessions; any abandoned `npm run dev` bricks the launcher.

Compounding it: the check ran *after* all backends were started and waited on, so the `EXIT` trap
tore down three healthy services. That is why the daemon log reads like a daemon crash when the
daemon was never at fault — the failing component and the apparently-failing component were
different.

See `docs/known_issues/launcher_vite_port_sibling_worktree_lockout.md` for the full write-up.

## Changes

| File | Change |
|------|--------|
| `launch_webapp.command` | New `_pid_cwd` + `_repo_common_dir` helpers; `_check_vite_port` now decides ownership by git common dir instead of a cwd prefix; the check is pre-flighted before the first `_start_api` call |
| `tests/test_launch_webapp_vite_port_ownership.py` | New — 9 tests, real temp git repos/worktrees against the launcher's actual shell helpers, plus source-level guards for both halves of the fix |
| `docs/known_issues/launcher_vite_port_sibling_worktree_lockout.md` | New incident write-up |
| `docs/known_issues/README.md` | Index row |

### Why git common dir

`git rev-parse --path-format=absolute --git-common-dir` returns the **same** absolute path for a
repository's main checkout and every `git worktree add` of it, and something different for any
unrelated project. It is a strictly stronger ownership signal than a cwd prefix: it still refuses
every genuinely foreign process, while recognising our own disposable dev server wherever it was
started from.

It also fails in the safe direction. A non-directory, a non-checkout, or a git too old for
`--path-format` all produce an empty string, and the guard `[ -n "$this_repo" ]` sends that to the
conservative `exit 1` branch. The change can never kill a process it failed to identify.

## Verification

* **Against the real blocker:** PID 85931's cwd and the main checkout both resolve to
  `/Users/kevinlee/Stockpy-live/.git` → auto-heal. The old test said foreign.
* **Safety side:** `/tmp`, `$HOME`, a fresh unrelated git repo, a non-git directory and an empty
  path all still resolve foreign → `exit 1` preserved.
* **Regression proof:** reverting `launch_webapp.command` to its pre-fix state fails **8 of 9**
  new tests; restoring it passes 9/9. The test catches the bug rather than merely passing
  alongside it.
* `bash -n launch_webapp.command` clean.
* Webapp: 2057/2057 vitest tests pass, `typecheck` and `build` clean at HEAD — the frontend was
  ruled out, not assumed innocent.
* Structural guards: `test_measure_settings_census.py`, `test_no_missing_call_timeouts.py`,
  `test_pilots_strategy_matrix.py` → 95 passed.

## Deliberately not done

* **`_sweep_stray_ports` (the `--stop` path) was left narrow.** Stop should not kill backends
  belonging to an active agent session in another worktree that the operator never started. With
  start-side auto-healing, it no longer needs to.
* **PID 85931 was not killed.** The fixed launcher clears it automatically on the next launch; no
  process on the operator's machine was terminated during this investigation.
* **`scripts/com.investyo.stack.plist`'s `--`-inside-XML-comments** was found (expat-strict
  invalid, `plutil -lint` OK, no runtime effect) and left alone rather than editing a live launchd
  plist for a cosmetic issue.

## Code review outcome (the "little code review" half of the ask)

The last five merged PRs (`a339e786`, `5cda430e`, `cffed0e1`, `68fc893a`, `abd8f6dc`) were
reviewed front and back. **No defects found.** Checked specifically: context-outside-provider
throws, mock/live response-shape divergence, mount-time effects that can trigger a live Robinhood
login, CONSTRAINT #4 fabrication, CONSTRAINT #6 fail-closed behaviour, bare `os.environ` settings
reads, missing `timeout=` kwargs, and hardcoded SQLite `db_path` defaults. The Weekly Digest
feature's own follow-up PR had already closed the one live-login risk in
`pilots/sector_gap.py::find_underrepresented_sectors`.
