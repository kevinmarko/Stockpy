# InvestYo Quant Platform — developer convenience targets
#
# Usage:
#   make verify      env-var check → test suite → one live advisory cycle
#   make test        pytest only
#   make smoke       smoke tests only
#
# All targets assume Python 3.12 lives in .venv; run ./setup.sh first.

PYTHON := .venv/bin/python3

.PHONY: verify test smoke

# ── Main gate ─────────────────────────────────────────────────────────────────
verify: _check_env test _live_run

_check_env:
	@echo ""
	@echo "=== Step 1 / 3  Environment check ==="
	@$(PYTHON) -c "\
from dotenv import load_dotenv; load_dotenv(); \
import os; \
required = ['FRED_API_KEY']; \
optional = ['RH_USERNAME', 'RH_PASSWORD', 'RH_MFA_SECRET', 'NTFY_TOPIC', 'ALPACA_API_KEY']; \
missing_req = [k for k in required if not os.environ.get(k)]; \
missing_opt = [k for k in optional if not os.environ.get(k)]; \
[print(f'  MISSING (required): {k}') for k in missing_req]; \
[print(f'  missing (optional): {k}') for k in missing_opt]; \
print('  Env check OK' if not missing_req else ''); \
exit(1 if missing_req else 0) \
"

test:
	@echo ""
	@echo "=== Step 2 / 3  Test suite ==="
	@$(PYTHON) -m pytest -v --tb=short

smoke:
	@echo ""
	@echo "=== Pipeline smoke tests ==="
	@$(PYTHON) -m pytest tests/test_pipeline_smoke.py -v --tb=short

_live_run:
	@echo ""
	@echo "=== Step 3 / 3  Live one-cycle run ==="
	@$(PYTHON) -c "\
from alerting import setup_logging, summarize_run; \
import main; \
setup_logging(); \
result = main.run_once(); \
print(summarize_run(result)) \
"
	@echo ""
	@echo "=== verify complete ==="
