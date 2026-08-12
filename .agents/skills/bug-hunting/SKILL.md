---
name: bug-hunting
description: >
  Run a systematic bug hunt across the Stockpy codebase using the unified
  Bug Hunter CLI (scripts/bug_hunter.py) and interpret the results.
  Use when asked to find bugs, run a quality audit, check for regressions,
  investigate an incident, or prepare a pre-merge quality gate.
---

# Bug Hunting Skill

This skill guides systematic bug detection, root-cause investigation, and
remediation across the InvestYo Quant Platform ("Stockpy").

## When to Trigger

Activate this skill when any of the following apply:

- The operator asks to **find bugs**, **run an audit**, or **hunt for regressions**
- You are about to **merge a PR** touching engines, signals, execution, sizing, or validation code
- An **incident** has occurred (crash, wrong signal, execution leak, data corruption)
- You've made changes to **multiple modules** and want a pre-commit quality gate
- The operator asks about the **health** of the codebase or **test coverage gaps**

## Quick Reference — Commands

```bash
# Quick scan (AST audit + webapp typecheck + targeted pytest + known issues)
python3 scripts/bug_hunter.py --quick

# Comprehensive scan (adds Gravity AI 94-step audit + validation reports)
python3 scripts/bug_hunter.py

# With JSON report for machine processing
python3 scripts/bug_hunter.py --json output/bug_hunt_report.json

# Adjust failure threshold (default: HIGH)
python3 scripts/bug_hunter.py --fail-on MEDIUM

# Include test files in AST audit scope
python3 scripts/bug_hunter.py --include-tests

# Run individual scanners directly:
python3 scripts/auditor/stockpy_codebase_auditor.py --root . --fail-on HIGH
python3 scripts/preflight_check.py --json
npm run --prefix webapp typecheck
pytest tests/test_quantitative_models.py  # lookahead bias perturbation
```

## How to Interpret Results

### Overall PASS / FAIL

The bug hunter exits `0` (PASS) only when **all** of these hold:
1. AST audit has no findings at or above `--fail-on` threshold
2. Webapp typecheck passes (or is SKIPPED because node_modules absent)
3. Preflight readiness check passes
4. Pytest suite passes
5. Gravity AI Review Suite passes (comprehensive mode only)

**SKIPPED** statuses (e.g. webapp node_modules not installed) do **not** cause
failure. **ERROR** statuses (subprocess crash, timeout) **do** cause failure —
a crash is never a silent pass.

### Severity Tiers

| Tier | When to Act |
|------|-------------|
| 🔴 CRITICAL | **Immediately.** Block release. Committed secrets, execution quarantine violation, lookahead bias in signals. |
| 🟠 HIGH | **< 24 hours.** Requires branch + PR. Circular imports, deployability gate regression, mock/live API drift. |
| 🟡 MEDIUM | **Scheduled sprint.** Undeclared env var, missing error state in UI, performance bottleneck. |
| 🔵 LOW | **Standard backlog.** Missing docstrings, help content anchor mismatch, type-hint gaps. |

### What Each Scanner Checks

| Scanner | Checks |
|---------|--------|
| **AST Auditor** | Secret leaks, execution quarantine violations, circular imports, undeclared env vars, unguarded I/O, code quality metrics |
| **Webapp Typecheck** | TypeScript compilation, mock/live API type parity |
| **Preflight Check** | Kill switch, DB existence, FRED key, Robinhood session, calibration drift, alert channels |
| **Pytest Suite** | Unit/integration tests, lookahead perturbation (quick mode includes `test_quantitative_models.py`) |
| **Gravity AI Suite** | 94+ deep platform audit steps: DB resilience, historical store routing, LLM commentary safety, Robinhood bridge, prompt registry |
| **Validation Reports** | Strategy deployability gate staleness (PBO/DSR/Sharpe/MaxDD) |

## Root-Cause Investigation Workflow

When a finding is surfaced:

1. **Do not mask the error.** Never add blanket `try/except`, dummy `0.0` returns, or comment out assertions.
2. **Inspect log evidence.** Read the actual stack trace, line numbers, and exception types.
3. **Trace upstream dataflow.** Verify data passes through `dto_models.py` DTOs. Check for NaN/Inf/zero-division.
4. **Check codebase constraints:**
   - Lazy import converted to top-level? → circular dependency
   - Env var read without `settings.py` declaration? → undeclared config
   - `robin_stocks` import outside `execution/`? → quarantine violation
   - `mockApi.ts` missing method matching `client.ts`? → API parity drift
5. **Write regression test** in `tests/test_<module>.py`.
6. **If Severity 1-2, log the incident** in `docs/incident_log.md` and optionally create `docs/known_issues/<date>_<name>.md`.

## Full Process Documentation

See [`docs/BUG_HUNTING_PROCESS.md`](../../docs/BUG_HUNTING_PROCESS.md) for the
complete 5-phase workflow, domain-specific checklists, and post-mortem templates.
