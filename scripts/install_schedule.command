#!/bin/bash
# =============================================================================
# install_schedule.command — install the InvestYo launchd scheduled jobs
# =============================================================================
#
# Double-click this file from Finder (or the Dock) to install the macOS
# launchd timers:
#   • com.investyo.monthly-retrain — runs `python -m scripts.retrain_models`
#                                    on the 1st of each month at 04:00 local
#   • com.investyo.weekly-edgar    — runs `python -m scripts.backfill_edgar_
#                                    fundamentals --tickers all` each Sunday 02:00 local
#
# NOT installed by default: com.investyo.daily-advisory (`main.py` each weekday
# 08:45). The orchestrator daemon (com.investyo.stack) has been the advisory
# runtime and execution-queue writer since the 2026-10-12 cutover; reloading the
# old job would bring back a second writer. It is kept for rollback only
# (docs/RUNBOOK.md §3.15):
#   ./scripts/install_schedule.command --with-daily-advisory
#
# These are OS timers ONLY — they invoke the scripts on a schedule. They do
# NOT create any autonomous self-invoking agent loop. Retraining deliberately
# runs OUTSIDE the daily advisory cycle.
#
# ONE-TIME SETUP — run this once in any Terminal:
#   chmod +x /Users/kevinlee/Desktop/Stockpy/scripts/install_schedule.command
#
# WHAT IT DOES:
#   1. Verifies .venv exists and Python is 3.12.x.
#   2. Copies each scripts/com.investyo.*.plist into ~/Library/LaunchAgents/
#      (rewriting the WorkingDirectory / paths to THIS repo's absolute
#      location so it works regardless of where the repo lives).
#   3. `launchctl unload` any existing job, then `launchctl load` the new plist.
#   4. Prints status and pauses so you can read the output.
#
# TO UNINSTALL (per job):
#   launchctl unload ~/Library/LaunchAgents/com.investyo.monthly-retrain.plist
#   rm ~/Library/LaunchAgents/com.investyo.monthly-retrain.plist
#   launchctl unload ~/Library/LaunchAgents/com.investyo.weekly-edgar.plist
#   rm ~/Library/LaunchAgents/com.investyo.weekly-edgar.plist
#
# =============================================================================

set -o pipefail

LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

# Every launchd job this installer manages by default. Add a label here to install more.
LABELS=(
    "com.investyo.monthly-retrain"
    "com.investyo.weekly-edgar"
)
ROLLBACK_LABEL="com.investyo.daily-advisory"

WITH_DAILY_ADVISORY=0
for _arg in "$@"; do
    case "$_arg" in
        --with-daily-advisory) WITH_DAILY_ADVISORY=1 ;;
        *) echo "Unknown argument: $_arg (only --with-daily-advisory is accepted)"; exit 2 ;;
    esac
done

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

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  InvestYo — install unattended launchd schedules"
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

if [[ "$WITH_DAILY_ADVISORY" == "1" ]]; then
    # Rollback path (docs/RUNBOOK.md §3.15). Only safe once the daemon is no
    # longer the queue writer, or main.py and the daemon both write the queue.
    QUEUE_MODE="$(cd "$REPO_ROOT" && NO_VENV_REEXEC=1 "$PYTHON" -c \
        'from settings import settings; print(settings.DAEMON_AGENTIC_QUEUE_MODE)' 2>/dev/null)"
    # Fail closed: anything but off/shadow (incl. primary, or empty when
    # settings could not be read) refuses.
    case "$QUEUE_MODE" in
        off|shadow)
            echo "DAEMON_AGENTIC_QUEUE_MODE=$QUEUE_MODE: installing $ROLLBACK_LABEL (rollback)."
            LABELS+=("$ROLLBACK_LABEL") ;;
        *)
            echo "REFUSED: --with-daily-advisory needs DAEMON_AGENTIC_QUEUE_MODE=off or shadow"
            echo "(read: '${QUEUE_MODE:-<could not read settings>}'). Set the mode first so the"
            echo "daemon stops writing the queue (docs/RUNBOOK.md §3.15), then re-run this."
            exit 1 ;;
    esac
fi

mkdir -p "$LAUNCH_AGENTS_DIR"
mkdir -p "$REPO_ROOT/output"

# ── Install each job ─────────────────────────────────────────────────────────
for LABEL in "${LABELS[@]}"; do
    SOURCE_PLIST="$SCRIPT_DIR/$LABEL.plist"
    INSTALLED_PLIST="$LAUNCH_AGENTS_DIR/$LABEL.plist"

    echo ""
    echo "── $LABEL ─────────────────────────────────────────────────────"
    if [[ ! -f "$SOURCE_PLIST" ]]; then
        echo "ERROR: plist not found at $SOURCE_PLIST"
        exit 1
    fi

    # Rewrite every occurrence of the canonical repo path in the template with
    # the real REPO_ROOT so the job is portable. Uses '|' as sed delimiter
    # (paths have slashes). The templates ship pointing at
    # /Users/kevinlee/Desktop/Stockpy.
    sed "s|/Users/kevinlee/Desktop/Stockpy|$REPO_ROOT|g" "$SOURCE_PLIST" > "$INSTALLED_PLIST"
    echo "Wrote $INSTALLED_PLIST"

    # Lint the installed plist before loading.
    if command -v plutil >/dev/null 2>&1; then
        if ! plutil -lint "$INSTALLED_PLIST"; then
            echo "ERROR: installed plist failed plutil -lint — aborting."
            exit 1
        fi
    fi

    # Unload any existing job, then load the new one.
    echo "Reloading launchd job '$LABEL'…"
    launchctl unload "$INSTALLED_PLIST" 2>/dev/null || true
    if ! launchctl load "$INSTALLED_PLIST"; then
        echo "ERROR: launchctl load failed for $LABEL."
        exit 1
    fi
    launchctl list | grep "$LABEL" || echo "  (not yet listed — will appear after next login/load)"
done

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "Installed. Schedules:"
if [[ "$WITH_DAILY_ADVISORY" == "1" ]]; then
    echo "  • daily-advisory  — each weekday 08:45 local time (ROLLBACK: main.py writes the queue)"
fi
echo "  • monthly-retrain — 1st of the month, 04:00 local time"
echo "  • weekly-edgar    — each Sunday, 02:00 local time"
echo ""
echo "Logs:"
if [[ "$WITH_DAILY_ADVISORY" == "1" ]]; then
    echo "  $REPO_ROOT/output/scheduled_advisory.out (and .err)"
fi
echo "  $REPO_ROOT/output/scheduled_retrain.out (and .err)"
echo "  $REPO_ROOT/output/scheduled_edgar.out (and .err)"
echo ""
echo "To uninstall a job:"
echo "  launchctl unload ~/Library/LaunchAgents/<label>.plist && rm ~/Library/LaunchAgents/<label>.plist"

exit 0
