#!/bin/bash
# =============================================================================
# nightly_local_ci.sh: one nightly run of the full local CI suite
# =============================================================================
#
# Run once a day by the launchd timer scripts/com.investyo.nightly-local-ci.plist
# (never auto-installed; see docs/RUNBOOK.md §5.1b). It runs
#
#     make local-ci ARGS="--profile nightly"
#
# in the checkout this script lives in. That is the full pytest suite with no
# marker filter (network + slow tests included), the local replacement for the
# retired nightly.yml workflow. There is no GitHub Actions in this repo, by
# standing policy; this timer is local only.
#
# It tests the checkout exactly as it is: no fetch, no pull, no checkout. A red
# run can be upstream flake (the nightly suite hits live APIs); read the log.
#
# LOGS go under settings.LOCAL_DATA_ROOT (default ~/.stockpy_local), shared by
# every worktree, never inside the repo:
#     <LOCAL_DATA_ROOT>/logs/nightly_ci/nightly_ci_<YYYYmmdd_HHMMSS>.log   one per run
#     <LOCAL_DATA_ROOT>/logs/nightly_ci/latest.log                         symlink to the newest
#     <LOCAL_DATA_ROOT>/logs/nightly_ci/last_status                        "<stamp> exit=<rc> log=<path>"
# Run logs older than 30 days are deleted at the end of each run.
#
# Exit code: the exit code of `make local-ci` (0 = every step passed), or 1 if
# the log directory cannot be created.
# =============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1
cd "$REPO_ROOT" || exit 1

# Resolve settings.LOCAL_DATA_ROOT the way every store does (shell env > .env >
# default), so the logs land next to the DB, models and other logs. If the
# .venv is missing or the import fails, fall back to the env var or the
# field's documented default.
DATA_ROOT=""
PYTHON="$REPO_ROOT/.venv/bin/python3"
if [ -x "$PYTHON" ]; then
    DATA_ROOT="$(NO_VENV_REEXEC=1 "$PYTHON" -c 'from settings import settings; print(settings.LOCAL_DATA_ROOT)' 2>/dev/null | tail -n 1)"
fi
if [ -z "$DATA_ROOT" ]; then
    DATA_ROOT="${LOCAL_DATA_ROOT:-$HOME/.stockpy_local}"
fi

LOG_DIR="$DATA_ROOT/logs/nightly_ci"
mkdir -p "$LOG_DIR" || exit 1

STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="$LOG_DIR/nightly_ci_$STAMP.log"

{
    echo "nightly local CI: start $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo "repo: $REPO_ROOT"
    echo "head: $(git rev-parse --short HEAD 2>/dev/null || echo unavailable) ($(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unavailable))"
    echo "command: make local-ci ARGS=\"--profile nightly\""
    echo ""
} >"$LOG" 2>&1

make local-ci ARGS="--profile nightly" >>"$LOG" 2>&1
RC=$?

echo "" >>"$LOG"
echo "nightly local CI: end $(date '+%Y-%m-%d %H:%M:%S %Z') exit=$RC" >>"$LOG"

ln -sf "$LOG" "$LOG_DIR/latest.log"
echo "$STAMP exit=$RC log=$LOG" >"$LOG_DIR/last_status"

find "$LOG_DIR" -name 'nightly_ci_*.log' -type f -mtime +30 -delete 2>/dev/null

exit "$RC"
