#!/usr/bin/env bash
# scripts/refresh_validations.sh — Walk-forward validation cadence (monthly).
#
# Verifies .venv + Python 3.12, activates the venv, and runs the Python
# validation runner.  Exits non-zero on any validation failure so this can be
# used as a CI gate or scheduled via cron.
#
# Usage:
#   ./scripts/refresh_validations.sh
#   ./scripts/refresh_validations.sh --strategies rsi2_mean_reversion
#   ./scripts/refresh_validations.sh --start 2010-01-01 --end 2023-12-31
#
# Recommended monthly cron (edit with crontab -e):
#   0 6 1 * * cd /path/to/stockpy && ./scripts/refresh_validations.sh >> logs/validations.log 2>&1
#
# All positional arguments after the script name are forwarded verbatim to
# scripts/refresh_validations.py.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

# ── venv sanity check ────────────────────────────────────────────────────────
if [ ! -f ".venv/bin/python3" ]; then
    echo "ERROR: .venv not found at $REPO_ROOT/.venv" >&2
    echo "       Run ./setup.sh first to create the virtual environment." >&2
    exit 1
fi

PYTHON_VERSION=$(.venv/bin/python3 --version 2>&1)
if ! echo "$PYTHON_VERSION" | grep -qE "^Python 3\.12\b"; then
    echo "ERROR: Expected Python 3.12.x inside .venv but got: $PYTHON_VERSION" >&2
    exit 1
fi

# ── activate and run ─────────────────────────────────────────────────────────
# shellcheck disable=SC1091
source .venv/bin/activate

echo "================================================================"
echo "  InvestYo — Walk-Forward Validation Cadence"
echo "  Repo  : $REPO_ROOT"
echo "  Python: $($PYTHON_VERSION)"
echo "  Date  : $(date)"
echo "================================================================"
echo

python3 -m scripts.refresh_validations "$@"
STATUS=$?

echo
if [ "$STATUS" -eq 0 ]; then
    echo "✅  Validation cadence complete. All strategies passed."
    echo "   JSON reports written to $REPO_ROOT/reports/"
else
    echo "⚠️   Validation cadence complete. One or more strategies failed."
    echo "   Review the output above and check reports/ for detailed JSON summaries."
fi

exit $STATUS
