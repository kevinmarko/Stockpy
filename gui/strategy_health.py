"""
gui/strategy_health.py
======================
Read-side consumer of the Gravity AI Review Suite's verification report.

The Gravity suite writes ``output/gravity_verification_report.json``
atomically (write-then-rename) at the end of every audit run.  This module
reads that file tolerantly and evaluates each strategy entry against the
canonical deployability thresholds from :mod:`validation.thresholds` — the
single source of truth shared with :mod:`validation.harness`.

Public API
----------
``DeployabilityGate``  — per-metric gate result (frozen dataclass).
``StrategyHealth``     — per-strategy health record (frozen dataclass).
``read_gravity_report``— tolerant reader: missing → [], corrupt → [].
``evaluate_gate``      — evaluate one strategy dict against thresholds.

CONSTRAINT #4 — no fabricated data
------------------------------------
``read_gravity_report`` returns ``[]`` on any read/parse failure rather than
inventing placeholder rows.  ``evaluate_gate`` returns ``None`` for any
metric that is absent or NaN rather than fabricating a pass/fail.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Canonical path written by the Gravity suite.
_DEFAULT_REPORT_PATH = Path("output") / "gravity_verification_report.json"


@dataclass(frozen=True)
class DeployabilityGate:
    """Result of evaluating one metric against its threshold.

    Attributes
    ----------
    metric    : Metric name (e.g. ``"PBO"``, ``"DSR"``).
    value     : Observed value (``None`` if absent/NaN).
    threshold : The threshold the value is compared against.
    direction : ``"below"`` (value must be < threshold) or ``"above"`` (value must be > threshold).
    passed    : ``True`` if the gate passed.  ``None`` if value unavailable — no fabrication.
    """

    metric: str
    value: Optional[float]
    threshold: float
    direction: str  # "below" | "above"
    passed: Optional[bool]


@dataclass(frozen=True)
class StrategyHealth:
    """Aggregated health record for one strategy entry.

    Attributes
    ----------
    strategy_id      : Canonical strategy identifier.
    deployable       : Final deployable flag from the report (``None`` = unknown).
    gates            : Per-metric gate evaluations.
    is_options_selling: Whether tail-scenario stress gate applies.
    stress_passed    : Tail-scenario gate result (``None`` when not applicable).
    last_audited_at  : ISO-8601 timestamp string from the report.
    """

    strategy_id: str
    deployable: Optional[bool]
    gates: List[DeployabilityGate]
    is_options_selling: bool
    stress_passed: Optional[bool]
    last_audited_at: Optional[str]


def _nan_safe(v: Any) -> Optional[float]:
    """Return ``None`` for missing or NaN values; otherwise float."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def read_gravity_report(
    path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Read the Gravity verification report and return the ``strategies`` list.

    Parameters
    ----------
    path:
        Path to the JSON file.  Defaults to
        ``output/gravity_verification_report.json``.

    Returns
    -------
    list[dict]
        The ``strategies`` array from the report, or ``[]`` on any failure
        (missing file, unreadable, corrupt JSON, wrong schema).  Callers
        must handle the empty-list case — CONSTRAINT #4, never fabricate.
    """
    target = Path(path) if path is not None else _DEFAULT_REPORT_PATH
    if not target.exists():
        logger.debug("gravity_verification_report not found at %s", target)
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("gravity_verification_report parse failed (%s): %s", target, exc)
        return []
    if not isinstance(payload, dict):
        logger.warning("gravity_verification_report root is not a dict")
        return []
    strategies = payload.get("strategies", [])
    if not isinstance(strategies, list):
        logger.warning("gravity_verification_report.strategies is not a list")
        return []
    return strategies


def evaluate_gate(strategy_dict: Dict[str, Any]) -> StrategyHealth:
    """Evaluate a strategy dict from the gravity report against thresholds.

    Reads thresholds from :mod:`validation.thresholds` — the canonical
    single source of truth.  Any metric absent or NaN produces a gate with
    ``passed=None`` (not fabricated).

    Parameters
    ----------
    strategy_dict:
        One element of the ``strategies`` list from the report.

    Returns
    -------
    StrategyHealth
        Structured health record.
    """
    from validation.thresholds import (
        DSR_MIN,
        MAX_DRAWDOWN_MAX,
        NET_SHARPE_MIN,
        PBO_MAX,
    )

    sid = str(strategy_dict.get("strategy_id", "unknown"))

    pbo_val = _nan_safe(strategy_dict.get("pbo"))
    dsr_val = _nan_safe(strategy_dict.get("dsr"))
    sharpe_val = _nan_safe(strategy_dict.get("net_sharpe"))
    maxdd_val = _nan_safe(strategy_dict.get("max_drawdown"))

    def _gate(metric, value, threshold, direction):
        if value is None:
            passed = None
        elif direction == "below":
            passed = value < threshold
        else:
            passed = value > threshold
        return DeployabilityGate(
            metric=metric,
            value=value,
            threshold=threshold,
            direction=direction,
            passed=passed,
        )

    gates = [
        _gate("PBO",        pbo_val,    PBO_MAX,         "below"),
        _gate("DSR",        dsr_val,    DSR_MIN,         "above"),
        _gate("Net Sharpe", sharpe_val, NET_SHARPE_MIN,  "above"),
        _gate("Max DD",     maxdd_val,  MAX_DRAWDOWN_MAX, "below"),
    ]

    is_options = bool(strategy_dict.get("is_options_selling", False))
    stress_passed_raw = strategy_dict.get("stress_test_passed")
    stress_passed: Optional[bool] = None if stress_passed_raw is None else bool(stress_passed_raw)

    # deployable mirrors the report field rather than re-deriving it here
    # so the GUI and the audit are always in agreement.
    deployable_raw = strategy_dict.get("deployable")
    deployable: Optional[bool] = None if deployable_raw is None else bool(deployable_raw)

    return StrategyHealth(
        strategy_id=sid,
        deployable=deployable,
        gates=gates,
        is_options_selling=is_options,
        stress_passed=stress_passed,
        last_audited_at=strategy_dict.get("last_audited_at"),
    )
