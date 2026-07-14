"""
tests/test_gravity_audit_panel_helpers.py
============================================
Unit tests for the two pure helper functions in
``gui/panels/gravity_audit.py`` that have never had a direct test despite
being non-trivial: ``_derive_step_status`` and ``_parse_trailing_json``.

Both are pure functions with no Streamlit dependency, so they are unit
tested directly rather than through a rendered panel. This closes the gap
identified in the 2026-07-14 test-coverage re-audit
(``docs/test_coverage_analysis.md``, Phase 5 item 2): ``_derive_step_status``
carries a documented history (in its own docstring) of misreporting a
passing Gravity audit step as a failure, and had zero tests protecting
against a regression of that exact bug.

Coverage
--------
* ``_derive_step_status``:
  - a top-level ``"status"`` string is classified PASS/FAIL by prefix
    match (``"PASSED"``, ``"PASSED_WITH_WARNINGS"`` both pass;
    ``"FAILED"``/``"ERROR"`` fail), case-insensitively.
  - a top-level ``"overall_pass"`` bool is used when no ``"status"`` key
    is present.
  - ``"status"`` takes precedence over ``"overall_pass"`` when both are
    present (matches the function's ``if/elif`` order).
  - the two legacy Steps 1-7 domain-specific shapes:
    ``step_3_5_discrepancy_analysis`` (``"conclusion"`` field, only
    ``"Perfect Alignment"`` passes) and ``step_7_simulation_impact``
    (joins ``vector_bt_status``/``backtrader_status``, fails if either
    contains "error").
  - an unrecognized shape (no known keys, unknown step key) falls
    through to the ``(False, "—")`` sentinel rather than raising or
    silently passing.
* ``_parse_trailing_json``:
  - a clean trailing JSON object is extracted and parsed.
  - trailing JSON preceded by unrelated stdout noise (log lines, print
    statements) is still found — this is the realistic Gravity-subprocess
    case the function exists for.
  - nested braces inside the JSON object are handled via depth counting,
    not a naive first/last brace match.
  - no ``"}"`` anywhere in the text returns ``None``.
  - unbalanced/malformed braces (more closes than opens, or a dangling
    open) return ``None`` rather than raising.
  - text ending in a syntactically-balanced but semantically invalid JSON
    blob returns ``None`` (the ``json.loads`` failure path).
  - empty string returns ``None``.
"""

from __future__ import annotations

import pytest

from gui.panels.gravity_audit import _derive_step_status, _parse_trailing_json


# ---------------------------------------------------------------------------
# _derive_step_status
# ---------------------------------------------------------------------------


class TestDeriveStepStatusFromStatusKey:
    @pytest.mark.parametrize(
        "status_value,expected_ok",
        [
            ("PASSED", True),
            ("passed", True),
            ("PASSED_WITH_WARNINGS", True),
            ("FAILED", False),
            ("ERROR", False),
            ("failed", False),
        ],
    )
    def test_classifies_by_status_prefix(self, status_value, expected_ok):
        ok, label = _derive_step_status("step_1_anything", {"status": status_value})
        assert ok is expected_ok
        assert label == status_value

    def test_status_takes_precedence_over_overall_pass(self):
        # Both keys present: "status" is checked first in the source.
        ok, label = _derive_step_status(
            "step_x", {"status": "FAILED", "overall_pass": True}
        )
        assert ok is False
        assert label == "FAILED"


class TestDeriveStepStatusFromOverallPass:
    def test_true_maps_to_passed(self):
        ok, label = _derive_step_status("step_9_something", {"overall_pass": True})
        assert ok is True
        assert label == "PASSED"

    def test_false_maps_to_failed(self):
        ok, label = _derive_step_status("step_9_something", {"overall_pass": False})
        assert ok is False
        assert label == "FAILED"


class TestDeriveStepStatusLegacyShapes:
    def test_step_3_5_perfect_alignment_passes(self):
        ok, label = _derive_step_status(
            "step_3_5_discrepancy_analysis", {"conclusion": "Perfect Alignment"}
        )
        assert ok is True
        assert label == "Perfect Alignment"

    def test_step_3_5_any_other_conclusion_fails(self):
        ok, label = _derive_step_status(
            "step_3_5_discrepancy_analysis", {"conclusion": "Discrepancy Found"}
        )
        assert ok is False
        assert label == "Discrepancy Found"

    def test_step_3_5_missing_conclusion_defaults_to_unknown_and_fails(self):
        ok, label = _derive_step_status("step_3_5_discrepancy_analysis", {})
        assert ok is False
        assert label == "UNKNOWN"

    def test_step_7_both_statuses_ok_passes(self):
        ok, label = _derive_step_status(
            "step_7_simulation_impact",
            {"vector_bt_status": "OK", "backtrader_status": "OK"},
        )
        assert ok is True
        assert label == "OK / OK"

    def test_step_7_error_in_either_status_fails(self):
        ok, label = _derive_step_status(
            "step_7_simulation_impact",
            {"vector_bt_status": "OK", "backtrader_status": "Error: crashed"},
        )
        assert ok is False
        assert "Error" in label

    def test_step_7_case_insensitive_error_match(self):
        ok, _ = _derive_step_status(
            "step_7_simulation_impact",
            {"vector_bt_status": "ERROR: timeout", "backtrader_status": "OK"},
        )
        assert ok is False

    def test_step_7_missing_sub_statuses_defaults_to_unknown_but_passes(self):
        # Neither sub-status contains "error", so this is a pass with an
        # "UNKNOWN" label -- documenting the function's actual (permissive)
        # behavior for a step_7 report with no populated sub-statuses.
        ok, label = _derive_step_status("step_7_simulation_impact", {})
        assert ok is True
        assert label == "UNKNOWN"


class TestDeriveStepStatusUnknownShape:
    def test_unrecognized_key_and_no_known_fields_fails_closed(self):
        ok, label = _derive_step_status("step_99_new_and_unmapped", {"foo": "bar"})
        assert ok is False
        assert label == "—"

    def test_empty_dict_fails_closed(self):
        ok, label = _derive_step_status("step_0", {})
        assert ok is False
        assert label == "—"


# ---------------------------------------------------------------------------
# _parse_trailing_json
# ---------------------------------------------------------------------------


class TestParseTrailingJson:
    def test_clean_json_object(self):
        result = _parse_trailing_json('{"a": 1, "b": "two"}')
        assert result == {"a": 1, "b": "two"}

    def test_trailing_json_after_stdout_noise(self):
        text = (
            "INFO: starting audit\n"
            "Step 1: PASSED\n"
            "Step 2: PASSED\n"
            '{"step_1": {"status": "PASSED"}, "step_2": {"status": "PASSED"}}'
        )
        result = _parse_trailing_json(text)
        assert result == {
            "step_1": {"status": "PASSED"},
            "step_2": {"status": "PASSED"},
        }

    def test_nested_braces_handled_by_depth_counting(self):
        text = 'noise {{{ not json\n{"outer": {"inner": {"deep": 1}}}'
        result = _parse_trailing_json(text)
        assert result == {"outer": {"inner": {"deep": 1}}}

    def test_no_closing_brace_returns_none(self):
        assert _parse_trailing_json("no json here at all") is None

    def test_empty_string_returns_none(self):
        assert _parse_trailing_json("") is None

    def test_unbalanced_extra_closing_brace_returns_none(self):
        # depth counting from the last "}" never reaches 0 with a start
        # index, since there's one more "}" than "{".
        assert _parse_trailing_json('{"a": 1}}') is None

    def test_dangling_open_brace_before_valid_json_still_parses_trailing_object(self):
        # The scan starts from the LAST "}" and walks backward, so an
        # earlier unbalanced "{" before a complete trailing object does not
        # prevent extraction of that trailing object.
        text = '{ dangling open\n{"a": 1}'
        result = _parse_trailing_json(text)
        assert result == {"a": 1}

    def test_malformed_json_inside_balanced_braces_returns_none(self):
        assert _parse_trailing_json("{'a': 1}") is None  # single quotes, not valid JSON

    def test_no_opening_brace_for_the_trailing_close_returns_none(self):
        assert _parse_trailing_json("some text }") is None
