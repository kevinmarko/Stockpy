"""Tests for numeric_utils.safe_float -- the canonical F2 dedup target for 4
of 7 formerly-independent ``_safe_float`` copies (see
docs/module_efficiency_redundancy_audit.md and
.claude/module_efficiency_audit_remediation_plan.md's PR 2)."""
from __future__ import annotations

import math

from numeric_utils import safe_float


class TestNoneAndMissing:
    def test_none_returns_none(self):
        assert safe_float(None) is None


class TestFiniteValues:
    def test_int_coerces_to_float(self):
        assert safe_float(5) == 5.0
        assert isinstance(safe_float(5), float)

    def test_float_passes_through(self):
        assert safe_float(3.14) == 3.14

    def test_numeric_string_coerces(self):
        assert safe_float("2.5") == 2.5

    def test_genuine_zero_is_not_none(self):
        """CONSTRAINT #4: a real 0 must round-trip as 0.0, never collapse to
        the same sentinel used for "not reported"."""
        assert safe_float(0) == 0.0
        assert safe_float(0) is not None
        assert safe_float("0") == 0.0


class TestNonFiniteFiltering:
    def test_nan_returns_none(self):
        assert safe_float(float("nan")) is None

    def test_positive_infinity_returns_none(self):
        assert safe_float(float("inf")) is None

    def test_negative_infinity_returns_none(self):
        assert safe_float(float("-inf")) is None

    def test_nan_string_returns_none(self):
        # float("nan") succeeds on this literal string -- still filtered.
        assert safe_float("nan") is None


class TestUncoercibleInputs:
    def test_non_numeric_string_returns_none(self):
        assert safe_float("not-a-number") is None

    def test_dict_returns_none(self):
        assert safe_float({"a": 1}) is None

    def test_list_returns_none(self):
        assert safe_float([1, 2, 3]) is None

    def test_callable_returns_none(self):
        """A bound method or function passed by mistake (e.g. a caller
        forgot to invoke it) must degrade to None, not raise -- matches
        pilots/vol_mispricing.py's original ``callable(val)`` guard, folded
        into the shared implementation."""
        assert safe_float(len) is None
        assert safe_float(lambda: 5) is None


class TestNeverRaises:
    def test_object_instance_never_raises(self):
        class Weird:
            pass

        assert safe_float(Weird()) is None
