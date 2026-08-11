#!/usr/bin/env python3
"""
Stockpy Bug Hunter CLI - Unified Bug Detection & Audit Runner
==============================================================

Orchestrates static AST code auditing, security secret scanning,
Gravity AI Review Suite, preflight system checks, webapp typechecks,
test suite verification, validation report staleness checks, and
known issue indexing across the InvestYo Quant Platform.

Usage:
    python scripts/bug_hunter.py [--quick] [--json REPORT_PATH] [--fail-on SEVERITY]

Options:
    --quick            Run fast static audits only (skips Gravity AI, full pytest, validation).
    --json PATH        Save machine-readable JSON bug hunting report to PATH.
    --fail-on SEVERITY Exit 1 if findings at or above SEVERITY exist (CRITICAL, HIGH, MEDIUM, LOW, NONE). Default: HIGH.
    --include-tests    Include test suite in static AST audit scan.
"""

import argparse
import glob
import json
import os
import sys
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Any

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

SEVERITY_LEVELS = {"CRITICAL": 50, "HIGH": 40, "MEDIUM": 30, "LOW": 20, "INFO": 10, "NONE": 0}
FAIL_STATUSES = ("FAIL", "ERROR")


def get_python_cmd(root_dir: Path) -> List[str]:
    """Return command list for Python execution, preferring project .venv if available."""
    venv_py3 = root_dir / ".venv" / "bin" / "python3"
    venv_py = root_dir / ".venv" / "bin" / "python"
    if venv_py3.exists():
        return [str(venv_py3)]
    if venv_py.exists():
        return [str(venv_py)]
    return [sys.executable]


def run_static_ast_audit(root_dir: Path, include_tests: bool = False,
                         fail_on: str = "HIGH") -> Dict[str, Any]:
    """Run stockpy_codebase_auditor.py static AST scan."""
    auditor_script = root_dir / "scripts" / "auditor" / "stockpy_codebase_auditor.py"
    if not auditor_script.exists():
        return {"status": "ERROR", "message": "stockpy_codebase_auditor.py not found", "findings": []}

    py_cmd = get_python_cmd(root_dir)
    cmd = py_cmd + [str(auditor_script), "--root", str(root_dir),
                    "--fail-on", fail_on]
    if include_tests:
        cmd.append("--include-tests")

    fd, json_tmp_str = tempfile.mkstemp(dir=str(root_dir), prefix=".bug_hunter_ast_", suffix=".json")
    os.close(fd)
    json_tmp = Path(json_tmp_str)
    cmd.extend(["--json", str(json_tmp)])

    try:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            findings = []
            summary = {}
            if json_tmp.exists():
                with open(json_tmp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    findings = data.get("findings", [])
                    summary = data.get("summary", {})

            return {
                "status": "PASS" if res.returncode == 0 else "FAIL",
                "exit_code": res.returncode,
                "findings": findings,
                "summary": summary,
                "stdout": res.stdout,
                "stderr": res.stderr
            }
        except Exception as e:
            return {"status": "ERROR", "message": str(e), "findings": []}
    finally:
        json_tmp.unlink(missing_ok=True)


def run_webapp_typecheck(root_dir: Path) -> Dict[str, Any]:
    """Run webapp TypeScript typecheck."""
    webapp_dir = root_dir / "webapp"
    if not webapp_dir.exists():
        return {"status": "SKIPPED", "message": "webapp directory not found", "errors": []}

    node_modules = webapp_dir / "node_modules"
    if not node_modules.exists():
        return {"status": "SKIPPED", "message": "webapp/node_modules not installed (run `cd webapp && npm install`)", "errors": []}

    try:
        res = subprocess.run(
            ["npm", "run", "typecheck"],
            cwd=str(webapp_dir),
            capture_output=True,
            text=True,
            timeout=60
        )
        return {
            "status": "PASS" if res.returncode == 0 else "FAIL",
            "exit_code": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr
        }
    except FileNotFoundError:
        return {"status": "ERROR", "message": "npm command not found", "errors": []}
    except subprocess.TimeoutExpired:
        return {"status": "ERROR", "message": "webapp typecheck timed out (60s)", "errors": []}
    except Exception as e:
        return {"status": "ERROR", "message": str(e), "errors": []}


def run_preflight_check(root_dir: Path) -> Dict[str, Any]:
    """Run preflight readiness check."""
    preflight_script = root_dir / "scripts" / "preflight_check.py"
    if not preflight_script.exists():
        return {"status": "ERROR", "message": "preflight_check.py not found"}

    py_cmd = get_python_cmd(root_dir)
    try:
        res = subprocess.run(
            py_cmd + [str(preflight_script), "--json"],
            capture_output=True,
            text=True,
            timeout=60
        )
        data = {}
        if res.stdout.strip():
            try:
                data = json.loads(res.stdout)
            except json.JSONDecodeError:
                pass

        return {
            "status": "PASS" if res.returncode == 0 else "FAIL",
            "exit_code": res.returncode,
            "details": data,
            "stdout": res.stdout if not data else "",
            "stderr": res.stderr
        }
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}


def run_pytest_verification(root_dir: Path, quick: bool) -> Dict[str, Any]:
    """Run pytest verification suite."""
    py_cmd = get_python_cmd(root_dir)
    if quick:
        # Quick targeted suite: contract tests + lookahead perturbation.
        # Deliberately excludes tests/test_bug_hunter.py: that file has an
        # integration test which shells out to `bug_hunter.py --quick`, and
        # that quick mode runs this very pytest step -- including
        # tests/test_bug_hunter.py -- so listing it here would make every
        # `--quick` run recursively re-spawn itself (bounded only by process
        # timeouts, not a real base case). Its self-test still runs as part
        # of the full (non-quick) suite / `make verify`.
        cmd = py_cmd + [
            "-m", "pytest",
            "tests/test_help_content.py",
            "tests/test_dto_boundary_contracts.py",
            "tests/test_quantitative_models.py",
            "-q",
        ]
    else:
        cmd = py_cmd + ["-m", "pytest", "-q"]

    try:
        res = subprocess.run(cmd, cwd=str(root_dir), capture_output=True, text=True, timeout=300)
        return {
            "status": "PASS" if res.returncode == 0 else "FAIL",
            "exit_code": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr
        }
    except subprocess.TimeoutExpired:
        return {"status": "ERROR", "message": "pytest suite timed out (300s)"}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}


def run_gravity_audit(root_dir: Path) -> Dict[str, Any]:
    """Run Gravity AI Review Suite (94+ platform audit steps).

    This is heavyweight — skipped in --quick mode.  Parses the JSON
    report emitted by the suite and flags any FAILED steps.
    """
    suite_script = root_dir / "Gravity AI Review Suite.py"
    if not suite_script.exists():
        return {"status": "ERROR", "message": "Gravity AI Review Suite.py not found",
                "failed_steps": [], "total_steps": 0}

    py_cmd = get_python_cmd(root_dir)
    try:
        res = subprocess.run(
            py_cmd + [str(suite_script)],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(root_dir),
        )

        # The suite prints JSON to stdout via export_machine_readable_report()
        report_data = {}
        if res.stdout.strip():
            # Find the last JSON object in stdout (suite may print other text first)
            for line in reversed(res.stdout.strip().splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        report_data = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue

        # Also check for Gravity_Verification_Report.json on disk
        report_file = root_dir / "Gravity_Verification_Report.json"
        if not report_data and report_file.exists():
            try:
                with open(report_file, "r", encoding="utf-8") as f:
                    report_data = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        failed_steps = []
        total_steps = 0
        if report_data:
            for key, val in report_data.items():
                if isinstance(val, dict) and "status" in val:
                    total_steps += 1
                    if val.get("status") == "FAILED":
                        failed_steps.append(key)

        overall_pass = report_data.get("overall_pass", len(failed_steps) == 0)

        return {
            "status": "PASS" if overall_pass and res.returncode == 0 else "FAIL",
            "exit_code": res.returncode,
            "total_steps": total_steps,
            "failed_steps": failed_steps,
            "failed_count": len(failed_steps),
        }
    except subprocess.TimeoutExpired:
        return {"status": "ERROR", "message": "Gravity AI Review Suite timed out (600s)",
                "failed_steps": [], "total_steps": 0}
    except Exception as e:
        return {"status": "ERROR", "message": str(e),
                "failed_steps": [], "total_steps": 0}


def check_validation_reports(root_dir: Path) -> Dict[str, Any]:
    """Scan output/validation_*.json for staleness or failing deployability gates.

    This is informational — reports which strategies have stale or failing
    validation reports, but doesn't re-run the expensive harness.
    """
    output_dir = root_dir / "output"
    if not output_dir.exists():
        return {"status": "SKIPPED", "message": "output/ directory not found",
                "stale": [], "failing": [], "total": 0}

    validation_files = sorted(output_dir.glob("validation_*.json"))
    if not validation_files:
        return {"status": "SKIPPED", "message": "No validation report files found",
                "stale": [], "failing": [], "total": 0}

    now = time.time()
    stale_threshold = 30 * 24 * 3600  # 30 days
    stale = []
    failing = []

    for vf in validation_files:
        try:
            mtime = vf.stat().st_mtime
            age_days = (now - mtime) / 86400
            with open(vf, "r", encoding="utf-8") as f:
                data = json.load(f)

            strategy_name = vf.stem.replace("validation_", "")
            is_deployable = data.get("deployable", data.get("overall_pass", True))
            is_stale = age_days > 30

            if is_stale:
                stale.append({"strategy": strategy_name, "age_days": round(age_days, 1),
                              "file": vf.name})
            if not is_deployable:
                failing.append({"strategy": strategy_name, "file": vf.name,
                                "pbo": data.get("pbo"), "dsr": data.get("dsr"),
                                "sharpe": data.get("sharpe"), "max_dd": data.get("max_dd")})
        except (json.JSONDecodeError, OSError):
            stale.append({"strategy": vf.stem, "file": vf.name, "error": "unreadable"})

    return {
        "status": "PASS" if not stale and not failing else "WARN",
        "total": len(validation_files),
        "stale": stale,
        "stale_count": len(stale),
        "failing": failing,
        "failing_count": len(failing),
    }


def scan_known_issues_and_incidents(root_dir: Path) -> Dict[str, Any]:
    """Index known issue post-mortems and incident log status."""
    known_issues_dir = root_dir / "docs" / "known_issues"
    incident_log_file = root_dir / "docs" / "incident_log.md"

    issues = []
    if known_issues_dir.exists():
        for p in sorted(known_issues_dir.glob("*.md")):
            issues.append({"file": p.name, "path": str(p)})

    has_incident_log = incident_log_file.exists()
    return {
        "status": "PASS",
        "known_issue_documents_count": len(issues),
        "known_issues": issues,
        "incident_log_present": has_incident_log
    }


def main():
    parser = argparse.ArgumentParser(description="Stockpy Bug Hunter CLI Runner")
    parser.add_argument("--quick", action="store_true", help="Run fast static scans only")
    parser.add_argument("--json", type=str, help="Output full JSON report to specified file path")
    parser.add_argument("--fail-on", choices=list(SEVERITY_LEVELS.keys()), default="HIGH", help="Minimum severity to fail execution")
    parser.add_argument("--include-tests", action="store_true", help="Include test suite in static AST audit scan")
    args = parser.parse_args()

    start_time = time.time()
    print("==================================================================")
    print(" 🎯 STOCKPY BUG HUNTER — Automated Bug Detection & Quality Gate")
    print("==================================================================")
    print(f"Mode: {'Quick' if args.quick else 'Comprehensive'} | Fail-On Severity: {args.fail_on}")
    print(f"Root: {ROOT_DIR}\n")

    total_steps = 5 if args.quick else 7
    step = 0

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "root": str(ROOT_DIR),
        "quick": args.quick,
        "fail_on": args.fail_on,
        "scans": {}
    }

    # 1. Static AST Audit
    step += 1
    print(f"🔍 [{step}/{total_steps}] Running Static AST Code Auditor...")
    ast_res = run_static_ast_audit(ROOT_DIR, include_tests=args.include_tests,
                                   fail_on=args.fail_on)
    report["scans"]["ast_audit"] = ast_res
    print(f"   Status: {ast_res['status']} | Total Findings: {len(ast_res.get('findings', []))}")

    # 2. Webapp Typecheck
    step += 1
    print(f"🔍 [{step}/{total_steps}] Running Webapp TypeScript Typecheck...")
    webapp_res = run_webapp_typecheck(ROOT_DIR)
    report["scans"]["webapp_typecheck"] = webapp_res
    print(f"   Status: {webapp_res['status']}")

    # 3. Preflight Readiness Check
    step += 1
    print(f"🔍 [{step}/{total_steps}] Running Preflight Readiness Check...")
    preflight_res = run_preflight_check(ROOT_DIR)
    report["scans"]["preflight"] = preflight_res
    print(f"   Status: {preflight_res['status']}")

    # 4. Pytest Verification
    step += 1
    print(f"🔍 [{step}/{total_steps}] Running Pytest Suite ({'Quick' if args.quick else 'Full'})...")
    pytest_res = run_pytest_verification(ROOT_DIR, quick=args.quick)
    report["scans"]["pytest"] = pytest_res
    print(f"   Status: {pytest_res['status']}")

    # 5. Known Issues Index
    step += 1
    print(f"🔍 [{step}/{total_steps}] Indexing Known Issues & Incident Logs...")
    known_res = scan_known_issues_and_incidents(ROOT_DIR)
    report["scans"]["known_issues"] = known_res
    print(f"   Indexed Post-Mortems: {known_res['known_issue_documents_count']}")

    # 6. Gravity AI Review Suite (comprehensive mode only)
    gravity_res = {"status": "SKIPPED", "message": "Skipped in --quick mode"}
    if not args.quick:
        step += 1
        print(f"🔍 [{step}/{total_steps}] Running Gravity AI Review Suite (94+ audit steps)...")
        gravity_res = run_gravity_audit(ROOT_DIR)
        report["scans"]["gravity_audit"] = gravity_res
        failed_count = gravity_res.get("failed_count", 0)
        total_gravity = gravity_res.get("total_steps", 0)
        print(f"   Status: {gravity_res['status']} | {total_gravity} steps, {failed_count} failed")
    else:
        report["scans"]["gravity_audit"] = gravity_res

    # 7. Validation Report Staleness (comprehensive mode only)
    validation_res = {"status": "SKIPPED", "message": "Skipped in --quick mode"}
    if not args.quick:
        step += 1
        print(f"🔍 [{step}/{total_steps}] Checking Validation Report Staleness...")
        validation_res = check_validation_reports(ROOT_DIR)
        report["scans"]["validation_reports"] = validation_res
        print(f"   Status: {validation_res['status']} | {validation_res.get('total', 0)} reports, "
              f"{validation_res.get('stale_count', 0)} stale, {validation_res.get('failing_count', 0)} failing")
    else:
        report["scans"]["validation_reports"] = validation_res

    elapsed = round(time.time() - start_time, 2)
    report["elapsed_seconds"] = elapsed

    # Process findings and check severity threshold
    fail_threshold_val = SEVERITY_LEVELS[args.fail_on]
    high_critical_findings = []

    for f in ast_res.get("findings", []):
        sev = f.get("severity", "LOW")
        val = SEVERITY_LEVELS.get(sev, 0)
        if val >= fail_threshold_val and fail_threshold_val > 0:
            high_critical_findings.append(f)

    # Determine overall status — all gates checked, ERROR = failure
    has_preflight_failure = preflight_res["status"] in FAIL_STATUSES
    has_test_failure = (pytest_res["status"] in FAIL_STATUSES
                        or webapp_res["status"] in FAIL_STATUSES)
    has_ast_failure = (len(high_critical_findings) > 0
                       or ast_res["status"] in FAIL_STATUSES)
    has_gravity_failure = gravity_res["status"] in FAIL_STATUSES
    overall_pass = not (has_preflight_failure or has_test_failure
                        or has_ast_failure or has_gravity_failure)

    report["overall_pass"] = overall_pass

    print("\n==================================================================")
    print(" 📊 BUG HUNTER SUMMARY REPORT")
    print("==================================================================")
    print(f"Overall Result: {'✅ PASS' if overall_pass else '❌ FAIL'}")
    print(f"Elapsed Time:   {elapsed}s")
    print(f"AST Audit:      {ast_res['status']} ({len(ast_res.get('findings', []))} findings)")
    print(f"Webapp Parity:  {webapp_res['status']}")
    print(f"Preflight Gate: {preflight_res['status']}")
    print(f"Pytest Suite:   {pytest_res['status']}")
    print(f"Gravity Suite:  {gravity_res['status']}")
    print(f"Validation:     {validation_res['status']}")

    if high_critical_findings:
        print(f"\n⚠️  High/Critical Findings Matching Fail Threshold (>= {args.fail_on}):")
        for f in high_critical_findings:
            print(f"  - [{f.get('severity')}] {f.get('module')}:{f.get('line', '?')} -> {f.get('message')}")

    if gravity_res.get("failed_steps"):
        print(f"\n⚠️  Gravity AI Failed Steps ({len(gravity_res['failed_steps'])}):")
        for step_name in gravity_res["failed_steps"][:10]:
            print(f"  - {step_name}")
        if len(gravity_res["failed_steps"]) > 10:
            print(f"  ... and {len(gravity_res['failed_steps']) - 10} more")

    if validation_res.get("stale"):
        print(f"\n📅 Stale Validation Reports (> 30 days):")
        for s in validation_res["stale"]:
            print(f"  - {s['strategy']}: {s.get('age_days', '?')} days old")

    if args.json:
        out_p = Path(args.json)
        with open(out_p, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\n📁 Machine-readable JSON report written to: {out_p.resolve()}")

    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    # Venv re-exec + .env loading -- placed here (not at module top)
    # because this module is also imported as a library by
    # tests/test_bug_hunter.py; a module-top call would fire the
    # re-exec check on every such import, not just when this file is
    # the actual entry point. See scripts/_bootstrap.py's module
    # docstring for the full rationale.
    from scripts._bootstrap import bootstrap
    bootstrap()
    main()
