#!/bin/bash
# =============================================================================
# install_stack_service.command — install the always-on InvestYo backend stack
# =============================================================================
#
# Double-click from Finder (or the Dock) to install the launchd agent
# com.investyo.stack, which starts (at login, and restarts on crash) the
# always-on backend stack for the Pilots PWA:
#   • orchestrator daemon — 5-min warm refresh + Control API :8601 + Pilots :8602
#   • data_api    :8603
#   • metrics_api :8604
#
# WHAT IT DOES:
#   1. Verifies .venv exists and Python is 3.12.x.
#   2. Installs scripts/com.investyo.stack.plist into ~/Library/LaunchAgents/
#      (rewriting paths to THIS repo's location) and launchctl-loads it.
#   3. Unloads the legacy com.investyo.daily-advisory job — the always-on
#      daemon supersedes that single pre-market run (and it was failing under
#      macOS TCC anyway). Its plist is left in place so you can re-enable it.
#   4. Waits a few seconds and checks the service actually came up, calling out
#      the Full Disk Access fix if it hit the macOS ~/Desktop permission wall.
#
# UNINSTALL:
#   launchctl unload ~/Library/LaunchAgents/com.investyo.stack.plist
#   rm ~/Library/LaunchAgents/com.investyo.stack.plist
# =============================================================================

set -o pipefail
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
LABEL="com.investyo.stack"

_on_exit() {
    local _exit_code=$?
    echo ""
    echo "──────────────────────────────────────────────────────────────"
    [[ "$_exit_code" == "0" ]] && echo "  Done (exit 0)." || echo "  Exited with code $_exit_code."
    read -r -s -n 1 -p "  Press any key to close this window…" _ 2>/dev/null || true
    echo ""
}
trap '_on_exit' EXIT

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  InvestYo — install the always-on backend stack service"
printf "  %s\n" "$(date '+%Y-%m-%d  %H:%M:%S')"
echo "  Repo: $REPO_ROOT"
echo "══════════════════════════════════════════════════════════════"
echo ""

# ── Verify .venv Python 3.12 ─────────────────────────────────────────────────
PYTHON="$REPO_ROOT/.venv/bin/python3"
if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: .venv not found at $PYTHON — run ./setup.sh first."
    exit 1
fi
PY_VER="$("$PYTHON" --version 2>&1)"
if [[ "$PY_VER" != *"3.12."* ]]; then
    echo "ERROR: .venv Python is $PY_VER — expected 3.12.x"
    exit 1
fi
echo "Using $PY_VER"

chmod +x "$SCRIPT_DIR/investyo_stack_service.sh" 2>/dev/null || true
mkdir -p "$LAUNCH_AGENTS_DIR" "$REPO_ROOT/output"

# ── Retire the legacy (broken-under-TCC) daily-advisory job ──────────────────
DAILY="$LAUNCH_AGENTS_DIR/com.investyo.daily-advisory.plist"
if [[ -f "$DAILY" ]]; then
    echo ""
    echo "Retiring legacy com.investyo.daily-advisory (superseded by the always-on daemon)…"
    launchctl unload "$DAILY" 2>/dev/null || true
    echo "  Unloaded. (plist left at $DAILY — re-enable with 'launchctl load' if you want the daily Sheet publish back.)"
fi

# ── Install the stack service ────────────────────────────────────────────────
SOURCE_PLIST="$SCRIPT_DIR/$LABEL.plist"
INSTALLED_PLIST="$LAUNCH_AGENTS_DIR/$LABEL.plist"
if [[ ! -f "$SOURCE_PLIST" ]]; then
    echo "ERROR: plist not found at $SOURCE_PLIST"
    exit 1
fi

echo ""
echo "── $LABEL ─────────────────────────────────────────────────────"
sed "s|/Users/kevinlee/Desktop/Stockpy|$REPO_ROOT|g" "$SOURCE_PLIST" > "$INSTALLED_PLIST"
echo "Wrote $INSTALLED_PLIST"

if command -v plutil >/dev/null 2>&1; then
    if ! plutil -lint "$INSTALLED_PLIST"; then
        echo "ERROR: installed plist failed plutil -lint — aborting."
        exit 1
    fi
fi

echo "Loading launchd job '$LABEL'…"
launchctl unload "$INSTALLED_PLIST" 2>/dev/null || true
if ! launchctl load "$INSTALLED_PLIST"; then
    echo "ERROR: launchctl load failed for $LABEL."
    exit 1
fi

# ── Health check: did it actually come up? ───────────────────────────────────
echo ""
echo "Waiting for the stack to come up (up to ~25s; metrics_api imports engines)…"
UP=false
for _ in $(seq 1 50); do
    if curl -sf "http://localhost:8601/health" >/dev/null 2>&1 \
       && curl -sf "http://localhost:8603/health" >/dev/null 2>&1 \
       && curl -sf "http://localhost:8604/health" >/dev/null 2>&1; then
        UP=true; break
    fi
    sleep 0.5
done

echo ""
if [[ "$UP" == true ]]; then
    echo "✅ Stack is up: control_api :8601, data_api :8603, metrics_api :8604 (pilots :8602 via daemon)."
else
    echo "⚠️  The stack did not answer on all ports yet. Check the logs:"
    echo "     tail -n 40 $REPO_ROOT/output/stack_service.err"
    echo "     tail -n 40 $REPO_ROOT/output/stack_daemon.log"
    if grep -q "Operation not permitted" "$REPO_ROOT/output/stack_service.err" 2>/dev/null \
       || grep -q "pyvenv.cfg" "$REPO_ROOT/output/stack_service.err" 2>/dev/null; then
        echo ""
        echo "  ┌────────────────────────────────────────────────────────────┐"
        echo "  │ macOS blocked launchd from reading your .venv (TCC).        │"
        echo "  │ FIX: System Settings → Privacy & Security → Full Disk Access│"
        echo "  │      → enable /bin/bash, then re-run this installer.        │"
        echo "  │ (Your repo is under a protected folder like ~/Desktop.)     │"
        echo "  └────────────────────────────────────────────────────────────┘"
    fi
fi

echo ""
echo "Note: if you ALSO open launch_app.command, its engine loop will spawn a"
echo "second daemon that contends for :8601/:8602 (the later one yields its API"
echo "bind but the advisory still runs). For always-on, prefer THIS service and"
echo "use launch_webapp.command just to view the data."
exit 0
