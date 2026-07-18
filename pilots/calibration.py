"""pilots/calibration.py — Recommendation Tracking & Calibration reader (READ-ONLY).
==================================================================================

Ports the four evaluation-analytics sections of the retired Streamlit Report
Viewer (``gui/panels/report_viewer.py``) — the "did our actual calls work?"
surface — into pure, dead-letter-safe readers for the Pilots PWA's
``GET /calibration/summary`` + ``GET /calibration/edge-by-strategy`` endpoints:

1. **Conviction calibration** — reliability diagram binning closed trades by
   conviction vs. realized win rate, via ``evaluation_engine.calibration_curve``.
   Bins under ``min_trades_per_bin`` report ``win_rate=None`` (insufficient
   sample — never a fabricated rate, CONSTRAINT #4). Plus a small summary
   (``total``, ``overall_win_rate``, ``calibration_error``) computed inline over
   the populated (scored) bins.
2. **Recommendation tracking** — model (paper-equivalent) vs. operator (actual
   closed-trade) return for every logged BUY signal, via
   ``evaluation_engine.recommendation_tracking_report``. Honest empty states
   (no BUY signals logged / horizon not elapsed / no acted+closed trades).
3. **MFE / MAE** — one point per current signal from the persisted
   ``state_snapshot.json`` ``signals[]`` (``mfe``/``mae``/``edge_ratio``);
   symbols without both ``mfe`` and ``mae`` are skipped (never fabricated).
4. **Edge ratio by strategy** — the heavier recompute: per closed trade, fetch
   OHLC bars (``HistoricalStore.get_bars``) + ``EvaluationEngine
   .calculate_edge_ratio``, grouped by the ``strategy`` tag. Behind its own
   endpoint so the main summary never blocks on it.

Plus ``recent_decisions_view`` — a read-only tail of the operator decision log
(``gui/decision_log.py``, streamlit-free).

Design invariants (identical to ``pilots/observability.py``, this reader's
precedent):

* **Never raises (CONSTRAINT #6)** — every sub-section degrades independently to
  an honest empty/null shape + a ``reason`` string; one section's DB/file
  failure never breaks the others.
* **Never fabricates (CONSTRAINT #4)** — NaN/undefined statistics are ``None``
  (JSON ``null``), never a guessed number. Genuine zeros (e.g. a strategy that
  really won 0% of trades) stay real zeros.
* Imports ``evaluation_engine``, ``transactions_store``,
  ``data.historical_store``, and ``gui.decision_log`` — NONE of which are on
  ``api/pilots_api.py``'s AST-guard denylist (only ``processing_engine``,
  ``strategy_engine``, ``forecasting_engine``, ``macro_engine``,
  ``technical_options_engine``, ``main_orchestrator``, ``desktop`` are
  forbidden). Imports are LAZY (inside function bodies), matching
  ``pilots/observability.py``/``pilots/realized.py``'s convention, so a
  missing/broken dependency degrades gracefully instead of breaking import of
  this module (and this whole API) at process start.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from settings import settings

logger = logging.getLogger(__name__)

__all__ = [
    "calibration_view",
    "recommendation_tracking_view",
    "mfe_mae_view",
    "edge_by_strategy_view",
    "recent_decisions_view",
    "calibration_summary",
]


def _finite_or_none(value: Any) -> Optional[float]:
    """Coerce to a finite float, else ``None`` (NaN -> ``null``, CONSTRAINT #4)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _decision_log_path() -> Path:
    """Resolve ``output/decision_log.jsonl`` from live settings per call.

    Resolves from ``settings.OUTPUT_DIR`` (not ``gui.decision_log.DEFAULT_LOG_PATH``'s
    hardcoded relative ``Path("output/...")``) so it matches the write side
    (``POST /decisions``) and stays isolatable under a tests-patched OUTPUT_DIR —
    mirrors ``pilots/observability.py::risk_gate_block_log``'s settings-resolution."""
    return settings.OUTPUT_DIR / "decision_log.jsonl"


# ---------------------------------------------------------------------------
# 1. Conviction calibration (reliability diagram)
# ---------------------------------------------------------------------------

_NO_CALIBRATION_REASON = (
    "No conviction-annotated closed trades yet — conviction scores are stored "
    "when trades close via TransactionsStore.record_trade(conviction=...)."
)


def _empty_calibration(n_bins: int, min_trades_per_bin: int, reason: str) -> Dict[str, Any]:
    return {
        "bins": [],
        "total": 0,
        "overall_win_rate": None,
        "calibration_error": None,
        "n_scored_bins": 0,
        "n_bins": n_bins,
        "min_trades_per_bin": min_trades_per_bin,
        "reason": reason,
    }


def calibration_view(n_bins: int = 10, min_trades_per_bin: int = 5) -> Dict[str, Any]:
    """Reliability diagram: conviction bins vs. realized win rate.

    Serializes ``evaluation_engine.calibration_curve`` — each bin carries
    ``bin_low``/``bin_high``/``bin_center``/``conviction_mean``/``win_rate``
    (``null`` under ``min_trades_per_bin``)/``count``/``perfect_calibration`` —
    plus an inline summary (``total`` trades w/ conviction, count-weighted
    ``overall_win_rate`` over scored bins, and ``calibration_error`` =
    mean ``|win_rate − bin_center|`` over scored bins). Honest empty shape +
    ``reason`` on cold start. Never raises (CONSTRAINT #6)."""
    try:
        from evaluation_engine import calibration_curve
        from transactions_store import TransactionsStore
    except Exception as exc:  # noqa: BLE001 — dead-letter: import failure
        logger.debug("calibration_view import failed: %s", exc)
        return _empty_calibration(n_bins, min_trades_per_bin, _NO_CALIBRATION_REASON)

    try:
        store = TransactionsStore(readonly=True)
        cal_df = calibration_curve(store, n_bins=n_bins, min_trades_per_bin=min_trades_per_bin)
    except Exception as exc:  # noqa: BLE001 — dead-letter: cold/unreadable DB
        logger.warning("calibration_view: calibration_curve failed: %s", exc)
        return _empty_calibration(n_bins, min_trades_per_bin, _NO_CALIBRATION_REASON)

    if cal_df is None or cal_df.empty:
        return _empty_calibration(n_bins, min_trades_per_bin, _NO_CALIBRATION_REASON)

    try:
        bins: List[Dict[str, Any]] = []
        total = 0
        scored: List[tuple] = []  # (win_rate, bin_center, count) for populated bins
        for row in cal_df.to_dict(orient="records"):
            count = int(row.get("count") or 0)
            total += count
            win_rate = _finite_or_none(row.get("win_rate"))
            bin_center = _finite_or_none(row.get("bin_center"))
            bins.append(
                {
                    "bin_low": _finite_or_none(row.get("bin_low")),
                    "bin_high": _finite_or_none(row.get("bin_high")),
                    "bin_center": bin_center,
                    "conviction_mean": _finite_or_none(row.get("conviction_mean")),
                    "win_rate": win_rate,
                    "count": count,
                    "perfect_calibration": _finite_or_none(row.get("perfect_calibration")),
                }
            )
            if win_rate is not None and bin_center is not None and count > 0:
                scored.append((win_rate, bin_center, count))

        overall_win_rate: Optional[float] = None
        calibration_error: Optional[float] = None
        if scored:
            scored_count = sum(c for _, _, c in scored)
            if scored_count > 0:
                overall_win_rate = sum(wr * c for wr, _, c in scored) / scored_count
            calibration_error = sum(abs(wr - bc) for wr, bc, _ in scored) / len(scored)
    except Exception as exc:  # noqa: BLE001 — dead-letter: malformed frame
        logger.warning("calibration_view: serialization failed: %s", exc)
        return _empty_calibration(n_bins, min_trades_per_bin, _NO_CALIBRATION_REASON)

    return {
        "bins": bins,
        "total": total,
        "overall_win_rate": overall_win_rate,
        "calibration_error": calibration_error,
        "n_scored_bins": len(scored),
        "n_bins": n_bins,
        "min_trades_per_bin": min_trades_per_bin,
        "reason": None if total > 0 else _NO_CALIBRATION_REASON,
    }


# ---------------------------------------------------------------------------
# 2. Recommendation tracking (model vs. operator return)
# ---------------------------------------------------------------------------


def _empty_tracking(horizon_days: int, reason: Optional[str]) -> Dict[str, Any]:
    return {
        "horizon_days": horizon_days,
        "model_return": None,
        "operator_return": None,
        "delta": None,
        "n_signals": 0,
        "n_acted": 0,
        "n_completed": 0,
        "n_with_exit": 0,
        "rows": [],
        "reason": reason,
    }


_NO_TRACKING_REASON = (
    "No BUY signals in the decision log yet — log decisions in the journal, then "
    "return after the horizon elapses to see the model-vs-operator tracking report."
)


def recommendation_tracking_view(
    horizon_days: int = 30, *, log_path: Optional[Path] = None
) -> Dict[str, Any]:
    """Model (paper-equivalent) vs. operator (actual closed-trade) return for
    every logged BUY signal, serialized from
    ``evaluation_engine.recommendation_tracking_report``.

    Maps the report's ``model_return_30d``/``operator_return_30d`` keys onto
    ``model_return``/``operator_return`` (the horizon may not be 30) and coerces
    every NaN to ``null`` (CONSTRAINT #4). Honest empty shape + ``reason`` when
    no BUY signals are logged yet. Never raises (CONSTRAINT #6)."""
    try:
        from evaluation_engine import recommendation_tracking_report
        from transactions_store import TransactionsStore
    except Exception as exc:  # noqa: BLE001 — dead-letter: import failure
        logger.debug("recommendation_tracking_view import failed: %s", exc)
        return _empty_tracking(horizon_days, _NO_TRACKING_REASON)

    path = log_path if log_path is not None else _decision_log_path()

    try:
        # readonly TransactionsStore — only get_trade_history is read here (to
        # link "acted" entries); the report builds its OWN non-readonly
        # HistoricalStore internally (get_bars is a write-through cache).
        store = TransactionsStore(readonly=True)
        rpt = recommendation_tracking_report(
            log_path=path, transactions_store=store, horizon_days=horizon_days
        )
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.warning("recommendation_tracking_view: report failed: %s", exc)
        return _empty_tracking(horizon_days, _NO_TRACKING_REASON)

    try:
        rows: List[Dict[str, Any]] = []
        for r in rpt.get("rows", []) or []:
            days_held = r.get("days_held")
            rows.append(
                {
                    "symbol": r.get("symbol"),
                    "signal_ts": r.get("signal_ts"),
                    "signal_action": r.get("signal_action"),
                    "conviction": _finite_or_none(r.get("conviction")),
                    "action_taken": r.get("action_taken"),
                    "model_return": _finite_or_none(r.get("model_return")),
                    "actual_return": _finite_or_none(r.get("actual_return")),
                    "days_held": int(days_held) if days_held is not None else None,
                    "trade_id": int(r["trade_id"]) if r.get("trade_id") is not None else None,
                    "completed": bool(r.get("completed")),
                }
            )
        n_signals = int(rpt.get("n_signals") or 0)
    except Exception as exc:  # noqa: BLE001 — dead-letter: malformed report
        logger.warning("recommendation_tracking_view: serialization failed: %s", exc)
        return _empty_tracking(horizon_days, _NO_TRACKING_REASON)

    return {
        "horizon_days": int(rpt.get("horizon_days") or horizon_days),
        "model_return": _finite_or_none(rpt.get("model_return_30d")),
        "operator_return": _finite_or_none(rpt.get("operator_return_30d")),
        "delta": _finite_or_none(rpt.get("delta")),
        "n_signals": n_signals,
        "n_acted": int(rpt.get("n_acted") or 0),
        "n_completed": int(rpt.get("n_completed") or 0),
        "n_with_exit": int(rpt.get("n_with_exit") or 0),
        "rows": rows,
        "reason": None if n_signals > 0 else _NO_TRACKING_REASON,
    }


# ---------------------------------------------------------------------------
# 3. MFE / MAE — current signals (pure snapshot read)
# ---------------------------------------------------------------------------


def mfe_mae_view(snapshot: Optional[dict]) -> Dict[str, Any]:
    """One MFE/MAE point per current signal from the persisted state snapshot.

    Reads ``snapshot['signals'][*]``'s ``mfe``/``mae``/``edge_ratio``/
    ``advisory_conviction``/``action`` (the exact fields
    ``reporting/state_snapshot.py`` / ``main_orchestrator._write_state_snapshot``
    write). A signal missing either ``mfe`` OR ``mae`` (NaN → null) is SKIPPED,
    never plotted as a fabricated origin point (CONSTRAINT #4). Takes the
    already-loaded snapshot dict (mirrors ``pilots/observability.py::
    regime_overlay``) so the caller controls path resolution. Never raises."""
    if not snapshot:
        return {"points": [], "reason": "No state snapshot yet — run the pipeline first."}
    try:
        points: List[Dict[str, Any]] = []
        for s in snapshot.get("signals", []) or []:
            mfe = _finite_or_none(s.get("mfe"))
            mae = _finite_or_none(s.get("mae"))
            if mfe is None or mae is None:
                continue  # no excursion data yet — never fabricated
            points.append(
                {
                    "symbol": s.get("symbol") or "?",
                    "mfe": mfe,
                    "mae": mae,
                    "edge_ratio": _finite_or_none(s.get("edge_ratio")),
                    "conviction": _finite_or_none(s.get("advisory_conviction")),
                    "action": s.get("action") or s.get("advisory_action") or "—",
                }
            )
    except Exception as exc:  # noqa: BLE001 — dead-letter: malformed snapshot
        logger.debug("mfe_mae_view failed: %s", exc)
        return {"points": [], "reason": "State snapshot malformed or unreadable."}

    reason = (
        None
        if points
        else (
            "No MFE/MAE excursion data in the latest snapshot yet — populates once "
            "symbols have trade history in the TransactionsStore."
        )
    )
    return {"points": points, "reason": reason}


# ---------------------------------------------------------------------------
# 4. Edge ratio by strategy — heavier recompute over closed trades
# ---------------------------------------------------------------------------

_NO_CLOSED_TRADES_REASON = (
    "No closed trades yet — edge ratio by strategy populates once trades close "
    "and historical bars are cached."
)


def edge_by_strategy_view() -> Dict[str, Any]:
    """MFE/MAE/Edge Ratio recomputed per CLOSED trade, grouped by ``strategy``.

    For each closed trade: fetch OHLC bars (``HistoricalStore.get_bars``, 756d ≈
    3y) and compute ``EvaluationEngine.calculate_edge_ratio`` over the hold
    period, then group by the ``strategy`` tag recorded at entry →
    ``{strategy, n_trades, mean_edge_ratio, median_edge_ratio, mean_mfe,
    mean_mae}``. Per-trade try/except (one bad trade never aborts the batch);
    honest empty ``rows`` + ``reason`` when there are no closed trades or none
    with recoverable OHLC history. NaN aggregates → ``null`` (CONSTRAINT #4).
    Never raises (CONSTRAINT #6)."""
    try:
        import pandas as pd

        from data.historical_store import HistoricalStore
        from evaluation_engine import EvaluationEngine
        from transactions_store import TransactionsStore
    except Exception as exc:  # noqa: BLE001 — dead-letter: import failure
        logger.debug("edge_by_strategy_view import failed: %s", exc)
        return {"rows": [], "reason": _NO_CLOSED_TRADES_REASON}

    try:
        store = TransactionsStore(readonly=True)
        closed = store.closed_trades_df()
    except Exception as exc:  # noqa: BLE001 — dead-letter: cold/unreadable DB
        logger.warning("edge_by_strategy_view: closed_trades_df failed: %s", exc)
        return {"rows": [], "reason": _NO_CLOSED_TRADES_REASON}

    if closed is None or closed.empty:
        return {"rows": [], "reason": _NO_CLOSED_TRADES_REASON}

    try:
        ee = EvaluationEngine()
        # NON-readonly: get_bars is a write-through cache; a readonly store would
        # silently force a live-only fetch every call (mirrors report_viewer.py).
        hstore = HistoricalStore()
        bars_cache: Dict[str, "pd.DataFrame"] = {}
        per_trade: List[Dict[str, Any]] = []

        for _, trade in closed.iterrows():
            try:
                sym = str(trade.get("symbol") or "").upper()
                if not sym:
                    continue
                if sym not in bars_cache:
                    try:
                        bars_cache[sym] = hstore.get_bars(sym, lookback_days=756)
                    except Exception:  # noqa: BLE001 — per-symbol fetch guard
                        bars_cache[sym] = pd.DataFrame()
                bars = bars_cache[sym]
                if bars is None or bars.empty:
                    continue
                entry_price = trade.get("entry_price")
                entry_ts = trade.get("entry_ts")
                exit_ts = trade.get("exit_ts")
                if pd.isna(entry_price) or pd.isna(entry_ts) or pd.isna(exit_ts):
                    continue
                edge = ee.calculate_edge_ratio(bars, float(entry_price), entry_ts, exit_ts)
                per_trade.append(
                    {
                        "strategy": trade.get("strategy") or "(untagged)",
                        "MFE": edge["MFE"],
                        "MAE": edge["MAE"],
                        "Edge Ratio": edge["Edge Ratio"],
                    }
                )
            except Exception as exc:  # noqa: BLE001 — per-trade dead-letter
                logger.debug("edge_by_strategy_view: skipping trade: %s", exc)

        if not per_trade:
            return {
                "rows": [],
                "reason": "No closed trades with recoverable OHLC history yet.",
            }

        per_trade_df = pd.DataFrame(per_trade).dropna(subset=["Edge Ratio"])
        if per_trade_df.empty:
            return {
                "rows": [],
                "reason": "No closed trades with a computable edge ratio yet.",
            }

        grouped = (
            per_trade_df.groupby("strategy")
            .agg(
                n_trades=("Edge Ratio", "count"),
                mean_edge_ratio=("Edge Ratio", "mean"),
                median_edge_ratio=("Edge Ratio", "median"),
                mean_mfe=("MFE", "mean"),
                mean_mae=("MAE", "mean"),
            )
            .reset_index()
            .sort_values("mean_edge_ratio", ascending=False)
        )

        rows: List[Dict[str, Any]] = []
        for r in grouped.to_dict(orient="records"):
            rows.append(
                {
                    "strategy": str(r.get("strategy") or "(untagged)"),
                    "n_trades": int(r.get("n_trades") or 0),
                    "mean_edge_ratio": _finite_or_none(r.get("mean_edge_ratio")),
                    "median_edge_ratio": _finite_or_none(r.get("median_edge_ratio")),
                    "mean_mfe": _finite_or_none(r.get("mean_mfe")),
                    "mean_mae": _finite_or_none(r.get("mean_mae")),
                }
            )
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.warning("edge_by_strategy_view: computation failed: %s", exc)
        return {"rows": [], "reason": "Edge-by-strategy computation failed."}

    return {"rows": rows, "reason": None if rows else _NO_CLOSED_TRADES_REASON}


# ---------------------------------------------------------------------------
# 5. Recent decisions — read-only tail of the operator decision log
# ---------------------------------------------------------------------------


def recent_decisions_view(limit: int = 50, *, log_path: Optional[Path] = None) -> Dict[str, Any]:
    """Newest-first tail of the operator decision log
    (``gui/decision_log.py``, streamlit-free).

    Returns ``{decisions, reason}`` — honest empty ``decisions`` + a ``reason``
    when nothing is logged yet. ``trade_id`` is ``null`` when a decision was
    never linked to a trade (never fabricated — CONSTRAINT #4). Never raises."""
    try:
        import pandas as pd

        from gui.decision_log import decisions_df
    except Exception as exc:  # noqa: BLE001 — dead-letter: import failure
        logger.debug("recent_decisions_view import failed: %s", exc)
        return {"decisions": [], "reason": "Decision log unavailable."}

    path = log_path if log_path is not None else _decision_log_path()

    try:
        df = decisions_df(path)
    except Exception as exc:  # noqa: BLE001 — dead-letter
        logger.warning("recent_decisions_view: decisions_df failed: %s", exc)
        return {"decisions": [], "reason": "Decision log unavailable."}

    if df is None or df.empty:
        return {"decisions": [], "reason": "No decisions logged yet."}

    try:
        df = df.sort_values("timestamp", ascending=False).head(limit)
        decisions: List[Dict[str, Any]] = []
        for r in df.to_dict(orient="records"):
            tid = r.get("trade_id")
            trade_id = None
            try:
                if tid is not None and not pd.isna(tid):
                    trade_id = int(tid)
            except (TypeError, ValueError):
                trade_id = None
            decisions.append(
                {
                    "symbol": r.get("symbol"),
                    "action_taken": r.get("action_taken"),
                    "signal_action": r.get("signal_action"),
                    "conviction": _finite_or_none(r.get("conviction")),
                    "notes": r.get("notes") or "",
                    "timestamp": r.get("timestamp"),
                    "signal_ts": r.get("signal_ts") or "",
                    "trade_id": trade_id,
                }
            )
    except Exception as exc:  # noqa: BLE001 — dead-letter: malformed frame
        logger.warning("recent_decisions_view: serialization failed: %s", exc)
        return {"decisions": [], "reason": "Decision log unavailable."}

    return {
        "decisions": decisions,
        "reason": None if decisions else "No decisions logged yet.",
    }


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------


def calibration_summary(
    *, horizon_days: int = 30, snapshot: Optional[dict] = None
) -> Dict[str, Any]:
    """Bundle calibration + recommendation-tracking + MFE/MAE + recent decisions
    into one payload for ``GET /calibration/summary``. Deliberately EXCLUDES the
    heavier edge-by-strategy recompute (its own endpoint) so this summary never
    blocks on per-trade bar fetches. Each section degrades independently
    (CONSTRAINT #6)."""
    return {
        "calibration": calibration_view(),
        "recommendation_tracking": recommendation_tracking_view(horizon_days),
        "mfe_mae": mfe_mae_view(snapshot),
        "recent_decisions": recent_decisions_view(),
    }
