#!/bin/bash
# =============================================================================
# launch.command — InvestYo Quant Platform double-click launcher (macOS)
# =============================================================================
#
# Double-click this file from Finder (or the Dock) to open a Terminal window
# and start the advisory pipeline.
#
# ONE-TIME SETUP — run this command once in any Terminal:
#
#   chmod +x /Users/kevinlee/Desktop/Stockpy/launch.command
#
# TO ADD TO THE DOCK:
#   1. Drag launch.command to your Dock.
#   2. Right-click the icon → Options → Keep in Dock.
#
# =============================================================================
# CONFIGURATION — edit only this block
# =============================================================================
#
#   REFRESH_INTERVAL_SECONDS
#     > 0  →  python main.py --interval N
#              Pipeline refreshes every N seconds until you close the window.
#     = 0  →  python main.py
#              Runs one full cycle then exits.
#
REFRESH_INTERVAL_SECONDS=60
#
# =============================================================================
# END CONFIGURATION — do not edit below this line
# =============================================================================

# ── Always pause before the window auto-closes so you can read any errors ────
_on_exit() {
    local _exit_code=$?
    echo ""
    echo "──────────────────────────────────────────────────────────────"
    case "$_exit_code" in
        0)   echo "  Pipeline finished cleanly (exit 0)." ;;
        130) echo "  Stopped by keyboard interrupt (Ctrl+C)." ;;
        *)   echo "  Pipeline exited with code $_exit_code." ;;
    esac
    # read may fail when stdin is closed (e.g. window force-quit) — suppress
    read -r -s -n 1 -p "  Press any key to close this window…" _ 2>/dev/null || true
    echo ""
}
trap '_on_exit' EXIT

# ── Navigate to the project root (same folder as this script) ─────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  InvestYo Quant Platform"
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
    echo "    python3.12 -m venv .venv"
    echo "    ./.venv/bin/pip install -r requirements.txt"
    echo ""
    exit 1
fi

echo "  ✓  Python $PYTHON_FULL  (.venv)"

# ── Warn if .env is absent — non-fatal; main.py degrades gracefully ───────────
if [ ! -f ".env" ]; then
    echo ""
    echo "  ⚠  .env not found."
    echo "     Copy .env.example → .env and fill in your API keys."
    echo "     Continuing — FRED macro data, Robinhood, and Alpaca will be skipped."
fi

echo ""

# ── Launch ─────────────────────────────────────────────────────────────────────
# Non-numeric or zero REFRESH_INTERVAL_SECONDS → single-run mode.
if [ "${REFRESH_INTERVAL_SECONDS:-0}" -gt 0 ] 2>/dev/null; then
    echo "  ▶  Interval mode  (--interval ${REFRESH_INTERVAL_SECONDS}s)"
    echo "     The pipeline refreshes every ${REFRESH_INTERVAL_SECONDS} seconds."
    echo "     Close this window (or press Ctrl+C) to stop."
    echo ""
    python main.py --interval "${REFRESH_INTERVAL_SECONDS}"
else
    echo "  ▶  Single-run mode  (REFRESH_INTERVAL_SECONDS=0)"
    echo "     One full cycle, then the pipeline exits."
    echo ""
    python main.py
fi

# ── Daily briefing digest ──────────────────────────────────────────────────────
# Generates output/briefing_YYYY-MM-DD.md and prints it to this Terminal window
# so the operator sees a concise summary of the current regime, top actions, Δ
# since yesterday, any dead-lettered symbols, and the 30-day calibration score
# — without having to open the HTML report or the GUI.
echo ""
echo "──────────────────────────────────────────────────────────────────────"
echo "  Generating daily briefing…"
echo ""
python -m scripts.daily_briefing --print || true
