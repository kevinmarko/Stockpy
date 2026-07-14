"""
tests/test_ai_verification_prompts.py
========================================
Unit tests for ``ai_verification_prompts.py`` — the prompt-compilation
scaffold for the "Gravity AI Auditor" LLM-review harness (distinct from
``Gravity AI Review Suite.py``'s own ``step_*`` static-analysis audits,
which are a separate, much larger file — see
``tests/test_gravity_mirrored_invariants.py`` for that one).

Before writing this suite, the ``GravityAIAuditor`` class was read in
full. Its own docstring claims it "pre-checks the code via RegEx for
required terminology" and that ``run_full_validation_suite`` "interacts
directly with the Claude/OpenAI APIs" in a live environment — but the
actual code does neither: there is no RegEx/keyword pre-check anywhere in
the class, and ``run_full_validation_suite`` never calls an LLM at all —
it is a scaffold that only compiles the prompt string
(``generate_prompt_for_step``) and appends a stub ``AIReviewReport`` with
a hardcoded ``status="PENDING_API_CALL"`` for each step it recognizes,
skipping any step number with no matching template. This test suite pins
what the code actually does, not what its docstrings aspirationally
describe — the original coverage-audit plan (mock an LLM call, assert
per-criterion checking) does not apply since none of that logic exists to
test.

Coverage
--------
* ``GravityAIAuditor.generate_prompt_for_step``: the returned string
  contains the system prompt, the step's own prompt text, and the target
  code verbatim, in that order.
* ``GravityAIAuditor.run_full_validation_suite``:
  - a step number with a matching template in ``ALL_PROMPTS`` produces
    exactly one stub ``AIReviewReport`` for that step, with the documented
    placeholder fields (``status="PENDING_API_CALL"``, ``score=0.0``,
    ``findings=["Awaiting Claude API Execution"]``,
    ``missing_elements=[]``) and a parseable UTC ISO timestamp.
  - a step number with NO matching template (e.g. 0, 9, 999) is silently
    skipped — no report is appended for it, and it does not raise.
  - multiple step numbers each produce their own report, in input-map
    iteration order; an empty map returns an empty list.
* ``ALL_PROMPTS`` / dataclass structure: exactly 8 templates, step
  numbers 1 through 8 with no gaps or duplicates; every template's
  ``criteria`` list is non-empty and every entry is a well-formed
  ``ValidationCriterion`` (``critical=True`` for all of them, per the
  baseline definitions); ``StepPromptTemplate``/``ValidationCriterion``/
  ``AIReviewReport`` round-trip through ``dataclasses.asdict``.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from ai_verification_prompts import (
    ALL_PROMPTS,
    SYSTEM_PROMPT,
    AIReviewReport,
    GravityAIAuditor,
    StepPromptTemplate,
    ValidationCriterion,
)


@pytest.fixture()
def auditor() -> GravityAIAuditor:
    return GravityAIAuditor()


# ---------------------------------------------------------------------------
# generate_prompt_for_step
# ---------------------------------------------------------------------------


class TestGeneratePromptForStep:
    def test_prompt_contains_system_prompt_step_text_and_target_code(self, auditor):
        template = ALL_PROMPTS[0]
        target_code = "def foo(): return 42  # UNIQUE_MARKER_XYZ"

        prompt = auditor.generate_prompt_for_step(template, target_code)

        assert SYSTEM_PROMPT in prompt
        assert template.prompt_text in prompt
        assert target_code in prompt

    def test_system_prompt_precedes_step_prompt_precedes_code(self, auditor):
        template = ALL_PROMPTS[2]
        target_code = "UNIQUE_TARGET_CODE_MARKER"

        prompt = auditor.generate_prompt_for_step(template, target_code)

        sys_idx = prompt.index(SYSTEM_PROMPT)
        step_idx = prompt.index(template.prompt_text)
        code_idx = prompt.index(target_code)
        assert sys_idx < step_idx < code_idx

    def test_different_templates_produce_different_prompts(self, auditor):
        p1 = auditor.generate_prompt_for_step(ALL_PROMPTS[0], "code")
        p2 = auditor.generate_prompt_for_step(ALL_PROMPTS[1], "code")
        assert p1 != p2


# ---------------------------------------------------------------------------
# run_full_validation_suite
# ---------------------------------------------------------------------------


class TestRunFullValidationSuite:
    def test_empty_map_returns_empty_list(self, auditor):
        assert auditor.run_full_validation_suite({}) == []

    def test_known_step_produces_one_stub_report(self, auditor):
        reports = auditor.run_full_validation_suite({1: "some code"})

        assert len(reports) == 1
        report = reports[0]
        assert isinstance(report, AIReviewReport)
        assert report.step_number == 1
        assert report.status == "PENDING_API_CALL"
        assert report.score == 0.0
        assert report.findings == ["Awaiting Claude API Execution"]
        assert report.missing_elements == []
        # Timestamp is a real, parseable ISO-8601 UTC timestamp.
        datetime.fromisoformat(report.timestamp)

    def test_unknown_step_number_is_silently_skipped(self, auditor):
        reports = auditor.run_full_validation_suite({999: "some code"})
        assert reports == []

    def test_mix_of_known_and_unknown_steps(self, auditor):
        reports = auditor.run_full_validation_suite(
            {1: "code a", 999: "code b", 3: "code c"}
        )

        step_numbers = [r.step_number for r in reports]
        assert step_numbers == [1, 3]

    def test_no_llm_or_network_call_is_made(self, auditor, monkeypatch):
        # There is no LLM client anywhere in this module to patch — this test
        # documents that fact by asserting the suite runs to completion with
        # no network-related dependency injected, and every report is the
        # hardcoded PENDING_API_CALL stub (never a real "PASSED"/"FAILED").
        reports = auditor.run_full_validation_suite({s: "x" for s in range(1, 9)})
        assert len(reports) == 8
        assert all(r.status == "PENDING_API_CALL" for r in reports)


# ---------------------------------------------------------------------------
# ALL_PROMPTS / dataclass structure
# ---------------------------------------------------------------------------


class TestAllPromptsStructure:
    def test_exactly_eight_templates_numbered_one_through_eight(self):
        assert len(ALL_PROMPTS) == 8
        assert sorted(t.step_number for t in ALL_PROMPTS) == list(range(1, 9))

    def test_no_duplicate_step_numbers(self):
        numbers = [t.step_number for t in ALL_PROMPTS]
        assert len(numbers) == len(set(numbers))

    def test_every_template_has_nonempty_criteria(self):
        for template in ALL_PROMPTS:
            assert isinstance(template, StepPromptTemplate)
            assert len(template.criteria) > 0

    def test_every_criterion_is_well_formed_and_critical(self):
        for template in ALL_PROMPTS:
            for crit in template.criteria:
                assert isinstance(crit, ValidationCriterion)
                assert crit.id
                assert crit.description
                assert len(crit.required_keywords) > 0
                assert crit.critical is True

    def test_step_prompt_text_is_nonempty(self):
        # NOTE: prompt_text is NOT guaranteed to be .strip()'d here -- the
        # module-level assignment strips the in-code _BASELINE_STEP_N_PROMPT
        # literal, but prompt_registry.get_registry().get(key, default=...)
        # prefers a "committed baseline" registry file over that in-code
        # default when one exists (verified by direct execution: steps 1-7
        # resolve from a committed baseline file and retain a trailing
        # newline; only step 8, which has no committed baseline, falls back
        # to the already-stripped in-code default). See
        # tests/test_gravity_prompt_sourcing.py for the registry-fallback
        # contract itself.
        for template in ALL_PROMPTS:
            assert template.prompt_text.strip() != ""


class TestDataclassRoundTrip:
    def test_validation_criterion_asdict(self):
        from dataclasses import asdict

        crit = ValidationCriterion("1.1", "desc", ["a", "b"], True)
        d = asdict(crit)
        assert d == {
            "id": "1.1",
            "description": "desc",
            "required_keywords": ["a", "b"],
            "critical": True,
        }

    def test_ai_review_report_asdict(self):
        from dataclasses import asdict

        report = AIReviewReport(
            step_number=1,
            status="PASSED",
            score=100.0,
            findings=["ok"],
            missing_elements=[],
            timestamp="2026-01-01T00:00:00+00:00",
        )
        d = asdict(report)
        assert d["step_number"] == 1
        assert d["status"] == "PASSED"
        assert d["score"] == 100.0
