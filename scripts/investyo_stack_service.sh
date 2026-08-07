#!/bin/bash
# =============================================================================
# investyo_stack_service.sh — always-on backend stack for the Pilots PWA
# =============================================================================
#
# Run by the launchd agent com.investyo.stack (RunAtLoad + KeepAlive), so it
# starts at login and is restarted on crash. It brings up the three backend
# processes the webapp reads from and keeps the pipeline collecting data:
#
#   * orchestrator daemon (BACKGROUNDED + waited on, see below) — 5-min warm
#       refresh cycles, and hosts the Control API :8601 + Pilots API :8602
#       (PILOTS_API_ENABLED in .env). Honors the DATA_FRESHNESS_TTL_SECONDS
#       gate: an interval cycle that finds the DB already fresh (<15 min)
#       skips the network pull.
#   * data_api    :8603  (background)
#   * metrics_api :8604  (background)
#   * caddy       :8888  (background) — reverse proxy for Pilots PWA
#
# SIGNAL PATH (2026-07 fix — read before changing anything below)
# -----------------------------------------------------------------
# The daemon is started in the BACKGROUND and this script `wait`s on it,
# with an explicit trap that forwards SIGTERM/SIGINT to it and polls for it
# to actually finish before reaping the two API children. This replaced an
# earlier design that ran the daemon in the FOREGROUND — which looked
# equivalent but was NOT: it silently defeated launchd's own SIGTERM
# entirely. Two facts made that so, both confirmed:
#
#   1. launchd's SIGTERM targets the JOB'S ROOT PROCESS -- this wrapper
#      script's own pid -- not the daemon child (`AbandonProcessGroup` only
#      kicks in to sweep the rest of the process group AFTER the root
#      process has died).
#   2. bash DEFERS a trap's execution until the CURRENT FOREGROUND COMMAND
#      completes (verified empirically: a trap on a foregrounded `sleep 5`
#      did not fire until the sleep finished, even though the signal arrived
#      moments in; the SAME command backgrounded + `wait $PID` fired the
#      trap immediately on signal arrival).
#
# Combined, under the OLD foreground design: launchd sends SIGTERM to this
# wrapper -> bash queues the EXIT/TERM trap and does nothing, because it's
# blocked on the foregrounded daemon -> the daemon (not the job root) NEVER
# receives that SIGTERM at all -> at `ExitTimeOut` launchd SIGKILLs the whole
# process group outright, and the daemon's entire sigwait/_teardown
# apparatus never runs a single line. Backgrounding + `wait` fixes this: the
# trap now fires the instant the signal arrives, and explicitly forwards it
# to the daemon before waiting for a real graceful exit.
#
# macOS /bin/bash is 3.2 — this script deliberately avoids bash-4 constructs
# (no `wait -n`, no associative arrays); the poll loop below (rather than a
# second `wait $DAEMON_PID` inside the trap) is chosen for that reason — a
# `wait` on an already-signalled/reaped pid can return without blocking on
# this bash version, so an explicit `kill -0` poll (mirroring
# launch_app.command's own previous-instance-replace loop) is the reliable
# primitive here.
#
# macOS NOTE: launchd runs in a restricted TCC context. If this repo lives
# under ~/Desktop / ~/Documents / ~/Downloads, you MUST grant Full Disk Access
# to /bin/bash (System Settings → Privacy & Security → Full Disk Access) or the
# service dies with "Operation not permitted: .venv/pyvenv.cfg". See
# install_stack_service.command.
# =============================================================================

set -o pipefail

# Repo root = parent of this scripts/ dir.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || exit 1

PYTHON="$REPO_ROOT/.venv/bin/python3"
UVICORN="$REPO_ROOT/.venv/bin/uvicorn"
LOG_DIR="$REPO_ROOT/output"
mkdir -p "$LOG_DIR"

if [ ! -x "$PYTHON" ]; then
    echo "$(date '+%F %T')  FATAL: .venv python not found at $PYTHON — run ./setup.sh" >&2
    exit 1
fi

echo "$(date '+%F %T')  Starting InvestYo stack (daemon + data_api + metrics_api)…"

# data_api (:8603) and metrics_api (:8604) — separate processes; the daemon
# cannot host them (its AST guard forbids the heavy-engine imports they need).
"$UVICORN" api.data_api:app    --port 8603 >> "$LOG_DIR/stack_data_api.log"    2>&1 &
"$UVICORN" api.metrics_api:app --port 8604 >> "$LOG_DIR/stack_metrics_api.log" 2>&1 &
caddy run --config "$REPO_ROOT/scripts/Caddyfile" >> "$LOG_DIR/stack_caddy.log" 2>&1 &

# Orchestrator daemon, BACKGROUNDED (see the SIGNAL PATH comment at the top
# of this file for why this is deliberate, not equivalent to the previous
# foreground design). --interval 300 = 5-min cadence; the freshness gate
# collapses pulls to at most one per DATA_FRESHNESS_TTL_SECONDS.
"$PYTHON" -m desktop.orchestrator_daemon --interval 300 >> "$LOG_DIR/stack_daemon.log" 2>&1 &
DAEMON_PID=$!

# Idempotency guard -- mirrors this codebase's Python _torn_down pattern
# (app_shell.py, desktop/orchestrator_daemon.py): a signal arriving while
# `wait` below is blocked interrupts that `wait` and runs this trap; once it
# returns, execution resumes after `wait` and immediately reaches the
# script's natural end, which fires the EXIT trap a SECOND time. Without
# this guard the poll loop (and the kill calls) would run twice.
_TORN_DOWN=0

_teardown() {
    [ "$_TORN_DOWN" = "1" ] && return
    _TORN_DOWN=1
    # Forward the signal to the daemon -- harmless no-op if it has already
    # exited on its own (kill on a dead pid just returns nonzero).
    kill -TERM "$DAEMON_PID" 2>/dev/null
    # Bounded poll for the daemon's OWN graceful teardown to actually finish
    # (settings.DAEMON_SHUTDOWN_TIMEOUT_SECONDS, default 25s) plus a margin
    # -- mirrors desktop/engine_supervisor.py's stop_engine() daemon-mode
    # timeout (DAEMON_SHUTDOWN_TIMEOUT_SECONDS + 5s grace). 60 iterations *
    # 0.5s = 30s.
    for _ in $(seq 1 60); do
        kill -0 "$DAEMON_PID" 2>/dev/null || break
        sleep 0.5
    done
    # Reap the two background API uvicorns -- always, regardless of how the
    # daemon exited -- so a launchd restart never leaves them orphaned
    # holding :8603/:8604.
    kill $(jobs -p) 2>/dev/null
}
trap '_teardown' EXIT INT TERM

# Block here (interruptibly -- see the SIGNAL PATH comment above) until the
# daemon exits, whether from a delivered signal or a crash of its own. Once
# this returns, the EXIT trap's idempotency guard means _teardown() either
# already ran (signal path) or runs now for the first time (daemon crashed
# on its own) -- either way the two API children still get reaped.
wait "$DAEMON_PID"
