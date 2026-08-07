#!/bin/bash
# =============================================================================
# launch_webapp.command — Stockpy Pilots PWA launcher (macOS)
# =============================================================================
#
# Double-click this file from Finder (or the Dock) to open a Terminal window
# and start the Pilots PWA (webapp/). Asks whether to run against offline
# MOCK data (default, zero-config) or LIVE data.
#
# In LIVE mode it wires the PWA to the real backends the app reads from:
#   * data_api     :8603  — started here if not already up
#   * metrics_api  :8604  — started here if not already up
#   * control_api  :8601  — the daemon's own status/trigger API
#   * pilots_api   :8602  — the daemon's own Pilots API
# control_api and pilots_api are normally hosted by ONE process: when .env has
# ORCHESTRATOR_DAEMON_ENABLED=true and neither port is already up, this script
# starts the real persistent orchestrator daemon (desktop/orchestrator_daemon.py,
# which hosts both) instead of two disconnected standalone stubs — a bare
# `uvicorn api.control_api:app` has no OrchestratorDaemon attached, so the
# webapp's Pipeline status page reads "daemon not reachable" even though a
# healthy process answers /health. Falls back to today's exact standalone-stub
# behavior when that flag is off, or when either port is already up (whatever
# is already there is reused as-is, never fought with).
# and writes webapp/.env.local (token + base URLs) so the app points at them.
# Only backends THIS script starts are stopped on exit — a running daemon is
# left untouched — and this script WAITS for its own backends to actually exit
# (not just signals them) before releasing its single-instance lock, so a
# second launch can never race a first one's still-dying processes. See the
# lock/wait-for-death comments below for the incident that motivated this: two
# overlapping launches once raced, the new one reused ports the old one was a
# moment from freeing, and the webapp was left pointed at three dead backends.
#
# LIVE mode reads whatever is already persisted (quant_platform.db / output/);
# it does NOT run the pipeline for you. If the pipeline hasn't produced data,
# screens show honest empty/404 states rather than fabricated numbers.
#
# ONE-TIME SETUP (already done, recorded for reference):
#   chmod +x /Users/kevinlee/Desktop/Stockpy/launch_webapp.command
# TO ADD TO THE DOCK: drag this file to the Dock → right-click → Options →
#   Keep in Dock.
# =============================================================================

# ── Repair PATH for GUI-launched contexts ────────────────────────────────────
# A double-clicked .app bundle (Stockpy Pilots.app, built by
# scripts/build_pilots_launcher.command) runs this script via AppleScript's
# `do shell script`, which uses a minimal, non-login PATH
# (/usr/bin:/bin:/usr/sbin:/sbin) — it never sources ~/.zprofile the way a
# real Terminal session does, so Homebrew's npm/node go missing even though
# they work fine when this same script is run from a normal terminal.
# Deliberately NOT a hand-rolled priority list of common install dirs — that
# was tried and got the ORDER wrong: it put /usr/local/bin ahead of
# /opt/homebrew/bin and silently picked up a stale Node v16 (a leftover 2021
# nodejs.org installer) instead of the real Homebrew v26 a normal terminal
# resolves to. Asking the user's own login shell for its actual PATH is the
# only way to get both the right directories AND the right order — it's
# exactly what ~/.zprofile's `eval "$(brew shellenv)"` line is for, and it's
# a no-op here whenever npm is already resolvable (i.e. every plain terminal
# double-click), so this only ever changes behavior in the one case it needs
# to.
if ! command -v npm >/dev/null 2>&1; then
    _login_path="$("${SHELL:-/bin/zsh}" -lc 'echo $PATH' 2>/dev/null)"
    [ -n "$_login_path" ] && PATH="$_login_path" && export PATH
    unset _login_path
fi

# PIDs of backends THIS script starts (so the exit trap stops only those).
STARTED_PIDS=()
# Budget (seconds) the exit trap waits for STARTED_PIDS to exit on their own
# before SIGKILLing survivors. Raised by _bring_up_control_and_pilots_api when
# it starts the real orchestrator daemon (as opposed to a standalone API
# stub) — the daemon's own graceful shutdown can legitimately take up to
# DAEMON_SHUTDOWN_TIMEOUT_SECONDS (default 25s), well beyond what a plain
# uvicorn stub needs.
BACKEND_SHUTDOWN_WAIT_SECONDS=15

# ── App mode (--live / --background / --stop) ─────────────────────────────────
# Invoked from the double-clickable "Stockpy Pilots.app" / "Stockpy Pilots
# (Stop).app" wrappers built by scripts/build_pilots_launcher.command.
#
# --live: what "Stockpy Pilots.app" runs. Opens a REAL, VISIBLE Terminal
# window (see build_pilots_launcher.command's _build_terminal_app) and skips
# the Mock/Live prompt (always Live) — otherwise it's byte-for-byte the same
# foreground bring-up as a plain double-click choosing Live. Closing that
# window (or Ctrl+C inside it) is the ENTIRE shutdown story: it routes
# through the same EXIT/TERM/HUP traps as every other foreground mode below,
# which already kill every backend this run started. No pidfile, no second
# icon required for the normal case.
#
# --stop: what "Stockpy Pilots (Stop).app" runs — now positioned as a SAFETY
# NET rather than the required shutdown step, for the case Terminal gets
# force-quit/crashes before a --live window's own close-triggered cleanup
# gets to run. See _app_stop()/_sweep_stray_ports() further down.
#
# --background: kept, unmodified, for backward compatibility — no visible
# window, hands backends off to a pidfile, exits immediately. No longer
# referenced by either app icon (confirmed nothing else in this repo calls
# it), but harmless to leave in place.
#
# A plain double-click with no args keeps today's exact interactive-Terminal
# behavior (Mock/Live prompt), byte-for-byte unchanged.
APP_MODE="${1:-}"
INTERACTIVE=true
APP_PID_FILE="/tmp/stockpy_webapp_logs/pilots_app.pids"
LOCK_FILE="/tmp/stockpy_webapp_logs/launch.lock"
if [ "$APP_MODE" = "--background" ] || [ "$APP_MODE" = "--stop" ]; then
    INTERACTIVE=false
    # No visible window in app mode — keep every echo so app_launcher.log is
    # still a real record, instead of output vanishing into nothing.
    mkdir -p /tmp/stockpy_webapp_logs
    exec >> /tmp/stockpy_webapp_logs/app_launcher.log 2>&1
    echo ""
    echo "── $(date '+%Y-%m-%d %H:%M:%S') — mode=$APP_MODE ──"
fi

_notify() {  # $1 = message ; best-effort macOS notification, never fatal
    command -v osascript >/dev/null 2>&1 &&
        osascript -e "display notification \"$1\" with title \"Stockpy Pilots\"" >/dev/null 2>&1
    return 0
}

# Echoes $LOCK_FILE's PID iff it's a LIVE launch_webapp.command process; silent
# (empty output) otherwise — a missing lock, a dead pid, or a pid recycled by
# an unrelated process since all read as "no genuine holder". Shared by
# _acquire_launch_lock (below) and --live's "already running" fast path, so
# the same-process verification logic exists in exactly one place.
_lock_holder_pid() {
    [ -f "$LOCK_FILE" ] || return 0
    local lock_pid lock_cmd
    lock_pid="$(cat "$LOCK_FILE" 2>/dev/null)"
    [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null || return 0
    lock_cmd="$(ps -p "$lock_pid" -ww -o command= 2>/dev/null)"
    [[ "$lock_cmd" == *"launch_webapp.command"* ]] && printf '%s' "$lock_pid"
    return 0
}

# ── Always pause before the window closes; stop only backends we started ─────
# Guarded with _cleaned so this can't run twice (see TERM/HUP traps below).
_cleaned=false
_on_exit() {
    local _exit_code=$?
    $_cleaned && return
    _cleaned=true
    # Signal everything we started, then WAIT for it to actually exit before
    # doing anything else — a bare `kill` returns immediately while uvicorn
    # (or the daemon) is still mid-shutdown (WebSocket teardown, the daemon's
    # own _teardown() sequence, etc. can take several seconds), and a NEW
    # launch_webapp.command invocation's "already up?" port check can't tell
    # a process that's about to die from one that's healthy. This wait is
    # what makes that distinction instead of leaving it to timing luck.
    local pid _i _still_alive
    for pid in "${STARTED_PIDS[@]}"; do
        [ -n "$pid" ] && kill "$pid" 2>/dev/null
    done
    for _i in $(seq 1 $((BACKEND_SHUTDOWN_WAIT_SECONDS * 2))); do
        _still_alive=false
        for pid in "${STARTED_PIDS[@]}"; do
            [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && _still_alive=true
        done
        $_still_alive || break
        sleep 0.5
    done
    # Anything still alive after its budget (a hung shutdown) gets no more
    # grace — better a hard kill than blocking this window forever.
    for pid in "${STARTED_PIDS[@]}"; do
        [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
    done
    # Safety-net sweep: if the window/session was torn down abruptly (force-quit,
    # crash) rather than via Ctrl+C, the foreground npm/vite tree can occasionally
    # survive as an orphan and squat on :5173 for the next launch. Path-scoped to
    # THIS project's vite binary only — never touches anything else.
    # SKIPPED on a clean --background hand-off (STARTED_PIDS was deliberately
    # cleared just before that exit 0, further down) — this sweep would
    # otherwise kill the very vite process that launch just started and
    # handed off to the pidfile for --stop to manage instead.
    if [ -n "$SCRIPT_DIR" ] && ! { [ "$APP_MODE" = "--background" ] && [ "$_exit_code" = "0" ]; }; then
        pkill -f "$SCRIPT_DIR/webapp/node_modules/.bin/vite" 2>/dev/null
    fi
    # Release the single-instance lock LAST, and only if it's still ours (a
    # PID match) — by this point every backend this instance started has
    # actually exited, not just been signaled, so the next launch's port
    # checks will see the truth instead of a dying process. See
    # _acquire_launch_lock for how this file is created.
    if [ -n "$LOCK_FILE" ] && [ -f "$LOCK_FILE" ] && [ "$(cat "$LOCK_FILE" 2>/dev/null)" = "$$" ]; then
        rm -f "$LOCK_FILE"
    fi
    echo ""
    echo "──────────────────────────────────────────────────────────────"
    case "$_exit_code" in
        0)   echo "  Pilots PWA stopped (exit 0)." ;;
        130) echo "  Stopped by keyboard interrupt (Ctrl+C)." ;;
        *)   echo "  Pilots PWA exited with code $_exit_code." ;;
    esac
    if [ "$INTERACTIVE" = true ]; then
        read -r -s -n 1 -p "  Press any key to close this window…" _ 2>/dev/null || true
        echo ""
    elif [ "$APP_MODE" = "--background" ] && [ "$_exit_code" != 0 ]; then
        # Only a genuine failure reaches this trap in background mode — the
        # success path clears STARTED_PIDS and exits 0 itself, further down,
        # before this trap would have anything to kill.
        _notify "Failed to start (exit $_exit_code) — see /tmp/stockpy_webapp_logs/app_launcher.log"
    fi
}
trap '_on_exit' EXIT
# TERM/HUP (window closed, session torn down) don't exit a script by default once
# trapped — route them through `exit` so the EXIT trap above still runs cleanup.
trap 'exit 143' TERM
trap 'exit 129' HUP

# ── Navigate to the project root (same folder as this script) ─────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── --stop: tear down whatever a prior --background launch started, then exit.
# Bypasses the launch lock entirely (stopping must work even if a stale lock
# is sitting there) and reuses the same wait-then-kill-9 grace period as the
# interactive trap above, just driven from a pidfile instead of STARTED_PIDS
# (this is a fresh process invocation — it never held those PIDs itself).
_read_env_value() {  # $1 = KEY ; echoes the value from ./.env (quotes stripped)
    local key="$1" line val
    [ -f ".env" ] || return 0
    line="$(grep -E "^${key}=" .env | tail -n 1)"
    val="${line#*=}"
    # strip surrounding whitespace and matching single/double quotes
    val="$(printf '%s' "$val" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    val="${val%\"}"; val="${val#\"}"
    val="${val%\'}"; val="${val#\'}"
    printf '%s' "$val"
}

# Stop-mode safety net for when there's no pidfile to work from — the --live
# mode (unlike the old --background mode) never writes $APP_PID_FILE at all,
# since a --live window's own EXIT/HUP trap handles its cleanup directly,
# in-process. If Terminal gets force-quit or crashes before that trap can
# run, nothing is left to tell a later --stop invocation what to kill. This
# sweeps this project's 5 known ports directly instead, generalizing
# _check_vite_port's own same-project verification (an owning process' cwd
# must match this checkout) from just :5173 to all five. Anything on these
# ports that ISN'T this project is reported and left strictly alone — same
# safety invariant _check_vite_port already guarantees for :5173 alone.
# Echoes nothing; returns 0 if it stopped >=1 of this project's processes,
# 1 if every port was either free or foreign. Defined here (ahead of
# _check_vite_port, further down) rather than next to its closest sibling,
# because _app_stop (right below) can be invoked as early as line ~296 —
# well before the script reaches _check_vite_port's own definition — and
# bash resolves a function call against whatever's already been defined by
# the time it's reached, not by where the call appears in the file.
_sweep_stray_ports() {
    local ports=(5173 8601 8602 8603 8604)
    local port pid cmd expect_dir own_pids=() found_any=false p _i _still_alive
    echo "  ▶  Checking ports 5173/8601-8604 for stray Pilots processes…"
    for port in "${ports[@]}"; do
        pid="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | head -n 1)"
        [ -z "$pid" ] && continue
        found_any=true
        expect_dir="$SCRIPT_DIR"
        [ "$port" = "5173" ] && expect_dir="$SCRIPT_DIR/webapp"
        if lsof -p "$pid" -a -d cwd 2>/dev/null | grep -q "$expect_dir"; then
            echo "  ⚠  :$port held by PID $pid (this project) — will stop it."
            # Same pid can legitimately own two ports (e.g. the orchestrator
            # daemon hosts both :8601 and :8602) — dedupe so it's only
            # signaled/waited-on once.
            case " ${own_pids[*]} " in *" $pid "*) ;; *) own_pids+=("$pid") ;; esac
        else
            cmd="$(ps -p "$pid" -ww -o command= 2>/dev/null)"
            echo "  ℹ  :$port is in use by PID $pid (${cmd:-unknown}) — not this project, leaving it alone."
        fi
    done
    if [ "${#own_pids[@]}" -eq 0 ]; then
        $found_any || echo "  ✓  No stray processes found on 5173/8601-8604."
        return 1
    fi
    for p in "${own_pids[@]}"; do kill "$p" 2>/dev/null; done
    for _i in $(seq 1 $((BACKEND_SHUTDOWN_WAIT_SECONDS * 2))); do
        _still_alive=false
        for p in "${own_pids[@]}"; do kill -0 "$p" 2>/dev/null && _still_alive=true; done
        $_still_alive || break
        sleep 0.5
    done
    for p in "${own_pids[@]}"; do kill -0 "$p" 2>/dev/null && kill -9 "$p" 2>/dev/null; done
    echo "  ✓  Stopped ${#own_pids[@]} stray process(es) from this project."
    return 0
}

_app_stop() {
    # Match the interactive path's own daemon-aware shutdown budget (see
    # _bring_up_control_and_pilots_api) — a session that started the real
    # orchestrator daemon needs the same up-to-DAEMON_SHUTDOWN_TIMEOUT_
    # SECONDS grace here too, or --stop would force-kill (-9) a daemon
    # that's still mid-_teardown() well before it's actually hung, instead
    # of only escalating to -9 once a genuinely-stuck shutdown has blown its
    # own advertised budget. Applies to both the pidfile path below AND
    # _sweep_stray_ports (which reads this same global).
    local daemon_timeout
    daemon_timeout="$(_read_env_value DAEMON_SHUTDOWN_TIMEOUT_SECONDS)"
    [[ "$daemon_timeout" =~ ^[0-9]+$ ]] || daemon_timeout=25
    BACKEND_SHUTDOWN_WAIT_SECONDS=$((daemon_timeout + 5))

    local did_pidfile_stop=false did_sweep_stop=false

    # Pidfile path: only ever populated by the legacy --background mode.
    if [ -f "$APP_PID_FILE" ]; then
        did_pidfile_stop=true
        echo "  ▶  Stopping backends listed in $APP_PID_FILE …"
        local pid _i _still_alive
        while read -r pid; do
            [ -n "$pid" ] && kill "$pid" 2>/dev/null
        done < "$APP_PID_FILE"
        for _i in $(seq 1 $((BACKEND_SHUTDOWN_WAIT_SECONDS * 2))); do
            _still_alive=false
            while read -r pid; do
                [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && _still_alive=true
            done < "$APP_PID_FILE"
            $_still_alive || break
            sleep 0.5
        done
        while read -r pid; do
            [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
        done < "$APP_PID_FILE"
        rm -f "$APP_PID_FILE"
    fi

    # Safety net: catches everything the pidfile path can't — a --live
    # Terminal window that was force-quit/crashed (that mode never writes a
    # pidfile at all, by design) as well as anything the pidfile above
    # missed. Always runs, regardless of whether the pidfile path found
    # anything, since the two are independent (e.g. an old --background
    # session's pidfile alongside an unrelated stray --live process).
    _sweep_stray_ports && did_sweep_stop=true

    pkill -f "$SCRIPT_DIR/webapp/node_modules/.bin/vite" 2>/dev/null
    rm -f "$LOCK_FILE"

    if [ "$did_pidfile_stop" = true ] || [ "$did_sweep_stop" = true ]; then
        echo "  ✓  Stopped."
        _notify "Pilots PWA stopped."
    else
        echo "  Nothing to stop — no pidfile, and none of ports 5173/8601-8604 are held by this project."
        _notify "Pilots PWA wasn't running."
    fi
    exit 0
}
if [ "$APP_MODE" = "--stop" ]; then
    _app_stop
fi

# ── --background fast path: already running (from a prior --background
# launch) → just reopen the tab instead of redoing the whole bring-up. Keeps
# a second "open" double-click from restarting an already-healthy session.
if [ "$APP_MODE" = "--background" ] && [ -f "$APP_PID_FILE" ] && curl -sf "http://localhost:5173/" >/dev/null 2>&1; then
    echo "  Already running — reopening the browser tab."
    open "http://localhost:5173"
    _notify "Pilots PWA is already running — http://localhost:5173"
    exit 0
fi

# ── --live fast path: already running (from a prior --live window still
# open) → just reopen the tab instead of starting a second session. Checks
# THREE things, not just :5173 — the single-instance lock is held by a real
# launch_webapp.command process, AND :5173 answers, AND :8602/health (the
# live pilots_api) answers. The third check is what distinguishes a genuine
# live session from a concurrently-running MOCK session, which would also
# hold the lock and serve :5173 but answer nothing on the backend ports —
# without it, a second Start click during an active mock session would
# silently reopen a tab that's still showing mock data under a "live" icon.
# When a mock session (or nothing) holds the lock, this intentionally falls
# through to _acquire_launch_lock below, which reports its own accurate
# "another launch already running" error instead.
if [ "$APP_MODE" = "--live" ]; then
    _live_holder_pid="$(_lock_holder_pid)"
    if [ -n "$_live_holder_pid" ] \
        && curl -sf "http://localhost:5173/" >/dev/null 2>&1 \
        && curl -sf "http://localhost:8602/health" >/dev/null 2>&1; then
        echo "  Already running live (PID $_live_holder_pid) — reopening the browser tab."
        open "http://localhost:5173"
        _notify "Pilots PWA is already running — http://localhost:5173"
        # Nothing was started or locked by THIS invocation -- skip the EXIT
        # trap's cleanup/pause entirely so this transient window doesn't sit
        # on "press any key" for no reason.
        trap - EXIT
        exit 0
    fi
    unset _live_holder_pid
fi

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  Stockpy Pilots PWA"
printf "  %s\n" "$(date '+%Y-%m-%d  %H:%M:%S')"
echo "  $SCRIPT_DIR/webapp"
echo "══════════════════════════════════════════════════════════════"
echo ""

# ── Guard: node/npm must be installed ─────────────────────────────────────────
if ! command -v npm >/dev/null 2>&1; then
    echo "  ERROR: npm was not found on your PATH."
    echo "         Install Node.js (https://nodejs.org) and try again."
    exit 1
fi
echo "  ✓  node $(node --version), npm $(npm --version)"

# ── Helpers ──────────────────────────────────────────────────────────────────
_port_up() {  # $1 = port ; returns 0 if /health answers
    curl -sf "http://localhost:$1/health" >/dev/null 2>&1
}

_start_api() {  # $1 = module:app ; $2 = port ; $3 = friendly name
    if _port_up "$2"; then
        echo "  ✓  $3 already up on :$2 (reusing)"
        return 0
    fi
    uvicorn "$1" --port "$2" > "/tmp/stockpy_webapp_logs/$3.log" 2>&1 &
    STARTED_PIDS+=("$!")
    local ok=false
    for _ in $(seq 1 40); do          # metrics_api imports heavy engines (~15s)
        if _port_up "$2"; then ok=true; break; fi
        sleep 0.5
    done
    if [ "$ok" = true ]; then
        echo "  ✓  $3 started on :$2"
    else
        echo "  ⚠  $3 did not answer on :$2 — see /tmp/stockpy_webapp_logs/$3.log"
    fi
}

# control_api (:8601) and pilots_api (:8602) are, in the fully-configured
# case, ONE process (desktop/orchestrator_daemon.py) rather than two
# independent uvicorn stubs — see the file header comment for why starting
# them as disconnected stubs silently breaks the webapp's Pipeline status
# page. This picks the right shape instead of always doing the simple thing.
_bring_up_control_and_pilots_api() {
    if _port_up 8601 || _port_up 8602; then
        # Something already answers on at least one of the two ports —
        # reuse it as-is (real daemon or standalone stub, doesn't matter),
        # exactly like every other _start_api call in this script.
        _start_api "api.pilots_api:app"   8602 "pilots_api"
        _start_api "api.control_api:app"  8601 "control_api"
        return 0
    fi

    local daemon_flag
    daemon_flag="$(_read_env_value ORCHESTRATOR_DAEMON_ENABLED)"
    daemon_flag="$(printf '%s' "$daemon_flag" | tr '[:upper:]' '[:lower:]')"
    if [ "$daemon_flag" != "true" ] && [ "$daemon_flag" != "1" ]; then
        # Flag is off — today's exact behavior, unchanged.
        _start_api "api.pilots_api:app"   8602 "pilots_api"
        _start_api "api.control_api:app"  8601 "control_api"
        return 0
    fi

    echo "  ▶  ORCHESTRATOR_DAEMON_ENABLED=true — starting the real orchestrator daemon…"
    local daemon_timeout
    daemon_timeout="$(_read_env_value DAEMON_SHUTDOWN_TIMEOUT_SECONDS)"
    [[ "$daemon_timeout" =~ ^[0-9]+$ ]] || daemon_timeout=25
    python -m desktop.orchestrator_daemon > "/tmp/stockpy_webapp_logs/orchestrator_daemon.log" 2>&1 &
    STARTED_PIDS+=("$!")
    BACKEND_SHUTDOWN_WAIT_SECONDS=$((daemon_timeout + 5))
    local ok=false
    for _ in $(seq 1 40); do          # engines warm on startup, can take several seconds
        if _port_up 8601; then ok=true; break; fi
        sleep 0.5
    done
    if [ "$ok" = true ]; then
        echo "  ✓  orchestrator daemon started (control_api :8601)"
    else
        echo "  ⚠  orchestrator daemon did not answer on :8601 — see /tmp/stockpy_webapp_logs/orchestrator_daemon.log"
    fi
    # PILOTS_API_ENABLED=false -> the daemon process doesn't host :8602 itself
    # (it stays a manually-launched standalone service by design); fall back
    # to starting it standalone, same as the non-daemon branch above.
    if _port_up 8602; then
        echo "  ✓  pilots_api already up on :8602 (hosted by the daemon)"
    else
        _start_api "api.pilots_api:app" 8602 "pilots_api"
    fi
}

# Loud, one-screen confirmation that live mode actually came up healthy —
# _start_api already warns per-service, but those warnings can scroll by
# unnoticed. This is the check that would have caught today's incident at
# launch time instead of only inside the webapp's Pipeline status page.
_verify_live_backends() {
    local all_ok=true p
    for p in 8601 8602 8603 8604; do
        if ! _port_up "$p"; then
            all_ok=false
            echo "  ⚠  :$p is still not answering — see its log in /tmp/stockpy_webapp_logs/"
        fi
    done
    if _port_up 8601; then
        local health
        health="$(curl -sf -m 3 "http://localhost:8601/health" 2>/dev/null)"
        if [[ "$health" == *'"daemon_alive":false'* ]]; then
            echo "  ℹ  control_api is up but no orchestrator daemon is attached (daemon_alive: false)."
            echo "     The webapp's Pipeline status will show \"not reachable\" until a real daemon"
            echo "     is running — set ORCHESTRATOR_DAEMON_ENABLED=true in .env, or start one"
            echo "     yourself: python -m desktop.orchestrator_daemon"
        fi
    fi
    if [ "$all_ok" = false ]; then
        echo ""
        echo "  ⚠  One or more live backends aren't responding — affected screens will show"
        echo "     honest empty/unreachable states rather than fabricated data."
    fi
}

# Vite runs with --strictPort (a silent port bump would break CORS against the
# backends, which are pinned to :5173 — see settings.CORS_ALLOWED_ORIGINS), so
# unlike the APIs above we can't just "reuse" a live server without risking a
# mock/live mode mismatch. Instead: detect a stale same-project instance and
# offer to clear it; for anything else, fail with a clear, actionable message
# instead of Vite's raw EADDRINUSE stack trace.
_check_vite_port() {
    local port=5173 pid cmd
    pid="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | head -n 1)"
    [ -z "$pid" ] && return 0

    cmd="$(ps -p "$pid" -ww -o command= 2>/dev/null)"

    # Auto-heal the common case: a leftover Vite dev server from THIS project's
    # own previous run (matched by process cwd) — never touches an unrelated
    # process. No prompt: it's our own disposable dev server, safe to recycle.
    if [[ "$cmd" == *"vite"* ]] && lsof -p "$pid" -a -d cwd 2>/dev/null | grep -q "$SCRIPT_DIR/webapp"; then
        echo "  ⚠  Port $port was held by a leftover Vite server from a previous run"
        echo "     of this project (PID $pid) — stopping it and continuing…"
        kill "$pid" 2>/dev/null
        for _ in $(seq 1 10); do
            lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1 || break
            sleep 0.3
        done
        if lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
            kill -9 "$pid" 2>/dev/null
            sleep 0.5
        fi
        if lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
            echo "  ERROR: PID $pid did not release port $port. Free it manually and re-run."
            exit 1
        fi
        echo "  ✓  Freed port $port."
        return 0
    fi

    echo ""
    echo "  ⚠  Port $port is already in use — Vite needs it (--strictPort) and"
    echo "     won't silently pick another port."
    echo "     PID $pid: ${cmd:-<unknown command>}"
    echo "  ERROR: this isn't a leftover server from this project — free port $port"
    echo "         yourself, then re-run this script, e.g.:"
    echo "         kill $pid"
    exit 1
}

# One launch_webapp.command instance at a time, full stop. Two overlapping
# launches raced in the wild: the OLD instance's exit-trap kill of its own
# pilots_api/data_api/metrics_api was still in flight the instant the NEW
# instance's "is this port already up?" check ran, so the new instance
# decided those still-dying processes were fine to reuse — moments before
# they actually died, leaving the new session's webapp pointed at three dead
# backends with no error anywhere except "daemon not reachable" on the
# Pipeline status screen. This makes "only one launch at a time" structural
# instead of timing luck. Ports are fixed (8601-8604, 5173) regardless of
# which checkout/worktree this script runs from, so the lock is intentionally
# one well-known path rather than scoped under SCRIPT_DIR.
_acquire_launch_lock() {
    mkdir -p /tmp/stockpy_webapp_logs
    local lock_pid
    lock_pid="$(_lock_holder_pid)"
    if [ -n "$lock_pid" ]; then
        echo ""
        echo "  ⚠  Another launch_webapp.command is already running (PID $lock_pid)."
        echo "     Close that window (or wait for it to fully exit) before starting a"
        echo "     new one — two at once races the backend startup and can leave the"
        echo "     daemon unreachable in the webapp."
        exit 1
    fi
    # A lock file that's present but not a genuine live holder (dead pid, or
    # the pid was recycled by an unrelated process since) is stale and
    # harmless — just overwrite it below.
    echo $$ > "$LOCK_FILE"
}
_acquire_launch_lock

# ── Ask: mock or live? (defaults to mock after 20s / on empty Enter) ─────────
# --background and --live both always run live — that's the whole point of
# either app wrapper; an operator who wants mock data still has the plain
# interactive launch.
if [ "$APP_MODE" = "--background" ] || [ "$APP_MODE" = "--live" ]; then
    MODE_CHOICE=2
else
    echo ""
    echo "  How would you like to run the Pilots PWA?"
    echo "    [1] Mock data   — offline, zero-config (default)"
    echo "    [2] Live data   — reads your real pipeline data via the backend APIs"
    echo ""
    read -r -t 20 -p "  Choice [1]: " MODE_CHOICE
    echo ""
fi
MODE_CHOICE="${MODE_CHOICE:-1}"

LIVE_MODE=false
[ "$MODE_CHOICE" = "2" ] && LIVE_MODE=true

if [ "$LIVE_MODE" = true ]; then
    # ── venv for the Python backends ─────────────────────────────────────────
    if [ ! -d ".venv" ]; then
        echo "  ▶  Creating .venv via ./setup.sh..."
        ./setup.sh || { echo "  ERROR: ./setup.sh failed"; exit 1; }
    fi
    # shellcheck disable=SC1091
    source ".venv/bin/activate" || { echo "  ERROR: could not activate .venv"; exit 1; }
    if ! python -c "import uvicorn" 2>/dev/null; then
        echo "  ERROR: uvicorn not installed — run ./setup.sh"
        exit 1
    fi
    [ -f ".env" ] || echo "  ⚠  .env not found — backends run with defaults (fail-open, no token)."

    mkdir -p /tmp/stockpy_webapp_logs
    echo ""
    echo "  ▶  Bringing up live backends (reusing anything already running)…"
    _start_api "api.data_api:app"     8603 "data_api"
    _start_api "api.metrics_api:app"  8604 "metrics_api"
    # control_api (:8601) + pilots_api (:8602) — see _bring_up_control_and_
    # pilots_api's own comment for why this isn't just two more _start_api
    # calls.
    _bring_up_control_and_pilots_api
    _verify_live_backends

    # ── Write webapp/.env.local so the PWA points at the live backends ───────
    TOKEN_VALUE="$(_read_env_value STATE_API_TOKEN)"
    {
        echo "# Auto-generated by launch_webapp.command (live mode). Safe to delete."
        echo "VITE_USE_MOCK=false"
        echo "VITE_API_BASE_URL=http://localhost:8602"
        echo "VITE_DATA_API_BASE_URL=http://localhost:8603"
        echo "VITE_METRICS_API_BASE_URL=http://localhost:8604"
        echo "VITE_CONTROL_API_BASE_URL=http://localhost:8601"
        echo "VITE_API_TOKEN=${TOKEN_VALUE}"
    } > webapp/.env.local
    if [ -n "$TOKEN_VALUE" ]; then
        echo "  ✓  webapp/.env.local written (token wired from STATE_API_TOKEN)"
    else
        echo "  ✓  webapp/.env.local written (no STATE_API_TOKEN in .env — reads are fail-open)"
    fi

    export VITE_USE_MOCK=false
fi

cd "$SCRIPT_DIR/webapp"

# ── Install webapp deps on first run ─────────────────────────────────────────
if [ ! -d "node_modules" ]; then
    echo ""
    echo "  ▶  First run — installing dependencies (npm install)…"
    npm install || { echo "  ERROR: npm install failed"; exit 1; }
fi

_check_vite_port

echo ""
if [ "$APP_MODE" = "--background" ]; then
    echo "  ▶  Starting the Pilots PWA against LIVE data in the background…"
    # --strictPort: a silent port bump would break CORS against the backends.
    npm run dev -- --strictPort > "/tmp/stockpy_webapp_logs/vite.log" 2>&1 &
    STARTED_PIDS+=("$!")
    ok=false
    for _ in $(seq 1 40); do
        curl -sf "http://localhost:5173/" >/dev/null 2>&1 && { ok=true; break; }
        sleep 0.5
    done
    if [ "$ok" != true ]; then
        echo "  ERROR: Pilots PWA did not come up on :5173 — see /tmp/stockpy_webapp_logs/vite.log"
        exit 1
    fi
    # Hand every backend PID this run started off to the pidfile so a later
    # --stop invocation (a fresh process, holding none of these PIDs itself)
    # can find and stop them — then clear STARTED_PIDS so the EXIT trap above
    # does NOT kill them when *this* launcher process exits a few lines down.
    printf '%s\n' "${STARTED_PIDS[@]}" > "$APP_PID_FILE"
    open "http://localhost:5173"
    echo "  ✓  Running at http://localhost:5173"
    _notify "Pilots PWA is running — http://localhost:5173"
    STARTED_PIDS=()
    exit 0
elif [ "$LIVE_MODE" = true ]; then
    echo "  ▶  Starting the Pilots PWA against LIVE data — opening in your browser."
    echo "     Must stay on :5173 for CORS (settings.CORS_ALLOWED_ORIGINS)."
    echo "     Close this window (or Ctrl+C) to stop (leaves any running daemon up)."
    echo ""
    # --strictPort: a silent port bump would break CORS against the backends.
    npm run dev -- --open --strictPort
else
    export VITE_USE_MOCK=true
    echo "  ▶  Starting the Pilots PWA against MOCK data — opening in your browser."
    echo "     Close this window (or Ctrl+C) to stop."
    echo ""
    npm run dev -- --open
fi
