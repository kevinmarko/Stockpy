"""
tests/test_pilots_gravity_audit.py
====================================
Tests for ``pilots/gravity_audit.py`` (the legacy structural Gravity Review
Suite log reader) and ``GET /gravity/audit-status`` on ``api/pilots_api.py``
(the composite read-only port of ``gui/panels/gravity_audit.py``'s AI Gravity
audit runner section + legacy suite section).

No trigger endpoint exists for either audit — this file only exercises reads.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from settings import settings
from pilots import gravity_audit
import api.pilots_api as pilots_api

client = TestClient(pilots_api.app)


# ---------------------------------------------------------------------------
# pilots/gravity_audit.py — pure unit tests
# ---------------------------------------------------------------------------


class TestParseTrailingJson:
    def test_extracts_trailing_object(self):
        text = "some log noise\nmore noise\n" + json.dumps({"a": {"status": "PASSED"}})
        assert gravity_audit._parse_trailing_json(text) == {"a": {"status": "PASSED"}}

    def test_no_braces_returns_none(self):
        assert gravity_audit._parse_trailing_json("no json here at all") is None

    def test_unbalanced_braces_returns_none(self):
        assert gravity_audit._parse_trailing_json("garbage { not valid") is None

    def test_malformed_json_inside_braces_returns_none(self):
        assert gravity_audit._parse_trailing_json("{not: valid, json}") is None


class TestDeriveStepStatus:
    def test_status_field_passed(self):
        ok, status = gravity_audit._derive_step_status("step_1", {"status": "PASSED"})
        assert ok is True
        assert status == "PASSED"

    def test_status_field_failed(self):
        ok, status = gravity_audit._derive_step_status("step_2", {"status": "FAILED"})
        assert ok is False
        assert status == "FAILED"

    def test_overall_pass_true(self):
        ok, status = gravity_audit._derive_step_status("step_x", {"overall_pass": True})
        assert ok is True
        assert status == "PASSED"

    def test_overall_pass_false(self):
        ok, status = gravity_audit._derive_step_status("step_x", {"overall_pass": False})
        assert ok is False
        assert status == "FAILED"

    def test_discrepancy_analysis_special_case_perfect_alignment(self):
        ok, status = gravity_audit._derive_step_status(
            "step_3_5_discrepancy_analysis", {"conclusion": "Perfect Alignment"}
        )
        assert ok is True
        assert status == "Perfect Alignment"

    def test_discrepancy_analysis_special_case_mismatch(self):
        ok, status = gravity_audit._derive_step_status(
            "step_3_5_discrepancy_analysis", {"conclusion": "Discrepancy Found"}
        )
        assert ok is False

    def test_simulation_impact_special_case_ok(self):
        ok, status = gravity_audit._derive_step_status(
            "step_7_simulation_impact",
            {"vector_bt_status": "OK", "backtrader_status": "OK"},
        )
        assert ok is True
        assert status == "OK / OK"

    def test_simulation_impact_special_case_error(self):
        ok, status = gravity_audit._derive_step_status(
            "step_7_simulation_impact",
            {"vector_bt_status": "Error: boom", "backtrader_status": "OK"},
        )
        assert ok is False

    def test_unknown_shape_falls_through_to_sentinel(self):
        ok, status = gravity_audit._derive_step_status("mystery_step", {"foo": "bar"})
        assert ok is False
        assert status == "—"


class TestLegacyAuditStatus:
    def test_missing_log_returns_honest_unavailable(self, tmp_path):
        result = gravity_audit.legacy_audit_status(str(tmp_path / "nope.log"))
        assert result["available"] is False
        assert result["all_passed"] is None
        assert result["steps"] == []
        assert result["reason"]

    def test_log_with_no_parseable_json_stays_fail_closed(self, tmp_path):
        p = tmp_path / "gravity_run.log"
        p.write_text("Running step 3 of 7...\n(still going, no verdict yet)\n", encoding="utf-8")
        result = gravity_audit.legacy_audit_status(str(p))
        assert result["available"] is False
        assert result["all_passed"] is None
        assert "in progress" in result["reason"] or "did not finish" in result["reason"]

    def test_all_steps_passed(self, tmp_path):
        p = tmp_path / "gravity_run.log"
        report = {
            "step_1_schema": {"status": "PASSED"},
            "step_2_lookahead": {"overall_pass": True},
        }
        p.write_text("log preamble\n" + json.dumps(report), encoding="utf-8")
        result = gravity_audit.legacy_audit_status(str(p))
        assert result["available"] is True
        assert result["all_passed"] is True
        assert result["reason"] is None
        assert {r["step"] for r in result["steps"]} == {"step_1_schema", "step_2_lookahead"}
        assert all(r["passed"] for r in result["steps"])

    def test_one_step_failed_flips_all_passed_false(self, tmp_path):
        p = tmp_path / "gravity_run.log"
        report = {
            "step_1_schema": {"status": "PASSED"},
            "step_2_lookahead": {"status": "FAILED"},
        }
        p.write_text(json.dumps(report), encoding="utf-8")
        result = gravity_audit.legacy_audit_status(str(p))
        assert result["available"] is True
        assert result["all_passed"] is False
        failed = [r for r in result["steps"] if r["step"] == "step_2_lookahead"][0]
        assert failed["passed"] is False
        assert failed["status"] == "FAILED"

    def test_non_dict_values_are_skipped_not_crashed_on(self, tmp_path):
        p = tmp_path / "gravity_run.log"
        report = {"step_1_schema": {"status": "PASSED"}, "meta_note": "not a step dict"}
        p.write_text(json.dumps(report), encoding="utf-8")
        result = gravity_audit.legacy_audit_status(str(p))
        assert result["available"] is True
        assert len(result["steps"]) == 1
        assert result["steps"][0]["step"] == "step_1_schema"

    def test_uses_settings_output_dir_when_path_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path, raising=False)
        (tmp_path / "gravity_run.log").write_text(
            json.dumps({"step_1_schema": {"status": "PASSED"}}), encoding="utf-8"
        )
        result = gravity_audit.legacy_audit_status(None)
        assert result["available"] is True
        assert result["all_passed"] is True

    def test_unreadable_path_never_raises(self, tmp_path):
        # A directory, not a file, at the target path -- read_text() raises
        # IsADirectoryError internally; must degrade, never propagate.
        d = tmp_path / "gravity_run.log"
        d.mkdir()
        result = gravity_audit.legacy_audit_status(str(d))
        assert result["available"] is False
        assert result["all_passed"] is None
        assert result["reason"]


# ===========================================================================
# GET /gravity/audit-status
# ===========================================================================


def _make_ai_report(*, claude_status="PASSED", gemini_status="PASSED", disagreement=False):
    return {
        "generated_at": "2026-07-20T12:00:00+00:00",
        "enabled": True,
        "steps": [
            {
                "step_number": 1,
                "step_title": "Data & Schema Integrity",
                "claude_verdict": {"status": claude_status, "score": 88, "findings": [], "missing_elements": []},
                "gemini_verdict": {"status": gemini_status, "score": 90, "findings": [], "missing_elements": []},
                "disagreement": disagreement,
                "notes": [],
                "timestamp": "2026-07-20T12:00:00+00:00",
            }
        ],
        "summary": {
            "total_steps": 1,
            "claude": {"passed": 1 if claude_status == "PASSED" else 0,
                       "failed": 1 if claude_status == "FAILED" else 0, "skipped": 0},
            "gemini": {"passed": 1 if gemini_status == "PASSED" else 0,
                       "failed": 1 if gemini_status == "FAILED" else 0, "skipped": 0},
            "disagreements": 1 if disagreement else 0,
        },
    }


class TestGravityAuditStatusEndpoint:
    def test_cold_start_never_500_and_is_honest(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path, raising=False)
        monkeypatch.setattr(settings, "GRAVITY_AI_RUNNER_OUTPUT_PATH",
                             str(tmp_path / "nope.json"), raising=False)
        monkeypatch.setattr(settings, "GRAVITY_AI_RUNNER_ENABLED", False, raising=False)
        resp = client.get("/gravity/audit-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ai_audit"]["status"] == "disabled"
        assert body["ai_audit"]["generated_at"] is None
        assert body["ai_audit"]["steps"] == []
        assert body["legacy_audit"]["available"] is False
        assert body["legacy_audit"]["all_passed"] is None
        assert body["legacy_audit"]["reason"]

    def test_missing_key_status(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "GRAVITY_AI_RUNNER_OUTPUT_PATH",
                             str(tmp_path / "nope.json"), raising=False)
        monkeypatch.setattr(settings, "GRAVITY_AI_RUNNER_ENABLED", True, raising=False)
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None, raising=False)
        monkeypatch.setattr(settings, "GEMINI_API_KEY", None, raising=False)
        resp = client.get("/gravity/audit-status")
        assert resp.json()["ai_audit"]["status"] == "missing_key"

    def test_partial_key_status(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "GRAVITY_AI_RUNNER_OUTPUT_PATH",
                             str(tmp_path / "nope.json"), raising=False)
        monkeypatch.setattr(settings, "GRAVITY_AI_RUNNER_ENABLED", True, raising=False)
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-x", raising=False)
        monkeypatch.setattr(settings, "GEMINI_API_KEY", None, raising=False)
        resp = client.get("/gravity/audit-status")
        assert resp.json()["ai_audit"]["status"] == "partial_key"

    def test_ready_status_with_full_report_and_disagreement(self, tmp_path, monkeypatch):
        target = tmp_path / "gravity_ai_audit.json"
        target.write_text(
            json.dumps(_make_ai_report(claude_status="PASSED", gemini_status="FAILED", disagreement=True)),
            encoding="utf-8",
        )
        monkeypatch.setattr(settings, "GRAVITY_AI_RUNNER_OUTPUT_PATH", str(target), raising=False)
        monkeypatch.setattr(settings, "GRAVITY_AI_RUNNER_ENABLED", True, raising=False)
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-x", raising=False)
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "gk-x", raising=False)
        resp = client.get("/gravity/audit-status")
        assert resp.status_code == 200
        ai = resp.json()["ai_audit"]
        assert ai["status"] == "ready"
        assert ai["generated_at"] == "2026-07-20T12:00:00+00:00"
        assert ai["total_steps"] == 1
        assert ai["disagreements"] == 1
        assert ai["health"] in ("warn", "fail")  # gemini FAILED -> at least a disagreement warn
        assert len(ai["steps"]) == 1
        step = ai["steps"][0]
        assert step["step_title"] == "Data & Schema Integrity"
        assert step["disagreement"] is True
        assert "PASSED" in step["claude"]
        assert "FAILED" in step["gemini"]

    def test_legacy_audit_populated_from_real_log(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path, raising=False)
        monkeypatch.setattr(settings, "GRAVITY_AI_RUNNER_OUTPUT_PATH",
                             str(tmp_path / "nope.json"), raising=False)
        monkeypatch.setattr(settings, "GRAVITY_AI_RUNNER_ENABLED", False, raising=False)
        (tmp_path / "gravity_run.log").write_text(
            json.dumps({"step_1_schema": {"status": "PASSED"}, "step_2_lookahead": {"status": "PASSED"}}),
            encoding="utf-8",
        )
        resp = client.get("/gravity/audit-status")
        legacy = resp.json()["legacy_audit"]
        assert legacy["available"] is True
        assert legacy["all_passed"] is True
        assert len(legacy["steps"]) == 2

    def test_corrupt_ai_report_never_500(self, tmp_path, monkeypatch):
        target = tmp_path / "gravity_ai_audit.json"
        target.write_text("not valid json {{{", encoding="utf-8")
        monkeypatch.setattr(settings, "GRAVITY_AI_RUNNER_OUTPUT_PATH", str(target), raising=False)
        monkeypatch.setattr(settings, "GRAVITY_AI_RUNNER_ENABLED", True, raising=False)
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-x", raising=False)
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "gk-x", raising=False)
        resp = client.get("/gravity/audit-status")
        assert resp.status_code == 200
        assert resp.json()["ai_audit"]["steps"] == []

    def test_fail_open_read_with_no_token(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path, raising=False)
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/gravity/audit-status")
        assert resp.status_code == 200

    def test_401_on_wrong_read_token(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path, raising=False)
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            resp = client.get("/gravity/audit-status", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_no_trigger_endpoint_exists(self):
        # Deliberate scope cut (see the GET endpoint's own docstring): no POST
        # exists to launch either audit from this API.
        resp = client.post("/gravity/audit-status")
        assert resp.status_code in (404, 405)
        resp2 = client.post("/gravity/run")
        assert resp2.status_code == 404
