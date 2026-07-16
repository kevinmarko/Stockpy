"""pilots/forecast_skill.py — per-symbol forecast reliability for the PWA.
=========================================================================

Surfaces the forecast-skill tracker (``forecasting/forecast_tracker.py``, backed
by the ``forecast_errors`` table in ``quant_platform.db``) for the mobile
SymbolDetail screen (``GET /symbols/{ticker}/forecast``): a reliability curve
(realized percent-error by bin, per model) plus the live inverse-RMSE skill
weights and pending/completed counts.

Design invariants (identical to the rest of the Pilots read layer):

* **Light-module read** — it imports ``forecasting.forecast_tracker`` (a light
  ``sqlite3`` + ``pandas`` reader). NOTE: ``forecasting`` (the package) is NOT the
  AST-forbidden ``forecasting_engine`` module, so this is safe on the
  ``api/pilots_api.py`` import path. It reads persisted DB state only — no
  network, no engine, no login.
* **Honesty (CONSTRAINT #4)** — a bin with too few samples has ``mean_pct_error``
  ``null`` (the tracker already NaN-shapes it); empty history returns empty
  collections, never fabricated skill.
* **Never raises (CONSTRAINT #6)** — any DB/import failure degrades to the empty
  view.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

__all__ = ["forecast_skill_view"]

_DEFAULT_HORIZON = 30


def _finite_or_none(value: Any):
    """Coerce to a finite float, else ``None`` (NaN → ``null``, CONSTRAINT #4)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _empty_view(symbol: str, horizon_days: int) -> Dict[str, Any]:
    return {
        "symbol": str(symbol or "").upper(),
        "horizon_days": int(horizon_days),
        "reliability_curve": [],
        "skill_weights": {},
        "pending": 0,
        "completed": 0,
        "reason": "No forecast history yet — run the pipeline to accumulate it.",
    }


def forecast_skill_view(symbol: str, horizon_days: int = _DEFAULT_HORIZON) -> Dict[str, Any]:
    """Return per-symbol forecast reliability + skill weights + counts.

    Shape::

        {symbol, horizon_days, reliability_curve: [{model_name, horizon_days,
         bin_center, mean_pct_error, count}], skill_weights: {model: weight},
         pending, completed, reason}

    ``reason`` is ``None`` on a normal hit, else an honest "no history" string.
    Never raises (CONSTRAINT #6).
    """
    sym = str(symbol or "").upper().strip()
    horizon = int(horizon_days)
    if not sym:
        return _empty_view(sym, horizon)

    try:
        from forecasting.forecast_tracker import ForecastTracker

        # A GET endpoint never writes; read-only also means a fresh install
        # doesn't silently create the table as a side effect of a read.
        tracker = ForecastTracker(readonly=True)
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.debug("ForecastTracker unavailable: %s", exc)
        return _empty_view(sym, horizon)

    try:
        curve_df = tracker.get_forecast_reliability_curve(symbol=sym, horizon_days=horizon)
        reliability: List[Dict[str, Any]] = []
        if curve_df is not None and not curve_df.empty:
            for row in curve_df.to_dict(orient="records"):
                reliability.append(
                    {
                        "model_name": str(row.get("model_name") or ""),
                        "horizon_days": int(row.get("horizon_days"))
                        if row.get("horizon_days") is not None
                        else horizon,
                        "bin_center": _finite_or_none(row.get("bin_center")),
                        "mean_pct_error": _finite_or_none(row.get("mean_pct_error")),
                        "count": int(row.get("count") or 0),
                    }
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("reliability curve failed for %s: %s", sym, exc)
        reliability = []

    try:
        raw_weights = tracker.get_skill_weights(sym, horizon) or {}
        skill_weights = {
            str(k): w
            for k, v in raw_weights.items()
            if (w := _finite_or_none(v)) is not None
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("skill weights failed for %s: %s", sym, exc)
        skill_weights = {}

    try:
        pending = int(tracker.pending_count(sym, horizon))
    except Exception:  # noqa: BLE001
        pending = 0
    try:
        completed = int(tracker.completed_count(sym, horizon))
    except Exception:  # noqa: BLE001
        completed = 0

    has_data = bool(reliability or skill_weights or pending or completed)
    return {
        "symbol": sym,
        "horizon_days": horizon,
        "reliability_curve": reliability,
        "skill_weights": skill_weights,
        "pending": pending,
        "completed": completed,
        "reason": None if has_data else _empty_view(sym, horizon)["reason"],
    }
