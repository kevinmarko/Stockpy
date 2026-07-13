"""Honest performance metrics for a Stockpy Pilot.

This is a **read-only** layer: it reads a Pilot's validated, PBO/DSR-gated
backtest summary off disk (``reports/<validation_strategy_id>_validation_summary.json``,
the JSON produced by ``validation.harness.ValidationReport.to_summary_dict()``)
and surfaces the headline metrics for the marketplace list and Pilot-detail page.

**Per-Pilot equity curve (D2 decision, 2026-07-13):** ``pilot_performance()``
also attempts to read ``reports/<validation_strategy_id>_equity_curve.json``
(written by ``validation.harness.StrategyValidationHarness._write_equity_curve``)
— the 60/40 walk-forward split's HELD-OUT, out-of-sample test-period returns,
converted to a cumulative equity series. This is deliberately NOT the
full-sample curve used for the headline Sharpe/Sortino/Calmar metrics, which
is fit by selecting the best IN-SAMPLE Sharpe over the whole window and would
be misleading to present as "the Pilot's track record". A strategy validated
before this file existed (or whose validation run degraded to an empty
returns series) has no equity-curve file yet; ``curve`` stays honestly
``None`` with an explanatory ``reason`` until the next validation run.

Design constraints (mirror the wider codebase conventions):

* **Dependency-light** — imports only ``settings`` + stdlib. NEVER imports the
  heavy engines or the validation harness, so it is safe to import on the API
  read path.
* **Never fabricate** (CONSTRAINT #4) — when a Pilot has no ``validation_strategy_id``,
  or its summary file is missing/unreadable, we return ``metrics=None`` /
  ``curve=None`` with an honest ``reason`` string. We NEVER synthesize a curve
  or invent metrics; when a real curve file exists it is filtered by
  ``range`` (never extrapolated beyond what's persisted) before being returned.
* **Dead-letter resilient** (CONSTRAINT #6) — a missing/corrupt file degrades to
  ``None``, never an exception.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from settings import settings

logger = logging.getLogger(__name__)

__all__ = [
    "load_validation_summary",
    "load_equity_curve",
    "pilot_performance",
    "pilot_headline",
]

# The five headline fields surfaced on the marketplace list / detail badge.
# Kept as a module constant so the headline helper and the detail path agree.
_HEADLINE_KEYS = ("sharpe", "dsr", "pbo", "max_drawdown", "deployable")

# Maps the API's ?range= values to a lookback window in calendar days, for
# slicing the persisted equity curve. A range longer than the persisted
# history is NOT an error -- we just return whatever's actually there
# (never fabricate history beyond what was validated).
_RANGE_DAYS = {
    "1W": 7,
    "1M": 30,
    "3M": 91,
    "6M": 182,
    "1Y": 365,
    "2Y": 730,
}


def _reports_dir(reports_dir: Optional[str]) -> Path:
    """Resolve the directory that holds ``*_validation_summary.json`` files.

    Defaults to the repo-level ``reports/`` directory; tests pass an override
    (e.g. ``tests/fixtures``) so they can point at a checked-in fixture.
    """
    if reports_dir is not None:
        return Path(reports_dir)
    return Path("reports")


def load_validation_summary(
    strategy_id: str,
    reports_dir: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Read ``<reports_dir>/<strategy_id>_validation_summary.json``.

    Parameters
    ----------
    strategy_id:
        The validation strategy id (a ``STRATEGY_REGISTRY`` key /
        ``Pilot.validation_strategy_id``), e.g. ``"timeseries_momentum"``.
    reports_dir:
        Directory to read from. ``None`` -> the default ``reports/`` dir. Tests
        point this at ``tests/fixtures``.

    Returns
    -------
    dict | None
        The parsed summary (schema per ``ValidationReport.to_summary_dict()``:
        ``sharpe``, ``dsr``, ``pbo``, ``max_drawdown``, ``deployable``,
        ``stress_gate_passed`` …) or ``None`` when the file is absent, empty,
        unreadable, or not a JSON object. NEVER raises (CONSTRAINT #6).
    """
    if not strategy_id:
        return None
    path = _reports_dir(reports_dir) / f"{strategy_id}_validation_summary.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.debug("load_validation_summary(%s): unreadable %s: %s", strategy_id, path, exc)
        return None
    if not isinstance(data, dict):
        logger.debug("load_validation_summary(%s): %s is not a JSON object", strategy_id, path)
        return None
    return data


def pilot_headline(pilot: Any, reports_dir: Optional[str] = None) -> Dict[str, Any]:
    """Return just the five headline fields for the marketplace list.

    Returns ``{sharpe, dsr, pbo, max_drawdown, deployable}`` with every value
    ``None`` when the Pilot has no validated backtest (``validation_strategy_id
    is None``) or its summary can't be loaded — never fabricated (CONSTRAINT #4).
    """
    headline: Dict[str, Any] = {k: None for k in _HEADLINE_KEYS}
    strategy_id = getattr(pilot, "validation_strategy_id", None)
    if not strategy_id:
        return headline
    summary = load_validation_summary(strategy_id, reports_dir=reports_dir)
    if summary is None:
        return headline
    for key in _HEADLINE_KEYS:
        # A field genuinely absent from the summary stays None (honest),
        # never coerced to 0.0.
        headline[key] = summary.get(key, None)
    return headline


def load_equity_curve(
    strategy_id: str,
    reports_dir: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Read ``<reports_dir>/<strategy_id>_equity_curve.json``.

    Returns the raw ``{"strategy", "source", "note", "points"}`` dict (see
    ``validation.harness.StrategyValidationHarness._write_equity_curve``) or
    ``None`` when the file is absent, empty, unreadable, malformed, or has no
    points -- NEVER raises (CONSTRAINT #6), and never synthesizes points that
    aren't actually on disk (CONSTRAINT #4).
    """
    if not strategy_id:
        return None
    path = _reports_dir(reports_dir) / f"{strategy_id}_equity_curve.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.debug("load_equity_curve(%s): unreadable %s: %s", strategy_id, path, exc)
        return None
    if not isinstance(data, dict):
        return None
    points = data.get("points")
    if not isinstance(points, list) or not points:
        return None
    return data


def _filter_curve_by_range(points: List[Dict[str, Any]], range: str) -> List[Dict[str, Any]]:  # noqa: A002
    """Slice ``points`` (sorted ascending by date) to the trailing window
    named by ``range``. An unrecognized ``range`` or a window longer than the
    persisted history returns every point unfiltered -- we only ever narrow
    what's on disk, never widen/fabricate it."""
    days = _RANGE_DAYS.get(range)
    if not days or not points:
        return points
    try:
        last_date = datetime.strptime(points[-1]["date"], "%Y-%m-%d")
    except (KeyError, ValueError):
        return points
    cutoff = last_date - timedelta(days=days)
    filtered = [
        p for p in points
        if _safe_parse_date(p.get("date")) is not None and _safe_parse_date(p["date"]) >= cutoff
    ]
    return filtered or points


def _safe_parse_date(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def pilot_performance(
    pilot: Any,
    range: str = "1M",  # noqa: A002 - matches the ?range= API query param name
    reports_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a Pilot's performance payload for the detail / performance endpoint.

    Shape: ``{"metrics": {...} | None, "curve": [{"date","value"}, ...] | None,
    "benchmark": None, "reason": str | None, "range": str}``.

    * ``metrics`` is the full validated summary dict when available, else
      ``None``.
    * ``curve`` is the persisted out-of-sample equity curve (see the module
      docstring's D2 decision) filtered to ``range``, or ``None`` when no
      curve has been persisted yet for this strategy -- we NEVER synthesize
      one (CONSTRAINT #4). ``benchmark`` stays ``None`` in v1 (no benchmark
      series is persisted alongside the curve yet).
    * ``reason`` is an honest human-readable explanation whenever ``metrics`` or
      ``curve`` is unavailable, else ``None``.
    * ``range`` is echoed for API symmetry and used to slice ``curve`` when
      one exists.
    """
    strategy_id = getattr(pilot, "validation_strategy_id", None)

    if not strategy_id:
        return {
            "metrics": None,
            "curve": None,
            "benchmark": None,
            "reason": "no validated backtest for this pilot",
            "range": range,
        }

    summary = load_validation_summary(strategy_id, reports_dir=reports_dir)
    if summary is None:
        return {
            "metrics": None,
            "curve": None,
            "benchmark": None,
            "reason": (
                f"no validation summary found for '{strategy_id}' "
                "(run the validation pipeline first)"
            ),
            "range": range,
        }

    # Metrics exist. Attempt the persisted out-of-sample equity curve too.
    curve_data = load_equity_curve(strategy_id, reports_dir=reports_dir)
    if curve_data is None:
        return {
            "metrics": summary,
            "curve": None,
            "benchmark": None,
            "reason": "no backtest series persisted",
            "range": range,
        }

    filtered_points = _filter_curve_by_range(curve_data["points"], range)
    return {
        "metrics": summary,
        "curve": filtered_points,
        "benchmark": None,
        "reason": curve_data.get("note", "out-of-sample walk-forward equity curve"),
        "range": range,
    }
