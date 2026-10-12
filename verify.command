#!/bin/bash
# =============================================================================
# verify.command — InvestYo Quant Platform pre-flight verify (macOS double-click)
# =============================================================================
#
# Double-click this file from Finder to run the three-step readiness check:
#   1. Env-var presence (FRED_API_KEY required; Robinhood / ntfy optional)
#   2. Full pytest suite
#   3. One dry-run pipeline cycle (scripts/step5_4_verify_cycle.py): queue mode
#      forced off, no orders, no pushes; prints the cycle's briefing summary
#
# ONE-TIME SETUP:
#   chmod +x /Users/kevinlee/Desktop/Stockpy/verify.command
#
# =============================================================================

# Change to repo directory regardless of where the script was launched from
cd "$(dirname "$0")" || exit 1

# ── Locate the .venv Python ──────────────────────────────────────────────────
PYTHON=".venv/bin/python3"

if [[ ! -f "$PYTHON" ]]; then
    echo "ERROR: .venv not found."
    echo "Run ./setup.sh first, then double-click verify.command again."
    echo ""
    read -rp "Press any key to close..." _
    exit 1
fi

# Verify Python version is 3.12.x
PY_VER=$("$PYTHON" --version 2>&1)
if [[ "$PY_VER" != *"3.12."* ]]; then
    echo "ERROR: .venv Python is $PY_VER — expected 3.12.x"
    echo "Recreate the venv with Python 3.12:"
    echo "  ./setup.sh --clean"
    echo ""
    read -rp "Press any key to close..." _
    exit 1
fi

echo "Using $PY_VER"
echo ""

# ── Step 1: Environment check ────────────────────────────────────────────────
echo "=== Step 1 / 3  Environment check ==="

ENV_OK=$("$PYTHON" - <<'PYEOF'
from dotenv import load_dotenv
load_dotenv()
import os, sys

required = ["FRED_API_KEY"]
optional = ["RH_USERNAME", "RH_PASSWORD", "RH_MFA_SECRET", "NTFY_TOPIC"]

missing_req = [k for k in required if not os.environ.get(k)]
missing_opt = [k for k in optional if not os.environ.get(k)]

for k in missing_req:
    print(f"  MISSING (required): {k}")
for k in missing_opt:
    print(f"  missing (optional): {k}")

if not missing_req and not missing_opt:
    print("  All env vars present — OK")
elif not missing_req:
    print("  Required env vars present; optional keys above are unset (advisory runs fine)")

sys.exit(1 if missing_req else 0)
PYEOF
)
ENV_STATUS=$?
echo "$ENV_OK"

if [[ $ENV_STATUS -ne 0 ]]; then
    echo ""
    echo "ERROR: Required env var(s) missing. Add them to .env and retry."
    echo ""
    read -rp "Press any key to close..." _
    exit 1
fi

echo ""

# ── Step 2: Test suite ───────────────────────────────────────────────────────
echo "=== Step 2 / 3  Test suite ==="
"$PYTHON" -m pytest -v --tb=short -n auto --dist loadgroup
PYTEST_STATUS=$?

if [[ $PYTEST_STATUS -ne 0 ]]; then
    echo ""
    echo "ERROR: Test suite failed (exit $PYTEST_STATUS). Fix failing tests before relying on this build."
    echo ""
    read -rp "Press any key to close..." _
    exit 1
fi

echo ""

# ── Step 3: One dry-run pipeline cycle ───────────────────────────────────────
echo "=== Step 3 / 3  One dry-run pipeline cycle (queue mode forced off; no orders, no pushes) ==="
# The helper loads .env, forces DAEMON_AGENTIC_QUEUE_MODE=off in-process, runs
# main_orchestrator.main(dry_run=True), and exits non-zero (with an explicit
# reason) if the cycle failed or did not refresh state_snapshot.json.
"$PYTHON" scripts/step5_4_verify_cycle.py
LIVE_STATUS=$?

echo ""
if [[ $LIVE_STATUS -eq 0 ]]; then
    echo "=== verify complete — all steps passed ==="
else
    echo "ERROR: verify cycle failed with status $LIVE_STATUS — see the reason printed above."
    echo "       The pipeline log is under \$LOCAL_DATA_ROOT/logs/investyo.log (default ~/.stockpy_local/logs)."
fi

echo ""
read -rp "Press any key to close..." _
exit "$LIVE_STATUS"
