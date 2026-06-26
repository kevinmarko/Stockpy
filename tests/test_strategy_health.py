"""
tests/test_strategy_health.py
================================
Unit tests for :mod:`gui.strategy_health`.

All tests are fully offline (no network, no Streamlit).

Verified invariants
-------------------
*   Module imports cleanly.
*   ``read_gravity_report`` returns ``[]`` on a missing file (CONSTRAINT #4).
*   ``read_gravity_report`` returns ``[]`` on corrupt JSON (CONSTRAINT #4).
*   ``read_gravity_report`` returns ``[]`` when root is not a dict.
*   ``read_gravity_report`` returns ``[]`` when ``strategies`` is not a list.
*   ``read_gravity_report`` returns the strategies list on a valid file.
*   ``evaluate_gate`` produces a :class:`StrategyHealth` with all gate fields.
*   Observed value within threshold → ``gate.passed=True``.
*   Observed value breaching threshold → ``gate.passed=False``.
*   Missing metric → ``gate.passed=None`` (never fabricated — CONSTRAINT #4).
*   NaN metric → ``gate.passed=None``.
*   ``deployable`` mirrors the report field (not re-derived).
*   ``DeployabilityGate`` and ``StrategyHealth`` are frozen dataclasses.
*   Thresholds come from :mod:`validation.thresholds` (single source of truth).
"""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import pytest


# ===========================================================================
# Import
# ===========================================================================

def test_module_importable():
    from gui import strategy_health  # noqa: F401


def test_public_api():
    from gui.strategy_health import (  # noqa: F401
        DeployabilityGate,
        StrategyHealth,
        evaluate_gate,
        read_gravity_report,
    )


# ===========================================================================
# Frozen dataclasses
# ===========================================================================

def test_deployability_gate_frozen():
    from gui.strategy_health import DeployabilityGate

    g = DeployabilityGate(metric="PBO", value=0.3, threshold=0.5, direction="below", passed=True)
    with pytest.raises((AttributeError, TypeError)):
        g.passed = False  # type: ignore[misc]


def test_strategy_health_frozen():
    from gui.strategy_health import StrategyHealth

    sh = StrategyHealth(
        strategy_id="X", deployable=True, gates=[], is_options_selling=False,
        stress_passed=None, last_audited_at=None,
    )
    with pytest.raises((AttributeError, TypeError)):
        sh.strategy_id = "Y"  # type: ignore[misc]


# ===========================================================================
# read_gravity_report — CONSTRAINT #4 failures return []
# ===========================================================================

def test_missing_file_returns_empty():
    from gui.strategy_health import read_gravity_report

    result = read_gravity_report(path=Path("/tmp/__no_gravity_report__.json"))
    assert result == []


def test_corrupt_json_returns_empty():
    from gui.strategy_health import read_gravity_report

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write("{corrupt!!!")
        tmp = Path(f.name)

    try:
        result = read_gravity_report(path=tmp)
    finally:
        tmp.unlink(missing_ok=True)

    assert result == []


def test_non_dict_root_returns_empty():
    from gui.strategy_health import read_gravity_report

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump([1, 2, 3], f)
        tmp = Path(f.name)

    try:
        result = read_gravity_report(path=tmp)
    finally:
        tmp.unlink(missing_ok=True)

    assert result == []


def test_strategies_not_list_returns_empty():
    from gui.strategy_health import read_gravity_report

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"strategies": "not a list"}, f)
        tmp = Path(f.name)

    try:
        result = read_gravity_report(path=tmp)
    finally:
        tmp.unlink(missing_ok=True)

    assert result == []


def test_valid_file_returns_strategies():
    from gui.strategy_health import read_gravity_report

    strategies = [
        {"strategy_id": "S1", "pbo": 0.2, "dsr": 1.1, "net_sharpe": 0.7,
         "max_drawdown": 0.15, "deployable": True},
    ]
    payload = {"run_id": "r1", "generated_at": "2026-01-01T00:00:00+00:00", "strategies": strategies}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        tmp = Path(f.name)

    try:
        result = read_gravity_report(path=tmp)
    finally:
        tmp.unlink(missing_ok=True)

    assert len(result) == 1
    assert result[0]["strategy_id"] == "S1"


# ===========================================================================
# evaluate_gate
# ===========================================================================

def _strategy(
    pbo=None, dsr=None, net_sharpe=None, max_drawdown=None,
    deployable=None, is_options_selling=False, stress_test_passed=None,
    strategy_id="TEST",
):
    d = {"strategy_id": strategy_id, "is_options_selling": is_options_selling}
    if pbo is not None:
        d["pbo"] = pbo
    if dsr is not None:
        d["dsr"] = dsr
    if net_sharpe is not None:
        d["net_sharpe"] = net_sharpe
    if max_drawdown is not None:
        d["max_drawdown"] = max_drawdown
    if deployable is not None:
        d["deployable"] = deployable
    if stress_test_passed is not None:
        d["stress_test_passed"] = stress_test_passed
    return d


def test_evaluate_gate_returns_strategy_health():
    from gui.strategy_health import StrategyHealth, evaluate_gate

    health = evaluate_gate(_strategy(pbo=0.2, dsr=1.1, net_sharpe=0.7, max_drawdown=0.15, deployable=True))
    assert isinstance(health, StrategyHealth)
    assert len(health.gates) == 4


def test_evaluate_gate_all_pass():
    from gui.strategy_health import evaluate_gate
    from validation.thresholds import DSR_MIN, MAX_DRAWDOWN_MAX, NET_SHARPE_MIN, PBO_MAX

    health = evaluate_gate(_strategy(
        pbo=PBO_MAX - 0.01,
        dsr=DSR_MIN + 0.01,
        net_sharpe=NET_SHARPE_MIN + 0.01,
        max_drawdown=MAX_DRAWDOWN_MAX - 0.01,
        deployable=True,
    ))
    assert all(g.passed is True for g in health.gates), [g for g in health.gates if not g.passed]


def test_evaluate_gate_pbo_fail():
    from gui.strategy_health import evaluate_gate
    from validation.thresholds import PBO_MAX

    health = evaluate_gate(_strategy(pbo=PBO_MAX + 0.01))
    pbo_gate = next(g for g in health.gates if g.metric == "PBO")
    assert pbo_gate.passed is False


def test_evaluate_gate_dsr_fail():
    from gui.strategy_health import evaluate_gate
    from validation.thresholds import DSR_MIN

    health = evaluate_gate(_strategy(dsr=DSR_MIN - 0.01))
    dsr_gate = next(g for g in health.gates if g.metric == "DSR")
    assert dsr_gate.passed is False


def test_evaluate_gate_missing_metric_is_none():
    """Missing metric → gate.passed=None — CONSTRAINT #4, never fabricated."""
    from gui.strategy_health import evaluate_gate

    health = evaluate_gate({"strategy_id": "T"})  # no metrics provided
    for g in health.gates:
        assert g.passed is None, f"Expected None for {g.metric}, got {g.passed}"


def test_evaluate_gate_nan_metric_is_none():
    """NaN metric → gate.passed=None."""
    from gui.strategy_health import evaluate_gate

    health = evaluate_gate(_strategy(pbo=float("nan")))
    pbo_gate = next(g for g in health.gates if g.metric == "PBO")
    assert pbo_gate.passed is None


def test_evaluate_gate_mirrors_report_deployable():
    """``deployable`` mirrors the report field — not re-derived from gates."""
    from gui.strategy_health import evaluate_gate

    # Even if all gates would pass, mirror what the report says
    health = evaluate_gate(_strategy(
        pbo=0.1, dsr=1.2, net_sharpe=0.8, max_drawdown=0.1, deployable=False,
    ))
    assert health.deployable is False


def test_evaluate_gate_options_selling_stress_passed():
    from gui.strategy_health import evaluate_gate

    health = evaluate_gate(_strategy(
        is_options_selling=True,
        stress_test_passed=True,
        deployable=True,
    ))
    assert health.is_options_selling is True
    assert health.stress_passed is True


def test_evaluate_gate_options_selling_stress_failed():
    from gui.strategy_health import evaluate_gate

    health = evaluate_gate(_strategy(
        is_options_selling=True,
        stress_test_passed=False,
        deployable=False,
    ))
    assert health.stress_passed is False


def test_evaluate_gate_stress_none_when_not_options():
    from gui.strategy_health import evaluate_gate

    health = evaluate_gate(_strategy(is_options_selling=False))
    assert health.stress_passed is None


# ===========================================================================
# Thresholds come from validation.thresholds
# ===========================================================================

def test_gate_uses_validation_thresholds():
    """The gates' thresholds must match validation.thresholds exactly."""
    from gui.strategy_health import evaluate_gate
    from validation.thresholds import DSR_MIN, MAX_DRAWDOWN_MAX, NET_SHARPE_MIN, PBO_MAX

    health = evaluate_gate(_strategy(pbo=0.1, dsr=1.0, net_sharpe=0.6, max_drawdown=0.2))
    gate_thresholds = {g.metric: g.threshold for g in health.gates}

    assert math.isclose(gate_thresholds["PBO"], PBO_MAX)
    assert math.isclose(gate_thresholds["DSR"], DSR_MIN)
    assert math.isclose(gate_thresholds["Net Sharpe"], NET_SHARPE_MIN)
    assert math.isclose(gate_thresholds["Max DD"], MAX_DRAWDOWN_MAX)
