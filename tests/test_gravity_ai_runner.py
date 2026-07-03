"""
tests/test_gravity_ai_runner.py
================================
Unit tests for ``engine.gravity_ai_runner``.

All provider calls are mocked — no real Claude / Gemini network traffic.

Coverage
--------
TestSchemaSurface             — `GravityAuditStepResult` accepts canonical payloads
                                AND rejects out-of-bounds ones.
TestStepFileMap               — all 8 steps in the file map; every mapped file
                                is read without crashing; nonexistent steps
                                produce a sentinel placeholder, never raise.
TestRunStepDisabled           — master switch off → no provider instantiated;
                                run_step still returns a StepRunResult with
                                both verdicts None.
TestRunStepAgreement          — both providers return PASSED → disagreement=False.
TestRunStepDisagreement       — Claude=PASSED, Gemini=FAILED → disagreement=True.
TestRunStepProviderRaises     — provider raises → that side is None; the OTHER
                                side's verdict survives (no cross-contamination).
TestRunStepUnknownStep        — step number outside 1-8 → notes record it,
                                runner never raises.
TestRunAll                    — sweeps all 8 steps; aggregate summary counts
                                add up to total_steps.
TestWriteReport               — round-trips through JSON atomically; missing
                                target dir is created; write failure soft-fails.
TestNoTopLevelLLMImport       — engine/gravity_ai_runner.py source has no
                                top-level `import anthropic` / `import google`.
TestNoOrderCode               — module source has no order-submission verbs
                                (audited by Gravity step_75 too).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from engine import gravity_ai_runner as runner
from engine.gravity_ai_runner import (
    RunReport,
    StepRunResult,
    _STEP_FILE_MAP,
    _summarise,
    run_all,
    run_step,
    write_report,
)
from llm.schemas import GravityAuditStepResult


# ---------------------------------------------------------------------------
# TestSchemaSurface
# ---------------------------------------------------------------------------


class TestSchemaSurface:
    def test_canonical_payload_accepted(self):
        r = GravityAuditStepResult(
            status="PASSED", score=92, findings=["vectorized"], missing_elements=[]
        )
        assert r.status == "PASSED"
        assert r.score == 92

    def test_bad_status_rejected(self):
        with pytest.raises(Exception):
            GravityAuditStepResult(status="OK", score=92)  # type: ignore[arg-type]

    def test_score_bounds_enforced(self):
        with pytest.raises(Exception):
            GravityAuditStepResult(status="PASSED", score=101)
        with pytest.raises(Exception):
            GravityAuditStepResult(status="PASSED", score=-1)


# ---------------------------------------------------------------------------
# TestStepFileMap
# ---------------------------------------------------------------------------


class TestStepFileMap:
    def test_eight_steps_mapped(self):
        # Step 8 (Tier 9 Scope 4 — Opal) was added alongside llm/research.py.
        assert sorted(_STEP_FILE_MAP.keys()) == [1, 2, 3, 4, 5, 6, 7, 8]
        for step, files in _STEP_FILE_MAP.items():
            assert isinstance(files, tuple) and len(files) >= 1, f"step {step} has empty map"

    def test_compose_target_code_for_each_step(self):
        # Every mapped file should be readable without raising; returns a string.
        from engine.gravity_ai_runner import _compose_target_code

        for step in _STEP_FILE_MAP:
            blob = _compose_target_code(step)
            assert isinstance(blob, str)
            assert len(blob) > 0
            # Each step's blob includes at least one '# === <path> ===' header.
            assert "# ===" in blob


# ---------------------------------------------------------------------------
# Fake-provider helpers
# ---------------------------------------------------------------------------


def _good_pass() -> GravityAuditStepResult:
    return GravityAuditStepResult(
        status="PASSED", score=95, findings=["clean"], missing_elements=[]
    )


def _good_fail() -> GravityAuditStepResult:
    return GravityAuditStepResult(
        status="FAILED", score=40, findings=["loops detected"], missing_elements=["EMA"]
    )


class _FakeProvider:
    """Stand-in LLMProvider that returns a canned result (or raises)."""

    name = "fake"

    def __init__(self, *, value=None, raises=None):
        self._value = value
        self._raises = raises
        self.call_count = 0

    def call_structured(self, system, user, schema_model):
        self.call_count += 1
        if self._raises is not None:
            raise self._raises
        return self._value


# ---------------------------------------------------------------------------
# TestRunStepDisabled
# ---------------------------------------------------------------------------


class TestRunStepDisabled:
    def test_master_switch_off_no_provider_instantiated(self, monkeypatch):
        # The runner reads settings to decide whether to construct a provider.
        # With the master switch off (default), it should never even *try*.
        monkeypatch.setattr(runner, "_claude_provider", lambda: None)
        monkeypatch.setattr(runner, "_gemini_provider", lambda: None)

        result = run_step(1)
        assert isinstance(result, StepRunResult)
        assert result.claude_verdict is None
        assert result.gemini_verdict is None
        assert result.disagreement is False
        assert any("claude provider unavailable" in n for n in result.notes)
        assert any("gemini provider unavailable" in n for n in result.notes)


# ---------------------------------------------------------------------------
# TestRunStepAgreement
# ---------------------------------------------------------------------------


class TestRunStepAgreement:
    def test_both_pass_no_disagreement(self):
        claude = _FakeProvider(value=_good_pass())
        gemini = _FakeProvider(value=_good_pass())

        result = run_step(1, claude=claude, gemini=gemini, target_code="# stub")
        assert result.claude_verdict is not None
        assert result.gemini_verdict is not None
        assert result.claude_verdict["status"] == "PASSED"
        assert result.gemini_verdict["status"] == "PASSED"
        assert result.disagreement is False
        assert claude.call_count == 1
        assert gemini.call_count == 1


# ---------------------------------------------------------------------------
# TestRunStepDisagreement
# ---------------------------------------------------------------------------


class TestRunStepDisagreement:
    def test_status_mismatch_flagged(self):
        claude = _FakeProvider(value=_good_pass())
        gemini = _FakeProvider(value=_good_fail())

        result = run_step(2, claude=claude, gemini=gemini, target_code="# stub")
        assert result.claude_verdict["status"] == "PASSED"
        assert result.gemini_verdict["status"] == "FAILED"
        assert result.disagreement is True


# ---------------------------------------------------------------------------
# TestRunStepProviderRaises
# ---------------------------------------------------------------------------


class TestRunStepProviderRaises:
    def test_claude_raises_gemini_survives(self):
        claude = _FakeProvider(raises=RuntimeError("boom"))
        gemini = _FakeProvider(value=_good_pass())

        result = run_step(3, claude=claude, gemini=gemini, target_code="# stub")
        assert result.claude_verdict is None
        assert result.gemini_verdict is not None
        # Disagreement is only computed when BOTH sides are present.
        assert result.disagreement is False
        assert any("claude returned None" in n for n in result.notes)

    def test_gemini_raises_claude_survives(self):
        claude = _FakeProvider(value=_good_pass())
        gemini = _FakeProvider(raises=RuntimeError("boom"))

        result = run_step(4, claude=claude, gemini=gemini, target_code="# stub")
        assert result.claude_verdict is not None
        assert result.gemini_verdict is None
        assert result.disagreement is False
        assert any("gemini returned None" in n for n in result.notes)

    def test_both_raise_no_crash(self):
        claude = _FakeProvider(raises=RuntimeError("a"))
        gemini = _FakeProvider(raises=RuntimeError("b"))
        result = run_step(5, claude=claude, gemini=gemini, target_code="# stub")
        assert result.claude_verdict is None
        assert result.gemini_verdict is None
        assert result.disagreement is False


# ---------------------------------------------------------------------------
# TestRunStepUnknownStep
# ---------------------------------------------------------------------------


class TestRunStepUnknownStep:
    def test_unknown_step_records_note_does_not_raise(self):
        result = run_step(999)
        assert result.step_number == 999
        assert result.claude_verdict is None
        assert result.gemini_verdict is None
        # The notes list flags that there's no prompt template for this step.
        assert any("no prompt template" in n for n in result.notes)


# ---------------------------------------------------------------------------
# TestRunAll
# ---------------------------------------------------------------------------


class TestRunAll:
    def test_run_all_sweeps_every_step(self, monkeypatch):
        claude = _FakeProvider(value=_good_pass())
        gemini = _FakeProvider(value=_good_pass())

        # Force the master switch on so run_all constructs providers via the
        # injected factories.  Inject the fakes via monkeypatch.
        monkeypatch.setattr(runner.settings if hasattr(runner, "settings") else type("", (), {})(),
                            "GRAVITY_AI_RUNNER_ENABLED", True, raising=False)

        # Inject the fakes directly so we don't depend on factory state.
        report = run_all(claude=claude, gemini=gemini)
        assert isinstance(report, RunReport)
        assert len(report.steps) == 8
        # Each step called both providers once (8 + 8 = 16 calls). Step 8
        # (Tier 9 Scope 4 — Opal) was added alongside llm/research.py.
        assert claude.call_count == 8
        assert gemini.call_count == 8
        # Summary counts add up to total_steps.
        s = report.summary
        assert s["total_steps"] == 8
        assert s["claude"]["passed"] + s["claude"]["failed"] + s["claude"]["skipped"] == 8
        assert s["gemini"]["passed"] + s["gemini"]["failed"] + s["gemini"]["skipped"] == 8

    def test_run_all_disabled_by_default(self):
        # With no providers injected AND master switch False (default),
        # run_all completes without any provider call.
        report = run_all()
        assert isinstance(report, RunReport)
        assert report.enabled is False
        # Every step's verdicts are None (provider unavailable).
        for st in report.steps:
            assert st.claude_verdict is None
            assert st.gemini_verdict is None
        assert report.summary["claude"]["skipped"] == 8
        assert report.summary["gemini"]["skipped"] == 8


# ---------------------------------------------------------------------------
# TestSummarise
# ---------------------------------------------------------------------------


class TestSummarise:
    def test_mixed_counts(self):
        steps = [
            StepRunResult(1, "S1",
                          claude_verdict={"status": "PASSED", "score": 90},
                          gemini_verdict={"status": "PASSED", "score": 88},
                          disagreement=False, notes=[], timestamp="t"),
            StepRunResult(2, "S2",
                          claude_verdict={"status": "FAILED", "score": 30},
                          gemini_verdict={"status": "PASSED", "score": 75},
                          disagreement=True, notes=[], timestamp="t"),
            StepRunResult(3, "S3",
                          claude_verdict=None,
                          gemini_verdict={"status": "FAILED", "score": 20},
                          disagreement=False, notes=[], timestamp="t"),
        ]
        s = _summarise(steps)
        assert s["total_steps"] == 3
        assert s["claude"]["passed"] == 1
        assert s["claude"]["failed"] == 1
        assert s["claude"]["skipped"] == 1
        assert s["gemini"]["passed"] == 2
        assert s["gemini"]["failed"] == 1
        assert s["gemini"]["skipped"] == 0
        assert s["disagreements"] == 1


# ---------------------------------------------------------------------------
# TestWriteReport
# ---------------------------------------------------------------------------


class TestWriteReport:
    def test_round_trip_through_json(self, tmp_path):
        report = run_all()  # disabled-by-default produces a valid empty-verdict report.
        target = tmp_path / "audits" / "report.json"
        out = write_report(report, path=str(target))
        assert out is not None
        # Parent dir was created.
        assert target.parent.is_dir()
        # Content parses back.
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert "steps" in loaded
        assert "summary" in loaded
        assert loaded["summary"]["total_steps"] == 8

    def test_write_failure_soft_fails(self, tmp_path, monkeypatch):
        report = run_all()
        # Make the target dir mkdir fail.
        monkeypatch.setattr(Path, "mkdir", lambda *a, **kw: (_ for _ in ()).throw(OSError("denied")))
        out = write_report(report, path=str(tmp_path / "denied" / "report.json"))
        assert out is None


# ---------------------------------------------------------------------------
# TestNoTopLevelLLMImport / TestNoOrderCode
# ---------------------------------------------------------------------------


class TestSourceGuards:
    def test_no_top_level_anthropic_or_google_import(self):
        path = Path(__file__).resolve().parents[1] / "engine" / "gravity_ai_runner.py"
        src = path.read_text(encoding="utf-8")
        top = "\n".join(ln for ln in src.splitlines()
                        if (not ln.startswith(" ") and not ln.startswith("\t")))
        assert "import anthropic" not in top
        assert "from anthropic" not in top
        assert "import google" not in top
        assert "from google" not in top

    def test_no_order_submission_verbs(self):
        path = Path(__file__).resolve().parents[1] / "engine" / "gravity_ai_runner.py"
        src = path.read_text(encoding="utf-8")
        for forbidden in ("submit_order", "place_order", "buy_order", "sell_order", "place_equity_order"):
            assert forbidden not in src, f"{forbidden} found in runner — must stay advisory-only"
