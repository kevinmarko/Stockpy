#!/bin/bash
# =============================================================================
# install_schedule.command — install the unattended daily-advisory launchd job
# =============================================================================
#
# Double-click this file from Finder (or the Dock) to install the macOS
# launchd timer that runs the InvestYo advisory pipeline once each weekday
# pre-market (08:45 America/New_York) via the existing headless `main.py`.
#
# This is an OS timer ONLY — it invokes `.venv/bin/python3 main.py` on a
# schedule. It does NOT create any autonomous self-invoking agent loop.
#
# ONE-TIME SETUP — run this once in any Terminal:
#   chmod +x /Users/kevinlee/Desktop/Stockpy/scripts/install_schedule.command
#
# WHAT IT DOES:
#   1. Verifies .venv exists and Python is 3.12.x.
#   2. Copies scripts/com.investyo.daily-advisory.plist into
#      ~/Library/LaunchAgents/ (rewriting the WorkingDirectory / paths to THIS
#      repo's absolute location so it works regardless of where the repo lives).
#   3. `launchctl unload` any existing job, then `launchctl load` the new plist.
#   4. Prints status and pauses so you can read the output.
#
# TO UNINSTALL:
#   launchctl unload ~/Library/LaunchAgents/com.investyo.daily-advisory.plist
#   rm ~/Library/LaunchAgents/com.investyo.daily-advisory.plist
#
# =============================================================================

set -o pipefail

LABEL="com.investyo.daily-advisory"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
INSTALLED_PLIST="$LAUNCH_AGENTS_DIR/$LABEL.plist"

# ── Always pause before the window auto-closes so errors are visible ─────────
_on_exit() {
    local _exit_code=$?
    echo ""
    echo "──────────────────────────────────────────────────────────────"
    if [[ "$_exit_code" == "0" ]]; then
        echo "  Done (exit 0)."
    else
        echo "  Exited with code $_exit_code."
    fi
    read -r -s -n 1 -p "  Press any key to close this window…" _ 2>/dev/null || true
    echo ""
}
trap '_on_exit' EXIT

# ── Resolve repo root = parent of the scripts/ dir this file lives in ─────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_PLIST="$SCRIPT_DIR/$LABEL.plist"

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  InvestYo — install unattended daily-advisory schedule"
printf "  %s\n" "$(date '+%Y-%m-%d  %H:%M:%S')"
echo "  Repo: $REPO_ROOT"
echo "══════════════════════════════════════════════════════════════"
echo ""

# ── Verify .venv Python 3.12 ─────────────────────────────────────────────────
PYTHON="$REPO_ROOT/.venv/bin/python3"
if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: .venv not found at $PYTHON"
    echo "Run ./setup.sh first, then double-click install_schedule.command again."
    exit 1
fi
PY_VER="$("$PYTHON" --version 2>&1)"
if [[ "$PY_VER" != *"3.12."* ]]; then
    echo "ERROR: .venv Python is $PY_VER — expected 3.12.x"
    exit 1
fi
echo "Using $PY_VER"

if [[ ! -f "$SOURCE_PLIST" ]]; then
    echo "ERROR: plist not found at $SOURCE_PLIST"
    exit 1
fi

# ── Build the installed plist, rewriting all absolute paths to THIS repo ─────
mkdir -p "$LAUNCH_AGENTS_DIR"
mkdir -p "$REPO_ROOT/output"

# Rewrite every occurrence of the canonical repo path in the template with the
# real REPO_ROOT so the job is portable. Uses '|' as sed delimiter (paths have
# slashes). The template ships pointing at /Users/kevinlee/Desktop/Stockpy.
sed "s|/Users/kevinlee/Desktop/Stockpy|$REPO_ROOT|g" "$SOURCE_PLIST" > "$INSTALLED_PLIST"
echo "Wrote $INSTALLED_PLIST"

# ── Lint the installed plist before loading ──────────────────────────────────
if command -v plutil >/dev/null 2>&1; then
    if ! plutil -lint "$INSTALLED_PLIST"; then
        echo "ERROR: installed plist failed plutil -lint — aborting."
        exit 1
    fi
fi

# ── Unload any existing job, then load the new one ───────────────────────────
echo ""
echo "Reloading launchd job '$LABEL'…"
launchctl unload "$INSTALLED_PLIST" 2>/dev/null || true
if ! launchctl load "$INSTALLED_PLIST"; then
    echo "ERROR: launchctl load failed."
    exit 1
fi

echo ""
echo "Installed. Current status:"
launchctl list | grep "$LABEL" || echo "  (not yet listed — will appear after next login/load)"
echo ""
echo "The advisory will run each weekday at 08:45 local time."
echo "Logs: $REPO_ROOT/output/scheduled_advisory.out (and .err)"
echo ""
echo "To uninstall:"
echo "  launchctl unload $INSTALLED_PLIST && rm $INSTALLED_PLIST"

exit 0
