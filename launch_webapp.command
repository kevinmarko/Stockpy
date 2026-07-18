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
#   * pilots_api   :8602  — REUSED if the orchestrator daemon already hosts it
#   * data_api     :8603  — started here if not already up
#   * metrics_api  :8604  — started here if not already up
#   * control_api  :8601  — the daemon's own status/trigger API (reused if up)
# and writes webapp/.env.local (token + base URLs) so the app points at them.
# Only backends THIS script starts are stopped on exit — a running daemon is
# left untouched.
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

# PIDs of backends THIS script starts (so the exit trap stops only those).
STARTED_PIDS=()

# ── Always pause before the window closes; stop only backends we started ─────
_on_exit() {
    local _exit_code=$?
    for pid in "${STARTED_PIDS[@]}"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null
        fi
    done
    echo ""
    echo "──────────────────────────────────────────────────────────────"
    case "$_exit_code" in
        0)   echo "  Pilots PWA stopped (exit 0)." ;;
        130) echo "  Stopped by keyboard interrupt (Ctrl+C)." ;;
        *)   echo "  Pilots PWA exited with code $_exit_code." ;;
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

# ── Ask: mock or live? (defaults to mock after 20s / on empty Enter) ─────────
echo ""
echo "  How would you like to run the Pilots PWA?"
echo "    [1] Mock data   — offline, zero-config (default)"
echo "    [2] Live data   — reads your real pipeline data via the backend APIs"
echo ""
read -r -t 20 -p "  Choice [1]: " MODE_CHOICE
echo ""
MODE_CHOICE="${MODE_CHOICE:-1}"

LIVE_MODE=false
[ "$MODE_CHOICE" = "2" ] && LIVE_MODE=true

if [ "$LIVE_MODE" = true ]; then
    # ── venv for the Python backends ─────────────────────────────────────────
    if [ ! -d ".venv" ]; then
        echo "  ERROR: .venv not found in $SCRIPT_DIR — create it with ./setup.sh"
        exit 1
    fi
    # shellcheck disable=SC1091
    source ".venv/bin/activate" || { echo "  ERROR: could not activate .venv"; exit 1; }
    if ! python -c "import uvicorn" 2>/dev/null; then
        echo "  ERROR: uvicorn not installed — run ./.venv/bin/pip install -r requirements.txt"
        exit 1
    fi
    [ -f ".env" ] || echo "  ⚠  .env not found — backends run with defaults (fail-open, no token)."

    mkdir -p /tmp/stockpy_webapp_logs
    echo ""
    echo "  ▶  Bringing up live backends (reusing anything already running)…"
    # pilots_api is normally hosted by the orchestrator daemon on :8602. Only
    # start a standalone one if nothing is answering there.
    _start_api "api.pilots_api:app"   8602 "pilots_api"
    _start_api "api.data_api:app"     8603 "data_api"
    _start_api "api.metrics_api:app"  8604 "metrics_api"
    if _port_up 8601; then
        echo "  ✓  control_api already up on :8601 (daemon)"
    else
        echo "  ·  control_api (:8601) not running — Pipeline Dashboard controls will be idle (daemon not started by this script)."
    fi

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

echo ""
if [ "$LIVE_MODE" = true ]; then
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
