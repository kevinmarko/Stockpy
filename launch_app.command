#!/bin/bash
# =============================================================================
# launch_app.command — InvestYo unified desktop app launcher (macOS)
# =============================================================================
#
# Double-click this file from Finder (or the Dock) to open a Terminal window
# and start the InvestYo unified desktop app (app_shell.py) — a single
# always-on native window (via pywebview) wrapping the full platform, with a
# background refresh loop. This replaces separately launching launch.command
# (headless pipeline) and launch_gui.command (browser-based Command Center)
# for day-to-day use; both remain valid standalone entry points.
#
# ONE-TIME SETUP — run this command once in any Terminal:
#
#   chmod +x /Users/kevinlee/Desktop/Stockpy/launch_app.command
#
# TO ADD TO THE DOCK:
#   1. Drag launch_app.command to your Dock.
#   2. Right-click the icon → Options → Keep in Dock.
#
# =============================================================================

# ── Always pause before the window auto-closes so you can read any errors ────
_on_exit() {
    local _exit_code=$?
    echo ""
    echo "──────────────────────────────────────────────────────────────"
    case "$_exit_code" in
        0)   echo "  InvestYo desktop app stopped (exit 0)." ;;
        130) echo "  Stopped by keyboard interrupt (Ctrl+C)." ;;
        *)   echo "  InvestYo desktop app exited with code $_exit_code." ;;
    esac
    # read may fail when stdin is closed (e.g. window force-quit) — suppress
    read -r -s -n 1 -p "  Press any key to close this window…" _ 2>/dev/null || true
    echo ""
}
trap '_on_exit' EXIT

# ── Navigate to the project root (same folder as this script) ─────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Shutdown grace window for a previous instance (2026-07 fix) ──────────────
# Must exceed the WORST CASE of app_shell.py's own _teardown(): stop_engine()
# (desktop/engine_supervisor.py) now waits up to
# settings.DAEMON_SHUTDOWN_TIMEOUT_SECONDS + 5s when ORCHESTRATOR_DAEMON_ENABLED
# is on (default 25 + 5 = 30s), plus stop_ui_server()'s own ~5-10s SIGTERM
# ->SIGKILL window -- ~35-40s worst case. The PREVIOUS flat 10s here was
# already right at the boundary for even the (unchanged) main.py --interval
# backend's 5+5=10s worst case, and would routinely cut the daemon backend's
# graceful teardown short. When nothing is mid-cycle, teardown is normally
# ~1s regardless, so this only lengthens the wait in the one case where
# waiting is actually the right thing to do.
SHUTDOWN_GRACE_SECONDS=40

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  InvestYo — starting unified desktop app…"
printf "  %s\n" "$(date '+%Y-%m-%d  %H:%M:%S')"
echo "  $SCRIPT_DIR"
echo "══════════════════════════════════════════════════════════════"
echo ""

# ── Stop any previous instance launched from this folder ─────────────────────
# Lets you just double-click this file again to "restart" — no need to find
# and close the old window/Terminal first. Scoped to a PID file written by
# THIS script (output/app_shell.pid, gitignored — see output/daemon.json for
# the same pattern) so it never touches an app_shell.py instance running from
# a different worktree/checkout on this machine.
PID_FILE="$SCRIPT_DIR/output/app_shell.pid"
if [ -f "$PID_FILE" ]; then
    OLD_PID="$(cat "$PID_FILE" 2>/dev/null)"
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "  ↺  Stopping previous instance (pid $OLD_PID)…"
        kill -TERM "$OLD_PID" 2>/dev/null
        _poll_count=$((SHUTDOWN_GRACE_SECONDS * 2))
        _elapsed=0
        for _ in $(seq 1 "$_poll_count"); do
            kill -0 "$OLD_PID" 2>/dev/null || break
            sleep 0.5
            _elapsed=$((_elapsed + 1))
            # Progress marker every ~5s so a genuinely long wait (a mid-cycle
            # daemon backend teardown) doesn't look like a hang.
            if [ $((_elapsed % 10)) -eq 0 ]; then
                echo "      Still waiting for the previous instance's pipeline cycle to finish… ($((_elapsed / 2))s)"
            fi
        done
        if kill -0 "$OLD_PID" 2>/dev/null; then
            echo "      Still running after ${SHUTDOWN_GRACE_SECONDS}s — forcing it closed."
            kill -KILL "$OLD_PID" 2>/dev/null
        fi
    fi
    rm -f "$PID_FILE"
fi

# ── Sync to the latest merged code (best-effort; never blocks the launch) ────
# Fast-forward only, exactly like the CLAUDE.md-documented post-merge sync —
# if local edits conflict with the pull, this just skips and runs whatever
# code is already here rather than touching your working tree.
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    UPSTREAM="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)"
    if [ -n "$UPSTREAM" ]; then
        echo "  ↻  Syncing with $UPSTREAM…"
        if git fetch --quiet 2>/dev/null && MERGE_OUT="$(git merge --ff-only "$UPSTREAM" 2>&1)"; then
            echo "  ✓  Up to date @ $(git rev-parse --short HEAD) ($(git rev-parse --abbrev-ref HEAD))"
        else
            echo "  ⚠  Could not fast-forward — running with the code already here."
            echo "     (local edits or diverged history; run 'git status' here to see why)"
        fi
    fi
fi
echo ""

# ── Guard 1: .venv must exist ─────────────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "  ERROR: Virtual environment (.venv) not found in:"
    echo "         $SCRIPT_DIR"
    echo ""
    echo "  Create it by opening Terminal and running:"
    echo ""
    echo "    cd \"$SCRIPT_DIR\""
    echo "    ./setup.sh"
    echo ""
    exit 1
fi

# ── Activate venv ─────────────────────────────────────────────────────────────
# shellcheck disable=SC1091
if ! source ".venv/bin/activate"; then
    echo "  ERROR: Could not activate .venv — try deleting and recreating it:"
    echo ""
    echo "    cd \"$SCRIPT_DIR\""
    echo "    rm -rf .venv"
    echo "    ./setup.sh"
    echo ""
    exit 1
fi

# ── Guard 2: Python interpreter must be exactly 3.12.x ───────────────────────
#
# This guard exists because a second Python (3.14) is also installed on this
# machine, and the wrong interpreter causes silent incompatibilities.
#
PYTHON_FULL=$(python --version 2>&1 | awk '{print $2}')   # e.g. "3.12.12"
PY_MAJOR=$(printf '%s' "$PYTHON_FULL" | cut -d. -f1)
PY_MINOR=$(printf '%s' "$PYTHON_FULL" | cut -d. -f2)

if [ "$PY_MAJOR" != "3" ] || [ "$PY_MINOR" != "12" ]; then
    echo "  ERROR: Wrong Python version detected."
    echo ""
    echo "  Found:    Python $PYTHON_FULL  (from .venv)"
    echo "  Required: Python 3.12.x"
    echo ""
    echo "  The .venv was created with the wrong interpreter."
    echo "  Fix it by running in Terminal:"
    echo ""
    echo "    cd \"$SCRIPT_DIR\""
    echo "    rm -rf .venv"
    echo "    ./setup.sh"
    echo ""
    exit 1
fi

echo "  ✓  Python $PYTHON_FULL  (.venv)"

# ── Guard 3: pywebview must be installed ──────────────────────────────────────
if ! python -c "import webview" 2>/dev/null; then
    echo "  ERROR: pywebview is not installed in .venv."
    echo "         Run: ./setup.sh"
    exit 1
fi

# ── Warn if .env is absent — non-fatal; engines degrade gracefully ────────────
if [ ! -f ".env" ]; then
    echo ""
    echo "  ⚠  .env not found."
    echo "     Copy .env.example → .env and fill in your API keys."
    echo "     Continuing — FRED macro data, Robinhood, and Alpaca will be skipped."
fi

echo ""
echo "  ▶  Starting InvestYo desktop app — one native window, always-on refresh."
echo "     Close the window (or press Ctrl+C here) to stop."
echo ""

# ── Launch the unified desktop app ────────────────────────────────────────────
mkdir -p "$(dirname "$PID_FILE")"
python app_shell.py &
APP_PID=$!
echo "$APP_PID" > "$PID_FILE"
wait "$APP_PID"
EXIT_CODE=$?
rm -f "$PID_FILE"
exit "$EXIT_CODE"
