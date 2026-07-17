"""pilots/strategy_health.py — per-gate deployability breakdown for every Pilot.

Ports the "Strategy Health / Deployability Gates" concept from the retired
Streamlit Command Center (``gui/panels/gravity_audit.py``'s Strategy Health
section + ``gui/panels/validation_lab.py``) into a catalog-wide read for the
Pilots PWA: for EVERY Pilot, WHY its underlying validated strategy is or isn't
deployable — the actual per-gate value vs. the required threshold, not just the
pass/fail badge ``pilots/performance.py::pilot_headline`` already surfaces
elsewhere.

This is intentionally a thin layer over two already-existing, confirmed-safe
readers:

* ``pilots.performance.load_validation_summary`` — reads the full validated
  summary JSON (schema = ``validation.harness.ValidationReport.to_summary_dict()``)
  at ``reports/<strategy_id>_validation_summary.json``. Reused directly rather
  than re-parsing the file here.
* ``validation.thresholds`` — the single source of truth for every gate
  threshold (``PBO_MAX``, ``DSR_MIN``, ``NET_SHARPE_MIN``, ``MAX_DRAWDOWN_MAX``).
  Imported directly; the numbers are NEVER re-typed here (mirrors that module's
  own "never hard-code these numbers elsewhere" directive).

Run-over-run trend
-------------------
``validation.harness.read_validation_history`` reads the persisted
``reports/history/<strategy_id>_validation_history.jsonl`` (one row per past
harness run), but importing ``validation.harness`` itself pulls in a much
heavier chain at module scope (``yfinance``, ``universe_engine``,
``execution.cost_model``, ``validation.metrics``, ``validation.stress_scenarios``)
that this dependency-light read path should not pay for. Per that function's
own docstring the read logic is intentionally tiny (a JSONL tail read), so — in
the same spirit as ``pilots/run_status.py`` PORTING (not importing) the
``preflight_check.py`` freshness logic — :func:`_read_validation_history_rows`
below reproduces the exact same file-naming convention and parsing logic
locally, using only stdlib.

Honesty (CONSTRAINT #4): a Pilot with no ``validation_strategy_id``, or whose
summary file is missing/unreadable, gets ``deployable=None`` + empty ``gates``
+ an honest ``reason`` string — never a fabricated gate result. A gate whose
underlying value is absent from the summary reports ``passed=None`` (unknown),
never coerced to a guessed pass/fail.

Dead-letter resilient (CONSTRAINT #6): every public function degrades to an
honest empty/partial shape on any failure; nothing here raises.

Dependency-light (pinned by
``tests/test_pilots_strategy_matrix.py::test_pilots_read_helpers_stay_dependency_light``,
parametrized to include ``"strategy_health"``): imports only stdlib +
``settings`` + the two confirmed-safe ``pilots.catalog`` / ``pilots.performance``
readers + the pure-constants ``validation.thresholds`` module — never
``validation.harness`` itself, never a heavy engine.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from pilots.catalog import Pilot, list_pilots
from pilots.performance import load_validation_summary
from validation import thresholds

logger = logging.getLogger(__name__)

__all__ = [
    "pilot_strategy_health",
    "strategy_health_rows",
]

# (summary key, display label, threshold, direction). Thresholds are read live
# from validation.thresholds — never re-typed as literals here.
_GATE_SPECS: tuple = (
    ("pbo", "Probability of Backtest Overfitting", thresholds.PBO_MAX, "below"),
    ("dsr", "Deflated Sharpe Ratio", thresholds.DSR_MIN, "above"),
    ("sharpe", "Net Sharpe Ratio", thresholds.NET_SHARPE_MIN, "above"),
    ("max_drawdown", "Max Drawdown", thresholds.MAX_DRAWDOWN_MAX, "below"),
)

# Most-recent-N trend points returned per pilot — bounds the response size;
# the persisted JSONL history can otherwise grow unbounded run-over-run.
_DEFAULT_TREND_LIMIT = 12


def _gate_passed(value: Any, threshold: float, direction: str) -> Optional[bool]:
    """Compare ``value`` to ``threshold`` per ``direction``; ``None`` if unknown.

    Never fabricates a verdict for a missing/non-numeric value (CONSTRAINT #4).
    """
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric:  # NaN check without importing math for one use
        return None
    return numeric < threshold if direction == "below" else numeric > threshold


def _build_gates(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build the per-gate breakdown list from a loaded validation summary."""
    gates: List[Dict[str, Any]] = []
    for key, label, threshold, direction in _GATE_SPECS:
        value = summary.get(key)
        gates.append(
            {
                "key": key,
                "label": label,
                "value": value,
                "threshold": threshold,
                "direction": direction,
                "passed": _gate_passed(value, threshold, direction),
            }
        )
    return gates


def _read_validation_history_rows(
    strategy_id: str,
    history_dir: str = "reports/history",
) -> List[Dict[str, Any]]:
    """Read ``<history_dir>/<strategy_id>_validation_history.jsonl``.

    PORTS (does not import) ``validation.harness.read_validation_history``'s
    read logic verbatim — same filename convention, same tolerant per-line
    JSON parsing, same dead-letter behavior — to avoid pulling that module's
    much heavier top-level import chain onto this dependency-light path (see
    module docstring). ``[]`` on any failure or when no history exists yet;
    never raises.
    """
    safe_name = strategy_id.replace(" ", "_").replace("/", "_")
    target = Path(history_dir) / f"{safe_name}_validation_history.jsonl"
    if not target.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        logger.debug("strategy_health: could not read %s: %s", target, exc)
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            logger.debug(
                "strategy_health(%s): skipping corrupt history line: %s", strategy_id, exc
            )
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _trend_points(rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Narrow raw history rows down to the small set of fields a sparkline needs.

    Oldest-first (matches ``read_validation_history``'s own ordering), capped
    to the most recent ``limit`` runs. A run missing a field reports ``None``
    for it rather than fabricating a value.
    """
    tail = rows[-limit:] if limit else rows
    return [
        {
            "report_date": row.get("report_date"),
            "pbo": row.get("pbo"),
            "dsr": row.get("dsr"),
            "sharpe": row.get("sharpe"),
            "max_drawdown": row.get("max_drawdown"),
            "deployable": row.get("deployable"),
        }
        for row in tail
    ]


def pilot_strategy_health(
    pilot: Pilot,
    *,
    reports_dir: Optional[str] = None,
    history_dir: str = "reports/history",
    trend_limit: int = _DEFAULT_TREND_LIMIT,
) -> Dict[str, Any]:
    """Return one Pilot's deployability-gate breakdown.

    Shape::

        {
            "pilot_id": str,
            "pilot_name": str,
            "strategy_id": str | None,
            "deployable": bool | None,
            "gates": [{"key", "label", "value", "threshold", "direction", "passed"}, ...],
            "is_options_selling": bool | None,
            "stress_gate_passed": bool | None,
            "report_date": str | None,
            "trend": [{"report_date", "pbo", "dsr", "sharpe", "max_drawdown", "deployable"}, ...],
            "reason": str | None,
        }

    ``gates`` is ``[]`` and every other field is ``None`` (with an honest
    ``reason``) when the Pilot has no ``validation_strategy_id`` or its summary
    is missing/unreadable — NEVER a fabricated gate result (CONSTRAINT #4).
    ``trend`` is a best-effort run-over-run series; a missing/empty history
    file degrades to ``[]`` without affecting the rest of the payload.
    """
    strategy_id = pilot.validation_strategy_id
    base: Dict[str, Any] = {
        "pilot_id": pilot.id,
        "pilot_name": pilot.name,
        "strategy_id": strategy_id,
        "deployable": None,
        "gates": [],
        "is_options_selling": None,
        "stress_gate_passed": None,
        "report_date": None,
        "trend": [],
        "reason": None,
    }

    if not strategy_id:
        base["reason"] = "no validated backtest for this pilot"
        return base

    summary = load_validation_summary(strategy_id, reports_dir=reports_dir)
    if summary is None:
        base["reason"] = (
            f"no validation summary found for '{strategy_id}' "
            "(run the validation pipeline first)"
        )
        return base

    base["deployable"] = summary.get("deployable")
    base["gates"] = _build_gates(summary)
    base["is_options_selling"] = summary.get("is_options_selling")
    base["stress_gate_passed"] = summary.get("stress_gate_passed")
    base["report_date"] = summary.get("report_date")

    try:
        history_rows = _read_validation_history_rows(strategy_id, history_dir=history_dir)
        base["trend"] = _trend_points(history_rows, trend_limit)
    except Exception as exc:  # noqa: BLE001 - trend is best-effort, never fatal
        logger.debug("strategy_health(%s): trend read failed: %s", strategy_id, exc)
        base["trend"] = []

    return base


def strategy_health_rows(
    *,
    reports_dir: Optional[str] = None,
    history_dir: str = "reports/history",
    trend_limit: int = _DEFAULT_TREND_LIMIT,
) -> List[Dict[str, Any]]:
    """Return the deployability-gate breakdown for every catalog Pilot.

    One entry per ``pilots.catalog.list_pilots()`` row, in catalog order.
    Never raises (CONSTRAINT #6) — a single Pilot's read failure degrades to
    its own honest empty entry (see :func:`pilot_strategy_health`) rather than
    aborting the whole list.
    """
    rows: List[Dict[str, Any]] = []
    for pilot in list_pilots():
        try:
            rows.append(
                pilot_strategy_health(
                    pilot,
                    reports_dir=reports_dir,
                    history_dir=history_dir,
                    trend_limit=trend_limit,
                )
            )
        except Exception as exc:  # noqa: BLE001 - one bad pilot must not abort the list
            logger.debug("strategy_health_rows: pilot '%s' failed: %s", pilot.id, exc)
            rows.append(
                {
                    "pilot_id": pilot.id,
                    "pilot_name": pilot.name,
                    "strategy_id": pilot.validation_strategy_id,
                    "deployable": None,
                    "gates": [],
                    "is_options_selling": None,
                    "stress_gate_passed": None,
                    "report_date": None,
                    "trend": [],
                    "reason": "strategy health read failed",
                }
            )
    return rows
