"""Shared state-snapshot writer + Δ-diff loader.

Extracted verbatim from ``main.py`` (the two functions
``_write_state_snapshot`` and ``_load_snapshot_diff_for_report``, renamed to
their public equivalents ``write_state_snapshot`` / ``load_snapshot_diff``) so
that both advisory entry points and the reporting layer share a single
state-snapshot persistence implementation.

``write_state_snapshot`` persists ``OUTPUT_DIR/state_snapshot.json`` and rotates
it into ``history/`` for the ``scripts.snapshot_diff`` reader;
``load_snapshot_diff`` returns the latest "Δ Since Last Run" diff dict for the
report band. Both swallow all errors (CONSTRAINT #6) — the daily report must
always render.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from settings import settings

if TYPE_CHECKING:
    from main import RunResult
    from dto_models import MacroEconomicDTO

logger = logging.getLogger(__name__)


def write_state_snapshot(result: RunResult, macro_dto: Optional[MacroEconomicDTO]) -> None:
    """Persist OUTPUT_DIR/state_snapshot.json + rotate into history/.

    Mirrors ``main_orchestrator._write_state_snapshot`` so the
    ``scripts.snapshot_diff`` reader sees a consistent schema across both
    entry points. Errors are swallowed (CONSTRAINT #6) — the daily report
    must always render.
    """
    try:
        import json
        from datetime import datetime, timezone

        snap = result.snapshot
        positions = getattr(snap, "positions", {}) or {}
        holdings = sorted(
            sym.upper() for sym, p in positions.items()
            if float(getattr(p, "quantity", 0.0) or 0.0) > 0
        )

        signals: List[Dict[str, Any]] = []
        for rec in result.recommendations:
            pos = positions.get(rec.symbol)
            ki = rec.key_indicators or {}
            shares = float(getattr(pos, "quantity", 0.0) or 0.0) if pos else 0.0
            signals.append({
                "symbol": rec.symbol,
                # advisory entry point: action == advisory_action (single source).
                "action": rec.action,
                "advisory_action": rec.action,
                "advisory_conviction": float(rec.conviction or 0.0),
                "advisory_position_pct": float(rec.suggested_position_pct or 0.0),
                "advisory_rationale": rec.rationale or "",
                "kelly_target": float(rec.suggested_position_pct or 0.0),
                "score": float(ki.get("score", 0.0) or 0.0),
                "price": float(getattr(pos, "current_price", 0.0) or 0.0) if pos else 0.0,
                "shares": shares,
                # GUI Strategy Matrix decomposition (additive; consumed by
                # gui/panels/strategy_matrix.py). Scalars sourced from
                # engine.advisory.Recommendation.key_indicators;
                # score_components is the one non-scalar field, carried
                # separately on the Recommendation dataclass (None when the
                # strategy engine failed this cycle — never fabricated).
                "meta_label_composite": float(ki.get("meta_label_composite", 1.0) or 1.0),
                "regime_multiplier": float(ki.get("regime_multiplier", 1.0) or 1.0),
                "kelly_target_pre_regime": ki.get("kelly_target_pre_regime", float("nan")),
                "kelly_target_post_regime": ki.get("kelly_target_post_regime", float("nan")),
                "score_components": rec.score_components or {},
                # Tactical price bands (gui/panels/report_viewer.py's "Tactical
                # Ranges" table already reads these keys) + suggested SELL exit
                # sizing — both computed on Recommendation, previously dropped
                # before reaching the GUI-facing snapshot.
                "buy_range": rec.buy_range or "",
                "sell_range": rec.sell_range or "",
                "suggested_exit_pct": float(rec.suggested_exit_pct or 0.0),
            })

        regime = "UNKNOWN"
        vix = 0.0
        if macro_dto is not None:
            regime = getattr(macro_dto, "market_regime", "UNKNOWN") or "UNKNOWN"
            vix = float(getattr(macro_dto, "vix_value", 0.0) or 0.0)

        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tickers": [r.symbol for r in result.recommendations],
            "holdings": holdings,
            "market_regime": str(regime),
            "vix": vix,
            "kill_switch_active": (settings.OUTPUT_DIR / "KILL_SWITCH").exists(),
            "macro_regime_gate_enabled": settings.MACRO_REGIME_GATE_ENABLED,
            "signals": signals,
        }
        snap_path = settings.OUTPUT_DIR / "state_snapshot.json"
        snap_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        try:
            from scripts.snapshot_diff import rotate_snapshot
            rotate_snapshot(
                snapshot,
                settings.OUTPUT_DIR,
                max_age_days=settings.SNAPSHOT_HISTORY_DAYS,
            )
        except Exception as rot_exc:
            logger.debug("Snapshot rotation skipped: %s", rot_exc)
    except Exception as exc:
        logger.warning("State snapshot write failed (non-critical): %s", exc)


def load_snapshot_diff() -> Optional[Dict[str, Any]]:
    """Return the latest Δ Since Last Run diff dict (or ``None`` if unavailable).

    Always called AFTER ``write_state_snapshot`` so the just-written
    snapshot is the "curr" side of the comparison; the prior rotated
    snapshot is "prev". Returns ``None`` on any failure or first ever run
    so the report template hides the band entirely.
    """
    try:
        from scripts.snapshot_diff import compute_diff_from_history
        diff = compute_diff_from_history(
            settings.OUTPUT_DIR,
            conviction_delta_threshold=settings.SNAPSHOT_CONVICTION_DELTA_THRESHOLD,
        )
        if diff.prev_ts is None and diff.curr_ts is None:
            return None
        return diff.to_dict()
    except Exception as exc:
        logger.debug("Δ-band diff unavailable: %s", exc)
        return None
