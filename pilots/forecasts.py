"""pilots/forecasts.py — the Forecasts screen's read layer (forecasting rebuild F6a).
================================================================================

Two read-only views behind ``GET /forecasts`` and
``GET /symbols/{ticker}/forecast-summary`` (``api/pilots_api.py``):

* :func:`forecasts_view` — one row per symbol from the pipeline's PERSISTED
  forecast (``output/state_snapshot.json`` ``signals[]``, written by
  ``main_orchestrator._write_state_snapshot``): the published point forecast
  and Monte-Carlo band per horizon, the fallback and "gate published naive"
  disclosures, and what the forecast changed this cycle (``forecast_alignment``
  points, an approximate action tier without them, the advisory rules it fired,
  and whether the sell-zone top came from the forecast). It never refits a
  model, unlike ``GET /metrics/forecast/{symbol}``.
* :func:`forecast_summary` — one symbol's ledger view (``forecast_errors``
  via ``forecasting.forecast_tracker.ForecastTracker(readonly=True)``): skill
  vs naive per horizon and model, the last recorded F3 gate decision plus the
  gate's evaluation on today's matured evidence (admitted / rejected with the
  reason), and Monte-Carlo band coverage.

Forecast z (F5) is not built yet: every ``z`` / ``err`` / ``err_n`` is
``None`` with ``z_reason`` saying so, and the top-level ``z`` block is
``None``. They become real values only once F5-1 lands.

Design invariants (same as the rest of the Pilots read layer):

* **Dependency-light** — stdlib + ``settings`` + the lazy
  ``forecasting.forecast_tracker`` (a ``sqlite3`` + ``pandas`` reader, not the
  AST-forbidden ``forecasting_engine``). No ``strategy_engine``, ``signals``,
  ``engine`` or ``desktop`` import (``tests/test_pilots_strategy_matrix.py``).
* **Honesty (CONSTRAINT #4)** — a missing value is ``None`` with a ``reason``,
  never ``0.0``. The action tier "without the forecast" is labelled
  approximate: the snapshot carries the rounded score but neither the
  choppy-market downgrade input nor a per-symbol kill-switch overlay.
* **Never raises (CONSTRAINT #6)** — a missing/corrupt snapshot or ledger
  degrades to an empty shape with ``reason``.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from settings import settings

logger = logging.getLogger(__name__)

__all__ = [
    "HORIZONS",
    "ACTION_TIER_CUTS",
    "action_tier",
    "sell_hi_from_forecast",
    "forecasts_view",
    "forecast_summary",
]

HORIZONS = (10, 30, 60, 90)

# StrategyEngine.evaluate_security's PHASE 5 score cuts (STRONG BUY >= 75,
# BUY >= 55, HOLD >= 35, else RISK REDUCE). A fixed algorithm constant, not a
# tunable setting, so it is a literal here: importing strategy_engine would
# put the heavy engine on the API import path. tests/test_pilots_forecasts.py
# pins these three numbers against strategy_engine.py's source.
ACTION_TIER_CUTS = (75.0, 55.0, 35.0)

_SOURCE = "state_snapshot.json"
_FORECAST_ALIGNMENT = "forecast_alignment"
_Z_NOT_BUILT = "forecast z is not measured yet (forecasting rebuild F5 is not built)"
_NO_SNAPSHOT = "No state snapshot yet — run the pipeline first."
_NO_SIGNALS = "The latest snapshot has no symbols."
_TIER_NOTE = (
    "approximate: score minus the forecast_alignment contribution, cut at the "
    "StrategyEngine tiers; the choppy-market downgrade and the macro kill-switch "
    "overlay are not in the snapshot"
)
# Sell-zone string from strategy_engine.apply_sell_side_range (active long):
# "Sell Zone: $LO - $HI | Stop @ $STOP". Values are formatted with 2 decimals.
_SELL_ZONE_RE = re.compile(r"Sell Zone:\s*\$([0-9][0-9,]*\.?[0-9]*)\s*-\s*\$([0-9][0-9,]*\.?[0-9]*)")
# LO and HI are each rounded to cents, so the ATR leg 2*LO - P is known to
# within 2 * 0.005 and HI to within 0.005; 0.02 covers both with margin.
_SELL_HI_TOLERANCE = 0.02


def _finite_or_none(value: Any) -> Optional[float]:
    """Coerce to a finite float, else ``None`` (bools are not numbers here)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _positive_or_none(value: Any) -> Optional[float]:
    f = _finite_or_none(value)
    return f if f is not None and f > 0 else None


def _flag_or_none(value: Any) -> Optional[bool]:
    """Snapshot disclosure flags are 1.0/0.0/null (or a bool). ``None`` stays
    ``None`` — "not recorded" is not "False"."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    f = _finite_or_none(value)
    if f is None:
        return None
    return f != 0.0


def action_tier(score: Optional[float]) -> Optional[str]:
    """The StrategyEngine tier a score falls in, or ``None`` for no score."""
    s = _finite_or_none(score)
    if s is None:
        return None
    strong, buy, hold = ACTION_TIER_CUTS
    if s >= strong:
        return "STRONG BUY"
    if s >= buy:
        return "BUY"
    if s >= hold:
        return "HOLD"
    return "RISK REDUCE"


def sell_hi_from_forecast(sell_range: Any, price: Any) -> Optional[bool]:
    """Whether the sell-zone top came from the 30-day forecast leg.

    ``apply_sell_side_range`` sets ``LO = P + 1.5*ATR`` and
    ``HI = max(P + 3*ATR, Forecast_30)``, so the ATR leg is ``2*LO - P``.
    ``True`` when HI exceeds it beyond rounding, ``False`` when HI equals it,
    ``None`` when there is no active sell zone (e.g. "Sell Now"), no price, or
    the numbers are inconsistent with that formula (never guessed).
    """
    p = _positive_or_none(price)
    if p is None or not isinstance(sell_range, str):
        return None
    m = _SELL_ZONE_RE.search(sell_range)
    if not m:
        return None
    try:
        lo = float(m.group(1).replace(",", ""))
        hi = float(m.group(2).replace(",", ""))
    except ValueError:
        return None
    atr_hi = 2.0 * lo - p
    if hi > atr_hi + _SELL_HI_TOLERANCE:
        return True
    if abs(hi - atr_hi) <= _SELL_HI_TOLERANCE:
        return False
    return None


def _gate_settings() -> Dict[str, Any]:
    return {
        "enabled": bool(getattr(settings, "FORECAST_NAIVE_GATE_ENABLED", False)),
        "min_improvement": _finite_or_none(getattr(settings, "FORECAST_NAIVE_GATE_MIN_IMPROVEMENT", None)),
        "min_obs": int(getattr(settings, "FORECAST_NAIVE_GATE_MIN_OBS", 0) or 0) or None,
        "window_days": int(getattr(settings, "FORECAST_SKILL_WINDOW_DAYS", 0) or 0) or None,
    }


def _horizon_cell(sig: Mapping[str, Any], h: int) -> Dict[str, Any]:
    is_fallback = _flag_or_none(sig.get(f"forecast_{h}_is_fallback"))
    if is_fallback is None and h == 30:
        # Snapshots written before F6a carry only the 30-day flag.
        is_fallback = _flag_or_none(sig.get("forecast_is_fallback"))
    return {
        "forecast": _positive_or_none(sig.get(f"forecast_{h}")),
        "lower": _positive_or_none(sig.get(f"forecast_{h}_lower")),
        "upper": _positive_or_none(sig.get(f"forecast_{h}_upper")),
        "is_fallback": is_fallback,
        "gated_naive": _flag_or_none(sig.get(f"forecast_{h}_gated_naive")),
        "z": None,
        "z_reason": _Z_NOT_BUILT,
        "err": None,
        "err_n": None,
    }


def _impact(sig: Mapping[str, Any]) -> Dict[str, Any]:
    components = sig.get("score_components")
    points = None
    if isinstance(components, Mapping):
        points = _finite_or_none(components.get(_FORECAST_ALIGNMENT))
    score = _finite_or_none(sig.get("score"))
    tier = action_tier(score)
    tier_without = action_tier(score - points) if (score is not None and points is not None) else None
    effects_raw = sig.get("advisory_forecast_effects")
    effects = [str(e) for e in effects_raw] if isinstance(effects_raw, list) else None
    action = sig.get("action")
    advisory_action = sig.get("advisory_action")
    return {
        "alignment_points": points,
        "score": score,
        "action": str(action) if action else None,
        "action_tier": tier,
        "action_tier_without_forecast": tier_without,
        "tier_changed_by_forecast": (tier != tier_without) if tier_without is not None else None,
        "tier_note": _TIER_NOTE,
        "advisory_action": str(advisory_action) if advisory_action else None,
        "advisory_effects": effects,
        "sell_range": str(sig.get("sell_range")) if sig.get("sell_range") else None,
        "sell_hi_from_forecast": sell_hi_from_forecast(sig.get("sell_range"), sig.get("price")),
    }


def forecasts_view(snapshot: Any) -> Dict[str, Any]:
    """Universe table for the Forecasts screen from the persisted snapshot.

    Shape::

        {as_of, source, gate: {enabled, min_improvement, min_obs, window_days},
         z: null, horizons: [10, 30, 60, 90],
         rows: [{symbol, price, held, horizons: {"10"|"30"|"60"|"90":
                 {forecast, lower, upper, is_fallback, gated_naive, z, z_reason,
                  err, err_n}},
                 impact: {alignment_points, score, action, action_tier,
                          action_tier_without_forecast, tier_changed_by_forecast,
                          tier_note, advisory_action, advisory_effects,
                          sell_range, sell_hi_from_forecast}}],
         reason}

    ``reason`` is ``None`` on a normal read. Never raises.
    """
    out: Dict[str, Any] = {
        "as_of": None,
        "source": _SOURCE,
        "gate": _gate_settings(),
        "z": None,
        "horizons": list(HORIZONS),
        "rows": [],
        "reason": None,
    }
    try:
        if not isinstance(snapshot, Mapping):
            out["reason"] = _NO_SNAPSHOT
            return out
        ts = snapshot.get("timestamp")
        out["as_of"] = str(ts) if ts else None
        holdings = snapshot.get("holdings")
        held = {str(s).upper() for s in holdings} if isinstance(holdings, list) else set()
        rows: List[Dict[str, Any]] = []
        signals = snapshot.get("signals")
        for sig in signals if isinstance(signals, list) else []:
            if not isinstance(sig, Mapping):
                continue
            sym = str(sig.get("symbol") or "").upper().strip()
            if not sym:
                continue
            try:
                rows.append({
                    "symbol": sym,
                    "price": _positive_or_none(sig.get("price")),
                    "held": sym in held,
                    "horizons": {str(h): _horizon_cell(sig, h) for h in HORIZONS},
                    "impact": _impact(sig),
                })
            except Exception as exc:  # noqa: BLE001 — dead-letter one row
                logger.debug("forecasts_view: skipped %s: %s", sym, exc)
        rows.sort(key=lambda r: r["symbol"])
        out["rows"] = rows
        if not rows:
            out["reason"] = _NO_SIGNALS
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("forecasts_view failed: %s", exc)
        out["rows"] = []
        out["reason"] = "error reading the snapshot"
        return out


_SKILL_KEYS = (
    "n", "median_abs_log_error", "naive_median_abs_log_error", "pct_beating_naive",
    "wins", "losses", "ties", "sign_test_p", "direction_hit_rate", "direction_n",
)


def _skill_rows(models: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not isinstance(models, Mapping):
        return rows
    for name in sorted(models):
        stats = models[name]
        if not isinstance(stats, Mapping):
            continue
        row: Dict[str, Any] = {"model": str(name)}
        for key in _SKILL_KEYS:
            val = stats.get(key)
            if key in ("n", "wins", "losses", "ties", "direction_n"):
                row[key] = int(val) if isinstance(val, (int, float)) and math.isfinite(float(val)) else None
            else:
                row[key] = _finite_or_none(val)
        rows.append(row)
    return rows


def _empty_summary(sym: str, reason: str) -> Dict[str, Any]:
    gate = _gate_settings()
    return {
        "symbol": sym,
        "as_of": None,
        "window_days": gate["window_days"],
        "gate": gate,
        "horizons": {},
        "reason": reason,
    }


def forecast_summary(
    symbol: str,
    tracker: Any = None,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
    """One symbol's forecast ledger view, per horizon.

    Shape::

        {symbol, as_of, window_days, gate: {enabled, min_improvement, min_obs,
         window_days},
         horizons: {"10"|"30"|"60"|"90": {
            published_model,                        # "gated_blend" or "blend"
            skill: [{model, n, median_abs_log_error, naive_median_abs_log_error,
                     pct_beating_naive, wins, losses, ties, sign_test_p,
                     direction_hit_rate, direction_n}], skill_reason,
            gate: {last_decision: null | {forecast_day, forecast_ts,
                                          fallback_to_naive, admitted},
                   evaluation: null | {admitted, rejected: {model: reason},
                                       fallback_to_naive},
                   evaluation_note, reason},
            mc_coverage: {n, empirical, nominal, reason}}},
         reason}

    ``skill`` uses ``ForecastTracker.skill_vs_naive`` over
    ``FORECAST_SKILL_WINDOW_DAYS``; the gate ``evaluation`` is
    ``compute_naive_gate`` on ``naive_gate_stats`` at ``as_of`` (the gate's
    own no-lookahead cutoff), taking every model with stats as a candidate —
    the reader cannot know which models produce a forecast next cycle, so a
    "did not produce" rejection never appears there; ``last_decision`` is what
    the gate actually recorded. ``tracker`` is injectable for tests; default is
    a read-only tracker on the shared DB. Never raises.
    """
    sym = str(symbol or "").upper().strip()
    if not sym:
        return _empty_summary(sym, "no symbol")
    try:
        from forecasting.forecast_tracker import (
            MODEL_BLEND,
            MODEL_GATED_BLEND,
            MODEL_MONTE_CARLO,
            ForecastTracker,
            compute_naive_gate,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("forecast_tracker unavailable: %s", exc)
        return _empty_summary(sym, "forecast ledger reader unavailable")
    if tracker is None:
        try:
            tracker = ForecastTracker(readonly=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("ForecastTracker(readonly) failed: %s", exc)
            return _empty_summary(sym, "forecast ledger unavailable")

    gate = _gate_settings()
    window = int(gate["window_days"] or 180)
    now = as_of if as_of is not None else datetime.now(timezone.utc)
    published = MODEL_GATED_BLEND if gate["enabled"] else MODEL_BLEND
    out: Dict[str, Any] = {
        "symbol": sym,
        "as_of": now.isoformat(),
        "window_days": window,
        "gate": gate,
        "horizons": {},
        "reason": None,
    }

    try:
        gate_stats = tracker.naive_gate_stats(sym, HORIZONS, window_days=window, as_of=now)
    except Exception as exc:  # noqa: BLE001
        logger.debug("naive_gate_stats failed for %s: %s", sym, exc)
        gate_stats = {"by_horizon": {}, "reason": "error"}
    try:
        last = tracker.latest_gate_decisions(sym, HORIZONS)
    except Exception as exc:  # noqa: BLE001
        logger.debug("latest_gate_decisions failed for %s: %s", sym, exc)
        last = {"by_horizon": {}, "reason": "error"}

    any_data = False
    for h in HORIZONS:
        cell: Dict[str, Any] = {"published_model": published}

        try:
            skill = tracker.skill_vs_naive(h, window, symbol=sym)
        except Exception as exc:  # noqa: BLE001
            logger.debug("skill_vs_naive failed for %s h=%d: %s", sym, h, exc)
            skill = {"models": {}, "reason": "error"}
        cell["skill"] = _skill_rows(skill.get("models") if isinstance(skill, Mapping) else None)
        cell["skill_reason"] = (skill.get("reason") if isinstance(skill, Mapping) else "error") or None
        any_data = any_data or bool(cell["skill"])

        gate_reason = gate_stats.get("reason") if isinstance(gate_stats, Mapping) else "error"
        evaluation = None
        if gate_reason != "error":
            by_h = gate_stats.get("by_horizon") if isinstance(gate_stats, Mapping) else None
            stats_h = by_h.get(h, {}) if isinstance(by_h, Mapping) else {}
            stats_h = stats_h if isinstance(stats_h, Mapping) else {}
            try:
                decision = compute_naive_gate(
                    stats_h, list(stats_h.keys()),
                    min_improvement=float(gate["min_improvement"] or 0.0),
                    min_obs=int(gate["min_obs"] or 0),
                )
                evaluation = {
                    "admitted": list(decision.get("admitted") or []),
                    "rejected": {str(k): str(v) for k, v in (decision.get("rejected") or {}).items()},
                    "fallback_to_naive": bool(decision.get("fallback_to_naive")),
                }
            except Exception as exc:  # noqa: BLE001
                logger.debug("compute_naive_gate failed for %s h=%d: %s", sym, h, exc)
                gate_reason = "error"
        last_by_h = last.get("by_horizon") if isinstance(last, Mapping) else None
        last_decision = last_by_h.get(h) if isinstance(last_by_h, Mapping) else None
        any_data = any_data or last_decision is not None
        cell["gate"] = {
            "last_decision": last_decision,
            "evaluation": evaluation,
            "evaluation_note": (
                "what the gate would decide now on matured outcomes, with every "
                "model that has stats as a candidate"
            ),
            "reason": gate_reason or (last.get("reason") if isinstance(last, Mapping) else None),
        }

        try:
            cov = tracker.coverage_report(sym, h, window_days=window, model_name=MODEL_MONTE_CARLO)
        except Exception as exc:  # noqa: BLE001
            logger.debug("coverage_report failed for %s h=%d: %s", sym, h, exc)
            cov = {"n": 0, "empirical_coverage": None, "nominal_coverage": None, "reason": "error"}
        cov = cov if isinstance(cov, Mapping) else {}
        n_cov = cov.get("n")
        cell["mc_coverage"] = {
            "n": int(n_cov) if isinstance(n_cov, int) else 0,
            "empirical": _finite_or_none(cov.get("empirical_coverage")),
            "nominal": _finite_or_none(cov.get("nominal_coverage")),
            "reason": cov.get("reason"),
        }
        any_data = any_data or bool(cell["mc_coverage"]["n"])
        out["horizons"][str(h)] = cell

    if not any_data:
        out["reason"] = "No matured forecast history for this symbol yet."
    return out
