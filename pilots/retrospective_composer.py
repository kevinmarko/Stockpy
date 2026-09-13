"""pilots/retrospective_composer.py — Per-trade retrospective composer
(READ-ONLY), backing the Retrospective Learning Loop / Trade Journal.

Composes ONE closed paper trade (a ``data.paper_account_store
.PaperAccountStore.get_full_closed_trades()`` row) into a full retrospective
record by joining three already-real, already-tested pieces:

1. **What happened** — the ``paper_closed_trades`` row itself, passed through
   verbatim (entry/exit price, realized PnL, holding period, close reason).
2. **The full move (MAE/MFE/Edge Ratio)** — a PURE price-history calculation.
   Reuses ``evaluation_engine.EvaluationEngine.calculate_edge_ratio`` fed by
   ``data.historical_store.HistoricalStore.get_bars`` — the EXACT two calls
   ``pilots/calibration.py::edge_by_strategy_view`` already makes per closed
   trade, just sourced from ``paper_closed_trades`` rows instead of
   ``transactions_store``'s bridged rows. This is zero new evaluation math.
   **Not gated on ``settings.PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED``** —
   that flag only controls whether a *different* downstream consumer
   (``evaluation_engine.calibration_curve``, which reads ``transactions_store``
   for a ``conviction`` value) ever sees these trades; MAE/MFE/Edge Ratio here
   need only real OHLC bars for the symbol over the hold window, nothing to
   do with the bridge. Do not conflate "is the bridge on" with "is MAE/MFE
   available" — they are independent facts about different tables.
3. **The why (decision provenance)** — ``data.trade_decision_snapshot_store
   .TradeDecisionSnapshotStore``, a forward-only capture of "what did we know
   at trade-open" keyed by the exact same ``(symbol, strategy_id, entry_ts)``
   triple a closed trade carries. See that module's docstring for the full
   design rationale.

THE SINGLE MOST IMPORTANT RULE — ``decision.state`` is computed ONLY from the
snapshot lookup result, NEVER inferred from ``strategy_id``, ``pilot_id``, or
anything else on the trade row. A trade with ``strategy_id == "Manual Trade"``
but no captured snapshot reports ``state="unknown"`` — NOT ``"manual"`` — even
though that inference would often be numerically correct: presenting an
inference as a captured record is fabrication under this repo's CONSTRAINT #4,
full stop. "No snapshot exists" means exactly one honest thing: "decision
context was not captured for this trade" (a pre-feature trade, or an
automated writer this feature hasn't been wired into yet) — disclosed,
deliberate, forward-only scoping, never silently upgraded to a guess.

Design invariants (identical to ``pilots/calibration.py``/``pilots/
observability.py``, this reader's precedent):

* **Never raises (CONSTRAINT #6)** — ``compose_trade_retrospective`` has an
  outer last-resort guard; store construction, the decision lookup, and the
  evaluation computation each degrade INDEPENDENTLY per trade so one
  section's failure (or one trade's malformed input) never blocks another.
* **Never fabricates (CONSTRAINT #4)** — a NaN/undefined MFE/MAE/Edge Ratio
  is ``None`` (JSON ``null``) with ``evaluation.available=False`` and a real
  ``reason`` string, never a guessed number. ``decision.state`` is never
  inferred (see above).
* Imports ``evaluation_engine``, ``data.historical_store``, and
  ``data.trade_decision_snapshot_store`` LAZILY (inside function bodies),
  matching ``pilots/calibration.py``'s documented convention exactly, so a
  missing/broken dependency degrades gracefully instead of breaking import of
  this module (and this whole API) at process start. This module is
  therefore listed in ``tests/test_pilots_strategy_matrix.py``'s
  ``_DEPENDENCY_LIGHT_EXEMPT`` set alongside ``calibration.py`` (same heavy,
  lazily-guarded import surface).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = ["compose_trade_retrospective", "compose_trade_retrospectives"]

_MANUAL_PROVENANCE = "manual"
_AUTOMATED_PREFIX = "automated:"

_REASON_ENTRY_TS_UNKNOWN = "entry_ts unknown for this trade"
_REASON_MISSING_INPUTS = "trade is missing the fields required to evaluate its hold period"
_REASON_NO_PRICE_HISTORY = "no price history available for this hold period"
_REASON_EVAL_UNCOMPUTABLE = "evaluation could not be computed for this trade"

_SnapshotKey = Tuple[str, str, datetime]


# ---------------------------------------------------------------------------
# Small, dependency-free helpers
# ---------------------------------------------------------------------------


def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    """``dict.get``, degrading to ``default`` for any non-mapping/malformed
    input instead of raising — this helper must never raise (CONSTRAINT #6)."""
    try:
        return obj.get(key, default)
    except Exception:  # noqa: BLE001 — deliberately broad, see docstring
        return default


def _finite_or_none(value: Any) -> Optional[float]:
    """Coerce to a finite float, else ``None`` (NaN/non-numeric -> ``null``,
    CONSTRAINT #4)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 string (or pass through a real ``datetime``) into a
    ``datetime``. Returns ``None`` — never a fabricated "now" — on ``None``,
    an unparseable value, or any other malformed input (CONSTRAINT #4).
    Deliberately folds "missing" and "malformed" into the same ``None``
    sentinel: both mean the same thing downstream — no usable timestamp."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _classify_provenance(provenance: Optional[str]) -> str:
    """Map a CAPTURED snapshot's raw provenance string onto the trade
    journal's decision-state vocabulary. Only ever called when a real
    snapshot row exists — see ``_decision_from_snapshot``. An unrecognized
    provenance string fails closed to ``"unknown"`` rather than guessing."""
    if not isinstance(provenance, str):
        return "unknown"
    if provenance == _MANUAL_PROVENANCE:
        return "manual"
    if provenance.startswith(_AUTOMATED_PREFIX):
        return "signal_driven"
    return "unknown"


def _unknown_decision() -> Dict[str, Any]:
    """The honest "decision context was not captured for this trade" shape —
    used both when no snapshot row exists at all AND when there is no valid
    key to even look one up with (missing entry_ts/symbol/strategy_id)."""
    return {
        "state": "unknown",
        "provenance": None,
        "conviction": None,
        "regime": None,
        "factors": None,
        "notes": None,
    }


def _decision_from_snapshot(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the ``decision`` block from a snapshot lookup result. This is
    the ONLY function that decides ``state`` — it never looks at anything
    from the trade row itself (no ``strategy_id``, no ``pilot_id``), by
    design (see module docstring's "single most important rule")."""
    if snapshot is None:
        return _unknown_decision()
    provenance = snapshot.get("provenance")
    factors = snapshot.get("factors")
    return {
        "state": _classify_provenance(provenance),
        "provenance": provenance,
        "conviction": _finite_or_none(snapshot.get("conviction")),
        "regime": snapshot.get("regime"),
        "factors": factors if isinstance(factors, dict) else None,
        "notes": snapshot.get("notes"),
    }


def _unavailable_evaluation(reason: str) -> Dict[str, Any]:
    return {"available": False, "mfe": None, "mae": None, "edge_ratio": None, "reason": reason}


def _evaluate_hold_period(
    *,
    symbol: Optional[str],
    entry_price: Optional[float],
    entry_dt: Optional[datetime],
    exit_dt: Optional[datetime],
    evaluation_engine: Optional[Any],
    historical_store: Optional[Any],
    bars_cache: Dict[str, Any],
) -> Dict[str, Any]:
    """MFE/MAE/Edge Ratio for one trade's hold window — the SAME two calls
    ``pilots/calibration.py::edge_by_strategy_view`` makes per closed trade
    (``HistoricalStore.get_bars`` + ``EvaluationEngine.calculate_edge_ratio``),
    reused verbatim. Degrades to ``available=False`` + a real ``reason``
    whenever any required input is missing, price history is unavailable, or
    the computed MFE/MAE/Edge Ratio come back non-finite (CONSTRAINT #4) —
    never passes a NaN through un-flagged."""
    if entry_dt is None:
        return _unavailable_evaluation(_REASON_ENTRY_TS_UNKNOWN)
    if exit_dt is None or not symbol:
        return _unavailable_evaluation(_REASON_MISSING_INPUTS)
    if entry_price is None:
        return _unavailable_evaluation(_REASON_MISSING_INPUTS)
    if evaluation_engine is None or historical_store is None:
        return _unavailable_evaluation(_REASON_NO_PRICE_HISTORY)

    sym_key = symbol.upper()
    if sym_key not in bars_cache:
        try:
            bars_cache[sym_key] = historical_store.get_bars(sym_key, lookback_days=756)
        except Exception as exc:  # noqa: BLE001 — per-symbol dead-letter
            logger.debug("retrospective_composer: get_bars(%s) failed: %s", sym_key, exc)
            bars_cache[sym_key] = None

    bars = bars_cache.get(sym_key)
    if bars is None or bars.empty:
        return _unavailable_evaluation(_REASON_NO_PRICE_HISTORY)

    try:
        edge = evaluation_engine.calculate_edge_ratio(bars, float(entry_price), entry_dt, exit_dt)
    except Exception as exc:  # noqa: BLE001 — dead-letter; calculate_edge_ratio
        # already catches its own internal exceptions and returns a NaN
        # sentinel dict, but a mocked/patched engine (or a future change to
        # that method) could still raise -- never let that propagate here.
        logger.debug("retrospective_composer: calculate_edge_ratio failed for %s: %s", sym_key, exc)
        return _unavailable_evaluation(_REASON_EVAL_UNCOMPUTABLE)

    mfe = _finite_or_none(edge.get("MFE"))
    mae = _finite_or_none(edge.get("MAE"))
    edge_ratio = _finite_or_none(edge.get("Edge Ratio"))
    if mfe is None or mae is None or edge_ratio is None:
        # calculate_edge_ratio returns an all-NaN sentinel whenever the hold
        # period slice was empty (no bars covering [entry_ts, exit_ts]) or
        # any other internal failure -- reported honestly, never as zeros.
        return _unavailable_evaluation(_REASON_NO_PRICE_HISTORY)

    return {"available": True, "mfe": mfe, "mae": mae, "edge_ratio": edge_ratio, "reason": None}


def _degraded_record(trade: Any) -> Dict[str, Any]:
    """Best-effort extraction of whatever real fields ``trade`` happens to
    carry, with an honest ``decision``/``evaluation`` block — the fallback
    shape for a trade this composer could not otherwise process. Never
    raises (every field goes through ``_safe_get``/``_finite_or_none``)."""
    return {
        "trade_id": _safe_get(trade, "trade_id"),
        "symbol": _safe_get(trade, "symbol"),
        "strategy_id": _safe_get(trade, "strategy_id"),
        "pilot_id": _safe_get(trade, "pilot_id"),
        "side": _safe_get(trade, "side"),
        "qty": _finite_or_none(_safe_get(trade, "qty")),
        "entry_ts": _safe_get(trade, "entry_ts"),
        "entry_price": _finite_or_none(_safe_get(trade, "entry_price")),
        "exit_ts": _safe_get(trade, "exit_ts"),
        "exit_price": _finite_or_none(_safe_get(trade, "exit_price")),
        "realized_pnl": _finite_or_none(_safe_get(trade, "realized_pnl")),
        "realized_pnl_pct": _finite_or_none(_safe_get(trade, "realized_pnl_pct")),
        "holding_period_days": _finite_or_none(_safe_get(trade, "holding_period_days")),
        "close_reason": _safe_get(trade, "close_reason"),
        "evaluation": _unavailable_evaluation(_REASON_MISSING_INPUTS),
        "decision": _unknown_decision(),
    }


def _compose_one(
    trade: Any,
    *,
    entry_dt: Optional[datetime],
    snapshots: Dict[_SnapshotKey, Dict[str, Any]],
    evaluation_engine: Optional[Any],
    historical_store: Optional[Any],
    bars_cache: Dict[str, Any],
) -> Dict[str, Any]:
    """Compose one trade given already-resolved shared state (the batch
    snapshot lookup dict, the shared engine/store instances, the per-symbol
    bars cache). Never raises — any failure anywhere in this function
    degrades to :func:`_degraded_record` (CONSTRAINT #6)."""
    try:
        symbol = _safe_get(trade, "symbol")
        strategy_id = _safe_get(trade, "strategy_id")
        entry_ts_raw = _safe_get(trade, "entry_ts")
        exit_ts_raw = _safe_get(trade, "exit_ts")
        exit_dt = _parse_ts(exit_ts_raw)

        base: Dict[str, Any] = {
            "trade_id": _safe_get(trade, "trade_id"),
            "symbol": symbol,
            "strategy_id": strategy_id,
            "pilot_id": _safe_get(trade, "pilot_id"),
            "side": _safe_get(trade, "side"),
            "qty": _finite_or_none(_safe_get(trade, "qty")),
            "entry_ts": entry_ts_raw,  # verbatim -- never reformatted
            "entry_price": _finite_or_none(_safe_get(trade, "entry_price")),
            "exit_ts": exit_ts_raw,  # verbatim -- never reformatted
            "exit_price": _finite_or_none(_safe_get(trade, "exit_price")),
            "realized_pnl": _finite_or_none(_safe_get(trade, "realized_pnl")),
            "realized_pnl_pct": _finite_or_none(_safe_get(trade, "realized_pnl_pct")),
            "holding_period_days": _finite_or_none(_safe_get(trade, "holding_period_days")),
            "close_reason": _safe_get(trade, "close_reason"),
        }

        # ---- decision: ONLY from the snapshot lookup, never inferred ----
        try:
            if entry_dt is None or not symbol or strategy_id is None:
                decision = _unknown_decision()
            else:
                key: _SnapshotKey = (symbol, strategy_id, entry_dt)
                decision = _decision_from_snapshot(snapshots.get(key))
        except Exception as exc:  # noqa: BLE001 — per-section dead-letter
            logger.debug(
                "retrospective_composer: decision section failed for trade %r: %s",
                base.get("trade_id"), exc,
            )
            decision = _unknown_decision()

        # ---- evaluation: pure price-history recompute ----
        try:
            evaluation = _evaluate_hold_period(
                symbol=symbol,
                entry_price=base["entry_price"],
                entry_dt=entry_dt,
                exit_dt=exit_dt,
                evaluation_engine=evaluation_engine,
                historical_store=historical_store,
                bars_cache=bars_cache,
            )
        except Exception as exc:  # noqa: BLE001 — per-section dead-letter
            logger.debug(
                "retrospective_composer: evaluation section failed for trade %r: %s",
                base.get("trade_id"), exc,
            )
            evaluation = _unavailable_evaluation(_REASON_EVAL_UNCOMPUTABLE)

        base["evaluation"] = evaluation
        base["decision"] = decision
        return base
    except Exception as exc:  # noqa: BLE001 — outer dead-letter, CONSTRAINT #6
        logger.warning("retrospective_composer: failed to compose trade %r: %s", trade, exc)
        return _degraded_record(trade)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compose_trade_retrospectives(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compose many ``paper_closed_trades`` rows (as returned by
    ``PaperAccountStore.get_full_closed_trades()``) into full retrospective
    records efficiently: the decision-snapshot lookup is batched via
    ``TradeDecisionSnapshotStore.get_snapshots_batch`` (one query instead of
    N), and OHLC bars are fetched at most ONCE per distinct symbol via a
    per-call ``bars_cache`` dict — mirroring ``pilots/calibration.py::
    edge_by_strategy_view``'s ``bars_cache`` pattern exactly. Never raises;
    always returns exactly one record per input trade, in order, degrading
    per-trade/per-section rather than aborting the whole batch."""
    if not trades:
        return []

    # ---- decision snapshot store (degrades the WHOLE decision section on failure) ----
    try:
        from data.trade_decision_snapshot_store import TradeDecisionSnapshotStore

        snap_store: Optional[Any] = TradeDecisionSnapshotStore(readonly=True)
    except Exception as exc:  # noqa: BLE001 — dead-letter: import/construction failure
        logger.warning("compose_trade_retrospectives: snapshot store unavailable: %s", exc)
        snap_store = None

    parsed_entries: List[Optional[datetime]] = []
    keys: List[_SnapshotKey] = []
    for trade in trades:
        entry_dt: Optional[datetime] = None
        try:
            entry_dt = _parse_ts(_safe_get(trade, "entry_ts"))
            symbol = _safe_get(trade, "symbol")
            strategy_id = _safe_get(trade, "strategy_id")
            if entry_dt is not None and symbol and strategy_id is not None:
                keys.append((symbol, strategy_id, entry_dt))
        except Exception as exc:  # noqa: BLE001 — per-trade dead-letter
            logger.debug("compose_trade_retrospectives: pre-scan failed for trade %r: %s", trade, exc)
            entry_dt = None
        parsed_entries.append(entry_dt)

    snapshots: Dict[_SnapshotKey, Dict[str, Any]] = {}
    if snap_store is not None and keys:
        try:
            snapshots = snap_store.get_snapshots_batch(keys)
        except Exception as exc:  # noqa: BLE001 — dead-letter
            logger.warning("compose_trade_retrospectives: get_snapshots_batch failed: %s", exc)
            snapshots = {}

    # ---- evaluation engine + historical store (degrades the WHOLE evaluation section) ----
    try:
        from evaluation_engine import EvaluationEngine

        evaluation_engine: Optional[Any] = EvaluationEngine()
    except Exception as exc:  # noqa: BLE001 — dead-letter: import/construction failure
        logger.warning("compose_trade_retrospectives: EvaluationEngine unavailable: %s", exc)
        evaluation_engine = None

    try:
        from data.historical_store import HistoricalStore

        # NON-readonly: get_bars is a write-through cache; a readonly store
        # would silently force a live-only fetch every call (mirrors
        # pilots/calibration.py::edge_by_strategy_view's own documented
        # reasoning -- see that function's docstring for the full rationale).
        historical_store: Optional[Any] = HistoricalStore()
    except Exception as exc:  # noqa: BLE001 — dead-letter: import/construction failure
        logger.warning("compose_trade_retrospectives: HistoricalStore unavailable: %s", exc)
        historical_store = None

    bars_cache: Dict[str, Any] = {}

    results: List[Dict[str, Any]] = []
    for trade, entry_dt in zip(trades, parsed_entries):
        results.append(
            _compose_one(
                trade,
                entry_dt=entry_dt,
                snapshots=snapshots,
                evaluation_engine=evaluation_engine,
                historical_store=historical_store,
                bars_cache=bars_cache,
            )
        )
    return results


def compose_trade_retrospective(trade: Dict[str, Any]) -> Dict[str, Any]:
    """Compose ONE ``paper_closed_trades`` row (as returned by
    ``PaperAccountStore.get_full_closed_trades()``) into a full retrospective
    record. Never raises (CONSTRAINT #6) — delegates to
    :func:`compose_trade_retrospectives` (a batch of one), with an absolute
    last-resort fallback to a fully degraded record if even that somehow
    fails."""
    try:
        results = compose_trade_retrospectives([trade])
        if results:
            return results[0]
    except Exception as exc:  # noqa: BLE001 — absolute last resort, CONSTRAINT #6
        logger.warning("compose_trade_retrospective: composition failed entirely: %s", exc)
    return _degraded_record(trade)
