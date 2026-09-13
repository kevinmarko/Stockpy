"""pilots/retrospective_insights.py — batch/pattern insights (READ-ONLY) for
the Retrospective Learning Loop (Trade Journal).

Two sections, returned by :func:`batch_insights`, that are STRUCTURALLY
never merged into one combined figure:

1. **Calibration** — reuses ``pilots.calibration.calibration_view()``
   (itself a thin, dead-letter-safe serialization of
   ``evaluation_engine.calibration_curve``) VERBATIM. This module never
   re-derives or re-buckets a conviction/win-rate reliability diagram of its
   own — the whole point is one single source of truth for "is the model's
   conviction score actually calibrated."

2. **Cohorts** — a manual-vs-signal-driven-vs-unknown breakdown of closed
   paper trades (``data/paper_account_store.py``'s ``paper_closed_trades``),
   classified via the SAME forward-only decision-snapshot lookup the
   Retrospective composer uses (``data/trade_decision_snapshot_store.py``):

   * no captured snapshot for a trade -> ``"unknown"``
   * a captured snapshot with ``provenance == "manual"`` -> ``"manual"``
   * a captured snapshot with ``provenance`` starting with ``"automated:"``
     -> ``"signal_driven"``
   * anything else -> ``"unknown"`` (defensive; should not happen given the
     snapshot store's own write-side validation)

   This is the SAME rule as, and deliberately kept in lockstep with,
   ``pilots.retrospective_composer``'s own decision-state derivation
   (``decision.state`` is NEVER inferred from ``strategy_id`` — see that
   module's docstring's anti-shortcut rule). Per-cohort ``n_trades``/
   ``win_rate``/``mean_realized_pnl_pct`` are computed independently for
   each of the three cohorts and there is NO combined/"overall" figure
   anywhere in the returned shape — merging manual and signal-driven
   outcomes into one number is exactly the kind of blur CONSTRAINT #4 rules
   out for this feature (see CLAUDE.md's "Retrospective Learning Loop"
   framing).

Design invariants (identical to ``pilots/calibration.py``/
``pilots/observability.py``, this reader's precedent):

* **Never raises (CONSTRAINT #6)** — the calibration section and the
  cohorts section are each wrapped in their OWN try/except so one section's
  failure never blocks the other; each section additionally degrades
  independently to an honest empty/null shape on any internal failure.
* **Never fabricates (CONSTRAINT #4)** — a cohort with zero trades reports
  ``win_rate=None``/``mean_realized_pnl_pct=None`` (never a fabricated
  ``0.0``); a trade's own missing ``realized_pnl_pct`` is excluded from the
  mean rather than coerced to ``0.0``.
* Imports ``data.paper_account_store``, ``data.trade_decision_snapshot_store``,
  and ``pilots.calibration`` — none of which are on ``api/pilots_api.py``'s
  AST-guard denylist (only ``processing_engine``, ``strategy_engine``,
  ``forecasting_engine``, ``macro_engine``, ``technical_options_engine``,
  ``main_orchestrator``, ``desktop`` are forbidden). Imports are LAZY
  (inside function bodies), matching ``pilots/calibration.py``'s documented
  convention, so a missing/broken dependency degrades gracefully instead of
  breaking import of this module (and this whole API) at process start.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = ["batch_insights"]

_COHORT_NAMES = ("signal_driven", "manual", "unknown")
_MAX_TRADES = 500


def _finite_float(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ---------------------------------------------------------------------------
# 1. Calibration — reused verbatim, never re-derived
# ---------------------------------------------------------------------------


def _fallback_calibration(reason: str) -> Dict[str, Any]:
    """Mirrors ``pilots.calibration._empty_calibration``'s shape exactly, so
    a caller reading ``batch_insights()["calibration"]`` sees the identical
    schema whether the real view succeeded or this fallback engaged."""
    return {
        "bins": [],
        "total": 0,
        "overall_win_rate": None,
        "calibration_error": None,
        "n_scored_bins": 0,
        "n_bins": 10,
        "min_trades_per_bin": 5,
        "reason": reason,
    }


def _calibration_section() -> Dict[str, Any]:
    """Verbatim ``pilots.calibration.calibration_view()`` output. NEVER
    re-derives/re-buckets a calibration curve of its own — see module
    docstring."""
    try:
        from pilots.calibration import calibration_view
    except Exception as exc:  # noqa: BLE001 — dead-letter: import failure
        logger.debug("batch_insights: calibration import failed: %s", exc)
        return _fallback_calibration("Calibration module unavailable.")

    try:
        return calibration_view()
    except Exception as exc:  # noqa: BLE001 — dead-letter: calibration_view itself raised
        logger.warning("batch_insights: calibration_view() raised: %s", exc)
        return _fallback_calibration("Calibration data unavailable.")


# ---------------------------------------------------------------------------
# 2. Cohorts — manual vs. signal-driven vs. unknown, structurally separate
# ---------------------------------------------------------------------------


def _empty_cohort() -> Dict[str, Optional[float]]:
    return {"n_trades": 0, "win_rate": None, "mean_realized_pnl_pct": None}


def _empty_cohorts() -> Dict[str, Dict[str, Optional[float]]]:
    return {name: _empty_cohort() for name in _COHORT_NAMES}


def classify_decision_state(snapshot: Optional[Dict[str, Any]]) -> str:
    """The SAME 3-state rule ``pilots.retrospective_composer`` uses: no
    snapshot -> ``"unknown"``; ``provenance == "manual"`` -> ``"manual"``;
    ``provenance`` starting with ``"automated:"`` -> ``"signal_driven"``;
    anything else -> ``"unknown"``. Exposed (not underscore-prefixed) so a
    caller — or a test proving lockstep with the composer — can reuse this
    exact classification rather than re-deriving it."""
    if not snapshot:
        return "unknown"
    provenance = snapshot.get("provenance")
    if provenance == "manual":
        return "manual"
    if isinstance(provenance, str) and provenance.startswith("automated:"):
        return "signal_driven"
    return "unknown"


def _parse_entry_ts(value: Any) -> Optional[datetime]:
    """Parses the ISO-formatted ``entry_ts`` string
    ``PaperAccountStore.get_full_closed_trades`` emits (or passes through a
    real ``datetime`` unchanged) back into a ``datetime`` suitable for
    ``TradeDecisionSnapshotStore.get_snapshots_batch``'s lookup keys.
    ``None`` on anything unparsable/missing — a trade with no usable
    ``entry_ts`` simply cannot have a matching snapshot (the store's own
    natural key requires one), so it is classified ``"unknown"``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _summarize_cohort(trades: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    n = len(trades)
    if n == 0:
        return _empty_cohort()

    wins = 0
    pct_values: List[float] = []
    for trade in trades:
        pnl = _finite_float(trade.get("realized_pnl"))
        if pnl is not None and pnl > 0:
            wins += 1
        pct = _finite_float(trade.get("realized_pnl_pct"))
        if pct is not None:
            pct_values.append(pct)

    win_rate = (wins / n) if n > 0 else None
    mean_pct = (sum(pct_values) / len(pct_values)) if pct_values else None
    return {"n_trades": n, "win_rate": win_rate, "mean_realized_pnl_pct": mean_pct}


def _cohorts_section(limit: int = _MAX_TRADES) -> Dict[str, Dict[str, Optional[float]]]:
    try:
        from data.paper_account_store import PaperAccountStore
        from data.trade_decision_snapshot_store import TradeDecisionSnapshotStore
    except Exception as exc:  # noqa: BLE001 — dead-letter: import failure
        logger.debug("batch_insights: cohort imports failed: %s", exc)
        return _empty_cohorts()

    try:
        store = PaperAccountStore(readonly=True)
        trades = store.get_full_closed_trades(limit=limit)
    except Exception as exc:  # noqa: BLE001 — dead-letter: cold/unreadable DB
        logger.warning("batch_insights: get_full_closed_trades failed: %s", exc)
        return _empty_cohorts()

    if not trades:
        return _empty_cohorts()

    # Build the (symbol, strategy_id, entry_ts) lookup keys for every trade
    # that has enough identity to possibly match a captured snapshot; a
    # trade missing symbol/strategy_id/entry_ts cannot match anything in the
    # snapshot store (its natural key requires all three) and is classified
    # "unknown" directly, with no lookup attempted.
    keys: List[Tuple[Any, Any, datetime]] = []
    trade_keys: List[Optional[Tuple[Any, Any, datetime]]] = []
    for trade in trades:
        symbol = trade.get("symbol")
        strategy_id = trade.get("strategy_id")
        entry_ts = _parse_entry_ts(trade.get("entry_ts"))
        if symbol and strategy_id and entry_ts is not None:
            key = (symbol, strategy_id, entry_ts)
            keys.append(key)
            trade_keys.append(key)
        else:
            trade_keys.append(None)

    snapshots_by_key: Dict[Tuple[Any, Any, datetime], Dict[str, Any]] = {}
    try:
        snap_store = TradeDecisionSnapshotStore(readonly=True)
        snapshots_by_key = snap_store.get_snapshots_batch(keys)
    except Exception as exc:  # noqa: BLE001 — dead-letter: cold/unreadable DB
        logger.warning("batch_insights: get_snapshots_batch failed: %s", exc)
        snapshots_by_key = {}

    buckets: Dict[str, List[Dict[str, Any]]] = {name: [] for name in _COHORT_NAMES}
    for trade, key in zip(trades, trade_keys):
        snapshot = snapshots_by_key.get(key) if key is not None else None
        state = classify_decision_state(snapshot)
        if state not in buckets:  # defensive — should never happen
            state = "unknown"
        buckets[state].append(trade)

    return {name: _summarize_cohort(bucket) for name, bucket in buckets.items()}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def batch_insights() -> Dict[str, Any]:
    """Calibration + manual/signal-driven/unknown cohort breakdown for the
    Retrospective Learning Loop. Each section is wrapped in its OWN
    try/except so one failing never blocks the other (CONSTRAINT #6);
    cohorts are structurally separate keys, never combined into one
    "overall"/"all" figure (CONSTRAINT #4 — see module docstring)."""
    try:
        calibration = _calibration_section()
    except Exception as exc:  # noqa: BLE001 — CONSTRAINT #6: never raise
        logger.warning("batch_insights: calibration section raised unexpectedly: %s", exc)
        calibration = _fallback_calibration("Calibration data unavailable.")

    try:
        cohorts = _cohorts_section()
    except Exception as exc:  # noqa: BLE001 — CONSTRAINT #6: never raise
        logger.warning("batch_insights: cohorts section raised unexpectedly: %s", exc)
        cohorts = _empty_cohorts()

    return {"calibration": calibration, "cohorts": cohorts}
