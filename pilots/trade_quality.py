"""pilots/trade_quality.py — Trade Quality (MFE/MAE + Edge Ratio) attribution
for the Pilots PWA.

Ports the Streamlit legacy GUI's ``gui/panels/report_viewer.py::
_render_trade_quality_section`` (+ its ``gui/report_viewer_helpers.py::
build_mfe_mae_scatter_frame`` helper) as two independent, honestly-degrading
pure functions taking already-fetched inputs (never doing I/O themselves —
I/O happens in ``api/pilots_api.py``'s ``GET /portfolio/trade-quality``
handler, exactly like ``pilots/attribution.py``'s
``portfolio_correlation_clusters``):

* **MFE vs. MAE scatter** (:func:`mfe_mae_scatter`) — one point per symbol
  from the LATEST pipeline snapshot's ``mfe``/``mae``/``edge_ratio`` fields
  (already computed and persisted onto ``output/state_snapshot.json``'s
  ``signals[]`` entries by ``reporting/state_snapshot.py`` /
  ``main_orchestrator._write_state_snapshot``). This is the PORTFOLIO-WIDE
  view (all symbols at once); ``webapp/src/screens/SymbolDetail.tsx`` already
  renders the PER-symbol MFE/MAE/edge_ratio trio, so this module does not
  duplicate that.

* **Edge Ratio by Strategy** (:func:`edge_ratio_by_strategy`) — an on-demand
  batch computation over every CLOSED trade in
  ``transactions_store.TransactionsStore``: for each trade, look up its
  symbol's historical bars (pre-fetched by the caller via
  ``data.historical_store.HistoricalStore.get_bars()`` — the same
  incrementally-cached bars source the rest of the platform reads), compute
  MFE/MAE/Edge-Ratio for that trade's specific hold period via
  ``evaluation_engine.EvaluationEngine.calculate_edge_ratio``, and average by
  the ``strategy`` tag recorded on the trade at entry.

Honesty rules (CONSTRAINT #4 / #6), preserved throughout:

* A signal missing either ``mfe`` or ``mae`` is dropped from the scatter
  entirely — never plotted as a fabricated origin point (mirrors
  ``build_mfe_mae_scatter_frame``'s ``dropna(subset=["mfe", "mae"])``).
* A closed trade whose symbol has no available bars (never fetched, or the
  caller's fetch failed) is skipped, not fabricated as a zero-excursion
  trade.
* A strategy group's ``avg_edge_ratio`` is averaged only over trades whose
  Edge Ratio was itself computable; a trade contributing MFE/MAE but an
  undefined Edge Ratio still counts toward ``avg_mfe``/``avg_mae`` but not
  ``avg_edge_ratio``.
* Both functions degrade to an honest empty shape + ``reason`` when there is
  nothing to show; neither raises.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "mfe_mae_scatter",
    "edge_ratio_by_strategy",
]


def _coerce_float(value: Any) -> Optional[float]:
    """Coerce ``value`` to a finite float, or ``None`` when not possible."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf guard
        return None
    return f


# ---------------------------------------------------------------------------
# MFE vs. MAE scatter — current signals
# ---------------------------------------------------------------------------


def mfe_mae_scatter(signals: List[dict]) -> List[Dict[str, Any]]:
    """One point per symbol with both ``mfe`` and ``mae`` in the latest snapshot.

    Parameters
    ----------
    signals:
        The parsed ``output/state_snapshot.json`` dict's ``signals`` list
        (e.g. ``pilots.scoring.load_snapshot(...).get("signals", [])``).

    Returns
    -------
    A list of ``{symbol, mfe, mae, edge_ratio, conviction, action}`` dicts,
    one per symbol carrying BOTH ``mfe`` and ``mae`` — a symbol missing
    either is skipped rather than plotted with a fabricated 0.0 (CONSTRAINT
    #4). ``edge_ratio``/``conviction`` are ``None`` when unavailable;
    ``action`` falls back to the advisory action, then ``None``.
    """
    rows: List[Dict[str, Any]] = []
    if not signals:
        return rows

    for sig in signals:
        if not isinstance(sig, dict):
            continue
        mfe = _coerce_float(sig.get("mfe"))
        mae = _coerce_float(sig.get("mae"))
        if mfe is None or mae is None:
            continue
        rows.append({
            "symbol": sig.get("symbol") or "?",
            "mfe": mfe,
            "mae": mae,
            "edge_ratio": _coerce_float(sig.get("edge_ratio")),
            "conviction": _coerce_float(sig.get("advisory_conviction")),
            "action": sig.get("action") or sig.get("advisory_action") or None,
        })
    return rows


# ---------------------------------------------------------------------------
# Edge Ratio by Strategy — closed trades
# ---------------------------------------------------------------------------


def edge_ratio_by_strategy(
    closed_trades_df: Any,
    bars_by_symbol: Dict[str, Any],
) -> Dict[str, Any]:
    """Average MFE/MAE/Edge Ratio per ``strategy``, over CLOSED trades.

    Parameters
    ----------
    closed_trades_df:
        A pandas DataFrame shaped like
        ``transactions_store.TransactionsStore.closed_trades_df()`` — columns
        include ``symbol``, ``strategy``, ``entry_price``, ``entry_ts``,
        ``exit_ts`` (``exit_price``/``shares``/``notes``/``conviction`` are
        present but unused here). ``None``/empty degrades honestly.
    bars_by_symbol:
        ``{symbol: bars_df}`` pre-fetched by the caller (e.g. via
        ``HistoricalStore.get_bars()``) — a DataFrame with a DatetimeIndex and
        ``High``/``Low``/``Close`` columns, exactly the shape
        ``evaluation_engine.EvaluationEngine.calculate_edge_ratio`` needs. A
        symbol absent from this dict (or mapped to an empty/``None`` frame)
        means every trade in that symbol is skipped — never fabricated.

    Returns
    -------
    dict with keys:

    * ``by_strategy`` — list of ``{strategy, n_trades, avg_mfe, avg_mae,
      avg_edge_ratio}``, sorted by ``avg_edge_ratio`` descending (``None``
      values sort last). A trade tagged with no ``strategy`` is grouped under
      ``"(untagged)"``, matching the legacy GUI's convention.
    * ``reason`` — honest explanation when ``by_strategy`` is empty, else
      ``None``.

    Never raises (CONSTRAINT #6); never fabricates a data point for a trade
    whose symbol has no recoverable OHLC history (CONSTRAINT #4).
    """
    if closed_trades_df is None or getattr(closed_trades_df, "empty", True):
        return {"by_strategy": [], "reason": "no closed trades yet"}

    try:
        from evaluation_engine import EvaluationEngine
    except Exception as exc:  # pragma: no cover - defensive, should not happen
        logger.warning("edge_ratio_by_strategy: evaluation_engine unavailable: %s", exc)
        return {"by_strategy": [], "reason": "edge-ratio engine unavailable"}

    try:
        import pandas as pd
    except Exception as exc:  # pragma: no cover - defensive, should not happen
        logger.warning("edge_ratio_by_strategy: pandas unavailable: %s", exc)
        return {"by_strategy": [], "reason": "edge-ratio engine unavailable"}

    engine = EvaluationEngine()
    per_strategy: Dict[str, List[Dict[str, Optional[float]]]] = {}

    for _, trade in closed_trades_df.iterrows():
        sym = str(trade.get("symbol") or "").upper().strip()
        if not sym:
            continue

        bars = bars_by_symbol.get(sym)
        if bars is None or getattr(bars, "empty", True):
            continue

        entry_price = _coerce_float(trade.get("entry_price"))
        entry_ts = trade.get("entry_ts")
        exit_ts = trade.get("exit_ts")
        if entry_price is None or entry_price <= 0:
            continue
        if entry_ts is None or exit_ts is None or pd.isna(entry_ts) or pd.isna(exit_ts):
            continue

        try:
            edge = engine.calculate_edge_ratio(bars, entry_price, entry_ts, exit_ts)
        except Exception as exc:  # noqa: BLE001 - dead-letter per trade
            logger.debug("edge_ratio_by_strategy: calculate_edge_ratio(%s) failed: %s", sym, exc)
            continue

        mfe = _coerce_float(edge.get("MFE"))
        mae = _coerce_float(edge.get("MAE"))
        if mfe is None or mae is None:
            # No pricing data found for this hold period — skip, never fabricate.
            continue
        edge_ratio = _coerce_float(edge.get("Edge Ratio"))

        strategy = str(trade.get("strategy") or "").strip() or "(untagged)"
        per_strategy.setdefault(strategy, []).append({
            "mfe": mfe, "mae": mae, "edge_ratio": edge_ratio,
        })

    if not per_strategy:
        return {
            "by_strategy": [],
            "reason": "no closed trades with recoverable OHLC history yet",
        }

    by_strategy: List[Dict[str, Any]] = []
    for strategy, rows in per_strategy.items():
        n_trades = len(rows)
        avg_mfe = sum(r["mfe"] for r in rows) / n_trades
        avg_mae = sum(r["mae"] for r in rows) / n_trades
        edge_vals = [r["edge_ratio"] for r in rows if r["edge_ratio"] is not None]
        avg_edge_ratio = (sum(edge_vals) / len(edge_vals)) if edge_vals else None
        by_strategy.append({
            "strategy": strategy,
            "n_trades": n_trades,
            "avg_mfe": avg_mfe,
            "avg_mae": avg_mae,
            "avg_edge_ratio": avg_edge_ratio,
        })

    by_strategy.sort(key=lambda r: (r["avg_edge_ratio"] is None, -(r["avg_edge_ratio"] or 0.0), r["strategy"]))

    return {"by_strategy": by_strategy, "reason": None}
