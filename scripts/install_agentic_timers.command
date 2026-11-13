#!/bin/bash
# =============================================================================
# install_agentic_timers.command: install the agentic Robinhood timers
# =============================================================================
#
# Installs (or removes) the two launchd jobs of the agentic Robinhood system:
#   • com.investyo.rh-agentic-cycle   every 15 min: gate, then the four agents
#                                     (Scout, Analyst, Risk, Trader)
#   • com.investyo.rh-agentic-review  weekdays 16:40: Agent 5 (Reviewer)
#
# Separate from install_schedule.command on purpose: that one never starts the
# trading agents. Run this only when you mean to.
#
# USAGE (from Terminal, or double-click for the default):
#   ./scripts/install_agentic_timers.command                 install both timers
#   ./scripts/install_agentic_timers.command --burnin        also set review mode + $50 cap
#   ./scripts/install_agentic_timers.command --burnin --cap 25
#   ./scripts/install_agentic_timers.command --check         show settings, change nothing
#   ./scripts/install_agentic_timers.command --uninstall     unload and remove both timers
#   --claude /path/to/claude   use this claude instead of the one found on PATH
#
# What it does:
#   1. Checks .venv (Python 3.12) and finds `claude` (PATH, then the usual
#      install folders). Refuses if there is no claude: the timers would fail.
#   2. --burnin only: sets ROBINHOOD_EXECUTION_MODE=review and the cap through
#      the runtime flags store, after you type REVIEW. It never sets live, and
#      refuses to touch a live setup.
#   3. Writes each plist to ~/Library/LaunchAgents with this repo's real path
#      and claude's folder first on PATH (scripts/agentic_timers.py render),
#      lints it, then unloads any old copy and loads the new one.
#   4. Prints what would still stop a cycle (mode off, no cap, queue writer not
#      primary, kill switch on).
#
# STOP everything: ./scripts/install_agentic_timers.command --uninstall
# (or the kill switch: python -m execution.kill_switch --activate).
#
# AGENTIC_TIMERS_DRY_RUN=1 renders into a temp folder and skips launchctl
# (used by tests; changes nothing on the Mac).
# =============================================================================

set -o pipefail

LABELS=(
    "com.investyo.rh-agentic-cycle"
    "com.investyo.rh-agentic-review"
)

ACTION="install"
BURNIN=0
CAP=""
CLAUDE_ARG=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --burnin) BURNIN=1 ;;
        --cap) shift; CAP="${1:-}"; [[ -z "$CAP" ]] && { echo "--cap needs a dollar amount"; exit 2; } ;;
        --claude) shift; CLAUDE_ARG="${1:-}"; [[ -z "$CLAUDE_ARG" ]] && { echo "--claude needs a path"; exit 2; } ;;
        --check) ACTION="check" ;;
        --uninstall) ACTION="uninstall" ;;
        -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
        *) echo "Unknown argument: $1 (see --help)"; exit 2 ;;
    esac
    shift
done
if [[ -n "$CAP" && "$BURNIN" != "1" ]]; then
    echo "--cap only applies with --burnin"
    exit 2
fi

DRY_RUN="${AGENTIC_TIMERS_DRY_RUN:-0}"

# ── Pause before a double-clicked window closes so errors stay visible ───────
_on_exit() {
    local _exit_code=$?
    echo ""
    echo "──────────────────────────────────────────────────────────────"
    if [[ "$_exit_code" == "0" ]]; then echo "  Done (exit 0)."; else echo "  Exited with code $_exit_code."; fi
    if [[ -t 0 && "$DRY_RUN" != "1" ]]; then
        read -r -s -n 1 -p "  Press any key to close this window…" _ 2>/dev/null || true
        echo ""
    fi
}
trap '_on_exit' EXIT

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [[ "$DRY_RUN" == "1" ]]; then
    LAUNCH_AGENTS_DIR="${AGENTIC_TIMERS_TARGET_DIR:-$(mktemp -d)}"
else
    LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
fi

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "  InvestYo: agentic Robinhood timers ($ACTION)"
printf "  %s\n" "$(date '+%Y-%m-%d  %H:%M:%S')"
echo "  Repo: $REPO_ROOT"
[[ "$DRY_RUN" == "1" ]] && echo "  DRY RUN: writing to $LAUNCH_AGENTS_DIR, no launchctl"
echo "══════════════════════════════════════════════════════════════"

# ── Uninstall needs no Python ────────────────────────────────────────────────
if [[ "$ACTION" == "uninstall" ]]; then
    for LABEL in "${LABELS[@]}"; do
        P="$LAUNCH_AGENTS_DIR/$LABEL.plist"
        if [[ -f "$P" ]]; then
            [[ "$DRY_RUN" == "1" ]] || launchctl unload "$P" 2>/dev/null || true
            rm -f "$P" && echo "Removed $LABEL"
        else
            echo "$LABEL was not installed"
        fi
    done
    echo "Timers removed. Settings (mode, cap) were not changed."
    exit 0
fi

# ── .venv Python 3.12 ────────────────────────────────────────────────────────
PYTHON="$REPO_ROOT/.venv/bin/python3"
if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: .venv not found at $PYTHON. Run ./setup.sh first."
    exit 1
fi
PY_VER="$("$PYTHON" --version 2>&1)"
if [[ "$PY_VER" != *"3.12."* && "$DRY_RUN" != "1" ]]; then
    echo "ERROR: .venv Python is $PY_VER; expected 3.12.x"
    exit 1
fi
HELPER=("$PYTHON" "$SCRIPT_DIR/agentic_timers.py")

if [[ "$ACTION" == "check" ]]; then
    echo ""
    "${HELPER[@]}" find-claude || true
    "${HELPER[@]}" check
    exit $?
fi

# ── claude ───────────────────────────────────────────────────────────────────
if [[ -n "$CLAUDE_ARG" ]]; then
    CLAUDE_BIN="$CLAUDE_ARG"
else
    CLAUDE_BIN="$("${HELPER[@]}" find-claude)" || {
        echo "ERROR: $CLAUDE_BIN"
        echo "Install Claude Code and log in, or pass --claude /path/to/claude."
        exit 1
    }
fi
if [[ ! -x "$CLAUDE_BIN" ]]; then
    echo "ERROR: $CLAUDE_BIN is not an executable file."
    exit 1
fi
echo "Using claude: $CLAUDE_BIN"

# ── --burnin: review mode + cap (typed confirmation in the helper) ───────────
if [[ "$BURNIN" == "1" ]]; then
    echo ""
    echo "── Burn-in settings ───────────────────────────────────────────"
    BURNIN_ARGS=(burnin)
    [[ -n "$CAP" ]] && BURNIN_ARGS+=(--cap "$CAP")
    if ! "${HELPER[@]}" "${BURNIN_ARGS[@]}"; then
        echo "Burn-in settings not applied; timers not installed."
        exit 1
    fi
fi

# ── Install each job ─────────────────────────────────────────────────────────
mkdir -p "$LAUNCH_AGENTS_DIR" "$REPO_ROOT/output"
for LABEL in "${LABELS[@]}"; do
    INSTALLED="$LAUNCH_AGENTS_DIR/$LABEL.plist"
    echo ""
    echo "── $LABEL ─────────────────────────────────────────────"
    if ! "${HELPER[@]}" render --label "$LABEL" --out "$INSTALLED" --claude "$CLAUDE_BIN"; then
        echo "ERROR: could not render $LABEL; nothing loaded."
        exit 1
    fi
    if command -v plutil >/dev/null 2>&1 && ! plutil -lint "$INSTALLED"; then
        echo "ERROR: $INSTALLED failed plutil -lint; nothing loaded."
        exit 1
    fi
    if [[ "$DRY_RUN" != "1" ]]; then
        launchctl unload "$INSTALLED" 2>/dev/null || true
        if ! launchctl load "$INSTALLED"; then
            echo "ERROR: launchctl load failed for $LABEL."
            exit 1
        fi
        launchctl list | grep "$LABEL" || echo "  (not listed yet; it appears after the next load/login)"
    fi
done

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "Installed. What the cycle will see:"
"${HELPER[@]}" check
echo ""
echo "Logs: $REPO_ROOT/output/agentic_cycle.out / .err and agentic_review.out / .err"
echo "Remove: ./scripts/install_agentic_timers.command --uninstall"
exit 0
