import pytest
from experiments.assignment import assign_arm

def test_assignment_is_deterministic():
    arm1 = assign_arm("exp1", "AAPL", "2026-08-23", ["control", "variant"], [0.5, 0.5])
    arm2 = assign_arm("exp1", "AAPL", "2026-08-23", ["control", "variant"], [0.5, 0.5])
    assert arm1 == arm2

def test_assignment_respects_allocations():
    arm = assign_arm("exp1", "AAPL", "2026-08-23", ["control", "variant"], [1.0, 0.0])
    assert arm == "control"
    arm = assign_arm("exp1", "AAPL", "2026-08-23", ["control", "variant"], [0.0, 1.0])
    assert arm == "variant"
