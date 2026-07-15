# Stockpy Audit Framework — README

Three files make up the audit framework for the **InvestYo Quant Platform
("Stockpy")** codebase:

| File | What it is |
|------|-----------|
| [`stockpy_audit_prompt.md`](stockpy_audit_prompt.md) | The 10-area audit **rubric** — a structured prompt for a human or AI reviewer, with a severity model and the codebase conventions to check against. |
| [`stockpy_codebase_auditor.py`](stockpy_codebase_auditor.py) | The executable **static auditor** — stdlib-only, read-only, non-destructive. Mechanizes the statically-checkable parts of the rubric. |
| `STOCKPY_AUDIT_README.md` | This usage guide. |

---

## Quick start

The auditor has **zero third-party dependencies** — it uses only the Python
standard library (`ast`, `re`, `json`, `pathlib`), so no install step is needed.

```bash
# Human-readable console report
python stockpy_codebase_auditor.py --root .

# Console report + full machine-readable JSON
python stockpy_codebase_auditor.py --root . --json audit_report.json

# Summary table only (no per-finding detail)
python stockpy_codebase_auditor.py --root . --quiet

# Include the test suite in the scan (excluded by default)
python stockpy_codebase_auditor.py --root . --include-tests

# Gate CI: exit non-zero if any HIGH-or-worse finding exists
python stockpy_codebase_auditor.py --root . --fail-on HIGH
```

> **Run it under the project's Python (3.12).** The auditor parses source with
> `ast`. Under an older interpreter, a valid 3.12-only construct (e.g. a backslash
> inside an f-string) is reported as a `parse_error` MEDIUM finding — that is an
> interpreter-version artifact, not a real defect. The finding message says so, and
> the report records the running Python version.

### CLI reference

| Flag | Default | Effect |
|------|---------|--------|
| `--root PATH` | `.` | Repository root to audit. |
| `--json PATH` | *(off)* | Write the full JSON report to `PATH`. |
| `--include-tests` | off | Also scan `tests/` and `test_*.py`. |
| `--fail-on {NONE,INFO,LOW,MEDIUM,HIGH,CRITICAL}` | `NONE` | Exit `1` if any finding at/above this severity exists. `NONE` never fails. |
| `--quiet` | off | Print only the summary table. |

**Exit codes:** `0` = below the `--fail-on` threshold · `1` = threshold met/exceeded
· `2` = bad invocation (e.g. `--root` does not exist).

---

## What it detects

| Severity | Category | Examples the script flags |
|----------|----------|---------------------------|
| 🔴 CRITICAL | Security | Hardcoded FRED key (32-hex), AWS keys, Postgres DSN with `user:pass@host`, committed private keys, `.env` not gitignored |
| 🟠 HIGH | Architecture / Execution | Cross-package circular import, order-execution verb defined outside `execution/` (advisory-quarantine violation) |
| 🟡 MEDIUM | Config / Robustness | Undeclared env var (read at runtime, absent from `settings.py` + `.env.example`), network/file I/O not inside try/except, fabricated-`0.0`-metric smell, parse errors |
| 🔵 LOW | Quality | Missing module docstring, thin type-hint coverage, undocumented public API, orphaned module, benign package re-export cycle, possible scaler leakage |

Each finding carries: **severity, category, check name, message, module, line
(when applicable), and a concrete remediation suggestion.**

### How it maps to the rubric

The script covers the mechanical parts of every area in `stockpy_audit_prompt.md`:

- **Area 1 Architecture** — circular-dependency SCC detection (Tarjan), orphan
  detection, with benign package/submodule re-export cycles auto-downgraded to LOW.
- **Area 2 Security** — regex secret scanning with an allowlist that excludes
  `os.environ`/`settings.`/`Field(...)`/fixtures/`…EXAMPLE` placeholders; `.env`
  gitignore check.
- **Area 3 Configuration** — undeclared-env-var cross-reference against `settings.py`
  and `.env.example`; `.env.example` presence.
- **Area 7 Execution safety** — order-verb quarantine check (AST-level `def` scan).
- **Area 8 Error handling** — unguarded network/file I/O via AST try-body ranges.
- **Area 9 Code quality** — docstring + type-hint coverage per module.
- **Area 10 Known issues** — targeted heuristics for fabricated metrics, scaler
  leakage, and conflicting Kelly implementations.

The **judgement-heavy areas** (4 Data Pipeline, 5 Strategy Layer, 6 Backtesting
correctness) are guided by the rubric prose and confirmed by a reviewer reading the
flagged code — the script surfaces pointers, a human/agent renders the verdict.

---

## Interpreting the output

The auditor is intentionally **honest, not alarmist**:

1. **A finding is a pointer, not a verdict.** Especially for Area-10 heuristics and
   circular-import findings, open the cited file and confirm before acting. The
   codebase legitimately uses lazy imports to break cycles and caches negative
   responses deliberately — statically-visible "cycles" are often already mitigated.

2. **Check against `CLAUDE.md` conventions first.** Many patterns that look like
   bugs are documented, deliberate decisions: the advisory quarantine, NaN-not-0.0
   discipline, and the `dividendYield` fraction / `debtToEquity` ×100 scale rules.
   The rubric's "Codebase conventions" section lists these — verify before reporting.

3. **A clean area is a real result.** If the security scan returns zero findings, the
   tree is clean of the patterns it checks — report that plainly rather than
   inventing an issue to fill the section.

4. **Triage by severity, then by category.** Findings are sorted CRITICAL → INFO so
   the console leads with what matters. The JSON preserves the same ordering.

### Example JSON shape

```json
{
  "tool": "stockpy_codebase_auditor",
  "version": "1.0.0",
  "generated_at": "2026-07-15T...Z",
  "root": "/path/to/Stockpy",
  "modules_scanned": 245,
  "severity_counts": {"CRITICAL": 0, "HIGH": 5, "MEDIUM": 16, "LOW": 55, "INFO": 0},
  "total_findings": 76,
  "findings": [
    {
      "severity": "HIGH",
      "category": "Architecture",
      "check": "circular_dependency",
      "message": "Circular import cycle: data.historical_store -> data.robinhood_portfolio -> data_engine -> data.historical_store",
      "module": "data/historical_store.py",
      "line": null,
      "suggestion": "Break the cycle with a lazy (in-function) import or a shared leaf module."
    }
  ]
}
```

---

## Priority action workflow

1. **Run** `python stockpy_codebase_auditor.py --root . --json audit_report.json`.
2. **Resolve every 🔴 CRITICAL immediately** — these are security/data-integrity
   issues (hardcoded secrets, committed `.env`, quarantine escapes). Rotate any
   exposed credential.
3. **Review 🟠 HIGH findings** against the architecture in `CLAUDE.md` — confirm each
   circular dependency's lazy-import mitigation and each order-verb location.
4. **Batch 🟡 MEDIUM findings** (undeclared env vars, unguarded I/O) into a hardening
   pass; add missing vars to `settings.py` + `.env.example`.
5. **Sweep 🔵 LOW findings** opportunistically (docstrings, type hints, dead code).
6. **Use the rubric** (`stockpy_audit_prompt.md`) to cover Areas 4/5/6 by hand — the
   correctness-heavy work the static pass can't finish alone.
7. **Gate CI** on the auditor once the tree is clean:
   `python stockpy_codebase_auditor.py --fail-on HIGH`.

---

## Extending the auditor

The auditor is a single, well-factored module. Common extension points:

- **Add a secret pattern:** append a `(name, regex, severity, suggestion)` tuple to
  `_SECRET_PATTERNS`. The `_SECRET_ALLOW` regex filters false positives — widen it if
  a new legitimate pattern trips the scanner.
- **Add an I/O signature:** extend `_IO_CALL_PATTERN` (e.g. a new HTTP client) so the
  unguarded-I/O check covers it.
- **Add a known-issue heuristic:** add a branch to `check_known_issues()`. Keep the
  severity honest — a *smell* is MEDIUM/LOW, not HIGH, unless it is a confirmed defect
  class.
- **Add a new area/check:** write a `check_<area>()` method, call it from `run()`, and
  emit findings via `self._add(...)`. Keep every finding anchored to a `module` and,
  where possible, a `line`.

The auditor is read-only and imports nothing from the code under audit (it parses
with `ast`), so it is always safe to run against a live working tree.

---

## Design guarantees

- **Stdlib-only** — runs in a bare venv / CI runner with no `pip install`.
- **Read-only & non-destructive** — never modifies, executes, or imports audited code.
- **Deterministic** — same tree in, same report out (findings sorted stably).
- **Honest** — reports what it finds; a clean check yields an empty finding list.
