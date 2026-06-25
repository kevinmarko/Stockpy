#!/bin/bash
# =============================================================================
# launch_gui.command — InvestYo Command Center launcher (macOS)
# =============================================================================
#
# Double-click this file from Finder (or the Dock) to open a Terminal window
# and start the Streamlit Command Center (gui/app.py) in your default browser.
#
# ONE-TIME SETUP — run this command once in any Terminal:
#
#   chmod +x /Users/kevinlee/Desktop/Stockpy/launch_gui.command
#
# TO ADD TO THE DOCK:
#   1. Drag launch_gui.command to your Dock.
#   2. Right-click the icon → Options → Keep in Dock.
#
# The Command Center is the visual front-end over the same pipeline that
# launch.command runs headless. It launches main_orchestrator.py as a
# subprocess, never calls broker code directly, and edits only NON-secret
# .env tunables (secrets stay masked).
# =============================================================================

# ── Always pause before the window auto-closes so you can read any errors ────
_on_exit() {
    local _exit_code=$?
    echo ""
    echo "──────────────────────────────────────────────────────────────"
    case "$_exit_code" in
        0)   echo "  Command Center stopped (exit 0)." ;;
        130) echo "  Stopped by keyboard interrupt (Ctrl+C)." ;;
        *)   echo "  Command Center exited with code $_exit_code." ;;
    esac
    read -r -s -n 1 -p "  Press any key to close this window…" _ 2>/dev/null || true
    echo ""
}
trap '_on_exit' EXIT

# ── Navigate to the project root (same folder as this script) ─────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  InvestYo Command Center"
printf "  %s\n" "$(date '+%Y-%m-%d  %H:%M:%S')"
echo "  $SCRIPT_DIR"
echo "══════════════════════════════════════════════════════════════"
echo ""

# ── Guard 1: .venv must exist ─────────────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "  ERROR: Virtual environment (.venv) not found in:"
    echo "         $SCRIPT_DIR"
    echo ""
    echo "  Create it by opening Terminal and running:"
    echo ""
    echo "    cd \"$SCRIPT_DIR\""
    echo "    python3.12 -m venv .venv"
    echo "    ./.venv/bin/pip install -r requirements.txt"
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
    echo "    python3.12 -m venv .venv"
    echo "    ./.venv/bin/pip install -r requirements.txt"
    echo ""
    exit 1
fi

# ── Guard 2: Python interpreter must be exactly 3.12.x ───────────────────────
PYTHON_FULL=$(python --version 2>&1 | awk '{print $2}')   # e.g. "3.12.12"
PY_MAJOR=$(printf '%s' "$PYTHON_FULL" | cut -d. -f1)
PY_MINOR=$(printf '%s' "$PYTHON_FULL" | cut -d. -f2)

if [ "$PY_MAJOR" != "3" ] || [ "$PY_MINOR" != "12" ]; then
    echo "  ERROR: Wrong Python version detected."
    echo ""
    echo "  Found:    Python $PYTHON_FULL  (from .venv)"
    echo "  Required: Python 3.12.x"
    echo ""
    echo "  Fix it by running in Terminal:"
    echo ""
    echo "    cd \"$SCRIPT_DIR\""
    echo "    rm -rf .venv"
    echo "    python3.12 -m venv .venv"
    echo "    ./.venv/bin/pip install -r requirements.txt"
    echo ""
    exit 1
fi

echo "  ✓  Python $PYTHON_FULL  (.venv)"

# ── Guard 3: streamlit must be installed ──────────────────────────────────────
if ! python -c "import streamlit" 2>/dev/null; then
    echo "  ERROR: streamlit is not installed in .venv."
    echo "         Run: ./.venv/bin/pip install -r requirements.txt"
    exit 1
fi

# ── Warn if .env is absent — non-fatal; engines degrade gracefully ────────────
if [ ! -f ".env" ]; then
    echo ""
    echo "  ⚠  .env not found."
    echo "     Copy .env.example → .env and fill in your API keys."
    echo "     The Settings tab can edit non-secret tunables once .env exists."
fi

echo ""
echo "  ▶  Starting Command Center — it will open in your browser."
echo "     Close this window (or press Ctrl+C) to stop."
echo ""

# ── Launch the Streamlit Command Center ───────────────────────────────────────
streamlit run gui/app.py
