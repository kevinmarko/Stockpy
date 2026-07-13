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
  or invent metrics. The ``curve`` is the REAL downsampled base-100 OOS equity
  series persisted by the harness (``equity_curve`` in the summary JSON); when a
  summary predates that field, or the strategy produced no meaningful returns, the
  curve stays ``None``. The ``range`` param is an honest tail-slice (zoom) of that
  same series — never a re-simulation.
* **Dead-letter resilient** (CONSTRAINT #6) — a missing/corrupt file degrades to
  ``None``, never an exception.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

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

# Approximate calendar-day windows for the PWA's range toggles. The persisted
# equity curve is downsampled (~120 points over the full OOS span), so a short
# range is an honest tail zoom limited by that resolution — not a re-run.
_RANGE_DAYS: Dict[str, int] = {
    "1W": 7,
    "1M": 31,
    "3M": 93,
    "6M": 186,
    "1Y": 372,
    "2Y": 745,
}


def _slice_curve_by_range(
    curve: List[Dict[str, Any]], range: str  # noqa: A002 - API query param name
) -> List[Dict[str, Any]]:
    """Return the tail of ``curve`` covering the last ``range`` calendar days.

    A pure zoom on the persisted series: keeps points whose ISO ``date`` is within
    ``_RANGE_DAYS[range]`` of the last point. An unknown range (the API validates,
    but be defensive) or unparseable dates return the full curve. Never returns a
    single-point curve when ≥2 points exist (a chart needs two), so a very short
    range on a sparse downsampled curve still renders — falls back to the last 2.
    """
    days = _RANGE_DAYS.get((range or "").upper())
    if not days or len(curve) <= 2:
        return curve
    try:
        last_iso = str(curve[-1].get("date"))
        last_day = date.fromisoformat(last_iso)
        cutoff = last_day - timedelta(days=days)
        sliced = [p for p in curve if date.fromisoformat(str(p.get("date"))) >= cutoff]
    except (ValueError, TypeError):
        return curve
    if len(sliced) < 2:
        return curve[-2:]
    return sliced


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

    Shape: ``{"metrics": {...} | None, "curve": [...] | None,
    "benchmark": [...] | None, "macro_benchmark": [...] | None,
    "reason": str | None, "range": str}``.

    * ``metrics`` is the full validated summary dict when available, else
      ``None``.
    * ``curve`` is the REAL downsampled base-100 OOS equity series persisted by
      the harness (``equity_curve`` in the summary), tail-sliced to ``range``.
      ``None`` when the summary predates that field or the strategy had no
      meaningful returns — NEVER synthesized (CONSTRAINT #4).
    * ``benchmark`` is the REAL persisted base-100 buy-&-hold-of-the-underlying
      curve (``benchmark_curve`` in the summary — the harness's ``y`` return
      series, aligned to the same OOS index as ``curve``), tail-sliced to the
      same ``range``. ``None`` when the summary predates that field or no
      meaningful underlying series was available — NEVER synthesized.
    * ``macro_benchmark`` is the REAL persisted base-100 SPY (broad-market)
      buy-&-hold curve (``macro_benchmark_curve`` in the summary — a SEPARATE,
      explicitly-labeled market overlay computed over the same OOS window),
      tail-sliced to the same ``range``. Independent of both ``curve`` and
      ``benchmark``. ``None`` when the summary predates that field, SPY data was
      unavailable, or the strategy's underlying already IS SPY (redundant) —
      NEVER synthesized.
    * ``reason`` is an honest human-readable explanation whenever ``metrics`` or
      ``curve`` is unavailable, else ``None``.
    * ``range`` is a tail-zoom on the persisted series (see
      :func:`_slice_curve_by_range`).
    """
    strategy_id = getattr(pilot, "validation_strategy_id", None)

    if not strategy_id:
        return {
            "metrics": None,
            "curve": None,
            "benchmark": None,
            "macro_benchmark": None,
            "reason": "no validated backtest for this pilot",
            "range": range,
        }

    summary = load_validation_summary(strategy_id, reports_dir=reports_dir)
    if summary is None:
        return {
            "metrics": None,
            "curve": None,
            "benchmark": None,
            "macro_benchmark": None,
            "reason": (
                f"no validation summary found for '{strategy_id}' "
                "(run the validation pipeline first)"
            ),
            "range": range,
        }

    # Benchmark is independent of the strategy curve: surface the persisted
    # buy-&-hold series (tail-sliced to the same range) when present and
    # renderable (>= 2 points), else honestly None (older summary / no meaningful
    # underlying series) — never fabricated (CONSTRAINT #4).
    raw_benchmark = summary.get("benchmark_curve")
    benchmark = (
        _slice_curve_by_range(raw_benchmark, range)
        if isinstance(raw_benchmark, list) and len(raw_benchmark) >= 2
        else None
    )

    # Macro benchmark (SPY / broad market) is a SEPARATE, explicitly-labeled
    # overlay, independent of both the strategy curve and the underlying
    # benchmark: surface the persisted SPY buy-&-hold series (tail-sliced to the
    # same range) when present and renderable (>= 2 points), else honestly None
    # (older summary / SPY unavailable / underlying already IS SPY → redundant) —
    # never fabricated (CONSTRAINT #4).
    raw_macro_benchmark = summary.get("macro_benchmark_curve")
    macro_benchmark = (
        _slice_curve_by_range(raw_macro_benchmark, range)
        if isinstance(raw_macro_benchmark, list) and len(raw_macro_benchmark) >= 2
        else None
    )

    # Metrics exist. Surface the persisted equity curve when present, tail-sliced
    # to the requested range; a missing/empty curve stays None with an honest
    # reason (older summary, or no meaningful returns) — never fabricated.
    raw_curve = summary.get("equity_curve")
    if isinstance(raw_curve, list) and len(raw_curve) >= 2:
        return {
            "metrics": summary,
            "curve": _slice_curve_by_range(raw_curve, range),
            "benchmark": benchmark,
            "macro_benchmark": macro_benchmark,
            "reason": None,
            "range": range,
        }

    return {
        "metrics": summary,
        "curve": None,
        "benchmark": benchmark,
        "macro_benchmark": macro_benchmark,
        "reason": "no backtest series persisted",
        "range": range,
    }
