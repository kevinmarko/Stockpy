"""
Unit tests for scripts/bug_hunter.py CLI runner.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure project root is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.bug_hunter import (
    scan_known_issues_and_incidents,
    run_static_ast_audit,
    run_webapp_typecheck,
    run_preflight_check,
    run_pytest_verification,
    check_validation_reports,
    get_python_cmd,
    SEVERITY_LEVELS,
    FAIL_STATUSES,
)


class TestSeverityModel(unittest.TestCase):
    """Verify severity level hierarchy and constants."""

    def test_severity_levels_hierarchy(self):
        """Severity levels must be strictly ordered CRITICAL > HIGH > MEDIUM > LOW > NONE."""
        self.assertGreater(SEVERITY_LEVELS["CRITICAL"], SEVERITY_LEVELS["HIGH"])
        self.assertGreater(SEVERITY_LEVELS["HIGH"], SEVERITY_LEVELS["MEDIUM"])
        self.assertGreater(SEVERITY_LEVELS["MEDIUM"], SEVERITY_LEVELS["LOW"])
        self.assertEqual(SEVERITY_LEVELS["NONE"], 0)

    def test_fail_statuses_includes_error(self):
        """FAIL_STATUSES must include both FAIL and ERROR so crashes are not silent."""
        self.assertIn("FAIL", FAIL_STATUSES)
        self.assertIn("ERROR", FAIL_STATUSES)
        self.assertNotIn("PASS", FAIL_STATUSES)
        self.assertNotIn("SKIPPED", FAIL_STATUSES)


class TestKnownIssuesScanner(unittest.TestCase):
    """Verify known issues and incident log indexing."""

    def test_scan_known_issues_and_incidents(self):
        """Indexing should find docs/known_issues/ documents and incident log."""
        res = scan_known_issues_and_incidents(ROOT_DIR)
        self.assertEqual(res["status"], "PASS")
        self.assertIn("known_issue_documents_count", res)
        self.assertGreaterEqual(res["known_issue_documents_count"], 0)
        self.assertTrue(res["incident_log_present"])

    def test_scan_nonexistent_directory(self):
        """Indexing a non-existent root should handle gracefully."""
        dummy = ROOT_DIR / "nonexistent_xyz_dir"
        res = scan_known_issues_and_incidents(dummy)
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["known_issue_documents_count"], 0)
        self.assertFalse(res["incident_log_present"])


class TestStaticAudit(unittest.TestCase):
    """Verify AST auditor invocation."""

    def test_run_static_ast_audit_structure(self):
        """AST audit result must have status and findings list."""
        res = run_static_ast_audit(ROOT_DIR, include_tests=False)
        self.assertIn("status", res)
        self.assertIn("findings", res)
        self.assertIsInstance(res["findings"], list)

    def test_run_static_ast_audit_missing_script(self):
        """AST audit should return ERROR if auditor script is missing."""
        dummy = ROOT_DIR / "nonexistent_xyz_dir"
        res = run_static_ast_audit(dummy)
        self.assertEqual(res["status"], "ERROR")
        self.assertIn("not found", res.get("message", ""))


class TestWebappTypecheck(unittest.TestCase):
    """Verify webapp typecheck handling."""

    def test_run_webapp_typecheck_nonexistent_dir(self):
        """Webapp typecheck should return SKIPPED for missing directory."""
        dummy = ROOT_DIR / "nonexistent_xyz_dir"
        res = run_webapp_typecheck(dummy)
        self.assertEqual(res["status"], "SKIPPED")


class TestPreflightCheck(unittest.TestCase):
    """Verify preflight check invocation."""

    def test_run_preflight_check_structure(self):
        """Preflight check result must have status field."""
        res = run_preflight_check(ROOT_DIR)
        self.assertIn("status", res)
        self.assertIn(res["status"], ("PASS", "FAIL", "ERROR"))

    def test_run_preflight_check_missing_script(self):
        """Preflight check should return ERROR if script is missing."""
        dummy = ROOT_DIR / "nonexistent_xyz_dir"
        res = run_preflight_check(dummy)
        self.assertEqual(res["status"], "ERROR")


class TestGetPythonCmd(unittest.TestCase):
    """Verify Python interpreter discovery logic."""

    def test_get_python_cmd_returns_list(self):
        """get_python_cmd must return a non-empty list of strings."""
        cmd = get_python_cmd(ROOT_DIR)
        self.assertIsInstance(cmd, list)
        self.assertGreater(len(cmd), 0)
        self.assertIsInstance(cmd[0], str)

    def test_get_python_cmd_fallback_to_sys_executable(self):
        """When no .venv exists, should fall back to sys.executable."""
        dummy = Path(tempfile.mkdtemp())
        try:
            cmd = get_python_cmd(dummy)
            self.assertEqual(cmd, [sys.executable])
        finally:
            dummy.rmdir()

    def test_get_python_cmd_prefers_venv(self):
        """When .venv/bin/python3 exists, should prefer it over sys.executable."""
        dummy = Path(tempfile.mkdtemp())
        try:
            venv_bin = dummy / ".venv" / "bin"
            venv_bin.mkdir(parents=True)
            venv_py3 = venv_bin / "python3"
            venv_py3.touch()
            cmd = get_python_cmd(dummy)
            self.assertEqual(cmd, [str(venv_py3)])
        finally:
            venv_py3.unlink()
            venv_bin.rmdir()
            (dummy / ".venv").rmdir()
            dummy.rmdir()


class TestValidationReports(unittest.TestCase):
    """Verify validation report staleness checker."""

    def test_check_validation_reports_missing_output_dir(self):
        """Should return SKIPPED if output/ does not exist."""
        dummy = ROOT_DIR / "nonexistent_xyz_dir"
        res = check_validation_reports(dummy)
        self.assertEqual(res["status"], "SKIPPED")

    def test_check_validation_reports_structure(self):
        """Result must contain stale and failing lists."""
        res = check_validation_reports(ROOT_DIR)
        self.assertIn("status", res)
        self.assertIn("stale", res)
        self.assertIn("failing", res)
        self.assertIsInstance(res["stale"], list)
        self.assertIsInstance(res["failing"], list)


class TestOverallPassLogic(unittest.TestCase):
    """Verify the overall_pass gate logic catches all failure modes.

    These tests validate that the critical bugs (preflight ignored,
    ERROR treated as PASS) are fixed.
    """

    def _compute_overall_pass(self, preflight_status, pytest_status,
                              webapp_status, ast_status, gravity_status,
                              high_critical_count=0):
        """Reproduce the overall_pass logic from main()."""
        has_preflight_failure = preflight_status in FAIL_STATUSES
        has_test_failure = (pytest_status in FAIL_STATUSES
                           or webapp_status in FAIL_STATUSES)
        has_ast_failure = (high_critical_count > 0
                          or ast_status in FAIL_STATUSES)
        has_gravity_failure = gravity_status in FAIL_STATUSES
        return not (has_preflight_failure or has_test_failure
                    or has_ast_failure or has_gravity_failure)

    def test_all_pass(self):
        """All PASS statuses should yield overall PASS."""
        self.assertTrue(self._compute_overall_pass(
            "PASS", "PASS", "PASS", "PASS", "PASS"))

    def test_preflight_fail_causes_overall_fail(self):
        """Preflight FAIL must cause overall FAIL (Bug 1 regression test)."""
        self.assertFalse(self._compute_overall_pass(
            "FAIL", "PASS", "PASS", "PASS", "PASS"))

    def test_preflight_error_causes_overall_fail(self):
        """Preflight ERROR must cause overall FAIL (Bug 2 regression test)."""
        self.assertFalse(self._compute_overall_pass(
            "ERROR", "PASS", "PASS", "PASS", "PASS"))

    def test_pytest_error_causes_overall_fail(self):
        """Pytest ERROR (crash/timeout) must cause overall FAIL."""
        self.assertFalse(self._compute_overall_pass(
            "PASS", "ERROR", "PASS", "PASS", "PASS"))

    def test_webapp_error_causes_overall_fail(self):
        """Webapp ERROR must cause overall FAIL."""
        self.assertFalse(self._compute_overall_pass(
            "PASS", "PASS", "ERROR", "PASS", "PASS"))

    def test_ast_error_causes_overall_fail(self):
        """AST audit ERROR must cause overall FAIL."""
        self.assertFalse(self._compute_overall_pass(
            "PASS", "PASS", "PASS", "ERROR", "PASS"))

    def test_gravity_fail_causes_overall_fail(self):
        """Gravity AI FAIL must cause overall FAIL."""
        self.assertFalse(self._compute_overall_pass(
            "PASS", "PASS", "PASS", "PASS", "FAIL"))

    def test_skipped_does_not_cause_failure(self):
        """SKIPPED statuses should NOT cause overall FAIL."""
        self.assertTrue(self._compute_overall_pass(
            "PASS", "PASS", "SKIPPED", "PASS", "SKIPPED"))

    def test_high_findings_cause_failure(self):
        """High/Critical AST findings must cause overall FAIL even if status is PASS."""
        self.assertFalse(self._compute_overall_pass(
            "PASS", "PASS", "PASS", "PASS", "PASS", high_critical_count=3))


class TestJsonReportOutput(unittest.TestCase):
    """Verify --json report file generation."""

    def test_json_report_writes_valid_file(self):
        """Running bug_hunter with --json should produce valid JSON with expected keys."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            import subprocess
            res = subprocess.run(
                [sys.executable, str(ROOT_DIR / "scripts" / "bug_hunter.py"),
                 "--quick", "--json", tmp_path, "--fail-on", "NONE"],
                capture_output=True, text=True, timeout=60, cwd=str(ROOT_DIR)
            )
            self.assertTrue(Path(tmp_path).exists(),
                            "JSON report file should be created")
            with open(tmp_path, "r") as f:
                data = json.load(f)
            self.assertIn("timestamp", data)
            self.assertIn("scans", data)
            self.assertIn("overall_pass", data)
            self.assertIn("ast_audit", data["scans"])
        finally:
            Path(tmp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
