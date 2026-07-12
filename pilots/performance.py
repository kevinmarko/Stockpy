"""Honest performance metrics for a Stockpy Pilot.

This is a **read-only** layer: it reads a Pilot's validated, PBO/DSR-gated
backtest summary off disk (``reports/<validation_strategy_id>_validation_summary.json``,
the JSON produced by ``validation.harness.ValidationReport.to_summary_dict()``)
and surfaces the headline metrics for the marketplace list and Pilot-detail page.

Design constraints (mirror the wider codebase conventions):

* **Dependency-light** — imports only ``settings`` + stdlib. NEVER imports the
  heavy engines or the validation harness, so it is safe to import on the API
  read path.
* **Never fabricate** (CONSTRAINT #4) — when a Pilot has no ``validation_strategy_id``,
  or its summary file is missing/unreadable, we return ``metrics=None`` /
  ``curve=None`` with an honest ``reason`` string. We NEVER synthesize a curve
  or invent metrics. No per-range equity curve is persisted yet, so ``curve`` is
  always ``None`` and ``range`` is echoed for API symmetry only.
* **Dead-letter resilient** (CONSTRAINT #6) — a missing/corrupt file degrades to
  ``None``, never an exception.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from settings import settings

logger = logging.getLogger(__name__)

__all__ = [
    "load_validation_summary",
    "pilot_performance",
    "pilot_headline",
]

# The five headline fields surfaced on the marketplace list / detail badge.
# Kept as a module constant so the headline helper and the detail path agree.
_HEADLINE_KEYS = ("sharpe", "dsr", "pbo", "max_drawdown", "deployable")


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


def pilot_performance(
    pilot: Any,
    range: str = "1M",  # noqa: A002 - matches the ?range= API query param name
    reports_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a Pilot's performance payload for the detail / performance endpoint.

    Shape: ``{"metrics": {...} | None, "curve": None, "benchmark": None,
    "reason": str | None, "range": str}``.

    * ``metrics`` is the full validated summary dict when available, else
      ``None``.
    * ``curve`` and ``benchmark`` are always ``None`` in v1 — no per-Pilot
      equity series is persisted yet, and we NEVER synthesize one (CONSTRAINT #4).
    * ``reason`` is an honest human-readable explanation whenever ``metrics`` or
      ``curve`` is unavailable, else ``None``.
    * ``range`` is echoed for API symmetry; since no per-range curve exists it
      does not change the output.
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

    # Metrics exist; curve does not (never persisted per-Pilot yet).
    return {
        "metrics": summary,
        "curve": None,
        "benchmark": None,
        "reason": "no backtest series persisted",
        "range": range,
    }
