# InvestYo Quant Platform — developer convenience targets
#
# Usage:
#   make verify      env-var check → test suite → one live advisory cycle
#   make test        pytest only (full suite -- no marker filter)
#   make ci          offline pytest only (mirrors GitHub Actions `test` job:
#                     -m "not network and not slow" --dist loadgroup)
#   make smoke       smoke tests only
#
#   make lint        ruff genuine-bug rules only (mirrors CI's Lint step)
#
# All targets assume Python 3.12 lives in .venv; run ./setup.sh first.
# A git worktree under .claude/worktrees/<name>/ has no .venv of its own, so
# PYTHON resolves: this checkout's .venv -> the main checkout's .venv (parent
# of `git rev-parse --git-common-dir`) -> see below. `make ci PYTHON=...`
# still overrides all of it.

_MAIN_ROOT := $(shell d=$$(git rev-parse --git-common-dir 2>/dev/null) && cd "$$d/.." && pwd)
_VENV_PYTHON := $(firstword $(wildcard .venv/bin/python3 $(if $(_MAIN_ROOT),$(_MAIN_ROOT)/.venv/bin/python3)))

# No .venv anywhere: fall back to system python3 but say so loudly -- this is
# the gate, and a silent fallback is how a run ends up under the wrong Python.
PYTHON := $(or $(_VENV_PYTHON),python3)
ifeq ($(_VENV_PYTHON),)
$(warning No .venv found here or in the main checkout; using system python3 ($(shell python3 -V 2>&1)). Run ./setup.sh or pass PYTHON=...)
endif

.PHONY: verify test ci smoke lint

# ── Main gate ─────────────────────────────────────────────────────────────────
verify: _check_env test _live_run

_check_env:
	@echo ""
	@echo "=== Step 1 / 3  Environment check ==="
	@$(PYTHON) -c "\
from dotenv import load_dotenv; load_dotenv(); \
import os; \
required = ['FRED_API_KEY']; \
optional = ['RH_USERNAME', 'RH_PASSWORD', 'RH_MFA_SECRET', 'NTFY_TOPIC']; \
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
	@$(PYTHON) -m pytest -v --tb=short -n auto --dist loadgroup

ci:
	@echo ""
	@echo "=== Offline test suite (mirrors GitHub Actions CI's \`test\` job) ==="
	@$(PYTHON) -m pytest -m "not network and not slow" -v --tb=short -n auto --dist loadgroup

smoke:
	@echo ""
	@echo "=== Pipeline smoke tests ==="
	@$(PYTHON) -m pytest tests/test_pipeline_smoke.py -v --tb=short

lint:
	@$(PYTHON) -m ruff check . --select=F821,F822,F823,E9

_live_run:
	@echo ""
	@echo "=== Step 3 / 3  Live one-cycle run ==="
	@$(PYTHON) -c "\
from dotenv import load_dotenv; load_dotenv(override=False); \
from alerting import setup_logging, summarize_run; \
import main; \
setup_logging(); \
result = main.run_once(); \
print(summarize_run(result)) \
"
	@echo ""
	@echo "=== verify complete ==="
