"""
tests/test_gravity_ai_panel.py
================================
Unit tests for ``gui.gravity_ai_panel`` — the Streamlit-free helpers behind
the Safety-tab AI Gravity audit section.

Runs fully headless: no Streamlit import, no real LLM provider, no network.
The Safety-tab wiring inside ``gui/panels/__init__.py`` is verified by a
source-grep test (mirroring ``tests/test_llm_commentary_panel.py``).

Coverage
--------
TestRunnerStatus         — disabled / missing_key / partial_key / ready.
TestLoadAuditReport      — missing file → None; corrupt JSON → None;
                           non-object → None; valid round-trip; missing
                           required keys → None (CONSTRAINT #6).
TestStepRows             — empty / None → []; full report → one row per
                           step; missing verdicts render as "—" without
                           fabricating PASSED (CONSTRAINT #4).
TestSummariseRun         — empty → "empty" health; agreement clean →
                           "clean"; disagreement → "warn"; Claude FAILED →
                           "fail".
TestHealthCaption        — every health value produces a non-empty caption.
TestPanelWiring          — ``gui.panels`` exports the new section
                           function AND ``render_gravity_audit`` calls it.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict
from unittest import mock

import pytest

from gui.gravity_ai_panel import (
    RunSummary,
    health_caption,
    load_audit_report,
    runner_status,
    step_rows,
    summarise_run,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_report(
    *,
    steps: int = 7,
    claude_pass: int = 7,
    claude_fail: int = 0,
    claude_skip: int = 0,
    gemini_pass: int = 7,
    gemini_fail: int = 0,
    gemini_skip: int = 0,
    disagreements: int = 0,
) -> Dict[str, Any]:
    step_list = []
    for i in range(steps):
        step_list.append({
            "step_number": i + 1,
            "step_title": f"Step {i + 1}",
            "claude_verdict": {"status": "PASSED", "score": 90,
                               "findings": [], "missing_elements": []},
            "gemini_verdict": {"status": "PASSED", "score": 92,
                               "findings": [], "missing_elements": []},
            "disagreement": False,
            "notes": [],
            "timestamp": "2026-06-30T20:00:00+00:00",
        })
    return {
        "generated_at": "2026-06-30T20:00:00+00:00",
        "enabled": True,
        "steps": step_list,
        "summary": {
            "total_steps": steps,
            "claude": {"passed": claude_pass, "failed": claude_fail, "skipped": claude_skip},
            "gemini": {"passed": gemini_pass, "failed": gemini_fail, "skipped": gemini_skip},
            "disagreements": disagreements,
        },
    }


# ---------------------------------------------------------------------------
# TestRunnerStatus
# ---------------------------------------------------------------------------


class TestRunnerStatus:
    def test_disabled_when_master_switch_off(self):
        s = SimpleNamespace(GRAVITY_AI_RUNNER_ENABLED=False)
        assert runner_status(s) == "disabled"

    def test_missing_key_when_enabled_but_no_keys(self):
        s = SimpleNamespace(GRAVITY_AI_RUNNER_ENABLED=True,
                            ANTHROPIC_API_KEY=None, GEMINI_API_KEY=None)
        assert runner_status(s) == "missing_key"

    def test_missing_key_when_keys_are_empty_strings(self):
        s = SimpleNamespace(GRAVITY_AI_RUNNER_ENABLED=True,
                            ANTHROPIC_API_KEY="", GEMINI_API_KEY="")
        assert runner_status(s) == "missing_key"

    def test_partial_key_when_only_anthropic_set(self):
        s = SimpleNamespace(GRAVITY_AI_RUNNER_ENABLED=True,
                            ANTHROPIC_API_KEY="sk-x", GEMINI_API_KEY=None)
        assert runner_status(s) == "partial_key"

    def test_partial_key_when_only_gemini_set(self):
        s = SimpleNamespace(GRAVITY_AI_RUNNER_ENABLED=True,
                            ANTHROPIC_API_KEY=None, GEMINI_API_KEY="g-x")
        assert runner_status(s) == "partial_key"

    def test_ready_when_both_keys_and_switch_on(self):
        s = SimpleNamespace(GRAVITY_AI_RUNNER_ENABLED=True,
                            ANTHROPIC_API_KEY="sk-x", GEMINI_API_KEY="g-x")
        assert runner_status(s) == "ready"

    def test_defaults_to_disabled_for_minimal_object(self):
        assert runner_status(SimpleNamespace()) == "disabled"


# ---------------------------------------------------------------------------
# TestLoadAuditReport
# ---------------------------------------------------------------------------


class TestLoadAuditReport:
    def test_missing_file_returns_none(self, tmp_path):
        assert load_audit_report(str(tmp_path / "missing.json")) is None

    def test_corrupt_json_returns_none(self, tmp_path):
        p = tmp_path / "audit.json"
        p.write_text("this is not json", encoding="utf-8")
        assert load_audit_report(str(p)) is None

    def test_non_object_root_returns_none(self, tmp_path):
        p = tmp_path / "audit.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        assert load_audit_report(str(p)) is None

    def test_missing_steps_key_returns_none(self, tmp_path):
        p = tmp_path / "audit.json"
        p.write_text(json.dumps({"summary": {}}), encoding="utf-8")
        assert load_audit_report(str(p)) is None

    def test_missing_summary_key_returns_none(self, tmp_path):
        p = tmp_path / "audit.json"
        p.write_text(json.dumps({"steps": []}), encoding="utf-8")
        assert load_audit_report(str(p)) is None

    def test_valid_round_trip(self, tmp_path):
        p = tmp_path / "audit.json"
        report = _make_report()
        p.write_text(json.dumps(report), encoding="utf-8")
        loaded = load_audit_report(str(p))
        assert loaded is not None
        assert loaded["summary"]["total_steps"] == 7

    def test_uses_settings_path_when_path_is_none(self, tmp_path, monkeypatch):
        # The default branch reads settings.GRAVITY_AI_RUNNER_OUTPUT_PATH.
        # We point it at a tmp file via monkeypatching the loaded settings object.
        from gui import gravity_ai_panel as panel_mod

        # Re-import settings inside the function — patch the module-level
        # `from settings import settings` lookup.
        import settings as settings_mod

        target = tmp_path / "ga.json"
        target.write_text(json.dumps(_make_report()), encoding="utf-8")
        monkeypatch.setattr(settings_mod.settings, "GRAVITY_AI_RUNNER_OUTPUT_PATH",
                            str(target), raising=False)
        loaded = load_audit_report(None)
        assert loaded is not None
        assert loaded["summary"]["total_steps"] == 7


# ---------------------------------------------------------------------------
# TestStepRows
# ---------------------------------------------------------------------------


class TestStepRows:
    def test_none_report_returns_empty_list(self):
        assert step_rows(None) == []

    def test_empty_steps_list_returns_empty(self):
        assert step_rows({"steps": [], "summary": {}}) == []

    def test_each_step_emits_one_row(self):
        report = _make_report(steps=7)
        rows = step_rows(report)
        assert len(rows) == 7
        for r in rows:
            assert r["claude"] == "✅ PASSED"
            assert r["gemini"] == "✅ PASSED"
            assert r["disagreement"] is False

    def test_missing_verdict_renders_as_dash_not_fabricated_pass(self):
        """CONSTRAINT #4 — never invent a PASSED verdict when a model
        soft-failed and returned None."""
        report = {
            "steps": [
                {
                    "step_number": 1,
                    "step_title": "Step 1",
                    "claude_verdict": None,
                    "gemini_verdict": {"status": "FAILED", "score": 40},
                    "disagreement": False,
                    "notes": ["claude provider unavailable"],
                }
            ],
            "summary": {},
        }
        rows = step_rows(report)
        assert rows[0]["claude"] == "—"
        assert rows[0]["gemini"] == "❌ FAILED"
        assert "claude provider unavailable" in rows[0]["notes"]

    def test_disagreement_carries_through(self):
        report = {
            "steps": [
                {
                    "step_number": 2,
                    "step_title": "Step 2",
                    "claude_verdict": {"status": "PASSED", "score": 80},
                    "gemini_verdict": {"status": "FAILED", "score": 30},
                    "disagreement": True,
                    "notes": [],
                }
            ],
            "summary": {},
        }
        rows = step_rows(report)
        assert rows[0]["disagreement"] is True
        assert rows[0]["claude"] == "✅ PASSED"
        assert rows[0]["gemini"] == "❌ FAILED"

    def test_non_dict_step_entries_are_skipped(self):
        report = {"steps": [None, 42, "garbage", {"step_number": 1, "step_title": "S1",
                                                  "claude_verdict": None,
                                                  "gemini_verdict": None,
                                                  "notes": []}],
                  "summary": {}}
        rows = step_rows(report)
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# TestSummariseRun
# ---------------------------------------------------------------------------


class TestSummariseRun:
    def test_none_report_is_empty_health(self):
        s = summarise_run(None)
        assert isinstance(s, RunSummary)
        assert s.health == "empty"
        assert s.total_steps == 0

    def test_clean_agreement(self):
        s = summarise_run(_make_report())
        assert s.health == "clean"
        assert s.total_steps == 7
        assert s.claude_passed == 7
        assert s.gemini_passed == 7
        assert s.disagreements == 0

    def test_warn_on_disagreement(self):
        rep = _make_report(disagreements=2)
        s = summarise_run(rep)
        assert s.health == "warn"

    def test_warn_on_skipped_side(self):
        rep = _make_report(claude_pass=4, claude_skip=3)
        s = summarise_run(rep)
        assert s.health == "warn"

    def test_fail_on_claude_failed(self):
        rep = _make_report(claude_pass=5, claude_fail=2)
        s = summarise_run(rep)
        assert s.health == "fail"
        assert s.claude_failed == 2

    def test_summary_is_frozen_dataclass(self):
        s = summarise_run(None)
        with pytest.raises(Exception):
            s.total_steps = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestHealthCaption
# ---------------------------------------------------------------------------


class TestHealthCaption:
    def test_every_health_yields_non_empty_caption(self):
        for h in ("empty", "clean", "warn", "fail"):
            s = RunSummary(
                generated_at="—", enabled=False, total_steps=0,
                claude_passed=0, claude_failed=0, claude_skipped=0,
                gemini_passed=0, gemini_failed=0, gemini_skipped=0,
                disagreements=0, health=h,
            )
            text = health_caption(s)
            assert isinstance(text, str) and text

    def test_fail_caption_cites_failed_count(self):
        s = RunSummary(
            generated_at="t", enabled=True, total_steps=7,
            claude_passed=5, claude_failed=2, claude_skipped=0,
            gemini_passed=7, gemini_failed=0, gemini_skipped=0,
            disagreements=2, health="fail",
        )
        text = health_caption(s)
        assert "2" in text
        assert "FAILED" in text.upper()


# ---------------------------------------------------------------------------
# TestPanelWiring
# ---------------------------------------------------------------------------


class TestPanelWiring:
    def test_section_helper_exported(self):
        from gui import panels

        assert hasattr(panels, "_render_gravity_ai_runner_section")
        assert callable(panels._render_gravity_ai_runner_section)

    def test_render_gravity_audit_calls_runner_section(self):
        # Lives in gui/panels/gravity_audit.py post-refactor (Phase 4a extracted
        # gui/panels/__init__.py into per-tab modules; __init__.py is now a
        # thin re-export stub — see tests/test_ai_insights_panel.py for the
        # same fix pattern applied to the AI Insights tab).
        path = Path(__file__).resolve().parents[1] / "gui" / "panels" / "gravity_audit.py"
        src = path.read_text(encoding="utf-8")
        assert "_render_gravity_ai_runner_section()" in src

    def test_section_imports_from_helper_module(self):
        # Lives in gui/panels/gravity_audit.py post-refactor (see
        # test_render_gravity_audit_calls_runner_section for the rationale).
        path = Path(__file__).resolve().parents[1] / "gui" / "panels" / "gravity_audit.py"
        src = path.read_text(encoding="utf-8")
        assert "from gui.gravity_ai_panel import" in src
        for name in (
            "health_caption",
            "load_audit_report",
            "runner_status",
            "step_rows",
            "summarise_run",
        ):
            assert name in src, f"helper {name} missing from gui/panels/gravity_audit.py"
