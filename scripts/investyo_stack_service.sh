#!/bin/bash
# =============================================================================
# investyo_stack_service.sh — always-on backend stack for the Pilots PWA
# =============================================================================
#
# Run by the launchd agent com.investyo.stack (RunAtLoad + KeepAlive), so it
# starts at login and is restarted on crash. It brings up the three backend
# processes the webapp reads from and keeps the pipeline collecting data:
#
#   * orchestrator daemon (FOREGROUND) — 5-min warm refresh cycles, and hosts
#       the Control API :8601 + Pilots API :8602 (PILOTS_API_ENABLED in .env).
#       Honors the DATA_FRESHNESS_TTL_SECONDS gate: an interval cycle that finds
#       the DB already fresh (<15 min) skips the network pull.
#   * data_api    :8603  (background)
#   * metrics_api :8604  (background)
#
# The daemon runs in the foreground: when it exits, this wrapper exits, the
# EXIT trap stops the two API children, and launchd (KeepAlive) restarts the
# whole stack. macOS /bin/bash is 3.2 — this script deliberately avoids bash-4
# constructs (no `wait -n`, no associative arrays).
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

# Stop background children (the two APIs) whenever this wrapper exits, so a
# launchd restart never leaves orphaned uvicorn processes holding :8603/:8604.
trap 'kill $(jobs -p) 2>/dev/null' EXIT INT TERM

echo "$(date '+%F %T')  Starting InvestYo stack (daemon + data_api + metrics_api)…"

# data_api (:8603) and metrics_api (:8604) — separate processes; the daemon
# cannot host them (its AST guard forbids the heavy-engine imports they need).
"$UVICORN" api.data_api:app    --port 8603 >> "$LOG_DIR/stack_data_api.log"    2>&1 &
"$UVICORN" api.metrics_api:app --port 8604 >> "$LOG_DIR/stack_metrics_api.log" 2>&1 &

# Orchestrator daemon in the FOREGROUND (NOT exec'd — control must return here
# so the EXIT trap can reap the two API children). --interval 300 = 5-min
# cadence; the freshness gate collapses pulls to at most one per
# DATA_FRESHNESS_TTL_SECONDS. When the daemon exits, this wrapper exits, the
# trap stops the APIs, and launchd (KeepAlive) restarts the whole stack.
"$PYTHON" -m desktop.orchestrator_daemon --interval 300 >> "$LOG_DIR/stack_daemon.log" 2>&1
