"""pilots/simulation.py -- "What-If" pilot allocation simulation (READ-ONLY).

Answers: "if I allocated $X to Pilot P today, on top of my real current
portfolio, what would the resulting Sharpe / Max Drawdown / heat look like?"

This is a **real, clearly-labeled dilution blend over REAL historical price
data** -- not portfolio optimization, not a fitted model, and not a fabricated
adjustment. Every number in the response is either:

* reused verbatim from the SAME real computation the Observability screen
  already shows (:func:`pilots.observability.portfolio_risk_metrics` /
  :func:`pilots.observability.portfolio_heat_metric` for the ``current``
  values), or
* derived from a synthetic equity curve built out of real daily closes
  (:meth:`data.historical_store.HistoricalStore.get_bars`) blended by real
  portfolio/target weights, run through the SAME
  ``evaluation_engine.calculate_equity_curve_metrics`` every other equity
  curve in this codebase uses (for the ``projected`` values).

No hardcoded deltas, no plausible-looking constants. See
:func:`simulate_pilot_allocation`'s docstring for the exact weight-blend
formula and the honesty note on why ``heat_pct_projected`` is always ``None``.

Design invariants (matches the rest of the Pilots read layer):

* **Never raises (CONSTRAINT #6)** -- any failure (unknown pilot, no
  snapshot, no price history, a downstream exception) degrades to the honest
  empty response shape with a ``reason`` string, never an exception that
  would 500 the endpoint.
* **Never fabricates (CONSTRAINT #4)** -- every metric that cannot be
  honestly computed is ``None``, never a guessed number. In particular
  ``heat_pct_projected`` is ALWAYS ``None`` -- there is no honest way to
  project per-position ``unrealized_pl`` for a hypothetical, never-entered
  position (see the function docstring's "Honesty rule").
* Imports ``pandas``, ``data.historical_store.HistoricalStore``, and
  ``evaluation_engine`` -- none of these are on ``api/pilots_api.py``'s
  AST-guard denylist (only ``processing_engine``, ``strategy_engine``,
  ``forecasting_engine``, ``macro_engine``, ``technical_options_engine``,
  ``main_orchestrator``, ``desktop`` are forbidden). Imports of those heavier
  dependencies are lazy (inside the function body), matching
  ``pilots/observability.py``'s convention, so a missing/broken dependency
  degrades gracefully instead of breaking import of this module (and this
  whole API) at process start.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional

from pilots import catalog, scoring
from pilots.observability import portfolio_heat_metric, portfolio_risk_metrics

logger = logging.getLogger(__name__)

__all__ = ["simulate_pilot_allocation"]


def _finite_or_none(value: Any) -> Optional[float]:
    """Coerce to a finite float, else ``None`` (NaN -> ``null``, CONSTRAINT #4)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _empty_response(pilot_id: str, reason: str) -> Dict[str, Any]:
    return {
        "pilot_id": pilot_id,
        "current": {"sharpe_ratio": None, "max_drawdown": None},
        "projected": {"sharpe_ratio": None, "max_drawdown": None},
        "heat_pct_current": None,
        "heat_pct_projected": None,
        "coverage": {"symbols_covered": 0, "symbols_total": 0},
        "reason": reason,
    }


def simulate_pilot_allocation(pilot_id: str, allocation_amount: float) -> Dict[str, Any]:
    """Simulate allocating ``allocation_amount`` (USD) to Pilot ``pilot_id`` on
    top of the operator's REAL current portfolio, and compare real "current"
    risk metrics against a "projected" set computed from a synthetic blended
    equity curve.

    Weight-blend formula (step 6 -- the ONLY math this function invents; every
    other number is either reused verbatim from an existing real computation
    or derived by running that same blended series through the real
    ``evaluation_engine.calculate_equity_curve_metrics``)::

        new_total_equity = current_total_equity + allocation_amount

        for symbol in (current_symbols | pilot_target_symbols):
            w_projected[symbol] = (
                w_current.get(symbol, 0.0) * current_total_equity / new_total_equity
            ) + (
                allocation_amount / new_total_equity
            ) * pilot_target_weight.get(symbol, 0.0)

    In plain English: every existing position's weight is proportionally
    diluted by the new (larger) total equity, and the new capital is
    allocated across the pilot's target holdings according to the pilot's own
    weight vector. This is a straightforward capital-weighted blend of two
    known weight vectors -- NOT portfolio optimization, NOT a risk model, and
    NOT a forecast of what the pilot would actually buy.

    Honesty rule (the single most important rule in this function):
    ``heat_pct_projected`` is ALWAYS ``None``. ``portfolio_heat_metric()``'s
    real formula needs each open position's real ``unrealized_pl``, which
    cannot exist for a hypothetical, never-entered position -- there is no
    honest way to project it, so this function does not invent one.

    Returns the shape documented in the module tests. Never raises
    (CONSTRAINT #6) -- every failure mode degrades to the honest empty shape
    with a ``reason``.
    """
    try:
        pilot = catalog.get_pilot(pilot_id)
    except Exception as exc:  # noqa: BLE001 - dead-letter: catalog lookup failure
        logger.warning("simulate_pilot_allocation: catalog.get_pilot failed: %s", exc)
        return _empty_response(pilot_id, "unknown pilot")

    if pilot is None:
        return _empty_response(pilot_id, "unknown pilot")

    try:
        from data.historical_store import HistoricalStore
    except Exception as exc:  # noqa: BLE001 - dead-letter: import failure
        logger.debug("simulate_pilot_allocation: HistoricalStore import failed: %s", exc)
        return _empty_response(pilot_id, "no portfolio snapshot available")

    try:
        store = HistoricalStore(readonly=True)
        snap = store.latest_account_snapshot()
    except Exception as exc:  # noqa: BLE001 - dead-letter: cold/unreadable DB
        logger.warning("simulate_pilot_allocation: latest_account_snapshot failed: %s", exc)
        return _empty_response(pilot_id, "no portfolio snapshot available")

    if snap is None:
        return _empty_response(pilot_id, "no portfolio snapshot available")

    total_equity = _finite_or_none(getattr(snap, "total_equity", None))
    if total_equity is None or total_equity <= 0:
        return _empty_response(pilot_id, "no portfolio snapshot available")

    # ---- current values -- reused verbatim from the real Observability
    # computations so this never silently diverges from what that screen
    # already shows (step 3 of the task spec). ----
    try:
        risk_metrics = portfolio_risk_metrics()
    except Exception as exc:  # noqa: BLE001 - dead-letter
        logger.warning("simulate_pilot_allocation: portfolio_risk_metrics failed: %s", exc)
        risk_metrics = {"sharpe_ratio": None, "max_drawdown": None}
    try:
        heat_metrics = portfolio_heat_metric()
    except Exception as exc:  # noqa: BLE001 - dead-letter
        logger.warning("simulate_pilot_allocation: portfolio_heat_metric failed: %s", exc)
        heat_metrics = {"heat_pct": None}

    current = {
        "sharpe_ratio": _finite_or_none(risk_metrics.get("sharpe_ratio")),
        "max_drawdown": _finite_or_none(risk_metrics.get("max_drawdown")),
    }
    heat_pct_current = _finite_or_none(heat_metrics.get("heat_pct"))

    # ---- build current weights from real positions ----
    positions = getattr(snap, "positions", None) or {}
    w_current: Dict[str, float] = {}
    for sym, pos in positions.items():
        mv = _finite_or_none(getattr(pos, "market_value", None))
        if mv is None:
            continue
        symbol = str(sym).upper().strip()
        if not symbol:
            continue
        w_current[symbol] = w_current.get(symbol, 0.0) + (mv / total_equity)

    # ---- pilot target holdings/weights ----
    try:
        snapshot = scoring.load_snapshot()
        holdings = scoring.pilot_holdings(pilot, snapshot) if snapshot is not None else []
    except Exception as exc:  # noqa: BLE001 - dead-letter
        logger.warning("simulate_pilot_allocation: pilot_holdings failed: %s", exc)
        holdings = []

    pilot_weight: Dict[str, float] = {}
    for h in holdings:
        symbol = str(h.get("symbol") or "").upper().strip()
        w = _finite_or_none(h.get("weight"))
        if not symbol or w is None:
            continue
        pilot_weight[symbol] = pilot_weight.get(symbol, 0.0) + w

    new_total_equity = total_equity + allocation_amount
    if new_total_equity <= 0:
        return _empty_response(pilot_id, "invalid resulting total equity")

    union_symbols = set(w_current.keys()) | set(pilot_weight.keys())
    w_projected: Dict[str, float] = {}
    for symbol in union_symbols:
        diluted = w_current.get(symbol, 0.0) * total_equity / new_total_equity
        new_capital_share = (allocation_amount / new_total_equity) * pilot_weight.get(symbol, 0.0)
        w_projected[symbol] = diluted + new_capital_share

    symbols_total = len(union_symbols)
    if symbols_total == 0:
        return _empty_response(pilot_id, "no aligned price history available")

    # ---- fetch aligned daily closes for every symbol in the union ----
    try:
        import pandas as pd
    except Exception as exc:  # noqa: BLE001 - dead-letter
        logger.debug("simulate_pilot_allocation: pandas import failed: %s", exc)
        return _empty_response(pilot_id, "no aligned price history available")

    closes: Dict[str, "pd.Series"] = {}
    for symbol in union_symbols:
        try:
            bars = store.get_bars(symbol, lookback_days=252)
        except Exception as exc:  # noqa: BLE001 - dead-letter: per-symbol fetch failure
            logger.debug("simulate_pilot_allocation: get_bars(%s) failed: %s", symbol, exc)
            continue
        if bars is None or bars.empty or "Close" not in bars.columns:
            continue
        if len(bars) < 2:
            continue
        closes[symbol] = bars["Close"]

    symbols_covered = len(closes)
    coverage = {"symbols_covered": symbols_covered, "symbols_total": symbols_total}

    if symbols_covered == 0:
        resp = _empty_response(pilot_id, "no aligned price history available")
        resp["current"] = current
        resp["heat_pct_current"] = heat_pct_current
        resp["coverage"] = coverage
        return resp

    # Drop uncovered symbols and renormalize the remaining weights. Only the
    # projected side needs the renormalized vector -- `current` deliberately
    # stays the direct reuse of portfolio_risk_metrics() (see below), so
    # `w_current` is not renormalized here.
    def _renormalize(weights: Dict[str, float]) -> Dict[str, float]:
        kept = {s: w for s, w in weights.items() if s in closes}
        total_w = sum(kept.values())
        if total_w <= 0:
            return {}
        return {s: w / total_w for s, w in kept.items()}

    w_projected_kept = _renormalize(w_projected)

    try:
        returns_df = pd.concat(closes, axis=1, join="inner").pct_change().dropna(how="any")
    except Exception as exc:  # noqa: BLE001 - dead-letter
        logger.warning("simulate_pilot_allocation: returns_df build failed: %s", exc)
        resp = _empty_response(pilot_id, "no aligned price history available")
        resp["current"] = current
        resp["heat_pct_current"] = heat_pct_current
        resp["coverage"] = coverage
        return resp

    if returns_df.empty:
        resp = _empty_response(pilot_id, "no aligned price history available")
        resp["current"] = current
        resp["heat_pct_current"] = heat_pct_current
        resp["coverage"] = coverage
        return resp

    try:
        from evaluation_engine import calculate_equity_curve_metrics
    except Exception as exc:  # noqa: BLE001 - dead-letter
        logger.debug("simulate_pilot_allocation: evaluation_engine import failed: %s", exc)
        resp = _empty_response(pilot_id, "no aligned price history available")
        resp["current"] = current
        resp["heat_pct_current"] = heat_pct_current
        resp["coverage"] = coverage
        return resp

    def _metrics_for(weights: Dict[str, float]) -> Dict[str, Optional[float]]:
        if not weights:
            return {"sharpe_ratio": None, "max_drawdown": None}
        cols = [s for s in weights if s in returns_df.columns]
        if not cols:
            return {"sharpe_ratio": None, "max_drawdown": None}
        w_series = pd.Series({s: weights[s] for s in cols})
        weighted_returns = (returns_df[cols] * w_series).sum(axis=1)
        equity_curve = 100.0 * (1.0 + weighted_returns).cumprod()
        equity_df = pd.DataFrame({
            "fetched_at": returns_df.index,
            "total_equity": equity_curve.values,
        })
        try:
            m = calculate_equity_curve_metrics(equity_df)
        except Exception as exc:  # noqa: BLE001 - dead-letter
            logger.warning("simulate_pilot_allocation: calculate_equity_curve_metrics failed: %s", exc)
            return {"sharpe_ratio": None, "max_drawdown": None}
        return {
            "sharpe_ratio": _finite_or_none(m.get("sharpe_ratio")),
            "max_drawdown": _finite_or_none(m.get("max_drawdown")),
        }

    projected = _metrics_for(w_projected_kept)
    # `current` deliberately stays the direct reuse of portfolio_risk_metrics()
    # captured above (single source of truth -- byte-identical to what the
    # Observability screen shows), never recomputed from the synthetic curve.

    return {
        "pilot_id": pilot_id,
        "current": current,
        "projected": projected,
        "heat_pct_current": heat_pct_current,
        "heat_pct_projected": None,
        "coverage": coverage,
        "reason": None,
    }
