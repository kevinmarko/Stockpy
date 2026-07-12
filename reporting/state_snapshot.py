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
import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from settings import settings

if TYPE_CHECKING:
    from main import RunResult
    from dto_models import MacroEconomicDTO

logger = logging.getLogger(__name__)


def _safe_float_or_none(val: Any) -> Optional[float]:
    """Coerce *val* to float, or ``None`` when missing/NaN.

    Mirrors ``main_orchestrator._safe_float_or_none`` so the advisory writer
    emits a JSON ``null`` (never a fabricated ``0.0``) when a metric simply
    isn't available for a ticker — CONSTRAINT #4. The GUI reader treats
    ``null`` identically to a missing key, letting it distinguish "not
    computed" from a genuine zero.
    """
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


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

        # HMM risk-on probability is a single macro-wide value (same for every
        # signal), carried on the advisory MacroEconomicDTO. Null-safe: None
        # (JSON null) when the HMM didn't run — never a fabricated 0.0 (which
        # the GUI would misread as a genuine 0% risk-on probability).
        hmm_risk_on_val = (
            _safe_float_or_none(getattr(macro_dto, "hmm_risk_on_probability", None))
            if macro_dto is not None
            else None
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
                # GICS sector from engine.advisory.Recommendation.sector (source
                # of truth: the symbol's FundamentalDataDTO). getattr-guarded so
                # an older Recommendation without the field degrades to "" rather
                # than raising; null → "" (never fabricated — CONSTRAINT #4).
                # Feeds a downstream sector-allocation view.
                "sector": getattr(rec, "sector", "") or "",
                # GUI telemetry parity with main_orchestrator._write_state_snapshot.
                # garch_vol IS present in engine.advisory key_indicators — this
                # fixes the Strategy Matrix "GARCH Vol" column that blanked on the
                # advisory path. hmm_risk_on is the macro-wide value above.
                # The multifactor Z-scores are NOT currently in the advisory
                # key_indicators (see report note): emitted as None (JSON null)
                # for a consistent schema — never a fabricated 0.0 (CONSTRAINT #4).
                "garch_vol": _safe_float_or_none(ki.get("garch_vol")),
                "hmm_risk_on": hmm_risk_on_val,
                "value_z": _safe_float_or_none(ki.get("value_z")),
                "quality_z": _safe_float_or_none(ki.get("quality_z")),
                "lowvol_z": _safe_float_or_none(ki.get("lowvol_z")),
                "size_z": _safe_float_or_none(ki.get("size_z")),
                "multifactor_composite": _safe_float_or_none(ki.get("multifactor_composite")),
                # PR2 Agent A — schema parity with main_orchestrator._write_state_snapshot.
                # These three metrics (FinBERT news sentiment, realized slippage,
                # CoVaR tail-dependency proxy) are NOT currently threaded onto
                # engine.advisory.Recommendation.key_indicators, so they serialize
                # as None (JSON null) on the advisory path until a future PR
                # populates them — never a fabricated 0.0 (CONSTRAINT #4). Mirrors
                # how the multifactor-Z keys above are handled.
                "news_sentiment": _safe_float_or_none(ki.get("news_sentiment")),
                "realized_slippage": _safe_float_or_none(ki.get("realized_slippage")),
                "covar_proxy": _safe_float_or_none(ki.get("covar_proxy")),
            })

        regime = "UNKNOWN"
        vix = 0.0
        # Recession + regime telemetry fields, mirroring the key spellings in
        # main_orchestrator._write_state_snapshot so the GUI Observability /
        # Report-Viewer tabs read a consistent schema across both writers.
        # Only emitted when a macro_dto is present (same as vix/regime today);
        # absent — never fabricated — when the advisory run had no macro data.
        macro_fields: Dict[str, Any] = {}
        if macro_dto is not None:
            regime = getattr(macro_dto, "market_regime", "UNKNOWN") or "UNKNOWN"
            vix = float(getattr(macro_dto, "vix_value", 0.0) or 0.0)
            macro_fields = {
                "yield_curve": float(getattr(macro_dto, "yield_curve", 0.0) or 0.0),
                "sahm_rule": float(getattr(macro_dto, "sahm_rule_indicator", 0.0) or 0.0),
                "high_yield_oas": float(getattr(macro_dto, "credit_spread", 0.0) or 0.0),
                # HMM probability is legitimately None when the HMM didn't run:
                # emit null, not 0.0, so the GUI can tell "didn't run" from "0%".
                "hmm_risk_on_probability": _safe_float_or_none(
                    getattr(macro_dto, "hmm_risk_on_probability", None)
                ),
            }

        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tickers": [r.symbol for r in result.recommendations],
            "holdings": holdings,
            "market_regime": str(regime),
            "vix": vix,
            **macro_fields,
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
